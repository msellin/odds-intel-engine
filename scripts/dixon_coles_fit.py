#!/usr/bin/env python3
"""DIXON-COLES-FIT — walk-forward, strictly out-of-sample per-match rates.

Phase 2 of `DIXON-COLES-OU-BASELINE` (dev/active/dixon-coles-plan.md). The
fitter itself is `workers/model/dixon_coles.py`; this is the driver that decides
WHICH matches each fit is allowed to see.

THE ONE PROPERTY THAT MATTERS. For every match this scores, the parameters must
come only from matches that had already finished when it kicked off. The whole
point of the exercise is a number comparable to the shipped alpha = 0.0000, and
a single leaked fixture makes that number meaningless in the flattering
direction. So:

  * leagues are fitted SEPARATELY (teams rarely cross leagues; one global fit
    would tie unrelated strength scales together)
  * within a league, refits happen on a weekly grid, and a refit at time T sees
    ONLY matches with date < T
  * a match is scored by the most recent refit STRICTLY BEFORE its own date
  * the guard is asserted per row, not assumed — `--verify` re-checks every
    scored match against its fit window and fails loudly

Exponential time decay is measured back from the REFIT date, not from the newest
match in the data: using the data's own maximum would make the weights depend on
what happened to be collected.

Writes nothing to the database. Emits a Parquet/CSV of per-match (lambda, mu) so
phase 4 can score it through the residual harness.

    python3 scripts/dixon_coles_fit.py --xi 0.0018 --out /tmp/dc_rates.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.dixon_coles import fit, prob_1x2, prob_over, score_matrix  # noqa: E402

MIN_LEAGUE_MATCHES = 60     # below this a per-league fit is noise, not a model
REFIT_DAYS = 7


def load(history_days: int) -> list[dict]:
    """Finished matches with a score, newest last. `history_days` bounds how far
    back the FIT may look; it does not bound what is scored."""
    return execute_query(
        """SELECT m.id::text AS mid, m.league_id::text AS lid,
                  m.home_team_id::text AS home, m.away_team_id::text AS away,
                  m.score_home::int AS hg, m.score_away::int AS ag,
                  m.date
             FROM matches m
            WHERE m.status = 'finished'
              AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
              AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
              AND m.league_id IS NOT NULL
              AND m.date > now() - make_interval(days => %s)
            ORDER BY m.date""",
        (history_days,),
    ) or []


def run(from_date: dt.date, xi: float, history_days: int, verify: bool,
        to_date: dt.date | None = None, rows: list[dict] | None = None):
    """`to_date` bounds what is SCORED (inclusive). Without it the refit grid
    runs to the newest match in the table, which for a validation window means
    fitting weeks nobody will look at — measured at 4 wasted refits per league
    per xi, on a 7-point grid.

    `rows` lets a caller load once and reuse: the xi sweep was re-pulling the
    same ~170k rows for every grid point."""
    if rows is None:
        rows = load(history_days)
    by_league: dict[str, list] = defaultdict(list)
    for r in rows:
        d = r["date"]
        by_league[r["lid"]].append(
            (r["mid"], r["home"], r["away"], int(r["hg"]), int(r["ag"]),
             d.date() if hasattr(d, "date") else d))

    out, skipped = [], defaultdict(int)
    n_fits = 0
    for lid, ms in by_league.items():
        if len(ms) < MIN_LEAGUE_MATCHES:
            skipped["league too small"] += sum(1 for m in ms if m[5] >= from_date)
            continue
        targets = [m for m in ms if m[5] >= from_date
                   and (to_date is None or m[5] <= to_date)]
        if not targets:
            continue

        # Weekly refit grid covering the target window.
        grid: list[dt.date] = []
        t = from_date
        last = max(m[5] for m in targets)
        while t <= last:
            grid.append(t)
            t = t + dt.timedelta(days=REFIT_DAYS)

        fits: dict[dt.date, object] = {}
        for g in grid:
            # STRICTLY BEFORE the refit date.
            train = [(m[1], m[2], m[3], m[4], m[5]) for m in ms if m[5] < g]
            if len(train) < MIN_LEAGUE_MATCHES:
                continue
            f = fit(train, xi=xi, ref_date=g)
            if f is not None:
                fits[g] = f
                n_fits += 1

        for mid, home, away, hg, ag, mdate in targets:
            # Most recent refit strictly before this match.
            usable = [g for g in fits if g <= mdate]
            if not usable:
                skipped["no fit before match"] += 1
                continue
            g = max(usable)
            f = fits[g]
            r = f.rates(home, away)
            if r is None:
                skipped["team not in fit"] += 1
                continue
            lam, mu = r
            if verify:
                # Re-derive the fit window and assert this match is not in it.
                assert g <= mdate, f"refit {g} is not before match {mdate}"
                leak = [m for m in ms if m[5] < g and m[0] == mid]
                assert not leak, f"match {mid} ({mdate}) is inside its own fit window (<{g})"
            m_ = score_matrix(lam, mu, f.rho)
            ph, pd_, pa = prob_1x2(m_)
            out.append({
                "match_id": mid, "league_id": lid, "date": mdate.isoformat(),
                "refit_date": g.isoformat(), "lam": round(lam, 6), "mu": round(mu, 6),
                "rho": round(f.rho, 6), "fit_n": f.n_matches,
                "p_over15": round(prob_over(m_, 1.5), 6),
                "p_over25": round(prob_over(m_, 2.5), 6),
                "p_over35": round(prob_over(m_, 3.5), 6),
                "p_home": round(ph, 6), "p_draw": round(pd_, 6), "p_away": round(pa, 6),
                "hg": hg, "ag": ag,
            })
    return out, skipped, n_fits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2026-08-20",
                    help="score matches on/after this date (the OOS window)")
    ap.add_argument("--xi", type=float, default=0.0018,
                    help="exponential time-decay per day; 0 disables")
    ap.add_argument("--history-days", type=int, default=1100)
    ap.add_argument("--out", default="/tmp/dc_rates.csv")
    ap.add_argument("--verify", action="store_true", default=True)
    a = ap.parse_args()

    frm = dt.date.fromisoformat(a.from_date)
    out, skipped, n_fits = run(frm, a.xi, a.history_days, a.verify)

    print(f"\n=== Dixon-Coles walk-forward — from {frm}, xi={a.xi}, refit every {REFIT_DAYS}d ===")
    print(f"  league fits performed : {n_fits:,}")
    print(f"  matches scored        : {len(out):,}")
    for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"  skipped ({k:22s}): {v:,}")
    if not out:
        print("  nothing scored")
        return 1

    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"\n  wrote {a.out}")

    # Sanity, not a verdict: raw calibration of the over-2.5 readout.
    act = sum(1 for r in out if r["hg"] + r["ag"] > 2) / len(out)
    pred = sum(r["p_over25"] for r in out) / len(out)
    print(f"  over-2.5  predicted {pred:.4f}  actual {act:.4f}  gap {pred-act:+.4f}")
    acth = sum(1 for r in out if r["hg"] > r["ag"]) / len(out)
    predh = sum(r["p_home"] for r in out) / len(out)
    print(f"  home win  predicted {predh:.4f}  actual {acth:.4f}  gap {predh-acth:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
