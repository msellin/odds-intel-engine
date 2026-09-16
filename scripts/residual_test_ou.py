#!/usr/bin/env python3
"""RESIDUAL-TEST-OU — does the O/U model predict anything the market price does not?

THE QUESTION AND WHY IT WAS STILL OPEN
--------------------------------------
`docs/OWN_STRATEGY_AUDIT_2026_09_15.md` §3 closed model-anchored 1x2 with
residual alpha = 0.0000, CI [0, 0.03]. The O/U row said something different:

    Model-anchored O/U | DEAD until re-measured clean | every staked pick came
    from a calibrator applied OUTSIDE its fitted domain | clean alpha untested

That is not the same verdict. 1x2 was measured and found empty; O/U was never
cleanly measured at all, because OU-CALIBRATOR-DOMAIN-MISMATCH (migration 335)
fitted the Platt curve on `predictions.model_probability` — the RAW ensemble
probability — and applied it to `shrunk` (alpha*model + (1-alpha)*pinnacle),
which is ~90% Pinnacle once odds > 3.0. The shipped under-2.5 curve
sigmoid(1.5258*p - 0.8341) has output range [0.3028, 0.6663]: it compressed a
0.2209 sd input to 0.0823 sd, so `edge = cal_prob - 1/odds` degenerated into
"how far is this price from ~0.45" and the 8% floor became a longshot-finder.

Every O/U number this project has ever staked on came through that curve. So
"O/U doesn't work" has never actually been tested — only "that broken curve
doesn't work" has.

WHAT THIS DOES
--------------
Skips the calibrator entirely and asks the clean question with the SAME
pre-registered method as `residual_test.py` (dev/active/residual-test-
preregistration.md), target swapped from home-win to over-2.5:

  * p_model  — RAW over-2.5 probability from the clean-cut bundle, Platt-fitted
               for LEVEL only on the first half, no market shrinkage anywhere
               (blending against Pinnacle before testing against Pinnacle is the
               trap the 1x2 pre-registration already caught)
  * p_market — Pinnacle's over-2.5, proportionally de-vigged, with a Shin arm as
               the pre-registered robustness check
  * alpha    — the blend weight minimising log-loss of alpha*model + (1-alpha)*market,
               FITTED on the first half, EVALUATED on the second
  * two arms — OPTIMISTIC on stored features, REALISTIC with the eight post-hoc
               columns forced NULL. THE REALISTIC ARM DECIDES.

PASS needs blend log-loss < market log-loss AND alpha > 0.02, exactly as 1x2.

The bundle is v20260914_clean_cut0820 and the universe starts 2026-08-20, so
every row is out of sample for it.

RESULT (2026-09-16, n = 7,273 out-of-sample fixtures, over-2.5 base rate 0.5347):

    arm                              alpha    market AUC   model AUC   residual AUC
    OPTIMISTIC (stored features)    0.0000      0.6007      0.5806        0.4514
    REALISTIC  (post-hoc NULLed)    0.0000      0.6007      0.5788        0.4443
    REALISTIC + SHIN                0.0000      0.6007      0.5788        0.4376

    O/U 1.5  (n=4,256): alpha 0.0000, market 0.5984 vs model 0.5636
    O/U 3.5  (n=5,680): alpha 0.0000, market 0.6083 vs model 0.5595

ALPHA IS ZERO ON EVERY ARM AND EVERY LINE. The optimiser, free to choose any
weight in [0,1], puts NOTHING on the model. The model alone is WORSE than the
market alone at every line, and the residual AUC is BELOW 0.5 — where the model
disagrees with the market, the market is right more often than the model.

So the audit row "Model-anchored O/U — DEAD until re-measured clean, clean alpha
untested" is now measured, and it lands where 1x2 landed: alpha = 0.

⚠️ WHAT THIS DOES **NOT** SAY, and the distinction matters before anyone spends
a month on a new O/U model. This is a verdict on THIS model, not on O/U
modelling, and this model is barely an O/U model at all:

  * ⚠️ THREE OF ITS 52 FEATURES HAVE NO COLUMN IN `match_feature_vectors`.
    `pinnacle_implied_home` / `_draw` / `_away` — the market's own 1x2 price —
    live in `match_signals` (train.py:581 builds them from odds_snapshots;
    daily_pipeline_v2 writes them as signals). Any evaluation that reads only
    mfv resolves them to None and `build_X` turns None into 0.0, silently
    handicapping the model on exactly the three features most likely to matter.
    This script now LEFT JOINs them (52.7% available on the universe). The
    verdict did not move. **`scripts/residual_test.py` — the 1x2 test whose
    alpha=0.0000 closed model-anchored 1x2 — still has this defect**; filed as
    RESIDUAL-TEST-ZEROED-MARKET-FEATURES.
  * it shares `feature_cols.pkl` with the 1x2 head — one 52-feature set,
    engineered for match OUTCOME, with the label swapped. The only architectural
    difference between the two trainers is max_depth (5 vs 6) and the objective.
  * the genuinely goal-specific features are nearly empty on the test universe:
    `xg_overperf_home` 3.9% populated, `referee_over25_pct` 9.8%.
  * even `pinnacle_implied_over25` — the market price, handed to the model as an
    input — is present on only 50.8% of rows.

That last point cuts BOTH ways and is worth stating plainly: the model was given
the market's own price as a feature and still cannot match the market's AUC, let
alone beat it. Whatever it is doing with 50 other columns is destroying
information rather than adding any. A model that cannot reproduce an input it
was handed has a problem that more features will not fix.

The `--line 15` / `--line 35` runs apply the OVER-2.5 head to other lines, so
they test "does this model's goal-expectation RANKING beat the market's at that
line" (AUC is rank-based; Platt absorbs the base-rate shift). They are not a
test of a dedicated 1.5 or 3.5 model, because no such model exists.

    python3 scripts/residual_test_ou.py [--line 25] [--cutoff 2026-08-20]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

BUNDLE = "data/models/soccer/v20260914_clean_cut0820"
CUTOFF = "2026-08-20"
POST_HOC = ["season_progress", "league_draw_rate_ytd", "league_clv_efficiency",
            "line_velocity", "goals_for_avg_home", "goals_against_avg_home",
            "goals_for_avg_away", "goals_against_avg_away"]


def ll(ps, ys):
    e = 1e-9
    return -mean(y * math.log(max(min(p, 1 - e), e)) + (1 - y) * math.log(max(min(1 - p, 1 - e), e))
                 for p, y in zip(ps, ys))


def auc(sc, ys):
    p = sorted(zip(sc, ys)); n = len(p); rk = {}; i = 0
    while i < n:
        j = i
        while j + 1 < n and p[j + 1][0] == p[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rk[k] = r
        i = j + 1
    pos = sum(y for _, y in p); neg = n - pos
    if not pos or not neg:
        return float("nan")
    return (sum(rk[k] for k, (_, y) in enumerate(p) if y == 1) - pos * (pos + 1) / 2) / (pos * neg)


def sig(z):
    return 1 / (1 + math.exp(-max(-60, min(60, z))))


def fit_platt(pairs, iters=2500, lr=2.0):
    a, b = 1.0, 0.0; n = len(pairs)
    for _ in range(iters):
        ga = gb = 0.0
        for p, y in pairs:
            e = sig(a * p + b) - y; ga += e * p; gb += e
        a -= lr * ga / n; b -= lr * gb / n
    return a, b


def fit_alpha(pm, pk, ys):
    best = (1e9, 0.0)
    for i in range(201):
        a = i / 200
        v = ll([a * m + (1 - a) * k for m, k in zip(pm, pk)], ys)
        if v < best[0]:
            best = (v, a)
    return best[1]


def shin2(o_over, o_under):
    """Shin de-vig for a TWO-outcome market. Removes proportionally more margin
    from the longshot, which is exactly where a spurious model edge would show
    up — the same reason the 1x2 test carries a Shin arm."""
    q = [1.0 / o_over, 1.0 / o_under]
    t = sum(q)
    z = 0.0
    for _ in range(60):
        zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z)) for qi in q]
        s_ = sum(zs)
        if s_ > 1:
            z += (s_ - 1) * 0.5
        else:
            z -= (1 - s_) * 0.5
        z = max(0.0, min(0.5, z))
    zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z)) for qi in q]
    return zs[0] / sum(zs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", default="25", help="O/U line suffix, e.g. 25 for over_under_25")
    ap.add_argument("--cutoff", default=CUTOFF)
    a_ = ap.parse_args()
    market = f"over_under_{a_.line}"
    thresh = float(a_.line) / 10.0

    c = psycopg2.connect(os.getenv("DATABASE_URL")).cursor(
        cursor_factory=psycopg2.extras.RealDictCursor)
    cols = joblib.load(f"{BUNDLE}/feature_cols.pkl")
    model = joblib.load(f"{BUNDLE}/over_under.pkl")
    real = [x for x in cols if not x.endswith("_missing")]
    c.execute("SELECT column_name FROM information_schema.columns "
              "WHERE table_name='match_feature_vectors'")
    have = {r["column_name"] for r in c.fetchall()}
    real = [x for x in real if x in have]

    # Same pre-KO bound as residual_test.py: POSITIVE minutes_to_kickoff means
    # before kickoff (every WRITER computes kickoff - now, despite
    # supabase_client.store_odds' docstring). Without it ~28% of "pre-kickoff"
    # Pinnacle prices were collected AFTER kickoff.
    c.execute(f"""
        WITH pin AS (SELECT DISTINCT ON (o.match_id, o.selection)
                            o.match_id, o.selection, o.odds::float od
                       FROM odds_snapshots o
                      WHERE o.market=%s AND o.bookmaker='Pinnacle'
                        AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
                        AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT {", ".join(f'mfv."{x}"' for x in real)},
               (m.score_home + m.score_away) AS total_goals, m.date,
               po.od po, pu.od pu,
               sh.v AS pinnacle_implied_home, sd.v AS pinnacle_implied_draw,
               sa.v AS pinnacle_implied_away
          FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
          -- SIGNAL-RESIDENT FEATURES (2026-09-16). pinnacle_implied_home/draw/away
          -- are model features that have NO COLUMN in match_feature_vectors — they
          -- live in match_signals (train.py:581 PINNACLE_FEATURE_COLS builds them
          -- from odds_snapshots; daily_pipeline_v2 writes them as signals). A test
          -- that reads only mfv feeds all three as 0.0 via r.get()->None and
          -- silently handicaps the model on 3 of its 52 real features — the three
          -- carrying the MARKET's own 1x2 price. Joined explicitly.
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_home'
                              ORDER BY captured_at DESC LIMIT 1) sh ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_draw'
                              ORDER BY captured_at DESC LIMIT 1) sd ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_away'
                              ORDER BY captured_at DESC LIMIT 1) sa ON true
          JOIN pin po ON po.match_id = m.id AND po.selection='over'
          JOIN pin pu ON pu.match_id = m.id AND pu.selection='under'
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.score_away IS NOT NULL AND m.date >= %s
         ORDER BY m.date""", (market, a_.cutoff))
    rows = c.fetchall()
    if len(rows) < 200:
        print(f"only {len(rows)} rows — too few to conclude")
        return 1

    ys = [1 if float(r["total_goals"]) > thresh else 0 for r in rows]
    inv = [1 / r["po"] + 1 / r["pu"] for r in rows]
    pk = [(1 / r["po"]) / s for r, s in zip(rows, inv)]
    pk_shin = [shin2(r["po"], r["pu"]) for r in rows]

    print(f"RESIDUAL TEST (O/U {thresh}) — bundle {BUNDLE}, matches on/after {a_.cutoff}")
    print(f"  n = {len(rows)}   over-{thresh} base rate = {mean(ys):.4f}")
    print(f"  Pinnacle overround = {mean(inv):.4f}  (vig ~ {100*(mean(inv)-1):.2f}%)\n")

    def build_X(null_post_hoc: bool):
        def val(r, cl):
            if cl.endswith("_missing"):
                base = cl[:-8]
                if null_post_hoc and base in POST_HOC:
                    return 1.0
                return 1.0 if r.get(base) is None else 0.0
            if null_post_hoc and cl in POST_HOC:
                return 0.0
            v = r.get(cl)
            return float(v) if v is not None else 0.0
        return np.array([[val(r, cl) for cl in cols] for r in rows])

    classes = list(model.classes_)
    # Production reads probs_ou[classes.index(1)] as over25_prob
    # (xgboost_ensemble.py:442) — same convention here, asserted not assumed.
    assert 1 in classes or True in classes, f"unexpected O/U classes {classes}"
    idx = classes.index(1) if 1 in classes else classes.index(True)
    cut = len(rows) // 2

    verdicts = []
    for arm, null_ph, mkt in (("OPTIMISTIC (stored features)", False, pk),
                              ("REALISTIC  (post-hoc NULLed) — DECIDES", True, pk),
                              ("REALISTIC + SHIN de-vig (robustness)", True, pk_shin)):
        P = model.predict_proba(build_X(null_ph))
        raw = [float(p[idx]) for p in P]
        a, b = fit_platt(list(zip(raw[:cut], [float(y) for y in ys[:cut]])))
        pm = [sig(a * p + b) for p in raw]
        alpha = fit_alpha(pm[:cut], mkt[:cut], ys[:cut])
        te = slice(cut, None)
        l_mkt = ll(mkt[te], ys[te])
        l_bl = ll([alpha * m + (1 - alpha) * k for m, k in zip(pm[te], mkt[te])], ys[te])
        l_mod = ll(pm[te], ys[te])
        resid_auc = auc([m - k for m, k in zip(pm[te], mkt[te])], ys[te])
        ok = (l_bl < l_mkt) and (alpha > 0.02)
        print(f"  -- {arm}")
        print(f"     Platt a={a:.3f} b={b:+.3f}   fitted alpha = {alpha:.4f}")
        print(f"     market alone   log-loss {l_mkt:.4f}   AUC {auc(mkt[te], ys[te]):.4f}")
        print(f"     model alone    log-loss {l_mod:.4f}   AUC {auc(pm[te], ys[te]):.4f}")
        print(f"     BLEND          log-loss {l_bl:.4f}")
        print(f"     blend vs market: {100*(l_mkt-l_bl)/l_mkt:+.3f}%")
        print(f"     residual AUC (model-market predicts outcome): {resid_auc:.4f}  (0.5 = no info)")
        print(f"     PRIMARY: {'PASS' if ok else 'FAIL'}  (needs blend<market AND alpha>0.02)")
        print()
        verdicts.append((arm, ok, alpha))

    dec = [v for v in verdicts if "DECIDES" in v[0]][0]
    print(f"VERDICT (realistic arm): {'PASS' if dec[1] else 'FAIL'}  alpha={dec[2]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
