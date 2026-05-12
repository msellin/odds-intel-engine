# OddsIntel Engine — Agent Instructions

## Project Documentation

All project documentation lives in this repo (`odds-intel-engine/`). Before starting any task, read the relevant files.

### Doc location convention

**Root `/`** — Agent protocol docs. Things agents must read or update as part of their task workflow: task tracking, system state, model logic, pipeline architecture, tier gating, data sources, infra costs. If a doc belongs in the "update when done" checklist, it lives here.

**`docs/`** — Strategy, playbooks, reference, and execution content. Things you look up rather than act on in every task: engagement strategy, launch plan, Reddit execution, API reference docs, archival backtests.

### Root docs (agent protocol)

| File | Purpose |
|------|---------|
| `PRIORITY_QUEUE.md` | **Master task list** — all open tasks across all docs, in priority order. Update status here first. |
| `ROADMAP.md` | Product vision, tier structure, milestones, system state, bot strategy, launch checklist |
| `MODEL_ANALYSIS.md` | Prediction model architecture, AI evaluations, improvement roadmap |
| `MODEL_WHITEPAPER.md` | **Technical whitepaper** — full model description for data scientists and external review. **Must be updated whenever model logic changes.** |
| `SIGNALS.md` | Every signal we collect (inventory, storage, flow into model) + 4-phase UX strategy for surfacing them (SUX-1 to SUX-12) |
| `TIER_ACCESS_MATRIX.md` | Feature matrix per tier (Anonymous/Free/Pro/Elite), conversion hooks, route protection |
| `WORKFLOWS.md` | Pipeline architecture — all scheduled jobs, order, manual run instructions, data sources |
| `DATA_SOURCES.md` | Data source architecture, API-Football integration status, alternatives evaluated |
| `INFRASTRUCTURE.md` | Full infra stack, current costs, and projections by growth phase |

### docs/ (strategy, playbooks, reference)

| File | Purpose |
|------|---------|
| `docs/ENGAGEMENT_PLAYBOOK.md` | Engagement & growth strategy — social proof, AI features, email, SEO, retention hooks (ENG-1 to ENG-17) |
| `docs/LAUNCH_PLAN.md` | Launch phases (organic → paid), validation metrics, ad copy, pricing |
| `docs/REDDIT_LAUNCH.md` | Reddit execution — progress tracker, subreddit rules, all 6 post drafts |
| `docs/AF_ENDPOINT_FREQUENCY.md` | API-Football endpoint update frequencies vs our polling — identifies gaps |
| `docs/API-Football_Documentation_v3.9.3.pdf` | Full API-Football v3.9.3 docs (130 pages) — **local only** (gitignored, 7.1MB) |

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

**Always add a smoke test.** Every task must have at least one test in `scripts/smoke_test.py` before the commit. No exceptions — even code-only changes get a source-inspection test.

**Never run the full smoke suite locally.** The full suite takes ~60s and GitHub Actions runs it on every push to main — that's the gate, not your local run. Locally, run only your new test using the `--filter` flag:

```bash
python3 scripts/smoke_test.py --filter MY-NEW-TEST     # substring, case-insensitive
python3 scripts/smoke_test.py -f INPLAY-LAMBDA          # short form
```

The pipe-to-grep pattern (`smoke_test.py 2>&1 | grep ...`) does NOT save runtime — the suite still runs, only the output is filtered. Use `--filter`. If you broke something elsewhere, CI will catch it after push — don't burn local time on the full suite for routine tasks.

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
| `MODEL_WHITEPAPER.md` | **Any change to model logic** — calibration, features, ensemble, sizing, signals, ELO, or bot strategies |

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
                    ③ Odds        (every 30min 07-22 UTC) — AF bulk odds (13 bookmakers)
                    ④ Predictions (05:30 UTC) — AF predictions
                    ⑤ Betting     (06:00 UTC) — Poisson/XGBoost model + signals + bet placement (morning cohort)
                    ⑥ Live Tracker (30s/60s/5min tiered, 10-23 UTC) — live scores, odds, events, lineups
                    ⑦ News Checker (09:00/12:30/16:30/19:30 UTC) — Gemini AI analysis
                    ⑧ Settlement  (21:00 UTC) — settle bets, post-match stats, ELO, CLV
                    ⑨ Betting Refresh (09:30/11:00/13:30/15:00/17:30/19:00/20:30 UTC) — re-evaluation with fresh odds per KO window
                                         |
                               Supabase Database (15 tables)
                                         |
                       Next.js Frontend (odds-intel-web) -> Vercel (not yet deployed)
```

## Key Technical Details (Engine)

- Python 3.14, dependencies in `requirements.txt`
- Supabase for DB (PostgreSQL) — migrations in `supabase/migrations/`
- **Railway** for pipeline automation (`workers/scheduler.py` — long-running process, $5/mo)
- Direct PostgreSQL (psycopg2) for all `supabase_client.py` functions + live tracker; PostgREST kept for external callers (settlement, pipeline_utils)
- GitHub Actions kept for manual `workflow_dispatch` triggers + DB migrations only
- Credentials in `.env` (gitignored) — never commit secrets
- Prediction model: Poisson + XGBoost blend with 3-tier fallback (A/B/C)
- 16 paper trading bots running since 2026-04-27

---

## Frontend (`../odds-intel-web/`)

The frontend lives at `../odds-intel-web/` (sibling directory). All rules for it live here — do not create duplicate docs in the frontend repo.

### Stack

- Next.js 15 (App Router), TypeScript, Tailwind CSS
- Auth + DB: Supabase (`createSupabaseServer()` in server components, `createBrowserClient()` in client components)
- Payments: Stripe (checkout, webhook at `/api/stripe/webhook`, portal)
- Error monitoring: Sentry
- Deployment: Vercel

### Tier Gating Rules

Server-side gating is the only safe gating. Client-side gating hides UI but does not protect data.

- Tier is read from `profiles.tier` (values: `free`, `pro`, `elite`) + `profiles.is_superadmin`
- `isElite = is_superadmin || tier === 'elite'`
- `isPro = isElite || tier === 'pro'` ← Elite users are always also Pro
- Pro data (odds movement, events, lineups, stats, injuries) must only be **fetched** server-side when `isPro === true` — never fetch then conditionally hide client-side
- Pass `isPro` and `isElite` as props down to any component that changes its rendering by tier — do not assume a component receives them without checking

### Key Frontend Files

| File | Purpose |
|------|---------|
| `src/lib/engine-data.ts` | All Supabase queries — data fetching layer |
| `src/lib/signal-labels.ts` | Signal translation layer — raw floats → human labels |
| `src/app/(app)/matches/[id]/page.tsx` | Match detail — server-side tier gating |
| `src/app/(app)/value-bets/page.tsx` | Value bets — server-side tier gating |
| `src/components/match-detail-free.tsx` | Free-tier match detail (pass `isPro` to suppress Pro CTAs for Pro/Elite users) |
| `src/components/match-signal-summary.tsx` | Intelligence Summary (SUX-4) |
| `src/components/signal-accordion.tsx` | Signal group accordion (SUX-5) |
| `src/components/signal-delta.tsx` | Signal delta — what changed since last visit (SUX-9) |
| `src/components/live-odds-chart.tsx` | Live in-play odds chart (FE-LIVE) |
| `src/components/bet-explain-button.tsx` | LLM bet explanation button (BET-EXPLAIN) |
| `src/app/api/bet-explain/route.ts` | Gemini API route — Elite only |
| `src/app/api/live-odds/route.ts` | Live odds API route — Pro only |
| `src/app/api/stripe/webhook/route.ts` | Stripe webhook handler |

### Frontend Code Conventions

- Server components fetch data; client components handle interaction — `"use client"` only when you need `useState`, `useEffect`, or browser APIs
- Never expose `SUPABASE_SERVICE_ROLE_KEY` to the client
- Select dropdowns: use `<SelectValue>{explicit display text}</SelectValue>` not `placeholder` — Radix Select doesn't resolve item label text until the dropdown is opened, causing the raw value string to display on first render
