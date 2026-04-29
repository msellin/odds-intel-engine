# OddsIntel Engine — Agent Instructions

## Project Documentation

All project documentation lives in this repo (`odds-intel-engine/`). Before starting any task, read the relevant files:

| File | Purpose |
|------|---------|
| `PRIORITY_QUEUE.md` | **Master task list** — all open tasks across all docs, in priority order. Update status here first. |
| `ROADMAP.md` | Product vision, tier structure, milestones, system state, bot strategy, launch checklist |
| `MODEL_ANALYSIS.md` | Prediction model architecture, AI evaluations, improvement roadmap |
| `SIGNALS.md` | Every signal we collect (inventory, storage, flow into model) + 4-phase UX strategy for surfacing them (SUX-1 to SUX-12) |
| `TIER_ACCESS_MATRIX.md` | Feature matrix per tier (Anonymous/Free/Pro/Elite), conversion hooks, route protection |
| `WORKFLOWS.md` | Pipeline architecture — all scheduled jobs, order, manual run instructions, data sources |
| `DATA_SOURCES.md` | Data source architecture, API-Football integration status, alternatives evaluated |
| `INFRASTRUCTURE.md` | Full infra stack, current costs, and projections by growth phase |
| `data/model_results/SOCCER_FINDINGS.md` | Soccer model iterations and backtest results (archival) |
| `data/model_results/TENNIS_FINDINGS.md` | Tennis model iterations and backtest results (archival) |
| `data/model_results/MEGA_BACKTEST_FINDINGS.md` | 354K match backtest across 275 leagues (archival) |

## Task Lifecycle — Every Task Must Follow This Exactly

This protocol exists because parallel agents caused real production bugs when docs drifted. Follow it without exception.

### Before writing any code

1. **Read `PRIORITY_QUEUE.md`** — check the task's current status. If it is already `🔄 In Progress`, stop and tell the user. Do not start parallel work on a task already claimed.
2. **Mark it `🔄 In Progress`** in `PRIORITY_QUEUE.md` — update the Status column immediately, before touching any code. This is the lock that prevents two agents stepping on each other.
3. **Read every doc relevant to the task** — at minimum: TIER_ACCESS_MATRIX.md if touching any tier/gating logic; SIGNALS.md if touching signals or match detail; WORKFLOWS.md if touching the pipeline; ROADMAP.md system state if touching what's built.

### While implementing

- If a task depends on something another task was supposed to build, **verify it was actually built** before assuming it exists. Read the code — do not trust doc status alone.
- If you discover a related bug or gap, **log it in PRIORITY_QUEUE.md** before moving on. Never silently fix something unrelated without tracking it.

### When done — before committing

Update **all** of the following that apply. "Not relevant" is almost never true for more than 2 of these:

| Doc | Update when |
|-----|-------------|
| `PRIORITY_QUEUE.md` | Always — change status to ✅ Done with date |
| `ROADMAP.md` (Current System State) | Any change to what's built or what tier sees what |
| `SIGNALS.md` | Any change to signal collection, storage, or UX surface |
| `TIER_ACCESS_MATRIX.md` | Any change to what tier can see or do |
| `WORKFLOWS.md` | Any change to pipeline jobs or schedule |
| `DATA_SOURCES.md` | Any change to data sources or coverage |
| `INFRASTRUCTURE.md` | Any change to costs, services, or infra |

Then commit docs **in the same commit as the code**. Never separate them — a code commit without doc update is an incomplete task.

### Status values for PRIORITY_QUEUE.md

| Symbol | Meaning |
|--------|---------|
| ⬜ | Not started |
| 🔄 In Progress | Claimed — another agent must not start this |
| ✅ Done YYYY-MM-DD | Complete and documented |

## Keeping Docs Updated

Do not let docs drift from reality. If you notice something marked TODO that is already done, fix it. If you notice a doc describing behaviour that no longer matches the code, fix the doc in the same commit.

- Manual steps and launch checklist live in `ROADMAP.md` (Launch Checklist section)
- Retired docs (BACKLOG, PROGRESS, NEXT_STEPS, research_findings) have been deleted — history is in git

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
