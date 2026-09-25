Parent rows: [[#141]] [[#148]] [[#149]] [[#152]] [[#155]] [[#156]] [[#157]] [[#158]] [[#159]] [[#160]] [[#161]] (PRIORITY_QUEUE.md)

# HANDOVER — everything bot-related from the 2026-09-24/25 session (read before any bot refactor)

Written for the agent that will run the big bot refactor/clean-up. It records what exists, WHY, the owner's
decisions and the rules that came out of two days of work. Numbers are as of 2026-09-25. Verify against the
code/DB before relying on any of them (CLAUDE.md "verify, don't trust doc status").

---

## 1. Models in production (probability sources)

| Name | Market | What it is | Where | Accuracy (holdout 08-31..09-24) |
|---|---|---|---|---|
| old ensemble (`predictions` source='ensemble', v2026MMDD bundles) | 1X2, O/U | Poisson + XGBoost blend; weekly retrain still runs | `daily_pipeline_v2.py`, `xgboost_ensemble.py` | 1X2 1.071–1.144 (worse than uniform); O/U worse than base rate on 1.5/2.5/3.5 |
| **NEW** `r1x2_d8plus_v1` | 1X2 | walk-forward ratings (Elo / pi / dynamic Poisson) | `workers/model/ratings_1x2.py`, `rating_1x2_predictions` | ~1.005 |
| **NEW+** `r1x2_comb_v1` | 1X2 | ratings + de-vigged 18-book consensus + Pinnacle, one logit per availability group | `workers/model/combined_1x2.py`, `market_consensus_1x2.py` | **0.976**; matches/edges Pinnacle |
| **O/U combined** `ou_comb_v1` | O/U 1.5/2.5/3.5 | same recipe (Poisson on λ_h+λ_a + power-de-vigged consensus + Pinnacle); SERVED p = Pinnacle where priced, else combined | `workers/model/combined_ou.py`, `ou_model_predictions` | 0.566/0.674/0.643 vs old 0.593/0.709/0.696; Pinnacle alone 0.561/0.673/0.642 |

Both new models refit twice daily inside `job_rating_1x2_shadow` (05:30/17:30) and re-price every 30 min
(`job_combined_1x2_refresh` :10/:40). Version names are DESIGN versions (v1 = this recipe), not dates.
⚠️ NEW+'s consensus still de-vigs PROPORTIONALLY (`market_consensus_1x2.py:12,74`) — known defect, [[#154]].

## 2. Bots created or changed in this session

| Bot | What | Status / visibility | Evidence |
|---|---|---|---|
| `bot_combined_1x2_ev5_v1` — ⭐ **VIP #1 "1x2 NEW+ EV5"** | NEW+, EV = p×odds−1 ≥ 5% flat, Pinnacle required, odds 1.30–6.00, one pick/match | VIP (`bots.vip`, `VIP_BOTS`), live picks → Pro/Elite DM + private channel; public settled-only | backtest B2 CLV +2.0% (n 1,050) |
| `bot_combined_1x2_ev8_v1` "1x2 NEW+ EV8" | EV ≥ 8% subset of EV5 | unlisted measuring bot, `hide_pending` | B2 +3.1% |
| `bot_ou_sharp_early_v1` — ⭐ **VIP #2 "O/U EARLY"** | soft book beats Pinnacle's power-de-vigged O/U price by EV 5–15%, quote ≥ 12 h before KO; NO model | VIP; job `workers/jobs/ou_sharp_outlier.py` :14/:44 | O3 T3 CLV +7.5%/+6.9%, ROI +10.4/+10.8 |
| `bot_ou_sharp_2anchor_v1` "O/U TWO-ANCHOR" | same, must also beat other-book consensus by ≥ 2% | experimental, `hide_pending` (shares VIP #2's picks) | O3 T2 +6.6%/+4.2% |
| `bot_v10_1x2_newplus_v1` "Match result — new model" | twin of `bot_v10_1x2`: NEW+ EV ≥ 3%, **odds 1.30–3.00** (LANES); VIP-held picks held back until kickoff ([[#164]]) | testing, `show_on_performance`, `show_on_picks` (migration 432) | LANES confirm CLV +2.66% (n 157) |
| `bot_rating_1x2_v1` "NEW", `bot_combined_1x2_v1` "NEW+" | twins of the OLD v10 rule on the new models | experimental | old rules starve on accurate models (NEW+ twin 14 picks) |
| `bot_high_roi_global_v2_newplus_v1` | created and **retired** same day (migration 429) | retired | its exact rule made 0 backtest picks |
| `bot_v10_1x2`, `bot_high_roi_global_v2` | **deliberately NOT switched** to the new model | CALIBRATED / BETA; both send picks | live CLV ≈ +4.7% Jul–Sep (v10) — ANALYSIS_GOTCHAS #84 |
| `bot_unified_gate_1x2_paper_v1`, `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`, `bot_ou35_model_v1` | NOT switched ([[#152]] step 3): fragile passes; the two Coolbet O/U "edges" were Coolbet pricing errors | unchanged | → [[#160]] guard first |
| forward-test arms `bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_b/c/d_v1` | reviewed 2026-09-25: keep 4 as TESTING, D → EXPERIMENTAL; two recorded-only twin arms → [[#161]] | see [[#155]] | sharp-anchor CLV +2.4 / +0.7 / B n 3 / C −0.05 (n 34) / D −5.4 |

Pipeline plumbing added (`daily_pipeline_v2.py`): `prob_source` (rating_1x2 / combined_1x2), `ou_prob_source='combined_ou'`,
`edge_unit='ev'`, `require_pinnacle`, `one_per_match`, ~~`vip_exclude`~~ (removed 2026-09-25, [[#164]] — replaced by the VIP-FIRST hold-back in `store_bet`). The shared
`pred` is restored before the next bot on the match (`_pred_orig`). Shadow-model rows now use sources `ensemble_shadow` /
`xgboost_shadow` ([[#147]], migration 419) — most readers never filtered model_version.

## 3. Owner decisions and rules (these are policy, not suggestions)

1. **Lifecycle statuses = distribution (PICKS track)** ([[#155]]): EXPERIMENTAL admin-only, nothing sent · TESTING on
   /performance, picks SENT, counted in own record, not in headline · BETA / CALIBRATED sent + headline. **⭐ VIP is a CHANNEL**
   on top of any status (Pro/Elite + private channel live; public settled-only) → "VIP · TESTING" until earned.
   Real money is NOT a status — it is the per-bot switch on /admin/bots.
2. **Anything sent is counted** in the bot's own public record; never publish without keeping score.
3. **Retirement = a FLAG, not automatic**: at n ≥ 50 settled with sharp-anchor CLV CI entirely < 0 → "review this bot" flag
   (admin attention inbox + /admin/bots); owner decides. Retired picks keep counting in the totals (#157).
4. **Change a LIVE bot only via a twin** (ANALYSIS_GOTCHAS #84): check its live `bot_ledger` CLV by month first; an open-price
   backtest is not the bot you run.
5. **Fix root causes** (memory `feedback_fix_root_causes`): any number fix = one shared computation + parity test + deprecate
   the wrong source + fix the writer.
6. **VIP FIRST** ([[#164]], owner 2026-09-25 — replaces the old "VIP split" / `vip_exclude`): VIP bots never give up a
   pick. FREE bots still RECORD a pick that is VIP-HELD (a VIP / hide_pending bot has it pending — read from the
   ledger) or IN VIP'S RANGE at their price and decision time (1X2 NEW+ EV ≥ 5%; O/U 5–15% above Pinnacle, ≥ 12 h
   out), but it is HELD BACK — not shown, not sent, not pending anywhere public — until kickoff. One module
   (`workers/utils/vip_guard.py`), applied by the writers (`store_bet`, forward-test `claim`, the board), stored as
   `held_back_until`; every surface filters that column (migration 439). `vip_exclude` (which SKIPPED the pick on a
   re-derived rule and leaked after price moves) is gone. VIP twins that share picks keep `hide_pending`.
7. **Re-checked older-rule picks** ([[#158]]): a bot's record = current-rule picks + older picks that pass the current rule
   on PICK-TIME data only (table `pick_rule_recheck`); the pre-registered test counts are not changed.
8. **/performance design**: bot column = name + status + method labels only; details in the click-open view (opening to
   everyone in [[#159]]); no ROI-based "proven/underperforming" judgements beside the status labels; headline later split
   VIP vs everything else; "work done" totals = 16,681 picks across 94 strategies, 68 retired ([[#157]]).

9. **FLAT STAKES EVERYWHERE** (owner 2026-09-25): every bot records one flat unit per pick, real-money sizing included; Kelly
   is not used until it proves itself. `kelly_fraction` stays stored as data only. All our backtests were flat and CLV-decided.

## 4. Definitions (the traps we fell into)

* **CLV = sharp-anchor close**: `leg_clv_sharp.clv_sharp` (fresh Pinnacle close) else `clv_cons` (≥ 5-book consensus); thin
  consensus separate; |clv| > 1 = data fault. The legacy `simulated_bets.clv` / `clv_pinnacle` has no close-age limit.
* **Own-book-close CLV is negative BY CONSTRUCTION for outlier strategies** (ANALYSIS_GOTCHAS #85) — it scored the live arm
  like the random control. The forward test's stop rule was amended to sharp-anchor CLV vs the junk control ([[#156]]).
* **No Pinnacle O/U close before mid-July** (ANALYSIS_GOTCHAS #83) — pre-July "CLV vs close" is circular.
* **ROI price bases**: `odds_at_pick` = MAX high-water over the fixture's history (inflated, §55); `odds_at_pick_live` =
  best of our 4 ESTONIAN books at pick time (the OWN basis); PUBLIC basis = "available at pick time" across ALL publishable
  books — being built in [[#159]], flat stake.
* The 1X2 served model was **home/away-swapped 05-10..09-14** ([[#065]]) — most of `bot_v10_1x2`'s record comes from then.
* O/U calibration bug window 09-03 10:49 .. 09-13 21:00 UTC.

## 5. Open work (see each PRIORITY_QUEUE row)

[[#159]] one ROI/CLV definition (in progress) · [[#157]] retired bots + totals · [[#155]] status rollout + retirement flag ·
[[#161]] twin arms (in progress) · [[#160]] Coolbet price-sanity guard · [[#153]] admin models page · [[#154]] model inputs
round 2 (both markets) · [[#152]] bot_v10_ou un-retire on `ou_comb_v1` (public TESTING vs admin-only still open).

## 6. Where the evidence lives

`dev/active/1x2-model-rebuild-plan.md` (rounds 1–3c, B, B2, B3, B4, B5) · `dev/active/market2-model-plan.md` (O1–O3) ·
`dev/active/model-bots-new-models-plan.md` (#152 step 3, LANES) · `dev/active/picks-forward-test-preregistration.md`
(Amendment 1, #158 note) · ANALYSIS_GOTCHAS #82–#85 · research outputs (gitignored) under `data/models/_research/`.
