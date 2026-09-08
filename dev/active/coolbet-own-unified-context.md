# Coolbet OWN-unified — context / findings

## Owner clarifications (2026-09-08)
- `real_bets` is MIXED history: historically fed by /admin/place-bets page (operator
  manually placed on Coolbet + marked "bet made" → real_bets), PLUS UI-placer auto rows
  since 2026-08-27. The −€387 net is manual+auto across many now-retired bots. History.
- TARGET STATE: UI placer ONLY, zero manual bets ever (it works without Imperva now).
- End goal: ONE unified, robust, monitorable flow — start/stop/pause, up/down counters,
  self-verify what it placed via Coolbet, no duplicates, full transparency.

## Real-money audit (real_bets, 2026-09-08)
- 960 UI-placed rows, €6,057 staked, net −€387.62 (~−6.4% turnover). MIXED provenance.
- bot_coolbet_value_v1 (current allowlisted line-shop): n=120, +€2.10, CLV +10.25 on 1x2,
  slippage +0.86% — break-even ROI but strong CLV (not-yet-converged pattern).
- Bleed is retired markets/bots: combo −121, double_chance −115, BTTS −56, OU35 −93,
  bot_aggressive −34, bot_dc_value −79.
- Vocabulary split is real AND diverges: 1x2 n=272 +3.0%/CLV+10 vs 1X2 n=131 −5.7%.

## Codebase map (from Explore agent, verified file:line) — key facts & CORRECTIONS
- 5 Coolbet launchd jobs, ALL currently unloaded: coolbet-ui-placer (REAL, hourly 06:00–21:00,
  --execute), coolbet-mac-daemon (paper, KeepAlive, execute=False hardcoded @589/591),
  coolbet-odds-snapshot (:03/:33), coolbet-feed-watchdog (:00/20/40), coolbet-resume (one-shot 13:45).
- Only two code paths move real money: place_coolbet_ui.py --execute, and coolbet_placer
  place_all_bets(execute=True) via manual CLI. Daemon never passes execute=True.
- CORRECTION to our doc: UI placer does NOT read user_placed_at/user_skipped_at (those are
  simulated_bets cols used by Path B only). UI placer writes user_pick_marks (state 1/2) but
  does NOT read it back for dedup. So marking simulated_bets.user_placed_at does NOT stop the
  UI placer — it dedups via coolbet_placement_attempts(outcome='placed') + exposure_conflict
  vs real_bets (in-memory within a pass).
- Path A self-verify = BALANCE DELTA before/after place() (coolbet_ui_placer.py:1600-1616),
  NOT ticket-id readback (Coolbet returns no ticket id in this UI flow). Re-reads live odds +
  re-applies _min_odds_for before staking; asserts slip holds exactly 1 selection.
- Kill switch: coolbet_session_state.placement_paused (id=1), is_placement_paused()/set_...;
  FAILS OPEN on DB error; checked deep in execute path only (paused system still loads picks +
  writes attempt rows). Telegram /pause /resume; or browser_sync --refresh-jwt --resume-placement.
- Pick-gen Path A: _run_coolbet_value_pass (daily_pipeline_v2:4816), edge≥0.03 (_LINESHOP_TRUE_EDGE_MIN),
  tiers 1-2, markets 1x2/ou25/ou35; runs at 04:00 morning + shadow interval :10/:40 (24/7).
- Spend ceilings Path A: MAX_BETS_PER_DAY=80, MAX_STAKE_PER_DAY=800; per-match caps.

## Gaps for unified flow (from map §7)
1. Two placers / two sources / two edge instruments; only odds floor shared. Line-shop 3% never
   walk-forward validated (COOLBET-REALMONEY-EDGE-GATE-RECONCILE).
2. Split dedup/placement-of-record state: user_pick_marks vs simulated_bets.user_*_at — neither
   placer cross-reads. Need ONE placement-of-record table.
3. Duplicate risk Path A: already_placed keys on pick UUID but shadow_bets_unique emits multiple
   UUIDs/logical pick (~45% dupes 2026-08-31); only exposure_conflict vs real_bets saves it.
4. No ticket-level readback — balance-delta only; overlapping placements can fool it.
5. Kill switch fails open + checked deep, not at load; no single observable "is real-money
   placement possible right now" (launchd load state not surfaced by status.py).
6. Doc drift maintained by hand — should be generated from code.

## STRATEGY backtest (in progress)
- Universe: 5799 finished fixtures with Coolbet 1x2 price + model prediction (+ Pinnacle).
- Line-shop realized: bot_coolbet_value_v1 shadow settled n=3019. Model-edge realized:
  calibrated simulated_bets n=571.
- STRUCTURAL LIMIT: line-shop can only be tested where Coolbet priced (~6k fixtures for 1x2),
  NOT 104k. Model-edge reaches 104k. Head-to-head must be on the shared Coolbet-covered universe.

## STRATEGY RESULT (2026-09-08) — line-shop vs model-edge, realized, at executable price
| Market | Line-shop (bot_coolbet_value_v1) | Model-edge (calibrated) | Winner |
|---|---|---|---|
| 1x2 | +13.4% n=1910 CLV+4.6 (Aug+22→Sep+4, decaying) | +12.2% n=367 CLV+7.1 (Aug+29/Sep+26) | ~tie, model-edge higher CLV |
| O/U | **−17.0% n=1109 CLV+4.2** (neg every fold+month: Aug−24/Sep−10) | **+15.0% n=204 CLV+5.8 ✓robust** (pos every month +2..+43) | MODEL-EDGE decisively |

Findings:
- Line-shop O/U is a live money leak: −17%, negative every fold AND every month, n~1100, ~37% of its volume. HIGH confidence.
- Line-shop 1x2 positive (+13%) but decaying (Aug+22→Sep+4). Watch.
- Model-edge O/U positive every month, fold-robust. Model-edge 1x2 higher CLV.
- SCALE: line-shop capped at ~3-6k (Coolbet coverage), cannot reach 104k. Model-edge validated
  to 104k/182k (2D-gate idealized, fold-robust +14% 1x2). Model-edge holds at every scale.

Recommendation: unified real-money flow should place on MODEL-EDGE + validated per-market gates
(1x2 ≥13%/≥2.8, O/U ≥8%/≥1.8), NOT line-shop 3%. URGENT high-confidence sub-finding: line-shop
must STOP betting O/U (−17%, every month). Tradeoff = fewer bets (quality cohort). Validate the
exact switch out-of-sample before flipping real money (15%-overfit discipline; model-edge per-month
O/U n=15-47, strength is consistency not any window; calibrated cohort has some selection favorability).

## OPERATIONAL STATE (2026-09-08 ~16:40 UTC) — Coolbet brought back up
- Imperva lifted (11:44 log: cookie refresh + live event match OK). FlareSolverr up.
- CDP Chrome relaunched (:9222), operator auto-logged-in via --cdp-auto-login, JWT refreshed
  (fresh, ~30m TTL, kept alive by mac-daemon CDP sync). session_healthy=✓, placing enabled.
- launchd loaded: coolbet-odds-snapshot, coolbet-feed-watchdog, coolbet-mac-daemon (paper),
  coolbet-ui-placer (REAL, hourly, --execute).
- SHIPPED COOLBET-LINESHOP-OU-STOP (369fea8): UI placer no longer places line-shop O/U
  (−17% leak); places 1x2 line-shop only. Dry-run confirmed the stop fires + all gates work.
- Double-bet safety: the 2 manual O/U bets (Smouha over2.5, Gala under2.5) — bot_coolbet_value_v1
  has 0 pending picks on those matches, AND O/U placement is stopped → no double possible.

## MODEL-EDGE O/U — VALIDATED for real-money placement (2026-09-08)
Calibrated cohort (what /picks + paper placer use), gate edge>=8% & odds>=1.8:
  n=170, ROI +19.0%, CLV +5.75, fold-robust (+14/+7/+41), monthly +14/+13/−1/+5/+43, ~4.1/day.
Each 1D component also robust. This is the profitable O/U path to wire into the UI placer.

## NEXT BUILD (unified placer, #3): add model-edge O/U as a real-money source in the UI placer
- Load calibrated simulated_bets O/U picks (pending, prematch, edge>=8%, not already placed).
- Feed same placement loop: live Coolbet re-price → re-check edge=cal_prob−1/coolbet_odds >= 0.08
  → odds floor _min_odds_for('o/u')=1.8 → exposure/dedup → place.
- Gate behind env flag OFF by default (e.g. COOLBET_UI_MODEL_EDGE_OU=1); build + smoke + DRY-RUN
  shown to owner BEFORE first real execute. Then 1x2 line-shop + model-edge O/U both live.
- End-state (epic): model-edge for BOTH markets through the UI placer + one placement-of-record.

## STRATEGIC FRAMING (owner, 2026-09-08) — how PICKS and OWN relate
Two directions, and the data says how they merge:
- 👥 PICKS (customers): model-edge ONLY (honest/defensible). "More markets, better picks."
- 🤖 OWN (Coolbet, soon Unibet — ONLY Estonia-automatable books): whatever makes money AT that book.
Two-layer model:
  - MODEL-EDGE (shared by PICKS + OWN): our model vs market. Customers get this; we bet this.
  - LINE-SHOP (OWN-ONLY): Coolbet's own price beats the sharp (Pinnacle). Bookmaker-specific,
    model-free by design — never a customer pick. This is why "Coolbet doesn't use the model
    in some cases": the line-shop layer is intentionally model-free.
Per-market merge (from our backtest):
  - O/U: model-edge wins (+19% vs line-shop −17%) → OWN uses model-edge O/U (building). Line-shop O/U retired.
  - 1x2: ~tie (~+13%), line-shop ~10x volume → run BOTH or converge on model-edge (higher CLV). OPEN CALL.
  - New markets: whichever validates for that book (same discipline).
Unified OWN placer = model-edge core (shared w/ /picks) + line-shop overlay where Coolbet mis-prices.
"Same but don't have to be" = shared model-edge core; OWN adds the line-shop overlay.

## SHADOW-BOT-CONSOLIDATION (queue under this epic)
~40 bots is messy but partly diagnosed. Audit ALL bots → retire dupes + dead-market, keep distinct,
tag each by LAYER (customer model-edge / OWN line-shop / retired). Grounded in:
- SHADOW-BOT-PROMOTION-REVIEW (done 2026-09-03): promote nothing yet; 4 line-shop/pin bots genuinely
  distinct (0-5% v10 overlap).
- DUPLICATE-BOTS-REVIEW (filed): bot_opt_home_lower 92% + bot_conservative 91% dupes of v10 → retire.
- Retired-market bots (BTTS/DC/combo/acca) still clutter the list.
Close each bot with evidence (retire/keep/relabel), map to the two-layer framework.

## QUEUED 2026-09-08 (owner-requested; formalize in PRIORITY_QUEUE after control-panel build lands)
2. STOP-LINESHOP-OU-GENERATION — stop bot_coolbet_value_v1 generating O/U shadow picks (placement already
   stopped; generation is now pure page-noise since model-edge O/U won). Cleans the shadow-bots overlap.
3. SHADOW-BOT-CONSOLIDATION — audit all ~40 bots: hide/retire dead (BTTS/DC/draws/tier4), tag survivors by
   layer (OWN line-shop / PICKS model-edge / retired). Grounded in SHADOW-BOT-PROMOTION-REVIEW + DUPLICATE-BOTS-REVIEW.
4. COOLBET-1X2-MODEL-EDGE-2ND-BOT — evaluate adding model-edge 1x2 as a 2nd Coolbet OWN bot. Overlap with
   line-shop 1x2 is only 2% (8 picks), both ~+14% → complementary coverage, not redundant. Mirror like the OU bot.
+ PROMOTION-GATE: do NOT promote any shadow bot to Telegram/customers without explicit owner sign-off. Owner decides.
+ PERFORMANCE-BOT-AUDIT — audit the bots feeding /performance (calibrated+beta+active cohort): what beta/
  underperforming bots are in it, retire or hold. Note: track-record is CORRECT/live (verified DB=API=page=791/691/
  10.7%/€739 on 2026-09-08) — NOT frozen; it looks static only because volume is ~2-6 settled picks/day on a
  691-bet/€6910 base. Real issue = low public-pick VOLUME (MARKET-EXPANSION), not a caching bug.

## FUTURE ARCHITECTURE VISION (owner, 2026-09-08) — market × book matrix
End state when Unibet (and more) join: ONE bot PER MARKET (e.g. "1x2 bot", "O/U bot") with its
own rules, and each market has a set of BOOKS it's enabled on (Coolbet, Unibet, ...). Operator
configures which market-bots are active on which books. Some markets exist on only one book
(not supported on both), so enablement is per (market, book) cell — a matrix, not a flat per-bot list.
Implication for the control model:
- Today's `coolbet_placer_bots` (per-bot on/off) is the v1. Evolve to per-(market, book) enablement.
- Current bots are Coolbet-specific (bot_coolbet_value_v1 = 1x2 line-shop @ Coolbet;
  bot_coolbet_ou_model_v1 = O/U model-edge @ Coolbet). Future: a market bot places on N books.
- The control panel should present MARKET-first with the BOOK shown (Coolbet now), so a Unibet
  column/toggle slots in later without a redesign.
- Line-shop is inherently per-book (it exploits THAT book's mispricing); model-edge is book-agnostic
  signal placed at whichever book(s) are enabled + clear the gates. Keep that distinction in the matrix.
NOTE: Unibet UI placer does not exist yet (UNIBET-KAMBI odds ingest exists; no placer). This is the
target to build toward, not now.
