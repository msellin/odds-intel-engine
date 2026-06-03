"""PIN-DRIFT-VETO Step 1: directional + news + magnitude + timing analysis.

Hypothesis: when Pinnacle drifts the implied prob AGAINST our pick before
kickoff AND we have no news_impact_score to explain it, we are at an
information disadvantage and our ROI suffers.

Method:
  For each settled 1X2 bet in last 60 days where pinnacle_line_move_<sel>
  is non-null:
    drift_for_pick    = pinnacle_line_move_<sel>_at_t6h
                        (positive = Pinnacle moved TOWARD our pick → good for us)
                        (negative = Pinnacle moved AWAY from our pick → bad for us)
    against_us        = drift_for_pick < 0
    abs_drift         = |drift_for_pick|
    news_explained    = |news_impact_score| > 0.01
    pick_time_bucket  = morning (timing_cohort='morning') vs refresh (everything else)

  Bucket and compute ROI / hit-rate / N / staked.

Output: prints a series of tables and writes a CSV with all rows for follow-up.
"""
from __future__ import annotations

import os
import sys
import csv
from pathlib import Path
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

# Allow running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402


# Selection → which line_move column matters for that bet.
SEL_TO_MOVE_COL = {
    "home": "pinnacle_line_move_home_at_t6h",
    "draw": "pinnacle_line_move_draw_at_t6h",
    "away": "pinnacle_line_move_away_at_t6h",
}


SQL = """
SELECT
    sb.id::text                                AS bet_id,
    sb.bot_id::text                            AS bot_id,
    sb.market,
    sb.selection,
    sb.stake,
    sb.pnl,
    sb.result::text                            AS result,
    sb.timing_cohort,
    sb.news_impact_score                       AS bet_news_impact,
    sb.pick_time,
    sb.clv_pinnacle,
    m.date                                     AS kickoff,
    l.name                                     AS league,
    l.country                                  AS country,
    mfv.pinnacle_line_move_home_at_t6h         AS move_home,
    mfv.pinnacle_line_move_draw_at_t6h         AS move_draw,
    mfv.pinnacle_line_move_away_at_t6h         AS move_away,
    mfv.news_impact_score                      AS mfv_news_impact,
    mfv.opening_implied_home                   AS opening_home
FROM simulated_bets sb
JOIN matches m ON m.id = sb.match_id
JOIN leagues l ON l.id = m.league_id
JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
WHERE sb.result::text IN ('won', 'lost')
  AND sb.market = '1x2'                       -- veto only applies to 1X2 for now
  AND m.date >= NOW() - INTERVAL '60 days'
  AND mfv.opening_implied_home IS NOT NULL
  AND (
      mfv.pinnacle_line_move_home_at_t6h IS NOT NULL
   OR mfv.pinnacle_line_move_draw_at_t6h IS NOT NULL
   OR mfv.pinnacle_line_move_away_at_t6h IS NOT NULL
  )
ORDER BY m.date DESC
"""


def aggregate(rows: list[dict[str, Any]]) -> tuple[int, float, float, float, float]:
    """Return (n, staked, pnl, roi_pct, hit_pct)."""
    if not rows:
        return (0, 0.0, 0.0, 0.0, 0.0)
    n = len(rows)
    staked = sum(float(r["stake"] or 0) for r in rows)
    pnl = sum(float(r["pnl"] or 0) for r in rows)
    wins = sum(1 for r in rows if r["result"] == "won")
    roi = (pnl / staked * 100) if staked > 0 else 0.0
    hit = (wins / n * 100) if n > 0 else 0.0
    return n, staked, pnl, roi, hit


def print_table(title: str, header: list[str], rows: list[list[str]]) -> None:
    print()
    print(f"━━━ {title} ━━━")
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0)) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print("─" * (sum(widths) + 2 * (len(widths) - 1)))
    for r in rows:
        print(fmt.format(*r))


def main() -> int:
    print("Querying 60 days of settled 1X2 bets with Pinnacle drift features...")
    rows = execute_query(SQL)
    if not rows:
        print("No rows found.")
        return 1

    enriched = []
    for r in rows:
        sel = r["selection"]
        move_col = SEL_TO_MOVE_COL.get(sel)
        if not move_col:
            continue
        # drift_for_pick: positive = Pinnacle moved toward our pick (good for us)
        # negative = Pinnacle moved away from our pick (bad for us — information disadvantage)
        move_map = {"home": r["move_home"], "draw": r["move_draw"], "away": r["move_away"]}
        drift_for_pick = move_map[sel]
        if drift_for_pick is None:
            continue
        drift_for_pick = float(drift_for_pick)
        abs_drift = abs(drift_for_pick)
        against_us = drift_for_pick < 0
        # News explained: either bet-time news_impact or match-level news_impact non-zero.
        bni = r.get("bet_news_impact")
        mni = r.get("mfv_news_impact")
        bni = float(bni) if bni is not None else 0.0
        mni = float(mni) if mni is not None else 0.0
        news_explained = abs(bni) > 0.01 or abs(mni) > 0.01
        cohort_raw = (r.get("timing_cohort") or "unknown").lower()
        is_morning = cohort_raw == "morning"

        r_out = dict(r)
        r_out["drift_for_pick"] = drift_for_pick
        r_out["abs_drift"] = abs_drift
        r_out["against_us"] = against_us
        r_out["news_explained"] = news_explained
        r_out["is_morning"] = is_morning
        enriched.append(r_out)

    print(f"Enriched {len(enriched)} bets (of {len(rows)} raw rows).")
    print()
    print("Conventions:")
    print("  drift_for_pick > 0  → Pinnacle moved TOWARD our pick (good CLV for us)")
    print("  drift_for_pick < 0  → Pinnacle moved AGAINST our pick (bad CLV for us)")
    print("  news_explained     → our pipeline has news_impact_score non-zero")
    print("  is_morning         → timing_cohort = 'morning' (no T-6h snapshot at bet time)")
    print()

    # ── Table 1: Direction × Magnitude ──
    buckets = [
        ("towards_us_<1pct",   lambda r: not r["against_us"] and r["abs_drift"] < 0.01),
        ("towards_us_1-3pct",  lambda r: not r["against_us"] and 0.01 <= r["abs_drift"] < 0.03),
        ("towards_us_3-5pct",  lambda r: not r["against_us"] and 0.03 <= r["abs_drift"] < 0.05),
        ("towards_us_>=5pct",  lambda r: not r["against_us"] and r["abs_drift"] >= 0.05),
        ("against_us_<1pct",   lambda r: r["against_us"] and r["abs_drift"] < 0.01),
        ("against_us_1-3pct",  lambda r: r["against_us"] and 0.01 <= r["abs_drift"] < 0.03),
        ("against_us_3-5pct",  lambda r: r["against_us"] and 0.03 <= r["abs_drift"] < 0.05),
        ("against_us_>=5pct",  lambda r: r["against_us"] and r["abs_drift"] >= 0.05),
    ]
    print_table(
        "Table 1: ROI by direction × magnitude (the load-bearing check)",
        ["bucket", "N", "staked", "pnl", "ROI", "hit%"],
        [
            [name, str(n), f"{s:>9.2f}", f"{p:>+9.2f}", f"{r:>+6.2f}%", f"{h:>5.1f}%"]
            for name, fn in buckets
            for n, s, p, r, h in [aggregate([x for x in enriched if fn(x)])]
        ],
    )

    # ── Table 2: against-us cohort × news-explained ──
    against_us_rows = [r for r in enriched if r["against_us"]]
    sub_buckets = [
        ("AGAINST + no news, <1pct",  lambda r: not r["news_explained"] and r["abs_drift"] < 0.01),
        ("AGAINST + no news, 1-3pct", lambda r: not r["news_explained"] and 0.01 <= r["abs_drift"] < 0.03),
        ("AGAINST + no news, 3-5pct", lambda r: not r["news_explained"] and 0.03 <= r["abs_drift"] < 0.05),
        ("AGAINST + no news, >=5pct", lambda r: not r["news_explained"] and r["abs_drift"] >= 0.05),
        ("AGAINST + news,    <1pct",  lambda r: r["news_explained"] and r["abs_drift"] < 0.01),
        ("AGAINST + news,    1-3pct", lambda r: r["news_explained"] and 0.01 <= r["abs_drift"] < 0.03),
        ("AGAINST + news,    3-5pct", lambda r: r["news_explained"] and 0.03 <= r["abs_drift"] < 0.05),
        ("AGAINST + news,    >=5pct", lambda r: r["news_explained"] and r["abs_drift"] >= 0.05),
    ]
    print_table(
        "Table 2: AGAINST-US cohort, split by whether news explained the move",
        ["bucket", "N", "staked", "pnl", "ROI", "hit%"],
        [
            [name, str(n), f"{s:>9.2f}", f"{p:>+9.2f}", f"{r:>+6.2f}%", f"{h:>5.1f}%"]
            for name, fn in sub_buckets
            for n, s, p, r, h in [aggregate([x for x in against_us_rows if fn(x)])]
        ],
    )

    # ── Table 3: against-us cohort × pick_time (morning vs refresh) ──
    print_table(
        "Table 3: AGAINST-US cohort, morning vs refresh (does morning have the leak?)",
        ["bucket", "N", "staked", "pnl", "ROI", "hit%"],
        [
            [f"{tag} + {mag}",
             str(n), f"{s:>9.2f}", f"{p:>+9.2f}", f"{r:>+6.2f}%", f"{h:>5.1f}%"]
            for tag, tag_fn in [("morning", lambda r: r["is_morning"]),
                                ("refresh", lambda r: not r["is_morning"])]
            for mag, mag_fn in [("<1pct",   lambda r: r["abs_drift"] < 0.01),
                                ("1-3pct",  lambda r: 0.01 <= r["abs_drift"] < 0.03),
                                ("3-5pct",  lambda r: 0.03 <= r["abs_drift"] < 0.05),
                                (">=5pct",  lambda r: r["abs_drift"] >= 0.05)]
            for n, s, p, r, h in [aggregate(
                [x for x in against_us_rows if tag_fn(x) and mag_fn(x)]
            )]
        ],
    )

    # ── Table 4: threshold sensitivity (what would each threshold actually save?) ──
    # The veto rule: skip if against_us AND not news_explained AND abs_drift >= threshold.
    veto_candidates = [r for r in enriched if r["against_us"] and not r["news_explained"]]
    all_n, all_s, all_p, all_roi, _ = aggregate(enriched)
    threshold_rows = []
    for t in [0.015, 0.020, 0.025, 0.030, 0.040, 0.050]:
        vetoed = [r for r in veto_candidates if r["abs_drift"] >= t]
        retained = [r for r in enriched if r not in vetoed]
        vn, vs, vp, vroi, _ = aggregate(vetoed)
        rn, rs, rp, rroi, _ = aggregate(retained)
        threshold_rows.append([
            f"{t:.3f}",
            str(vn), f"{vs:>9.2f}", f"{vp:>+9.2f}", f"{vroi:>+6.2f}%",
            str(rn), f"{rs:>9.2f}", f"{rp:>+9.2f}", f"{rroi:>+6.2f}%",
            f"{(rroi - all_roi):+6.2f}pp",
        ])
    print_table(
        "Table 4: Threshold sweep — veto if (against_us AND no_news AND abs_drift >= T)",
        ["thresh", "veto_N", "veto_$", "veto_pnl", "veto_ROI",
                  "kept_N", "kept_$", "kept_pnl", "kept_ROI", "Δ_vs_all"],
        threshold_rows,
    )

    # ── Write the full enriched CSV for the threshold-picking script ──
    out_csv = Path(__file__).resolve().parent.parent / "dev" / "active" / "pin_drift_veto_60d.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        cols = [
            "bet_id", "bot_id", "kickoff", "league", "country",
            "market", "selection", "stake", "pnl", "result",
            "timing_cohort", "is_morning",
            "drift_for_pick", "abs_drift", "against_us",
            "bet_news_impact", "mfv_news_impact", "news_explained",
            "clv_pinnacle",
        ]
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in enriched:
            writer.writerow({k: r.get(k) for k in cols})

    print()
    print(f"Wrote {len(enriched)} enriched rows to {out_csv}")
    print()
    print("Summary line:")
    print(f"  baseline 1X2 cohort 60d: N={all_n} staked={all_s:.2f} pnl={all_p:+.2f} ROI={all_roi:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
