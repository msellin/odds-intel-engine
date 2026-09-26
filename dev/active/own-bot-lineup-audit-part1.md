Parent: PRIORITY_QUEUE.md #191 OWN-BOT-LINEUP-AND-ADMIN-BOTS-CLEANUP-2026-09-26 (sub-item (a), audit part 1: triggers / generators / OWN)

# #191 audit, part 1: sharp triggers, sharp generators and the OWN bot (2026-09-26)

Read-only audit. Everything below comes from the code (`workers/automation/sharp_engine.py`,
`workers/jobs/pick_trigger_matcher.py`, `pick_triggers.py`, `ou_sharp_outlier.py`, `own_bots.py`,
`own_bet_board.py`, `scripts/publish_picks_forward_test.py`) and the DB (`bot_config`, `bot_performance`,
`bot_ledger` ⋈ `leg_clv_sharp`, `bot_distribution`, `coolbet_placer_bots`), taken 2026-09-26 ~20:30 UTC.

## How to read the numbers

- **n** = settled legs (won/lost, pre-match). Every CI is a bootstrap over **matches** (5,000 resamples, seed 191).
- **ROI** = flat 1 unit at the OWN price (`bot_ledger.pnl_unit_own`: our 4 books at pick time; the /admin/bots figure).
- **CLV vs Pinnacle** = `bot_performance.clv_own` (the admin page). For every bot here except the 2-anchor and aligned
  ones it is **circular** (§85): the trigger is "beats Pinnacle by ≥ 3%", so an unmoved Pinnacle returns the trigger edge.
- **Indep. CLV** = `odds_own × p_close_cons − 1` where `cons_status='ok'` (≥ 5 books, Pinnacle and own book excluded).
  `odds_own` is the pick-time snapshot price for pre-W2.1 legs (migration 467). The **clean** figure drops the
  `own_basis='our_books_unverified'` legs (no pick-time snapshot survived, so their price may be the rewritten one).
  Coverage is only 35–45% of legs (the consensus close needs ≥ 5 books).
- **Est.** = #172 re-price (`scripts/analysis/own_track_estonian_reprice.py`, re-run 2026-09-26 evening):
  best Coolbet / Unibet-Site / Epicbet / Tonybet quote at pick time (≤ 180 min), judged on independent CLV; ROI flat.
- **Overlap** = share of the bot's (match, market, selection) keys also picked by another active bot, window since
  2026-09-22 (when `bot_trigger_1x2_sharp_v1` started firing correctly, #007). `*` = a /performance bot.

## Verdict table

| Bot | What it really is | Current? | n | ROI (own) | CLV vs Pin (circular) | Indep. CLV clean [95% CI] | Est. re-price: indep. CLV / ROI | Overlap | Proposed verdict |
|---|---|---|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | 1X2 · SHARP · Pinnacle-only (Shin) · Coolbet only · pp ≥ 3%, ≤ 8% ceiling, quote ≤ 60 min · `shadow_bets` · not published · "placeable" but switch OFF | yes, 84 picks/7 d; **no #182 guards** | 299 | −5.2% [−20.6, +10.7] | +8.5% (n 271) | **+2.7% [−0.5, +5.7]** n 96 · last 3 h **+6.5% [+4.0, +9.1] n 57** · > 3 h −0.3% n 51 | +1.6% [−1.1, +4.1] n 102 / −6.5% n 257 | 97% inside `bot_trigger_1x2_sharp_v1` | **FOLD** into OWN 1X2 · SHARP (`bot_own_1x2_v1`) |
| `bot_coolbet_trigger_sharp_ou_v1` | O/U 2.5 · SHARP · Pinnacle-only (power) · Coolbet only · same gates | yes, 17/7 d; no #182 guards | 78 | −11.2% [−35.4, +13.2] | +4.0% (n 76) | **−3.6% [−9.6, +2.1]** n 24 | −2.2% [−7.3, +2.6] n 29 / −25.1% n 60 | 100% inside `bot_trigger_ou_sharp_v1` | **RETIRE** (history seeds a future OWN O/U · SHARP) |
| `bot_unibet_trigger_sharp_1x2_v1` | 1X2 · SHARP · Pinnacle-only · Unibet-Site only · same gates | yes, 89/7 d; no #182 guards | 275 | +3.3% [−14.0, +21.2] | +6.3% (n 264) | **+2.6% [−0.7, +5.8]** n 108 · last 3 h **+7.1% [+3.7, +10.5] n 69** · > 3 h +1.8% n 58 | +2.6% [−0.5, +5.6] n 122 / −3.7% n 233 | 96% inside `bot_trigger_1x2_sharp_v1` | **FOLD** into OWN 1X2 · SHARP (`bot_own_1x2_v1`) |
| `bot_unibet_trigger_sharp_ou_v1` | O/U 2.5 · SHARP · Pinnacle-only (power) · Unibet-Site only | yes, 12/7 d; no #182 guards | 61 | −19.8% [−42.1, +2.9] | +4.1% (n 59) | **+4.4% [+0.1, +9.2]** n 18 | +2.7% [−1.0, +6.7] n 25 / −27.4% n 53 | 88% inside `bot_trigger_ou_sharp_v1` | **FOLD** into a new OWN O/U · SHARP (the only O/U trigger with a positive independent sign; n too small to stand alone) |
| `bot_trigger_1x2_sharp_v1` | 1X2 · SHARP · Pinnacle-only · best of Coolbet + Unibet-Site · pp ≥ 3%, ≤ 8% · written per sweep by `pick_generator` → `sharp_engine` | yes, 128/7 d (record starts 09-22; 25 phantom picks voided 09-20); no #182 guards | 112 | +2.7% [−26.2, +33.1] | +5.6% (n 68) | **+1.1% [−3.1, +4.9]** n 62 · last 3 h **+8.1% [+4.7, +11.3] n 33** · > 3 h **−5.7% [−11.4, −0.4] n 35** | +1.9% [−2.1, +5.4] n 68 / +4.2% n 112 | 81% inside union of the two per-book 1X2 triggers | **FOLD** into `bot_own_1x2_v1` (it is the same bot minus the guards and minus Epicbet/Tonybet) |
| `bot_trigger_ou_sharp_v1` | O/U 2.5 · SHARP · Pinnacle-only (power) · best of Coolbet + Unibet-Site | yes, 24/7 d; no #182 guards | 23 | −30.4% [−72.8, +16.5] | +5.7% (n 18) | **−2.7% [−10.6, +3.3]** n 16 | +0.5% [−8.1, +8.2] n 16 / −27.8% n 23 | 83% inside union of the per-book O/U triggers | **FOLD** into a new OWN O/U · SHARP (same role as the 1X2 fold; alone it is too thin to judge) |
| `bot_trigger_1x2_sharp_tight_v1` | 1X2 · SHARP · Pinnacle-only · pooled Coolbet/Epicbet/Unibet-Site/Tonybet (61% Epicbet) · pp ≥ 2%, odds ≤ 2.50, no ceiling · pre-registered INSTRUMENT | yes, 152/7 d; no #182 guards | 323 | +9.0% [−1.6, +19.2] | +0.8% (n 288) | **+1.2% [−0.1, +2.6]** n 206 · last 3 h +1.7% [+0.5, +3.0] n 86 | +1.3% [+0.1, +2.6] n 208 (Holm p 0.33) / +9.9% [−1.0, +20.5] n 318 | 37% shared with the other 1X2 triggers | **RETIRE** at its own pre-registered readout (margin-corrected own-book CLV **−3.7%**, n 272 of the 300 → the RETIRE cell is < −2%) |
| `bot_ou_sharp_2anchor_v1` | O/U 1.5/2.5/3.5 · SHARP + leave-one-out consensus ≥ 2% EV · power Pinnacle ≤ 3 h · **every publishable book** (Bet365, Betfair, BetVictor … as well as ours) · EV 5–15%, odds 1.30–6.00 · `simulated_bets` · not published, not placeable | yes, 99 picks since 09-24; Pinnacle-only anchor (not v2) | 84 | +6.4% [−16.6, +31.1] | +0.3% (n 34) | at our-books price **−0.4% [−3.9, +3.4]** n 33 · at recorded (global) price +3.0% [−0.3, +6.4] n 33 | −0.6% [−4.3, +3.6] n 32 / +5.6% n 82 | 26% shared with /performance O/U bots (19% with O/U EARLY*) | **KEEP as PICKS test** (value, if any, is a global-book price; at Estonian books it is zero) |
| `bot_sharp_aligned_v1` | 1X2 + O/U 2.5 · SHARP (Shin Pinnacle) · forward-test v4 + own-book quote within 5 min of the Pinnacle **fetch** · `picks_forward_test` arm `sharp_own_book_aligned` · recorded, never sent | yes, 14 picks since 09-25 | 7 | −1.3% (n 7) | −3.3% (n 5) | **−2.5% [−5.3, −0.7]** n 5 | −5.4% n 5 / −4.3% n 7 | **100% inside `bot_sharp_1x2_v1`* + `bot_sharp_ou_v1`*** | **RETIRE** (its gate aligns on OUR fetch time, which §88 shows is not quote liveness; it is a strict subset of the published picks) |
| `bot_own_1x2_v1` | 1X2 · SHARP · **v2 anchor** (Pinnacle + exchange, else consensus without the book) · best of Coolbet/Unibet-Site/Epicbet/Tonybet · EV ≥ 3%, ≤ 8% · < 3 h to KO · ≥ 3 confirming books · power-de-vig robustness · no market split · `shadow_bets` cohort `own` · not published, no placement row | yes (live since 09-26, every 10 min); **has all #182 guards** | 0 (1 pending: Coolbet away 2.75, 20:13) | — | — | — | — | — | **KEEP** — this is the OWN 1X2 · SHARP bot the folds go into |

## Cross-cutting findings

1. **Every trigger bot is Pinnacle-only.** `sharp_engine.ANCHOR_BOOK = "Pinnacle"`; the engine never reads the exchange,
   has no market-split guard, no power-de-vig robustness check and no time-to-kick-off window. Only `bot_own_1x2_v1`
   (via `own_bet_board.build` + `anchors_for_books`) carries the #182 guards and the v2 anchor. The per-book triggers
   are therefore exactly the population the #182 filter study found wanting before its guards were added.
2. **The last-3 h split reproduces on every 1X2 trigger's own history**, independently of the study that set the rule:
   Coolbet +6.5% (n 57) vs −0.3% (n 51); Unibet +7.1% (n 69) vs +1.8% (n 58); book-agnostic +8.1% (n 33) vs
   **−5.7% [−11.4, −0.4]** (n 35). This is not fully independent evidence: the #182 study drew on largely the
   same legs, and the within-3 h figures include `our_books_unverified` legs. It is consistent with the study,
   not a separate confirmation of it. It is the argument for folding the 1X2 triggers into `bot_own_1x2_v1` rather
   than keeping them beside it.
3. **Per-book bots duplicate the book-agnostic ones.** Since 09-22, 96–100% of each per-book trigger's picks are
   also a pick of `bot_trigger_1x2_sharp_v1` / `bot_trigger_ou_sharp_v1`. The book is a column
   (`recommended_bookmaker`), not a strategy. The registry already said so on 2026-09-11 (MERGE-TRIGGER-BOTS);
   the retirement it promised never happened.
4. **"Placeable" is misleading on all seven sharp-engine bots.** `bot_config.placeable = True`, but every one has
   `ui_place_enabled = false` in `coolbet_placer_bots`. The exported `placement_floor` is also an empty band
   (1X2 `edge_min` 0.10/0.13 with `edge_max` 0.08; O/U `edge_min` 0.08 = `edge_max`). No sharp trigger could place
   even if switched on. Worth fixing in the (d) page cleanup so the page stops showing them as real-money capable.
5. **O/U sharp has no working OWN form yet.** Across the four O/U triggers (n 23–78 each) independent CLV ranges from
   −3.6% to +4.4%, all CIs straddle zero, and flat ROI is −11% to −30%. `bot_ou_sharp_2anchor_v1`, at our books,
   is −0.4%. An OWN O/U · SHARP bot should be built on the `bot_own_1x2_v1` engine (v2 anchor, 3 h, guards)
   and pre-registered, not inherited from these.
6. **Pre-W2.1 price caveat is material.** Independent CLV at the RECORDED price is 1–2 pp higher than at the pick-time
   price on every per-book trigger: Coolbet 1X2 +4.6% vs +2.7%, Unibet 1X2 +6.5% vs +2.6%, tight +2.2% vs +1.2%.
   Always quote the pick-time or clean figure.
7. **Housekeeping:** `bot_own_1x2_v1` is missing from `scripts/export_bot_config.py`, so it has no `bot_config` row
   ("Settings unknown" on /admin/bots, the same gap `bot_ah_sharp_v1` had).

## Per-bot notes

**`bot_coolbet_trigger_sharp_1x2_v1`** is one book's slice of the book-agnostic 1X2 trigger (97% overlap). Clean
independent CLV is +2.7% (n 96), undetermined, and flat ROI is −5%. All of the positive signal sits in the last 3 h,
which is `bot_own_1x2_v1`'s rule. Keeping it adds a per-book view that `GROUP BY recommended_bookmaker` on the OWN
bot already gives. FOLD. Its history stays in `shadow_bets` as evidence.

**`bot_coolbet_trigger_sharp_ou_v1`**: independent CLV is negative (−3.6%, n 24 clean) and ROI is −11% (n 78). It is
fully contained in `bot_trigger_ou_sharp_v1`. RETIRE.

**`bot_unibet_trigger_sharp_1x2_v1`** has the best 1X2 trigger record: clean +2.6% (n 108), Estonian re-price +2.6%
(n 122, Holm p 0.93), last 3 h +7.1% (n 69). It is still undetermined and 96% contained in the book-agnostic bot.
FOLD into `bot_own_1x2_v1`, which already prices Unibet-Site.

**`bot_unibet_trigger_sharp_ou_v1`** is the only O/U trigger whose clean independent CLV has a CI above zero, but only
barely (+4.4% [+0.1, +9.2]) and on n 18, beside −20% ROI on n 61. The signal is not strong enough to keep a
single-book bot. FOLD into a future OWN O/U · SHARP bot as prior evidence.

**`bot_trigger_1x2_sharp_v1`** is the OWN 1X2 idea without the guards and without Epicbet/Tonybet. Its own history
gives the cleanest case for the 3 h rule (+8.1% within 3 h vs −5.7% before). FOLD. Keeping it beside
`bot_own_1x2_v1` would publish the same picks twice with two different records on /admin/bots.

**`bot_trigger_ou_sharp_v1`**: n 23, independent CLV −2.7% (n 16), ROI −30%. It contains the per-book O/U triggers.
FOLD, which in practice means retiring it once an OWN O/U bot exists.

**`bot_trigger_1x2_sharp_tight_v1`** is a pre-registered instrument (`dev/active/own-sharp-tight-preregistration.md`)
whose judge is margin-corrected own-book CLV at n ≥ 300. It now stands at **−3.7% on n 272** (323 settled): inside
the RETIRE cell (< −2%), about a week before the count qualifies. Caveat (§85): an own-book close is negative by
construction for an outlier-picker, so the pre-registered judge is weaker than it looked on 09-15. The
independent judge gives only +1.2% [−0.1, +2.6] (n 206), and +1.3% at Estonian books (Holm p 0.33). The
+9% ROI is the 12-day effect the pre-registration warned about. Only 37% of its picks are shared, because the
odds ≤ 2.50 band is its own. It is not a duplicate, but it answers no open question. RETIRE when the
pre-registered readout confirms.

**`bot_ou_sharp_2anchor_v1`** is not an OWN bot. It prices every publishable book (Bet365, Betfair, BetVictor are
among its picks) with a Pinnacle-only anchor. At our books its independent CLV is −0.4% (n 33). At its recorded
global price it is +3.0% [−0.3, +6.4]. Forward n is small (84 settled, clv_cons n 33) against a backtest of
+4.2–6.6%. It overlaps O/U EARLY (VIP) on only 19%. KEEP as a PICKS test (experimental, unpublished) until
clv_cons n ≈ 100. It should not be folded into OWN.

**`bot_sharp_aligned_v1`** is a strict subset of the published sharp picks (100% overlap). It records 14 picks in
1.5 days, with a pre-registered readout at n = 50/100. Its gate is "own-book quote ≤ 5 min from the Pinnacle
quote", and both timestamps are OUR fetch times. §88 / #186 show the AF-Pinnacle value is usually ~2 h old
whatever its fetch time, so the gate does not measure what the #156 audit hoped it would. The first 5 CLV legs
are −2.5%. RETIRE; or, if the owner prefers to honour the pre-registration, let it run to n = 50 (≈ 1 week) and
retire then.

**`bot_own_1x2_v1`** is the target of the 1X2 folds. It went live 2026-09-26 with one pending pick. It needs
`export_bot_config.py` coverage. Once the triggers are folded, the per-book record comes from
`recommended_bookmaker`.

## What the folds imply for the OWN line-up (input to (a3))

- **OWN 1X2 · SHARP** = `bot_own_1x2_v1` (exists). It absorbs 4 bots: coolbet/unibet 1X2 triggers,
  `bot_trigger_1x2_sharp_v1`, and in effect the tight instrument.
- **OWN O/U · SHARP** = does not exist. Build it on `own_bots.py` (v2 anchor, O/U market, the same guards), pre-registered.
  The unibet/coolbet/book-agnostic O/U triggers are its prior evidence, not its rule.
- Instruments surviving this part: none. The two instruments here (tight, aligned) both hit their own RETIRE condition or lost their premise.
