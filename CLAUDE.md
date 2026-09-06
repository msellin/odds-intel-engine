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
| `docs/VPS_NEXTJS_MIGRATION_RUNBOOK.md` | Vercel→VPS Next.js migration playbook — used for odds-intel-web 2026-07-07, reusable for future sites |
| `docs/ANALYSIS_GOTCHAS.md` | **Read before writing any analysis query.** Table/source vocabularies, capabilities that already exist (model A/B via `SHADOW_MODEL_VERSION`), dedup rules, outlier guards, and the CLV-vs-ROI variance numbers. Every entry is something that was guessed wrong or rediscovered the hard way. |

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

### Don't file what you can fix — the queue is not a diary

Added 2026-09-06 after the owner observed the backlog was growing faster than it
was shrinking. The measurement that day: **46 rows created, 36 closed, net +10**,
with 22 of the new rows still open at the end of it.

Most of that residue was avoidable. Three rules:

1. **Under ~30 minutes: fix it, don't file it.** A stale comment, a wrong doc
   line, a dead enum branch, a one-line grading error — filing these costs more
   total effort than fixing them and converts a 5-minute job into a permanent
   row someone has to read and re-triage forever.
2. **One project is one row.** Six Coolbet ingest tickets made the backlog read
   six times larger than it was and split one investigation's history across six
   places. If the work packages share a blocker, an owner and a week, they are
   sub-items of an epic, not peers.
3. **A decision is not a task.** "Do not build this", "negative result",
   "superseded by X", "merged into Y", "revisit at n=30" are all CLOSED with a
   reason and (where relevant) a trigger. They were sitting in the open count
   for weeks; closing nine of them took under an hour and lost nothing.

**None of this means stop looking.** The same day's audits found a live model
signal running at half strength, an inverted meta-model training label, and a
public CLV figure showing +9.5% where the honest number is -3.0% — none of which
were on any list that morning. Diagnostic work is the point. The rule is to
FINISH what it turns up, not to file it and move on.

**When triaging, close with evidence, never by assumption.** Every closure in
the 2026-09-06 pass was verified first — against the DB, the code, or the
scheduler registration — because several tickets' own bodies described a state
that was no longer true in either direction.

### Status values for PRIORITY_QUEUE.md

| Symbol | Meaning |
|--------|---------|
| ⬜ | Not started |
| 🔄 In Progress | Claimed — another agent must not start this |
| ✅ Done YYYY-MM-DD | Complete and documented |

### Project direction — tag every task with it

This project has **two directions**. They overlap but they are not the same, and
work that serves one can be worthless or even harmful to the other. Every task
must say which it serves and *how*.

| Tag | Direction | What "good" means |
|---|---|---|
| **🤖 OWN** | Automate the operator's own betting and make it as profitable as possible | Executable price, real placement, bankroll, blast-radius limits. Judged on money actually staked and CLV on placed bets. |
| **👥 PICKS** | Offer customers the best possible picks, backed by our own data and numbers | Honest published figures, coverage, clarity, defensible claims. Judged on whether a reader can act on it and whether the number survives scrutiny. |
| **🤖👥 BOTH** | Serves both | Say how it serves each — they usually differ. |

**Write the tag AND a short "how" on the task row.** "Both" on its own is not
useful; `🤖👥 BOTH — sharper placement gate for us, and the same floor makes the
public pick honest` is.

**Why this rule exists.** Several times work has been prioritised as if it
helped both when it only helped one:

- An odds floor on the placer is **🤖 OWN** — it changes what we stake. Publishing
  a break-even price to readers is **👥 PICKS** — it changes what they trust. The
  same number, two different jobs, and they can want different values.
- Collecting a market we cannot bet is **👥 PICKS** at best.
- A prettier `/performance` page does nothing for **🤖 OWN**.
- Fixing a stale price basis is **🤖👥 BOTH**, but for different reasons: we would
  stake on a phantom price, *and* we would publish an inflated track record.

**When the two directions conflict, say so explicitly in the task** rather than
silently optimising for one. Restricting picks to a narrow profitable band is
good for **🤖 OWN** and bad for **👥 PICKS** (fewer picks to show), and that
trade-off is the owner's call, not an implementation detail.

**Any task-list table presented to the owner must carry a Direction column**
alongside Priority, Estimate and Status.

## Keeping Docs Updated

Do not let docs drift from reality. If you notice something marked TODO that is already done, fix it. If you notice a doc describing behaviour that no longer matches the code, fix the doc in the same commit.

- Manual steps and launch checklist live in `ROADMAP.md` (Launch Checklist section)
- Retired docs (BACKLOG, PROGRESS, NEXT_STEPS, research_findings) have been deleted — history is in git

## Deployment — three paths, all automated

Everything below fires on push to `main`. **Do not hand-deploy**; if something
looks stale, check the workflow run rather than SSHing in and pulling.

| What changed | Path | Workflow | Effect |
|---|---|---|---|
| `workers/**`, `requirements.txt` | engine → VPS | `odds-intel-engine/.github/workflows/deploy.yml` | pull + `systemctl restart oddsintel-scheduler` |
| Anything else in the engine repo | engine → VPS | same | pull only, no restart |
| `supabase/migrations/**` | DB | `migrate.yml` | applies + records in `_schema_migrations` |
| `odds-intel-web/**` | web → VPS | `odds-intel-web/.github/workflows/deploy.yml` | pull + clean build + `pm2 restart` |

**Why this is written down (ENGINE-DEPLOY-2026-08-24):** the engine had no
deploy automation until 2026-08-24 while the web repo and migrations both did.
On that date the VPS was found **21 commits behind** — `BOT-NO-PIN-TIER0-GUARD`
and `BOT-NO-PIN-MODEL-SANITY`, both shipped the previous day specifically to
stop bad picks, had never run. Because the frontend half of the same day's work
*had* auto-deployed, the task looked shipped. Assume nothing about what is live
on the box; the drift check is the only thing that actually proves it.

`deploy_drift_check.yml` runs daily at 06:00 UTC and Telegram-alerts if either
repo is behind, on a non-main branch, has uncommitted tracked changes, or if
`oddsintel-scheduler` / pm2 `odds-intel-web` is not running.

Manual deploy (only when Actions itself is broken):
```bash
ssh root@204.168.199.8 'cd /opt/odds-intel-engine && git pull --ff-only \
  && systemctl restart oddsintel-scheduler && systemctl is-active oddsintel-scheduler'
```

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
                    VPS Postgres 17 (Hetzner 204.168.199.8) — public schema, 134 tables
                    (migrated from Supabase 2026-07-09 — SUPABASE-TO-VPS)
                    Supabase kept for Auth (auth.users, 52 users) + Storage (models bucket)
                                         |
                    PostgREST 12.2.3 (VPS docker, host-network :3012) → nginx
                       api.oddsintel.app (Cloudflare Flexible SSL)
                                         |
                       Next.js Frontend (odds-intel-web) → VPS pm2 :3000 → nginx
```

## Key Technical Details (Engine)

- Python 3.14, dependencies in `requirements.txt`
- **Postgres 17 on Hetzner VPS** for DB — migrations in `supabase/migrations/` (kept the folder name for history; applied by GitHub Actions to the VPS DB via the auto-apply workflow)
- Supabase Auth still authoritative for user identity (`auth.users`); Supabase Storage still hosts model bundles
- **Hetzner VPS** for pipeline automation (`workers/scheduler.py` as systemd unit **`oddsintel-scheduler.service`** — note the name, `odds-scheduler` does not exist; scheduler + FS Docker + Postgres + PostgREST + Next.js all colocated)
- Direct PostgreSQL (psycopg2) for engine writes; PostgREST for HTTP-based frontend reads and external callers
- GitHub Actions runs: engine deploy (`deploy.yml`), DB migrations (`migrate.yml`), daily drift check (`deploy_drift_check.yml`), smoke tests, plus manual `workflow_dispatch` triggers
- Credentials in `.env` (gitignored) — never commit secrets
- Prediction model: Poisson + XGBoost blend with 3-tier fallback (A/B/C)
- 16 paper trading bots running since 2026-04-27
- Nightly VPS backup at 03:30 UTC → Hetzner Storage Box (`/opt/oddsintel/backup-oddsintel.sh`, 14-day local + 90-day remote retention)

---

## Frontend (`../odds-intel-web/`)

The frontend lives at `../odds-intel-web/` (sibling directory). All rules for it live here — do not create duplicate docs in the frontend repo.

### Stack

- Next.js 15 (App Router), TypeScript, Tailwind CSS
- **Auth**: Supabase — `createSupabaseServer()` (server, cookie-backed) + `createSupabaseBrowser()` (client). These stay on `NEXT_PUBLIC_SUPABASE_URL` even post-migration.
- **Data**: VPS PostgREST at `https://api.oddsintel.app` — `createSupabasePublic()` (anon reads) + `createServerServiceClient()` (server-side service_role, bypasses RLS, requires explicit user_id filter for per-user queries). Env vars: `NEXT_PUBLIC_POSTGREST_URL`, `NEXT_PUBLIC_POSTGREST_ANON_KEY`, `POSTGREST_SERVICE_KEY`.
- **Per-user client-side reads** must go through a Next.js server route (browser can't authenticate to VPS PostgREST directly). Example: `/api/me/profile` in place of `supabase.from("profiles").eq("id", user.id)` on the browser client.
- Payments: Stripe (checkout, webhook at `/api/stripe/webhook`, portal)
- Error monitoring: Sentry
- Deployment: VPS pm2 (:3000) behind nginx. **Automatic on push to main** via `.github/workflows/deploy.yml` — see "Deployment" below. NEXT_PUBLIC_* are baked at build time, so any env change requires a fresh build.

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
