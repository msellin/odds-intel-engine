#!/usr/bin/env python3
"""ENSEMBLE-RECALIBRATION — fit per-market calibration from `predictions`.

WHY NOT THE EXISTING FITTER
---------------------------
`fit_platt.py` sources BTTS and OU from settled `simulated_bets` filtered to
the current MODEL_VERSION. Two problems, both fatal:

  * Sample size. It requires MIN_SAMPLES_OU=300; v20260712 has 21 under / 7
    over, and the all-time maximum for ANY version is 175. The threshold is
    unreachable by design, which is why `model_calibration` has never held a
    single `over_under_*` row.

  * Selection bias. A settled bet is a prediction that PASSED an edge filter,
    i.e. the tail where the model most disagrees with the market. Fitting a
    calibration curve on that tail and applying it to all predictions is how
    `btts_yes` ended up with a=3.885/b=-2.563 from n=261 — a curve that makes
    calibration THREE TIMES WORSE when applied: measured out-of-sample, BTTS
    ECE goes 0.0412 raw -> 0.1679 with that fit.

`predictions` has neither problem: every fixture the model priced, 9,066
settled BTTS rows and 4,313 OU 3.5 rows for v20260712, with no filter applied.
It is the same source the 1x2 branch already uses, and 1x2 is the one market
whose calibration works (ECE 0.083 -> 0.004).

THE SAFETY RULE
---------------
A calibration is only written when it beats the RAW probability out-of-sample
on a time-ordered split. That is not a formality: on OU 1.5 the refit is worse
than raw (0.0506 vs 0.0462), so this script deliberately leaves OU 1.5
uncalibrated. Shipping a fit because it was fitted is exactly how the harmful
BTTS curve reached production.

    python3 scripts/fit_calibration_from_predictions.py
    python3 scripts/fit_calibration_from_predictions.py --apply
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

MARKETS = ("btts_yes", "btts_no", "over25", "under25",
           "over35", "under35", "over15", "under15")
MIN_N = 800            # below this a per-market curve is noise
TRAIN_FRAC = 0.70
# The refit must beat raw by more than this to be worth shipping. A margin
# rather than ">" so we do not churn production for a rounding difference.
MIN_ECE_GAIN = 0.002


def _won(market: str, h: int, a: int) -> bool | None:
    if market == "btts_yes":
        return h > 0 and a > 0
    if market == "btts_no":
        return not (h > 0 and a > 0)
    for tag, line in (("15", 1.5), ("25", 2.5), ("35", 3.5)):
        if market == f"over{tag}":
            return (h + a) > line
        if market == f"under{tag}":
            return (h + a) < line
    return None


def _ece(pairs, bins: int = 10) -> float:
    b = defaultdict(list)
    for p, y in pairs:
        b[min(int(p * bins), bins - 1)].append((p, y))
    n = sum(len(v) for v in b.values())
    if not n:
        return 0.0
    return sum(
        len(v) / n * abs(sum(x[0] for x in v) / len(v) - sum(x[1] for x in v) / len(v))
        for v in b.values() if v
    )


def _fit_platt(pairs, iters: int = 3000, lr: float = 2.0) -> tuple[float, float]:
    """Fit y ~ sigmoid(a * prob + b), LINEAR IN THE PROBABILITY.

    This must match `improvements.apply_platt`, which computes
    `1 / (1 + exp(-(a * prob + b)))` on the raw probability — NOT on its logit.

    The first version of this script fitted on the logit, which is the
    statistically better form but the wrong one here: the coefficients then
    mean something different from what production applies. It showed as
    btts_no improving out-of-sample while getting WORSE through the real
    apply_platt path, which is the tell. Coefficients must be fitted in the
    space they will be used in, or the validation measures a function nobody
    runs.
    """
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = 0.0
        for p, y in pairs:
            q = 1.0 / (1.0 + math.exp(-(a * p + b)))
            err = q - y
            ga += err * p
            gb += err
        n = len(pairs)
        a -= lr * ga / n
        b -= lr * gb / n
    return a, b


def _apply(p: float, a: float, b: float) -> float:
    """Mirror of apply_platt's 1-feature branch."""
    return 1.0 / (1.0 + math.exp(-(a * p + b)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to model_calibration")
    ap.add_argument("--model-version", default="v20260712")
    args = ap.parse_args()

    from workers.api_clients.db import execute_query, execute_write

    rows = execute_query(
        """SELECT p.market, p.model_probability::float AS pr,
                  m.score_home AS h, m.score_away AS a
             FROM predictions p
             JOIN matches m ON m.id = p.match_id
            WHERE p.source = 'ensemble'
              AND p.model_version = %s
              AND m.status = 'finished'
              AND m.score_home IS NOT NULL
              AND p.model_probability IS NOT NULL
              AND p.market = ANY(%s)
            ORDER BY m.date""",
        (args.model_version, list(MARKETS)),
    )
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        w = _won(r["market"], r["h"], r["a"])
        if w is not None:
            buckets[r["market"]].append((r["pr"], 1.0 if w else 0.0))

    console.print(f"\n[bold]Calibration fit from `predictions`[/bold] "
                  f"({args.model_version}, {len(rows):,} settled rows)\n")

    t = Table(show_header=True, header_style="bold")
    for c in ("market", "n", "slope a", "b", "ECE raw", "ECE refit", "ship?"):
        t.add_column(c, justify="right" if c != "market" else "left")

    ship: list[tuple] = []
    for mkt in MARKETS:
        v = buckets.get(mkt, [])
        if len(v) < MIN_N:
            t.add_row(mkt, str(len(v)), "—", "—", "—", "—", f"no (n<{MIN_N})")
            continue
        cut = int(len(v) * TRAIN_FRAC)          # time-ordered, never shuffled
        train, test = v[:cut], v[cut:]
        a, b = _fit_platt(train)
        e_raw = _ece(test)
        e_new = _ece([(_apply(p, a, b), y) for p, y in test])
        good = e_new < e_raw - MIN_ECE_GAIN
        t.add_row(mkt, str(len(v)), f"{a:.3f}", f"{b:+.3f}",
                  f"{e_raw:.4f}", f"{e_new:.4f}",
                  "[green]yes[/green]" if good else "[yellow]no[/yellow]")
        if good:
            ship.append((mkt, a, b, len(v), e_raw, e_new))
    console.print(t)
    console.print("\n  [dim]Only a fit that beats the RAW probability out-of-sample is "
                  "written. OU 1.5 fails that test and stays uncalibrated — shipping a "
                  "curve because it was fitted is how the harmful BTTS fit reached "
                  "production.[/dim]")

    if not ship:
        console.print("\n[yellow]Nothing beats raw — nothing to write.[/yellow]")
        return 0
    if not args.apply:
        console.print(f"\n[yellow]Dry run — would write {len(ship)}: "
                      f"{', '.join(m for m, *_ in ship)}. Re-run with --apply.[/yellow]")
        return 0

    for mkt, a, b, n, e_raw, e_new in ship:
        # ece_before/ece_after are the OUT-OF-SAMPLE figures, not the training
        # fit — a fitter that records its own training ECE always looks good.
        execute_write(
            """INSERT INTO model_calibration
                   (market, platt_a, platt_b, platt_c, sample_count,
                    ece_before, ece_after, fitted_at)
               VALUES (%s, %s, %s, NULL, %s, %s, %s, NOW())""",
            (mkt, a, b, n, e_raw, e_new),
        )
        console.print(f"  wrote {mkt}: a={a:.3f} b={b:+.3f} "
                      f"(n={n}, ECE {e_raw:.4f} -> {e_new:.4f} out-of-sample)")
    console.print(f"\n[green]Wrote {len(ship)} calibrations.[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
