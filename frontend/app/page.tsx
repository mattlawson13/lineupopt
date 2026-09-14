"use client";

import { useEffect, useState } from "react";
import BuildSlatePanel from "@/components/BuildSlatePanel";
import PlayerPoolTable from "@/components/PlayerPoolTable";
import LineupsPanel from "@/components/LineupsPanel";
import PlayerDetailDrawer from "@/components/PlayerDetailDrawer";
import { api, Lineup, PlayerRow, Slate } from "@/lib/api";

type Tab = "build" | "players" | "lineups";

// Slate.name is just "NFL Slate <dk_draft_group_id>" — the raw DK ID isn't
// meaningful to a person picking a slate, so build a human label from the
// fields that are (contest format, week, kickoff date) instead of showing it.
function formatSlateLabel(s: Slate): string {
  const type = s.contest_type === "showdown" ? "Showdown" : "Classic";
  const when = new Date(s.start_time_utc).toLocaleDateString(undefined, {
    weekday: "short",
    month: "numeric",
    day: "numeric",
  });
  return `${type} — Week ${s.week} (${when})`;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("build");
  const [slates, setSlates] = useState<Slate[]>([]);
  const [activeSlateId, setActiveSlateId] = useState<string | null>(null);
  const [players, setPlayers] = useState<PlayerRow[]>([]);
  const [lineups, setLineups] = useState<Lineup[]>([]);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listSlates()
      .then((s) => {
        setSlates(s);
        if (s.length && !activeSlateId) setActiveSlateId(s[0].id);
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadSlateData = (slateId: string) => {
    setActiveSlateId(slateId);
    api.getSlatePlayers(slateId).then(setPlayers).catch((e) => setError(String(e)));
    api.listLineups(slateId).then(setLineups).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    if (activeSlateId) loadSlateData(activeSlateId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSlateId]);

  const handleBuildComplete = (slateId: string) => {
    api.listSlates().then(setSlates);
    loadSlateData(slateId);
    setTab("players");
  };

  const activeSlate = slates.find((s) => s.id === activeSlateId);

  return (
    <main className="mx-auto max-w-[1400px] px-4 py-6">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            LineupOpt <span className="text-accent">NFL DFS</span>
          </h1>
          <p className="text-xs text-slate-500">Projection, simulation & optimization platform</p>
        </div>

        <div className="flex items-center gap-3">
          {slates.length > 0 && (
            <select
              value={activeSlateId ?? ""}
              onChange={(e) => loadSlateData(e.target.value)}
              className="rounded border border-surface-border bg-surface-raised px-3 py-1.5 text-sm focus:border-accent focus:outline-none"
            >
              {slates.map((s) => (
                <option key={s.id} value={s.id}>
                  {formatSlateLabel(s)}
                </option>
              ))}
            </select>
          )}
          <nav className="flex gap-1 rounded-lg border border-surface-border bg-surface-raised p-1">
            {(["build", "players", "lineups"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded px-3 py-1.5 text-sm font-medium capitalize transition ${
                  tab === t ? "bg-accent text-surface" : "text-slate-400 hover:text-slate-100"
                }`}
              >
                {t === "build" ? "Build Slate" : t}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {activeSlate && (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SummaryCard label="Slate" value={formatSlateLabel(activeSlate)} />
          <SummaryCard label="Week" value={String(activeSlate.week)} />
          <SummaryCard label="Games" value={String(activeSlate.game_ids.length)} />
          <SummaryCard label="Players" value={String(players.length)} />
        </div>
      )}

      {error && <div className="mb-4 rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</div>}

      {tab === "build" && <BuildSlatePanel onComplete={handleBuildComplete} />}
      {tab === "players" &&
        (players.length ? (
          <PlayerPoolTable players={players} onSelectPlayer={setSelectedPlayer} />
        ) : (
          <EmptyState text="No player pool yet — build a slate first." />
        ))}
      {tab === "lineups" && activeSlateId && <LineupsPanel lineups={lineups} slateId={activeSlateId} />}

      {selectedPlayer && activeSlateId && (
        <PlayerDetailDrawer player={selectedPlayer} slateId={activeSlateId} onClose={() => setSelectedPlayer(null)} />
      )}
    </main>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised px-4 py-3">
      <div className="truncate text-lg font-semibold">{value}</div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-10 text-center text-sm text-slate-500">
      {text}
    </div>
  );
}
