"""Monte Carlo simulation orchestrator — spec section 10.

Simulates the whole slate together (not player-by-player in isolation):
for each game, draws a game script per simulation, applies the resulting
position/role multiplier to every player in that game, and draws
fantasy-point outcomes using a Gaussian copula so that within-game
correlated players (spec section 9) actually move together across
simulations rather than being independent draws that happen to share a
mean.
"""
from __future__ import annotations

import dataclasses
import time

import numpy as np

from app.config.loader import get_simulation_settings
from app.correlations.engine import CorrelationEntry
from app.simulation.distributions import SimulationSummary, gamma_ppf_from_normal, summarize_player_draws
from app.simulation.game_sim import GameSimInput, draw_game_scripts, multiplier_for


@dataclasses.dataclass
class PlayerSimInput:
    player_id: str
    position: str
    team: str
    salary: int
    mean_projection: float
    cv: float | None = None  # falls back to config's position default


@dataclasses.dataclass
class SlateSimulationResult:
    num_simulations: int
    duration_seconds: float
    player_summaries: dict[str, SimulationSummary]
    player_draws: dict[str, np.ndarray]  # kept in-memory for optimizer/portfolio use; not persisted raw (see models/analytics.py note)


def _nearest_psd(corr: np.ndarray) -> np.ndarray:
    corr = (corr + corr.T) / 2
    eigvals, eigvecs = np.linalg.eigh(corr)
    eigvals = np.clip(eigvals, 1e-6, None)
    psd = eigvecs @ np.diag(eigvals) @ eigvecs.T
    d = np.sqrt(np.diag(psd))
    d[d == 0] = 1.0
    psd = psd / np.outer(d, d)
    np.fill_diagonal(psd, 1.0)
    return psd


def _build_correlation_matrix(player_ids: list[str], entries: list[CorrelationEntry]) -> np.ndarray:
    n = len(player_ids)
    idx = {pid: i for i, pid in enumerate(player_ids)}
    corr = np.eye(n)
    for e in entries:
        if e.player_a_id in idx and e.player_b_id in idx:
            i, j = idx[e.player_a_id], idx[e.player_b_id]
            corr[i, j] = corr[j, i] = e.correlation
    return _nearest_psd(corr)


def simulate_slate(
    players: list[PlayerSimInput],
    games: list[GameSimInput],
    team_to_game: dict[str, str],
    team_to_role: dict[str, str],  # team -> "home" | "away"
    correlation_entries: list[CorrelationEntry],
    num_simulations: int | None = None,
    seed: int | None = None,
) -> SlateSimulationResult:
    cfg = get_simulation_settings()
    num_simulations = min(num_simulations or cfg["default_simulation_count"], cfg["max_simulation_count"])
    rng = np.random.default_rng(seed if seed is not None else cfg.get("random_seed"))
    variance_cfg = cfg["player_variance"]

    start = time.monotonic()
    games_by_id = {g.game_id: g for g in games}
    players_by_game: dict[str, list[PlayerSimInput]] = {}
    standalone_players: list[PlayerSimInput] = []
    for p in players:
        game_id = team_to_game.get(p.team)
        if game_id and game_id in games_by_id:
            players_by_game.setdefault(game_id, []).append(p)
        else:
            standalone_players.append(p)

    all_draws: dict[str, np.ndarray] = {}

    for game_id, group in players_by_game.items():
        game = games_by_id[game_id]
        scripts = draw_game_scripts(game, num_simulations, rng)

        player_ids = [p.player_id for p in group]
        corr_matrix = _build_correlation_matrix(player_ids, correlation_entries)
        z = rng.multivariate_normal(mean=np.zeros(len(group)), cov=corr_matrix, size=num_simulations)

        # Precompute the multiplier for each unique script name once, then
        # index — avoids recomputing per-simulation.
        unique_scripts = np.unique(scripts)
        for i, p in enumerate(group):
            role = team_to_role.get(p.team)
            mult_by_script = {s: multiplier_for(s, p.position, role) for s in unique_scripts}
            script_to_idx = {s: i for i, s in enumerate(unique_scripts)}
            mult_lookup = np.array([mult_by_script[s] for s in unique_scripts])
            mult_per_sim = mult_lookup[np.searchsorted(unique_scripts, scripts)]

            cv = p.cv or variance_cfg.get(p.position, {}).get("cv", 0.5)
            effective_mean = p.mean_projection * mult_per_sim
            all_draws[p.player_id] = gamma_ppf_from_normal(z[:, i], effective_mean, cv)

    for p in standalone_players:
        cv = p.cv or variance_cfg.get(p.position, {}).get("cv", 0.5)
        z = rng.standard_normal(num_simulations)
        all_draws[p.player_id] = gamma_ppf_from_normal(z, p.mean_projection, cv)

    # Cross-sectional rank (per simulation, across all players) for top-1%/
    # top-5% probability — spec section 10.
    ordered_ids = list(all_draws.keys())
    matrix = np.vstack([all_draws[pid] for pid in ordered_ids])  # (n_players, n_sims)
    n_players = matrix.shape[0]
    ranks = matrix.shape[0] - 1 - np.argsort(np.argsort(matrix, axis=0), axis=0)  # 0 = best in that sim
    percentile_rank = 1 - ranks / max(n_players - 1, 1)
    top1_flags = percentile_rank >= 0.99
    top5_flags = percentile_rank >= 0.95

    salary_by_id = {p.player_id: p.salary for p in players}
    summaries: dict[str, SimulationSummary] = {}
    for i, pid in enumerate(ordered_ids):
        summaries[pid] = summarize_player_draws(
            all_draws[pid], salary_by_id[pid], top1_flags[i], top5_flags[i]
        )

    return SlateSimulationResult(
        num_simulations=num_simulations,
        duration_seconds=round(time.monotonic() - start, 3),
        player_summaries=summaries,
        player_draws=all_draws,
    )
