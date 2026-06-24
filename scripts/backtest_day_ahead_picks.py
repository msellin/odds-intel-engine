"""
Day-ahead pick cadence backtest (DAY-AHEAD-V1).

Question: should OddsIntel publish picks the night before (T-24h..T-12h) instead
of through the day (current T-6h..T-30m)?

Approach (intentionally simple, directional, not perfect):

  1. Universe: settled calibrated picks since 2026-05-04 (1x2/OU/BTTS only,
     result IN (won, lost)). That is the ~624-bet cohort.
  2. For each pick, reconstruct max-of-N-books odds at each horizon window
     (T-24h, T-12h, T-6h, T-2h, T-30m) and at Pinnacle's closing snap.
  3. Re-apply the bet rule at each horizon: would calibrated_prob × max_odds - 1
     still be >= edge_threshold? If yes, simulate a bet at that horizon's MAX.
     If no, the pick is "missed" at that horizon.
  4. Compute ROI / coverage / CLV-vs-close per horizon and compare to the
     production baseline (the actually placed bets).
  5. Edge thresholds: default 5%, sensitivities at 3% and 7%.

If the data isn't there (e.g., T-24h coverage is sparse because the pipeline
doesn't snap that early), the script reports it plainly via the coverage_pct
column.

Outputs: stdout table + dev/active/day_ahead_backtest_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WINDOW_START_DEFAULT = "2026-05-04"
WINDOW_END_DEFAULT = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
STAKE_UNIT = 10.0

# Horizon windows: target hours-before-kickoff -> (window_low_h, window_high_h)
# Lower bound is "at least this far before kickoff", upper bound is "no further
# back than this". E.g. T-24h means we'll accept any snap in [22h..26h pre-KO].
# We widen later horizons because snapshot density drops far from kickoff.
HORIZONS: list[tuple[str, float, float]] = [
    # name,      window_low_h, window_high_h   (hours before kickoff)
    ("T-24h",   18.0,         30.0),
    ("T-12h",   10.0,         16.0),
    ("T-6h",    4.5,          8.0),
    ("T-2h",    1.5,          3.0),
    ("T-30m",   0.25,         1.25),
]

# Edge thresholds to evaluate.
EDGE_THRESHOLDS = [0.03, 0.05, 0.07]
DEFAULT_EDGE = 0.05

# Skip bets where odds-shopping at MAX gives a price more than 25% above the
# captured price — almost always means we matched stale or wrong selection
# rows (e.g. a different OU line). Same guardrail as backtest_fresh_pinnacle_clv.
MAX_ODDS_BLOWUP = 1.25


# ---------------------------------------------------------------------------
# Data pull
# ---------------------------------------------------------------------------

def _norm_market(m: str) -> str:
    """Collapse o/u + over_under_25 into a single key."""
    return "over_under_25" if m in ("o/u", "over_under_25") else m


def pull_picks(start: str, end: str, cohort: str) -> list[dict]:
    """Pull settled calibrated (or broader) picks in the window."""
    if cohort == "calibrated":
        maturity_clause = "b.maturity_label = 'calibrated'"
    elif cohort == "broad":
        maturity_clause = "b.maturity_label IN ('calibrated','beta','active')"
    else:
        raise ValueError(f"unknown cohort: {cohort}")

    rows = execute_query(
        f"""
        SELECT
          sb.id, sb.match_id::text AS match_id,
          sb.market, sb.selection,
          sb.odds_at_pick::float    AS placed_odds,
          sb.model_probability::float AS model_prob,
          sb.calibrated_prob::float   AS cal_prob,
          sb.edge_percent::float      AS edge_pct,
          sb.stake::float             AS stake,
          sb.pnl::float               AS pnl,
          sb.pick_time,
          sb.result::text             AS result,
          b.maturity_label            AS maturity,
          m.date                      AS kickoff,
          m.score_home, m.score_away,
          l.country, l.name AS league
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.created_at >= %s::date
          AND sb.created_at <  %s::date
          AND {maturity_clause}
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2','o/u','over_under_25','btts')
        ORDER BY sb.created_at ASC
        """,
        (start, end),
    )
    return rows


def pull_horizon_odds(match_ids: list[str], kickoffs: dict) -> dict:
    """For each (match_id, market, selection), produce per-horizon max odds.

    Returns dict keyed by (match_id, normalized_market, selection):
      {
        "horizons": { "T-24h": {"max": odds, "bk": "..."}|None, ... },
        "pin_close": odds | None,
        "any_close": odds | None,
      }

    Strategy: pull all relevant snaps in ONE chunked sweep, then bucketise
    each row into the horizon window(s) it falls inside.

    Closing line: latest pre-kickoff snap (+5min grace). Pinnacle preferred,
    otherwise any-book.
    """
    out: dict = {}
    if not match_ids:
        return out

    # Time-range filter: we only need snaps from T-30h pre-kickoff through
    # T+5min (to catch the closing window). Without this, the query pulls
    # every snapshot across all time for each match — explodes to 30M+ rows
    # and times out the pooler (exit 144 / statement timeout). With the
    # filter we get ~50-200 rows per (match, market, selection) — workable.
    CHUNK = 50
    from datetime import timedelta as _td
    for i in range(0, len(match_ids), CHUNK):
        ids = match_ids[i:i + CHUNK]
        # Compute per-chunk time window from the kickoffs we know about
        chunk_kos = [kickoffs[m] for m in ids if m in kickoffs]
        if not chunk_kos:
            continue
        win_start = (min(chunk_kos) - _td(hours=30)).isoformat()
        win_end = (max(chunk_kos) + _td(minutes=5)).isoformat()
        rows = execute_query(
            """
            SELECT match_id::text AS mid, market, selection, bookmaker,
                   odds::float AS odds, timestamp
              FROM odds_snapshots
             WHERE match_id = ANY(%s::uuid[])
               AND market IN ('1x2','o/u','over_under_25','btts')
               AND odds IS NOT NULL AND odds > 1.0
               AND (is_live IS NULL OR is_live = false)
               AND timestamp >= %s::timestamptz
               AND timestamp <= %s::timestamptz
            """,
            (ids, win_start, win_end),
        )
        for r in rows:
            ko = kickoffs.get(r["mid"])
            if not ko:
                continue
            ts = r["timestamp"]
            h_pre = (ko - ts).total_seconds() / 3600.0
            mkt = _norm_market(r["market"])
            key = (r["mid"], mkt, r["selection"])
            slot = out.setdefault(key, {
                "horizons": {h[0]: None for h in HORIZONS},
                "pin_close": None, "pin_close_ts": None,
                "any_close": None, "any_close_ts": None,
            })
            o = float(r["odds"])
            bk = r["bookmaker"] or ""

            # Bucket into horizon windows
            for name, lo, hi in HORIZONS:
                if lo <= h_pre <= hi:
                    cur = slot["horizons"][name]
                    if cur is None or o > cur["max"]:
                        slot["horizons"][name] = {"max": o, "bk": bk}

            # Closing line (latest at-or-before kickoff, +5min grace)
            if ts <= ko + timedelta(minutes=5):
                if bk == "Pinnacle" and (
                    slot["pin_close_ts"] is None or ts > slot["pin_close_ts"]
                ):
                    slot["pin_close"] = o
                    slot["pin_close_ts"] = ts
                if slot["any_close_ts"] is None or ts > slot["any_close_ts"]:
                    slot["any_close"] = o
                    slot["any_close_ts"] = ts
    return out


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------

def _clv_pct(placed: float | None, close: float | None) -> float | None:
    if not placed or not close or placed <= 1.0 or close <= 1.0:
        return None
    return (placed / close - 1.0) * 100


def run_horizon_backtest(picks: list[dict], odds_idx: dict, edge_thresh: float):
    """For each pick + each horizon, decide whether to place at horizon's MAX
    odds. Compute per-horizon aggregates.

    Production baseline = the actually-placed bets (placed_odds + real PnL).

    Edge rule: cal_prob * max_odds - 1 >= edge_thresh.

    "Missed" = pick exists in production but at horizon T-Nh either
      (a) no odds snap available, or
      (b) edge no longer meets threshold.
    """
    per_horizon: dict = {h[0]: {
        "n_fires": 0, "n_no_snap": 0, "n_below_edge": 0,
        "stake": 0.0, "pnl": 0.0,
        "clv_pin": [], "clv_any": [],
        "max_odds_samples": [],
    } for h in HORIZONS}

    # Production baseline: literally the placed bets we already have
    baseline = {
        "n": 0, "stake": 0.0, "pnl": 0.0,
        "clv_pin": [], "clv_any": [],
    }

    for p in picks:
        mkt = _norm_market(p["market"])
        key = (p["match_id"], mkt, p["selection"])
        slot = odds_idx.get(key)
        placed_odds = p["placed_odds"] or 0.0
        cal_prob = p["cal_prob"] or 0.0
        # Fallback to model_prob if calibrated_prob is missing
        if not cal_prob:
            cal_prob = p["model_prob"] or 0.0

        # ---- baseline: actually-placed ----
        baseline["n"] += 1
        baseline["stake"] += STAKE_UNIT
        won = p["result"] == "won"
        baseline_pnl = (placed_odds - 1.0) * STAKE_UNIT if won else -STAKE_UNIT
        baseline["pnl"] += baseline_pnl
        if slot:
            clv_p = _clv_pct(placed_odds, slot.get("pin_close"))
            clv_a = _clv_pct(placed_odds, slot.get("any_close"))
            if clv_p is not None:
                baseline["clv_pin"].append(clv_p)
            if clv_a is not None:
                baseline["clv_any"].append(clv_a)

        # ---- per-horizon: would the rule fire? ----
        if not slot or not cal_prob:
            # no odds slot at all -> all horizons miss with n_no_snap
            for h in HORIZONS:
                per_horizon[h[0]]["n_no_snap"] += 1
            continue

        for name, _, _ in HORIZONS:
            h_slot = slot["horizons"].get(name)
            if h_slot is None:
                per_horizon[name]["n_no_snap"] += 1
                continue
            mx = h_slot["max"]

            # Reject obviously wrong line matches (e.g. mismatched OU lines)
            if placed_odds and mx > placed_odds * MAX_ODDS_BLOWUP and mkt == "over_under_25":
                per_horizon[name]["n_no_snap"] += 1
                continue

            edge_at_h = cal_prob * mx - 1.0
            if edge_at_h < edge_thresh:
                per_horizon[name]["n_below_edge"] += 1
                continue

            # Fire at this horizon
            per_horizon[name]["n_fires"] += 1
            per_horizon[name]["stake"] += STAKE_UNIT
            pnl_at_h = (mx - 1.0) * STAKE_UNIT if won else -STAKE_UNIT
            per_horizon[name]["pnl"] += pnl_at_h
            per_horizon[name]["max_odds_samples"].append(mx)

            clv_p = _clv_pct(mx, slot.get("pin_close"))
            clv_a = _clv_pct(mx, slot.get("any_close"))
            if clv_p is not None:
                per_horizon[name]["clv_pin"].append(clv_p)
            if clv_a is not None:
                per_horizon[name]["clv_any"].append(clv_a)

    return baseline, per_horizon


def summarize(per_horizon: dict, total_picks: int, baseline: dict) -> dict:
    def _agg(name, d):
        n_fires = d["n_fires"]
        coverage = 100.0 * n_fires / total_picks if total_picks else 0.0
        roi = 100.0 * d["pnl"] / d["stake"] if d["stake"] else 0.0
        clv_pin = (sum(d["clv_pin"]) / len(d["clv_pin"])) if d["clv_pin"] else None
        clv_any = (sum(d["clv_any"]) / len(d["clv_any"])) if d["clv_any"] else None
        avg_odds = (sum(d["max_odds_samples"]) / len(d["max_odds_samples"])) if d["max_odds_samples"] else None
        return {
            "horizon": name,
            "n_fires": n_fires,
            "n_no_snap": d["n_no_snap"],
            "n_below_edge": d["n_below_edge"],
            "coverage_pct": round(coverage, 1),
            "stake": round(d["stake"], 2),
            "pnl": round(d["pnl"], 2),
            "roi_pct": round(roi, 2),
            "mean_clv_pin_pct": round(clv_pin, 2) if clv_pin is not None else None,
            "mean_clv_any_pct": round(clv_any, 2) if clv_any is not None else None,
            "mean_max_odds": round(avg_odds, 3) if avg_odds is not None else None,
        }

    out = {h[0]: _agg(h[0], per_horizon[h[0]]) for h in HORIZONS}

    b_roi = 100.0 * baseline["pnl"] / baseline["stake"] if baseline["stake"] else 0.0
    b_clv_pin = (sum(baseline["clv_pin"]) / len(baseline["clv_pin"])) if baseline["clv_pin"] else None
    b_clv_any = (sum(baseline["clv_any"]) / len(baseline["clv_any"])) if baseline["clv_any"] else None
    out["production_baseline"] = {
        "horizon": "production_baseline",
        "n_fires": baseline["n"],
        "coverage_pct": 100.0,
        "stake": round(baseline["stake"], 2),
        "pnl": round(baseline["pnl"], 2),
        "roi_pct": round(b_roi, 2),
        "mean_clv_pin_pct": round(b_clv_pin, 2) if b_clv_pin is not None else None,
        "mean_clv_any_pct": round(b_clv_any, 2) if b_clv_any is not None else None,
    }
    return out


def print_table(label: str, summary: dict):
    print(f"\n[{label}]")
    print(f"  {'horizon':>20s} {'fires':>7s} {'cov%':>6s} {'no_snap':>8s} {'<edge':>7s} "
          f"{'stake':>8s} {'pnl':>9s} {'ROI%':>7s} {'avg_odds':>9s} "
          f"{'CLVpin%':>8s} {'CLVany%':>8s}")
    print(f"  {'-'*120}")
    order = [h[0] for h in HORIZONS] + ["production_baseline"]
    for k in order:
        s = summary.get(k)
        if not s:
            continue
        clv_p = f"{s['mean_clv_pin_pct']:+.2f}" if s.get('mean_clv_pin_pct') is not None else "—"
        clv_a = f"{s['mean_clv_any_pct']:+.2f}" if s.get('mean_clv_any_pct') is not None else "—"
        avg_o = f"{s['mean_max_odds']:.3f}" if s.get('mean_max_odds') is not None else "—"
        no_snap = s.get('n_no_snap', "—")
        below = s.get('n_below_edge', "—")
        print(f"  {k:>20s} {s['n_fires']:>7d} {s['coverage_pct']:>5.1f}% "
              f"{str(no_snap):>8s} {str(below):>7s} "
              f"{s['stake']:>8.0f} {s['pnl']:>+9.0f} {s['roi_pct']:>+6.2f}% "
              f"{avg_o:>9s} {clv_p:>8s} {clv_a:>8s}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START_DEFAULT)
    ap.add_argument("--end", default=WINDOW_END_DEFAULT)
    ap.add_argument("--cohort", choices=["calibrated", "broad"], default="calibrated")
    ap.add_argument("--out", default="dev/active/day_ahead_backtest_results.json")
    args = ap.parse_args()

    print(f"Day-ahead pick cadence backtest")
    print(f"Window: {args.start} -> {args.end}")
    print(f"Cohort: {args.cohort}")
    print()

    # Pull picks
    picks = pull_picks(args.start, args.end, args.cohort)
    print(f"Settled picks: {len(picks):,}")
    if not picks:
        print("No picks — bailing.")
        return

    by_mkt = defaultdict(int)
    for p in picks:
        by_mkt[_norm_market(p["market"])] += 1
    print(f"  by market: {dict(by_mkt)}")
    print()

    # Pull odds for all match_ids in one bulk sweep
    match_ids = sorted({p["match_id"] for p in picks})
    kickoffs = {p["match_id"]: p["kickoff"] for p in picks}
    print(f"Bulk-loading odds for {len(match_ids)} matches...")
    odds_idx = pull_horizon_odds(match_ids, kickoffs)
    print(f"  loaded {len(odds_idx):,} (match,market,selection) slots")
    print()

    # Quick coverage snapshot — how many slots actually have each horizon?
    coverage_by_horizon: dict = defaultdict(int)
    for slot in odds_idx.values():
        for name, _, _ in HORIZONS:
            if slot["horizons"].get(name) is not None:
                coverage_by_horizon[name] += 1
    print("Horizon availability (slots with at least one snap in window):")
    for name, _, _ in HORIZONS:
        n = coverage_by_horizon[name]
        pct = 100.0 * n / max(1, len(odds_idx))
        print(f"  {name}: {n:,} slots ({pct:.1f}% of total)")
    print()

    # Run at each edge threshold
    all_results: dict = {}
    for edge in EDGE_THRESHOLDS:
        baseline, per_h = run_horizon_backtest(picks, odds_idx, edge)
        summary = summarize(per_h, len(picks), baseline)
        all_results[f"edge_{int(edge*100)}pct"] = summary
        label = f"edge_threshold={edge*100:.0f}%"
        print_table(label, summary)

    # Build verdict from default-edge summary
    default_key = f"edge_{int(DEFAULT_EDGE*100)}pct"
    default_summary = all_results[default_key]
    base = default_summary["production_baseline"]
    h24 = default_summary["T-24h"]
    h12 = default_summary["T-12h"]
    h6 = default_summary["T-6h"]

    def _verdict(h: dict, b: dict) -> str:
        if h["n_fires"] == 0:
            return "NO DATA (no T-24h snaps in window)"
        roi_delta = h["roi_pct"] - b["roi_pct"]
        cov = h["coverage_pct"]
        if cov < 40:
            verdict = "REJECT (coverage too low)"
        elif roi_delta > 1.0 and cov > 60:
            verdict = "BETTER (publish night-before)"
        elif abs(roi_delta) < 1.0 and cov > 60:
            verdict = "SAME (cadence is a UX choice, not an edge question)"
        else:
            verdict = "WORSE (stick with day-of cadence)"
        return (f"{verdict} — coverage {cov:.0f}%, ROI {h['roi_pct']:+.2f}% vs "
                f"baseline {b['roi_pct']:+.2f}% (delta {roi_delta:+.2f}pp)")

    summary_text = (
        f"Cohort: {args.cohort} pre-match (1x2/OU/BTTS, settled, n={len(picks)}). "
        f"At edge_threshold={DEFAULT_EDGE*100:.0f}%: "
        f"T-24h verdict: {_verdict(h24, base)}; "
        f"T-12h verdict: {_verdict(h12, base)}; "
        f"T-6h verdict: {_verdict(h6, base)}."
    )

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(summary_text)
    print()
    print("Production baseline (actually placed): "
          f"n={base['n_fires']} stake={base['stake']:.0f} "
          f"pnl={base['pnl']:+.0f} ROI={base['roi_pct']:+.2f}%")

    # Persist JSON
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_payload = {
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start, "end": args.end},
        "cohort": f"{args.cohort} pre-match (1x2/OU/BTTS, settled won/lost)",
        "n_picks": len(picks),
        "n_matches": len(match_ids),
        "n_odds_slots_loaded": len(odds_idx),
        "horizon_availability": {
            name: {
                "slots_with_snap": coverage_by_horizon[name],
                "pct_of_slots": round(100.0 * coverage_by_horizon[name] / max(1, len(odds_idx)), 1),
            }
            for name, _, _ in HORIZONS
        },
        "results_by_edge_threshold": all_results,
        "default_edge_threshold": DEFAULT_EDGE,
        "summary": summary_text,
    }
    out_path.write_text(json.dumps(out_payload, indent=2, default=str))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
