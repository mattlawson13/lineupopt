"use client";

import { useEffect, useState } from "react";
import { api, PlayerRow } from "@/lib/api";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function PlayerDetailDrawer({
  player,
  slateId,
  onClose,
}: {
  player: PlayerRow;
  slateId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getPlayerDetail(player.player_id, slateId)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [player.player_id, slateId]);

  const percentileData = detail?.simulation?.percentiles
    ? Object.entries(detail.simulation.percentiles).map(([k, v]) => ({ pct: `p${k}`, value: v as number }))
    : [];

  const why = detail?.why_panel;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={onClose}>
      <div
        className="h-full w-full max-w-lg overflow-y-auto border-l border-surface-border bg-surface-raised p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold">{player.name}</h2>
            <p className="text-sm text-slate-500">
              {player.position} · {player.team} vs {player.opponent} · ${player.salary.toLocaleString()}
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-100">
            ✕
          </button>
        </div>

        {loading && <p className="text-sm text-slate-500">Loading…</p>}

        {!loading && why && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">Why This Projection?</h3>
            <div className="space-y-1 rounded border border-surface-border bg-surface p-3 text-sm">
              <Row label="Projected fantasy points" value={why.projected_fantasy_points} bold />
              <Row label="Base projection" value={why.base_projection} />
              <Row label="Vegas adjustment" value={why.vegas_adjustment} signed />
              <Row label="Usage adjustment" value={why.usage_adjustment} signed />
              <Row label="Matchup adjustment" value={why.matchup_adjustment} signed />
              <Row label="Weather adjustment" value={why.weather_adjustment} signed />
              <Row label="Injury adjustment" value={why.injury_adjustment} signed />
              <Row label="Model uncertainty" value={why.model_uncertainty} prefix="±" />
            </div>
          </section>
        )}

        {!loading && detail?.ensemble && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">Ensemble</h3>
            <div className="grid grid-cols-4 gap-2 text-center text-xs">
              <Stat label="Floor" value={detail.ensemble.floor} />
              <Stat label="Median" value={detail.ensemble.median} />
              <Stat label="Ceiling" value={detail.ensemble.ceiling} />
              <Stat label="Std Dev" value={detail.ensemble.std_dev} />
            </div>
          </section>
        )}

        {!loading && percentileData.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">Simulated Distribution</h3>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={percentileData}>
                <XAxis dataKey="pct" stroke="#64748b" fontSize={11} />
                <YAxis stroke="#64748b" fontSize={11} />
                <Tooltip contentStyle={{ background: "#12161f", border: "1px solid #1f2430" }} />
                <Bar dataKey="value" fill="#3fd0a0" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            {detail.simulation && (
              <div className="mt-2 grid grid-cols-4 gap-2 text-center text-xs">
                <Stat label="3x%" value={(detail.simulation.prob_3x_salary * 100).toFixed(0) + "%"} />
                <Stat label="5x%" value={(detail.simulation.prob_5x_salary * 100).toFixed(0) + "%"} />
                <Stat label="Top1%" value={(detail.simulation.prob_top1pct * 100).toFixed(0) + "%"} />
                <Stat label="Top5%" value={(detail.simulation.prob_top5pct * 100).toFixed(0) + "%"} />
              </div>
            )}
          </section>
        )}

        {!loading && detail?.ownership && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">Ownership</h3>
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <Stat label="Projected" value={detail.ownership.projected_ownership_pct + "%"} />
              <Stat label="Chalk score" value={detail.ownership.chalk_score} />
              <Stat label="Contrarian" value={detail.ownership.contrarian_score} />
            </div>
          </section>
        )}

        {!loading && detail?.best_stacks?.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">Best Stacks</h3>
            <ul className="space-y-1 text-xs text-slate-300">
              {detail.best_stacks.map((s: any, i: number) => (
                <li key={i} className="flex justify-between">
                  <span>
                    {s.with} <span className="text-slate-500">({s.relationship})</span>
                  </span>
                  <span className="mono-num">{s.correlation.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, bold, signed, prefix }: { label: string; value: number; bold?: boolean; signed?: boolean; prefix?: string }) {
  const display = signed && value > 0 ? `+${value}` : `${prefix ?? ""}${value}`;
  return (
    <div className={`flex justify-between ${bold ? "font-semibold text-accent" : "text-slate-300"}`}>
      <span>{label}</span>
      <span className="mono-num">{display}</span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded bg-surface px-1 py-1.5">
      <div className="mono-num font-semibold text-slate-100">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}
