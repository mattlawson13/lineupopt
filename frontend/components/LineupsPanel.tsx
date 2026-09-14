"use client";

import { useEffect, useState } from "react";
import { api, Lineup } from "@/lib/api";

// CPT first (Showdown's premium slot leads on DK's own UI); FLEX last so
// Classic's own FLEX doesn't collide with Showdown's 5 FLEX slots — both
// formats sort correctly through this one list since roster_slot values
// never overlap between the two (QB/RB/WR/TE/DST vs CPT/FLEX).
const SLOT_ORDER = ["CPT", "QB", "RB", "WR", "TE", "DST", "FLEX"];

export default function LineupsPanel({ lineups, slateId }: { lineups: Lineup[]; slateId: string }) {
  // Late swap replaces one lineup with a freshly re-optimized one (a new
  // OptimizationRun under the hood) — re-fetching the slate's lineups from
  // the backend would collapse the view down to just that new run's single
  // lineup, losing the rest of the portfolio. Keeping a local, editable
  // copy lets a swap update just that one card in place instead, and lets
  // "Generate More" append a fresh batch onto what's already shown.
  const [localLineups, setLocalLineups] = useState(lineups);
  const [swapping, setSwapping] = useState<Set<string>>(new Set());
  const [swapError, setSwapError] = useState<Record<string, string>>({});
  const [generatingMore, setGeneratingMore] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => setLocalLineups(lineups), [lineups]);

  const handleLateSwap = async (lineupId: string) => {
    setSwapping((prev) => new Set(prev).add(lineupId));
    setSwapError((prev) => ({ ...prev, [lineupId]: "" }));
    try {
      const result = await api.lateSwapLineup(lineupId);
      const swapped = result.lineups[0];
      setLocalLineups((prev) => prev.map((lu) => (lu.id === lineupId ? swapped : lu)));
    } catch (e: any) {
      setSwapError((prev) => ({ ...prev, [lineupId]: e?.message || String(e) }));
    } finally {
      setSwapping((prev) => {
        const next = new Set(prev);
        next.delete(lineupId);
        return next;
      });
    }
  };

  const handleGenerateMore = async () => {
    setGeneratingMore(true);
    setGenerateError(null);
    try {
      const result = await api.generateLineups({
        slate_id: slateId,
        num_lineups: 10,
        exclude_lineup_ids: localLineups.map((lu) => lu.id),
      });
      if (result?.lineups) {
        setLocalLineups((prev) => [...prev, ...result.lineups]);
      } else {
        setGenerateError(result?.detail || "No lineups returned");
      }
    } catch (e: any) {
      setGenerateError(e?.message || String(e));
    } finally {
      setGeneratingMore(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await api.exportDkCsv(slateId);
    } catch (e: any) {
      setExportError(e?.message || String(e));
    } finally {
      setExporting(false);
    }
  };

  if (!localLineups.length) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-8 text-center text-sm text-slate-500">
        No lineups yet — build a slate to generate them.
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="text-xs text-slate-500">{localLineups.length} lineups</span>
        <div className="flex items-center gap-2">
          {generateError && <span className="text-[11px] text-danger">{generateError}</span>}
          {exportError && <span className="text-[11px] text-danger">{exportError}</span>}
          <button
            onClick={handleExport}
            disabled={exporting}
            title="Download a CSV in DraftKings' own bulk-upload format — copy its player columns into DK's downloaded contest-entry template (which has your real Entry ID/Contest ID) alongside it, then upload that to DK"
            className="rounded border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting ? "Exporting…" : "Export to DraftKings"}
          </button>
          <button
            onClick={handleGenerateMore}
            disabled={generatingMore}
            title="Generate 10 more lineups that stay meaningfully different from the ones below, instead of near-duplicates"
            className="rounded border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {generatingMore ? "Generating…" : "Generate 10 More (No Duplicates)"}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {localLineups.map((lu) => (
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

          <div className="mb-3 rounded border border-surface-border bg-surface p-2.5 text-xs leading-relaxed text-slate-300">
            <span className="font-semibold text-slate-400">WHY THIS LINEUP? </span>
            {lu.explanation}
          </div>

          <button
            onClick={() => handleLateSwap(lu.id)}
            disabled={swapping.has(lu.id)}
            title="Lock in players whose game has already started; re-optimize everyone else against current projections"
            className="w-full rounded border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {swapping.has(lu.id) ? "Swapping…" : "Late Swap"}
          </button>
          {swapError[lu.id] && (
            <div className="mt-1.5 text-[11px] text-danger">{swapError[lu.id]}</div>
          )}
        </div>
        ))}
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
