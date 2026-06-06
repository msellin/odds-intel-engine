"""POST-CAL-IMPACT (2026-06-06): monitor distributional shift in placed bets
before vs after the 6 fresh Platt rows landed at 2026-06-06 10:35 UTC.

Markets calibrated today (cutoff 2026-06-06 10:35 UTC):
  1. asian_handicap_away -0.5  (50 samples, ECE 20.6% → 2.9%)
  2. btts_no                   (87 samples, ECE 11.7% → 8.1%)
  3. btts_yes                  (154 samples, ECE 16.1% → 1.7%)
  4. double_chance_1x          (63 samples, ECE 25.7% → 12.7%)
  5. double_chance_x2          (170 samples, ECE 22.7% → ~0%)
  6. inplay_e_under_25         (216 samples, ECE 21.9% → 8.9%) — applies in-play only

For each market we compare:
  * 14-day window BEFORE cutoff vs window AFTER cutoff
  * Count of bets placed
  * Mean model_probability (raw, pre-Platt)
  * Mean calibrated_prob (post-Platt — what edge was computed against)
  * Mean shift = calibrated_prob - model_probability
  * Mean edge_percent

A healthy update shows: slight shift in calibrated mean, bet counts stable.
A red flag: edge collapsed (bots stop picking that market) or calibrated mean
moved dramatically against actual hit rate.

Output:
  * stdout — human-readable summary
  * markdown file in dev/active/ for the priority queue close-out

Usage:
  PYTHONPATH=. python3 scripts/check_post_calibration_impact.py
  PYTHONPATH=. python3 scripts/check_post_calibration_impact.py --window-days 21
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

CUTOFF = datetime(2026, 6, 6, 10, 35, tzinfo=timezone.utc)


class Market(NamedTuple):
    cal_key: str
    sim_market: str
    sim_selection_filter: str
    sim_selection_args: tuple


MARKETS: list[Market] = [
    Market("asian_handicap_away -0.5", "asian_handicap", "selection = %s", ("away -0.5",)),
    Market("btts_no",                  "btts",           "LOWER(selection) = %s", ("no",)),
    Market("btts_yes",                 "btts",           "LOWER(selection) = %s", ("yes",)),
    Market("double_chance_1x",         "double_chance",  "LOWER(selection) = %s", ("1x",)),
    Market("double_chance_x2",         "double_chance",  "LOWER(selection) = %s", ("x2",)),
    Market("inplay_e_under_25",        "INPLAY",         "",                       ()),
]


def fetch_window_stats(cur, m: Market, since, until) -> dict:
    """Return aggregate stats for bets in [since, until)."""
    if m.sim_market == "INPLAY":
        # inplay_e calibration is applied during in-play bot inference; no
        # equivalent simulated_bets market exists yet (in-play flow logs
        # picks elsewhere). Mark as not-applicable so the report flags it.
        return {"count": None, "raw_mean": None, "cal_mean": None, "shift": None, "edge_mean": None}

    sql = f"""
        SELECT
            COUNT(*),
            AVG(model_probability),
            AVG(calibrated_prob),
            AVG(calibrated_prob - model_probability),
            AVG(edge_percent)
        FROM simulated_bets
        WHERE market = %s
          AND {m.sim_selection_filter}
          AND created_at >= %s
          AND created_at < %s
          AND model_probability IS NOT NULL
          AND calibrated_prob IS NOT NULL
    """
    cur.execute(sql, (m.sim_market, *m.sim_selection_args, since, until))
    row = cur.fetchone()
    count, raw_mean, cal_mean, shift, edge_mean = row
    return {
        "count":    int(count or 0),
        "raw_mean": float(raw_mean) if raw_mean is not None else None,
        "cal_mean": float(cal_mean) if cal_mean is not None else None,
        "shift":    float(shift) if shift is not None else None,
        "edge_mean": float(edge_mean) if edge_mean is not None else None,
    }


def fmt(v, pct=False, signed=False):
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:+.2f}pp" if signed else f"{v * 100:.2f}%"
    return f"{v:+.4f}" if signed else f"{v:.4f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=14,
                    help="Days of pre-cutoff history to compare (default 14)")
    ap.add_argument("--output", type=str,
                    default=f"dev/active/post-calibration-impact-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md",
                    help="Markdown output path")
    args = ap.parse_args()

    pre_since  = CUTOFF - timedelta(days=args.window_days)
    pre_until  = CUTOFF
    post_since = CUTOFF
    post_until = datetime.now(timezone.utc) + timedelta(seconds=1)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    md_rows = []
    md_rows.append("# Post-calibration impact — 6 markets calibrated 2026-06-06 10:35 UTC\n")
    md_rows.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    md_rows.append(f"Pre window:  {pre_since.strftime('%Y-%m-%d %H:%M')} → {pre_until.strftime('%Y-%m-%d %H:%M')} UTC")
    md_rows.append(f"Post window: {post_since.strftime('%Y-%m-%d %H:%M')} → {post_until.strftime('%Y-%m-%d %H:%M')} UTC")
    post_hours = (post_until - post_since).total_seconds() / 3600
    md_rows.append(f"Post elapsed: {post_hours:.1f} hours\n")
    md_rows.append("Note: `calibrated_prob` in PRE-cutoff bets reflects the OLD Platt params (or none for AH).")
    md_rows.append("So a shift in mean calibrated_prob is the combined effect of new params + any natural drift in raw model output.\n")

    print(f"\nPost-calibration impact — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Pre window:  {pre_since.strftime('%Y-%m-%d %H:%M')} → {pre_until.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Post window: {post_since.strftime('%Y-%m-%d %H:%M')} → {post_until.strftime('%Y-%m-%d %H:%M')} UTC ({post_hours:.1f}h)")
    print()

    header = ("Market", "n pre", "n post", "raw pre→post", "cal pre→post", "shift Δ", "edge pre→post")
    col_widths = (28, 7, 7, 18, 18, 10, 18)
    line = "  ".join(h.ljust(w) for h, w in zip(header, col_widths))
    print(line)
    print("-" * len(line))

    md_rows.append("| Market | n pre | n post | raw pre→post | cal pre→post | shift Δ | edge pre→post |")
    md_rows.append("|---|---:|---:|---|---|---|---|")

    for m in MARKETS:
        pre  = fetch_window_stats(cur, m, pre_since, pre_until)
        post = fetch_window_stats(cur, m, post_since, post_until)

        if pre["count"] is None:
            cells = (m.cal_key, "n/a", "n/a", "in-play only", "in-play only", "—", "—")
        else:
            raw_str = f"{fmt(pre['raw_mean'])} → {fmt(post['raw_mean'])}"
            cal_str = f"{fmt(pre['cal_mean'])} → {fmt(post['cal_mean'])}"
            shift_str = (
                f"{fmt(post['shift'], signed=True)}"
                if post["count"] > 0 else "—"
            )
            edge_str = f"{fmt(pre['edge_mean'], pct=True)} → {fmt(post['edge_mean'], pct=True)}"
            cells = (m.cal_key, str(pre["count"]), str(post["count"]),
                     raw_str, cal_str, shift_str, edge_str)

        row = "  ".join(str(c).ljust(w) for c, w in zip(cells, col_widths))
        print(row)
        md_rows.append("| " + " | ".join(str(c) for c in cells) + " |")

    print()
    md_rows.append("\n## Interpretation cues\n")
    md_rows.append("- **Count collapsed** (post << pre, accounting for window length): "
                   "calibration deflated probabilities enough to kill edge — bots stopped picking. "
                   "Investigate whether the new params over-correct.")
    md_rows.append("- **Shift Δ near zero**: calibration is barely changing what the bots see. "
                   "Probably fine; was it worth fitting?")
    md_rows.append("- **Edge mean swung negative**: calibration is correctly catching previously "
                   "over-confident picks. Healthy.")
    md_rows.append("- **AH n=0 post**: expected if the cohort doesn't hit `away -0.5` lines often. "
                   "Wait until the next bet lands.\n")
    md_rows.append("Re-run any time: `PYTHONPATH=. python3 scripts/check_post_calibration_impact.py`")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("\n".join(md_rows))
    print(f"Markdown report: {args.output}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
