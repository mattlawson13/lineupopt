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

export interface DkContest {
  dk_contest_id: string;
  name: string;
  entry_fee: number;
  total_prizes: number;
  max_entries: number;
  is_guaranteed: boolean;
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
  injury_alert_detail: string | null;
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

export interface ResolutionSummary {
  slate_resolution_id: string;
  our_best_actual_points: number;
  retro_optimal_points: number;
  retro_optimal_lineup_id: string | null;
  players_resolved: number;
  mae: number;
  bias: number;
  biggest_misses: { name: string; position: string; projected: number; actual: number; error: number }[];
}

export interface ResolutionSummaryStats {
  slates_resolved: number;
  avg_projection_mae: number | null;
  avg_projection_bias: number | null;
}

export interface ResolutionPatterns {
  note: string;
  by_contest_type: Record<
    string,
    {
      slates_resolved: number;
      avg_salary_used_pct: number;
      stack_type_distribution: Record<string, { count: number; pct: number }>;
      avg_projection_mae: number;
      avg_projection_bias: number;
    }
  >;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

export const api = {
  listSlates: () => getJSON<Slate[]>("/api/slates"),
  listAvailableDkSlates: () => getJSON<AvailableDkSlate[]>("/api/slates/available"),
  listContestsForDraftGroup: (dkDraftGroupId: string) =>
    getJSON<DkContest[]>(`/api/slates/available/${dkDraftGroupId}/contests`),
  getSlate: (id: string) => getJSON<Slate & { games: Game[] }>(`/api/slates/${id}`),
  getSlatePlayers: (id: string) => getJSON<PlayerRow[]>(`/api/slates/${id}/players`),
  getSlateGames: (id: string) => getJSON<Game[]>(`/api/slates/${id}/games`),
  listLineups: (slateId: string) => getJSON<Lineup[]>(`/api/lineups?slate_id=${slateId}`),
  getPlayerDetail: (playerId: string, slateId: string) =>
    getJSON<any>(`/api/players/${playerId}?slate_id=${slateId}`),

  streamBuildSlate: (
    body: {
      dk_draft_group_id: string;
      num_simulations: number;
      num_lineups: number;
      objective: string;
      dk_contest_id?: string;
      max_player_exposure_pct?: number;
      max_captain_exposure_pct?: number;
    },
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

  lateSwapLineup: async (lineupId: string) => {
    const res = await fetch(`${API_BASE}/api/lineups/${lineupId}/late-swap`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
    return body as { lineups: Lineup[]; kept_from_original: number; swapped_slots: number };
  },

  resolveSlate: async (slateId: string) => {
    const res = await fetch(`${API_BASE}/api/slates/${slateId}/resolve`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
    return body as ResolutionSummary;
  },
  getResolutionPatterns: () => getJSON<ResolutionPatterns>("/api/resolutions/patterns"),

  exportDkCsv: async (slateId: string) => {
    const res = await fetch(`${API_BASE}/api/lineups/export_dk_csv?slate_id=${slateId}`);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const match = res.headers.get("content-disposition")?.match(/filename="([^"]+)"/);
    const filename = match?.[1] || `dk_upload_${slateId}.csv`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // The reliable path: fills lineups into the actual file DraftKings gave
  // the user (its Export Player List / a contest's bulk-upload template),
  // preserving everything else (Entry ID/Contest ID columns if present,
  // the player-ID reference table) exactly as DK provided it. A
  // from-scratch file (exportDkCsv above) isn't reliably accepted by DK's
  // own uploader on its own.
  mergeDkCsv: async (slateId: string, file: File) => {
    const form = new FormData();
    form.append("slate_id", slateId);
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/lineups/merge_dk_csv`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const match = res.headers.get("content-disposition")?.match(/filename="([^"]+)"/);
    const filename = match?.[1] || `dk_upload_${slateId}.csv`;
    let stats: { rows_filled: number; rows_appended: number; lineups_used: number; lineups_total: number } | null = null;
    try {
      stats = JSON.parse(res.headers.get("x-export-stats") || "null");
    } catch {
      stats = null;
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return stats;
  },

  getResolutionSummary: () => getJSON<ResolutionSummaryStats>("/api/resolutions/summary"),

  resolveAllSlates: async () => {
    const res = await fetch(`${API_BASE}/api/resolutions/resolve_all`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
    return body as {
      total_slates: number;
      resolved: number;
      skipped_already_resolved: number;
      unavailable: number;
      errors: number;
      results: { slate_id: string; name: string; status: string; detail?: string }[];
    };
  },

  captureAllOpenSlates: async () => {
    const res = await fetch(`${API_BASE}/api/slates/capture_all_open`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
    return body as {
      open_draft_groups: number;
      already_captured: number;
      newly_captured: number;
      skipped: number;
      errors: number;
      results: { dk_draft_group_id: string; status: string; detail?: string; slate_id?: string; teams?: number; games?: number; players?: number }[];
    };
  },
};
