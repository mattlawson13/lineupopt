"use client";

import { useEffect, useState } from "react";
import { api, ResolutionPatterns, ResolutionSummary } from "@/lib/api";

export default function ResolutionsPanel({ slateId }: { slateId: string }) {
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ResolutionSummary | null>(null);
  const [patterns, setPatterns] = useState<ResolutionPatterns | null>(null);
  const [patternsLoading, setPatternsLoading] = useState(true);

  const loadPatterns = () => {
    setPatternsLoading(true);
    api
      .getResolutionPatterns()
      .then(setPatterns)
      .catch(() => setPatterns(null))
      .finally(() => setPatternsLoading(false));
  };

  useEffect(loadPatterns, []);

  const handleResolve = async () => {
    setResolving(true);
    setResolveError(null);
    setSummary(null);
    try {
      const result = await api.resolveSlate(slateId);
      setSummary(result);
      loadPatterns(); // this may have just added a new data point to the cross-slate patterns below
    } catch (e: any) {
      setResolveError(e?.message || String(e));
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-surface-border bg-surface-raised p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">Resolve This Slate</h2>
          <button
            onClick={handleResolve}
            disabled={resolving}
            className="rounded bg-accent px-4 py-2 text-sm font-semibold text-surface transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-40"
          >
            {resolving ? "Resolving…" : "Resolve"}
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-500">
          Grades this slate against real final stats once the games are over, and computes the
          &quot;retro-optimal&quot; lineup — the best roster obtainable with perfect hindsight — for comparison.
          Only works once the slate&apos;s games have actually finished.
        </p>
        {resolveError && (
          <div className="rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{resolveError}</div>
        )}
        {summary && (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Your Best" value={summary.our_best_actual_points.toFixed(1)} />
              <Stat label="Retro-Optimal" value={summary.retro_optimal_points.toFixed(1)} />
              <Stat label="Proj. MAE" value={summary.mae.toFixed(2)} />
              <Stat label="Proj. Bias" value={summary.bias.toFixed(2)} />
            </div>
            {summary.biggest_misses.length > 0 && (
              <div>
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Biggest Projection Misses
                </div>
                <table className="w-full text-xs">
                  <tbody>
                    {summary.biggest_misses.slice(0, 8).map((m, i) => (
                      <tr key={i} className="border-b border-surface-border/40">
                        <td className="py-1 font-medium">{m.name}</td>
                        <td className="py-1 text-slate-500">{m.position}</td>
                        <td className="mono-num py-1 text-right text-slate-400">proj {m.projected.toFixed(1)}</td>
                        <td className="mono-num py-1 text-right text-slate-400">actual {m.actual.toFixed(1)}</td>
                        <td className={`mono-num py-1 text-right ${m.error > 0 ? "text-warn" : "text-accent"}`}>
                          {m.error > 0 ? "+" : ""}
                          {m.error.toFixed(1)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      <div className="rounded-lg border border-surface-border bg-surface-raised p-5">
        <h2 className="mb-3 text-lg font-semibold tracking-tight">Cross-Slate Patterns</h2>
        {patternsLoading ? (
          <div className="text-sm text-slate-500">Loading…</div>
        ) : !patterns || Object.keys(patterns.by_contest_type).length === 0 ? (
          <div className="text-sm text-slate-500">
            No slates resolved yet — resolve a slate above once its games are final.
          </div>
        ) : (
          <>
            <p className="mb-4 text-xs text-slate-500">{patterns.note}</p>
            <div className="space-y-4">
              {Object.entries(patterns.by_contest_type).map(([contestType, data]) => (
                <div key={contestType}>
                  <div className="mb-1.5 text-sm font-semibold capitalize text-accent">
                    {contestType}{" "}
                    <span className="font-normal text-slate-500">
                      ({data.slates_resolved} slate{data.slates_resolved === 1 ? "" : "s"} resolved)
                    </span>
                  </div>
                  <div className="mb-2 grid grid-cols-3 gap-2 text-center text-xs">
                    <Stat label="Avg Salary Used" value={`${data.avg_salary_used_pct}%`} />
                    <Stat label="Avg Proj. MAE" value={data.avg_projection_mae.toFixed(2)} />
                    <Stat label="Avg Proj. Bias" value={data.avg_projection_bias.toFixed(2)} />
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(data.stack_type_distribution).map(([stackType, s]) => (
                      <span
                        key={stackType}
                        className="rounded border border-surface-border bg-surface px-2 py-1 text-[11px] capitalize"
                      >
                        {stackType.replace(/_/g, " ")}: {s.pct}%
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
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
