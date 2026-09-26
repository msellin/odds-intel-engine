"""SYSTEM-MAP-REGISTRY (2026-09-09) — the single source of truth for what every
active bot IS.

Why this file exists: the owner observed the betting system gets *harder* to
understand every time we touch bots / floors / anchors, because the facts about a
bot were scattered (bots table, the web SHADOW_BOTS list, coolbet_placer's
PLACEABLE_BOTS, the scheduler) with no one place required to stay true. This is
that one place. `docs/SYSTEM_MAP.md` is its human-readable form; the smoke test
`SYSTEM-MAP-REGISTRY-NOT-DRIFTED` fails CI if this file disagrees with the code or
the DB, so it cannot rot into fiction.

The one word that caused most of the confusion is "edge". There are TWO:
  * MODEL edge  = cal_prob − 1/book_odds   (anchor: our calibrated model)  floor 13%/8%
  * SHARP edge  = P_sharp  − 1/book_odds   (anchor: de-vigged Pinnacle)     floor ~3%
They are different quantities against different yardsticks — the floors are NOT
comparable across them. `anchor` on each spec says which one a bot uses.

Nothing here places bets or reads at runtime-critical paths; it is pure data +
lookups. Which bots CAN stake is not listed here or anywhere by name any more
(#139, owner decision 4, 2026-09-24): it is the code rule
placement_gate.placement_path_reason applied to the exported config, and which of
those MAY stake is the audited coolbet_placer_bots switch on /admin/bots. The
drift test asserts every `real_money=True` bot here has a placement path.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── anchor / family vocab ────────────────────────────────────────────────────
ANCHOR_MODEL = "model"   # fair value = our calibrated model probability
ANCHOR_SHARP = "sharp"   # fair value = Shin-de-vigged Pinnacle probability
ANCHOR_NONE = "none"     # no fair-value reference of its own — the in-play rig,
                         # which prices off the BOOK's own de-vigged probability.
                         # NOT "an internal bot": a league/odds filter over model
                         # picks is still MODEL-anchored (see bot_high_roi_global_v2,
                         # corrected 2026-09-22).
# ANCHOR_CONSENSUS (2026-09-22, [[#068]]): fair value = the de-vigged CONSENSUS
# of >=5 books, not one book's line. Its own constant rather than a flavour of
# ANCHOR_SHARP, because the distinction is load-bearing on two counts. (a) It is
# what a reader needs to know: "one sharp book says so" and "ten books agree" are
# different claims, and the /performance chip renders straight off this field.
# (b) It changes which guards apply — a consensus has ZERO overround by
# construction, so the anchor-quality test that gates the sharp arm is
# meaningless against it and had to be replaced by a book COUNT.
ANCHOR_CONSENSUS = "consensus"

FAM_COOLBET_REAL = "coolbet_real"      # places real money at Coolbet (gated)
FAM_TRIGGER = "trigger"                # book-agnostic trigger engine (paper)
FAM_COOLBET_PAPER = "coolbet_paper"    # Coolbet own-price paper bots
FAM_INTERNAL = "internal"              # internal model/strategy paper validators
FAM_FORWARD_TEST = "forward_test"      # pre-registered published-picks forward test
FAM_INPLAY = "inplay"                  # in-play slow-state rig (paper) — OWN Phase 1b

FAMILY_TITLES = {
    FAM_COOLBET_REAL: "Real-money capable · Coolbet UI placer",
    FAM_TRIGGER: "Trigger engine · model vs sharp anchor (paper)",
    FAM_COOLBET_PAPER: "Coolbet own-price paper bots",
    FAM_INTERNAL: "Internal model / strategy validators (paper)",
    FAM_FORWARD_TEST: "Pre-registered PICKS forward test (published, not staked)",
    FAM_INPLAY: "In-play slow-state rig (paper) — OWN Phase 1b",
}


@dataclass(frozen=True)
class BotSpec:
    name: str
    family: str
    market: str            # human label: '1x2', 'O/U 2.5', 'O/U 3.5', 'corners', 'mixed'
    anchor: str            # ANCHOR_MODEL | ANCHOR_SHARP | ANCHOR_NONE
    edge_floor: float | None   # required edge in prob-points (None = n/a)
    odds_floor: float | None
    real_money: bool       # True == the registry's real-money family; must HAVE a placement path
    one_liner: str         # what it does, plain language
    twin: str | None = None    # the bot it is a head-to-head variant of
    # [[#162]] owner decision (b), 2026-09-25: "no twins" — a live bot's rule is changed in place
    # and every pick is tagged with the rule it was made under. BUMP THIS (r1 -> r2 …) in the same
    # commit as any change to the bot's pick rule (gates, floors, filters, prob source). The
    # scheduler writes it to bots.rule_version at start-up (bot_status.sync_rule_versions) and a DB
    # trigger stamps it on every new simulated_bets / shadow_bets row (migration 453).
    rule_version: str = "r1"


# ── the active bots (must match `bots` WHERE retired_at IS NULL) ─────────────
# Floors that are enforced by coolbet_placer are annotated; the drift test checks
# the model-bot / trigger floors against coolbet_placer._MIN_*_BY_MARKET.
BOTS: list[BotSpec] = [
    # Real-money capable — anchor = MODEL, validated floors 13%/8% + odds 2.80/1.80
    BotSpec("bot_coolbet_1x2_model_v1", FAM_COOLBET_REAL, "1x2", ANCHOR_MODEL,
            0.10, 2.80, True,
            "Places our calibrated model's HOME-UNDERDOG 1x2 picks at Coolbet's own price when model edge ≥10% & odds ≥2.80. Real money, per-bot toggle. FAVLONG-CUTS-2026-09-09: restricted to home-underdogs @10% (the one fold-robust 1x2 engine); home-favs + aways excluded, draws are a sharp-trigger edge (§57)."),
    BotSpec("bot_coolbet_ou_model_v1", FAM_COOLBET_REAL, "O/U 2.5", ANCHOR_MODEL,
            0.08, 1.80, True,
            "Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. Real money, per-bot toggle. ⚠️ TOGGLED OFF 2026-09-13 (migration 335, ui_place_enabled=false) — OU-CALIBRATOR-DOMAIN-MISMATCH: every pick it ever staked was generated by a Platt curve fitted on raw ensemble probs and applied to Pinnacle-shrunk probs, which manufactured the edge. n=32, −€139.30, −43.5% ROI, CLV −5.7% (t=−4.6). It keeps its placement path; its coolbet_placer_bots row is OFF and LOCKED (locked_reason, migration 413). Re-enable on positive CLV over a post-fix window."),

    # Trigger engine — book-agnostic windows, PAPER. Model vs Sharp anchor twins.
    BotSpec("bot_coolbet_trigger_sharp_1x2_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental, observing all bands). Paper. Head-to-head vs the model twin. r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) at match time (not from the :05 Stage-A window), with the engine's 8% edge ceiling and, on O/U, the power fair price (devig.fair_prob) instead of Shin.",
            rule_version="r2"),
    BotSpec("bot_coolbet_trigger_sharp_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental, observing all bands). Paper. Head-to-head vs the model twin. r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) at match time (not from the :05 Stage-A window), with the engine's 8% edge ceiling and, on O/U, the power fair price (devig.fair_prob) instead of Shin.",
            twin="bot_coolbet_trigger_ou_v1",
            rule_version="r2"),


    # SHADOW-BOT-VERDICTS-2026-09-14: the three MODEL-anchored 1x2 trigger bots
    # (bot_coolbet_trigger_1x2_v1, bot_unibet_trigger_1x2_v1,
    # bot_trigger_1x2_model_v1) were RETIRED by migration 336 — CLV -8.4% to
    # -9.2% on placeable books at t=-7.1..-14.5, with no fold-robust positive
    # configuration over any edge floor, odds floor or single selection. Their
    # SHARP twins below are kept: same fixtures, same prices, opposite verdict,
    # which is the cleanest evidence in the system that the ANCHOR is what
    # separates a winning bot from a losing one. The `twin=` back-references
    # were dropped with them rather than left pointing at dead specs.
    #
    # The four MODEL-anchored O/U trigger bots are deliberately NOT retired:
    # every settled pick they own falls inside the OU-CALIBRATOR-DOMAIN-MISMATCH
    # window, so excising it leaves no evidence to judge them on. Staged at
    # dev/active/HELD_retire_model_anchored_ou_losers.sql pending era-3 volume.

    # UNIBET-TRIGGER-BOTS-2026-09-09 (Stage 3b) — the Unibet twins, reading the
    # broad Unibet-Site odds sweep. Paper. The DRAW edge the model can't see lives
    # on the sharp anchor here (soft-book mispricing vs de-vig Pinnacle, §57).
    BotSpec("bot_unibet_trigger_sharp_1x2_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Unibet's 1x2 site price beats the de-vigged Pinnacle line by ≥3% (no odds floor). Paper. Where the draw edge should surface (§57). r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) at match time (not from the :05 Stage-A window), with the engine's 8% edge ceiling and, on O/U, the power fair price (devig.fair_prob) instead of Shin.",
            rule_version="r2"),
    BotSpec("bot_unibet_trigger_sharp_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Unibet's O/U 2.5 site price beats the de-vigged Pinnacle line by ≥3% (no odds floor). Paper. r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) at match time (not from the :05 Stage-A window), with the engine's 8% edge ceiling and, on O/U, the power fair price (devig.fair_prob) instead of Shin.",
            twin="bot_unibet_trigger_ou_v1",
            rule_version="r2"),

    # MERGE-TRIGGER-BOTS-2026-09-11 — the book-agnostic replacements for the
    # eight above. The eight were 2 anchors x 2 books x 2 markets, but the BOOK
    # is a venue, not a strategy: `pick_generator` already compares across every
    # book a bot may use and records the winner as `recommended_bookmaker`, so
    # book is a column to GROUP BY, not an identity (and the split would have
    # become 12 bots the moment Epicbet joined). These four are on the two real
    # axes, anchor x market.
    #
    # The eight are DELIBERATELY still active alongside them: the pooled-vs-
    # per-selection calibrator measurement is mid-flight, and retiring them now
    # would make that comparison span a bot change AND a calibrator change.
    # A follow-up migration retires them once `trigger_calibrator_watch` pages
    # the verdict.
    BotSpec("bot_trigger_1x2_sharp_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Book-agnostic SHARP 1x2 trigger: any placeable book beating the de-vigged Pinnacle line by >=3% AND by <=8% (no odds floor). Paper. The 3% floor is set EXPLICITLY — a sharp edge is measured against a near-true line and is never comparable to a model floor. The 8% CEILING exists for the mirror-image reason (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20): the observed maximum overlay on Pinnacle is +6.6%, so a larger apparent edge is a mis-mapped price, not an opportunity. ⚠️ ITS ENTIRE TRACK RECORD (25 picks) WAS VOIDED on 2026-09-20 — every pick was priced off a quote belonging to another fixture, which published +549.9% ROI / EUR 1,319.80. n=0 today; any number quoted for this bot before that date is meaningless. ⚠️⚠️ AND THE 3% FLOOR WAS NEVER LIVE (SHARP-FLOOR-STACKED-ON-MODEL-FLOOR, fixed 2026-09-22, [[#007]]): `best_price_router.decide_book` re-imposed the 10%/13% MODEL selection floor on top of the explicit 3%, so the EFFECTIVE floor was 10-13% against an anchor whose largest genuine overlay is +6.6%. That is WHY all 25 picks were phantom — an unreachable floor can only be cleared by a wrong price. From 2026-09-20 the 8% ceiling sat BELOW that floor, leaving an empty band, and the bot emitted nothing at all between 2026-09-20 15:58 and the fix. r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) — fair price devig.fair_prob (Shin 1x2, power O/U), book quote <= 60 min (was the router's 180), every gate on every book before the best price is chosen (the ceiling used to be checked on the winner only).",
            rule_version="r2"),
    BotSpec("bot_trigger_ou_sharp_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Book-agnostic SHARP O/U 2.5 trigger: any placeable book beating the de-vigged Pinnacle line by >=3% AND by <=8% (no odds floor). Paper. The ceiling matters MORE here than on 1x2: O/U prices are compressed into ~1.2-3.0, so a wrong fixture rarely trips a price-ratio guard — all 6 of this bot's picks passed the ratio test and all 6 were contaminated. ⚠️ ALL 6 VOIDED 2026-09-20 (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES); n=0 today. ⚠️⚠️ The 3% floor was never live either — see the 1x2 twin: the effective floor was the 8% MODEL O/U floor, equal to the ceiling, so the admissible band was a single point and the bot last emitted 2026-09-19 16:21. Fixed 2026-09-22 ([[#007]]). r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine (workers/automation/sharp_engine.py) — fair price devig.fair_prob (Shin 1x2, power O/U), book quote <= 60 min (was the router's 180), every gate on every book before the best price is chosen (the ceiling used to be checked on the winner only).",
            twin="bot_trigger_ou_model_v1",
            rule_version="r2"),
    # SHARP-TIGHT-INSTRUMENT-2026-09-15. An INSTRUMENT, not a strategy — the one
    # OWN configuration two independent research rounds agreed was worth
    # measuring and neither thought was worth a euro. Pre-registration:
    # dev/active/own-sharp-tight-preregistration.md. PROMOTION REQUIRES
    # margin-corrected own-book CLV > 0 at n>=300; ROI may never promote it.
    BotSpec("bot_trigger_1x2_sharp_tight_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.02, 1.01, False,
            "INSTRUMENT (paper, never placeable). SHARP 1x2 at the TIGHT gate the original 70,200-cell sweep could not express — it swept a constant expected-ROI floor while the live gate is a constant probability-difference floor, and roi_edge = prob_edge x odds makes the latter a CURVE in odds. Gate: P_shin - 1/odds >= 2% AND odds <= 2.50, pooled over Coolbet/Epicbet/Unibet-Site. Backtest n=225 ROI +17.07% CI [+4.18,+29.95] OOS +23.40% — but a 12-day effect whose margin-corrected own-book CLV is -5.4 to -7.6%, so it is being MEASURED, not believed. r2 (#162 W7.6, 2026-09-26): decided by the ONE sharp engine at match time — fair price read then, not from the :05 Stage-A window; the pre-registered gate itself (>= 2%, odds <= 2.50, <= 60 min) is unchanged and it gets NO ceiling.",
            rule_version="r2"),

    # UNIFIED-GATE-INSTRUMENT-2026-09-22 ([[#033]]). An INSTRUMENT, not a
    # strategy: it exists because the draw/away half of the owner's unified-gate
    # hypothesis is UNTESTABLE on what we have — the calibrated cohort at
    # odds>=2.80 is 236 HOME of 240. Promotion is not on the table; the only
    # output is a population that can answer the question.
    BotSpec("bot_unified_gate_1x2_paper_v1", FAM_TRIGGER, "1x2", ANCHOR_MODEL,
            0.10, 2.80, False,
            "INSTRUMENT (paper, never placeable, never published). Every 1x2 selection — home, draw AND away — at a FLAT 10% model edge and odds >= 2.80, across both placeable books. The flat floor IS the hypothesis: the registry's selection-aware floor is 10% home / 13% draw+away, and inheriting it would make the instrument test the thing it is meant to be compared against. It exists because the calibrated cohort at odds>=2.80 is 236 HOME of 240, so every draw/away figure quoted so far (including 'draws are -31.5% over n=95') rests on a ~98%-home population and answers nothing. No edge ceiling — model-anchored, where a 20% edge is ordinary. ⚠️ Could not have been built correctly before 2026-09-22: until SHARP-FLOOR-STACKED-ON-MODEL-FLOOR was fixed ([[#007]]) the router re-imposed the selection-aware floor over any explicit edge_floor, so draws/aways would have run at 13% while the config said 10%."),

    # CONSENSUS-ANCHOR-ARM-2026-09-22 ([[#068]]). The published forward test's
    # SECOND arm. Its own bot identity, not a variant of the sharp one, because
    # a reader expanding a leaderboard row must see ONE rule's record — two
    # different anchors averaged into one number describe neither.
    # CONSENSUS SPLIT BY GRADE ([[#095]], 2026-09-23, migration 380). The single
    # `bot_consensus_anchor_v1` (#068) is RETIRED and its picks are owned by two
    # bots, one per `grade` (#094). Same ledger row, same selection rule — the
    # grade decides only whose record a pick counts toward. Split in the VIEWS,
    # not the ledger: per-grade arms would defeat the (match, market, selection,
    # arm) de-dupe when a leg's grade flips between runs, and send it twice.
    # RE-TIERED 2026-09-23 ([[#098]], migration 381): the letters shifted DOWN.
    # B = strongest (clean + odds 1.20-1.60), C = standard (clean, other odds),
    # D = weak (recorded, NEVER sent). Grade A is reserved for model picks.
    # RULE v2 (2026-09-24, [[#106]]): the 3% floor must hold under EVERY credible de-vig
    # method (Shin, additive, power), not just Shin — consensus_edge_v2_2026_09_24.
    BotSpec("bot_consensus_b_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5", ANCHOR_CONSENSUS,
            0.03, 1.20, False,
            "PUBLISHED (Telegram + /picks), never staked. **TESTING (owner #155, 2026-09-25: n 3 — was BETA) — the STRONGEST consensus tier.** Every grade check passes (classified league, no second panel book disagrees, edge <= 6%) AND odds 1.20-1.60. The only rule positive in all three samples: ours 56 d +17.8% (n=48), unseen May-Jul +14.7% (n=29), Beat the Bookie 2015-16 +9.8% (n=696, Holm p<1e-4). Mechanism: favourite-longshot bias. ~1 pick/day. Ledger: picks_forward_test WHERE arm='consensus_anchor' AND grade='B'.",
            twin="bot_consensus_c_v1"),
    BotSpec("bot_consensus_c_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5", ANCHOR_CONSENSUS,
            0.03, None, False,
            "PUBLISHED (Telegram + /picks), never staked. **TESTING — the STANDARD consensus tier.** Every grade check passes, odds outside 1.20-1.60. Positive but unproven: +2.9% unseen (n=150), +3.3% external (n=6,380). The bulk of the channel (~12/day). Ledger: grade='C'.",
            twin="bot_consensus_b_v1"),
    BotSpec("bot_consensus_d_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5", ANCHOR_CONSENSUS,
            0.03, None, False,
            "**EXPERIMENTAL (owner #155, 2026-09-25) — admin only.** RECORDED, NOT PUBLISHED since 2026-09-23 — the WEAK consensus picks: tier-0 league, OR a second panel book sees no edge at the price, OR edge > 6%. Claimed to the ledger so the record stays checkable, never sent (since #155 by its STATUS: the scheduler's arm_bot_sends reads bot_distribution). Loses on our own data: -25.6% in sample, -2.8% unseen. Its earlier picks were published as grade C and stay counted in picks_public_all (sent = counted); as an EXPERIMENTAL bot it has no /performance row. Ledger: grade='D'.",
            twin="bot_consensus_c_v1"),


    # Coolbet own-price paper bots
    # bot_ou35_model_v1 RETIRED 2026-09-25 (migration 438, owner): the first 'review this bot' flag (#155) — 460 settled,
    # sharp-anchor CLV −4.5% (upper 95% −4.0%); #152 step 3 read −8% on the old model. Picks stay in the totals (#157).

    # Internal model / strategy validators (paper, not a Coolbet placement path)
    # V10-SPLIT-BY-MARKET (migration 375, 2026-09-22, [[#040]]). `bot_v10_all` was
    # ONE spec with market "mixed". Splitting it was filed as accounting; measuring
    # the halves first showed the two markets sit on OPPOSITE SIDES OF ZERO, both
    # with CIs excluding it (de-vigged Pinnacle CLV, gotcha 8):
    #     1x2            n=335   +2.50%   95% CI [+0.41, +4.60]   ROI +7.3% exec
    #     over_under_25  n=181   -3.85%   95% CI [-5.01, -2.69]   ROI  -0.54%
    # So "the calibrated reference bot, +11-13%, the yardstick other bots are read
    # against" was one market carrying the other, and every bot ever compared to it
    # was compared to a blend. "mixed" as a market label was hiding that.
    BotSpec("bot_v10_1x2", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "The reference bot's 1x2 half (status ACTIVE — was CALIBRATED until #175 merged BETA + CALIBRATED, 2026-09-26): v10 model across target leagues, tier-adjusted thresholds. De-vigged Pinnacle CLV +2.50% (n=335, CI [+0.41,+4.60]) — but the positive record is July-2026 onward (May −0.87%, Jun −2.15%, Jul +8.35%, Aug +8.24%, Sep +7.25%), so it is a three-month yardstick, not a five-month one. r2 (#180, 2026-09-26): no Tier C candidates — a Tier C match's probability is API-Football's 1X2 (worse than guessing) + a 50/50 O/U prior.",
            rule_version="r2"),
    # RATING-1X2-BOT ([[#141]], 2026-09-24): owner request "1x2 market NEW".
    BotSpec("bot_rating_1x2_v1", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "1x2 market NEW: a twin of bot_v10_1x2 (same thresholds, odds 1.30-4.50, min_prob 0.30, cohort, veto, staking) priced by the walk-forward 1X2 rating model r1x2_d8plus_v1 used as is — no Pinnacle shrinkage, which for bot_v10_1x2 returns Platt(Pinnacle). Holdout log-loss 1.008 vs 1.071 for the old head, but alpha vs Pinnacle = 0, so ROI needs CLV to back it. experimental, paper, no placement path."),
    BotSpec("bot_combined_1x2_v1", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "1x2 market NEW+: the same twin of bot_v10_1x2, priced by the COMBINED 1X2 model r1x2_comb_v1 (ratings + de-vigged 18-book consensus + Pinnacle, API-Football only where unpriced; 0.9763 vs 1.0711 log-loss). Built mostly from market prices, so its edge is the quoted book sitting away from the consensus (consensus-outlier), not model-vs-market. experimental, paper, no placement path."),
    BotSpec("bot_v10_1x2_newplus_v1", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "Match result — NEW MODEL (#152): twin of bot_v10_1x2 on the NEW+ model (r1x2_comb_v1), EV >= 3% flat, odds 1.30-3.00 (LANES cap, 2026-09-25), never a VIP-held pick (so the EV 3-5% band). bot_v10_1x2 is unchanged (live CLV ~+4.7% Jul-Sep); the two are compared live after 50-100 settled picks. testing, shown on /performance, paper. r2 (#177, 2026-09-26): record RESTARTED at the #176 fix (bots.record_restart_at, migration 463) — earlier picks read a ~25-min-old probability; sent picks stay in the record.",
            rule_version="r2"),
    BotSpec("bot_combined_1x2_ev5_v1", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "1x2 NEW+ EV5 (#141 B4): the combined model r1x2_comb_v1 as a consensus-outlier bettor in its natural unit — EV = p x odds - 1 >= 5% flat across tiers, Pinnacle price required, no min_prob, odds 1.30-6.00, one pick per match. Backtest B2 CLV +2.0% (n=1,050; +1.1% at our own sweepers), same window so optimistic. VIP · TESTING (#155), paper, no placement path. r2 (#162 W7.7/W7.8, 2026-09-25): runs EXACTLY the B2 rule — the ~8 inherited legacy gates are skipped (exact_rule) — and holds one pick per match ACROSS runs. r3 (#177, 2026-09-26): record RESTARTED at the #176 fix (bots.record_restart_at, migration 463) — earlier picks read a ~25-min-old probability; sent picks stay in the record.",
            rule_version="r3"),
    # bot_combined_1x2_ev8_v1 RETIRED 2026-09-25 (migration 444): a strict subset of the VIP bot
    # above; the EV8 band is now a split of the VIP ledger (per-pick vip_ev_label).
    BotSpec("bot_ou_sharp_early_v1", FAM_INTERNAL, "ou", ANCHOR_SHARP,
            None, None, False,
            "O/U EARLY (#149): a soft book's O/U 1.5/2.5/3.5 quote beats Pinnacle's power-de-vigged fair price by EV 5-15% (cap = palpable-error guard) while it is >= 12 h before kickoff; one pick per (match, line). Backtest round O3 T3: CLV +7.5% (Aug, n=383) / +6.9% (Sep, n=459), ROI +10.4% / +10.8%. Job workers/jobs/ou_sharp_outlier.py. VIP · TESTING (#155), paper, no placement path."),
    BotSpec("bot_ou_sharp_2anchor_v1", FAM_INTERNAL, "ou", ANCHOR_SHARP,
            None, None, False,
            "O/U TWO-ANCHOR (#149): as O/U EARLY without the 12 h rule, but the book must ALSO beat the leave-one-out consensus of the other books by >= 2% EV. Backtest round O3 T2: CLV +6.6% (Aug, n=420) / +4.2% (Sep, n=480). experimental, paper, no placement path."),
    # bot_v10_ou RETIRED 2026-09-24 (migration 399, owner "yes" on [[#077]]): de-vigged
    # Pinnacle CLV -3.85% (n=181), negative in all 5 months and 7 model versions, and
    # every O/U route measured 09-23/24 (#089, #118, #090 a) ends at alpha = 0.
    # Its return on the new O/U model (#152, owner 2026-09-25) is the TWIN below, so the old
    # model's 252-pick record neither becomes the new rule's nor trips the #155 review flag.
    BotSpec("bot_v10_ou_comb_v1", FAM_INTERNAL, "ou", ANCHOR_MODEL,
            None, None, False,
            "Goals over/under — NEW MODEL (#152, migration 443): successor of the retired bot_v10_ou on the combined O/U model ou_comb_v1 (served p = Pinnacle where priced, else combined), EV >= 3% flat, O/U 1.5/2.5/3.5 over+under, odds 1.30-3.00, one pick per match; O/U EARLY-held / VIP-range picks held back until kickoff (#164). Backtest 08-31..09-24 at open: 61 picks (~2.4/day), sharp CLV +2.0% [+1.0, +3.0]. testing, sent to /picks, paper. r2 (#162 W7.8, 2026-09-25): one pick per match ACROSS runs, not just within one. r3 (#177, 2026-09-26): record RESTARTED at the #176 fix (bots.record_restart_at, migration 463) — earlier picks read a ~25-min-old probability; sent picks stay in the record.",
            rule_version="r3"),
    # REGISTRY-DRIFT-FIX-2026-09-09: bot_1x2_specialist, bot_dnb_specialist and
    # bot_summer_specialist were retired in the DB (migrations 323/324, 2026-09-09
    # 10:25–10:41) but left in the registry — removed here so active_names() matches
    # the DB (SYSTEM-MAP-REGISTRY-NOT-DRIFTED section 5).
    # ANCHOR CORRECTED 2026-09-22 (owner: "how is strategy different from model?").
    # It was ANCHOR_NONE, which reads as "no fair-value basis". That is false:
    # `daily_pipeline_v2` gives this bot `edge_thresholds` (1x2_fav 0.06 /
    # 1x2_long 0.09) — the SAME model edge `bot_v10_all` uses — and then filters
    # by league (Spain/Australia/Iceland), selection (home/away) and odds band
    # 1.50-5.50. It is the model anchor with extra filters, not a third method.
    #
    # It mattered because the /performance chip renders straight off this field,
    # so a customer surface was telling readers this bot priced against something
    # other than the model. ANCHOR_NONE now means what it says — the in-play rig,
    # which prices off the BOOK's own de-vigged probability and has no model or
    # sharp reference at all.
    BotSpec("bot_high_roi_global_v2", FAM_INTERNAL, "1x2", ANCHOR_MODEL,
            None, None, False,
            "1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator. r2 (#180, 2026-09-26): no Tier C candidates — a Tier C match's probability is API-Football's 1X2 (worse than guessing) + a 50/50 O/U prior.",
            rule_version="r2"),

    # PICKS-FORWARD-TEST-BOT-2026-09-14 — the published PICKS rule, registered so
    # it is not a silent strategy. It is unlike every other row in this list in
    # three ways, and each one is deliberate:
    #
    #  * It WRITES NOTHING. No simulated_bets, no shadow_bets. Its ledger is
    #    `picks_forward_test`, surfaced through the read-only projection
    #    `picks_forward_test_shadow` (migration 345). Migration 342's header
    #    explains why: both bet tables are bot-scoped with staking semantics, and
    #    every bot-cohort query ever written would silently absorb these rows.
    #  * `odds_floor` is None because this rule has an odds CAP (4.0), not a
    #    floor — the edge collapses into longshot noise above it. There is no
    #    field here for a cap, and putting 4.0 in `odds_floor` would read as the
    #    exact opposite of what the rule does.
    #  * Its floor is 3% SHARP edge and is MULTIPLICATIVE (P_shin x price - 1),
    #    where the model floors in this file are differences in probability
    #    points. Not comparable, in either direction.
    #
    # The market label is deliberately not "1x2" or "O/U 2.5": the drift test
    # cross-checks single-market sharp floors against pick_triggers' Stage-A
    # config, and this rule is not that engine — it is a standalone publisher
    # whose constants are locked by PICKS-FORWARD-TEST-RULE-LOCKED instead.
    # SPLIT BY MARKET 2026-09-24 ([[#122]], migration 402): the parent
    # bot_sharp_forward_test_v1 is retired; its picks are OWNED by market. Same rule,
    # same pre-registered test — bookkeeping only. Market labels carry "(forward test)"
    # so the drift test does not cross-check them against pick_triggers' Stage-A floors.
    BotSpec("bot_sharp_1x2_v1", FAM_FORWARD_TEST, "1x2 (forward test)",
            ANCHOR_SHARP, 0.03, None, False,
            "1x2 half of the published sharp picks. " + "The PUBLISHED picks. Pre-registered forward test started 2026-09-14: best book price beats the Shin-de-vigged Pinnacle line by >=3%, odds <=4.0 (a CAP, not a floor), anchor and bet quote within 60 min, top 8/day. Uses NO model output. Flat 1 unit, no Kelly, no bankroll. Writes NO simulated_bets and NO shadow_bets — read-through only, via picks_forward_test_shadow. Prior: +5.5% ROI backtest, 95% CI [-0.7,+11.7] = NO DEMONSTRATED EDGE. Stops at n=200/400 unless it beats the junk-anchor control on sharp-anchor CLV (one-sided bootstrap p<0.025; AMENDED 2026-09-25, #156 — own-book margin-corrected CLV is still reported, decides nothing), promote/kill at n=800 on the ROI CI. Junk-anchor negative control runs alongside, unpublished. Rule locked in dev/active/picks-forward-test-preregistration.md.",
            twin="bot_sharp_ou_v1"),
    BotSpec("bot_sharp_ou_v1", FAM_FORWARD_TEST, "O/U 2.5 (forward test)",
            ANCHOR_SHARP, 0.03, None, False,
            "O/U 2.5 half of the published sharp picks (clv_sharp +0.67% n=22 at the split; retire on its own record if its CI is entirely below 0 at n >= 100). " + "The PUBLISHED picks. Pre-registered forward test started 2026-09-14: best book price beats the Shin-de-vigged Pinnacle line by >=3%, odds <=4.0 (a CAP, not a floor), anchor and bet quote within 60 min, top 8/day. Uses NO model output. Flat 1 unit, no Kelly, no bankroll. Writes NO simulated_bets and NO shadow_bets — read-through only, via picks_forward_test_shadow. Prior: +5.5% ROI backtest, 95% CI [-0.7,+11.7] = NO DEMONSTRATED EDGE. Stops at n=200/400 unless it beats the junk-anchor control on sharp-anchor CLV (one-sided bootstrap p<0.025; AMENDED 2026-09-25, #156 — own-book margin-corrected CLV is still reported, decides nothing), promote/kill at n=800 on the ROI CI. Junk-anchor negative control runs alongside, unpublished. Rule locked in dev/active/picks-forward-test-preregistration.md.",
            twin="bot_sharp_1x2_v1"),
    # TWIN ARMS ([[#161]], 2026-09-25, owner-approved, migration 434). Each is its parent's
    # rule in every gate plus ONE gate the #156 audit pointed to, under its own arm and
    # rule_version. RECORDED, NEVER PUBLISHED (not in PUBLISHED_ARMS; every public view
    # filters an explicit arm allow-list) — EXPERIMENTAL under #155. Pre-registered in
    # dev/active/picks-forward-test-preregistration.md ("TWIN ARMS"); readout at n=50/100
    # by scripts/picks_forward_test_checkpoint.py --twins.
    BotSpec("bot_sharp_aligned_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5 (twin)",
            ANCHOR_SHARP, 0.03, None, False,
            "RECORDED, NOT PUBLISHED. Twin of the sharp picks: live v4 in every gate PLUS, at our own direct books (Coolbet, Unibet-Site, Epicbet, Tonybet), the book's quote and the Pinnacle anchor quote must be <= 5 min apart (API-Football books arrive with gap 0). Audit: sharp picks at our books +3.9% vs sharp close at <= 5 min, -0.6% at 5-60 min (small cells). Ledger: picks_forward_test WHERE arm='sharp_own_book_aligned'. Judged vs arm='live' and the junk control on sharp-anchor CLV at n=100.",
            twin="bot_sharp_1x2_v1"),
    BotSpec("bot_consensus_pinconf_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5 (twin)",
            ANCHOR_CONSENSUS, 0.03, None, False,
            "RECORDED, NOT PUBLISHED. Twin of the consensus picks: consensus v2 in every gate PLUS, where a fresh tight Pinnacle anchor exists (anchor.py pinnacle_tight), EV >= 0% against that Pinnacle price too. Audit: consensus picks at our books -2.1% vs sharp close. Ledger: picks_forward_test WHERE arm='consensus_pin_confirmed'. Judged vs arm='consensus_anchor' and the junk control on sharp-anchor CLV at n=100.",
            twin="bot_consensus_c_v1"),
    # OWN Phase 1b (2026-09-15) — the in-play slow-state RIG. Two paper bots, one
    # measurement: the LIVE arm prices the two LOCKED triggers (0-0 at 35-54' ->
    # under 2.5; two-goal lead at 70-89' -> the leader; both at <= 2.20) at
    # Epicbet's ON-SCREEN price; the CONTROL arm prices the same trigger at the
    # same instant off API-Football's live aggregate. Two bots because
    # shadow_bets_unique de-duplicates on (bot, match, market, selection). Primary
    # metric is hit-rate minus the book's de-vigged prob (CLV inadmissible in
    # play). STOP at n=1,000 if the lift is negative; decide at n=3,000.
    # workers/jobs/inplay_collector.py · scripts/inplay_slowstate_eval.py.
    BotSpec("bot_inplay_slowstate_v1", FAM_INPLAY, "in-play O/U 2.5 + 1x2", ANCHOR_NONE,
            None, None, False,
            "In-play slow-state rig, LIVE arm: T1 0-0 at 35-54' -> UNDER 2.5 at <= 2.20; T2 two-goal lead at 70-89' -> the leader at <= 2.20, at Epicbet's on-screen price. Paper. Metric: hit-rate minus the book's de-vigged prob; STOP n=1,000 if lift < 0, decide n=3,000."),
    BotSpec("bot_inplay_slowstate_afctl_v1", FAM_INPLAY, "in-play O/U 2.5 + 1x2", ANCHOR_NONE,
            None, None, False,
            "CONTROL arm of the in-play rig: the same two triggers priced off API-Football's live aggregate at the same instant. The live-minus-control gap is the value of the fresh board. Never a strategy on its own.",
            twin="bot_inplay_slowstate_v1"),
]


# [[#155]] ONE STATUS DECIDES DISTRIBUTION: a bot's status (bots.maturity_label) is the only per-bot
# input for every customer channel — workers/utils/bot_status.py, view bot_distribution. VIP is a
# CHANNEL on top of a status: both VIP bots read "VIP · TESTING" (owner 2026-09-25).
# VIP ([[#148]], owner 2026-09-24). The paid-tier bot: its LIVE picks go only to
# Pro/Elite users (Telegram DMs) and, once it exists, the private VIP channel
# (TELEGRAM_VIP_CHAT_ID); the public sees them only once SETTLED on /performance.
# Mirrors `bots.vip` (migration 420) — smoke VIP-BOTS-MATCH-DB checks the two sets are equal (#162 W8.8).
# Each pick is labelled by its expected value: EV8 (>= 8%) or EV5 (5-8%).
VIP_BOTS: frozenset[str] = frozenset({"bot_combined_1x2_ev5_v1", "bot_ou_sharp_early_v1"})  # 1X2 VIP + O/U VIP (#149, owner 2026-09-25)
VIP_EV8_MIN = 0.08


def vip_ev_label(calibrated_prob: float, odds: float) -> str:
    """'EV8' when p x odds - 1 >= 8%, else 'EV5' (the VIP bot's own floor is 5%)."""
    return "EV8" if float(calibrated_prob) * float(odds) - 1 >= VIP_EV8_MIN else "EV5"


def by_name(name: str) -> BotSpec | None:
    return next((b for b in BOTS if b.name == name), None)


# BOT-RETIREMENT-ON-CLV-2026-09-14 — five bots REMOVED from this list and
# retired in the DB (migration 348), on margin-corrected own-book CLV:
#   bot_coolbet_trigger_ou_v1       n=371  EV -6.27%
#   bot_team_total_paper_shadow_v1  n=310  EV -4.98%
#   bot_unibet_trigger_ou_v1        n=221  EV -5.61%
#   bot_1h_1x2_paper_shadow_v1      n=165  EV -6.25%
#   bot_trigger_ou_model_v1         n=164  EV -5.94%
# Four of the five were MODEL-anchored. The sharp-anchored bots stay even
# where their point estimate is negative — their CIs still straddle zero and
# the anchor comparison is the open question.


def active_names() -> set[str]:
    return {b.name for b in BOTS}


def placeable_names() -> set[str]:
    """Bots this registry declares as real-money. Each MUST have a placement path
    (placement_gate.placement_path_reason); the capable set is wider (#139)."""
    return {b.name for b in BOTS if b.real_money}


def family(fam: str) -> list[BotSpec]:
    return [b for b in BOTS if b.family == fam]
