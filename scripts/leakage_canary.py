#!/usr/bin/env python3
"""LEAKAGE-CANARY — find training features that know the answer.

WHY THIS EXISTS
---------------
ELO-FORM-LEAK (2026-09-14) went unnoticed from May to September. `elo_diff`
scored AUC 0.7396 against home-win while the de-vigged market scores 0.7270 —
and the model's biggest feature was partly the label. Nothing flagged it because
nothing was looking.

THE RULE, and it is a hard one: **no strictly pre-match feature can
out-discriminate the market.** The market is thousands of informed participants
pricing the same fixture with at least the information we have. A single column
that beats it is not a brilliant feature; it is reading the result. Every leak
this project has found shows up as exactly that signature.

So this ranks every numeric feature in `match_feature_vectors` by AUC against a
settled outcome and flags anything at or above the market's own AUC. It cannot
prove a feature is clean — a leak weaker than the market hides — but it catches
the expensive kind, which is the kind we have had.

    python3 scripts/leakage_canary.py
    python3 scripts/leakage_canary.py --since 2026-06-01 --outcome home
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

OUTCOMES = {
    "home":  "m.score_home > m.score_away",
    "over":  "(m.score_home + m.score_away) > 2",
    "btts":  "m.score_home > 0 AND m.score_away > 0",
}

# Features that ARE the market, or are derived from it. These are *expected* to
# approach the market's AUC and must not be flagged — the canary asks "does this
# beat the market", and a market price trivially ties it.
MARKET_DERIVED = (
    "pinnacle_", "implied_", "market_", "opening_", "closing_", "odds_",
    "bookmaker_disagreement", "sharp_", "line_velocity", "drift", "steam",
    "clv", "pseudo_clv", "vig", "overround",
)


# Columns that ARE the outcome, or trivially encode it. They live in the same
# table as the features but are never in FEATURE_COLS, so a high correlation is
# expected and meaningless. Verified against the live bundle's feature_cols.pkl:
# `total_goals` correlates 0.761 with over-2.5 precisely because it IS the total.
# Excluding them is not a loophole — a column that sneaks a label in under a
# different name still has to pass _is_label, which lists names, not patterns.
LABEL_COLS = {
    "total_goals", "match_outcome", "score_home", "score_away",
    "over_25", "under_25", "btts", "result", "ht_score_home", "ht_score_away",
}


def _is_market(col: str) -> bool:
    return any(tok in col for tok in MARKET_DERIVED)


def _is_label(col: str) -> bool:
    return col in LABEL_COLS


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--outcome", default="home", choices=sorted(OUTCOMES))
    ap.add_argument("--min-n", type=int, default=2000)
    args = ap.parse_args()

    from workers.api_clients.db import execute_query

    outcome_sql = OUTCOMES[args.outcome]
    cols = [r["column_name"] for r in execute_query(
        """SELECT column_name FROM information_schema.columns
            WHERE table_name = 'match_feature_vectors'
              AND data_type IN ('double precision','numeric','integer','real',
                                'smallint','bigint')
            ORDER BY ordinal_position""")]

    results = []
    for col in cols:
        rows = execute_query(
            f"""SELECT count(*) AS n,
                       corr(mfv."{col}"::float,
                            CASE WHEN {outcome_sql} THEN 1.0 ELSE 0.0 END) AS c
                  FROM match_feature_vectors mfv
                  JOIN matches m ON m.id = mfv.match_id
                 WHERE m.status = 'finished' AND m.score_home IS NOT NULL
                   AND m.date >= %s AND mfv."{col}" IS NOT NULL""",
            (args.since,),
        )
        if not rows or not rows[0]["n"] or rows[0]["n"] < args.min_n:
            continue
        c = rows[0]["c"]
        if c is None:
            continue
        # |corr| -> approximate AUC. Monotone, so ranking is what matters; the
        # absolute value is a guide, not a claim.
        if _is_label(col):
            continue
        results.append((col, abs(float(c)), rows[0]["n"], _is_market(col)))

    if not results:
        console.print("[yellow]no features with enough settled rows[/yellow]")
        return 0

    market_best = max((v for _, v, _, m in results if m), default=0.0)
    results.sort(key=lambda r: -r[1])

    t = Table(show_header=True, header_style="bold",
              title=f"Leakage canary — |corr| with `{args.outcome}`, since {args.since}")
    for cname in ("feature", "|corr|", "n", "kind", "verdict"):
        t.add_column(cname, justify="right" if cname != "feature" else "left")
    flagged = []
    for col, v, n, is_mkt in results[:30]:
        kind = "market" if is_mkt else "model"
        if not is_mkt and v >= market_best and market_best > 0:
            verdict = "[red]*** BEATS MARKET ***[/red]"
            flagged.append((col, v, n))
        elif not is_mkt and v >= 0.9 * market_best:
            verdict = "[yellow]near market[/yellow]"
        else:
            verdict = ""
        t.add_row(col, f"{v:.4f}", f"{n:,}", kind, verdict)
    console.print(t)
    console.print(f"\n  strongest MARKET-derived feature: |corr| = {market_best:.4f} "
                  f"— this is the ceiling a pre-match feature should not clear.")
    if flagged:
        console.print(f"\n[bold red]{len(flagged)} non-market feature(s) out-discriminate "
                      f"the market:[/bold red]")
        for col, v, n in flagged:
            console.print(f"    {col}  |corr|={v:.4f}  n={n:,}")
        console.print("[dim]  That is the ELO-FORM-LEAK signature. Check the date bound on "
                      "whatever writes it before trusting any model trained on it.[/dim]")
    else:
        console.print("\n[green]  No non-market feature out-discriminates the market.[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
