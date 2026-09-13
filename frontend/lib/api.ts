// Empty string = relative to this app's own origin, proxied server-side to
// the backend via next.config.js's rewrites() — this is what lets the app
// work correctly whether it's opened as localhost or through a public
// tunnel (ngrok etc.), without exposing the backend itself. Set
// NEXT_PUBLIC_API_BASE_URL to override with an absolute URL if needed.
// Trailing slash stripped so `${API_BASE}${path}` (path always starts with
// "/") never produces a double slash — https://host.com// -> 404 on
// FastAPI/Starlette's exact path matching. This bit the deployed app for
// real: NEXT_PUBLIC_API_BASE_URL was set with a trailing slash on Vercel,
// which next.config.js's rewrite already guarded against but this
// client-side path didn't.
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/+$/, "");

export interface AvailableDkSlate {
  dk_draft_group_id: string;
  contest_format: "classic" | "showdown";
  start_time_utc: string;
  contest_count: number;
  sample_contest_name: string;
}

export interface Slate {
  id: string;
  sport: string;
  contest_type: string;
  dk_draft_group_id: string;
  name: string;
  season: number;
  week: number;
  start_time_utc: string;
  game_ids: string[];
  source: string;
  imported_at: string;
}

export interface Game {
  id: string;
  season: number;
  week: number;
  home_team: string;
  away_team: string;
  kickoff_utc: string;
  is_dome: boolean;
  home_score_final: number | null;
  away_score_final: number | null;
}

export interface SimSummary {
  mean: number;
  median: number;
  std_dev: number;
  floor: number;
  ceiling: number;
  percentiles: Record<string, number>;
  prob_3x_salary: number;
  prob_4x_salary: number;
  prob_5x_salary: number;
  prob_6x_salary: number;
  prob_top1pct: number;
  prob_top5pct: number;
}

export interface PlayerRow {
  dk_player_id: string;
  player_id: string;
  name: string;
  position: string;
  team: string;
  opponent: string;
  game_id: string;
  roster_status: string;
  salary: number;
  projection: number;
  median: number | null;
  floor: number | null;
  ceiling: number | null;
  value: number;
  ownership_pct: number | null;
  leverage: number | null;
  chalk_score: number | null;
  contrarian_score: number | null;
  sim: SimSummary | null;
}

export interface LineupPlayerRow {
  player_id: string;
  name: string;
  position: string;
  team: string;
  roster_slot: string;
  salary: number;
  projected_points: number;
}

export interface Lineup {
  id: string;
  salary_used: number;
  salary_remaining: number;
  projected_points: number;
  ceiling: number;
  floor: number;
  avg_ownership_pct: number | null;
  leverage_score: number | null;
  stack_type: string | null;
  stack_description: string | null;
  uniqueness_score: number | null;
  ai_rank: number | null;
  ai_score: number | null;
  explanation: string | null;
  players: LineupPlayerRow[];
}

export interface BuildProgressEvent {
  step: string;
  status: "running" | "success" | "warning" | "error";
  detail: string;
  data: Record<string, unknown>;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

export const api = {
  listSlates: () => getJSON<Slate[]>("/api/slates"),
  listAvailableDkSlates: () => getJSON<AvailableDkSlate[]>("/api/slates/available"),
  getSlate: (id: string) => getJSON<Slate & { games: Game[] }>(`/api/slates/${id}`),
  getSlatePlayers: (id: string) => getJSON<PlayerRow[]>(`/api/slates/${id}/players`),
  getSlateGames: (id: string) => getJSON<Game[]>(`/api/slates/${id}/games`),
  listLineups: (slateId: string) => getJSON<Lineup[]>(`/api/lineups?slate_id=${slateId}`),
  getPlayerDetail: (playerId: string, slateId: string) =>
    getJSON<any>(`/api/players/${playerId}?slate_id=${slateId}`),

  streamBuildSlate: (
    body: { dk_draft_group_id: string; num_simulations: number; num_lineups: number; objective: string },
    onEvent: (evt: BuildProgressEvent) => void,
    onDone: () => void,
    onError: (msg: string) => void
  ) => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/slates/build`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() || "";
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data:")) continue;
            const evt = JSON.parse(line.slice(5).trim()) as BuildProgressEvent;
            onEvent(evt);
          }
        }
        onDone();
      } catch (e: any) {
        onError(e?.message || String(e));
      }
    })();
  },

  generateLineups: (payload: Record<string, unknown>) =>
    fetch(`${API_BASE}/api/lineups/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => r.json()),
};
