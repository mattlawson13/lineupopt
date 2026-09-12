# LineupOpt — DraftKings DFS Projection, Simulation & Optimization Platform

A quant-grade DraftKings DFS analytics platform: proprietary projections,
Monte Carlo simulation, correlation modeling, and portfolio-aware lineup
optimization — built NFL-first, architected so Soccer (and other sports)
can be added without rewriting the core system.

This is not a lineup generator that maximizes projected points. It's a
pipeline: **data → projections → ownership → correlations → simulation →
optimization → AI-ranked, explained lineups**, with every number
traceable back to what produced it.

## Status: real, working, live-tested

Every layer described below was built AND exercised end-to-end against
**live current DraftKings NFL data** in this repo's own dev environment —
not a mock. `BUILD SLATE` was run against a real DK draft group, producing
real players, real salaries, real simulated distributions, real optimized
lineups with real QB/pass-catcher stacks, viewed in a real browser. See
"What's real vs. scaffolded" below for the honest breakdown of depth per
component.

---

## 1. Architecture

```
backend/app/
  config/          YAML-driven scoring rules, roster rules, ensemble
                    weights, sim/optimization/ownership/correlation
                    settings — nothing is hardcoded in Python.
  models/           SQLAlchemy schema — the full spec'd table set.
  data_sources/     One adapter class per provider (DataSource interface).
  normalization/    Cross-source player/team identity resolution.
  features/         Usage snapshots, game environment, matchup z-scores.
  projections/nfl/  Component-based per-position projection models
                    (QB/RB/WR/TE/K/DST) + ensemble blending.
  ownership/        Transparent linear ownership model + leverage.
  correlations/     Empirical + prior-based pairwise correlation engine.
  simulation/       Vectorized Monte Carlo (game-script + Gaussian copula).
  optimization/     PuLP ILP optimizer, DK rules, stacking, diversification.
  ai/               Rule-based lineup ranking + "why" explanation layer.
  backtesting/      Out-of-sample projection accuracy vs. real outcomes.
  ingestion/        slate_builder.py — orchestrates the whole BUILD SLATE
                    pipeline; validation.py — data-quality gates.
  sports/           Sport abstraction (NFL fully implemented; Soccer
                    scaffolded — see section 4).
  api/              FastAPI routes.

frontend/           Next.js 14 + TypeScript + Tailwind dashboard.
```

Every major component is swappable: change a data source without
touching the projection engine; change scoring rules via YAML without
touching the optimizer; add a sport by implementing `sports/base.py`'s
interface.

## 2. Data sources — what's real, what needs a key, what's manual

| Source | Used for | Status |
|---|---|---|
| **DraftKings** (`api.draftkings.com` / `www.draftkings.com` public JSON) | Slate, players, salaries, positions, games, roster status | ✅ Live, no key. Verified against the real DK site endpoints — same calls the DK website itself makes. |
| **DraftKings CSV export** | Same, offline fallback | ✅ Zero-scraping path: parses DK's own "Export to CSV" file. Most robust option if DK's JSON shape ever changes. |
| **nflverse-data** (GitHub release assets) | Historical stats, rosters, snap counts, depth charts, schedules | ✅ Live, free, no key. Community-standard successor to nflfastR's data releases. |
| **Open-Meteo** | Weather (temp/wind/precip) | ✅ Live, free, no key. |
| **The Odds API** | Vegas spread/total | ⚠️ Requires a free-tier API key (`ODDS_API_KEY` in `.env`). Without it, the build falls back to neutral (0 spread / 44 total) defaults and flags it clearly in the build log — never silently. |
| **ESPN** (`site.api.espn.com`) | Injuries, news | ⚠️ Public, no-key endpoint, but returned 403 in this sandboxed dev environment (Akamai bot protection on this network). Falls back to manual import (`POST /api/injuries/import`, `/api/news/import`) with a clear warning. May work fine from a normal residential/cloud IP — worth re-testing in your deployment. |
| **FantasyPros / other paid projections** | `source_projection_1/2/3` | ❌ Not scraped (paywalled, ToS-restricted). Use `POST /api/projections/import` with a CSV you export from the provider. |

This is the project's own explicit policy (see spec): never fabricate an
endpoint, never scrape a paywalled/ToS-restricted source, always provide a
legitimate manual-import fallback.

## 3. What's real vs. scaffolded

**Fully implemented and tested:**
- DK ingestion (live API + CSV), slate/team/game/player persistence
- Component-based NFL projection model (QB/RB/WR/TE/K/DST) with Vegas,
  usage-trend, matchup, weather, and injury-availability adjustments —
  every number has an additive "Why?" breakdown
- Projection ensemble blending (model + market + manually-imported sources,
  configurable weights)
- Ownership model (transparent linear model, softmax-normalized to roster
  slots, chalk/contrarian scores, leverage)
- Correlation engine (empirical-when-available + prior + conditional
  game-total/spread multipliers)
- Monte Carlo simulation (game-script scenarios conditioned on Vegas,
  Gaussian-copula correlated draws, full percentile/probability output) —
  10k sims in ~3s, 50k in ~14s on a full 224-player/14-game slate
- DK-legal ILP optimizer (PuLP): salary cap, roster/FLEX rules, team/game
  limits, locks/excludes, forced QB+pass-catcher stacking
- Portfolio generation: exposure caps, overlap/uniqueness control, stack
  rotation across a slate's best game environments
- Rule-based AI lineup ranking + human-readable "why this lineup" panel
- Out-of-sample backtesting engine (real nflverse historical data, strictly
  no future leakage)
- Data validation gate (missing salary, invalid position, duplicate
  player, projection outliers, etc.)
- Full FastAPI layer + SSE-streamed `BUILD SLATE` + Next.js dashboard
  (player pool, lineup view, player detail with distribution chart)
- 59 passing pytest tests across scoring, constraints, projections,
  ownership, correlation, simulation, stacking, exposure, validation

**Scaffolded — real interfaces, not yet full depth:**
- **AI chat assistant** (spec section 35/36): not built this pass. The
  rule-based lineup evaluator (`ai/lineup_evaluator.py`) already enforces
  "never fabricate a stat" by only ever reading structured DB output; a
  conversational layer on top is a natural next step using the same rule.
- **Late swap** (spec section 22): not implemented. The manual lineup
  builder (`POST /api/lineups/generate`) already supports re-optimizing
  with locks/excludes against a slate's existing projections, which is
  most of the mechanism — it just isn't wired to "which players have
  locked" contest-clock logic yet.
- **Soccer** (`sports/soccer/`): config (DK scoring/roster rules) and a
  first pass at soccer-specific correlation relationships are real; the
  projection model and game-script simulation raise `NotImplementedError`
  with an explanation rather than silently running NFL math on soccer
  players. See that module's docstring for exactly what's needed.
- **Backtesting breadth**: accurate for position/usage-tier; salary-range
  and game-total accuracy are intentionally omitted rather than faked,
  since there's no legitimate free historical DK-salary or Vegas-line
  archive wired up yet (see `backtesting/engine.py` docstring).

## 4. Running it locally

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # fill in ODDS_API_KEY etc. if you have them
alembic upgrade head      # or rely on create_all() at app startup for dev
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at your backend
npm run dev
```
Open http://localhost:3000, go to **Build Slate**, paste a live DK draft
group ID (find one via DK's site — e.g. inspect `getcontests?sport=NFL`),
and click **BUILD SLATE**.

### Postgres/Redis (optional — SQLite is the zero-setup default)
```bash
docker compose up -d postgres redis
# then set DATABASE_URL=postgresql+psycopg://lineupopt:lineupopt@localhost:5432/lineupopt in backend/.env
```

### Tests
```bash
cd backend && source .venv/bin/activate && python -m pytest -q
```

## 5. The BUILD SLATE pipeline

`POST /api/slates/build` (or `/build-from-csv`) streams Server-Sent Events
as it runs, matching this sequence:

```
DK slate → data validation → teams/games/players persisted → Vegas lines
→ weather → injuries → historical usage (nflverse) → projection ensemble
→ ownership → correlation matrix → Monte Carlo simulation
→ DK-legal optimization (with stacking) → AI ranking/explanation
```

Every step degrades gracefully and reports its status (`success` /
`warning` / `error`) rather than failing the whole build or silently
using stale data — this is enforced by design (`ingestion/slate_builder.py`,
`data_sources/base.py`'s `SourceUnavailableError`).

## 6. Configuration

Every tunable number lives in `backend/app/config/*.yaml`, never scattered
in code:
- `dk_scoring_nfl.yaml` — DK's actual scoring rules
- `dk_roster_rules_nfl.yaml` — salary cap, roster slots, FLEX eligibility
  (classic + showdown)
- `projection_weights.yaml` — ensemble blend weights, usage recency/shrinkage
- `simulation_settings.yaml` — sim count, game-script probabilities,
  per-position variance
- `optimization_settings.yaml` — contest-mode objective weights
  (cash/single-entry/small-GPP/large-GPP), default exposure/diversification
- `ownership_settings.yaml`, `correlation_settings.yaml`

## 7. Roadmap (see spec's own phasing)

- **Phase 1–2 (done):** DK ingestion, projections, ownership, correlation,
  simulation, optimizer, stacking, portfolios.
- **Phase 3 (partial):** backtesting ✅, AI lineup ranking ✅, AI chat
  assistant ⬜, late swap ⬜.
- **Phase 4 (scaffolded):** Soccer projection/simulation/optimizer ⬜.

## 8. Known limitations worth knowing before you trust the numbers

- Projection coefficients (Vegas/matchup/usage sensitivities per position)
  are reasonable starting priors, not yet calibrated by backtesting —
  `backtesting/engine.py` is exactly the tool to do that calibration with,
  and an early run against 2024 weeks 5–6 showed MAE ≈ 6.0 pts,
  correlation ≈ 0.47 overall (see it yourself: `GET /api/backtest?season=2024&weeks=5,6`).
- Without `ODDS_API_KEY`, every projection runs on a neutral game
  environment (no real Vegas signal) — get a key for real accuracy.
- ESPN injury data may be blocked from some hosting networks; verify it
  works from your actual deployment, or use manual import.
