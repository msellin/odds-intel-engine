#!/usr/bin/env python3
"""
Backtest: does market-consensus shrinkage improve CS2 model calibration?

Walks last 90d of settled CS2 matches (cs2_predictions ⨝ cs2_results ⨝
cs2_upcoming_matches), reconstructs the per-side consensus the bot would
have seen, applies the shrinkage that cs2_bot.py now uses by default, and
compares calibration metrics for raw model_prob vs shrunk_prob per source.

Outputs per (model_version, market) the row count and:
  - log_loss (lower is better — proper scoring rule)
  - Brier score (lower is better)
  - ECE (expected calibration error, 10-bin — lower is better)

Verdict per source: PROMOTE if shrinkage strictly improves both log_loss and
ECE by ≥0.5%; HOLD if mixed; ROLLBACK if shrinkage worsens either by ≥1%.

Usage:
    python3 scripts/esports/cs2_shrinkage_backtest.py
    python3 scripts/esports/cs2_shrinkage_backtest.py --days 60
    python3 scripts/esports/cs2_shrinkage_backtest.py --report dev/active/cs2-shrinkage-backtest-2026-06-25.md
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query
from scripts.esports.cs2_bot import (
    ALPHA_BY_SOURCE, DEFAULT_ALPHA, market_consensus, shrink_prob,
)

EPS = 1e-9


def _log_loss(p: float, y: int) -> float:
    p = min(max(p, EPS), 1.0 - EPS)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def _ece(records: list[tuple[float, int]], n_bins: int = 10) -> tuple[float, list[tuple[int, float, float, int]]]:
    """Expected Calibration Error. Returns (ECE, per-bin diagnostics)."""
    if not records:
        return 0.0, []
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in records:
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    total = len(records)
    ece = 0.0
    diag = []
    for i, b in enumerate(bins):
        if not b:
            continue
        avg_p = sum(p for p, _ in b) / len(b)
        avg_y = sum(y for _, y in b) / len(b)
        gap = abs(avg_p - avg_y)
        ece += gap * (len(b) / total)
        diag.append((i, avg_p, avg_y, len(b)))
    return ece, diag


def _fetch_settled(days: int) -> list[dict]:
    """One row per (settled match, model_version) with prob + consensus inputs."""
    return list(execute_query("""
        SELECT p.bo3gg_id,
               p.model_version,
               p.win_prob1, p.win_prob2,
               u.bookie_odds1, u.bookie_odds2,
               u.coolbet_odds1, u.coolbet_odds2,
               u.pinnacle_odds1, u.pinnacle_odds2,
               r.winner
        FROM cs2_predictions p
        JOIN cs2_results r ON r.bo3gg_id = p.bo3gg_id
        JOIN cs2_upcoming_matches u ON u.bo3gg_id = p.bo3gg_id
        WHERE r.kickoff_time >= NOW() - INTERVAL %s
          AND r.winner IN ('team1', 'team2')
          AND p.win_prob1 IS NOT NULL
          AND p.win_prob2 IS NOT NULL
    """, (f"{days} days",)))


def _consensus_for_row(row: dict, side: str) -> float | None:
    """Mirror cs2_bot._eligible_books + market_consensus for match_winner."""
    cols = [
        row.get(f"bookie_odds{side}"),
        row.get(f"coolbet_odds{side}"),
        row.get(f"pinnacle_odds{side}"),
    ]
    prices = [("x", float(o)) for o in cols if o is not None and float(o) > 1.0]
    if len(prices) < 2:
        return None
    cons = market_consensus(prices)
    return cons[0] if cons else None


def _evaluate(records: list[dict]) -> dict:
    """Group by model_version, compute calibration metrics raw vs shrunk."""
    by_source: dict[str, dict] = {}
    for row in records:
        source = row["model_version"]
        slot = by_source.setdefault(source, {
            "n_total": 0, "n_shrunk": 0,
            "raw_logloss": 0.0, "shrunk_logloss": 0.0,
            "raw_brier": 0.0, "shrunk_brier": 0.0,
            "raw_records": [], "shrunk_records": [],
        })
        # One observation per side per match — track team1 and team2 outcomes
        # independently for ECE binning (more data, full calibration view).
        for side_idx, prob_col in (("1", "win_prob1"), ("2", "win_prob2")):
            raw_p = float(row[prob_col])
            y = 1 if row["winner"] == f"team{side_idx}" else 0
            cons = _consensus_for_row(row, side_idx)
            alpha = ALPHA_BY_SOURCE.get(source, DEFAULT_ALPHA)
            shrunk_p = shrink_prob(raw_p, cons, alpha) if cons is not None else None

            slot["n_total"] += 1
            slot["raw_logloss"] += _log_loss(raw_p, y)
            slot["raw_brier"] += _brier(raw_p, y)
            slot["raw_records"].append((raw_p, y))

            # Shrunk metrics: only count rows where shrinkage actually applied.
            # Skip rows with no consensus so we measure shrinkage's effect
            # on its own population (not penalised for fall-through cases).
            if shrunk_p is not None:
                slot["n_shrunk"] += 1
                slot["shrunk_logloss"] += _log_loss(shrunk_p, y)
                slot["shrunk_brier"] += _brier(shrunk_p, y)
                slot["shrunk_records"].append((shrunk_p, y))
                # For fair comparison we also need raw metrics on the same
                # subset — store separately.
                slot.setdefault("raw_on_shrunk_records", []).append((raw_p, y))
                slot.setdefault("raw_on_shrunk_logloss", 0.0)
                slot["raw_on_shrunk_logloss"] += _log_loss(raw_p, y)
                slot.setdefault("raw_on_shrunk_brier", 0.0)
                slot["raw_on_shrunk_brier"] += _brier(raw_p, y)
    return by_source


def _verdict(raw_ll: float, shrunk_ll: float, raw_ece: float, shrunk_ece: float) -> str:
    ll_delta = (raw_ll - shrunk_ll) / max(raw_ll, EPS)  # positive = shrunk is better
    ece_delta = raw_ece - shrunk_ece                     # positive = shrunk is better
    if ll_delta >= 0.005 and ece_delta >= 0.005:
        return "✓ PROMOTE — shrinkage improves both log_loss and ECE"
    if ll_delta <= -0.01 or ece_delta <= -0.01:
        return "✗ ROLLBACK — shrinkage worsens log_loss or ECE by ≥1%"
    return "~ HOLD — mixed or near-neutral; α tuning may help"


def _format_report(by_source: dict, days: int) -> str:
    lines = []
    lines.append(f"# CS2 shrinkage calibration backtest — {days}d window")
    lines.append("")
    lines.append("Compares raw model probability vs market-consensus-shrunk")
    lines.append("probability on settled CS2 matches. Shrinkage formula:")
    lines.append("")
    lines.append("    shrunk = α · model_prob + (1 − α) · consensus_implied")
    lines.append("")
    lines.append(f"Per-source α: {ALPHA_BY_SOURCE}")
    lines.append("")
    lines.append("Per-source metrics on the population where shrinkage applied")
    lines.append("(consensus from ≥2 books available — others fall through to raw).")
    lines.append("")
    header = f"| {'source':<10} | {'n':>5} | {'raw_LL':>7} | {'shr_LL':>7} | {'Δ_LL':>6} | {'raw_ECE':>7} | {'shr_ECE':>7} | {'Δ_ECE':>6} | verdict |"
    sep = f"| {'-'*10} | {'-'*5} | {'-'*7} | {'-'*7} | {'-'*6} | {'-'*7} | {'-'*7} | {'-'*6} | {'-'*7} |"
    lines.append(header)
    lines.append(sep)

    for source in ("elo+pq_v1", "v8", "v7", "hltv_v1"):
        slot = by_source.get(source)
        if not slot or slot["n_shrunk"] == 0:
            lines.append(f"| {source:<10} | {0:>5} | {'-':>7} | {'-':>7} | {'-':>6} | {'-':>7} | {'-':>7} | {'-':>6} | no data |")
            continue
        n_s = slot["n_shrunk"]
        raw_ll = slot.get("raw_on_shrunk_logloss", 0.0) / n_s
        shr_ll = slot["shrunk_logloss"] / n_s
        raw_ece, _ = _ece(slot.get("raw_on_shrunk_records", []))
        shr_ece, _ = _ece(slot["shrunk_records"])
        d_ll = raw_ll - shr_ll
        d_ece = raw_ece - shr_ece
        verdict = _verdict(raw_ll, shr_ll, raw_ece, shr_ece)
        lines.append(
            f"| {source:<10} | {n_s:>5d} | {raw_ll:>7.4f} | {shr_ll:>7.4f} | "
            f"{d_ll:>+6.4f} | {raw_ece*100:>6.2f}% | {shr_ece*100:>6.2f}% | "
            f"{d_ece*100:>+5.2f}% | {verdict.split(' — ')[0]} |"
        )

    lines.append("")
    lines.append("**Δ_LL** = raw − shrunk log-loss (positive ⇒ shrinkage better).")
    lines.append("**Δ_ECE** = raw − shrunk ECE (positive ⇒ shrinkage better).")
    lines.append("")
    # Verdict summary per source.
    for source in ("elo+pq_v1", "v8", "v7", "hltv_v1"):
        slot = by_source.get(source)
        if not slot or slot["n_shrunk"] == 0:
            continue
        n_s = slot["n_shrunk"]
        raw_ll = slot.get("raw_on_shrunk_logloss", 0.0) / n_s
        shr_ll = slot["shrunk_logloss"] / n_s
        raw_ece, _ = _ece(slot.get("raw_on_shrunk_records", []))
        shr_ece, _ = _ece(slot["shrunk_records"])
        verdict = _verdict(raw_ll, shr_ll, raw_ece, shr_ece)
        lines.append(f"- **{source}** (α={ALPHA_BY_SOURCE.get(source, DEFAULT_ALPHA)}): {verdict}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="settled-results window in days")
    ap.add_argument("--report", default=None, help="write Markdown report to this path")
    args = ap.parse_args()

    records = _fetch_settled(args.days)
    print(f"\n=== CS2 shrinkage backtest — {args.days}d window, {len(records)} (pred, match) rows ===\n")
    if not records:
        print("  no settled rows in window — nothing to evaluate")
        return 1
    by_source = _evaluate(records)
    report = _format_report(by_source, args.days)
    print(report)
    if args.report:
        Path(args.report).write_text(report)
        print(f"\n  wrote report to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
