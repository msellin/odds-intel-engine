"""PICKS forward test — the amended checkpoint verdict ([[#156]], 2026-09-25). READ-ONLY.

The stopping rule of the pre-registered sharp-edge forward test was AMENDED on
2026-09-25, before the live arm reached n=200 (see the dated AMENDMENT section in
dev/active/picks-forward-test-preregistration.md). This script computes that
amended verdict. It writes nothing; the checkpoints are taken by hand (#134) and
this is the calculation they are taken with, so the number cannot be re-derived
differently each time.

WHY THE AMENDMENT. The original instrument, `clv_margin_corrected`, is the pick's
odds against THE SAME SOFT BOOK's own close. This rule picks a leg BECAUSE that
book misprices it, and a mispriced soft line that is never corrected closes where
it opened — so its own close scores the pick at roughly minus the margin by
construction (ANALYSIS_GOTCHAS §85). On that measure the live arm could not be
told from the random junk-anchor control (live − junk −0.4pp [−1.9, +1.2]); on the
sharp-anchor close it beat the control by +4.6pp (1X2) and +3.7pp (O/U).

THE AMENDED RULE (pre-stated, do not tune):
  * Instrument = SHARP-ANCHOR CLV per settled (won/lost) leg:
      `leg_clv_sharp.clv_sharp` (odds × Shin-de-vigged fresh Pinnacle close − 1) when
      status = 'ok', else `clv_cons` (>=5-book consensus close) when cons_status = 'ok'.
      The source is recorded. 3–4-book "thin" consensus is EXCLUDED. |clv| > 1 is
      excluded as a data fault (same guard as bot_scoreboard).
  * Populations = arm 'live' vs arm 'junk_anchor', the SAME rule_version (the live
    arm's current one). The '+DEGENERATE_JUNK_DAY1' rows are a different
    rule_version, so they never enter.
  * Statistic = market-stratified difference in mean sharp-anchor CLV, live − control:
      Δ = Σ_m w_m (mean_live_m − mean_ctrl_m),  w_m = live arm's share of scored legs in m.
    Stratified because the two arms carry different market mixes (the control is
    ~61% 1X2, the live arm ~71%), and 1X2 and O/U sit at different CLV levels.
  * Test = one-sided bootstrap, B = 10,000, resampling legs WITH replacement within
    each arm × market cell, fixed seed 20260925. p = share of replicates with Δ* <= 0.
  * Checkpoints = the live arm's SETTLED count (won/lost, as the original rule)
    reaching 200 and 400. At each: CONTINUE only if p < ALPHA (0.025, one-sided —
    i.e. the 97.5% lower bound of Δ is above zero); otherwise STOP. Requiring the
    live arm to clear the control at both looks means a rule with no edge survives
    the pair with probability <= 0.025.
  * The n=800 ROI rules are UNCHANGED. The own-book margin-corrected figure keeps
    being computed and is printed beside the verdict, for both arms, but decides nothing.

    python3 -m scripts.picks_forward_test_checkpoint
    python3 -m scripts.picks_forward_test_checkpoint --rule-version sharp_edge_v4_2026_09_15
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHECKPOINTS = (200, 400)
ALPHA = 0.025            # one-sided, per look
BOOT_B = 10_000
SEED = 20260925
CLV_ABS_MAX = 1.0

SQL = """
SELECT p.arm, p.market, p.outcome, p.clv_margin_corrected,
       CASE WHEN c.status = 'ok' THEN c.clv_sharp
            WHEN c.cons_status = 'ok' THEN c.clv_cons END           AS clv_anchor,
       CASE WHEN c.status = 'ok' THEN 'pinnacle'
            WHEN c.cons_status = 'ok' THEN 'consensus' END          AS anchor_source
  FROM picks_forward_test p
  LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = p.id
 WHERE p.arm IN ('live', 'junk_anchor')
   AND p.rule_version = %s
   AND p.outcome IN ('won', 'lost')
"""


def anchor_value(row: dict) -> float | None:
    v = row.get("clv_anchor")
    if v is None:
        return None
    v = float(v)
    return v if abs(v) <= CLV_ABS_MAX else None


def cells(rows: list[dict], key: str = "clv") -> dict:
    """{(arm, market): [values]} for rows whose `key` is not None."""
    out: dict = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            out[(r["arm"], r["market"])].append(float(r[key]))
    return out


def stratified_delta(c: dict) -> tuple[float | None, dict]:
    """Δ = Σ_m w_m (mean_live_m − mean_ctrl_m), w = live arm's share per market.
    Markets missing from either arm are dropped (and their weight renormalised)."""
    markets = sorted({m for (a, m) in c if a == "live"})
    usable = [m for m in markets if c.get(("live", m)) and c.get(("junk_anchor", m))]
    n_live = sum(len(c[("live", m)]) for m in usable)
    if n_live == 0:
        return None, {}
    per = {}
    d = 0.0
    for m in usable:
        lv, cv = c[("live", m)], c[("junk_anchor", m)]
        diff = sum(lv) / len(lv) - sum(cv) / len(cv)
        w = len(lv) / n_live
        per[m] = {"w": w, "live": sum(lv) / len(lv), "ctrl": sum(cv) / len(cv),
                  "n_live": len(lv), "n_ctrl": len(cv), "diff": diff}
        d += w * diff
    return d, per


def bootstrap(c: dict, b: int = BOOT_B, seed: int = SEED) -> tuple[float, float]:
    """One-sided p (share of Δ* <= 0) and the 97.5% lower bound of Δ."""
    rng = random.Random(seed)
    reps = []
    for _ in range(b):
        rc = {k: [v[rng.randrange(len(v))] for _ in range(len(v))] for k, v in c.items() if v}
        d, _ = stratified_delta(rc)
        if d is not None:
            reps.append(d)
    reps.sort()
    p = sum(1 for x in reps if x <= 0) / len(reps)
    lo = reps[int(len(reps) * ALPHA)]
    return p, lo


def verdict(n_settled_live: int, p: float | None) -> str:
    reached = [k for k in CHECKPOINTS if n_settled_live >= k]
    if not reached:
        return f"NO CHECKPOINT YET (live settled n={n_settled_live}; next at {CHECKPOINTS[0]})"
    k = reached[-1]
    if p is None:
        return f"n={k} CHECKPOINT: NO SHARP-ANCHOR DATA — cannot pass, STOP pending a data fix"
    if p < ALPHA:
        return f"n={k} CHECKPOINT: CONTINUE — live beats control (one-sided p={p:.4f} < {ALPHA})"
    return f"n={k} CHECKPOINT: STOP — live does not beat control (one-sided p={p:.4f} >= {ALPHA})"


def current_live_rule_version(cur) -> str:
    cur.execute("""SELECT rule_version FROM picks_forward_test WHERE arm = 'live'
                    ORDER BY published_at DESC LIMIT 1""")
    return cur.fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rule-version", help="default: the live arm's newest rule_version")
    args = ap.parse_args()

    from workers.api_clients.db import get_conn
    import psycopg2.extras
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET TRANSACTION READ ONLY")
        rv = args.rule_version or current_live_rule_version(conn.cursor())
        cur.execute(SQL, (rv,))
        rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        r["clv"] = anchor_value(r)
        mc = r.get("clv_margin_corrected")
        r["clv_mc"] = float(mc) if mc is not None and abs(float(mc)) <= CLV_ABS_MAX else None

    n_live = sum(1 for r in rows if r["arm"] == "live")
    n_ctrl = sum(1 for r in rows if r["arm"] == "junk_anchor")
    print(f"rule_version {rv} — settled: live {n_live}, control {n_ctrl}\n")

    for arm in ("live", "junk_anchor"):
        a = [r for r in rows if r["arm"] == arm]
        src = defaultdict(int)
        for r in a:
            src[r["anchor_source"] if r["clv"] is not None else "unscored"] += 1
        print(f"  {arm:12s} sources: " + ", ".join(f"{k} {v}" for k, v in sorted(src.items())))
    print()

    out = {}
    for label, key in (("SHARP-ANCHOR CLV (decides)", "clv"),
                       ("own-book margin-corrected CLV (reported, decides nothing)", "clv_mc")):
        c = cells(rows, key)
        d, per = stratified_delta(c)
        print(label)
        for m, x in per.items():
            print(f"  {m:14s} live {x['live']*100:+.2f}% (n={x['n_live']})  "
                  f"control {x['ctrl']*100:+.2f}% (n={x['n_ctrl']})  Δ {x['diff']*100:+.2f}pp  w={x['w']:.2f}")
        if d is None:
            print("  no data\n")
            out[key] = None
            continue
        p, lo = bootstrap(c)
        out[key] = p
        print(f"  stratified Δ {d*100:+.2f}pp · 97.5% lower bound {lo*100:+.2f}pp · one-sided p={p:.4f}\n")

    print("VERDICT (amended rule, sharp-anchor CLV): " + verdict(n_live, out.get("clv")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
