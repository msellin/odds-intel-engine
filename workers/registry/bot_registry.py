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
lookups. PLACEABLE_BOTS stays hardcoded in scripts/place_coolbet_ui.py as a
defense-in-depth safety set — this registry does not re-derive it, the drift test
only asserts the two agree.
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
    real_money: bool       # True == must be in PLACEABLE_BOTS
    one_liner: str         # what it does, plain language
    twin: str | None = None    # the bot it is a head-to-head variant of


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
            "Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. Real money, per-bot toggle. ⚠️ TOGGLED OFF 2026-09-13 (migration 335, ui_place_enabled=false) — OU-CALIBRATOR-DOMAIN-MISMATCH: every pick it ever staked was generated by a Platt curve fitted on raw ensemble probs and applied to Pinnacle-shrunk probs, which manufactured the edge. n=32, −€139.30, −43.5% ROI, CLV −5.7% (t=−4.6). It stays in PLACEABLE_BOTS (the code boundary is unchanged); only the DB toggle is off. Re-enable on positive CLV over a post-fix window."),

    # Trigger engine — book-agnostic windows, PAPER. Model vs Sharp anchor twins.
    BotSpec("bot_coolbet_trigger_sharp_1x2_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental, observing all bands). Paper. Head-to-head vs the model twin."),
    BotSpec("bot_coolbet_trigger_sharp_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental, observing all bands). Paper. Head-to-head vs the model twin.",
            twin="bot_coolbet_trigger_ou_v1"),


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
            "Sharp twin: fires when Unibet's 1x2 site price beats the de-vigged Pinnacle line by ≥3% (no odds floor). Paper. Where the draw edge should surface (§57)."),
    BotSpec("bot_unibet_trigger_sharp_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Sharp twin: fires when Unibet's O/U 2.5 site price beats the de-vigged Pinnacle line by ≥3% (no odds floor). Paper.",
            twin="bot_unibet_trigger_ou_v1"),

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
            "Book-agnostic SHARP 1x2 trigger: any placeable book beating the de-vigged Pinnacle line by >=3% AND by <=8% (no odds floor). Paper. The 3% floor is set EXPLICITLY — a sharp edge is measured against a near-true line and is never comparable to a model floor. The 8% CEILING exists for the mirror-image reason (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20): the observed maximum overlay on Pinnacle is +6.6%, so a larger apparent edge is a mis-mapped price, not an opportunity. ⚠️ ITS ENTIRE TRACK RECORD (25 picks) WAS VOIDED on 2026-09-20 — every pick was priced off a quote belonging to another fixture, which published +549.9% ROI / EUR 1,319.80. n=0 today; any number quoted for this bot before that date is meaningless. ⚠️⚠️ AND THE 3% FLOOR WAS NEVER LIVE (SHARP-FLOOR-STACKED-ON-MODEL-FLOOR, fixed 2026-09-22, [[#007]]): `best_price_router.decide_book` re-imposed the 10%/13% MODEL selection floor on top of the explicit 3%, so the EFFECTIVE floor was 10-13% against an anchor whose largest genuine overlay is +6.6%. That is WHY all 25 picks were phantom — an unreachable floor can only be cleared by a wrong price. From 2026-09-20 the 8% ceiling sat BELOW that floor, leaving an empty band, and the bot emitted nothing at all between 2026-09-20 15:58 and the fix."),
    BotSpec("bot_trigger_ou_sharp_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.01, False,
            "Book-agnostic SHARP O/U 2.5 trigger: any placeable book beating the de-vigged Pinnacle line by >=3% AND by <=8% (no odds floor). Paper. The ceiling matters MORE here than on 1x2: O/U prices are compressed into ~1.2-3.0, so a wrong fixture rarely trips a price-ratio guard — all 6 of this bot's picks passed the ratio test and all 6 were contaminated. ⚠️ ALL 6 VOIDED 2026-09-20 (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES); n=0 today. ⚠️⚠️ The 3% floor was never live either — see the 1x2 twin: the effective floor was the 8% MODEL O/U floor, equal to the ceiling, so the admissible band was a single point and the bot last emitted 2026-09-19 16:21. Fixed 2026-09-22 ([[#007]]).",
            twin="bot_trigger_ou_model_v1"),
    # SHARP-TIGHT-INSTRUMENT-2026-09-15. An INSTRUMENT, not a strategy — the one
    # OWN configuration two independent research rounds agreed was worth
    # measuring and neither thought was worth a euro. Pre-registration:
    # dev/active/own-sharp-tight-preregistration.md. PROMOTION REQUIRES
    # margin-corrected own-book CLV > 0 at n>=300; ROI may never promote it.
    BotSpec("bot_trigger_1x2_sharp_tight_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.02, 1.01, False,
            "INSTRUMENT (paper, never placeable). SHARP 1x2 at the TIGHT gate the original 70,200-cell sweep could not express — it swept a constant expected-ROI floor while the live gate is a constant probability-difference floor, and roi_edge = prob_edge x odds makes the latter a CURVE in odds. Gate: P_shin - 1/odds >= 2% AND odds <= 2.50, pooled over Coolbet/Epicbet/Unibet-Site. Backtest n=225 ROI +17.07% CI [+4.18,+29.95] OOS +23.40% — but a 12-day effect whose margin-corrected own-book CLV is -5.4 to -7.6%, so it is being MEASURED, not believed."),

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
            "PUBLISHED (Telegram + /picks), never staked. **BETA — the STRONGEST consensus tier.** Every grade check passes (classified league, no second panel book disagrees, edge <= 6%) AND odds 1.20-1.60. The only rule positive in all three samples: ours 56 d +17.8% (n=48), unseen May-Jul +14.7% (n=29), Beat the Bookie 2015-16 +9.8% (n=696, Holm p<1e-4). Mechanism: favourite-longshot bias. ~1 pick/day. Ledger: picks_forward_test WHERE arm='consensus_anchor' AND grade='B'.",
            twin="bot_consensus_c_v1"),
    BotSpec("bot_consensus_c_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5", ANCHOR_CONSENSUS,
            0.03, None, False,
            "PUBLISHED (Telegram + /picks), never staked. **TESTING — the STANDARD consensus tier.** Every grade check passes, odds outside 1.20-1.60. Positive but unproven: +2.9% unseen (n=150), +3.3% external (n=6,380). The bulk of the channel (~12/day). Ledger: grade='C'.",
            twin="bot_consensus_b_v1"),
    BotSpec("bot_consensus_d_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5", ANCHOR_CONSENSUS,
            0.03, None, False,
            "RECORDED, NOT PUBLISHED since 2026-09-23 — the WEAK consensus picks: tier-0 league, OR a second panel book sees no edge at the price, OR edge > 6%. Claimed to the ledger so the record stays checkable, never sent (scheduler skips send for grade D). Loses on our own data: -25.6% in sample, -2.8% unseen. Its earlier picks were published as grade C and stay on /performance. Ledger: grade='D'.",
            twin="bot_consensus_c_v1"),


    # Coolbet own-price paper bots
    BotSpec("bot_ou35_model_v1", FAM_COOLBET_PAPER, "O/U 3.5", ANCHOR_MODEL,
            0.08, 1.80, False,
            "Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic calibration). Paper. +7.8% not-robust, accruing forward."),

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
            "The calibrated reference bot's 1x2 half: v10 model across target leagues, tier-adjusted thresholds. De-vigged Pinnacle CLV +2.50% (n=335, CI [+0.41,+4.60]) — but the positive record is July-2026 onward (May −0.87%, Jun −2.15%, Jul +8.35%, Aug +8.24%, Sep +7.25%), so it is a three-month yardstick, not a five-month one."),
    # bot_v10_ou RETIRED 2026-09-24 (migration 399, owner "yes" on [[#077]]): de-vigged
    # Pinnacle CLV -3.85% (n=181), negative in all 5 months and 7 model versions, and
    # every O/U route measured 09-23/24 (#089, #118, #090 a) ends at alpha = 0.
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
            "1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator."),

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
    BotSpec("bot_sharp_forward_test_v1", FAM_FORWARD_TEST, "1x2 + O/U 2.5",
            ANCHOR_SHARP, 0.03, None, False,
            "The PUBLISHED picks. Pre-registered forward test started 2026-09-14: best book price beats the Shin-de-vigged Pinnacle line by >=3%, odds <=4.0 (a CAP, not a floor), anchor and bet quote within 60 min, top 8/day. Uses NO model output. Flat 1 unit, no Kelly, no bankroll. Writes NO simulated_bets and NO shadow_bets — read-through only, via picks_forward_test_shadow. Prior: +5.5% ROI backtest, 95% CI [-0.7,+11.7] = NO DEMONSTRATED EDGE. Stops at n=200/400 on margin-corrected CLV, promote/kill at n=800 on the ROI CI. Junk-anchor negative control runs alongside, unpublished. Rule locked in dev/active/picks-forward-test-preregistration.md."),
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
    """Bots this registry declares as real-money — MUST equal PLACEABLE_BOTS."""
    return {b.name for b in BOTS if b.real_money}


def family(fam: str) -> list[BotSpec]:
    return [b for b in BOTS if b.family == fam]
