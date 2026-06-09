"""
Train the v7 stacking model on full history + save coefficients to DB.

v7 = logistic regression on [logit(hltv_v1_prob), form_diff, h2h_diff,
tm_diff, rest_diff, rank_diff, bo_centered, pistol_diff, tier_a, tier_b].

Coefficients live in cs2_model_coefficients (model_version='v7') so the
production scorer cs2_v7_predict.py can apply them on every scan.

Run:
    python3 scripts/esports/cs2_v7_train.py --since 2025-01-01

Re-run weekly via cron once enough new data has accumulated.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from cs2_sneak_peek_v7 import (  # type: ignore
    load_matches_with_features, load_team_map, load_pistol_map, load_tier_map,
    build_rows,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score, log_loss  # noqa: E402


# Feature order is FROZEN — cs2_v7_predict.py reads these from DB and
# must apply in the same order. If you change this, bump model_version
# to 'v7.1' / 'v8' etc.
FEATURE_KEYS = [
    "logit_saved",   # logit of hltv_v1 saved win_prob1
    "form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
    "bo_centered", "pistol_diff",
    "tier_s", "tier_a", "tier_b", "tier_c", "tier_d",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01",
                    help="Training window start. 2025-06-01 = best AUC empirically.")
    args = ap.parse_args()

    print(f"=== v7 training  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  --since {args.since}")
    print("  loading data...")

    tm = load_team_map()
    pistol = load_pistol_map()
    tier_map = load_tier_map()
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map)
    print(f"  rows: {len(rows)}")

    # Train on ALL rows — for production deploy we want max signal, not the
    # walk-forward eval split (that's for sneak peek).
    X = np.array([[r[k] for k in FEATURE_KEYS] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)

    model = LogisticRegression(max_iter=2000)
    model.fit(X, y)

    intercept = float(model.intercept_[0])
    coefs = dict(zip(FEATURE_KEYS, [float(c) for c in model.coef_[0]]))

    # In-sample metrics (for sanity, not generalization claim)
    p = model.predict_proba(X)[:, 1]
    in_auc = float(roc_auc_score(y, p))
    in_ll = float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4)))
    print(f"  in-sample AUC {in_auc:.4f}  log_loss {in_ll:.4f}")
    print(f"  intercept {intercept:+.4f}")
    for k, c in coefs.items():
        print(f"    {k:14} {c:+.4f}")

    # Persist to DB
    execute_write("""
        INSERT INTO cs2_model_coefficients
            (model_version, a, b, intercept, coefs, feature_keys, n, auc,
             log_loss, trained_at, seeded_from)
        VALUES ('v7', 1.0, 0.0, %s, %s::jsonb, %s, %s, %s, %s, NOW(), 'cs2_v7_train.py')
        ON CONFLICT (model_version) DO UPDATE SET
            intercept = EXCLUDED.intercept,
            coefs = EXCLUDED.coefs,
            feature_keys = EXCLUDED.feature_keys,
            n = EXCLUDED.n,
            auc = EXCLUDED.auc,
            log_loss = EXCLUDED.log_loss,
            trained_at = NOW()
    """, (intercept, json.dumps(coefs), FEATURE_KEYS, len(rows), in_auc, in_ll))
    print(f"\n  saved → cs2_model_coefficients[model_version='v7']")


if __name__ == "__main__":
    main()
