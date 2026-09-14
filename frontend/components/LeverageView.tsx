"use client";

import { useMemo, useState } from "react";
import { PlayerRow } from "@/lib/api";
import clsx from "clsx";

// The backend's own PlayerRow.leverage field (optimal% - projected
// ownership%) is never actually populated — it depends on a "which
// players appear in the best-simulated lineups" portfolio pass that was
// never wired up (see app/ingestion/slate_builder.py's _build_ownership,
// which leaves it null with a comment saying as much). Rather than ship a
// column that always reads "—", this computes a leverage score from what
// IS reliably present on every build: projection and ownership%, compared
// within each position group (a 20%-owned RB1 and a 20%-owned punt WR are
// not equally "chalky" — leverage only means something relative to peers
// at the same position).
type Scored = PlayerRow & { leverageScore: number | null; projPctl: number | null; ownPctl: number | null };

function percentileRanks(values: number[]): number[] {
  // Rank-based percentile (0-100, higher = larger value), average rank for
  // ties. Good enough for a directional UI signal, not a statistics tool.
  const n = values.length;
  if (n <= 1) return values.map(() => 50);
  const sorted = [...values].map((v, i) => ({ v, i })).sort((a, b) => a.v - b.v);
  const ranks = new Array<number>(n);
  let idx = 0;
  while (idx < n) {
    let j = idx;
    while (j + 1 < n && sorted[j + 1].v === sorted[idx].v) j++;
    const avgRank = (idx + j) / 2;
    for (let k = idx; k <= j; k++) ranks[sorted[k].i] = avgRank;
    idx = j + 1;
  }
  return ranks.map((r) => (r / (n - 1)) * 100);
}

// A full DK slate lists every rostered player, including deep bench/
// inactive guys DK itself prices at the salary floor. Computing leverage
// as a pure percentile-within-position over the WHOLE pool falls apart
// there: a $2,500 3rd-string TE projected for near-zero points can still
// rank in a high projection percentile simply because most of the rest
// of the position pool is also near-zero, while ownership is compressed
// near zero across the board — producing a "leverage play" that's really
// just an irrelevant player nobody (rightly) rosters. DK's own salary
// already encodes "is this a plausible roster option this week" better
// than anything derivable client-side, so leverage is only computed
// among the top half of each position's salary range.
const SALARY_PERCENTILE_FLOOR = 50;

function scorePlayers(players: PlayerRow[]): Scored[] {
  const byPosition = new Map<string, PlayerRow[]>();
  for (const p of players) {
    if (p.ownership_pct == null || p.projection == null) continue;
    const arr = byPosition.get(p.position) ?? [];
    arr.push(p);
    byPosition.set(p.position, arr);
  }

  const scoreById = new Map<string, { leverageScore: number; projPctl: number; ownPctl: number }>();
  for (const group of byPosition.values()) {
    const salaryPctls = percentileRanks(group.map((p) => p.salary));
    const eligible = group.filter((_, i) => salaryPctls[i] >= SALARY_PERCENTILE_FLOOR);
    if (eligible.length < 2) continue;

    const projPctls = percentileRanks(eligible.map((p) => p.projection));
    const ownPctls = percentileRanks(eligible.map((p) => p.ownership_pct as number));
    eligible.forEach((p, i) => {
      scoreById.set(p.dk_player_id, {
        projPctl: projPctls[i],
        ownPctl: ownPctls[i],
        leverageScore: projPctls[i] - ownPctls[i],
      });
    });
  }

  return players.map((p) => {
    const s = scoreById.get(p.dk_player_id);
    return { ...p, leverageScore: s?.leverageScore ?? null, projPctl: s?.projPctl ?? null, ownPctl: s?.ownPctl ?? null };
  });
}

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DST"];

export default function LeverageView({ players }: { players: PlayerRow[] }) {
  const [position, setPosition] = useState("ALL");
  const scored = useMemo(() => scorePlayers(players), [players]);

  const filtered = useMemo(() => {
    let rows = scored.filter((p) => p.leverageScore !== null);
    if (position !== "ALL") rows = rows.filter((p) => p.position === position);
    return rows.sort((a, b) => (b.leverageScore ?? 0) - (a.leverageScore ?? 0));
  }, [scored, position]);

  const topLeverage = filtered.slice(0, 5);
  const topChalkToFade = [...filtered].sort((a, b) => (a.leverageScore ?? 0) - (b.leverageScore ?? 0)).slice(0, 5);

  if (scored.every((p) => p.leverageScore === null)) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-10 text-center text-sm text-slate-500">
        No ownership projections yet for this slate — leverage needs both a projection and an ownership% per player.
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <LeverageCallouts
          title="Top Leverage Plays"
          subtitle="High projection relative to position, low ownership — underpriced by the field"
          players={topLeverage}
          tone="accent"
        />
        <LeverageCallouts
          title="Lower Relative Value"
          subtitle="Owned more than their projection supports within position — not necessarily high-owned in absolute terms, just relatively overpriced by the field"
          players={topChalkToFade}
          tone="danger"
        />
      </div>

      <div className="rounded-lg border border-surface-border bg-surface-raised">
        <div className="flex flex-wrap items-center gap-3 border-b border-surface-border p-3">
          <select
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm focus:border-accent focus:outline-none"
          >
            {POSITIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <span className="text-xs text-slate-500">
            Leverage = projection percentile − ownership percentile, computed within each position group
          </span>
          <span className="ml-auto text-xs text-slate-500">{filtered.length} players</span>
        </div>
        <div className="max-h-[65vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface-raised">
              <tr className="border-b border-surface-border">
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Player</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Pos</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Team</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Salary</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Proj</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Own%</th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Leverage</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr key={p.dk_player_id} className="border-b border-surface-border/60 hover:bg-surface/60">
                  <td className="px-3 py-1.5 font-medium">{p.name}</td>
                  <td className="px-3 py-1.5 text-slate-400">{p.position}</td>
                  <td className="px-3 py-1.5 text-slate-400">
                    {p.team} <span className="text-slate-600">vs {p.opponent}</span>
                  </td>
                  <td className="mono-num px-3 py-1.5">${p.salary.toLocaleString()}</td>
                  <td className="mono-num px-3 py-1.5 text-accent">{p.projection?.toFixed(1)}</td>
                  <td className="mono-num px-3 py-1.5">{p.ownership_pct?.toFixed(1)}%</td>
                  <td
                    className={clsx(
                      "mono-num px-3 py-1.5 font-medium",
                      (p.leverageScore ?? 0) > 20 ? "text-accent" : (p.leverageScore ?? 0) < -20 ? "text-danger" : ""
                    )}
                  >
                    {p.leverageScore! > 0 ? "+" : ""}
                    {p.leverageScore?.toFixed(0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function LeverageCallouts({
  title,
  subtitle,
  players,
  tone,
}: {
  title: string;
  subtitle: string;
  players: Scored[];
  tone: "accent" | "danger";
}) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <h3 className={clsx("text-sm font-semibold", tone === "accent" ? "text-accent" : "text-danger")}>{title}</h3>
      <p className="mb-3 text-xs text-slate-500">{subtitle}</p>
      <ul className="space-y-1.5">
        {players.map((p) => (
          <li key={p.dk_player_id} className="flex items-center justify-between text-sm">
            <span>
              <span className="font-medium">{p.name}</span>{" "}
              <span className="text-xs text-slate-500">
                {p.position} · {p.team}
              </span>
            </span>
            <span className="mono-num text-xs text-slate-400">
              {p.projection?.toFixed(1)} pts · {p.ownership_pct?.toFixed(1)}% own
            </span>
          </li>
        ))}
        {players.length === 0 && <li className="text-xs text-slate-500">Not enough data yet.</li>}
      </ul>
    </div>
  );
}
