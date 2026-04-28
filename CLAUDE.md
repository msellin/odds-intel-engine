# OddsIntel Engine — Agent Instructions

## Project Documentation

All project documentation lives in this repo (`odds-intel-engine/`). Before starting any task, read the relevant files:

| File | Purpose |
|------|---------|
| `ROADMAP.md` | Product vision, tier structure, milestone goals, launch checklist, open decisions |
| `BACKLOG.md` | ⚠️ Retired 2026-04-29 — all tasks are in PRIORITY_QUEUE.md |
| `PROGRESS.md` | Current status of both engine and frontend, data coverage, architecture |
| `INFRASTRUCTURE.md` | Full infra stack, current costs, and projections by growth phase |
| `DATA_SOURCES.md` | Data source architecture, API-Football integration status, alternatives evaluated |
| `MODEL_ANALYSIS.md` | Prediction model architecture, AI evaluations, improvement roadmap |
| `RESEARCH.md` | Master research document — sports, APIs, pricing, architecture |
| `PRIORITY_QUEUE.md` | **Master task list** — all open tasks across all docs, in priority order. Update status here first. |
| `data/model_results/NEXT_STEPS.md` | Engine-specific priority queue and data gaps |
| `data/model_results/SOCCER_FINDINGS.md` | Soccer model iterations and backtest results |
| `data/model_results/TENNIS_FINDINGS.md` | Tennis model iterations and backtest results |
| `data/model_results/MEGA_BACKTEST_FINDINGS.md` | 354K match backtest across 275 leagues |

## Keeping Docs Updated

When you complete a task, update the relevant documentation:
- Mark tasks done in `PRIORITY_QUEUE.md` (master task list — update status column here first)
- Update `PROGRESS.md` if you change what's built or fix a data issue
- Manual steps and launch checklist live in `ROADMAP.md` (Launch Checklist section)
- Do NOT update `BACKLOG.md` — it is retired

Do not let docs drift from reality. If you notice something marked TODO that is already done, fix it.

## Architecture

```
API-Football Ultra ($29/mo)  -> PRIMARY: fixtures, odds (13 bookmakers), live data,
                                lineups, injuries, standings, H2H, events, player stats
Kambi API (free)             -> Supplementary odds for 41 leagues
Sofascore API (free)         -> xG post-match only (fallback fixture source)
ESPN (free)                  -> Settlement results backup
                                         |
                    Python Daily Pipeline (08:00 UTC) — T2/T3/T9/T10 enrichment + predictions
                    AI News Checker (09:00 UTC, Gemini 2.5 Flash) — non-injury news only
                    Settlement (21:00 UTC) — T4/T8/T12 post-match enrichment
                    Odds Snapshots (every 2h, 06-22 UTC)
                    Live Tracker (every 5min, 12-22 UTC) — T5/T6/T7/T8 live data
                                         |
                               Supabase Database (15 tables)
                                         |
                       Next.js Frontend (odds-intel-web) -> Vercel (not yet deployed)
```

## Frontend Repo

The frontend lives at `../odds-intel-web/` (sibling directory). It has its own `CLAUDE.md` with Next.js-specific rules. All shared project docs are here in the engine repo — do not create duplicate docs in the frontend repo.

## Key Technical Details

- Python 3.14, dependencies in `requirements.txt`
- Supabase for DB (PostgreSQL) — migrations in `supabase/migrations/`
- GitHub Actions for automation — workflows in `.github/workflows/`
- Credentials in `.env` (gitignored) — never commit secrets
- Prediction model: Poisson + XGBoost blend with 3-tier fallback (A/B/C)
- 6 paper trading bots running since 2026-04-27
