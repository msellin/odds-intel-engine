# OddsIntel Engine — Agent Instructions

## Project Documentation

All project documentation lives in this repo (`odds-intel-engine/`). Before starting any task, read the relevant files:

| File | Purpose |
|------|---------|
| `PRIORITY_QUEUE.md` | **Master task list** — all open tasks across all docs, in priority order. Update status here first. |
| `ROADMAP.md` | Product vision, tier structure, milestones, system state, bot strategy, launch checklist |
| `MODEL_ANALYSIS.md` | Prediction model architecture, AI evaluations, improvement roadmap |
| `SIGNAL_ARCHITECTURE.md` | Every signal we collect — inventory, storage, timeline, how signals flow into the model |
| `SIGNAL_UX_ROADMAP.md` | How to surface signals in UI — 4-phase plan from UX reviews, task IDs SUX-1 to SUX-12 |
| `TIER_ACCESS_MATRIX.md` | Feature matrix per tier (Anonymous/Free/Pro/Elite), conversion hooks, route protection |
| `DATA_SOURCES.md` | Data source architecture, API-Football integration status, alternatives evaluated |
| `INFRASTRUCTURE.md` | Full infra stack, current costs, and projections by growth phase |
| `RESEARCH.md` | Master research document — sports, APIs, pricing, architecture (archival, 2026-04-26) |
| `UI_RESEARCH.md` | UI/UX patterns from competitor analysis (archival, 2026-04-27) |
| `data/model_results/SOCCER_FINDINGS.md` | Soccer model iterations and backtest results (archival) |
| `data/model_results/TENNIS_FINDINGS.md` | Tennis model iterations and backtest results (archival) |
| `data/model_results/MEGA_BACKTEST_FINDINGS.md` | 354K match backtest across 275 leagues (archival) |

## Keeping Docs Updated

When you complete a task, update the relevant documentation:
- Mark tasks done in `PRIORITY_QUEUE.md` (master task list — update status column here first)
- Update `ROADMAP.md` Current System State section if you change what's built
- Manual steps and launch checklist live in `ROADMAP.md` (Launch Checklist section)
- Retired docs (BACKLOG, PROGRESS, NEXT_STEPS, research_findings) have been deleted — history is in git

Do not let docs drift from reality. If you notice something marked TODO that is already done, fix it.

## Database Migrations

**All migrations live in `supabase/migrations/` in this repo (odds-intel-engine) — never in odds-intel-web.**

- Naming convention: `NNN_short_description.sql` — e.g. `016_free_user_features.sql`
- NNN = zero-padded sequential number, next is always current highest + 1
- Applied automatically via GitHub Actions (`migrate.yml`) on any push to main that touches `supabase/migrations/`
- Can also be triggered manually via Actions → "OddsIntel — Run DB Migrations" → Run workflow

## Architecture

```
API-Football Ultra ($29/mo)  -> PRIMARY: fixtures, odds (13 bookmakers), live data,
                                lineups, injuries, standings, H2H, events, player stats
Kambi API (free)             -> Supplementary odds for 41 leagues
ESPN (free)                  -> Settlement results backup
                                         |
                    ① Fixtures    (04:00 UTC) — AF fixtures + league coverage (weekly)
                    ② Enrichment  (04:15/12:00/16:00 UTC) — standings, H2H, team stats, injuries
                    ③ Odds        (every 2h 05-22 UTC) — AF bulk odds + Kambi
                    ④ Predictions (05:30 UTC) — AF predictions
                    ⑤ Betting     (06:00 UTC) — Poisson/XGBoost model + signals + bet placement
                    ⑥ Live Tracker (every 5min, 12-22 UTC) — live scores, odds, events, lineups
                    ⑦ News Checker (09:00/12:30/16:30/19:30 UTC) — Gemini AI analysis
                    ⑧ Settlement  (21:00 UTC) — settle bets, post-match stats, ELO, CLV
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
