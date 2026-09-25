Parent: [[#152]] MODEL-BOTS-ON-NEW-MODELS-2026-09-25

# #152 — existing model bots on the new models (plan + pre-registration)

Owner (2026-09-25): make the existing MODEL bots better with the new 1X2 model (NEW+, `r1x2_comb_v1`) and the new O/U
model (`ou_comb_v1`, served = Pinnacle where priced else combined), un-retire `bot_v10_ou` on the O/U model, and find a
configuration per bot where it gets better. Two VIP bots already exist (1X2 NEW+ EV5, O/U EARLY) and must stay the best.

## Bots in scope and their identity (kept fixed — a config search may not change what the bot IS)
| bot | market / scope kept fixed | visibility | old probability |
|---|---|---|---|
| `bot_v10_1x2` | 1X2, all leagues, all selections | PUBLIC (/picks, calibrated) | old ensemble → Platt(Pinnacle) |
| `bot_high_roi_global_v2` | 1X2 home/away, Spain/Australia/Iceland, odds 1.50–5.50 | PUBLIC (beta) | old ensemble |
| `bot_coolbet_1x2_model_v1` | 1X2 HOME-UNDERDOG at Coolbet's own price, odds ≥ 2.80 | experimental; real-money-CAPABLE (paused, not armed) | old ensemble |
| `bot_unified_gate_1x2_paper_v1` | 1X2 all selections, odds ≥ 2.80, both placeable books | instrument (paper) | old ensemble |
| `bot_coolbet_ou_model_v1` | O/U 2.5 at Coolbet's own price, odds ≥ 1.80 | experimental; real-money-CAPABLE (toggled off 09-13) | old ensemble O/U |
| `bot_ou35_model_v1` | O/U 3.5 at Coolbet's own price, odds ≥ 1.80 | paper | own isotonic on raw over35 |
| `bot_v10_ou` (un-retire) | O/U 2.5 (+ 1.5/3.5 allowed as config), all books | PUBLIC "TESTING" once live | old ensemble O/U |

## Pre-registration — step 3 (2026-09-25, BEFORE any run)
**Window** kickoffs 2026-08-31..2026-09-24 at OPEN prices (earliest pre-kickoff quote per book, `timestamp < kickoff`), as
backtests B / B3. **Select half** 08-31..09-12, **confirm half** 09-13..09-24 (one look). **Metric:** mean CLV vs Pinnacle's
power-de-vigged last pre-kickoff price on the same market and line (ANALYSIS_GOTCHAS #83: real closes exist in this window).
ROI reported, never the decision variable. Probabilities: NEW+ walk-forward OPEN (as B/B2), combined O/U OPEN (as O1),
old = the stored pre-kickoff `predictions` ensemble (the #065 XGB un-swap applied, as B3).
**Per-bot family (≤ 9 configs, identity above held fixed):** model ∈ {old, new} × edge unit ∈ {bot's own pp rule, EV}
× threshold ∈ {bot's own, one step lower, one step higher} (pp steps ±2pp; EV ∈ {3%, 5%, 8%}); every other bot gate as
live. **Public-bot constraint** (owner's VIP split, 2026-09-25): the three PUBLIC bots may not take a pick the VIP bot of
their market holds — 1X2: NEW+ EV ≥ 5% at the same (match, selection); O/U: EV vs Pinnacle 5–15% with the quote ≥ 12 h
before kickoff.
**Procedure:** on the select half pick the config with the best mean CLV among those with ≥ 20 CLV picks; score it ONCE on
the confirm half against the bot's CURRENT config (old model, own rule) on the same half: one-sided bootstrap of the
difference in mean CLV (10k resamples), **Holm m = 7 (bots)**. **SWITCH** a bot (new rule_version, history before the switch
kept and labelled) only if adj p < 0.05 AND the new config's confirm CLV > 0. Otherwise: no switch, reason written down.
`bot_v10_ou` is un-retired on its best new-model config regardless (owner decision), but published as TESTING and its
backtest shown only if it passes.
**Expected:** old configs ≈ 0 or negative CLV everywhere (B3: baseline negative in 95% of configs); the new model helps the
bots whose rule is an EV/edge rule with room below the VIP band; the public-bot constraint may leave v10_1x2 and v10_ou
with little or no edge — in which case that is the answer, not a reason to loosen the constraint.

## RESULTS — step 3 (2026-09-25 morning; `scripts/backtest_model_bots_new_models.py`, 12 configs/bot)
| bot | current, confirm half | best new-model config, confirm half | pre-reg verdict |
|---|---|---|---|
| bot_v10_1x2 | n 10, CLV −0.1% | NEW+ EV ≥ 3%, VIP-excluded: n 370, +1.0% | no switch (Holm 1.0) |
| bot_high_roi_global_v2 | n 1 | nothing better (−4.6%) | no switch |
| bot_coolbet_1x2_model_v1 | n 312, −10.7% | NEW+ EV ≥ 5%: n 24 (9 priced), +12.8% (median +0.9%) | switch — fragile |
| bot_unified_gate_1x2_paper_v1 | n 704, −14.2% | NEW+ EV ≥ 8%: n 82, −1.0% | no switch (still < 0) |
| bot_coolbet_ou_model_v1 | n 4, −13.7% | new O/U: n 30 (6 priced), +18% | switch — all 6 are Coolbet quotes > 1.25× Pinnacle |
| bot_ou35_model_v1 | n 169, −8.0% | new O/U: n 32 (5 priced), +23.6% | switch — same suspect quotes |
| bot_v10_ou | n 0 | new O/U EV ≥ 3%: 21 picks, +1.6% | < 20 priced picks |
**Owner-approved switch of bot_v10_1x2 was REVERTED before commit (2026-09-25):** its LIVE record (bot_ledger, match-day
prices, real Pinnacle closes) is CLV +6.6% Jul (n 40), +6.0% Aug (82), +1.8% Sep (56) — ≈ +4.7% on 178 picks — while this
backtest priced its old rule at OPENING quotes (10 picks). A working live bot outranks a 10-pick open-price backtest; the
NEW+ version should run beside it as a twin and be compared live. See ANALYSIS_GOTCHAS #84.
**The three formal "switch" verdicts are not acted on:** 5–9 priced picks each, and the two O/U ones are Coolbet pricing
errors that the live Coolbet bots have no guard against — a separate bug (Coolbet price-sanity guard), to be fixed first.

## Pre-registration — LANES: odds split between the two NEW+ public twins (2026-09-25, BEFORE the run)
Owner: give the Match-result twin and the High-odds twin their own odds lanes so they do not publish the same picks,
and find a split where BOTH stay profitable. Both use NEW+ (`r1x2_comb_v1`, walk-forward OPEN, as B2/B3), Pinnacle price
required, `vip_exclude` (never a pick the VIP bot holds: NEW+ EV ≥ 5% same match+selection), one pick per match per bot.
* **Match-result twin:** EV ≥ 3%, all selections, odds 1.30 – s (s exclusive).
* **High-odds twin:** EV ≥ 2%, home/away only, odds s – 6.00, all leagues.
* **Split s ∈ {2.00, 2.30, 2.50, 2.80, 3.00}** (5 candidates).
**Select** on kickoffs 08-31..09-12: s = argmax of min(CLV_match, CLV_high) among s where both have ≥ 30 CLV picks.
**Confirm ONCE** on 09-13..09-24 at that s: each twin's mean CLV vs Pinnacle's power-de-vigged close, one-sided bootstrap
p (10k), Holm m = 2 (the two twins). **PASS** = both adj p < 0.05. Report per s and half: n, CLV [CI], ROI [CI], picks/day,
remaining overlap between the twins (should be 0 by construction) and with VIP (0 by construction), plus the no-split
reference (Match twin 1.30–4.50, High-odds twin 1.60–6.00) with its overlap count. Same window caveat as B2/B3: seen
before, so the forward record decides.
