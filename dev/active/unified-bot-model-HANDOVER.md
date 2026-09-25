Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# HANDOVER — #139 unified bot model + admin dashboard (written 2026-09-24, end of session)

> **UPDATE 2026-09-25 (second #139 session) — the admin dashboard track is DONE and live.**
> - Shipped (web 219dc0c → 786e402 + the round-3 commit; engine 335635fe → latest): IA moves P1–P9 (owner decisions:
>   the Coolbet footprint pause stops SWEEPING only, never real bets; LoL/Tennis/CS2/Place deleted; no Telegram
>   footprint command); design system `src/components/oi/` (Panel, StatCard, StatusBadge, ChartCard/DonutCard,
>   DataTable); top bar (breadcrumb, ⌘K that only navigates, attention bell); Overview (KPI cards, Urgent / To-check
>   inbox with exact-row links, charts); every page redesigned (Bots, Pick queue = /admin/shadow-bots, Real bets,
>   Feeds, Jobs = /admin/ops, new /admin/activity). URLs kept on purpose (65 smoke pins).
> - UX: 3 testers scored 7 / 6 / 6.5; all findings fixed; re-test 7.5; its money findings fixed in round 3
>   (30-day card "couldn't load" not €0; CAN STAKE = NO when a readable layer blocks — money-reviewed; Overview feed
>   colours grey when the status check is stale; "1x2" finds "1×2").
> - Also fixed along the way: /admin/bots slowness (router.replace re-rendered the page on the server on every
>   click → history.replaceState); 'no picks for 48h' hourly false alarm (health_alerts reads all ledgers);
>   migration 417 `pipeline_job_latest` (found 5 jobs failing 17–33 days → spun off as its own task); shadow
>   clv_pinnacle_live NULL since ~09-03 (settlement select + migration 422, 20,090 rows); footprint hour-booking;
>   CI flake (`_this_thread_only` for DB monkeypatches).
> - #140 DONE (no slice survives) → #150 (sharp-trigger CLV grader) + #151 (Coolbet refusals under budget) filed.
> - Phase 5 drafts for the owner: `dev/active/unified-bot-model-phase5-decisions.md` (six decisions, plain words,
>   recommendations) and `…-phase5-schema-draft.md` (invariant map, 5b–5e path, ~15–17.5 d). NEXT: owner answers the
>   six decisions → two independent schema reviews → 5b.
> - Known small leftovers (UX re-test, minor): bot-registry prose "INSTRUMENT (paper, never placeable)" should read
>   "not meant for real money" (technically has a placement path; € switch off) — edit via bot_registry.py +
>   SYSTEM_MAP together; developer text in bot descriptions behind a "technical detail" toggle; Pick queue
>   "OLD BOT PRICES" card wording; phone card layout for Pick queue / Bots tables; Feeds shows two Coolbet
>   request counts for "this hour"; deep links opened directly don't scroll; Kill-switch Resume dialog's
>   "Recorded reason" empty.
> - Owner actions still outstanding: OWNER_USER_IDS (arming blocked until set), SHADOW_MODEL_VERSION pin,
>   ROUTER_ALLOW_REAL=false — commands in §1 below.


Read this first, then `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` and `dev/active/unified-bot-model-tasks.md`.

## 0. State at handover — everything is committed and pushed (verified 2026-09-24, end of session)

All three agents that were running have finished, and their output is committed:
- the visual direction (§5, 387402ff);
- the IA audit (§6, c9e2507a);
- the shared admin shell (reviewed PASS; web 57086ec, engine 1c0cbdf0).

Both checkouts were clean for this work at handover, and **nothing is pending**.

**Priority note:** the owner set **#141 (1x2 model rebuild) as TOP PRIORITY** in another session (fd512f54).
Check PRIORITY_QUEUE.md for the current order before picking up the #139 dashboard work below.

**Other sessions share both checkouts.** Never stage their files: the #141 work touches
`dev/active/1x2-model-rebuild-plan.md`, `workers/model/ratings_1x2.py` and `scripts/ab_1x2_rating_arms.py`;
the #142 work touches `workers/utils/footprint.py`. Also check `scripts/smoke_test.py` hunk by hunk.
One stray #142 hunk slipped into 1c0cbdf0; it only removed 4 duplicated lines in test_book_footprint, and the test passes.

**Owner's open answers** (collect them before the related work):
1. Keep the real-money controls on /admin/bots? (The earlier decision was yes.)
2. LoL and Tennis pages: delete, or keep in a collapsed "Archive" group?
3. A Telegram command for the Coolbet footprint pause?
4. The three owner commands in §1 (SHADOW_MODEL_VERSION pin, ROUTER_ALLOW_REAL, OWNER_USER_IDS). Check whether they have been run.

## 1. Where things stand

### Shipped and live (all reviewed by 1–3 agents)
- **Research:** audits #137 (`docs/BOTS_AUDIT_2026_09_24.md`) and #138 (`docs/SHADOW_BOTS_AND_CONTROLS_AUDIT_2026_09_24.md`); genesis research `dev/active/unified-bot-model-genesis/` (ledgers, bots-and-controls, predictions, about 70 invariants); 5a inventory `docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md`.
- **Migration 410:** `bot_ledger` (ONE LEDGER PER BOT), `bot_scoreboard` (pre-registered bots scored on their CURRENT rule_version only; |clv|≤1 guard), `bot_capabilities`, `bot_config` table. `scripts/export_bot_config.py` runs as a daily 03:40 job.
- **Migration 411:** `bot_weekly`, `bot_market_stats` (same-market junk-control comparison), `bot_ledger_display`.
- **Migration 413** — the control panel for OWN real money (spec `dev/active/bots-control-panel-spec.md` §16 = owner decisions):
  - fleet pause and resume (typed confirmation);
  - owner-only arming (`ARM REAL MONEY` + reason, no expiry);
  - per-bot real-money eligibility in the DB. It replaced the code whitelist `PLACEABLE_BOTS`: 11 capable bots, all OFF, and `bot_coolbet_ou_model_v1` is locked;
  - "Show on /picks";
  - an append-only `control_changes` audit table;
  - `placer_heartbeats`;
  - Telegram is stop-only.

  DB guards refuse starts outside the audited functions on UPDATE, INSERT, DELETE and TRUNCATE, and a re-pause keeps the standing reason. It passed three money-safety review rounds. Live state at handover: paused, not armed, 0 of 11 bots ON.
- **/admin/bots** is redesigned: KPI strip, verdict chips, CLV forest bars against the same-market junk control, admin shell, controls, sheet with tabs, activity log. A dev preview runs at `localhost:3055` via `.claude/launch.json` "bots-preview" (`BOT_BOARD_FIXTURE`; regenerate the data with `python3 scripts/dump_bot_board_fixture.py`).
- **Bug fixes found along the way:**
  - Coolbet 1H goal lines were wiping the full-match over/under prices (b4802c71).
  - Sim CLV had been NULL since 09-14 (2cf14433).
  - `clv_pinnacle_devig` had not been written since 09-05 (04fff33f).
  - The Epicbet/Tonybet trigger cohorts failed the DB CHECK (migration 409).
  - The per-pick gate now sits at the only real-money Coolbet POST, `_place_bet_api` (b9d2d05c, f882f35d).
  - `/pausepicks` now stops sending only; recording continues (67fd3caa, d20b0409).
  - Shadow auto-select now picks weekly `vYYYYMMDD` bundles only.

### Owner actions still outstanding (the permission system blocked me from editing `.env` files)
```bash
ssh root@204.168.199.8 "cd /opt/odds-intel-engine && sed -i 's/^SHADOW_MODEL_VERSION=v20260705$/# SHADOW_MODEL_VERSION=v20260705  # removed 2026-09-24 #139/' .env && systemctl restart oddsintel-scheduler"
sed -i '' 's/^ROUTER_ALLOW_REAL=true$/ROUTER_ALLOW_REAL=false/' ~/www/odds-intel-engine/.env
ssh root@204.168.199.8 "cd /opt/odds-intel-web && echo 'OWNER_USER_IDS=c0b8031b-cb8a-4316-9969-81c8c7cfa794' >> .env.production.local && pm2 restart odds-intel-web"
```
Arming fails closed until `OWNER_USER_IDS` is set.

## 2. What the owner asked for last (the admin dashboard) — NEXT WORK

Owner, 2026-09-24:
- "the left navigation menu … all of the pages should have the left menu, a single component, only render the middle block"
- "make it look like a real admin dashboard" — his references were TailAdmin, Purple Admin, Staradmin and SB Admin
- "interactive admin dashboards"
- "it needs to align with our site"

**Decisions taken:**
1. **Shared admin shell.** One `admin/layout.tsx` with one sidebar component; only the middle changes.
2. **Build it ourselves.** No downloaded template. Base it on shadcn's official dashboard and sidebar blocks (the repo is already shadcn-style on `@base-ui/react`), restyled to the references. Add recharts (shadcn charts) and TanStack Table only if they are not already present.
3. **Use the site's DARK palette and tokens** so admin and public look like one product. Take the STRUCTURE from the references: grouped icon sidebar, top bar (search, notifications fed by attention items, theme toggle, account), trend KPI cards with sparklines, interactive charts (hover, legend toggles, 7d/30d/90d), and DataTables (search, sort, paging, row actions).
4. **Move the Coolbet footprint toggle** from /admin/bots to /admin/feeds. It is a feed control; it ended up on the bots page only because it lives in the same DB row.
5. **Run an information-architecture audit** that decides what belongs on which page (the owner: "carefully audit what needs to be on what page"). The /admin index becomes an "attention" inbox.
6. **Rebuilding the public site is NOT in scope.** It is filed as **#143**, pending the owner's go-ahead.

**Build order:**
- (a) Review and commit the shared-layout refactor (§0).
- (b) Read both specs and reconcile the sidebar groups (the IA audit wins).
- (c) Builder agent(s) apply the design system to the shell and every admin page, one page per step. Each step gets 1 UI reviewer with screenshots at 1440/1024/375, plus a data reviewer where numbers change.
- (d) Deploy and give the owner the link. He wants to review and will point things out.

## 3. After the dashboard (the #139 remainder)
- **#140 BOT-SLICE-ANALYSIS**, pre-registered with a holdout, Holm correction, judged on mc-CLV. Run it on the 11 capable bots FIRST: no bot has positive evidence yet, and newly enabled bots would use the placer's default 3% floor. Run #140 before the owner switches anything on.
- **`bot_unified_gate_1x2_paper_v1` calibrator bug** (raw 0 → ~0.156, which is why it takes longshots): fix it, don't retire the bot.
- **Phase B:** retire/un-retire, labels and display names from the page. The shadow-bots page becomes "today's picks" (Phase 2).
- **Phase 5:** the single `picks` table migration, about 2½–3 weeks, and six owner decisions come first (see the 5a inventory). Rewrite the schema from the genesis invariants and get two reviewers before creating any table.
- **New bots from #141** (`bot_rating_1x2_v1`, `bot_combined_1x2_v1`) already show up automatically as model_sim; they are not real-money capable because no placer reads simulated_bets.

## 4. Working rules that held this session
- **Every step is verified by 1–2 independent review agents** (owner rule; two for anything touching money or migrations). Send their findings back to the builder, then re-review.
- **Commit only your own hunks.** Use a temp index: `export GIT_INDEX_FILE=<scratch>/idx; git read-tree HEAD; git add <own files>; commit; unset; git reset -q; push`. For `scripts/smoke_test.py`, build the staged file from HEAD plus your own test blocks.
- **Run smoke tests with `--filter` only.** Test migrations inside a ROLLED-BACK transaction before pushing. Never edit `.env` files; give the owner the command instead.
- **The owner is not a deep football or stats person.** Report outcomes in plain words and tables.

## 5. Landed after the handover was first written

### Admin visual direction — DONE (`dev/active/admin-shell-visual-direction.md`, refs in `dev/active/admin-refs/`)
- **Look:** dark by default, using the public site's own `globals.css` tokens and no new colours, just aliases (success, danger, info) plus the three pick-method colours from /picks. The STRUCTURE comes from shadcn sidebar-07 + dashboard-01, TailAdmin, Tremor and Materio. Style is borders, not shadows.
- **Shared components go in `src/components/oi/`,** named so the public pages can reuse them later (#143): Panel, SectionLabel, StatCard, TrendPill, StatusBadge, Segmented, ChartCard, DataTable, PageHeader.
- **Libraries:**
  - recharts 3.8.1 is ALREADY installed, plus the copied-in shadcn chart wrapper.
  - The only new npm dependency is `@tanstack/react-table`.
  - The ⌘K palette is built from our Dialog and Input, with no `cmdk`.
  - Sidebar: swap the new shared shell's internals for shadcn's Sidebar. Add the copied-in tooltip, skeleton and `use-mobile` files; no new package is needed.
- **First charts:** picks per week by family and cumulative flat-stake P/L per bot are ready now. Per-bot CLV over time with the junk-control line needs weekly control data. Feed coverage over time needs a new daily history view, because only today and yesterday are stored.
- **Light mode:** optional and last. About 655 hard-coded colour classes would fail contrast on white.
- The doc ends with a **7-step build order** and a "what changes" row per admin page. Its sidebar groups are a DRAFT; the IA audit decides them.
- The `bots-preview` dev server (:3055) was left running by that agent. Restart it via `.claude/launch.json` if needed.

## 6. Admin information-architecture audit — DONE (`dev/active/admin-information-architecture.md`)
**Important finding:** the Coolbet footprint pause is not only a feeds switch. When it is set, the Mac daemon also skips
PLACING, and `coolbet_control.can_stake()` counts it as a blocker. But the 6-layer money ladder on /admin/bots leaves it out,
so the page could say "CAN STAKE: YES" while the engine says no. Fix this before anything else.

Top 10 moves, in the audit's order:
1. 🤖 Add the footprint pause to the money ladder as a read-only blocking layer, and fix its "Not a money switch" label.
2. 🤖 Move the footprint switch to the Coolbet block on /admin/feeds, next to the Mac daemon heartbeat. Add a 4th sidebar status line.
   After that, the Controls card on /admin/bots holds only the picks channel.
3. 🤖 Remove the two unaudited write paths.
   - /admin/shadow-bots flips the footprint pause through `/api/admin/coolbet-daemons-pause`, a plain UPDATE with no audit row.
   - Its per-bot real-money toggle uses the legacy route, which is now OFF-only.
   - Route both through the audited function.
4. 🤖👥 Turn /admin into an ATTENTION INBOX built from existing data: placer armed or stale, picks channel quiet or paused, red feeds,
   bot issues, failed jobs, stuck settlement, data-quality findings, unreconciled manual bets.
5. 🤖 Promote /admin/real-bets to a "Money" page in the sidebar. It holds 992 rows; the last is 2026-09-15.
6. 🤖 Cut /admin/shadow-bots down to a "Pick queue" (today's picks + Place). Drop its safety strip and scoreboard, which duplicate /admin/bots.
7. 🤖👥 Keep ONE per-bot scoring. It is computed three ways today; fold /admin/shadow-bots/[bot] into the /admin/bots sheet.
8. Delete the dead pages: /admin/cs2 reads tables that no longer exist, and /admin/place served a trial that ended in June. LoL/Tennis are the owner's call.
9. Turn Ops into a "Jobs" page. Odds coverage, live tracker and API-Football budget move to Feeds; drop the paid-tier leftovers and the stale bots section.
10. Gaps that need engine work: proof the picks channel is sending, one activity log across both audit tables, Coolbet session
    health on Feeds, deploy drift, and which model version is live.

**Open questions for the owner:** (a) keep the real-money controls on /admin/bots? The earlier decision was yes.
(b) LoL/Tennis: delete, or keep under a collapsed "Archive" group? (c) add a Telegram command for footprint on/off?

**Suggested order for the next agent:**
- ~~commit the shared shell~~ (done, 57086ec);
- IA move 1 (ladder correctness, 🤖 money), then 2 and 3;
- then the visual design system (§5) page by page, with the IA sitemap as the sidebar groups;
- then moves 4–9.

Every step gets a reviewer.

## 7. Input for the refactor from other sessions (owner-forwarded, 2026-09-25)

- **#153 /admin/models brief** → `dev/active/admin-models-page-brief.md`. Read its top section before
  the phase-5 schema: picks carry `model_version` but **no rule/config version**, rules live in
  BOTS_CONFIG + four standalone jobs (already resolved by `scripts/export_bot_config.py` → `bot_config`,
  migration 410), two edge units (probability points vs EV) are in use, and visibility is split across
  four fields. The owner said more input is coming from the session building the new bots — wait for it
  before starting phase 5 or the Bots page / Pick queue redesign.
- **Real bets page / `real_bets` table (owner, 2026-09-25).** The owner never opens `/admin/real-bets`; the
  per-bot picks table already shows "Bet made". What the page adds: € P/L (bots count units), one cross-bot
  list, forward-test bets (no bot_id → unlinkable), reconcile to-do, daily-limit use. Proposal: fold into a
  "Real money" view on Bots and drop the page. The TABLE is load-bearing though — dedupe before placing
  (coolbet_placer, mac daemon, best_price_router), per-match exposure + daily caps, settlement, alerts.
  Refactor question: keep a parallel table joined back by id, or one bets table with a real-money flag
  + actual stake/price/book (which would also fix the unlinkable forward-test bets). Decide in the audit.
