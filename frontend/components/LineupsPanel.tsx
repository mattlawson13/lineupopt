"use client";

import { Lineup } from "@/lib/api";

// CPT first (Showdown's premium slot leads on DK's own UI); FLEX last so
// Classic's own FLEX doesn't collide with Showdown's 5 FLEX slots — both
// formats sort correctly through this one list since roster_slot values
// never overlap between the two (QB/RB/WR/TE/DST vs CPT/FLEX).
const SLOT_ORDER = ["CPT", "QB", "RB", "WR", "TE", "DST", "FLEX"];

export default function LineupsPanel({ lineups }: { lineups: Lineup[] }) {
  if (!lineups.length) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-8 text-center text-sm text-slate-500">
        No lineups yet — build a slate to generate them.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {lineups.map((lu) => (
        <div key={lu.id} className="rounded-lg border border-surface-border bg-surface-raised p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-accent">
              #{lu.ai_rank ?? "—"} · {lu.stack_type?.replace(/_/g, " ") || "no stack"}
            </span>
            <span className="mono-num text-xs text-slate-500">
              ${lu.salary_used.toLocaleString()} used · ${lu.salary_remaining.toLocaleString()} left
            </span>
          </div>

          <table className="mb-3 w-full text-xs">
            <tbody>
              {[...lu.players]
                .sort((a, b) => SLOT_ORDER.indexOf(a.roster_slot) - SLOT_ORDER.indexOf(b.roster_slot))
                .map((p) => (
                  <tr key={p.player_id} className="border-b border-surface-border/40">
                    <td className="w-12 py-1 text-slate-500">{p.roster_slot}</td>
                    <td className="py-1 font-medium">{p.name}</td>
                    <td className="py-1 text-slate-500">{p.team}</td>
                    <td className="mono-num py-1 text-right">${p.salary.toLocaleString()}</td>
                  </tr>
                ))}
            </tbody>
          </table>

          <div className="mb-3 grid grid-cols-4 gap-2 text-center text-xs">
            <Stat label="Proj" value={lu.projected_points.toFixed(1)} />
            <Stat label="Ceil" value={lu.ceiling.toFixed(1)} />
            <Stat label="Own%" value={lu.avg_ownership_pct?.toFixed(1) ?? "—"} />
            <Stat label="Uniq" value={lu.uniqueness_score?.toFixed(1) ?? "—"} />
          </div>

          <div className="rounded border border-surface-border bg-surface p-2.5 text-xs leading-relaxed text-slate-300">
            <span className="font-semibold text-slate-400">WHY THIS LINEUP? </span>
            {lu.explanation}
          </div>
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-surface px-1 py-1.5">
      <div className="mono-num font-semibold text-slate-100">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}
