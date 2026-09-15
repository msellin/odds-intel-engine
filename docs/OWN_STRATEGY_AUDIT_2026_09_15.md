# 🤖 OWN — full audit and a tested plan, 2026-09-15

**Scope.** Both repos (`odds-intel-engine`, `odds-intel-web`), the VPS database, and
the 2026-09-13/14 audit corpus, read as one system. Direction: **🤖 OWN first**
(the owner's stated priority 1), 👥 PICKS second. Every number below was either
re-queried from the DB today or is cited to a committed script; nothing is quoted
from prose alone. No code was changed by this audit.

**Written for the owner.** Sections 1–3 are the verdict and the evidence. Section 4
is the plan. Section 5 is the part nobody wants to read: the ceiling.

---

## 1. The verdict in five sentences

1. **The system as built cannot make money at the three books we can legally
   place at, and the last 48 hours of audits proved it four independent ways**
   (model α = 0, sharp anchor is a stale soft quote on our slate, best-of-3 line
   shopping leaves 5.66% residual margin, own-book line movement is unexploitable).
   These findings are sound. I re-checked the load-bearing ones and none moved.
2. **Real money is paused, but the placement stack is still armed underneath the
   pause** — two `--execute` cron jobs are loaded on the Mac today, the session
   kill switch fails OPEN on a DB error, and the Unibet placement arm never reads
   it. That is a Phase-0 fix, not a research question.
3. **There is no pre-match strategy left to test at these books.** Every remaining
   "maybe" — a live Pinnacle feed, more markets, more segments, per-book splits —
   was measured on 2026-09-14 and came back at or below the vig.
4. **Two things remain with a positive expectation, and neither is a model:**
   (a) **in-play, short side only**, at Epicbet, where the book's margin is the
   tightest anywhere in our reachable universe and the search is constrained but
   not closed; (b) **promotions / boosts / free bets** at every EMTA-licensed book,
   which is deterministic +EV and needs no prediction at all.
5. **Even if both work, the honest ceiling is low four figures per year** before
   the books limit the account. The owner should decide, with that number in
   front of them, whether OWN justifies more engineering than Phase 0 plus one
   pre-registered in-play forward test.

---

## 2. What the DB says today (re-queried 2026-09-15)

### 2a. Real money, all time, confirmed placements only (`real_bets.placed_real = TRUE`)

| | n | staked | P&L | ROI | span |
|---|---|---|---|---|---|
| **all** | **139** | €1,390 | **−€97.50** | **−7.0%** | 2026-08-27 → 09-13 |
| `bot_coolbet_value_v1` (line-shop, retired) | 120 | €1,200 | +€2.10 | +0.2% | 08-27 → 09-07 |
| `bot_coolbet_ou_model_v1` (model, off) | 14 | €140 | −€49.60 | −35.4% | 09-09 → 09-13 |
| `bot_coolbet_1x2_model_v1` (model, off) | 5 | €50 | −€50.00 | −100% | 09-10 → 09-12 |

By market and side, same population:

| market · side | n | P&L | ROI | avg odds |
|---|---|---|---|---|
| 1x2 · home | 33 | +€71.00 | +21.5% | 3.01 |
| 1x2 · draw | 24 | +€59.00 | +24.6% | 3.63 |
| 1x2 · away | 30 | −€22.80 | −7.6% | 3.47 |
| O/U 2.5 · under | 26 | −€112.30 | −43.2% | 2.84 |
| O/U 3.5 · under | 8 | −€63.20 | −79.0% | 2.17 |
| O/U 3.5 · over | 10 | −€30.00 | −30.0% | 2.56 |
| O/U 2.5 · over | 8 | +€0.80 | +1.0% | 2.15 |

Read: the line-shop bot broke even (which is what a zero-edge rule at a 7.7% book
looks like when it happens to catch a good fortnight), and **every euro of loss came
from the two model bots in their last five days**, which coincides with the O/U
calibrator defect (migration 335). The 1x2 home/draw profit is n=57 at odds ~3.2
and is not evidence of anything.

There are a further 823 settled `real_bets` rows with `placed_real IS NULL`
(−€418.85 on €4,760) — the pre-2026-08-27 era of manual + phantom-paper rows that the
`OWN_PATH_VERDICT` correctly excludes. Nobody should ever sum the two populations.

### 2b. Paper fleet, last 60 days, own-book close only (`shadow_bets_unique`)

| bot | n | raw own-book CLV | book margin | ≈ EV |
|---|---|---|---|---|
| `bot_unibet_trigger_sharp_1x2_v1` | 76 | +10.96% | ~8.6–9.1% | **≈ +2%** |
| `bot_coolbet_trigger_sharp_1x2_v1` | 74 | +4.41% | ~7.8% | ≈ −3% |
| `bot_coolbet_trigger_sharp_ou_v1` | 20 | +6.35% | ~6.5% | ≈ 0% |
| `bot_v10_all` (the "calibrated" reference) | 161 | +1.97% | ~7.8% | ≈ −5.5% |
| `bot_trigger_1x2_sharp_tight_v1` (the instrument) | 13 | +0.80% | — | too early |

One bot is marginally positive at n=76 over five days. That is not a strategy; it is
what `OWN_BOOK_UNIVERSE` already called "one candidate, nowhere near a decision".
**`bot_v10_all` — the bot whose picks feed `/performance` — is ≈ −5.5% EV at the
books we can bet**, while showing +12.5% ROI at best-of-books prices on the page.

### 2c. The data we actually have to work with

| book | 1x2 fixtures / 14d | median obs per price series (7d) | how it reaches us |
|---|---|---|---|
| Coolbet | 3,872 | **4** | Mac, Imperva, `:03/:33` sweep smeared over ~25 min |
| Epicbet | 3,855 | **23** | VPS via FlareSolverr, `:02/:32` |
| Unibet-Site | 1,951 | 7 | Mac, DataDome, `:15/:45` |
| Pinnacle (AF feed) | 4,353 | 28 | AF `:00/:30`, lagged +0.81pp median vs real |

Retention prunes every series to three rows after 7 days (`ANALYSIS_GOTCHAS` §59). So
**any price-path study has a 7-day window**, and Coolbet — the only book with a
working placer — is the worst-observed book we have. Epicbet is the best-observed and
has no placer. That asymmetry shapes the plan.

### 2d. The PICKS forward test

`sharp_edge_v1` closed at n=8 (−2.97 units); v2 and v3 are live with their own n.
Meaningless either way at this n, and the surfaces correctly refuse to render the
backtest. Nothing to conclude.

---

## 3. Where the edge is NOT — closed, with the evidence pointer

Filed here so the next session does not spend a day re-deriving any of it. Each row
was checked today against the script or the DB, not the doc.

| idea | verdict | the one number | where |
|---|---|---|---|
| Model-anchored 1x2 | **DEAD** | residual α = 0.0000, CI [0, 0.03], against four benchmarks incl. our own books | `residual_test.py`, `c8b729af` |
| Model-anchored O/U | **DEAD until proven otherwise** | every staked pick came from a calibrator applied outside its fitted domain; clean α untested | mig 335, master list #4 |
| Sharp anchor (AF Pinnacle) at our books, 3% floor | **DEAD** | time-aligned ≤15 min: −14.2% (n=112); on the <4%-overround subpopulation: −3.36% (n=72); gated at 4% anchor overround, **0 legs** clear 3% | `d4238ec1`, `ANCHOR_IS_NOT_SHARP` |
| A live Pinnacle feed would fix the anchor | **NO** | paired n=92: AF is +0.81pp *lagged*, not wrong; a fresh row differs by +0.02pp; the ≥9% quotes are real Pinnacle with $200 limits | `AF-PINNACLE-NOT-PINNACLE` step (1) |
| Best-of-3 line shopping | **DEAD** | median best-of-3 overround 5.66% vs 2% kill line (n=359 aligned) | `own_path_kill_criterion.py` |
| Steam / lag at our books | **DEAD** | follow-through β 0.00–0.14 where exploitation needs ≈1; best of 194 cells −4.16% | `own_line_movement.py` |
| Derivative markets (corners, cards, 1H, team totals) | **DEAD** | flat 8.00% margin = automated derivation engine; no cell's CI excludes zero positively | `own_market_expansion_sweep.py` |
| Segments (women, youth, cups, night KOs, longshots…) | **DEAD** | 899 cells, zero at break-even, whole spread inside 3pp of −7.4% | `own_segment_signal_search.py` |
| 27 stored signals vs own-book price | **DEAD** | zero add anything OOS | same |
| Sharp-tight prob-diff instrument | **INSTRUMENT, paper** | +17% ROI beside −5 to −7.6% own-book EV, 12-day effect | `own-sharp-tight-preregistration.md` |
| Expand to Olybet/Optibet/Betsafe/bet365.ee **for line shopping** | **NOT WORTH IT for that reason** | our three books agree to 0.6–1.0pp de-vigged; more books of the same kind cannot open a 5.66% gap to <2% | `OWN_BOOK_UNIVERSE` |

The only analytic thread I would have run myself — the sharp rule restricted to
fixtures where the anchor is a genuine line — was already run on 2026-09-14
(`d4238ec1`) and came back empty. I did not re-run it; duplicating a settled
measurement is how this project's backlog grew.

---

## 4. The plan

Four phases. Phase 0 is mandatory and cheap. Phases 1–2 are the only two
strategies with a defensible prior. Phase 3 is the real-money gate. Every phase has
a pre-registered stop, because the dominant failure mode in this repo has been a
number computed and not read.

### Phase 0 — make "paused" actually mean paused (🤖 OWN, ~1 day, do first)

Verified in code and on this Mac today:

| # | defect | evidence | fix |
|---|---|---|---|
| 0.1 | `is_placement_paused()` **fails OPEN** — returns "not paused" on any DB exception | `workers/automation/coolbet_state.py:310-332`, defended in its own docstring | fail CLOSED, like `ui_place_enabled_bots()` already does 200 lines away. Two safety reads must not point in opposite directions. |
| 0.2 | The pause is read **inside `stage_bet`**, after the browser is driven and the stake is typed — never at run level | `place_coolbet_ui.main()` has no call | one read at the top of `main()`; abort the run |
| 0.3 | **`unibet_placer.place_bet()` never reads the pause**, nor daily caps, nor kickoff cutoff | `workers/automation/unibet_placer.py` — no reference | route every executor through one `assert_may_place()` |
| 0.4 | `best_price_router.py` runs with `--execute` at :20/:50 and is inert **only** because `ROUTER_ALLOW_REAL` is unset in the plist | `local/launchd/com.oddsintel.best-price-router.plist`, **loaded in launchd right now** | unload the plist while OWN is paused; a paused product should not depend on an env var's absence |
| 0.5 | `com.oddsintel.coolbet-ui-placer` runs `--all-enabled --execute` hourly, inert only because `PLACEABLE_BOTS ∩ ui_place_enabled` is empty | **loaded in launchd right now**; header comment still says caps 20/€200 (real: 80/€800) | unload; fix the comment |
| 0.6 | A second complete real-money path exists (`coolbet_placer.place_all_bets(execute=True)` via `scripts/place_coolbet_bets.py`) with its own edge logic and `real_bets` vocabulary | audit §3 | delete it or make it import the UI placer's gates. Two placers to one account is the `RELIABILITY_LEDGER` §4 pattern. |

Smoke tests for each. Success measure: `python3 -m workers.automation.coolbet_control --status`
prints a single line that is TRUE only when nothing on any host can stake, and a
kill-switch mutation test (drop the DB) leaves every executor refusing.

### Phase 1 — in-play, short side only, at Epicbet (🤖 OWN, 4–6 weeks paper, then decide)

**Why this is the one pre-match-adjacent idea with a real prior.** The 2026-09-14
in-play work found no positive strategy but did find the *structure*: on 7,539
level-score snapshots the relative margin is **3.8% on the favourite, 6.7% on the
draw, 14.3% on the underdog**. Every loser tested was buying the long side. Restricted
to prices 1.05–2.30 the house edge nearly vanishes: 0-0 → under 2.5 at 35'–54'
reads **+0.4% [−3.2, +4.0]** (n=1,815); a 2-goal leader at 70'–89' reads **+1.1%
[−1.4, +3.4]** (n=752). And Epicbet's in-play margin measured **5.56%** against
AF's 6.51% — the tightest price anywhere we can place. That is a very different
starting point from −7.4% pre-match.

In-play is also the only regime where a soft book is *structurally* behind: it
reprices on a delay after events, and our state engine (`live_match_snapshots`, 45 s
cadence, 2.2M rows, 35k matches) already knows the score and minute. The edge, if it
exists, is *timing on information we already hold*, not a better model.

**What to build (small):**
1. Promote `workers/jobs/inplay_epicbet_collector.py` from scratch job to a
   scheduled VPS job writing to a proper table (`inplay_book_quotes`: fixture, book,
   market, selection, line, odds, minute, score, `captured_at`, `af_age_s`). It
   already runs; it needs a home and a retention rule.
2. A **paper in-play bot** that fires only on pre-registered short-price triggers
   and records **the Epicbet price on screen at decision time**, never an AF price.
   Settlement is the final score — we own that half outright.
3. The **O/U fidelity check** (`oufid.jsonl`) must be read first: 1x2 fidelity of the
   AF history vs Epicbet is established (median gaps ≤0.42pp); O/U is not, and most
   candidates are O/U.

**Pre-registration (lock before the first paper pick):**
- Triggers: only cells that already read ≥ 0 on the 2.2M-row history **after** the
  wide-window robustness check (`robust.py`), expressed through a price ≤ 2.20.
  Start with the two above. No new cell enters without a 10-minute-wide window and
  a split-half agreement.
- Primary metric: **realised ROI at the recorded Epicbet price**, flat 1 unit,
  cluster-robust CI on fixture. CLV is not admissible in-play (`ANALYSIS_GOTCHAS` §14).
  At odds ~1.6 per-bet sd ≈ 0.5, so ±2% precision needs ~2,400 bets — at 15–25
  triggers/day that is 4–5 months, which is why the stop rules are asymmetric.
- **STOP** at n=400 if ROI < −3% (the vig is winning). **CONTINUE** at n=400 if
  CI includes zero. **PROMOTE to Phase 3** at n≥800 only if CI lower bound > 0.
- Negative control: same triggers, price replaced by the AF aggregate at the same
  second; the gap between the arms is the value of the fresh board and must be
  positive or the collector is not earning its keep.

**Kill criterion for the phase:** if `oufid` shows AF O/U is *not* a faithful proxy
AND the collector cannot hold ≥ 30 concurrent fixtures at 30 s cadence for two
weeks without gaps, the history is unusable and the phase is closed on cost.

### Phase 2 — promotions, boosts, free bets, acca insurance (🤖 OWN, ongoing, low engineering)

Stated in `OWN_PATH_VERDICT` as "the one thing with positive expected value at this
scale" and then dropped as "a product decision". It is the *only* OWN lever that is
+EV by construction, so it deserves a concrete shape:

- **What it is.** A promotion converts a book's margin into a subsidy: a 100%
  odds boost on a fairly-priced favourite is +50% EV; a free bet staked on a
  ~4.0 de-vigged-fair price returns ~70–75% of its face value in expectation;
  acca insurance on 4–5 leg combos of near-fair legs is +EV on the refund alone.
  None of it requires a model. All of it requires **a fair price**, which is the
  one thing this system computes well (Shin de-vig of a tight consensus).
- **What to build.** (1) A promo ledger table: book, promo type, terms, expiry,
  max stake, computed EV, taken/not. (2) A small `promo_ev.py` that takes the offer
  and our consensus fair price and prints EV and the optimal side. (3) Accounts at
  every EMTA-licensed book we do not yet have (Olybet, Optibet, Betsafe, Paf,
  Tonybet, bet365.ee). **This is the correct reason to expand the book universe**
  — more books means more promotions, not tighter prices.
- **Pre-registration.** Every promo taken is logged with its EV *before* the bet.
  Primary metric is **realised P&L vs pre-computed EV**, monthly. Kill: two
  consecutive months where realised sits below EV by more than 1.5 sd, which would
  mean the fair price is wrong.
- **Ceiling.** Realistically low four figures per year at Estonian retail, a few
  hours a month of the owner's time. It is also the lever most likely to survive
  account restrictions, because promotional volume is what the books want.

### Phase 3 — the real-money gate (any strategy, ever)

Unchanged in spirit from the existing pre-registrations; written once so every
strategy is judged the same way:

1. Paper first, on the executable price at the book we would use, recorded at
   decision time with a freshness stamp (the build `OWN-ANCHOR-GATE-VERIFICATION`
   asked for: decision quote ≤ 60 min old or the leg is void).
2. Promote on the strategy's **pre-registered** primary metric and n, never on ROI
   alone unless ROI *is* the pre-registered metric (in-play).
3. Real money starts at **€10 flat** (already the code default), one bot, one book,
   with Phase 0 gates verified by mutation test.
4. **Log the maximum stake the book accepts on every placement.** Account limiting
   is the binding risk and is currently unmeasured; the first stake refusal is the
   day the strategy's ceiling becomes known.
5. Weekly review on realised vs pre-registered EV; stop at the pre-registered n.

### Explicitly NOT in the plan

- Any model-anchored staking, any retrain "for OWN". The clean model beats a
  constant by 2.4% and the market by nothing. Model work is 👥 PICKS research, if
  anything.
- Pre-match sharp-anchor staking, at any floor, with any feed. Measured empty.
- New bolt-on market bots. Measured empty.
- A Pinnacle scraper. `pinnacle_movement_research.py` already exists, self-disabled
  on 2026-06-09, and its spike doc says DO NOT SHIP; the paired test says a live
  feed shrinks edges. The single thing worth taking from the guest API is
  `limits[].amount` as a validity flag, and only for the PICKS anchor.
- Kelly. Compounding an unproven edge is a bankroll error.

---

## 5. The ceiling — say it before spending another week

Pull the numbers together. A working in-play short-side edge of **+2–3%** on
**15–25 bets/day** at **€10–25** is **€110–560/month** before variance and before
limits. Promotions add a similar order. Coolbet and Epicbet will restrict a
consistently winning account; the industry norm is weeks to a few months at the
first sign of sharp action, which caps the stake at a few euros exactly when the
strategy is proven. **Low four figures per year is the realistic OWN outcome if
everything works.** Against that: this codebase is ~120 tables, 26 GB, two hosts,
three anti-bot chains, and a 92-row open queue, most of it built for OWN.

That is not an argument to stop. It is the number that should decide **how much
more to build**. My recommendation:

- **Do Phase 0 regardless.** An armed placer under a fail-open pause is a
  liability whatever the strategy decision.
- **Run Phase 1 as one bounded experiment** with the stop rules above and no
  parallel research threads. If it fails its n=400 check, OWN closes to Phase 2
  only.
- **Start Phase 2 now** — it is the only OWN P&L available this month and it costs
  almost nothing.
- **Do not fund any other OWN research** until Phase 1 reports.

---

## 6. 👥 PICKS — three things that are wrong on the public surface today

Secondary to the ask, but they are live and cheap:

1. **`/performance` publishes the model-era record with no banner.** Its headline
   ROI is `bot_v10_all` at best-of-books prices since 2026-05-04 — a rule that has
   since been retired (mig 335, 1X2 inversion, α=0) and a price basis that is
   ≈ −5.5% EV at any book a reader could use (§2b). `/picks` refuses to link to it
   for exactly this reason, then `/performance` mounts the forward-test panel
   anyway. Either banner it as a closed model-era ledger or take it down.
2. **`/api/v1/track-record`** serves that same cohort and, unlike `/api/v1/upcoming`,
   carries no `meta.edge_basis`. Add the field; the landing hero reads its `roi_pct`.
3. **`/admin/shadow-bots` re-hardcodes odds floors** (`oddsFloor: 2.8 / 1.8`) instead
   of importing `engine-floors.ts` — the one surface describing what stakes real
   money is the one not on the generated source. Web-repo Telegram webhook still
   tells users "Telegram alerts are available on Pro and Elite plans".

The forward test itself is correctly built: no backtest rendered, n and CI on the
live number, control arm recorded, rule pinned by smoke. Leave it alone and let it
accrue.

---

## 7. Codebase hygiene the audits surfaced (file once, cull in one pass)

Dead or duplicated, per the read-only code audit; none of it is load-bearing and
all of it costs reader time or cron cycles:

- `workers/jobs/inplay_bot.py` (135 KB, env-gated off since 2026-08-21) and
  `coolbet_inplay.py`. Phase 1 replaces them; delete when the new bot lands.
- 8 of 11 `BOOK_MARKET_BOTS` entries in `pick_trigger_matcher.py` — retired bots
  that still run a query every 30 min and no-op.
- `bot_configs.WIDE_CONFIGS` (defined, never run); the line-shop O/U stop scoped to
  a retired bot in `place_coolbet_ui.py:809`; `_ODDS_TOLERANCE` in `coolbet_placer.py`.
- `pick_generator.generate` / `on_odds_written` / `match_and_emit` /
  `compute_triggers` swallow every exception — the "silent zero" shape.
- `PIN_CROSS_DRIFT_VETO_ENABLED` — a documented veto that counts and places anyway.
- Web: ~700–900 lines of tier/Stripe code with no purchase path; `stripe`/`svix`
  deps; README says Next 15 on Vercel (it is Next 16 on pm2); `.env.*` files present
  in the working tree — confirm they are gitignored.
- `PRIORITY_QUEUE.md`: 92 ⬜ and 20 🔄 rows. CLAUDE.md's own rule — close decisions
  with a reason, merge sub-tasks into epics — would roughly halve it in an afternoon.

---

## Reproduce

```bash
# real-money ledger and paper fleet, as in §2
python3 scripts/own_path_kill_criterion.py --days 30 --align-min 15
python3 scripts/own_sharp_config_sweep.py --days 150 --diagnostics --bot-clv
# in-play structure (needs the frozen pkl from dev/active/inplay-strategy-discovery-context.md)
# placement safety
launchctl list | grep oddsintel
python3 -m workers.automation.coolbet_control --status
```
