# OddsIntel — Product Roadmap

> Product vision, tier structure, milestone goals, and open decisions.
> Task tracking lives in PRIORITY_QUEUE.md — not here.
> Last updated: 2026-05-19

---

## Product Vision

Two parallel lanes, one product:

**Lane 1 — Match Intelligence** (data & analytics)
A genuinely useful place to research football matches — odds comparison, form, stats, AI injury alerts. Most of the value is visible free; depth unlocks with subscription.

**Lane 2 — Betting Tips** (model-driven picks)
Our best bot's picks surfaced as tips for paying users. Only launched once ROI is validated with real settled bets. Not sold as "bots" — sold as picks with model confidence.

Both lanes feed the same frontend. Tips are the Elite tier differentiator when ready.

---

## Tier Structure

| Tier | Price | Target user |
|------|-------|-------------|
| **Free** | €0 | Casual football fan, curious about the product |
| **Pro** | €4.99/mo | Does own research, wants better data |
| **Elite** | €14.99/mo | Serious bettor, wants model-backed picks |

Founder pricing (locks in for life): Pro €3.99/mo · Elite €9.99/mo
Annual: Pro €39.99/yr (€3.33/mo) · Elite €119.99/yr (€9.99/mo)

### What each tier includes

**Free** (anonymous + signed-in)
- All ~467 daily fixtures with kickoff, league, venue, referee
- Best available odds (single best price across all bookmakers we track)
- H2H records and recent meetings
- League standings + team form (last 5 matches)
- Live scores during matches (auto-refresh)
- Match intelligence grade (A/B/D) + signal teasers on notable matches
- Match interest indicator (⚡ / 🔥 / —)

*Signed-in free additionally:*
- Favourite teams + leagues → "My Matches" filtered view
- Prediction tracker (log picks, track hit rate vs AI)
- Daily free AI value pick (1 unlock per day)
- Match notes (private journal)
- Community prediction voting (1X2 poll)

**Pro** (€4.99/mo)
- Everything in Free
- Full odds comparison across all 13 bookmakers with best-price highlighting (1X2, O/U 2.5, BTTS, O/U 1.5, O/U 3.5)
- Pre-match odds movement charts (1X2 + O/U 2.5)
- Live in-play odds chart by match minute (FE-LIVE)
- Intelligence Summary: top 5 signals in plain English with severity indicators (SUX-4/6)
- Signal group accordion: Market, Team Quality, Context, News & Injuries (SUX-5)
- Signal Delta: "what changed since your last visit" (SUX-9)
- AI injury & suspension alerts with player names
- Confirmed lineups + formation view
- Team season stats (goals avg, clean sheet %, most-used formation)
- Post-match stats (shots, possession, corners, xG) + HT vs FT comparison
- Player ratings
- Match events timeline (goals, cards, subs)
- Value bets page — directional (match + selection + edge tier, no exact %)

**Elite** ← ✅ Open for subscriptions (60+ settled bets, positive ROI confirmed 2026-05-27)
- Everything in Pro
- Full value bets page: exact odds, model probability %, edge %, Kelly stake
- Natural language bet explanations — "Why this pick?" powered by Gemini (BET-EXPLAIN)
- Pro→Elite conversion hook in Intelligence Summary (model conclusion lock)
- CLV tracking (beat the closing line analysis) — per match + personal aggregate
- Tips from top-performing bot once ROI validated
- Personal bankroll analytics — `/bankroll` page: cumulative units chart, ROI, hit rate, avg CLV, max drawdown, model benchmark, per-league breakdown, recent picks (ELITE-BANKROLL ✅ live)
- Telegram alerts — instant DM when a new value bet is found (USER-TELE-NOTIFY ✅ live)
- *Planned:* League performance filter — restrict value bets to leagues where model has historically outperformed (ELITE-LEAGUE-FILTER)
- *Planned:* Custom multi-signal alert stacking — alert only when confidence + edge + line movement all align (ELITE-ALERT-STACK)

### Key UX principle
Everyone sees all matches. Depth of information varies by tier.
Filter toggle: "Show all matches" (default) / "Show matches with [my tier] data" — additive, not restrictive.

---

## Milestones

### Milestone 1 — Free Tier Launch
**Status:** ✅ Ready to promote — site live at oddsintel.app, Stripe set up, all core pages built.

**Goal:** Public-facing product. Someone can find the site, see today's matches, understand what the product is, and sign up.

**What's built:** Public matches page, auth, match detail (free + pro sections with server-side tier gating), signal grade + teasers + pulse (SUX-1/2/3), live scores, track record, onboarding flow, legal pages, analytics, OG image. Stripe checkout + webhook + portal.

**Remaining (post-launch polish):** Stitch redesign (F7) — parked until after first users arrive. Pre-launch items done: Beta label (LAUNCH-BETA) ✅, daily AI pick visible without login (LAUNCH-PICK) ✅.

---

### Milestone 2 — Pro Tier Launch
**Status:** ✅ Ready to launch — all blockers resolved

**Goal:** First paying customers. Enough depth to justify €4.99/mo.

**What's built:** Tier gating, match detail Pro sections (injuries, lineups, stats, events, ratings, odds), value bets page (F5 redesign ✅), track record with real data, onboarding flow. Stripe checkout + webhook + portal. Intelligence Summary (SUX-4), Signal Accordion (SUX-5), Signal Delta (SUX-9), Live In-Play Chart (FE-LIVE). Value bets page: Free=teaser, Pro=directional, Elite=full.

**Ready to promote.** Pro users now see a clear data gap vs Free — deep signal analysis, live in-play odds, full odds comparison.

---

### Milestone 3 — Elite Tier + Tips Launch
**Status:** 🔲 Blocked on data collection — close (~82% of threshold)

**Goal:** Tips product live. Requires validated ROI.

**Blocking condition:** Top-performing bot needs 60+ settled bets with positive ROI. **bot_aggressive has 49 settled bets +93 units as of 2026-05-05.** At ~5-10 bets/day, should hit 60 bets by ~May 8-9.

**What's built:** 24 paper trading bots (16 pre-match + 8 in-play since 2026-05-06), tier B backtest script, bot validation tracker (check_bot_validation.py exits 1 when condition met). In-play bots: strategies A, A2, B, C, C_home, D, E, F using Bayesian xG posterior — see § INPLAY Plan in PRIORITY_QUEUE.md. Elite bankroll dashboard live (ELITE-BANKROLL ✅).

**Remaining:** Singapore/South Korea odds source (B6), PostHog feature flag for tips toggle (INFRA-7).

---

## Current System State (2026-06-24, infra updates 2026-07-13)

> **Infra update 2026-08-24 (SCHEDULER-STALL-RCA / SCHEDULER-AF-429-DEADLOCK)** — the scheduler's
> recurring multi-hour "hangs" (Jul 12, Jul 15, Aug 22) were never the systemd process dying. A single
> APScheduler worker thread wedged inside `fix_stale_live_matches()`, which fan-out-fetched 225-350
> fixtures one AF call at a time inside a job that fires every 15 min with `max_instances=1`; every
> later run was then correctly skipped, silently, for hours. That sweep is now batched and
> wall-clock-bounded, every AF request has a hard retry budget, and a new `job_stall_watchdog` (5-min
> interval) stack-dumps and Telegrams any job that holds its worker past 45 min. **Operational
> consequence: "the scheduler is running" is not evidence the pipeline is running.** Check
> `max_instances blocked` in `journalctl -u oddsintel-scheduler` — that line means a job is wedged.

> **Infra update 2026-07-13** — Supabase `public` schema dropped (SUPABASE-CLEANUP-DROP). Data plane fully on Hetzner VPS Postgres 17 since 2026-07-09 (SUPABASE-TO-VPS); Supabase now holds only Auth (52 users) + Storage `models` bucket (222 MB). Supabase DB is 18 MB, cleared for Free-tier downgrade — pending user dashboard action. See `INFRASTRUCTURE.md` for full state + cost delta.

> **Note 2026-06-24**: Major product collapse. The frontend surface narrowed from
> ~17 public pages (Free/Pro/Elite tiered) to 5: landing, /picks, /performance,
> /privacy, /terms. ~40,000 LoC deleted from frontend. Backend pipeline runs
> unchanged — all bots, all crons, all leagues still being processed. The
> simplification is purely about what gets *surfaced* to users. See the
> "Public surface 2026-06-24" subsection below for current truth. The
> 2026-06-15 section that follows is preserved for historical context.

### Public surface (2026-06-24, post-collapse)
| Surface | Status |
|---------|--------|
| `/` landing | ✅ Minimal hero pulling live ROI from `/api/v1/track-record` |
| `/picks` | ✅ Live pending pre-match picks for the next 36 hours. **Anonymous** = calibrated-only cohort (same set the Telegram public channel ships). **Logged-in** = wider calibrated + beta + active cohort, plus per-row "Mark bet" checkbox persisting to `user_pick_marks`. Wider cohort renders server-side only — no client-fetchable route exposes it. Gated via PICKS-USER-GATE 2026-08-22. |
| `/performance` | ✅ Settled track record + per-bot leaderboard. **Anonymous** = 10-bet ledger teaser + "Sign up free" CTA; **logged-in** = full filterable history (league / market / bot filters, 6-way sort, 200-row cap). Leaderboard subhead surfaces full strategy funnel ("Tested to date: N · proven / underperforming / maturing / retired"). Gate changed from `isPro` to `!!user` on 2026-08-21 (PERF-SIGNUP-HISTORY) — signup is now the reward, not payment (post TIER-COLLAPSE). |
| `/privacy`, `/terms` | ✅ Minimal nav, retained for legal |
| `/api/v1/track-record` | ✅ Public JSON feed of settled bets (median CLV, beat-rate, ROI) |
| `/api/v1/upcoming` | ✅ Public JSON feed of pending picks (next 36h). Narrowed 2026-08-22 to `maturity_label = 'calibrated'` only so it matches the Telegram public channel one-to-one. The wider signed-in cohort is not available via any JSON endpoint. |
| `/admin/*` | ✅ Operator-only, untouched by collapse |
| /profile | ✅ Restored 2026-08-21 as a minimal page (email + tier + sign out); linked from the new nav avatar dropdown |
| WC pages, /value-bets, /matches/*, /live, /accuracy, /bankroll, /learn, /how-it-works, /methodology, /my-picks, /predictions, /recaps, /vs, /welcome, /pricing, /changelog | ❌ Deleted |
| Stripe checkout/upgrade/portal | ❌ Deleted (webhook retained for 2 legacy subscribers) |
| Tier matrix (Free/Pro/Elite) | ❌ Deprecated — no paid product right now |

### Verification stack (added 2026-06-24)
| Layer | Mechanism |
|-------|-----------|
| Live JSON feed | `/api/v1/track-record` and `/api/v1/upcoming` |
| Daily public ledger | `ledger/YYYY-MM-DD.json` committed by `github-actions[bot]` nightly at 22:45 UTC via `.github/workflows/track_record_ledger.yml`; SHA-256 in `ledger/index.json` |
| Bitcoin blockchain anchor | OpenTimestamps stamp on every daily snapshot; the workflow's `ots upgrade` step pulls in Bitcoin block-header proofs the day after stamping |
| Open source | engine + web both public on GitHub; anyone can replay picks against ESPN/Flashscore via match_id + kickoff_utc |

### Telegram public channel (added 2026-06-24)
- `@oddsintelpicks` — public, free, anyone can join
- Bot (renamed from "Coolbet Bot" to "OddsIntel" via @BotFather) is admin with Post Messages
- Pipeline: every calibrated-maturity pre-match pick (1x2/OU/BTTS) auto-posts via `coolbet_signaler.py` → `workers.notify.telegram.send_telegram_public()`
- Beta/active/experimental picks stay in operator chat only — public channel is curated, not the firehose

### Pipeline additions 2026-06-24
| Cron | Schedule | Purpose |
|------|----------|---------|
| `job_closing_snap` | */5 12-23 UTC | Per-fixture odds snap for matches in T-15→T+5, stored with `is_closing=TRUE` — fixes the historical 25% Pinnacle close coverage |
| `job_odds_tomorrow` (3 new) | 04:00, 10:00, 16:00 UTC | T24H-COVERAGE — expand T-24h odds snap coverage from 15% to ~60-70% to support the high-ROI early-fire cohort surfaced by `dev/active/day_ahead_backtest_results.json` |
| GitHub Actions ledger | 22:45 UTC daily | `track_record_ledger.yml` — exports + OTS-stamps + commits the daily snapshot |

### Bot roster changes 2026-06-24
- **bot_ah_home_fav** — retired (calibrated AH was -13.63% ROI on n=132 since 2026-05-24; one good week then 5 losing weeks). Shadow_bets continues — reactivation gated on ≥30 shadow bets at ≥3% ROI sustained over a week.

---

## Current System State (2026-06-15)

### Backend
| Component | Status |
|-----------|--------|
| API-Football Ultra ($29/mo) | ✅ Primary data source |
| ① Fixtures (04:00 + 4 refreshes/day) | ✅ AF fixtures + league coverage + postponement detection |
| ② Enrichment (04:15/10:30/13:00/16:00 UTC) | ✅ Standings, H2H, team stats, injuries |
| ③ Odds (every 30min 07-22 UTC + closing odds 13:30/17:30/20:00) | ✅ AF bulk odds, 13 bookmakers |
| ④ Predictions (05:30 UTC) | ✅ AF predictions (coverage-aware) |
| ⑤ Betting (8×/day: 06:00/09:30/11:00/13:30/15:00/17:30/19:00/20:30 UTC) | ✅ Poisson/XGBoost + Pinnacle anchor + sharp consensus gate + veto filters |
| ⑥ LivePoller (24/7, adaptive 30s live / 120s idle) | ✅ Live scores, events, lineups, in-play odds + in-play bots |
| ⑦ AI news checker (4×/day + 14:30) | ✅ Gemini 2.5 Flash, qualitative-only |
| ⑧ Settlement (21:00 + 01:00 UTC) | ✅ Settle + CLV + Pinnacle CLV + ELO + post-mortem + weekly Platt + blend refit (Wed+Sun) + dynamic DC rho. **BET-VOID-INTEGRITY 2026-08-24** — the 15-min `settle_ready` sweep now re-settles bets that are `void` on a match that has since finished with a score. Voiding a postponed fixture used to be a one-way door: 194 bets (57 winning `double_chance 1X` on Piast 1-1 Legia, 133 from an unversioned 2026-08-23 cleanup, 4 `bot_v10_all` rows that moved bankroll) sat permanently misreported. Genuine AH/DNB pushes and `void_reason='quarantine'` rows are never touched. |
| ⑨ Historical backfill | ✅ Complete 2026-05-10 — 47,228 finished matches; `backfill_complete.flag` set, job auto-disabled |
| ⑨b Internationals backfill | ✅ **WC-PHASE-2 2026-06-02** — `scripts/backfill_internationals.py` pulled fixtures + lineups/events/stats/players for 59 national-team competitions (WC 2018/2022, Euro 2020/2024, Copa America, AFCON, Asian Cup, Gold Cup, all 4 UEFA Nations League editions, WC 2022 + WC 2026 qualifiers across all 6 confederations, Friendlies, regional). 5,000+ international matches now in DB — enables training a national-team predictor (WC-PHASE-3) and the WC 2026 landing page (WC-PHASE-4). Smoke: INTL-BACKFILL. |
| WC 2026 fixtures | ✅ **WC-PHASE-1 2026-06-02** — 72 group-stage fixtures backfilled via new `fetch_fixtures --league/--season` mode. Migration 163 flipped `show_on_frontend=true` on the WC league. AF still returns no bookmaker odds + flat 33/33/33 predictions for WC fixtures (will populate closer to kickoff but engine doesn't depend on it). Smoke: WC-FIXTURES-IN-DB. |
| WC 2026 ad-landing OG cards | ✅ **WC-AD-LANDING-OG 2026-06-09** — Three new `opengraph-image.tsx` files (1200×630, next/og edge runtime) for /world-cup, /world-cup/bracket, /world-cup/groups-predictor so Meta/IG ad unfurls render WC-themed cards instead of the generic root OG. Bracket card surfaces all 5 named AI ghost competitors. Page-level `openGraph` + `twitter` metadata blocks added for ad-friendly titles/descriptions. Smoke: WC-AD-LANDING-OG. |
| WC 2026 odds via Odds API | 🪦 **Retired 2026-06-25** (ODDS-API-WC-DEACTIVATE) — Originally daily 06:30 UTC sweep of `soccer_fifa_world_cup`. WC's commercial value to us was minimal; The Odds API budget repurposed for tennis (TENNIS-PAPER-BETS). Key + client kept. |
| WC Bracket + Group Standings + AI ghosts | ✅ **WC-GROUP-PREDICTOR + WC-AI-GHOSTS Done 2026-06-02** — Second WC game (group standings predictor at `/world-cup/groups-predictor`, 48 picks per user, scoring 5/3/2/1 + 5pt perfect-group bonus, max 192) + five named AI ghost competitors (Elite/Pro/Free AI / Market Implied / Chalk) seeded via `scripts/generate_ai_brackets.py`. Combined leaderboard ranks humans + AI by `total_score = bracket + group`; only humans eligible for prizes. Migration 170 introduces `wc_group_predictions`, relaxes wc_bracket_picks / wc_bracket_meta to accept AI rows (XOR(user_id, ai_label)), adds `total_score` + `current_percentile`. Activity tiles on `/world-cup` header surface entry counts. |
| WC Bracket stage-gated rewrite | ✅ **WC-BRACKET-STAGE-GATED Done 2026-06-02** — Replaced the single-shot full-bracket with a stage-gated BBC-style flow. Each knockout round (R32 → R16 → QF → SF → Final) opens after the previous round resolves and locks at its first kickoff. Scoring is POSITIONAL (winner of THIS matchup). Migration 171 (additive) adds `wc_bracket_slot_assignments` + `matches.round_label`. `workers/jobs/wc_bracket_slot_sync.py` runs every 30 min during WC window; parses AF round labels and seeds slot assignments; inline-fires `generate_ai_brackets --round <r>` when a new round seeds. FE: phased `WCBracketBoard` with 4 lifecycle states (unseeded / open / locked / settled). |
| WC 2026 full landing page | ✅ **WC-PHASE-4 Done (pre-2026-06-06)** — `/world-cup` landing at `src/app/(app)/world-cup/page.tsx` (894 lines, 7 tabs: Overview / Schedule / Groups / Knockouts / Teams / Leaderboard / Predictions). 5,000-iteration Monte-Carlo advancement probabilities via `computeAdvancement()` with ELO fallback. Tiered access (Pro lock on knockout AI predictions). 17 WC components, 9 supporting libs, 8 backend jobs, AI ghost leaderboard, vs-you scorecard, AI match previews integration. |
| Sunday cron chain (03-06:30 UTC) | ✅ **2026-06-15** — 03:00 weekly_retrain → 04:00 meta_retrain → 05:00 meta_validate → 06:00 weekly_threshold_check → 06:30 weekly_bot_review. Threshold check (`THRESHOLD-CHECK-WEEKLY 2026-06-06`) emails gate-count snapshot. Bot maturity review (`BOT-MATURITY-REVIEW-WEEKLY 2026-06-15`) emails per-bot 30/60/90d hit/ROI/CLV + PROMOTE/DEMOTE/HOLD verdict; thresholds: PROMOTE if real ROI > +10% AND sim CLV > +5% AND maturity != calibrated, DEMOTE if real ROI < -5% AND maturity = calibrated, HOLD otherwise. Closes the manual-promotion loop that let bot_high_alignment auto-place real money in the 2026-06-13 incident. |
| ⑩ AI match previews (07:15 UTC) | ✅ Gemini 200-word previews for top 10 matches |
| ⑪ Email digest (10/12/14/16 UTC slots) | ✅ Smart-slot digest + value bet alerts (16:00/20:45) + weekly (Mon 08:00) + watchlist (08:30/14:30/20:30) |
| Pre-match bots (active) | ✅ **16 active** as of 2026-06-15. **Calibrated (4)**: bot_v10_all, bot_ah_home_fav, bot_1x2_specialist, bot_dnb_specialist. **Active (6)**: bot_conservative, bot_greek_turkish, bot_opt_away_british, bot_opt_away_europe, bot_opt_home_lower, bot_opt_ou_british. **Beta (4)**: bot_high_alignment, bot_high_roi_global_v2, bot_ou_specialist, bot_proven_leagues_v2. **Experimental (2)**: bot_acca_leg_shadow, bot_btts_v2. Cherry-pick placer real-money gate: `maturity_label='calibrated'` only — see CHERRY-PICK-PLACER + BOT-MATURITY-REVIEW-WEEKLY for the audit cadence. |
| Combo/acca bots | ⏸️ **All retired 2026-06-06** — bot_acca_coolbet, bot_acca_proven, bot_acca_value, bot_combo_system, bot_combo_proven_system. The COMBO-RESTRUCTURE 2026-05-22 universe (N=5 only, require_ou15=True, ≥8% per-leg edge) didn't produce enough placeable picks at the per-market edge floors that landed in PER-MARKET-EDGE-V2. Combo infra (`real_bets.combo_legs JSONB`, `system_type`, `_settle_system_fours_up()`) is preserved for future revivals. |
| Pre-match bots (retired) | Original 8 retired 2026-05-17/19 (bot_aggressive, bot_lower_1x2, bot_opt_home_lower, bot_draw_specialist, bot_conservative, bot_dc_value, bot_dc_strong_fav, bot_dnb_away_value); migrations 117+122 (2026-05-22) un-retired most for data volume. 2026-05-25: **bot_ou15_defensive** (migration 129, BOT-OU15-DIAGNOSE-CLOSE — calibration drift). 2026-05-27: **bot_dc_value, bot_btts_all, bot_btts_conservative** (migration 137, RETIRE-DC-BTTS — DC derived-market edge unreliable, BTTS model overestimates hit rate 15.6pp, negative CLV). 2026-05-28: **bot_high_roi_global, bot_proven_leagues** (HRG-V2 — superseded by v2 versions). 2026-06-01: **bot_dc_specialist** (migration 155, RETIRE-DC-SPECIALIST — −7.5% ROI / +3.7% CLV on n=58 since v2; DC derived-market root cause same as bot_dc_value), **bot_lower_1x2** (migration 156, RETIRE-LOWER-1X2 — stale-flag fix), **bot_aggressive** (migration 160, RETIRE-BOT-AGGRESSIVE — third stale-flag fix; self-stopped firing 2026-05-24, 705 stale bets inflated active-cohort drag), **bot_draw_specialist** + **inplay_f** (migration 162, STALE-FLAG-AUDIT — 4th + 5th stale-flag fixes; draw_specialist -100% ROI / -39.6% CLV on n=4 last 30d, inplay_f de facto retired since 2026-05-09). Migration 162 also CLEARED retired_reason text on 2 recovered bots (bot_conservative +104% ROI n=8 last 30d, bot_opt_home_lower +51.9% n=20) that had been re-enabled by migration 122 but kept misleading reason text. Reasons in DB, collapsed in /performance. **Performance page**: page.tsx now applies the live-DB retired filter to the active leaderboard so newly-retired bots disappear immediately (previously a 30-min lag waiting for dashboard_cache rebuild — same pattern that was already in place for the retired_breakdown section). |
| Odds-range tightening | ✅ **PER-BOT-SLICE-TIGHTEN 2026-05-18**: bot_ou25_global (cap 2.50), bot_ou35_attacking (cap 3.00), bot_btts_conservative (cap 2.00), bot_greek_turkish (cap 3.50). bot_btts_all reverted (live data contradicted backtest). ✅ **SLICE-LIVE-VALIDATE 2026-05-25** (pre Phase 3.5): bot_aggressive odds (1.25, 5.00) → (1.25, 2.50) [retired 2.50-3.00 and 3.50+ leakers], `selection_filter` excludes Draw (live ROI -32.7% on n=89); bot_btts_all odds (1.50, 2.80) → (2.00, 2.80) [retired 1.50-2.00 bucket at live ROI -13.9%]. Net retroactive live P&L delta: +€272 net positive. Baseline + comparison tool in `dev/active/backtest-slice-baseline.csv` + `scripts/slice_live_validate.py`. |
| Shadow runs (06:30 / 11:30 / 15:30 UTC) | ✅ `shadow_bets` — all bots at every window. Per-bot per-cohort factorial ROI ready ~2026-06-15. |
| Experimental shadow bots (2026-08-19 cohort) | ✅ **6 active** after PER-BOT-SWEEP-2026-08-24 retired 2. **Line-shop (3)**: `bot_pin_1x2_home_v1`, `bot_sweep_ou25_v1`, `bot_sweep_ou35_v1` — no model dependency, gate on **de-vigged** Pinnacle edge ≥3%, tiers 1-2. **Model-driven (3)**: `bot_sweep_1x2_home_v1`, `bot_sweep_1x2_draw_v1`, `bot_sweep_btts_yes_v1` — tiers 2-3, unchanged thresholds. **Retired 2026-08-24**: `bot_pin_1x2_draw_tier4_v1` (5% gate below a 12.2% overround — 85% of picks negative-EV, −40.8% live), `bot_no_pin_home_v1` (negative at every threshold tested, −10.6% live). Config history in `bot_config_history` (migration 281) — every config is recoverable. Promotion gate is a **t-statistic** (\|t\| ≥ 1.65, n ≥ 200), not a raw ROI level — SHADOW-PROMOTION-GATE-2026-08-26 Monte-Carlo showed the old `n≥50, ROI≥3%` rule promoted a **truly break-even bot 43% of the time** and retired a genuinely +5% bot 27% of the time, and that raising n does not fix a threshold problem (at n=2000 a break-even bot still promotes 17%). CLV is read from **`clv_pinnacle`** (de-vigged Pinnacle close, so 0 = Pinnacle-fair); the old any-book `clv` showed all nine bots positive including two retired for being negative-EV by construction. `bot_sweep_btts_yes_v1` shows **no CLV at all** — API-Football's Pinnacle feed carries only 8 bet types and BTTS is not one of them, so it has no sharp anchor. De-vig is **Shin**, not proportional (LINESHOP-SHIN-DEVIG-2026-08-26). See `MODEL_WHITEPAPER.md` §10c. |
| Accessible-bookmaker filter | ✅ Edge math restricted to EU/Estonia-accessible books. `recommended_bookmaker` per bet. |
| Active production model | ✅ **`v20260607`** (since 2026-06-07) — 58,019 training rows (+1,752 vs v20260531 incl. June 6 CLV backfill). Beats v20260524_market: 1X2 −16.4% log_loss, AH −4.2%, BTTS −3.0%; OU +2.8% (TIER-C-EXPAND data composition drag — ongoing). Promoted 2026-06-07 via `scripts/promote_model.py`. **Env var**: `MODEL_VERSION=v20260607`. **OU per-tier routing** (OU-CLV-OPTION-B-RE-EVAL 2026-06-12): per-tier OU 2.5 holdout (n=219) showed v20260607 beats `v14_recreate_2026_05_11` on T1 by **+49pp ROI@+5%** (45.23% vs −4.09% on n=180); T2/T3+ holdout too thin to falsify. Routing shipped via `_resolve_version("ou", tier=tier)` reading `MODEL_VERSION_OU_T{1|2|3}` → `MODEL_VERSION_OU` → `MODEL_VERSION`. Env vars: `MODEL_VERSION_OU_T1=v20260607` (promotes global on T1), `MODEL_VERSION_OU=v14_recreate_2026_05_11` (T2/T3+ default, unchanged from 2026-06-07). Full history in `docs/MODEL_HISTORY.md`. |
| Meta-model (B-ML3) | ✅ **3 candidate bundles in Storage** — v21 (logistic, CV AUC 0.569), v22 (logistic + G fixed, 0.572), v23_xgb (XGBoost, 0.587). Currently in PASSIVE scoring (`META_B_ML3_ENABLED=false`). **2026-06-01 pre-flight on n=657 settled bets: all 7 bundles FAIL** the 5pp gate — top-quintile CLV-beat is INVERSELY related to bottom (Δ = −7 to −20 pp). Formal verdict still at 2026-06-10; current expectation = retrain v3 with more data, not activate. |
| BOT-HIGH-ALIGNMENT | ✅ Live since 2026-05-25 07:05 UTC. New paper bot fires only on alignment_class=HIGH across all markets, 3% edge floor. Pipeline supports per-bot `min_alignment_class` config. 2026-06-13 incident: had been auto-placing real money for days because the Mac daemon lacked the maturity_label gate Railway had — bot is `beta` and accumulated 59 real bets at -19.7% ROI before the gate was enforced everywhere. Now the Coolbet placer (placer + signaler + Mac daemon) all enforce the calibrated-only gate via `_allowed_maturity_labels()`. |
| Coolbet placer architecture | ✅ **COOLBET-SIGNALER-A 2026-06-12** — Replaces Railway-side auto-placer with a Telegram-signal flow after the overnight 100+ SMS spam incident. Signaler reads `simulated_bets` for qualified picks, sends one Telegram per bet, marks `signaled_at` to prevent re-fire. No Coolbet API calls in the hot path. **Mac-at-home daemon** (`workers/automation/coolbet_mac_daemon.py`) handles auto-placement from a residential IP, signaler remains as safety net. Auth: **CDP-Chrome JWT auto-renew** (`COOLBET-CDP-JWT-EXTRACT 2026-06-12`) — operator's CDP-Chrome session (`--remote-debugging-port=9222`, separate profile `Chrome-CDP-OddsIntel`) auto-renews JWT every ~20min via Coolbet's `/s/auth/renew-token`; daemon reads via raw CDP websockets. DB-backed JWT store (`coolbet_session_state.jwt_current`, mig 245) replaces the env-var sync. `placement_paused` kill switch in DB. |
| Anonymous auth (frontend) | ✅ **ANON-AUTH-PHASE-1/2/3/4 Done 2026-06-10** — Lazy anonymous Supabase user creation on first save action (favorite_match / tracker_pick). Migration 234 (profiles.email nullable + RLS hardening on 9 tables, mig 235; SECURITY INVOKER on cs2_pit_team_map, mig 236). In-place upgrade UX via `<UpgradeModal />` — Google + Discord OAuth via `linkIdentity` (preserves user.id + favorites), email+password, merge-conflict path. Cloudflare Turnstile invisible captcha on signup. 90-day prune cron. Ops snapshot adds anon metrics. |
| Phase 3.5 paper window | ✅ **2026-05-25 → 2026-06-07 — concluded**. The controlled paper-only validation window closed on schedule; model v20260607 promoted same day. Real-money placement gated to calibrated cohort only (CHERRY-PICK-PLACER). The 2026-06-13 incident exposed that the Mac daemon was placing for ALL bots regardless of maturity_label (Railway had the gate, Mac didn't); fixed + BOT-MATURITY-REVIEW-WEEKLY now systematically prints PROMOTE/DEMOTE/HOLD verdicts every Sunday. |
| ML model registry | ✅ Supabase Storage auto-upload + lazy download on scheduler start. 16 bundles archived. `MODEL_VERSION` env var. |
| match_feature_vectors | ✅ Nightly ETL + live-build on every betting refresh |
| Calibration | ✅ Per-tier Platt (1X2 1-feature, OU 2.5 2-feature). BTTS Platt fitted 2026-05-27 (offline holdout, n=139). DC + AH Platt fitted 2026-05-28 from live simulated_bets (`scripts/fit_platt_live.py`): `double_chance_1x` (n=58), `double_chance_x2` (n=105), `asian_handicap` aggregate (n=128). `apply_platt()` has `_MARKET_ROOTS` fallback for granular AH keys. Blend weights optimized. Dynamic DC rho. Weekly refit. **In-play calibration infrastructure (INPLAY-CALIBRATION-COMPLETE 2026-06-15)**: central `apply_platt` call in `_build_inplay_bet_data` + parameterized `scripts/fit_platt_inplay.py --strategy NAME`; canonical key shape `{bot}_{market}_{selection}` shared between read and write paths via `workers.jobs.inplay_bot.inplay_market_key()`. Every in-play bet writes `calibrated_prob` (equals raw until a Platt row lands for the key — honest no-op). Strategy E's existing `"inplay_e_under_25"` key preserved via `trigger["market_key"]` override. |
| Pinnacle signals | ✅ Implied probs, line movement, veto gate, Pinnacle-anchored CLV |
| pseudo_clv | ✅ All ~280 matches/day |
| Featured leagues | ✅ `show_on_frontend` flag. ~50 curated leagues. |

### Frontend (odds-intel-web)
| Page | Status |
|------|--------|
| Landing page | ✅ Built |
| Auth (magic link + Google) | ✅ Custom SMTP via Resend, branded emails |
| /matches | ✅ Today + Tomorrow tabs, smart sort, dual layout, signal grade + pulse + teasers (SUX-1/2/3), What Changed Today, "X analyzing" counter, community vote splits |
| /matches/[id] | ✅ Free+Pro+Elite sections, server-side tier gating, all odds markets, Intelligence Summary (SUX-4/6/7), Signal Accordion (SUX-5), Signal Delta (SUX-9), Live in-play chart, bot consensus, Model vs Market vs Users, AI preview, post-match signal reveal |
| /value-bets | ✅ Tiered: Free=teaser+stats, Pro=directional picks, Elite=full table + BET-EXPLAIN |
| /performance | ✅ Real Supabase data (`/track-record` URL kept as a redirect for any external backlinks) |
| /predictions | ✅ 8 featured leagues, SEO prediction pages with FAQ schema |
| /learn | ✅ 12-term betting glossary with FAQ schema |
| /methodology | ✅ Public model explanation |
| /bankroll | ✅ Elite-gated personal bankroll analytics (ROI, CLV, drawdown, per-league) |
| /my-picks | ✅ Personal bet tracker + "Model vs You" + shareable pick cards |
| /welcome onboarding | ✅ Built |
| /admin/bots | ✅ Superadmin bot P&L dashboard |
| /admin/place | ✅ Coolbet auto-place candidate table. **PER-MARKET-EDGE-V2 2026-06-06**: badge gates per market (1x2 ≥10%, o/u ≥3%, AH ≥5%, BTTS ≥10%, DC retired) instead of a flat 5% floor. Mirrors the placer's per-market thresholds. |
| /admin/real-bets | ✅ Real-money placement log + ROI/CLV stats. **PER-MARKET-EDGE-V2 2026-06-06**: split into Era v1 (pre `MARKET_THRESHOLDS_V2_EPOCH = 2026-06-06T17:00:00Z`) vs Era v2 (post) so the threshold-change lift is measurable in isolation. Daily breakdown row highlighted at the epoch. |
| Stripe payments | ✅ Live production mode since 2026-05-04. Checkout + webhook + portal + tier gating + annual billing + founding rates + promo codes |
| Superadmin tier preview | ✅ Cookie-based tier switcher for QA |

### Data coverage
- Fixtures with any model data: ~200/467 (43%)
- Tier A team coverage: 541 → 864 unique teams after TIER-C-EXPAND (2026-05-19) added 14 top divisions (ARG/AUT/BRA/CHN/DNK/FIN/IRL/JPN/MEX/NOR/POL/RUS/SWE/USA).
- Matches with Tier A predictions: ~50-100/day (was ~30-50)
- Matches with Tier B predictions: ~180/day
- Matches with Tier C predictions (AF xG → Poisson grid since TIER-C-AF-XG 2026-05-19): every fixture where AF returns `af_goals_home/_away` — typically the rest of the slate, including small leagues like Syria/Gabon/Uganda/Iraq. OU/BTTS/AH now fire here instead of being hardcoded to a 50/50 prior.
- Singapore S.League (+27.5% ROI): no live odds feed — biggest gap

---

## Launch Checklist (manual steps — only Margus can do these)

- [x] Gemini API key — created in AI Studio for OddsIntel project
- [x] Deploy to Vercel — project linked, env vars set
- [x] Domain — oddsintel.app bought and connected to Vercel
- [x] Google Search Console — verified, sitemap submitted
- [x] Migration 009 applied in Supabase SQL editor
- [x] **Stripe** — production mode live 2026-05-04. Products + prices created (Pro €4.99, Elite €14.99 + annual + founding). Checkout + webhook + portal built.
- [x] **Stripe webhook endpoint** — live endpoint at `https://www.oddsintel.app/api/stripe/webhook`, `STRIPE_WEBHOOK_SECRET` updated in Vercel
- [x] **GitHub secrets** — `SUPABASE_ACCESS_TOKEN`, `SUPABASE_PROJECT_REF`, `API_FOOTBALL_KEY`, `SUPABASE_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_DB_PASSWORD`, `GEMINI_API_KEY` all set
- [x] **Vercel env var** — `GEMINI_API_KEY` added to Production (for BET-EXPLAIN `/api/bet-explain`)
- [x] **Resend** — account created, `oddsintel.app` domain verified, `RESEND_API_KEY` + `DIGEST_FROM_EMAIL` + `SITE_URL` set in Railway + `.env`

---

## Engagement & Growth Strategy

**Full playbook:** `docs/ENGAGEMENT_PLAYBOOK.md` — synthesized from 4 independent AI brainstorm sessions + web research (2026-04-30).

**Core principles:** No gamification, premium analytical tone, transparency as differentiator, social proof through aggregate data (not forums/profiles).

**Phase 1 (launch sprint):** ✅ All done — ENG-1 through ENG-7 shipped by 2026-05-05.

**Phase 2 (retention):** ✅ All done — ENG-8 through ENG-14 shipped by 2026-05-05. Watchlist alerts, personal bet tracker + Model vs You, weekly email, What Changed Today, Model vs Market vs Users, shareable pick cards, SEO prediction pages.

**Phase 3 (differentiation):** Market inefficiency index (ENG-15, ~June — needs 30 days data), season-end review (ENG-17, ~Aug+).

---

## Open Decisions

| Decision | Options | Status |
|----------|---------|--------|
| Tier names final? | Free/Pro/Elite | ✅ Done |
| Tips: picks or signals? | "Today's picks" vs "What bot_X would bet" | ⏳ Pending |
| Design: Stitch redesign or ship now? | Polish current vs wait for Stitch designs | ⏳ Pending |
| user_bets feature at M3? | Follow a tip → personal P&L | ✅ Promoted to ENG-9 (Phase 2, ~May W3-4) |

---

## Bot Strategy

**24 paper trading bots** running across two categories (8 retired, 16 pre-match + 8 in-play active):

**Pre-match (16 active, since 2026-04-27):** Based on historical backtest data — edge thresholds and league filters derived from football-data.co.uk 2007-2025 and beat_the_bookie 2005-2015. Includes `bot_proven_leagues` (5 cross-era confirmed leagues). 5 morning / 6 midday / 5 pre-KO timing cohorts. 8 retired (reasons in DB). **PER-BOT-EDGE-THRESHOLD-APPLY 2026-05-25**: applied 25K-row sweep optima pre Phase 3.5 — bot_aggressive/v2 → 15%, bot_ou35_attacking → 14%, bot_btts_all → 12%, bot_btts_conservative → 8%.

**In-play (8 bots, since 2026-05-06):** Rule-based strategies A, A2, B, C, C_home, D, E, F using Bayesian xG posterior. Run inside LivePoller every 30s. See § INPLAY Plan in PRIORITY_QUEUE.md.

**Never retire current bots when adding new ones.** They are the baseline — their ROI data is what proves (or disproves) whether new bots are better.

New bots planned based on live data accumulation:

| Bot | Trigger | ~When | What's different |
|-----|---------|-------|-----------------|
| `bot_meta_v1` | 3000+ quality CLV rows (created_at >= 2026-05-06, clv IS NOT NULL) | ~late June (582/3000 as of 2026-05-12, ~60/day avg) | Uses logistic regression EV score instead of hardcoded thresholds. First bot learning from live data. **Note:** original ~May 17 estimate assumed ~273/day; actual rate ~60/day. MFV row threshold (3,819) already met — CLV outcome rows are the binding constraint. |
| `bot_high_alignment` | 300+ settled bot bets (>= 2026-05-06) | ✅ Ready now (590 settled as of 2026-05-12, was ~June 5 estimate) | Only bets when alignment_class=HIGH. Fewer bets, higher precision. Implement ALN-1 first (threshold met). |
| `bot_retrained_xgb` | ✅ HIST-BACKFILL complete 2026-05-10 — gated on ML-RETRAIN-1 run | ~June | XGBoost retrained on recent data, not 2007-2025. Backfill done; awaiting `workers/model/train.py` rerun. |
| In-play strategies G, H | Week 2 after Phase 1A launch | ~May 13 | Shot Quality Under + Corner Pressure Over |
| In-play strategies I, J, K | Week 3 after Phase 1A launch | ~May 20 | Possession Trap + Dominant Underdog + 2H Burst |

---

## Notes & Context

**Why bots are internal tools, not the product:**
The 24 bots are validation instruments — they find which markets/leagues have real edge before we sell tips. The product is the picks from the best-performing bot.

**Scotland League Two cross-era signal:**
+12.3% ROI in mega backtest (2005-15) AND +21% in recent 2022-25 backtest. Two models, two eras, same direction. Most consistent signal we have.

**Greece/Turkey era sensitivity:**
Positive ROI in 2022-25 data but negative in 2005-15. Keep running for data but don't promote to tips until 30+ settled bets confirm current-era edge.

**CLV as the short-term proof metric:**
Results take weeks to accumulate (variance). CLV (did we beat the closing line?) is measurable within days. A bot consistently beating the closing line = finding real value even before wins/losses confirm it.

**Pricing rationale:**
€4.99/mo Pro is a no-brainer for anyone who bets more than once a week — one saved bad bet pays for 6 months. €14.99/mo Elite is priced for serious bettors who understand edge and CLV.
