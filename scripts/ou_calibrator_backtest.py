#!/usr/bin/env python3
"""OU-CALIBRATOR-DOMAIN-MISMATCH — what would have happened under each calibrator.

WHY THIS EXISTS
---------------
`scripts/fit_calibration_from_predictions.py` fits the O/U Platt curve on
`predictions.model_probability` — the RAW ensemble probability. But
`improvements.calibrate_prob` applies it to `shrunk`, which is
`alpha * model_prob + (1 - alpha) * pinnacle_devig` and is ~90% Pinnacle once
odds > 3.0 (alpha floors at 0.10). Fitted in one domain, applied in another.

The fit's own out-of-sample ECE check therefore measured a function that is
never executed, which is why a harmful curve passed validation — the same trap
`_fit_platt`'s docstring already warns about for logit-vs-probability.

This script replays the real gate over the real universe under four arms so the
choice between them is measured rather than argued:

  A  live      sigmoid(a*shrunk + b) with the params actually in production
  B  none      cal = shrunk            (what deleting the rows gives you)
  C  refit     Platt refitted ON shrunk (correct domain, 1-feature)
  D  refit+odds  2-feature sigmoid(a*shrunk + c*log(odds) + b)

Arms C and D are fit on a time-ordered TRAIN slice and scored only on TEST, so
no arm sees its own evaluation data.

    python3 scripts/ou_calibrator_backtest.py
    python3 scripts/ou_calibrator_backtest.py --since 2026-06-01 --edge-floor 0.08
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
import os  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

# Mirrors improvements.calibrate_prob. Kept as literals rather than importing so
# the backtest still reports the OLD behaviour after the production code changes.
ALPHA_FALLBACK = {1: 0.2333, 2: 0.2871, 3: 0.3064, 4: 0.1295}
LIVE_PLATT = {                       # fitted 2026-09-03 10:49:20 UTC, never refit
    "under": (1.5258414484479286, -0.8341459180945152),
    "over": (1.5261901280388852, -0.6923935415865159),
}
ACCESSIBLE = ("Coolbet", "Betano", "Unibet", "Epicbet")
ODDS_MIN, ODDS_MAX = 1.30, 4.50      # bot_v10_all
MIN_PROB = 0.30

sig = lambda z: 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def alpha_for(tier: int, odds: float, alphas: dict) -> float:
    """Exactly improvements._get_shrinkage_alpha + CAL-ALPHA-ODDS (V2 default OFF)."""
    a = alphas.get(f"shrinkage_alpha_t{tier}_goalline", ALPHA_FALLBACK.get(tier, 0.25))
    return max(a - 0.20, 0.10) if odds > 3.0 else a


def fit_platt(pairs, iters=1500, lr=2.0):
    """y ~ sigmoid(a*p + b), linear in the probability — mirrors apply_platt."""
    a, b = 1.0, 0.0
    n = len(pairs)
    for _ in range(iters):
        ga = gb = 0.0
        for p, y in pairs:
            e = sig(a * p + b) - y
            ga += e * p
            gb += e
        a -= lr * ga / n
        b -= lr * gb / n
    return a, b


def fit_platt2(triples, iters=1500, lr=2.0):
    """y ~ sigmoid(a*p + c*log(odds) + b) — the 2-feature branch (platt_c)."""
    a, b, c = 1.0, 0.0, 0.0
    n = len(triples)
    for _ in range(iters):
        ga = gb = gc = 0.0
        for p, lo, y in triples:
            e = sig(a * p + c * lo + b) - y
            ga += e * p
            gb += e
            gc += e * lo
        a -= lr * ga / n
        b -= lr * gb / n
        c -= lr * gc / n
    return a, b, c


def load(since: str):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), connect_timeout=20)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT market, platt_a::float a FROM model_calibration "
                "WHERE market LIKE 'shrinkage_alpha%%_goalline'")
    alphas = {r["market"]: r["a"] for r in cur.fetchall()}

    # One row per (match, selection): model prob, Pinnacle anchor (pre-match and
    # closing), and the best accessible book price. DISTINCT ON picks the latest
    # pre-match quote, mirroring the pipeline's "freshest snapshot" behaviour.
    cur.execute(
        """
        WITH pin AS (
          SELECT DISTINCT ON (o.match_id, o.selection)
                 o.match_id, o.selection, o.odds::float AS odds
            FROM odds_snapshots o
           WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
             AND o.is_live IS NOT TRUE AND o.odds > 1.01
           ORDER BY o.match_id, o.selection, o.timestamp DESC),
        pinclose AS (
          SELECT DISTINCT ON (o.match_id, o.selection)
                 o.match_id, o.selection, o.odds::float AS odds
            FROM odds_snapshots o
           WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
             AND o.is_live IS NOT TRUE AND o.is_closing IS TRUE AND o.odds > 1.01
           ORDER BY o.match_id, o.selection, o.timestamp DESC),
        -- STALE-BEST-ODDS guard: take each book's LAST pre-match quote, then the
        -- max across books. Taking max() over every snapshot ever taken would
        -- pick "the highest price ever seen, not one on offer" (commit 5d8985a)
        -- and inflates exactly the tail a tight edge floor selects. A first run
        -- of this backtest did that and showed arms B/C at +19-24 pct ROI and
        -- +25 pct CLV, which is the artifact, not the strategy.
        -- (Keep bare per-cent signs out of this SQL entirely: one in a comment
        --  breaks psycopg2 parameter interpolation -- see HOTFIX d93e242.)
        lastq AS (
          SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
                 o.match_id, o.selection, o.bookmaker, o.odds::float AS odds
            FROM odds_snapshots o
           WHERE o.market='over_under_25' AND o.is_live IS NOT TRUE
             AND o.bookmaker = ANY(%s) AND o.odds > 1.01
           ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC),
        best AS (
          SELECT match_id, selection, max(odds)::float AS odds
            FROM lastq GROUP BY match_id, selection),
        pred AS (
          SELECT DISTINCT ON (p.match_id, p.market) p.match_id, p.market,
                 p.model_probability::float AS mp
            FROM predictions p
           WHERE p.source='ensemble' AND p.market IN ('over25','under25')
             AND p.model_probability IS NOT NULL
           ORDER BY p.match_id, p.market, p.created_at DESC)
        SELECT m.id, m.date, COALESCE(l.tier,1) AS tier,
               (m.score_home + m.score_away) AS tg,
               po.mp AS mp_over, pu.mp AS mp_under,
               pio.odds AS pin_over, piu.odds AS pin_under,
               pco.odds AS pinc_over, pcu.odds AS pinc_under,
               bo.odds AS best_over, bu.odds AS best_under
          FROM matches m
          LEFT JOIN leagues l ON l.id = m.league_id
          JOIN pred po ON po.match_id=m.id AND po.market='over25'
          JOIN pred pu ON pu.match_id=m.id AND pu.market='under25'
          JOIN pin pio ON pio.match_id=m.id AND pio.selection='over'
          JOIN pin piu ON piu.match_id=m.id AND piu.selection='under'
          LEFT JOIN pinclose pco ON pco.match_id=m.id AND pco.selection='over'
          LEFT JOIN pinclose pcu ON pcu.match_id=m.id AND pcu.selection='under'
          LEFT JOIN best bo ON bo.match_id=m.id AND bo.selection='over'
          LEFT JOIN best bu ON bu.match_id=m.id AND bu.selection='under'
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.date >= %s AND m.date < NOW()
         ORDER BY m.date
        """, (list(ACCESSIBLE), since))
    return cur.fetchall(), alphas


def build(rows, alphas):
    """Expand each match into its over/under candidates with production's `shrunk`."""
    out = []
    for r in rows:
        ov = 1.0 / r["pin_over"]
        un = 1.0 / r["pin_under"]
        tot = ov + un
        if tot <= 0:
            continue
        for sel in ("over", "under"):
            odds = r[f"best_{sel}"]
            if not odds or not (ODDS_MIN <= odds <= ODDS_MAX):
                continue
            mp = r[f"mp_{sel}"]
            anchor = (ov if sel == "over" else un) / tot        # Shin-free proportional de-vig
            a = alpha_for(int(r["tier"] or 1), odds, alphas)
            shrunk = a * mp + (1 - a) * anchor
            # CLV anchor: de-vigged Pinnacle CLOSE for the same selection
            cl = None
            if r["pinc_over"] and r["pinc_under"]:
                co, cu = 1.0 / r["pinc_over"], 1.0 / r["pinc_under"]
                cl = (co if sel == "over" else cu) / (co + cu)
            won = (r["tg"] > 2) if sel == "over" else (r["tg"] < 3)
            out.append({"d": r["date"], "sel": sel, "odds": odds, "shrunk": shrunk,
                        "won": won, "close": cl})
    return out


def evaluate(cands, cal_fn, edge_floor):
    picks = []
    for c in cands:
        cal = cal_fn(c)
        if cal < MIN_PROB:
            continue
        if cal - 1.0 / c["odds"] < edge_floor:
            continue
        picks.append((c, cal))
    if not picks:
        return None
    n = len(picks)
    w = sum(1 for c, _ in picks if c["won"])
    rets = [(c["odds"] - 1) if c["won"] else -1.0 for c, _ in picks]
    mu = sum(rets) / n
    sd = (sum((x - mu) ** 2 for x in rets) / (n - 1)) ** 0.5 if n > 1 else 0.0
    t = mu / (sd / math.sqrt(n)) if sd > 0 else 0.0
    clv = [c["odds"] * c["close"] - 1 for c, _ in picks if c["close"]]
    ct = float("nan")
    if len(clv) > 1:
        cm = sum(clv) / len(clv)
        csd = (sum((x - cm) ** 2 for x in clv) / (len(clv) - 1)) ** 0.5
        ct = cm / (csd / math.sqrt(len(clv))) if csd > 0 else 0.0
    return {"n": n, "hit": 100 * w / n, "claim": 100 * sum(x for _, x in picks) / n,
            "roi": 100 * mu, "t": t,
            "clv": 100 * sum(clv) / len(clv) if clv else float("nan"),
            "clv_t": ct, "nclv": len(clv)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--edge-floor", type=float, default=0.08)
    ap.add_argument("--train-frac", type=float, default=0.60)
    args = ap.parse_args()

    rows, alphas = load(args.since)
    cands = build(rows, alphas)
    cands.sort(key=lambda c: c["d"])
    cut = int(len(cands) * args.train_frac)
    train, test = cands[:cut], cands[cut:]
    console.print(f"\n[bold]OU calibrator backtest[/bold]  universe={len(cands):,} candidates "
                  f"from {len(rows):,} matches since {args.since}")
    console.print(f"[dim]TRAIN {len(train):,} ({train[0]['d'].date()}..{train[-1]['d'].date()})  "
                  f"TEST {len(test):,} ({test[0]['d'].date()}..{test[-1]['d'].date()})  "
                  f"edge floor {args.edge_floor:.0%}[/dim]\n")

    # Arms C and D are fit per selection on TRAIN only.
    fits1, fits2 = {}, {}
    for sel in ("over", "under"):
        tr = [c for c in train if c["sel"] == sel]
        fits1[sel] = fit_platt([(c["shrunk"], 1.0 if c["won"] else 0.0) for c in tr])
        fits2[sel] = fit_platt2([(c["shrunk"], math.log(c["odds"]),
                                  1.0 if c["won"] else 0.0) for c in tr])

    arms = {
        "A  live (broken)": lambda c: sig(LIVE_PLATT[c["sel"]][0] * c["shrunk"]
                                          + LIVE_PLATT[c["sel"]][1]),
        "B  no calibrator": lambda c: c["shrunk"],
        "C  refit on shrunk": lambda c: sig(fits1[c["sel"]][0] * c["shrunk"]
                                            + fits1[c["sel"]][1]),
        "D  refit + odds": lambda c: sig(fits2[c["sel"]][0] * c["shrunk"]
                                         + fits2[c["sel"]][2] * math.log(c["odds"])
                                         + fits2[c["sel"]][1]),
    }

    t = Table(show_header=True, header_style="bold")
    for col in ("arm", "picks", "claimed", "actual", "gap", "ROI", "t", "CLV vs Pin close"):
        t.add_column(col, justify="right" if col != "arm" else "left")
    for name, fn in arms.items():
        r = evaluate(test, fn, args.edge_floor)
        if not r:
            t.add_row(name, "0", "—", "—", "—", "—", "—", "—")
            continue
        gap = r["claim"] - r["hit"]
        col = "green" if r["roi"] > 0 else "red"
        t.add_row(name, f"{r['n']:,}", f"{r['claim']:.1f}%", f"{r['hit']:.1f}%",
                  f"[{'red' if gap > 3 else 'green'}]{gap:+.1f}pp[/]",
                  f"[{col}]{r['roi']:+.1f}%[/]", f"{r['t']:+.2f}",
                  f"{r['clv']:+.2f}% t={r['clv_t']:+.1f} (n={r['nclv']:,})")
    console.print(t)
    console.print("\n[dim]TEST slice only; arms C and D never see it. 'gap' is claimed minus "
                  "actual hit rate — a positive gap is overconfidence, which is the defect "
                  "this whole investigation is about.[/dim]")
    console.print(f"[dim]fits on shrunk: " + "  ".join(
        f"{s}: a={fits1[s][0]:.3f} b={fits1[s][1]:+.3f}" for s in fits1) + "[/dim]")
    console.print(f"[dim]2-feature:      " + "  ".join(
        f"{s}: a={fits2[s][0]:.3f} b={fits2[s][1]:+.3f} c={fits2[s][2]:+.3f}" for s in fits2) + "[/dim]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
