"use client";

import { useMemo, useState } from "react";
import { PlayerRow } from "@/lib/api";
import clsx from "clsx";

type SortKey = keyof Pick<
  PlayerRow,
  "salary" | "projection" | "ceiling" | "ownership_pct" | "value" | "leverage"
> | "name";

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DST"];

export default function PlayerPoolTable({
  players,
  onSelectPlayer,
}: {
  players: PlayerRow[];
  onSelectPlayer: (p: PlayerRow) => void;
}) {
  const [position, setPosition] = useState("ALL");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("projection");
  const [sortDesc, setSortDesc] = useState(true);

  const teams = useMemo(() => Array.from(new Set(players.map((p) => p.team))).sort(), [players]);
  const [team, setTeam] = useState("ALL");

  const filtered = useMemo(() => {
    let rows = players;
    if (position !== "ALL") rows = rows.filter((p) => p.position === position);
    if (team !== "ALL") rows = rows.filter((p) => p.team === team);
    if (search.trim()) rows = rows.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));
    rows = [...rows].sort((a, b) => {
      const av = sortKey === "name" ? a.name : (a[sortKey] ?? -Infinity);
      const bv = sortKey === "name" ? b.name : (b[sortKey] ?? -Infinity);
      if (typeof av === "string" || typeof bv === "string") {
        return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
      }
      return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
    return rows;
  }, [players, position, team, search, sortKey, sortDesc]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDesc((d) => !d);
    else {
      setSortKey(key);
      setSortDesc(true);
    }
  };

  const Th = ({ label, k }: { label: string; k: SortKey }) => (
    <th
      onClick={() => toggleSort(k)}
      className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500 hover:text-slate-300"
    >
      {label} {sortKey === k && (sortDesc ? "▼" : "▲")}
    </th>
  );

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised">
      <div className="flex flex-wrap items-center gap-3 border-b border-surface-border p-3">
        <input
          placeholder="Search player…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm focus:border-accent focus:outline-none"
        />
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
        <select
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          className="rounded border border-surface-border bg-surface px-2 py-1.5 text-sm focus:border-accent focus:outline-none"
        >
          <option value="ALL">ALL TEAMS</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="ml-auto text-xs text-slate-500">{filtered.length} players</span>
      </div>

      <div className="max-h-[70vh] overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-raised">
            <tr className="border-b border-surface-border">
              <Th label="Player" k="name" />
              <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Pos</th>
              <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">Team</th>
              <Th label="Salary" k="salary" />
              <Th label="Proj" k="projection" />
              <Th label="Ceil" k="ceiling" />
              <Th label="Value" k="value" />
              <Th label="Own%" k="ownership_pct" />
              <Th label="Lev" k="leverage" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr
                key={p.dk_player_id}
                onClick={() => onSelectPlayer(p)}
                className="cursor-pointer border-b border-surface-border/60 hover:bg-surface/60"
              >
                <td className="px-3 py-1.5 font-medium">{p.name}</td>
                <td className="px-3 py-1.5 text-slate-400">{p.position}</td>
                <td className="px-3 py-1.5 text-slate-400">
                  {p.team} <span className="text-slate-600">vs {p.opponent}</span>
                </td>
                <td className="mono-num px-3 py-1.5">${p.salary.toLocaleString()}</td>
                <td className="mono-num px-3 py-1.5 text-accent">{p.projection?.toFixed(1)}</td>
                <td className="mono-num px-3 py-1.5">{p.ceiling?.toFixed(1) ?? "—"}</td>
                <td className="mono-num px-3 py-1.5">{p.value?.toFixed(2)}</td>
                <td className="mono-num px-3 py-1.5">{p.ownership_pct?.toFixed(1) ?? "—"}%</td>
                <td
                  className={clsx(
                    "mono-num px-3 py-1.5",
                    (p.leverage ?? 0) > 15 ? "text-accent" : (p.leverage ?? 0) < -10 ? "text-danger" : ""
                  )}
                >
                  {p.leverage?.toFixed(1) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
