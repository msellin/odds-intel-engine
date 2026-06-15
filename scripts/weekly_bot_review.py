"""BOT-MATURITY-REVIEW-WEEKLY (2026-06-15): Sunday rollup of per-bot performance
with PROMOTE / DEMOTE / HOLD verdict for each currently-active bot.

Origin: 2026-06-13 audit found `bot_high_alignment` (maturity=beta, -€56 over
50 real bets) had been auto-placing real money for days because the Mac
daemon lacked the maturity gate that Railway had. The decision "which bots
are trustworthy enough to spend real money on?" was manual and ad-hoc —
promotions happened reactively when someone happened to notice.

This script makes the decision explicit, scheduled, and auditable. Output
is plain text (column-aligned) so the email helper can wrap it in <pre>
without re-rendering.

Verdict thresholds (verdict window = 60d, sample n ≥ 20):
  PROMOTE  real ROI > +10%  AND  sim CLV > +5%  AND  maturity != calibrated
  DEMOTE   real ROI < -5%                       AND  maturity = calibrated
  HOLD     everything else (including n < 20 — not enough signal)

Thresholds are starting points — refine after the first 2-3 weeks of runs.

Schema notes (verified 2026-06-15):
  - bots table is soccer-only (CS2 uses cs2_real_bets keyed by bot_name string)
  - simulated_bets.clv is `our_odds / closing_pinnacle - 1` (fraction, not %)
  - real_bets has bot_id FK to bots(id); placed_at is the timestamp
  - simulated_bets uses created_at; result IN ('pending','won','lost','void','half_won','half_lost')
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
MIN_BETS_FOR_VERDICT   = 20
PROMOTE_REAL_ROI_PCT   = 10.0   # real ROI > +10%
PROMOTE_SIM_CLV_PCT    = 5.0    # sim CLV  > +5%
DEMOTE_REAL_ROI_PCT    = -5.0   # real ROI < -5%
WINDOW_DAYS            = (30, 60, 90)


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _fetch_active_bots(cur):
    cur.execute("""
        SELECT id, name, maturity_label
        FROM bots
        WHERE is_active = true
          AND retired_at IS NULL
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


def _verdict(maturity, sim60, real60):
    """Compute PROMOTE / DEMOTE / HOLD from 60d window metrics.
    sim60, real60 are tuples (n, hit_pct, roi_pct, clv_pct)."""
    real_n, _, real_roi, _ = real60
    _,      _, _,        sim_clv = sim60

    if real_n < MIN_BETS_FOR_VERDICT:
        return "HOLD"  # not enough real-money signal yet

    # DEMOTE: calibrated bot bleeding real money
    if maturity == "calibrated" and real_roi is not None and real_roi < DEMOTE_REAL_ROI_PCT:
        return "DEMOTE"

    # PROMOTE: non-calibrated bot earning real money AND sim CLV confirms edge
    if (maturity != "calibrated"
        and real_roi is not None and real_roi > PROMOTE_REAL_ROI_PCT
        and sim_clv  is not None and sim_clv  > PROMOTE_SIM_CLV_PCT):
        return "PROMOTE"

    return "HOLD"


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


def _print_bot_block(name, maturity, verdict, sim30, sim60, sim90, real30, real60, real90):
    print(f"═══ {name}  ·  maturity={maturity}  ·  verdict={verdict}")
    print(f"  window  |  sim n  sim hit   sim ROI  sim CLV |  real n  real hit  real ROI |  divergence")
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
    print(f"Verdict window: last {VERDICT_WINDOW_DAYS}d (★ in tables below) · min n={MIN_BETS_FOR_VERDICT}")
    print(f"Thresholds:  PROMOTE if real ROI > +{PROMOTE_REAL_ROI_PCT:.0f}% AND sim CLV > +{PROMOTE_SIM_CLV_PCT:.0f}% AND maturity != calibrated")
    print(f"             DEMOTE  if real ROI < {DEMOTE_REAL_ROI_PCT:+.0f}% AND maturity = calibrated")
    print(f"             HOLD    else (including n < {MIN_BETS_FOR_VERDICT} — not enough real-money signal)")
    print()

    verdict_counts = defaultdict(int)
    actionable = []   # PROMOTE / DEMOTE go to the top
    held       = []

    for bot_id, name, maturity in bots:
        sim_rows  = _fetch_sim_bets(cur, bot_id)
        real_rows = _fetch_real_bets(cur, bot_id)

        sim30  = _window_metrics(sim_rows,  0, 30, ran_at)
        sim60  = _window_metrics(sim_rows,  0, 60, ran_at)
        sim90  = _window_metrics(sim_rows,  0, 90, ran_at)
        real30 = _window_metrics(real_rows, 0, 30, ran_at)
        real60 = _window_metrics(real_rows, 0, 60, ran_at)
        real90 = _window_metrics(real_rows, 0, 90, ran_at)

        verdict = _verdict(maturity, sim60, real60)
        verdict_counts[verdict] += 1

        block = (name, maturity, verdict, sim30, sim60, sim90, real30, real60, real90)
        if verdict in ("PROMOTE", "DEMOTE"):
            actionable.append(block)
        else:
            held.append(block)

    print(f"=== HEADLINE ===  {verdict_counts['PROMOTE']} PROMOTE · {verdict_counts['DEMOTE']} DEMOTE · {verdict_counts['HOLD']} HOLD · {len(bots)} active bots")
    print()

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

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
