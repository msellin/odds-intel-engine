# OddsIntel Engine — Agent Instructions

## Project Documentation

All project documentation lives in this repo (`odds-intel-engine/`). Before starting any task, read the relevant files.

### Doc location convention

**Root `/`** — Agent protocol docs. Things agents must read or update as part of their task workflow: task tracking, system state, model logic, pipeline architecture, tier gating, data sources, infra costs. If a doc belongs in the "update when done" checklist, it lives here.

**`docs/`** — Strategy, playbooks, reference, and execution content. Things you look up rather than act on in every task: engagement strategy, launch plan, Reddit execution, API reference docs, archival backtests.

### Root docs (agent protocol)

| File | Purpose |
|------|---------|
| `PRIORITY_QUEUE.md` | **THE master task list — the only backlog in this repo.** Every unit of work that outlives a session has a row here, sub-tasks included. Update status here first. See "ONE master task list" below; machine-checked by smoke `SINGLE-MASTER-TASK-LIST`. |
| `docs/SYSTEM_MAP.md` | **The map — read first.** Single index for how picks, bots and every % work: the two edges (model vs sharp), every bot (anchor/floor/paper-or-real), the %-glossary, the gate stack. Built on `workers/registry/bot_registry.py`; machine-checked by smoke test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED`. |
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
| `docs/COOLBET_RUNBOOK.md` | **Coolbet troubleshooting** — transport chain (Mac→FS→Imperva→API), live endpoint map, and symptom→cause→fix for every Coolbet failure mode. Read first when Coolbet collection or placement breaks. |
| `docs/COOLBET_OWN_BETTING.md` | **Coolbet own-betting flow & architecture (single source of truth)** — what generates the picks we place with our own money, the full gate stack (maturity → per-market edge floor → 2.80 odds floor → live-edge → blast-radius), pre-match-only, paper-vs-real, and the placer-vs-shadow-bots-page distinction. **Read/update before touching any Coolbet placement gate or floor.** |
| `docs/MODELLING_DATA_AUDIT_2026_09_16.md` | **Read before proposing any model change.** What the feature table actually contains (nine predictors ≥88%, 48 columns under 50%), what raw material we hold, and what can be derived without new collection — ordered by coverage × value. Written after 1x2 AND O/U both measured residual α = 0 on one shared feature set. |
| `docs/BOOK_SET_COUNTERFACTUAL_2026_09_22.md` | **Read before changing `ACCESSIBLE_BOOKMAKERS` or arguing about which books PICKS should price off.** The counterfactual behind [[#005]]: opening the set is worth +2.1% on price, un-hides 5.7–7.9% of selections that currently have NO accessible price, and adds +52–76% O/U volume — while every realised-outcome comparison flips sign with the evaluation instant, so it must NOT be sold as a performance fix. Also rules the book set OUT as a cause of the accuracy collapse (#065 a). |
| `dev/active/per-market-feature-sets-design.md` | **Read before building or retraining ANY model head.** Which features belong to which market and why — 1x2 lives on the DIFFERENCE of scoring rates, totals on the SUM, BTTS on the low-score dependence — plus the size rule and the two decisions (market prices as features; coverage gating) that must be made before a head is trained. |
| `docs/RELIABILITY_LEDGER.md` | **Read before debugging a "mystery" outage.** The failure PATTERNS that keep recurring — blaming the loud thing, config edited but not deployed, a second code path inheriting no gates, unconfirmable placements, self-inflicted rate-limiting, caches that fail closed, tests pinning the old reality — each with its tell and the guard that now exists. |
| `docs/ANALYSIS_GOTCHAS.md` | **Read before writing any analysis query.** Table/source vocabularies, capabilities that already exist (model A/B via `SHADOW_MODEL_VERSION`), dedup rules, outlier guards, and the CLV-vs-ROI variance numbers. Every entry is something that was guessed wrong or rediscovered the hard way. |

## Task Lifecycle — Every Task Must Follow This Exactly

This protocol exists because parallel agents caused real production bugs when docs drifted. Follow it without exception.

### ONE master task list — `PRIORITY_QUEUE.md`, and nothing else (added 2026-09-18)

**`PRIORITY_QUEUE.md` is the only backlog.** If a unit of work outlives the session
that found it, it has a row there. No exceptions, no second list, no "I'll track this
in my plan doc".

**Why this rule exists.** The 2026-09-18 audit found open task markers in **71 files**.
Only 19% were in the master. Two *other* backlogs had grown — `docs/MASTER_TASK_LIST_2026_09_14.md`
and `docs/PRODUCT_FIX_PLAN_2026_09_14.md` — carrying real P0 work the master never
referenced, so every triage of the queue was blind to them. One of those items was a
**106,726-row CLV defect that sat unmoved for four days** because no master row existed
to surface it. Separately, 55% of the master's own open rows turned out to be already
done or stale, because closing a row was nobody's job once the work moved elsewhere.

**Sub-tasks are fine. Losing them is not.** The dev-doc rule above still stands —
a large task gets `dev/active/[task]-tasks.md` and you tick items as you go. Three
conditions make that safe:

1. **Name the parent.** The first line of any `*-tasks.md` says which `PRIORITY_QUEUE.md`
   row it belongs to. A checklist with no parent row is an orphan backlog in training.
2. **Promote what outlives the parent.** When the parent row closes, every unticked item
   either gets its own master row or is explicitly dropped with a reason *in the commit
   that closes it*. Never leave a live item behind in a doc nobody reads again.
3. **Archive on close.** Move the doc to `dev/archive/` when its parent row closes.
   `dev/active/` held **318 files** at the time of writing, most of them finished work.

**A checkbox is not automatically a task.** These are legitimate and must NOT be swept
into the queue:
- **Procedure checklists** — steps you tick *while performing an operation*
  (`docs/ROLLBACK_RUNBOOK.md` is the canonical example). They describe a procedure, not a backlog.
- **In-flight `dev/active/*-tasks.md` checklists** that satisfy the three conditions above.

Everything else — a root protocol doc, a `docs/` strategy doc, a plan, a findings write-up —
**describes reality and holds no open work.** If you catch yourself adding `- [ ]` to one,
that item belongs in `PRIORITY_QUEUE.md` with a Direction tag and an estimate.

**Machine-checked** by smoke `SINGLE-MASTER-TASK-LIST`, which fails when a new competing
backlog forms outside the master.


### Before writing any code

1. **Read `PRIORITY_QUEUE.md`** — check the task's current status. If it is already `🔄 In Progress`, stop and tell the user. Do not start parallel work on a task already claimed.
2. **Mark it `🔄 In Progress`** in `PRIORITY_QUEUE.md` — update the Status column immediately, before touching any code. This is the lock that prevents two agents stepping on each other.
3. **Read every doc relevant to the task** — at minimum: TIER_ACCESS_MATRIX.md if touching any tier/gating logic; SIGNALS.md if touching signals or match detail; WORKFLOWS.md if touching the pipeline; ROADMAP.md system state if touching what's built.

### While implementing

- If a task depends on something another task was supposed to build, **verify it was actually built** before assuming it exists. Read the code — do not trust doc status alone.
- If you discover a related bug or gap, **log it in PRIORITY_QUEUE.md** before moving on. Never silently fix something unrelated without tracking it.

### When done — before committing

**Always add a smoke test** in `scripts/smoke_test.py` (source inspection is fine). Run ONLY yours locally: `python3 scripts/smoke_test.py -f MY-NEW-TEST` (piping the full suite to grep saves nothing). The full suite is CI's job.

**Docs, in the same commit as the code** — three obligations, not a nine-row checklist:
1. The `PRIORITY_QUEUE.md` row → ✅ Done YYYY-MM-DD.
2. **The doc that OWNS what you changed** (pipeline job → `WORKFLOWS.md`, tier → `TIER_ACCESS_MATRIX.md`, source → `DATA_SOURCES.md`, infra/cost → `INFRASTRUCTURE.md`, built surface → `ROADMAP.md`, signal → `SIGNALS.md`). Always-mandatory where they apply: bot / edge / floor / model version → `docs/SYSTEM_MAP.md` **and** `workers/registry/bot_registry.py` together (CI drift test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED`); model logic → `MODEL_WHITEPAPER.md`.
3. The ripple grep (see "Keeping Docs Updated").

### Speed rules — shared checkout, never wait (owner, 2026-09-25)

- **Never wait for CI, deploys, migrations or scheduled runs.** Push once, move on. Hand back your *verification steps* (the query/command + the expected result + when it becomes true, e.g. "after migration 441 applies") for the coordinating session to run in bulk. Never `gh run watch`. **Write them to `ops/verify/<task>.yml`** — the scheduler's `verify_queue` job runs them when due and Telegram-alerts only on mismatch/expiry (WORKFLOWS.md, VERIFY-QUEUE).
- **A red CI run is only yours if it is NEW.** The job summary and the end of the log split failures into **NEW (this push)** vs **INHERITED** (`SMOKE-NEW-VS-INHERITED`; also in the `smoke-failures` artifact). Fix NEW; leave inherited to their owner. The job stays red for either.
- **One commit + one push per task per repo**, not one per step.
- **The checkout and index are shared with other live sessions.** Never `git stash`, `git reset`, or `git checkout` files you did not edit. Stage only your own hunks; `git pull --rebase --autostash` right before pushing.
- **Never read `PRIORITY_QUEUE.md` (2.4 MB) or `scripts/smoke_test.py` (58k lines) whole** — `grep -n`, then read line ranges.

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

### Research before you train — no model work starts without it (added 2026-09-23)

**Before building or retraining any model head, the literature question must be
answered first and written down.** Not after a null result, not "while it
trains" — before.

**Why this is a rule and not a preference.** Six months of model work produced
**five consecutive α = 0.0000 results** against de-vigged Pinnacle. Two
afternoons of literature review then established, with primary sources, things
that would have changed what was built:

* **1x2 and totals are informationally orthogonal by construction.** Karlis &
  Ntzoufras model the goal DIFFERENCE via the Skellam distribution, in which the
  sum is *integrated out and discarded* — so a difference-shaped feature set is
  provably silent about totals. Our vector carried an explicit `elo_diff` and
  **no sum term at all**, feeding five heads.
* **Past goals are the WORST input to a goals model.** Wheatcroft, 54,437
  matches, Bonferroni-corrected p<0.0001: ratings fed shots+corners returned
  +535 units where the same ratings fed goals returned −631, negative in 10 of
  10 leagues. Our `goals_for_avg_*` features are exactly that input.
* **More features is not better.** A 40-feature engineered set scored 0.2416 RPS
  against **0.2085 for a two-number rating**, same model class, ~300k training
  matches. A single-covariate Elo beat every entry in an earlier challenge.
* **Time decay differs by market** — ~300-day half-life for O/U 2.5 against
  30-90 for match outcome. We share one across every head. *(Corrected
  2026-09-24: the 300 days is Wheatcroft & Sienkiewicz's decay for a shot-CONVERSION
  model WITH odds as a regressor — without odds it was 90 — not a memory for
  shot-volume ratings.)*
* **Draws are not worth targeting** — 1.4% model skill against 3.0% for the
  bookmaker, c-statistic 0.62 for both.
* **α = 0 against a sharp closing line is the NORMAL published result.** Nothing
  in a 51-league benchmark beat the bookmaker consensus. Our zeros are not
  evidence of a bug.

None of that required new data, and all of it was available before any of the
five measurements were taken.

**What "answered first" means, concretely:**

1. The market's **structural shape** is stated — difference, sum, marginal, or
   dependence — with a citation, before features are chosen.
2. The **feature set is sized deliberately**, and the default direction is DOWN.
   `dev/active/per-market-feature-sets-design.md` carries the size rule.
3. **Known negative results are checked** so effort is not spent re-deriving
   them, and the expected outcome is stated before the run.
4. If the literature has **nothing** on the market (corners and cards, as of
   2026-09-23), that is recorded as the answer and the work proceeds knowing it
   is unprecedented rather than assuming it is well-trodden.

⚠️ **And a multi-market sweep carries a family-wise correction from the start.**
Testing six markets and reporting the best is the multiple-comparison trap that
`ANALYSIS_GOTCHAS §47` and [[#073]]'s permutation design exist to prevent.

## Keeping Docs Updated

Do not let docs drift from reality. If you notice something marked TODO that is already done, fix it. If you notice a doc describing behaviour that no longer matches the code, fix the doc in the same commit.

- Manual steps and launch checklist live in `ROADMAP.md` (Launch Checklist section)
- Retired docs (BACKLOG, PROGRESS, NEXT_STEPS, research_findings) have been deleted — history is in git

### Explain what/why/where, and ripple-check the docs (added 2026-09-10)

The clearer the picture of **what** each part does, **why** it exists, and **where** it
runs, the faster and safer this product grows. Two obligations follow, and they are not
optional:

1. **Document what you do and why — in the same commit as the change.** Not just *that*
   you changed something, but the reasoning: what was wrong, what the correct model is,
   why this fix over alternatives. A future reader (including you) must be able to
   reconstruct the decision without re-deriving it. Prefer a short "why" sentence in the
   code/doc over a clever silent change. The single sources of truth are load-bearing —
   when you touch generation, a surface, a placement path, a gate, a job, or a bot,
   update the doc that owns it (see the "When done" checklist table) in that commit.

2. **Ripple-check: a change that makes one doc right often makes others wrong.** Before
   you finish, actively hunt for the OTHER docs your change just made stale, and fix or
   flag them in the same commit. This is a required step, not a nicety — do it explicitly:

   ```bash
   # after renaming/retiring/moving anything, grep the whole docs surface for it:
   grep -rln "<the thing you changed>" docs/ *.md
   ```

   Then, for every hit: correct it, or add a dated "RETIRED/CHANGED YYYY-MM-DD" banner if a
   full rewrite is out of scope — never leave a doc asserting the old reality with no
   marker. A doc that confidently describes a component you just deleted is worse than no
   doc: it actively misleads. **Real example (2026-09-10): retiring the Coolbet paper
   daemon left EIGHT docs describing it as live; the grep above found them, and each was
   fixed or banner-flagged in the same pass.** If you cannot fix them all now, list the
   remaining stale docs in `PRIORITY_QUEUE.md` before moving on — a known-stale doc is a
   tracked task, never a silent lie.

## Deployment — three paths, all automated

Everything below fires on push to `main`. **Do not hand-deploy**; if something
looks stale, check the workflow run rather than SSHing in and pulling.

| What changed | Path | Workflow | Effect |
|---|---|---|---|
| `workers/**`, `requirements.txt`, or a `scripts/` module the scheduler imports (e.g. `publish_picks_forward_test.py`; derived from `from scripts.X` imports, added 2026-09-23) | engine → VPS | `odds-intel-engine/.github/workflows/deploy.yml` | pull + `systemctl restart oddsintel-scheduler` |
| Anything else in the engine repo | engine → VPS | same | pull only, no restart |
| `supabase/migrations/**` | DB | `migrate.yml` | applies + records in `_schema_migrations` |
| `odds-intel-web/**` | web → VPS | `odds-intel-web/.github/workflows/deploy.yml` | pull + clean build + `pm2 restart` |

Assume nothing about what is live on the box (ENGINE-DEPLOY-2026-08-24: the VPS was once 21 commits behind while the task looked shipped); the drift check is the proof.

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
- **A duplicate NNN is legal — do not rename to dodge one.** `migrate.yml` keys `_schema_migrations` on the full FILENAME (primary key) and applies in glob order; 343, 354, 355, 356 and 361 already exist twice. On a collision keep your number with a distinct suffix. Renaming a *pushed* migration makes it a new, never-applied file (and orphans the old row). If two same-number files depend on each other, say so in a `-- depends on:` comment.
- Applied automatically via GitHub Actions (`migrate.yml`) on any push to main that touches `supabase/migrations/`
- Can also be triggered manually via Actions → "OddsIntel — Run DB Migrations" → Run workflow

## Architecture

```
API-Football Mega (150K/day)  -> PRIMARY: fixtures, odds (9 bookmakers incl. Pinnacle since 2026-09-13; was 13), live data,
                                lineups, injuries, standings, H2H, events, player stats
Kambi API (free)             -> ⛔ RETIRED 2026-09-15, and ⚠️ NOT PLACEABLE before that
                                (UNIBET-KAMBI-RETIRED). 41-league sweep
                                removed 2026-05-06; job_unibet_kambi_odds wrote
                                bookmaker='Unibet-Kambi' until the cron was unregistered.
                                unibet.ee LEFT the Kambi API (2026-09-06, KAMBI-FEED-DIVERGENCE):
                                38% of our stored Kambi prices read HIGHER than the site (median
                                +3.3%, max +23.5%), so staking on them means staking on a price
                                that does not exist. It was excluded from ACCESSIBLE_BOOKMAKERS
                                then, and kept writing ~815k rows over nine more days with NO
                                consumer -- every reference to it in the codebase is an
                                EXCLUSION. The PLACEABLE Unibet feed is 'Unibet-Site'
                                (workers/automation/unibet_odds_feed.py), which since
                                UNIBET-SITE-MARKET-WIDENING-2026-09-15 also carries BTTS, double
                                chance, DNB, corners, 1H corners, 1H goals and cards -- the
                                breadth that was Kambi's last remaining justification. Module and
                                job kept for manual runs; historical rows kept.
ESPN (free)                  -> Settlement results backup
                                         |
     ── MORNING CHAIN — ONE sequential job at 04:00 UTC (morning_pipeline) ──
                    ① Fixtures    — AF fixtures (today + tomorrow rows; league refresh Mon)
                    ② Enrichment  — standings, H2H, team stats, injuries
                    ③ Odds        — AF bulk odds (9 bookmakers incl. Pinnacle since 2026-09-13; was 13) for today
                    ④ Predictions — club model + AF predictions + national-team predictor
                    ⑤ Betting     — Poisson/XGBoost model + signals + trigger engine (morning cohort)
                                         |
     ── STANDALONE SCHEDULED JOBS ──
                    Odds refresh   (24/7, every :00/:30 — NOT windowed) — AF bulk odds
                    ⑥ LivePoller    (24/7 background thread, 45s live/120s idle) — live scores/odds/stats for settlement; in-play BETTING retired 2026-08-21 (/odds/live gated off; InplayBot code deleted 2026-09-26, #162 W7.2)
                    ⑦ News Checker  (09:00/12:30/14:30/16:30/18:30 UTC) — Gemini AI analysis
                    ⑧ Settlement    (21:00/23:30/01:00 UTC) — settle bets, post-match stats, ELO, CLV
                    ⑨ Betting Refresh (hourly at :05/:35, 24/7) — re-evaluation with fresh odds
                       (schedules are authoritative in workers/scheduler.py)
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
- Nightly VPS backup at 03:30 UTC → Hetzner Storage Box (`/opt/oddsintel/backup-oddsintel.sh`, **3-day local + 90-day remote** retention). CrossRank shares this box, this Postgres and this Storage Box account — both backup scripts live in `deploy/vps/` and must not prune each other's dumps. **The Storage Box shell is restricted: only `ls`, `rm`, `mkdir` — no `find`.** See `deploy/vps/README.md`.

---

## Frontend (`../odds-intel-web/`)

The frontend lives at `../odds-intel-web/` (sibling directory). All rules for it live here — do not create duplicate docs in the frontend repo.

### Stack

- Next.js 15 (App Router), TypeScript, Tailwind CSS
- **Auth**: Supabase — `createSupabaseServer()` (server, cookie-backed) + `createSupabaseBrowser()` (client). These stay on `NEXT_PUBLIC_SUPABASE_URL` even post-migration.
- **Data**: VPS PostgREST at `https://api.oddsintel.app` — `createSupabasePublic()` (anon reads) + `createServerServiceClient()` (server-side service_role, bypasses RLS, requires explicit user_id filter for per-user queries). Env vars: `NEXT_PUBLIC_POSTGREST_URL`, `NEXT_PUBLIC_POSTGREST_ANON_KEY`, `POSTGREST_SERVICE_KEY`.
- **The anon role reads ONLY what the public site reads** (migration 404, #072, 2026-09-24): 5 base tables + the `*_public` / summary views + `rpc/get_coverage_counts`. A new table is private by default. A new public read = a `*_public` view + an explicit `GRANT SELECT … TO anon` in the same migration + the name added to smoke `ANON-LEAST-PRIVILEGE`. Otherwise use the service client server-side.
- **Per-user client-side reads** must go through a Next.js server route (browser can't authenticate to VPS PostgREST directly). Example: `/api/me/profile` in place of `supabase.from("profiles").eq("id", user.id)` on the browser client.
- Payments: Stripe (checkout, webhook at `/api/stripe/webhook`, portal)
- Error monitoring: Sentry
- Deployment: VPS pm2 (:3000) behind nginx. **Automatic on push to main** via `.github/workflows/deploy.yml` — see "Deployment" below. NEXT_PUBLIC_* are baked at build time, so any env change requires a fresh build.

### Tier Gating Rules

> **Corrected 2026-09-21.** This section used to describe gating for match detail and
> `/value-bets`, neither of which exists. **There is no paid product right now** — `ROADMAP.md`
> records the Free/Pro/Elite matrix as deprecated, and there is **no checkout route** in the web
> app (only a vestigial Stripe webhook). The tier code below is still live and still correct for
> the surfaces that remain, but the meaningful gate today is **`is_superadmin`**, which separates
> the public pages from `/admin/**`.

Server-side gating is the only safe gating. Client-side gating hides UI but does not protect data.

- Tier is read from `profiles.tier` (values: `free`, `pro`, `elite`) + `profiles.is_superadmin`
- `isElite = is_superadmin || tier === 'elite'`
- `isPro = isElite || tier === 'pro'` ← Elite users are always also Pro
- Pro data (odds movement, events, lineups, stats, injuries) must only be **fetched** server-side when `isPro === true` — never fetch then conditionally hide client-side
- Pass `isPro` and `isElite` as props down to any component that changes its rendering by tier — do not assume a component receives them without checking

### Key Frontend Files

> **Corrected 2026-09-21 (`CLAUDE-MD-MAPS-A-DELETED-PRODUCT`).** Every one of the 11 paths
> previously listed here had been deleted by `PRODUCT-COLLAPSE` (`f6d3648`, **2026-06-24** — 174
> files, 39,872 lines), which removed `/matches`, `/value-bets` and the entire signals UX. The
> tables below are the surface that actually exists. **The public product is five pages.**

**Public pages** — this is the whole customer-facing surface:

| File | Purpose |
|------|---------|
| `src/app/page.tsx` | Landing — head-to-head vs other public models, reads `ledger/comparison_*.json` |
| `src/app/picks/page.tsx` | `/picks` — the pre-registered sharp-edge forward test (the product) |
| `src/app/(app)/performance/page.tsx` | Settled track record + per-bot leaderboard |
| `src/app/(app)/track-record/page.tsx` | Public ledger |
| `src/app/methodology/page.tsx` | How the numbers are computed — the auditability pitch |

**Data layer:**

| File | Purpose |
|------|---------|
| `src/lib/engine-data.ts` | All PostgREST queries — the data fetching layer (large; start here) |
| `src/lib/upcoming-picks.ts` | The `/picks` feed, incl. per-market edge floors and break-even odds |
| `src/lib/forward-test-picks.ts` | The pre-registered forward test's own reads |
| `src/lib/coolbet-edge.ts` | `COOLBET_AUTO_MIN_EDGE_BY_MARKET` — the web-side per-market floors |
| `src/lib/bot-aggregates.ts` | Per-bot rollups behind the leaderboard |
| `src/lib/shadow-bots/queries.ts` | Admin shadow-bots page data (see `verdict.ts` for the labels) |
| `src/lib/get-user-tier.ts` | Reads `profiles.tier` + `is_superadmin` |

**Admin (superadmin only)** — one shared shell (`admin/layout.tsx`): sidebar + top bar (breadcrumb, ⌘K search that only navigates, attention bell = the Overview's items cached 60 s — `src/lib/admin-shell-data.ts`). Shared components `src/components/oi/` (Panel, StatCard, StatusBadge, ChartCard/DonutCard, **DataTable** on @tanstack/react-table). Pages (#139, 2026-09-24): `/admin` **Overview** (plain-language answers `src/components/oi/answer-strip.tsx` — every page opens with them since 2026-09-25, card explanations sit behind an ⓘ `info-tip.tsx`; attention inbox `src/lib/admin-attention.ts`, charts `src/lib/admin-overview.ts`) · `/admin/bots` **Bots** (registry, one scoring, real-money ladder + controls, bot sheet with charts and the full pick ledger — `/admin/shadow-bots/[bot]` now redirects here) · `/admin/shadow-bots` **Pick queue** (today's picks + Place €X only) · `/admin/real-bets` **Real bets** (money ledger, caps, unconfirmed manual bets, promotions; `src/lib/admin-money.ts`) · `/admin/feeds` **Feeds** (#107; per-feed controls, "Coolbet collection" On/Off switch (= the footprint pause, stops odds collection only), budgets, coverage, DQ) · `/admin/ops` **Jobs** (view `pipeline_job_latest`, mig 417; settlement) · `/admin/activity` **Activity** (`control_changes` ∪ `feed_actions`). URLs kept on purpose (65 smoke pins); labels follow the IA sitemap. **Deleted 2026-09-24:** `/admin/place`, CS2 / LoL / Tennis. Local design preview for every admin page: `BOT_BOARD_FIXTURE` dev server + `scripts/dump_bot_board_fixture.py` / `scripts/dump_admin_fixture.py`. This is where the
operator-facing surfaces live; most day-to-day work touches these, not the public pages.

| File | Purpose |
|------|---------|
| `src/app/(app)/admin/page.tsx` | Overview dashboard (attention inbox + charts) |
| `src/app/(app)/admin/bots/bots-board.tsx` | Bots — registry, scoring, real-money controls |
| `src/app/(app)/admin/shadow-bots/page.tsx` | Pick queue — today's picks + manual Place |
| `src/app/(app)/admin/real-bets/page.tsx` | Real bets — the money ledger |
| `src/app/(app)/admin/ops/page.tsx` | Jobs — scheduled job health, settlement |
| `src/app/api/admin/bots/controls/route.ts` | Every admin control write (per-bot real-money eligibility, /picks, pauses) → the audited `admin_set_control`. The legacy `coolbet-placer-bots` / `coolbet-daemons-pause` routes were deleted 2026-09-24 (#139 IA P3) |
| `src/app/api/v1/track-record/route.ts` | Public track-record API |
| `src/app/api/v1/upcoming/route.ts` | Public upcoming-picks API |
| `src/app/api/stripe/webhook/route.ts` | Stripe webhook — **vestigial, there is no checkout route** |

### Frontend Code Conventions

- Server components fetch data; client components handle interaction — `"use client"` only when you need `useState`, `useEffect`, or browser APIs
- Never expose `SUPABASE_SERVICE_ROLE_KEY` to the client
- Select dropdowns: use `<SelectValue>{explicit display text}</SelectValue>` not `placeholder` — Radix Select doesn't resolve item label text until the dropdown is opened, causing the raw value string to display on first render
