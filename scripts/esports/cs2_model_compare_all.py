"""
CS2 model comparison runner — executes every sneak-peek script in sequence
on the same training window and produces one unified table of AUC / LogLoss
/ Brier / Accuracy for the headline model from each version.

Reads the most recent run_id from each sneak peek's persisted output in
cs2_model_backtest_history. If a version hasn't been run today, it triggers
the script and waits.

Run:
    python3 scripts/esports/cs2_model_compare_all.py [--since 2025-06-01]
"""

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query  # noqa: E402


# Map version → (sneak peek script, EXACT feature_set label as persisted).
# Labels must be exact — DB has many similar-looking rows from kd-covered subsets etc.
SNEAK_PEEKS = [
    ("v5", "scripts/esports/cs2_sneak_peek_v5.py", "v5 ALL (kitchen sink)"),
    ("v6", "scripts/esports/cs2_sneak_peek_v6.py", "v6 v5-best + kd"),
    ("v7", "scripts/esports/cs2_sneak_peek_v7.py", "v7 ALL"),
    ("v8", "scripts/esports/cs2_sneak_peek_v8.py", "full_v8 = v7 ALL + kd_diff"),
]
# Baseline + kd-covered-subset labels (these are exact too)
BASELINE_LABELS = ["v6_baseline_hltv_v1", "v7_baseline_hltv_v1", "full_baseline"]
KD_COVERED_LABELS = [
    "kd-covered_baseline",
    "kd-covered_v7 ALL (no kd) — reference",
    "kd-covered_v8 = v7 ALL + kd_diff",
    "kd-covered_kd_diff alone",
    "kd-covered_v5-best + kd (v6-style)",
]


def run_sneak_peek(script_path: str, since: str) -> bool:
    """Run a sneak peek script. Return True if succeeded."""
    print(f"\n>> Running {script_path} --since {since}")
    result = subprocess.run(
        [sys.executable, script_path, "--since", since],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        print(f"   [ERROR] exit {result.returncode}")
        print(result.stderr[-500:])
        return False
    return True


def latest_metric(label_exact: str, since: str) -> dict | None:
    """Find latest backtest_history row by EXACT label + since date."""
    rows = execute_query("""
        SELECT feature_set, n_matches, n_train, n_test,
               auc, logloss, brier, accuracy, run_id, run_at
        FROM cs2_model_backtest_history
        WHERE since_date = %s
          AND feature_set = %s
        ORDER BY run_at DESC
        LIMIT 1
    """, (since, label_exact))
    return rows[0] if rows else None


def find_first_baseline(since: str) -> dict | None:
    for lbl in BASELINE_LABELS:
        r = latest_metric(lbl, since)
        if r:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    ap.add_argument("--rerun", action="store_true",
                    help="Re-run all sneak peeks even if recent backtest_history exists")
    args = ap.parse_args()

    print(f"=== CS2 Model Comparison  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  window: --since {args.since}")
    print(f"  rerun:  {args.rerun}")

    # Step 1 — run sneak peeks (skip if recent + not --rerun)
    for version, script, label in SNEAK_PEEKS:
        if not Path(script).exists():
            print(f"\n  [{version}] script {script} not found — skip")
            continue

        if not args.rerun:
            existing = latest_metric(label, args.since)
            if existing:
                age = (datetime.now(timezone.utc) - existing["run_at"]).total_seconds() / 3600
                if age < 24:
                    print(f"\n  [{version}] using existing run from {age:.1f}h ago")
                    continue
        run_sneak_peek(script, args.since)

    # Step 2 — gather metrics and print table
    print("\n\n" + "=" * 90)
    print(f"  CS2 MODEL HEADLINE — full sample, since {args.since}")
    print("=" * 90)
    print(f"  {'ver':<5} {'feature_set':<40} {'n':>5} {'AUC':>7} {'ΔvBase':>8} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 90)

    # Baseline (hltv_v1 direct)
    base = find_first_baseline(args.since)
    base_auc = float(base["auc"]) if base and base["auc"] else None
    if base:
        print(f"  {'base':<5} {'hltv_v1 direct (Pinnacle market)':<40} {base['n_matches']:>5} "
              f"{float(base['auc']):>7.3f} {'—':>8} {float(base['logloss']):>7.4f} "
              f"{float(base['brier']):>7.4f} {float(base['accuracy']):>6.3f}")

    for version, _, label in SNEAK_PEEKS:
        r = latest_metric(label, args.since)
        if not r or r["auc"] is None:
            print(f"  {version:<5} {label[:38]:<40} (no row)")
            continue
        auc = float(r["auc"])
        delta = (auc - base_auc) if base_auc is not None else None
        delta_str = f"{delta:+.3f}" if delta is not None else "—"
        print(f"  {version:<5} {label[:38]:<40} {r['n_matches']:>5} {auc:>7.3f} {delta_str:>8} "
              f"{float(r['logloss']):>7.4f} {float(r['brier']):>7.4f} {float(r['accuracy']):>6.3f}")

    # K/D-covered subset (only v8 produces this)
    print()
    print(f"  K/D-COVERED SUBSET — matches where both teams have K/D, since {args.since}")
    print("-" * 90)
    for label in [
        "kd-covered :: baseline",
        "kd-covered :: v7 ALL (no kd) — reference",
        "kd-covered :: v8 = v7 ALL + kd_diff",
        "kd-covered :: kd_diff alone",
        "kd-covered :: v5-best + kd (v6-style)",
    ]:
        r = latest_metric(label, args.since)
        if not r or r["auc"] is None:
            continue
        auc = float(r["auc"])
        print(f"  {label[:48]:<48} n={r['n_matches']:>5}  AUC={auc:>5.3f}  "
              f"LogL={float(r['logloss']):>6.4f}  Brier={float(r['brier']):>6.4f}")

    print("\n" + "=" * 90)
    print("  Higher AUC / lower LogL & Brier = better.")
    print("  Δ vs base = AUC lift over the raw Pinnacle market baseline.")
    print("=" * 90)


if __name__ == "__main__":
    main()
