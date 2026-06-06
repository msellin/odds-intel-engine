"""Analyze the Pinnacle weekend line-movement experiment output.

Reads the CSV that `scripts/pinnacle_movement_research.py` writes,
computes per-match drift across T-windows (240min / 60min / 15min
before kickoff), aggregates to an overall drift distribution + per-
league breakdown, and produces a markdown verdict on whether weekend
Pinnacle direct-polling at higher cadence would materially improve
our CLV calculation vs the current AF 3h refresh cycle.

Designed to run unattended Mon 2026-06-09 06:00 UTC right after the
collector auto-exits — output is a single deterministic markdown
report at `dev/active/pinnacle-movement-analysis-YYYY-MM-DD.md`.

The verdict thresholds (see DECISION CRITERIA in the output) were
chosen pre-data per the spec discipline; do not retune them after
seeing results.

Usage:
    python3 scripts/analyze_pinnacle_movement.py
    python3 scripts/analyze_pinnacle_movement.py --csv PATH --out PATH

Stdlib only — no numpy/pandas. Keeps the analyzer cheap to run on
any Python 3.10+ environment without pinning extra deps.
"""

import argparse
import csv
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Pre-data decision criteria (frozen, don't retune after seeing results) ──
# Implied-probability shift threshold. A 1pp shift on a fair 50% line moves
# decimal odds from 2.00 → 2.04 — meaningful for CLV but small enough that
# we should only act on the broader pattern, not single matches.
MATERIAL_SHIFT_PP = 1.0

# What fraction of matches need to show material late drift to justify
# weekend-only direct-Pinnacle polling? See DECISION CRITERIA in output.
THRESHOLD_STRONG_CASE_PCT = 20.0
THRESHOLD_MARGINAL_PCT = 10.0

# T-window boundaries. Defined in minutes before kickoff.
WINDOWS = [
    ("T-240min", 240),   # 4h — covers full poll window
    ("T-180min", 180),   # 3h — matches AF refresh cycle (one cycle pre-KO)
    ("T-120min", 120),
    ("T-60min", 60),
    ("T-30min", 30),
    ("T-15min", 15),
    ("T-5min", 5),
]

DEFAULT_CSV = "dev/active/pinnacle-movement-2026-06-05.csv"
DEFAULT_OUT_FMT = "dev/active/pinnacle-movement-analysis-{date}.md"


def parse_dt(s: str) -> datetime:
    """Parse our CSV's ISO 8601 timestamps (with timezone)."""
    return datetime.fromisoformat(s)


def implied(decimal_odds: float) -> float:
    """Decimal odds → implied probability (pre-vig). Caller handles vig math."""
    return 1.0 / decimal_odds if decimal_odds > 0 else float("nan")


def load_snapshots(csv_path: Path) -> list[dict]:
    """Read CSV rows + parse datetimes + decimal odds into floats."""
    rows = []
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                row = {
                    "fetch_time": parse_dt(r["fetch_time_utc"]),
                    "match_id": r["our_match_id"],
                    "kickoff": parse_dt(r["kickoff_utc"]),
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "league": r["league"],
                    "home_dec": float(r["home_decimal"]),
                    "draw_dec": float(r["draw_decimal"]),
                    "away_dec": float(r["away_decimal"]),
                }
                row["t_to_ko_min"] = (row["kickoff"] - row["fetch_time"]).total_seconds() / 60.0
                rows.append(row)
            except (ValueError, KeyError):
                # Tolerate a single malformed row — don't fail the whole analysis
                continue
    return rows


def group_by_match(snapshots: list[dict]) -> dict[str, list[dict]]:
    """Bucket snapshots by match_id, sorted by fetch_time ascending."""
    by_match: dict[str, list[dict]] = defaultdict(list)
    for s in snapshots:
        by_match[s["match_id"]].append(s)
    for mid in by_match:
        by_match[mid].sort(key=lambda x: x["fetch_time"])
    return by_match


def snapshot_at_window(snapshots: list[dict], minutes_before_ko: float) -> dict | None:
    """Return the snapshot with t_to_ko closest to (but ≥) the given minutes-
    before-kickoff value. None if no snapshot is at/before that window."""
    candidates = [s for s in snapshots if s["t_to_ko_min"] >= minutes_before_ko]
    if not candidates:
        return None
    # Closest to (but ≥) the threshold — i.e., the latest snapshot inside the window
    return min(candidates, key=lambda s: s["t_to_ko_min"] - minutes_before_ko)


def compute_drift_pp(open_snap: dict, close_snap: dict) -> dict:
    """Implied-probability deltas between two snapshots, expressed in
    percentage points (1pp = 0.01). Returns dict with per-market deltas
    plus the max absolute delta across the three markets."""
    deltas = {}
    for market in ("home", "draw", "away"):
        open_imp = implied(open_snap[f"{market}_dec"])
        close_imp = implied(close_snap[f"{market}_dec"])
        deltas[market] = (close_imp - open_imp) * 100  # pp
    deltas["max_abs"] = max(abs(deltas[m]) for m in ("home", "draw", "away"))
    return deltas


def analyze(csv_path: Path) -> dict:
    """End-to-end analysis. Returns a dict suitable for the markdown renderer."""
    snapshots = load_snapshots(csv_path)
    by_match = group_by_match(snapshots)

    # Aggregate counters
    match_count = len(by_match)
    snap_count = len(snapshots)
    snaps_per_match = sorted(len(s) for s in by_match.values())

    # Per-match drift computations
    per_match_drift = []
    by_league: dict[str, list[float]] = defaultdict(list)
    for mid, snaps in by_match.items():
        if len(snaps) < 2:
            continue  # Need at least 2 snapshots to compute drift
        first, last = snaps[0], snaps[-1]
        drift_full = compute_drift_pp(first, last)
        # Drift within the final hour: T-60 → last available snapshot
        t60 = snapshot_at_window(snaps, 60)
        drift_final_hour = compute_drift_pp(t60, last) if t60 is not None and t60 is not last else None
        # Drift within the final 15min: T-15 → last
        t15 = snapshot_at_window(snaps, 15)
        drift_final_15 = compute_drift_pp(t15, last) if t15 is not None and t15 is not last else None
        per_match_drift.append({
            "match_id": mid,
            "league": snaps[0]["league"],
            "home_team": snaps[0]["home_team"],
            "away_team": snaps[0]["away_team"],
            "n_snaps": len(snaps),
            "first_t_to_ko_min": snaps[0]["t_to_ko_min"],
            "last_t_to_ko_min": snaps[-1]["t_to_ko_min"],
            "drift_full": drift_full,
            "drift_final_hour": drift_final_hour,
            "drift_final_15": drift_final_15,
        })
        by_league[snaps[0]["league"]].append(drift_full["max_abs"])

    # How many matches showed material drift (≥ MATERIAL_SHIFT_PP) in each window?
    def pct_material(key: str) -> tuple[int, int]:
        with_drift = [m for m in per_match_drift if m[key] is not None]
        if not with_drift:
            return (0, 0)
        material = sum(1 for m in with_drift if m[key]["max_abs"] >= MATERIAL_SHIFT_PP)
        return (material, len(with_drift))

    full_material, full_n = pct_material("drift_full")
    fh_material, fh_n = pct_material("drift_final_hour")
    f15_material, f15_n = pct_material("drift_final_15")

    # Per-league summary (only leagues with ≥3 matches captured)
    league_summary = []
    for league, drifts in by_league.items():
        if len(drifts) < 3:
            continue
        league_summary.append({
            "league": league,
            "n": len(drifts),
            "mean_max_abs_pp": statistics.mean(drifts),
            "median_max_abs_pp": statistics.median(drifts),
            "max_drift_pp": max(drifts),
        })
    league_summary.sort(key=lambda x: x["mean_max_abs_pp"], reverse=True)

    # Drift distribution (full-window max-abs)
    all_full_drifts = [m["drift_full"]["max_abs"] for m in per_match_drift]
    drift_dist = {
        "n": len(all_full_drifts),
        "mean": statistics.mean(all_full_drifts) if all_full_drifts else 0,
        "median": statistics.median(all_full_drifts) if all_full_drifts else 0,
        "p75": statistics.quantiles(all_full_drifts, n=4)[2] if len(all_full_drifts) >= 4 else None,
        "p90": statistics.quantiles(all_full_drifts, n=10)[8] if len(all_full_drifts) >= 10 else None,
        "max": max(all_full_drifts) if all_full_drifts else 0,
    }

    # Top 5 movers (largest full-window drift)
    top_movers = sorted(per_match_drift, key=lambda m: m["drift_full"]["max_abs"], reverse=True)[:5]

    # Decide the verdict
    fh_pct = (fh_material / fh_n * 100) if fh_n > 0 else 0
    if fh_pct >= THRESHOLD_STRONG_CASE_PCT:
        verdict = "STRONG"
    elif fh_pct >= THRESHOLD_MARGINAL_PCT:
        verdict = "MARGINAL"
    else:
        verdict = "NEGATIVE"

    return {
        "csv_path": str(csv_path),
        "snap_count": snap_count,
        "match_count": match_count,
        "snaps_per_match_median": statistics.median(snaps_per_match) if snaps_per_match else 0,
        "snaps_per_match_max": snaps_per_match[-1] if snaps_per_match else 0,
        "drift_dist": drift_dist,
        "full_material": full_material, "full_n": full_n,
        "fh_material": fh_material, "fh_n": fh_n, "fh_pct": fh_pct,
        "f15_material": f15_material, "f15_n": f15_n,
        "league_summary": league_summary,
        "top_movers": top_movers,
        "verdict": verdict,
    }


def render_markdown(a: dict) -> str:
    """Render the analysis dict to a markdown report."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = []
    lines.append(f"# Pinnacle weekend line-movement analysis — {today}")
    lines.append("")
    lines.append(f"**Source:** `{a['csv_path']}`")
    lines.append(f"**Analyzer:** `scripts/analyze_pinnacle_movement.py` (stdlib-only, deterministic)")
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Matches captured: **{a['match_count']:,}**")
    lines.append(f"- Total snapshots: **{a['snap_count']:,}**")
    lines.append(f"- Snapshots per match (median / max): **{a['snaps_per_match_median']} / {a['snaps_per_match_max']}**")
    lines.append("")

    if a["snap_count"] == 0:
        lines.append("**No snapshots captured. Likely causes:** the collector hadn't run yet, or all matches in the window were outside the 4h polling threshold. Re-check `scripts/pinnacle_movement_research.py` logs.")
        return "\n".join(lines)

    lines.append("## Drift distribution (full-window max abs per match, pp)")
    lines.append("")
    d = a["drift_dist"]
    lines.append(f"- n = **{d['n']:,}** matches with ≥2 snapshots")
    lines.append(f"- mean: **{d['mean']:.2f}pp**")
    lines.append(f"- median: **{d['median']:.2f}pp**")
    if d["p75"] is not None:
        lines.append(f"- p75: **{d['p75']:.2f}pp**")
    if d["p90"] is not None:
        lines.append(f"- p90: **{d['p90']:.2f}pp**")
    lines.append(f"- max: **{d['max']:.2f}pp**")
    lines.append("")

    lines.append("## Matches with material drift (≥1.0pp implied prob shift)")
    lines.append("")
    lines.append("| Window | matched | total | % |")
    lines.append("|---|---|---|---|")
    if a["full_n"] > 0:
        lines.append(f"| Full window (first → last snap) | {a['full_material']} | {a['full_n']} | {a['full_material']/a['full_n']*100:.1f}% |")
    if a["fh_n"] > 0:
        lines.append(f"| **Final hour (T-60 → last)** | **{a['fh_material']}** | **{a['fh_n']}** | **{a['fh_pct']:.1f}%** |")
    if a["f15_n"] > 0:
        lines.append(f"| Final 15min (T-15 → last) | {a['f15_material']} | {a['f15_n']} | {a['f15_material']/a['f15_n']*100:.1f}% |")
    lines.append("")

    lines.append("## DECISION CRITERIA (frozen pre-data)")
    lines.append("")
    lines.append(f"Threshold metric: **% of matches with ≥{MATERIAL_SHIFT_PP}pp implied-prob max-abs shift in the final hour (T-60 → close).**")
    lines.append("")
    lines.append(f"- **STRONG case** (≥{THRESHOLD_STRONG_CASE_PCT:.0f}%): material late drift is common enough that AF's 3h refresh cycle is missing meaningful movement on weekends. Build a weekend-only Pinnacle direct-poll job.")
    lines.append(f"- **MARGINAL** ({THRESHOLD_MARGINAL_PCT:.0f}%-{THRESHOLD_STRONG_CASE_PCT:.0f}%): mixed evidence. File a more targeted follow-up — likely scoped to a specific league or kickoff window where drift concentrates.")
    lines.append(f"- **NEGATIVE** (<{THRESHOLD_MARGINAL_PCT:.0f}%): late drift is rare. AF 3h cycle is fine; don't burn engineering on weekend Pinnacle polling. Close the question.")
    lines.append("")
    lines.append(f"### Verdict: **{a['verdict']}**")
    lines.append("")
    if a["verdict"] == "STRONG":
        lines.append(f"{a['fh_pct']:.1f}% of matches with a T-60 snapshot showed ≥1pp implied-prob movement in the final hour. Recommend building a weekend-only Pinnacle direct-poll job (≤30 req/h, scoped to top-N leagues by drift concentration — see league table below).")
    elif a["verdict"] == "MARGINAL":
        lines.append(f"{a['fh_pct']:.1f}% of matches showed material late drift — in the 'maybe' band. The per-league breakdown will tell you whether drift is concentrated enough to justify a narrow-scope poll (e.g., only Premier League + La Liga + Bundesliga). Don't commit to a broad job yet.")
    else:
        lines.append(f"Only {a['fh_pct']:.1f}% of matches showed ≥1pp late drift. AF's 3h refresh cycle catches the meaningful movement. Close the question; weekend Pinnacle direct-polling isn't worth the engineering.")
    lines.append("")

    if a["league_summary"]:
        lines.append("## Per-league drift summary (≥3 matches captured)")
        lines.append("")
        lines.append("| League | n | mean max-abs (pp) | median max-abs (pp) | max drift (pp) |")
        lines.append("|---|---|---|---|---|")
        for ls in a["league_summary"][:15]:
            lines.append(f"| {ls['league']} | {ls['n']} | {ls['mean_max_abs_pp']:.2f} | {ls['median_max_abs_pp']:.2f} | {ls['max_drift_pp']:.2f} |")
        lines.append("")

    if a["top_movers"]:
        lines.append("## Top 5 single-match movers (full-window max-abs)")
        lines.append("")
        lines.append("| Match | League | n_snaps | first T-KO | last T-KO | max-abs (pp) |")
        lines.append("|---|---|---|---|---|---|")
        for m in a["top_movers"]:
            lines.append(
                f"| {m['home_team']} vs {m['away_team']} | {m['league']} | "
                f"{m['n_snaps']} | {m['first_t_to_ko_min']:.0f}min | "
                f"{m['last_t_to_ko_min']:.0f}min | {m['drift_full']['max_abs']:.2f} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append(f"- Analyzer: `scripts/analyze_pinnacle_movement.py`")
    lines.append(f"- Source CSV: `{a['csv_path']}`")
    lines.append(f"- Material-shift threshold: `{MATERIAL_SHIFT_PP}pp` (frozen pre-data)")
    lines.append(f"- Decision thresholds: strong ≥{THRESHOLD_STRONG_CASE_PCT}%, marginal ≥{THRESHOLD_MARGINAL_PCT}%, negative <{THRESHOLD_MARGINAL_PCT}%")
    lines.append(f"- Run command: `python3 scripts/analyze_pinnacle_movement.py`")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path(DEFAULT_CSV),
                        help=f"Path to the experiment CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown path (default: dev/active/pinnacle-movement-analysis-YYYY-MM-DD.md)")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV not found at {args.csv}")
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = args.out or Path(DEFAULT_OUT_FMT.format(date=today))

    result = analyze(args.csv)
    md = render_markdown(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)

    print(f"Wrote analysis to: {out_path}")
    print(f"Verdict: {result['verdict']}")
    print(f"Coverage: {result['match_count']:,} matches / {result['snap_count']:,} snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
