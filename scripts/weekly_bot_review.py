"""BOT-MATURITY-REVIEW-WEEKLY (2026-06-15): Sunday rollup of per-bot performance
with PROMOTE / DEMOTE / HOLD verdict for each currently-active bot.

Origin: 2026-06-13 audit found `bot_high_alignment` (maturity=beta, -EUR 56 over
50 real bets) had been auto-placing real money because the Mac daemon lacked
the maturity gate that the pipeline had. The decision "which bots are
trustworthy enough to spend real money on?" was manual and ad-hoc —
promotions happened reactively when someone happened to notice.

This script makes the decision explicit, scheduled, and auditable. Output
is plain text (column-aligned) so the email helper can wrap it in <pre>
without re-rendering.

BOT-GATE-REACHABLE (2026-08-28) — three defects fixed, all found by auditing
why the job had never once emitted a PROMOTE or a DEMOTE in 10 weeks of runs:

  1. THE GATE WAS A CATCH-22. Promotion required 20+ settled REAL bets, but
     `COOLBET_RECORD_ALLOWED_MATURITY=calibrated` means only calibrated bots
     ever write to `real_bets`. A beta bot could not earn the real-money
     history the gate demanded, because being beta is exactly what stopped it
     placing real money. Every non-calibrated bot sat at real n <= 2 forever.
     Fix: a second, reachable promotion path scored on PAPER evidence
     (see PROMOTE_PAPER_* below). The real-money path is kept as a fast lane.

  2. THE SHADOW LEDGER WAS INVISIBLE. `bot_sweep_*`, `bot_pin_*` and
     `bot_coolbet_value_v1` write to `shadow_bets`, not `simulated_bets`, so
     they reported as DORMANT / zero-activity despite real track records —
     `bot_pin_1x2_home_v1` was sitting on 104 settled picks at +13.1pct ROI
     and could not be seen. Fix: `_fetch_paper_bets` falls back to
     `shadow_bets_unique` for bots with no `simulated_bets` rows.

  3. IN-PLAY BOTS WERE STRUCTURALLY INELIGIBLE. `sim CLV > +5pct` can never be
     true for `inplay_*` — there is no closing line for an in-play pick, so
     `clv` is always NULL. `inplay_l` is calibrated only because it was
     promoted by hand. Fix: bots with no CLV data at all are scored on a
     higher ROI bar instead (PROMOTE_PAPER_ROI_NO_CLV_PCT).

  Also: DEMOTE only ever applied to calibrated bots, so a beta bot bleeding
  paper money (bot_summer_specialist, -56pct ROI) stayed beta indefinitely —
  and beta is visible to every signed-in user on /picks. DEMOTE now applies
  at any maturity once there is enough paper evidence.

Verdict thresholds (verdict window = 60d):
  PROMOTE  (real path)   real n >= 20  AND real ROI > +10pct AND paper CLV > +5pct
           (paper path)  paper n >= 100 AND paper ROI > +3pct AND paper CLV > +3pct
           (paper, no-CLV bots)        paper n >= 100 AND paper ROI > +8pct
           ... all three additionally require maturity != calibrated
  DEMOTE   (real path)   maturity == calibrated AND real n >= 20 AND real ROI < -5pct
           (paper path)  paper n >= 100 AND paper ROI < -10pct
  HOLD     everything else (including not enough evidence either way)

Thresholds are judgement calls, not derived numbers. The paper bars are set
deliberately LOWER than the real-money bars because paper ROI is measured
without slippage, limits, or the selection bias documented in
[[project_self_use_validation_phase3]] — a paper edge is weaker evidence per
unit, so the answer is to demand more of it (n >= 100 vs n >= 20), not to
demand a bigger number. Revisit once a paper-promoted bot has 60d of real
money behind it and the two can be compared directly.

PROMOTION IS STILL MANUAL. This job only emits a verdict and emails it. The
`bots.maturity_label` change is a hand-written migration (see
`174_promote_inplay_l_calibrated.sql`) so that a threshold bug can never
silently hand a bot the real-money key.

Schema notes (verified 2026-06-15, extended 2026-08-28):
  - bots table is soccer-only (CS2 uses cs2_real_bets keyed by bot_name string)
  - simulated_bets.clv is `our_odds / closing_pinnacle - 1` (fraction, not pct)
  - real_bets has bot_id FK to bots(id); placed_at is the timestamp
  - simulated_bets uses created_at; result IN ('pending','won','lost','void')
  - shadow_bets_unique is a VIEW over shadow_bets, DISTINCT ON
    (bot_id, match_id, market, selection) keeping the earliest pick_time —
    raw shadow_bets holds one row per TIMING COHORT, so querying it directly
    multiplies every pick by ~20 and inflates n. Always use the view.
  - shadow_bets.clv_pinnacle is the Pinnacle-anchored CLV (fraction); the
    plain `clv` column is anchored on the pick's own book and is not
    comparable to simulated_bets.clv. Use clv_pinnacle, fall back to clv.
  - MARKET VOCABULARIES DIFFER BETWEEN THE TWO PAPER LEDGERS. shadow_bets
    stores '1X2' / 'O/U' (uppercase) alongside 'over_under_25' /
    'over_under_35', while simulated_bets stores '1x2' / 'o/u'. Joining the
    two on market without normalising silently returns zero overlap. This is
    why _fetch_paper_bets picks ONE source per bot instead of unioning them.
"""
from __future__ import annotations
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2  # noqa: E402

# Verdict thresholds — pinned by smoke test so they can't drift silently
VERDICT_WINDOW_DAYS    = 60
WINDOW_DAYS            = (30, 60, 90)

# --- Real-money path (original gate, kept as the fast lane) ------------------
MIN_BETS_FOR_VERDICT   = 20     # min settled REAL bets for the real-money path
PROMOTE_REAL_ROI_PCT   = 10.0   # real ROI > +10%
PROMOTE_SIM_CLV_PCT    = 5.0    # paper CLV > +5%
DEMOTE_REAL_ROI_PCT    = -5.0   # real ROI < -5%

# --- Paper path (BOT-GATE-REACHABLE 2026-08-28) ------------------------------
# The only path a non-calibrated bot can actually walk: real_bets is gated on
# maturity=calibrated, so paper evidence is the ONLY evidence a beta bot can
# accumulate. Higher n, lower ROI/CLV bars — see module docstring for why.
MIN_PAPER_BETS_FOR_VERDICT  = 100    # min settled paper picks (sim OR shadow)
PROMOTE_PAPER_ROI_PCT       = 3.0    # paper ROI > +3%
PROMOTE_PAPER_CLV_PCT       = 3.0    # paper CLV > +3%
# Bots with NO closing line at all (inplay_*) cannot produce CLV. Rather than
# leaving them permanently ineligible, score them on a stiffer ROI bar alone.
PROMOTE_PAPER_ROI_NO_CLV_PCT = 8.0   # paper ROI > +8% when CLV is unavailable
DEMOTE_PAPER_ROI_PCT        = -10.0  # paper ROI < -10% at any maturity
# A high-frequency bot can reach n=100 in under a week — bot_pin_1x2_home_v1
# hit 104 settled picks in 6 days on its first run. Sample size alone is not
# evidence of durability: one hot week of a single market regime clears n=100
# without saying anything about whether the edge survives. Require the picks to
# SPAN at least this many days as well, so promotion needs persistence, not
# just volume. Demotion is deliberately NOT span-gated — a bot losing money
# fast should be caught fast.
MIN_PAPER_SPAN_DAYS         = 21


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _fetch_active_bots(cur):
    # CS2-FILTER (2026-06-21): exclude `bot_cs2_*` rows. CS2 bots write
    # bets to `cs2_real_bets` (separate table from soccer's `simulated_bets`
    # / `real_bets`), so they show 0/0/0 in this report and just pollute
    # the dormant footer. This is a soccer-only review by design.
    cur.execute("""
        SELECT id, name, maturity_label
        FROM bots
        WHERE is_active = true
          AND retired_at IS NULL
          AND name NOT LIKE 'bot_cs2_%'
        ORDER BY maturity_label, name
    """)
    return cur.fetchall()


def _fetch_sim_bets(cur, bot_id):
    """Return list of (created_at, result, stake, pnl, clv) for last 90d settled bets.

    simulated_bets.result uses the bet_result enum which only carries
    won/lost/void/pending — no half_won/half_lost (those exist on real_bets'
    TEXT result column).
    """
    cur.execute("""
        SELECT created_at, result, stake, pnl, clv
        FROM simulated_bets
        WHERE bot_id = %s
          AND result IN ('won','lost','void')
          AND created_at >= NOW() - INTERVAL '90 days'
    """, (bot_id,))
    return cur.fetchall()


def _fetch_shadow_bets(cur, bot_id):
    """BOT-GATE-REACHABLE (2026-08-28) — same tuple shape as _fetch_sim_bets,
    read from the shadow ledger.

    `bot_sweep_*`, `bot_pin_*` and `bot_coolbet_value_v1` never write to
    `simulated_bets`; they write to `shadow_bets`. Before this existed they
    reported as DORMANT with zero activity, which is how `bot_pin_1x2_home_v1`
    accumulated 104 settled picks at +13.1% ROI without ever appearing in a
    weekly review.

    MUST read the `shadow_bets_unique` VIEW, not the base table: shadow_bets
    holds one row per timing cohort (~20 per pick), so the base table inflates
    n by ~20x and skews ROI toward whichever cohorts settled.

    CLV: prefer `clv_pinnacle` (Pinnacle-anchored, comparable to
    simulated_bets.clv). Falls back to `clv`, which is anchored on the pick's
    own book, when the Pinnacle anchor is missing.
    """
    cur.execute("""
        SELECT created_at, result, stake, pnl,
               COALESCE(clv_pinnacle, clv) AS clv
        FROM shadow_bets_unique
        WHERE bot_id = %s
          AND result IN ('won','lost','void')
          AND created_at >= NOW() - INTERVAL '90 days'
    """, (bot_id,))
    return cur.fetchall()


def _fetch_paper_bets(cur, bot_id):
    """Return (rows, source_label) for the bot's paper ledger.

    ONE source per bot, never a union. The two ledgers use different market
    vocabularies — shadow_bets says '1X2' / 'O/U' / 'over_under_25' where
    simulated_bets says '1x2' / 'o/u' — so a naive union double-counts the
    same pick under two spellings. (`bot_v10_all` has 357 sim rows and 319
    shadow rows covering 85 of the same 93 matches in the last 30d; joining
    them on market returns zero overlap, which looks like two independent
    ledgers and is not.)

    Pipeline bots write to simulated_bets and additionally re-record into
    shadow_bets as part of the timing-cohort experiment, so simulated_bets is
    the canonical ledger whenever it has rows. Shadow-only bots fall through
    to the shadow ledger.
    """
    sim = _fetch_sim_bets(cur, bot_id)
    if sim:
        return sim, "sim"
    shadow = _fetch_shadow_bets(cur, bot_id)
    if shadow:
        return shadow, "shadow"
    return [], "none"


def _fetch_real_bets(cur, bot_id):
    """Return list of (placed_at, result, stake, pnl) for last 90d settled bets."""
    cur.execute("""
        SELECT placed_at, result, stake, pnl
        FROM real_bets
        WHERE bot_id = %s
          AND result IN ('won','lost','void','half_won','half_lost')
          AND placed_at >= NOW() - INTERVAL '90 days'
    """, (bot_id,))
    return cur.fetchall()


def _window_metrics(rows, ts_idx, days, now):
    """Bucket rows whose timestamp is within `days` of now. Return
    (n, hit_rate_pct_or_None, roi_pct_or_None, clv_pct_or_None)."""
    cutoff = now.timestamp() - days * 86400
    in_window = [r for r in rows if r[ts_idx].timestamp() >= cutoff]
    n = len(in_window)
    if n == 0:
        return (0, None, None, None)

    # ROI: sum(pnl) / sum(stake). Void bets count as stake=stake, pnl=0
    total_stake = sum(float(r[2]) for r in in_window if r[2] is not None)
    total_pnl   = sum(float(r[3]) for r in in_window if r[3] is not None)
    roi_pct = (total_pnl / total_stake * 100) if total_stake > 0 else None

    # Hit rate: won / (won + lost), exclude void/half
    decisive = [r for r in in_window if r[1] in ("won", "lost")]
    hits = sum(1 for r in decisive if r[1] == "won")
    hit_pct = (hits / len(decisive) * 100) if decisive else None

    # CLV (sim only — real_bets has no clv column). When the row tuple is
    # length 5 the 5th entry is clv, when 4 it's the real-bets shape.
    clv_pct = None
    if len(in_window[0]) == 5:
        clvs = [float(r[4]) for r in in_window if r[4] is not None]
        if clvs:
            clv_pct = sum(clvs) / len(clvs) * 100

    return (n, hit_pct, roi_pct, clv_pct)


def _paper_span_days(rows, days, now):
    """Days between the first and last paper pick inside the window. Used to
    stop a single high-volume week from clearing the n>=100 promotion bar."""
    cutoff = now.timestamp() - days * 86400
    ts = [r[0].timestamp() for r in rows if r[0].timestamp() >= cutoff]
    if len(ts) < 2:
        return 0.0
    return (max(ts) - min(ts)) / 86400.0


def _has_clv_data(paper_rows):
    """True when the bot produces CLV at all. `inplay_*` bots never do — there
    is no closing line for an in-play pick — so scoring them on a CLV bar
    makes them permanently ineligible for promotion regardless of results."""
    return any(len(r) == 5 and r[4] is not None for r in paper_rows)


def _verdict(maturity, paper60, real60, has_clv, paper_span_days):
    """Compute PROMOTE / DEMOTE / HOLD from 60d window metrics.

    paper60, real60 are tuples (n, hit_pct, roi_pct, clv_pct).
    has_clv is False for bots that structurally cannot produce CLV.
    paper_span_days is the first-to-last spread of paper picks in the window.

    Returns (verdict, reason) — reason is a short human string printed next to
    the verdict so the operator can see WHICH rule fired without re-deriving
    it from the numbers.
    """
    real_n, _, real_roi, _ = real60
    paper_n, _, paper_roi, paper_clv = paper60

    is_calibrated = maturity == "calibrated"

    # ---- DEMOTE first: a bot losing money outranks any promotion case ------
    # Real-money path — a calibrated bot bleeding actual money.
    if (is_calibrated
            and real_n >= MIN_BETS_FOR_VERDICT
            and real_roi is not None
            and real_roi < DEMOTE_REAL_ROI_PCT):
        return "DEMOTE", f"real ROI {real_roi:+.1f}% < {DEMOTE_REAL_ROI_PCT:+.0f}% on n={real_n}"

    # Paper path — applies at ANY maturity. Before BOT-GATE-REACHABLE this
    # branch was calibrated-only, so a beta bot at -56% ROI stayed beta (and
    # therefore stayed visible to every signed-in user on /picks) forever.
    if (paper_n >= MIN_PAPER_BETS_FOR_VERDICT
            and paper_roi is not None
            and paper_roi < DEMOTE_PAPER_ROI_PCT):
        return "DEMOTE", f"paper ROI {paper_roi:+.1f}% < {DEMOTE_PAPER_ROI_PCT:+.0f}% on n={paper_n}"

    # ---- PROMOTE: calibrated is the top rung, nothing to promote into ------
    if is_calibrated:
        return "HOLD", "already calibrated"

    # Real-money fast lane (the original gate). Only reachable for bots that
    # somehow have real bets despite the maturity gate — manual placements, or
    # history predating COOLBET_RECORD_ALLOWED_MATURITY being switched on.
    if (real_n >= MIN_BETS_FOR_VERDICT
            and real_roi is not None and real_roi > PROMOTE_REAL_ROI_PCT
            and paper_clv is not None and paper_clv > PROMOTE_SIM_CLV_PCT):
        return "PROMOTE", (f"real ROI {real_roi:+.1f}% on n={real_n} "
                           f"+ paper CLV {paper_clv:+.1f}%")

    # Paper path — the reachable one. Needs BOTH volume and persistence.
    if (paper_n >= MIN_PAPER_BETS_FOR_VERDICT
            and paper_span_days >= MIN_PAPER_SPAN_DAYS
            and paper_roi is not None):
        if has_clv:
            if paper_roi > PROMOTE_PAPER_ROI_PCT and paper_clv is not None and paper_clv > PROMOTE_PAPER_CLV_PCT:
                return "PROMOTE", (f"paper ROI {paper_roi:+.1f}% + CLV {paper_clv:+.1f}% "
                                   f"on n={paper_n}")
        else:
            # No closing line available for this bot family — stiffer ROI bar
            # stands in for the missing CLV confirmation.
            if paper_roi > PROMOTE_PAPER_ROI_NO_CLV_PCT:
                return "PROMOTE", (f"paper ROI {paper_roi:+.1f}% on n={paper_n} "
                                   f"(no-CLV bot, {PROMOTE_PAPER_ROI_NO_CLV_PCT:+.0f}% bar)")

    # ---- HOLD, with the binding constraint named ---------------------------
    if paper_n < MIN_PAPER_BETS_FOR_VERDICT:
        return "HOLD", f"paper n={paper_n} < {MIN_PAPER_BETS_FOR_VERDICT}"
    if paper_span_days < MIN_PAPER_SPAN_DAYS:
        return "HOLD", (f"paper picks span {paper_span_days:.0f}d < "
                        f"{MIN_PAPER_SPAN_DAYS}d (n={paper_n} but too recent)")
    if paper_roi is None:
        return "HOLD", "no settled paper ROI"
    if has_clv and (paper_clv is None or paper_clv <= PROMOTE_PAPER_CLV_PCT):
        clv_str = f"{paper_clv:+.1f}%" if paper_clv is not None else "n/a"
        return "HOLD", f"paper CLV {clv_str} <= {PROMOTE_PAPER_CLV_PCT:+.0f}%"
    bar = PROMOTE_PAPER_ROI_NO_CLV_PCT if not has_clv else PROMOTE_PAPER_ROI_PCT
    return "HOLD", f"paper ROI {paper_roi:+.1f}% <= {bar:+.0f}%"


def _fmt_pct(v):
    return f"{v:+6.1f}%" if v is not None else "      —"


def _fmt_int(v):
    return f"{v:>5d}" if v is not None else "    —"


def _divergence(sim, real):
    """sim_roi - real_roi in percentage points. None if either side missing."""
    if sim[2] is None or real[2] is None:
        return None
    return sim[2] - real[2]


def _fmt_div(v):
    return f"{v:+6.1f}pp" if v is not None else "       —"


def _print_calibration_section(cur, ran_at):
    """Per-bin calibration audit on last 60d of settled simulated_bets.
    Surfaces miscalibration regressions within 7 days instead of 26 (the
    time it took to find GLOBAL-PLATT-OVERCONFIDENCE).

    Bins calibrated_prob into 10pp buckets, compares predicted vs actual
    win rate, computes 1.96σ confidence interval for the gap (binomial
    standard error). Flags bins where |gap| > 1.96 × stderr AND gap > 5pp
    (5pp threshold filters noise; 1.96σ filters small-sample variance)."""
    print("================================= CALIBRATION (60d, all markets) ==================================")
    cur.execute("""
        SELECT calibrated_prob::float, (result::text='won')::int AS won, stake::float, pnl::float
        FROM simulated_bets
        WHERE calibrated_prob IS NOT NULL
          AND result::text IN ('won','lost')
          AND created_at >= NOW() - INTERVAL '60 days'
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (no settled bets in last 60d)")
        print()
        return

    import math
    # 10pp buckets from 20% to 90% — outside this range samples are too
    # thin for the 1.96σ check to be meaningful.
    buckets = [(0.20, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.60),
               (0.60, 0.70), (0.70, 0.80), (0.80, 0.90)]
    print(f"  {'bin':10} {'n':>5} {'pred%':>7} {'actual%':>8} {'gap pp':>9} "
          f"{'stderr':>7} {'flag':>5} {'roi%':>8}")
    any_flagged = False
    for lo, hi in buckets:
        in_b = [(p, y, s, pn) for p, y, s, pn in rows if lo <= p < hi]
        n = len(in_b)
        if n < 5:
            continue
        pred = sum(p for p, _, _, _ in in_b) / n
        actual = sum(y for _, y, _, _ in in_b) / n
        gap = pred - actual
        # Binomial standard error on actual.
        stderr = math.sqrt(actual * (1 - actual) / n) if n > 1 else 0.0
        significant = abs(gap) > max(0.05, 1.96 * stderr)
        flag = "⚠️" if significant else ""
        if significant:
            any_flagged = True
        stake_sum = sum(s for _, _, s, _ in in_b)
        pnl_sum = sum(pn for _, _, _, pn in in_b)
        roi = (pnl_sum / stake_sum * 100) if stake_sum > 0 else 0.0
        print(f"  {int(lo*100):2}-{int(hi*100):2}%      {n:>5} "
              f"{pred*100:>6.1f}% {actual*100:>7.1f}% {gap*100:>+7.1f}pp "
              f"{stderr*100:>5.1f}pp {flag:>5} {roi:>+7.1f}%")
    if any_flagged:
        print()
        print("  ⚠️ = predicted vs actual gap is statistically significant (|gap| > max(5pp, 1.96σ)) —")
        print("      this band is materially miscalibrated and likely leaking ROI. See PRIORITY_QUEUE.md →")
        print("      GLOBAL-PLATT-OVERCONFIDENCE or ISOTONIC-ACTIVATE-V20260621 for the latest mitigation.")
    print()


def _print_bot_block(name, maturity, verdict, reason, source,
                     sim30, sim60, sim90, real30, real60, real90):
    # `source` names WHICH paper ledger the numbers came from (sim vs shadow).
    # Without it a reader cannot tell whether a bot with no simulated_bets is
    # genuinely idle or simply writing to the other table — the exact
    # confusion that hid bot_pin_1x2_home_v1 for weeks.
    print(f"═══ {name}  ·  maturity={maturity}  ·  verdict={verdict}  ·  {reason}")
    print(f"  paper ledger: {source}")
    # Column widths must match the row format below exactly — this digest is
    # emailed inside <pre>, so a misaligned header is the whole table ruined.
    print("          |" + "----------- paper ----------".center(33, "-")
          + "|" + "------- real ------".center(24, "-") + "|")
    print(f"  window  | {'n':>5}  {'hit':>7}  {'ROI':>7}  {'CLV':>7} | {'n':>5}  {'hit':>7}  {'ROI':>7} |  divergence")
    for label, sim, real in (
        ("  30d   ", sim30, real30),
        ("  60d★  ", sim60, real60),  # ★ = verdict window
        ("  90d   ", sim90, real90),
    ):
        div = _divergence(sim, real)
        print(
            f"{label}  | {_fmt_int(sim[0])}  {_fmt_pct(sim[1])}  {_fmt_pct(sim[2])}  {_fmt_pct(sim[3])} "
            f"| {_fmt_int(real[0])}  {_fmt_pct(real[1])}  {_fmt_pct(real[2])} "
            f"|  {_fmt_div(div)}"
        )
    print()


def main():
    ran_at = datetime.now(timezone.utc)
    conn = _connect()
    cur = conn.cursor()

    bots = _fetch_active_bots(cur)

    print(f"Bot maturity review · {ran_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Verdict window: last {VERDICT_WINDOW_DAYS}d (★ in tables below)")
    print("Thresholds (maturity != calibrated for every PROMOTE path):")
    print(f"  PROMOTE  real   · real n >= {MIN_BETS_FOR_VERDICT} AND real ROI > +{PROMOTE_REAL_ROI_PCT:.0f}% AND paper CLV > +{PROMOTE_SIM_CLV_PCT:.0f}%")
    print(f"  PROMOTE  paper  · paper n >= {MIN_PAPER_BETS_FOR_VERDICT} AND paper ROI > +{PROMOTE_PAPER_ROI_PCT:.0f}% AND paper CLV > +{PROMOTE_PAPER_CLV_PCT:.0f}%")
    print(f"  PROMOTE  no-CLV · paper n >= {MIN_PAPER_BETS_FOR_VERDICT} AND paper ROI > +{PROMOTE_PAPER_ROI_NO_CLV_PCT:.0f}%  (inplay_* — no closing line exists)")
    print(f"           both paper paths also require picks spanning >= {MIN_PAPER_SPAN_DAYS}d (persistence, not just volume)")
    print(f"  DEMOTE   real   · calibrated AND real n >= {MIN_BETS_FOR_VERDICT} AND real ROI < {DEMOTE_REAL_ROI_PCT:+.0f}%")
    print(f"  DEMOTE   paper  · any maturity AND paper n >= {MIN_PAPER_BETS_FOR_VERDICT} AND paper ROI < {DEMOTE_PAPER_ROI_PCT:+.0f}%")
    print("  HOLD     else — the reason column names the binding constraint")
    print()
    print("Paper ledger = simulated_bets when the bot writes there, else shadow_bets_unique")
    print("(sweep/pin/coolbet-value bots are shadow-only). Promotion remains a MANUAL migration.")
    print()

    verdict_counts = defaultdict(int)
    actionable = []   # PROMOTE / DEMOTE go to the top
    held       = []   # HOLD bots with non-zero activity in the 90d window
    dormant    = []   # active in DB but zero PAPER (sim or shadow) AND zero real
                      # bets in 90d — e.g. CS2 bots writing to cs2_real_bets,
                      # or bots with filters too tight to fire. Collapsed into a
                      # one-liner so they don't drown the digest.
                      # BOT-GATE-REACHABLE 2026-08-28: shadow-only bots used to
                      # land here wrongly because only simulated_bets was read.

    for bot_id, name, maturity in bots:
        sim_rows, source = _fetch_paper_bets(cur, bot_id)
        real_rows = _fetch_real_bets(cur, bot_id)

        sim30  = _window_metrics(sim_rows,  0, 30, ran_at)
        sim60  = _window_metrics(sim_rows,  0, 60, ran_at)
        sim90  = _window_metrics(sim_rows,  0, 90, ran_at)
        real30 = _window_metrics(real_rows, 0, 30, ran_at)
        real60 = _window_metrics(real_rows, 0, 60, ran_at)
        real90 = _window_metrics(real_rows, 0, 90, ran_at)

        has_clv = _has_clv_data(sim_rows)
        span60  = _paper_span_days(sim_rows, VERDICT_WINDOW_DAYS, ran_at)
        verdict, reason = _verdict(maturity, sim60, real60, has_clv, span60)
        verdict_counts[verdict] += 1

        block = (name, maturity, verdict, reason, source,
                 sim30, sim60, sim90, real30, real60, real90)
        if verdict in ("PROMOTE", "DEMOTE"):
            actionable.append(block)
        elif sim90[0] == 0 and real90[0] == 0:
            # Truly silent — no sim picks, no real bets across the full 90d
            # window. Anything firing at all (even rarely) stays in `held` so
            # we don't accidentally hide a low-volume signal.
            dormant.append((name, maturity))
        else:
            held.append(block)

    n_dormant = len(dormant)
    print(f"=== HEADLINE ===  {verdict_counts['PROMOTE']} PROMOTE · {verdict_counts['DEMOTE']} DEMOTE · "
          f"{verdict_counts['HOLD']} HOLD ({n_dormant} dormant) · {len(bots)} active bots")
    print()

    # CALIBRATION-ECE-BY-BIN (2026-06-21): early-warning surface for the
    # next GLOBAL-PLATT-OVERCONFIDENCE-style regression. Tonight's audit
    # found the 50-70% calibrated_prob band leaking $5,800/yr but it took
    # 26 days from the v20260607 promotion to surface — would have been
    # 7 days max if this section had existed.
    _print_calibration_section(cur, ran_at)

    if actionable:
        print("============================ ACTIONABLE (PROMOTE / DEMOTE) ============================")
        print()
        for b in actionable:
            _print_bot_block(*b)
    else:
        print("(no PROMOTE / DEMOTE verdicts this week — all bots HOLD)")
        print()

    print("============================== HOLD (no action needed) ===============================")
    print()
    for b in held:
        _print_bot_block(*b)

    # Dormant footer — one-liner per maturity tier so the digest stays compact
    # but the operator can still see which bots aren't firing at all. Common
    # causes: CS2 bots using cs2_real_bets (not in this report), filters too
    # tight, recently activated and not yet pickup.
    if dormant:
        print("============================ DORMANT (zero activity in 90d) ===========================")
        print("(no paper picks in simulated_bets OR shadow_bets_unique AND no real bets in the")
        print(" last 90 days — likely CS2 bots writing to cs2_real_bets, or live bots whose")
        print(" filters caught nothing this window)")
        print()
        by_maturity = defaultdict(list)
        for name, maturity in dormant:
            by_maturity[maturity or "unlabelled"].append(name)
        for maturity in sorted(by_maturity):
            names = sorted(by_maturity[maturity])
            print(f"  {maturity:14s} ({len(names):2d}): {', '.join(names)}")
        print()

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
