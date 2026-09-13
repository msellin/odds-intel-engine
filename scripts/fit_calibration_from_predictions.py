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

# `predictions.market` and the key `apply_platt` is CALLED with are not the
# same vocabulary, and getting this wrong writes a calibration nobody reads.
# The pipeline builds its key as f"{os_market}_{os_selection}" from the
# odds-snapshot naming (`over_under_25` + `over`), so it asks for
# "over_under_25_over" while `predictions` stores "over25". A fit written under
# the predictions name loads fine, matches nothing at call time, and
# apply_platt silently returns the input — the same failure mode as
# PLATT-LIMIT-30-TRUNCATION, arrived at from the opposite direction.
# BTTS needs no mapping: both sides already say btts_yes / btts_no.
PRODUCTION_KEY = {
    "over25":  "over_under_25_over",
    "under25": "over_under_25_under",
    "over35":  "over_under_35_over",
    "under35": "over_under_35_under",
    "over15":  "over_under_15_over",
    "under15": "over_under_15_under",
}
MIN_N = 800            # below this a per-market curve is noise
TRAIN_FRAC = 0.70
# The refit must beat raw by more than this to be worth shipping. A margin
# rather than ">" so we do not churn production for a rounding difference.
MIN_ECE_GAIN = 0.002
# Range guard (OU-CALIBRATOR-DOMAIN-MISMATCH). A sigmoid fitted in the wrong
# domain collapses toward a constant: the 2026-09-03 under-2.5 curve spanned only
# [0.3028, 0.6663]. Books price O/U 2.5 selections down to ~0.22 implied, so a
# curve that cannot emit below 0.30 reports edge on every longshot by
# construction. These bounds are deliberately loose — they reject a degenerate
# curve, not a merely imperfect one.
MIN_RANGE = 0.55       # sig(a+b) - sig(b) must span at least this much
MAX_FLOOR = 0.20       # sig(b): the lowest probability the curve can ever emit
MIN_CEILING = 0.80     # sig(a+b): the highest


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
    ap.add_argument("--i-have-fixed-the-domain-mismatch", action="store_true",
                    help="Override the domain-mismatch refusal. Only pass this once "
                         "this script fits on `shrunk` (what improvements.py:227 "
                         "actually passes to _apply_stage2), not on the raw "
                         "predictions.model_probability. See OU-CALIBRATOR-REFIT-ON-SHRUNK.")
    ap.add_argument("--model-version", default=None,
                    help="pin one version for every market (default: resolve "
                         "the live version PER MARKET)")
    args = ap.parse_args()

    from workers.api_clients.db import execute_query, execute_write

    # OU-PLATT-UNFITTABLE-2026-09-03: resolve the model version PER MARKET, not
    # once globally. The engine routes per market (`_resolve_version`:
    # MODEL_VERSION_OU_T{tier} -> MODEL_VERSION_{MARKET} -> global), so OU
    # predictions are written under a different version from 1X2. Pinning the
    # global version returned ZERO over25 rows and made OU look unfittable when
    # 5,490 settled rows existed under the OU version.
    #
    # Resolve by SETTLED VOLUME in a recent window, not by "most recent row".
    # Several model versions write predictions concurrently (shadow A/B), so
    # the newest row is a race: it resolved over25 -> v20260705 (413 settled)
    # while under25 -> v20260719 (5,351), splitting a market PAIR across two
    # versions and making OU 2.5 look unfittable. Volume is stable and picks
    # the version actually producing the data.
    live = {}
    for r in execute_query(
        """SELECT DISTINCT ON (p.market) p.market, p.model_version, COUNT(*) AS n
             FROM predictions p
             JOIN matches m ON m.id = p.match_id
            WHERE p.source = 'ensemble' AND p.market = ANY(%s)
              AND m.status = 'finished' AND m.score_home IS NOT NULL
              AND p.model_probability IS NOT NULL
              AND p.created_at >= NOW() - INTERVAL '120 days'
            GROUP BY p.market, p.model_version
            ORDER BY p.market, COUNT(*) DESC""",
        (list(MARKETS),),
    ):
        live[r["market"]] = r["model_version"]

    rows = []
    for mkt in MARKETS:
        version = args.model_version or live.get(mkt)
        if not version:
            continue
        rows.extend(execute_query(
            """SELECT p.market, p.model_probability::float AS pr,
                      m.score_home AS h, m.score_away AS a
                 FROM predictions p
                 JOIN matches m ON m.id = p.match_id
                WHERE p.source = 'ensemble'
                  AND p.model_version = %s
                  AND m.status = 'finished'
                  AND m.score_home IS NOT NULL
                  AND p.model_probability IS NOT NULL
                  AND p.market = %s
                ORDER BY m.date""",
            (version, mkt),
        ))
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        w = _won(r["market"], r["h"], r["a"])
        if w is not None:
            buckets[r["market"]].append((r["pr"], 1.0 if w else 0.0))

    vshown = args.model_version or ", ".join(
        f"{m}={live[m]}" for m in MARKETS if m in live and live[m])
    console.print(f"\n[bold]Calibration fit from `predictions`[/bold] "
                  f"({len(rows):,} settled rows)\n[dim]versions: {vshown}[/dim]\n")

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

    # ------------------------------------------------------------------
    # OU-CALIBRATOR-DOMAIN-MISMATCH (2026-09-13) — WRITES ARE DISABLED.
    #
    # This script fits on `predictions.model_probability`, the RAW ensemble
    # probability. Production does NOT call apply_platt with that number.
    # `improvements.calibrate_prob` runs stage-1 shrinkage first and passes
    # `shrunk = alpha * model_prob + (1 - alpha) * pinnacle_devig` into
    # `_apply_stage2` (improvements.py:227). Once odds > 3.0, alpha floors at
    # 0.10, so `shrunk` is ~90% Pinnacle and nothing like the fit's X.
    #
    # Every market in MARKETS goes through that shrinkage — `_GOALLINE_PREFIXES`
    # is ("btts", "over", "under") — so there is no market this script can
    # currently calibrate correctly, and the `e_new < e_raw` check above cannot
    # catch it: it measures a function production never executes. That is the
    # same trap `_fit_platt`'s own docstring documents for logit-vs-probability,
    # reached from the other direction.
    #
    # What the one shipped run did (2026-09-03 10:49 UTC, over/under 2.5 + 3.5):
    # the under-2.5 curve sigmoid(1.5258*p - 0.8341) has total output range
    # [0.3028, 0.6663] and a fixed point at 0.4713, so it inflated every
    # probability below 0.4713 by 5-11pp. `edge = cal_prob - 1/odds` became "how
    # far is this price from ~0.45", maximised by the longest price on the board.
    # bot_v10_all went 14.6% -> 69.9% O/U share and +38.5% -> -15.0% ROI, a
    # -EUR467 drawdown. Measured by scripts/ou_calibrator_backtest.py on a
    # held-out slice: live curve CLV -1.57% (t=-2.4) vs no curve +24.7% (t=+5.5).
    #
    # Re-enabling requires fitting on `shrunk` (reconstruct it the way the
    # backtest does: alpha * model_prob + (1-alpha) * devigged Pinnacle), and
    # validating on the edge >= floor SELECTED subpopulation rather than
    # universe-wide ECE — an average-ECE win is the wrong loss for a curve whose
    # only job is to feed a tail gate. Tracked as OU-CALIBRATOR-REFIT-ON-SHRUNK.
    # ------------------------------------------------------------------
    if args.apply and not args.i_have_fixed_the_domain_mismatch:
        console.print(
            "\n[bold red]REFUSING TO WRITE — domain mismatch.[/bold red]\n"
            "This script fits on predictions.model_probability (raw ensemble), but\n"
            "improvements.calibrate_prob applies stage 2 to `shrunk` (~90% Pinnacle\n"
            "above odds 3.0). The out-of-sample ECE check above therefore validates a\n"
            "function production never runs.\n\n"
            "The single shipped run of this script (2026-09-03) cost -EUR467 on\n"
            "bot_v10_all and was reverted in migration 335. See\n"
            "scripts/ou_calibrator_backtest.py for the measured comparison.\n\n"
            "Fit on `shrunk` before re-enabling. Tracked: OU-CALIBRATOR-REFIT-ON-SHRUNK."
        )
        return 2

    # Second line of defence, for after the domain is fixed: a curve whose output
    # range cannot reach the probabilities the market actually prices is not a
    # calibration, it is a constant with a slope. The 2026-09-03 under-2.5 fit
    # could never emit anything below 0.3028 while books routinely price O/U 2.5
    # selections at 0.22-0.25 implied.
    rejected = []
    for mkt, a, b, n, e_raw, e_new in list(ship):
        lo, hi = _apply(0.0, a, b), _apply(1.0, a, b)
        if lo > MAX_FLOOR or hi < MIN_CEILING or (hi - lo) < MIN_RANGE:
            rejected.append((mkt, lo, hi))
            ship.remove((mkt, a, b, n, e_raw, e_new))
    for mkt, lo, hi in rejected:
        console.print(f"  [red]rejected {mkt}[/red]: output range [{lo:.4f}, {hi:.4f}] "
                      f"— too compressed to be a calibration "
                      f"(need span >= {MIN_RANGE}, floor <= {MAX_FLOOR}, ceiling >= {MIN_CEILING})")
    if not ship:
        console.print("\n[yellow]Every fit failed the range guard — nothing to write.[/yellow]")
        return 0

    if not args.apply:
        console.print(f"\n[yellow]Dry run — would write {len(ship)}: "
                      f"{', '.join(m for m, *_ in ship)}. Re-run with --apply.[/yellow]")
        return 0

    for mkt, a, b, n, e_raw, e_new in ship:
        mkt = PRODUCTION_KEY.get(mkt, mkt)   # write the key production asks for
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
