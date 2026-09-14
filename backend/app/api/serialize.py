"""Thin dict serializers for API responses — deliberately not a full
pydantic ORM-mapping layer given the size of the schema; these just pick
the fields the frontend needs and make datetimes JSON-safe.
"""
from __future__ import annotations

import datetime


def _iso(dt: datetime.datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def serialize_slate(slate) -> dict:
    return {
        "id": slate.id, "sport": slate.sport, "contest_type": slate.contest_type,
        "dk_draft_group_id": slate.dk_draft_group_id, "name": slate.name,
        "season": slate.season, "week": slate.week, "start_time_utc": _iso(slate.start_time_utc),
        "game_ids": slate.game_ids, "source": slate.source, "imported_at": _iso(slate.imported_at),
        "injury_alert_detail": slate.injury_alert_detail,
    }


def serialize_game(game) -> dict:
    return {
        "id": game.id, "season": game.season, "week": game.week,
        "home_team": game.home_team.abbreviation, "away_team": game.away_team.abbreviation,
        "kickoff_utc": _iso(game.kickoff_utc), "is_dome": game.is_dome,
        "home_score_final": game.home_score_final, "away_score_final": game.away_score_final,
    }


def serialize_player_row(dk_row, ensemble, ownership, sim_summary) -> dict:
    salary = dk_row.salaries[-1].salary if dk_row.salaries else 0
    proj = ensemble.ensemble_projection if ensemble else 0.0
    value = round(proj / max(salary, 1) * 1000, 2) if salary else 0.0
    own_pct = ownership.projected_ownership_pct if ownership else None
    return {
        "dk_player_id": dk_row.dk_player_id,
        "player_id": dk_row.player_id,
        "name": dk_row.display_name,
        "position": dk_row.dk_position,
        "team": dk_row.team_abbreviation,
        "opponent": dk_row.opponent_abbreviation,
        "game_id": dk_row.game_id,
        "roster_status": dk_row.roster_status,
        "salary": salary,
        "projection": proj,
        "median": ensemble.median if ensemble else None,
        "floor": ensemble.floor if ensemble else None,
        "ceiling": ensemble.ceiling if ensemble else None,
        "value": value,
        "ownership_pct": own_pct,
        "leverage": (sim_summary.prob_top5pct * 100 - own_pct) if (sim_summary and own_pct is not None) else None,
        "chalk_score": ownership.chalk_score if ownership else None,
        "contrarian_score": ownership.contrarian_score if ownership else None,
        "sim": {
            "mean": sim_summary.mean, "median": sim_summary.median, "std_dev": sim_summary.std_dev,
            "floor": sim_summary.floor, "ceiling": sim_summary.ceiling, "percentiles": sim_summary.percentiles,
            "prob_3x_salary": sim_summary.prob_3x_salary, "prob_4x_salary": sim_summary.prob_4x_salary,
            "prob_5x_salary": sim_summary.prob_5x_salary, "prob_6x_salary": sim_summary.prob_6x_salary,
            "prob_top1pct": sim_summary.prob_top1pct, "prob_top5pct": sim_summary.prob_top5pct,
        } if sim_summary else None,
    }


def serialize_lineup(lineup, players_by_id) -> dict:
    return {
        "id": lineup.id, "salary_used": lineup.salary_used, "salary_remaining": lineup.salary_remaining,
        "projected_points": lineup.projected_points, "ceiling": lineup.ceiling, "floor": lineup.floor,
        "avg_ownership_pct": lineup.projected_ownership_product, "leverage_score": lineup.leverage_score,
        "stack_type": lineup.stack_type, "stack_description": lineup.stack_description,
        "uniqueness_score": lineup.uniqueness_score, "ai_rank": lineup.ai_rank, "ai_score": lineup.ai_score,
        "explanation": lineup.explanation,
        "players": [
            {
                "player_id": lp.player_id, "name": players_by_id.get(lp.player_id, {}).get("name", ""),
                "position": players_by_id.get(lp.player_id, {}).get("position", ""),
                "team": players_by_id.get(lp.player_id, {}).get("team", ""),
                "roster_slot": lp.roster_slot, "salary": lp.salary, "projected_points": lp.projected_points,
            }
            for lp in lineup.players
        ],
    }
