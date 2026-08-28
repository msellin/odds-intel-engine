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
     scored with a t-test (see the gate constants below).

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
     ROI t-test at a much larger n instead.

  Also: DEMOTE only ever applied to calibrated bots, so a beta bot bleeding
  paper money (bot_summer_specialist, -56pct ROI) stayed beta indefinitely —
  and beta is visible to every signed-in user on /picks. DEMOTE now applies
  at any maturity once there is enough paper evidence.

Verdict thresholds (verdict window = 60d) — see BOT-GATE-TSTAT below:
  basis    de-vigged Pinnacle CLV once n >= CLV_MIN_N (100), else per-bet ROI
           once n >= MIN_SETTLED_FOR_DECISION (200)
  PROMOTE  t >= +1.65 AND maturity != calibrated AND >= 14d observed
  DEMOTE   t <= -1.65, at ANY maturity
  DEMOTE   real-money tripwire: calibrated AND real n >= 20 AND real ROI < -5%
  HOLD     everything else; the digest names the binding constraint per bot

BOT-GATE-TSTAT (2026-08-28, same day as the above) — the first cut of the paper
path used RAW thresholds (ROI > +3%, CLV > +3%, DEMOTE at ROI < -10%). That was
wrong, and SHADOW-PROMOTION-GATE-2026-08-26 had already proved why for the
sibling gate on /admin/shadow-bots: a raw level is cleared whenever noise lands
above it, and raising n does not fix it. Simulated at n=100 against the odds
pool these bots actually bet at, the raw version demoted a BREAK-EVEN bot 24.4%
of the time and a genuinely +5% bot 14.7% of the time — because per-bet ROI SD
is 1.341, so a -10% threshold is a t of -0.75. The t-gate holds both error rates
at the nominal ~5%. Thresholds are now SHARED with the admin page; smoke pins
the pairing so the two surfaces cannot drift apart.

Point-in-time replay over the full multi-book archive (2026-04-28 -> 2026-08-26,
scripts/lineshop_replay.py) says none of the three line-shop bots is yet
significant, which is exactly what the gate now reports rather than promoting
them on a flattering ROI:
    bot_pin_1x2_home_v1   n=583  ROI +6.91%  t=+1.20
    bot_sweep_ou35_v1     n=379  ROI +1.77%  t=+0.29
    bot_sweep_ou25_v1     n=365  ROI +1.18%  t=+0.21
Note also that the two sweep bots CLAIM ~+6.5% edge and realise ~+1.5%; only
bot_pin_1x2_home_v1's claimed edge (+6.9%) matches what it earns (+6.91%).

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

# --- Paper path (BOT-GATE-REACHABLE 2026-08-28, revised same day) ------------
# The only path a non-calibrated bot can actually walk: real_bets is gated on
# maturity=calibrated, so paper evidence is the ONLY evidence a beta bot can
# accumulate.
#
# REVISION (BOT-GATE-TSTAT): the first cut of this path used raw thresholds
# (paper ROI > +3%, paper CLV > +3%, DEMOTE at ROI < -10%). That was wrong, and
# SHADOW-PROMOTION-GATE-2026-08-26 had already proved why for the sibling gate
# on /admin/shadow-bots: a raw ROI level is close to uninformative because it is
# cleared whenever noise lands above it, and raising n does not fix it — at
# n=2000 a break-even bot still promotes 17% of the time.
#
# Simulated against the empirical odds pool these bots actually bet at (n=740,
# mean odds 2.95, 20k trials at n=100), the raw-threshold version behaved like:
#
#     true ROI  true CLV   promote   DEMOTE
#          0%        0%       0.0%    24.4%   <- break-even bot demoted 1 in 4
#         +5%       +5%      54.1%    14.7%   <- GOOD bot demoted 1 in 7
#
# The ROI leg is the culprit: per-bet ROI SD is 1.341, so at n=100 the standard
# error is ~13pp and a -10% threshold is a t of -0.75. That is a coin flip, and
# demotion is the consequential direction — it strips a bot off /picks.
#
# The same t-statistic gate the admin page uses fixes it outright:
#
#     true ROI  true CLV   promote   DEMOTE
#          0%        0%       5.0%     5.1%   <- exactly the nominal 5% per side
#         +5%       +5%     100.0%     0.0%
#         -5%       -3%       0.0%    95.5%
#
# (The 100% power figure is idealised — it assumes independent CLV draws at the
# documented per-bet SD of 0.090. Real picks correlate within a matchday, so
# read it as an upper bound on power, not a promise.)
#
# THESE CONSTANTS MUST MATCH odds-intel-web/src/app/(app)/admin/shadow-bots/page.tsx.
# Two gates deciding the same question with different numbers is how a bot ends
# up promoted on one surface and retired on the other. Smoke pins the pairing.
PROMOTE_T                   = 1.65   # one-sided 5% test
RETIRE_T                    = -1.65
CLV_MIN_N                   = 100    # CLV converges ~222x faster than ROI
MIN_SETTLED_FOR_DECISION    = 200    # fallback when the bot has no CLV anchor
MIN_DAYS_FOR_DECISION       = 14     # two weekend cycles

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


def _t_stat(values):
    """One-sample t = mean / standard error. None when there is nothing to
    test. This is the gating statistic on both surfaces — keep it identical to
    the tStat/clvTStat computation in the admin shadow-bots page."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = (var / n) ** 0.5
    if se <= 0:
        return None
    return mean / se


def _window_rows(rows, days, now):
    cutoff = now.timestamp() - days * 86400
    return [r for r in rows if r[0].timestamp() >= cutoff]


def _gate_inputs(paper_rows, days, now):
    """Return (gate_t, basis, n_basis, observation_days) for the verdict.

    Decide on CLV where we have it. CLV's per-bet SD is 0.090 against ROI's
    1.341 (ANALYSIS_GOTCHAS #8), so CLV needs ~222x fewer bets for the same
    precision — CLV_MIN_N=100 is already past the ~78 needed for +/-2%, while
    ROI needs MIN_SETTLED_FOR_DECISION=200 and is still far slower.

    Bots on markets Pinnacle does not quote (BTTS above all) and every inplay_*
    bot have no CLV anchor at all and fall back to the ROI gate. That is a real
    cost of betting an unanchored market and the digest names it rather than
    hiding it. It also replaces the old special-case "no-CLV bots get a stiffer
    raw ROI bar" hack, which was itself a raw threshold and had the same flaw.
    """
    in_window = _window_rows(paper_rows, days, now)
    if not in_window:
        return None, "none", 0, 0.0

    ts = [r[0].timestamp() for r in in_window]
    observation_days = (max(ts) - min(ts)) / 86400.0 if len(ts) > 1 else 0.0

    clvs = [float(r[4]) for r in in_window if len(r) == 5 and r[4] is not None]
    if len(clvs) >= CLV_MIN_N:
        return _t_stat(clvs), "clv", len(clvs), observation_days

    # ROI fallback: per-bet return is (odds-1) on a win, -1 on a loss. We store
    # stake and pnl rather than odds, so the per-bet return is pnl/stake.
    rets = [float(r[3]) / float(r[2])
            for r in in_window
            if r[1] in ("won", "lost") and r[2] and float(r[2]) > 0 and r[3] is not None]
    if len(rets) >= MIN_SETTLED_FOR_DECISION:
        return _t_stat(rets), "roi", len(rets), observation_days

    n_have = len(clvs) if clvs else len(rets)
    basis = "clv" if clvs else "roi"
    return None, basis, n_have, observation_days


def _verdict(maturity, gate_t, basis, n_basis, observation_days, real60):
    """Compute PROMOTE / DEMOTE / HOLD.

    Returns (verdict, reason) — reason names WHICH rule fired, or which
    constraint binds, so the operator can act without re-deriving it.
    """
    real_n, _, real_roi, _ = real60
    is_calibrated = maturity == "calibrated"

    # ---- Real-money tripwire ----------------------------------------------
    # Deliberately NOT a statistical verdict: at n=20 the ROI standard error is
    # ~30pp, so "real ROI < -5%" is a t of about -0.17 and will fire on noise.
    # It is kept anyway because the loss function is asymmetric — a false
    # DEMOTE costs a calibrated bot nothing but paper trading, while a false
    # negative costs actual money every day it persists. Trigger-happy is the
    # correct bias here, and calling it a tripwire rather than a verdict is the
    # honest way to say so.
    if (is_calibrated
            and real_n >= MIN_BETS_FOR_VERDICT
            and real_roi is not None
            and real_roi < DEMOTE_REAL_ROI_PCT):
        return "DEMOTE", (f"real-money tripwire: ROI {real_roi:+.1f}% on n={real_n} "
                          f"(not a significance test — see docstring)")

    # ---- Not enough evidence to decide ------------------------------------
    if gate_t is None:
        need = CLV_MIN_N if basis == "clv" else MIN_SETTLED_FOR_DECISION
        label = "with CLV" if basis == "clv" else "settled (no CLV anchor)"
        return "HOLD", f"{n_basis}/{need} {label}"
    if observation_days < MIN_DAYS_FOR_DECISION:
        return "HOLD", (f"{observation_days:.0f}/{MIN_DAYS_FOR_DECISION} days observed "
                        f"(n={n_basis} but too recent)")

    # ---- The gate ----------------------------------------------------------
    if gate_t <= RETIRE_T:
        # Applies at ANY maturity. Before BOT-GATE-REACHABLE this was
        # calibrated-only, so a losing beta bot stayed beta — and beta is
        # visible to every signed-in user on /picks.
        return "DEMOTE", f"{basis} t={gate_t:+.2f} <= {RETIRE_T:+.2f} on n={n_basis}"
    if gate_t >= PROMOTE_T:
        if is_calibrated:
            return "HOLD", f"already calibrated ({basis} t={gate_t:+.2f})"
        return "PROMOTE", f"{basis} t={gate_t:+.2f} >= {PROMOTE_T:+.2f} on n={n_basis}"

    return "HOLD", f"{basis} t={gate_t:+.2f} inconclusive on n={n_basis}"


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
    print("Gate: a one-sided t-test on the metric that converges, NOT a raw ROI level.")
    print(f"  basis    · de-vigged Pinnacle CLV once n >= {CLV_MIN_N}; else per-bet ROI once n >= {MIN_SETTLED_FOR_DECISION}")
    print(f"             (CLV per-bet SD 0.090 vs ROI 1.341 — CLV needs ~222x fewer bets)")
    print(f"  PROMOTE  · t >= {PROMOTE_T:+.2f} AND maturity != calibrated AND >= {MIN_DAYS_FOR_DECISION}d observed")
    print(f"  DEMOTE   · t <= {RETIRE_T:+.2f} at ANY maturity")
    print(f"  DEMOTE   · real-money tripwire: calibrated AND real n >= {MIN_BETS_FOR_VERDICT} "
          f"AND real ROI < {DEMOTE_REAL_ROI_PCT:+.0f}% (deliberately not a significance test)")
    print("  HOLD     · else — the reason column names the binding constraint")
    print()
    print("Thresholds are shared with /admin/shadow-bots. A raw ROI gate was measured at")
    print("24% false-demote on a break-even bot; the t-gate holds both error rates at ~5%.")
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

        gate_t, basis, n_basis, obs_days = _gate_inputs(
            sim_rows, VERDICT_WINDOW_DAYS, ran_at)
        verdict, reason = _verdict(
            maturity, gate_t, basis, n_basis, obs_days, real60)
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
