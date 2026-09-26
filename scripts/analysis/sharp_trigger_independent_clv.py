#!/usr/bin/env python3
"""[[#150]] Sharp-anchored bots graded against an INDEPENDENT close.

WHY. A bot that fires when a soft book's price beats de-vigged Pinnacle cannot be graded
against either of the two prices that defined the bet:
  * its OWN book's close — a soft line that never moves closes where it opened, so CLV is 0 raw
    and ≈ −margin corrected, by construction (ANALYSIS_GOTCHAS §85);
  * Pinnacle's close — if Pinnacle does not move, CLV = the trigger edge (≥ 3%) by construction.
The independent judge is the consensus close of the OTHER books: `leg_clv_sharp.clv_cons`
(workers/jobs/clv_sharp.py, #113) — ≥ 5 books, de-vigged, fresh ≤ 60 min before kickoff, with
Pinnacle AND the leg's own book excluded (Pinnacle quotes never enter the consensus input;
`exclude_book` drops the own book and its skins). The #150 row said clv_cons excluded only the
own book — the code excludes both (checked 2026-09-26).

PRE-REGISTRATION (written before the first run, 2026-09-26; nothing below is tuned on output).
  Population  every sharp-anchored bot in the registry (anchor == ANCHOR_SHARP), every settled
              leg in bot_ledger (won/lost) that has leg_clv_sharp.cons_status = 'ok'.
  Metric      mean clv_cons per bot. Uncertainty: bootstrap over MATCHES (10,000 resamples,
              seed 150), one-sided p = share of resampled means <= 0.
  Expected    POSITIVE, small (+0.5 … +2%): if Pinnacle leads, the lagging soft books move
              toward it by kickoff, so the ex-Pinnacle consensus close sits nearer Pinnacle's view
              than the price we took. If the triggers mostly catch Pinnacle noise, the others do
              not follow and the mean sits slightly BELOW zero (≈ −the soft-book margin gap).
  Verdicts    judged only with n >= 50 legs; Holm across the judged bots at alpha 0.05:
                'independent edge'   Holm-adjusted p < 0.05 and mean > 0
                'negative'           upper 95% bootstrap bound < 0
                'undetermined'       otherwise
              Bots with n < 50: 'too few' (reported, not judged).
  Context     (never a verdict) the same legs' Pinnacle-close CLV (clv_sharp), the odds basis
              (§30: 'high_water' would inflate), and coverage = legs with a consensus close /
              settled legs.

Read-only. Prints a table and writes data/models/_research/sharp150/results.json (gitignored).

    python3 scripts/analysis/sharp_trigger_independent_clv.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

N_MIN = 50
B = 10_000
SEED = 150
ALPHA = 0.05


def load(bots: list[str]) -> list[dict]:
    from workers.api_clients.db import execute_query
    return execute_query(
        """
        SELECT l.bot_name, l.match_id::text AS match_id, l.market, l.result,
               c.clv_cons::float AS clv_cons, c.cons_status, c.cons_n_books,
               c.clv_sharp::float AS clv_sharp, c.status AS pin_status, c.odds_basis
          FROM bot_ledger l
          JOIN leg_clv_sharp c
            ON c.leg_id = l.pick_id
           AND c.ledger = CASE l.source WHEN 'sim' THEN 'simulated_bets'
                                        WHEN 'shadow' THEN 'shadow_bets'
                                        WHEN 'forward_test' THEN 'picks_forward_test'
                                        ELSE l.source END
         WHERE l.bot_name = ANY(%s) AND l.result IN ('won', 'lost') AND NOT l.is_inplay
        """, (bots,)) or []


def settled_counts(bots: list[str]) -> dict[str, int]:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT bot_name, count(*) n FROM bot_ledger
            WHERE bot_name = ANY(%s) AND result IN ('won','lost') AND NOT is_inplay
            GROUP BY 1""", (bots,)) or []
    return {r["bot_name"]: int(r["n"]) for r in rows}


def boot(by_match: dict[str, list[float]], rng) -> tuple[float, float, float, float]:
    """mean, one-sided p(mean<=0), 2.5% and 97.5% bounds — resampling MATCHES."""
    keys = list(by_match)
    sums = np.array([sum(by_match[k]) for k in keys])
    cnts = np.array([len(by_match[k]) for k in keys])
    mean = sums.sum() / cnts.sum()
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(mean), float((means <= 0).mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def holm(pvals: dict[str, float]) -> dict[str, float]:
    order = sorted(pvals, key=pvals.get)
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * pvals[k]))
        out[k] = run
    return out


def main() -> None:
    from workers.registry.bot_registry import BOTS, ANCHOR_SHARP
    bots = [b.name for b in BOTS if b.anchor == ANCHOR_SHARP]
    rows = load(bots)
    settled = settled_counts(bots)
    rng = np.random.default_rng(SEED)

    per: dict[str, dict] = {}
    for bot in bots:
        legs = [r for r in rows if r["bot_name"] == bot]
        ok = [r for r in legs if r["cons_status"] == "ok" and r["clv_cons"] is not None]
        by_match: dict[str, list[float]] = defaultdict(list)
        for r in ok:
            by_match[r["match_id"]].append(r["clv_cons"])
        d = {"settled": settled.get(bot, 0), "n": len(ok),
             "coverage": (len(ok) / settled[bot]) if settled.get(bot) else None}
        pin = [r["clv_sharp"] for r in ok if r["pin_status"] == "ok" and r["clv_sharp"] is not None]
        d["odds_basis"] = sorted({r["odds_basis"] for r in ok if r["odds_basis"]})
        d["clv_pinnacle_same_legs"] = float(np.mean(pin)) if pin else None
        if ok:
            d["mean"], d["p"], d["lo"], d["hi"] = boot(by_match, rng)
        per[bot] = d

    judged = {b: d["p"] for b, d in per.items() if d["n"] >= N_MIN}
    adj = holm(judged)
    for b, d in per.items():
        if d["n"] < N_MIN:
            d["verdict"] = "too few"
            continue
        d["p_holm"] = adj[b]
        if d["p_holm"] < ALPHA and d["mean"] > 0:
            d["verdict"] = "independent edge"
        elif d["hi"] < 0:
            d["verdict"] = "negative"
        else:
            d["verdict"] = "undetermined"

    fmt = lambda x: "   —  " if x is None else f"{x*100:+6.2f}%"
    print(f"{'bot':36} {'settled':>7} {'n_cons':>6} {'cover':>6}  {'CLV cons':>8} {'95% CI':>17}  "
          f"{'p_holm':>6}  {'Pin CLV':>8}  basis / verdict")
    for b, d in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
        ci = f"[{d['lo']*100:+.1f},{d['hi']*100:+.1f}]" if "lo" in d else ""
        cov = f"{d['coverage']*100:5.0f}%" if d["coverage"] is not None else "   — "
        ph = f"{d['p_holm']:.3f}" if "p_holm" in d else "  —  "
        print(f"{b:36} {d['settled']:7d} {d['n']:6d} {cov}  {fmt(d.get('mean'))} {ci:>17}  {ph:>6}  "
              f"{fmt(d['clv_pinnacle_same_legs'])}  {'+'.join(d['odds_basis'])} / {d['verdict']}")

    out = ROOT / "data/models/_research/sharp150"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(per, indent=2, default=str))


if __name__ == "__main__":
    main()
