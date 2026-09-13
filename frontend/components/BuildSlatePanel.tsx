"use client";

import { useEffect, useState } from "react";
import { api, AvailableDkSlate, BuildProgressEvent } from "@/lib/api";

function formatSlateLabel(s: AvailableDkSlate): string {
  const start = new Date(s.start_time_utc);
  const when = start.toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  const formatTag = s.contest_format === "showdown" ? "[Showdown] " : "";
  return `${formatTag}${when} — ${s.sample_contest_name} (${s.contest_count} contests)`;
}

const STEP_LABELS: Record<string, string> = {
  dk_slate: "DraftKings slate",
  data_validation: "Data validation",
  persist_entities: "Players / teams / games",
  betting: "Vegas lines",
  weather: "Weather",
  injuries: "Injury reports",
  historical_stats: "Historical usage (nflverse)",
  projections: "Projection ensemble",
  ownership: "Ownership model",
  correlations: "Correlation matrix",
  simulation: "Monte Carlo simulation",
  optimize: "Lineup optimization",
  ai_rank: "AI lineup ranking",
  complete: "Build complete",
};

const STATUS_ICON: Record<string, string> = {
  running: "⏳",
  success: "✓",
  warning: "⚠",
  error: "✗",
};

const STATUS_COLOR: Record<string, string> = {
  running: "text-slate-400",
  success: "text-accent",
  warning: "text-warn",
  error: "text-danger",
};

export default function BuildSlatePanel({
  onComplete,
}: {
  onComplete: (slateId: string) => void;
}) {
  const [draftGroupId, setDraftGroupId] = useState("");
  const [availableSlates, setAvailableSlates] = useState<AvailableDkSlate[]>([]);
  const [slatesLoading, setSlatesLoading] = useState(true);
  const [slatesError, setSlatesError] = useState<string | null>(null);
  // Conservative defaults for a free-tier (512MB) backend host — a full
  // main slate has ~750 relevant players, and 10k sims x 20 lineups got
  // an actual deployed instance OOM-killed. Still editable — raise these
  // if/when the backend has more memory to work with.
  const [numSims, setNumSims] = useState(3000);
  const [numLineups, setNumLineups] = useState(10);
  const [objective, setObjective] = useState("large_field_gpp");
  const [events, setEvents] = useState<BuildProgressEvent[]>([]);
  const [building, setBuilding] = useState(false);

  const loadAvailableSlates = () => {
    setSlatesLoading(true);
    setSlatesError(null);
    api
      .listAvailableDkSlates()
      .then((slates) => {
        setAvailableSlates(slates);
        setDraftGroupId((prev) => prev || slates[0]?.dk_draft_group_id || "");
      })
      .catch((e) => setSlatesError(e?.message || String(e)))
      .finally(() => setSlatesLoading(false));
  };

  useEffect(loadAvailableSlates, []);

  const handleBuild = () => {
    if (!draftGroupId) return;
    setEvents([]);
    setBuilding(true);
    api.streamBuildSlate(
      { dk_draft_group_id: draftGroupId, num_simulations: numSims, num_lineups: numLineups, objective },
      (evt) => {
        setEvents((prev) => {
          const idx = prev.findIndex((e) => e.step === evt.step);
          if (idx >= 0) {
            const copy = [...prev];
            copy[idx] = evt;
            return copy;
          }
          return [...prev, evt];
        });
        if (evt.step === "complete" && evt.status === "success") {
          const slateId = evt.data?.slate_id as string | undefined;
          if (slateId) onComplete(slateId);
        }
      },
      () => setBuilding(false),
      (msg) => {
        setBuilding(false);
        setEvents((prev) => [...prev, { step: "fatal_error", status: "error", detail: msg, data: {} }]);
      }
    );
  };

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-5">
      <h2 className="mb-4 text-lg font-semibold tracking-tight">Build Slate</h2>
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="col-span-2 flex flex-col gap-1 text-xs text-slate-400 sm:col-span-4">
          Slate
          {slatesLoading ? (
            <div className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-500">
              Loading open DraftKings slates…
            </div>
          ) : slatesError ? (
            <div className="flex items-center gap-2 rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-sm text-danger">
              <span className="flex-1">Couldn&apos;t load slates: {slatesError}</span>
              <button onClick={loadAvailableSlates} className="shrink-0 underline hover:no-underline">
                Retry
              </button>
            </div>
          ) : availableSlates.length === 0 ? (
            <div className="flex items-center gap-2 rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-500">
              <span className="flex-1">No Classic NFL slates open for entry right now.</span>
              <button onClick={loadAvailableSlates} className="shrink-0 underline hover:no-underline">
                Refresh
              </button>
            </div>
          ) : (
            <select
              className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none"
              value={draftGroupId}
              onChange={(e) => setDraftGroupId(e.target.value)}
            >
              {availableSlates.map((s) => (
                <option key={s.dk_draft_group_id} value={s.dk_draft_group_id}>
                  {formatSlateLabel(s)}
                </option>
              ))}
            </select>
          )}
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Simulations
          <input
            type="number"
            className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none"
            value={numSims}
            onChange={(e) => setNumSims(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Lineups
          <input
            type="number"
            className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none"
            value={numLineups}
            onChange={(e) => setNumLineups(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Objective
          <select
            className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          >
            <option value="cash">Cash</option>
            <option value="single_entry">Single Entry</option>
            <option value="small_field_gpp">Small-Field GPP</option>
            <option value="large_field_gpp">Large-Field GPP</option>
          </select>
        </label>
      </div>

      <button
        onClick={handleBuild}
        disabled={building || !draftGroupId}
        className="rounded bg-accent px-4 py-2 text-sm font-semibold text-surface transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-40"
      >
        {building ? "Building…" : "BUILD SLATE"}
      </button>

      {events.length > 0 && (
        <ul className="mt-5 space-y-1.5 border-t border-surface-border pt-4 font-mono text-xs">
          {events.map((evt) => (
            <li key={evt.step} className={`flex items-start gap-2 ${STATUS_COLOR[evt.status] || "text-slate-300"}`}>
              <span className="w-4 shrink-0">{STATUS_ICON[evt.status] || "•"}</span>
              <span className="w-52 shrink-0 text-slate-500">{STEP_LABELS[evt.step] || evt.step}</span>
              <span>{evt.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
