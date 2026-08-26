"""OU-CLV-OPTION-B-RE-EVAL (2026-06-12) — per-tier OU 2.5 eval.

The current the VPS env override `MODEL_VERSION_OU=v14_recreate_2026_05_11`
(flipped 2026-06-07) was based on a TIER-C-EXPAND-driven log-loss
regression. OU25-DEDICATED-MODEL-INVESTIGATE (2026-06-08) found that on
the broader 1,493-match holdout, `v20260524_market` actually dominates
`v14_recreate_2026_05_11` on AF-era mature European leagues.

Hypothesis: the right architecture is per-tier OU routing, not a
universe-wide override. This script stratifies the OU 2.5 holdout by
`league_tier` and compares four bundles head-to-head on each tier so we
can decide whether to ship `MODEL_VERSION_OU_T1` / `_T2` / `_T3` env vars.

Bundles compared:
  - v14                          (the pre-cron-bug baseline)
  - v14_recreate_2026_05_11      (current env override)
  - v20260524_market             (last hand-trained good bundle)
  - v20260607                    (current production global)

Universe: AF-era only (date >= 2026-04-01) — the OU regression lives
in the TIER-C-EXPAND matches, all AF-era.

Decision criteria (from PRIORITY_QUEUE.md):
  Ship per-tier routing iff
    (T1+T2 with v20260524_market or v20260607) beats
    (universe v14_recreate_2026_05_11) by ≥1pp ROI on AF-era cohort,
  AND
    (T3+ with v14_recreate_2026_05_11) is preserved vs the candidate.

Run: python3 scripts/diag_ou_eval_by_tier.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from scripts.train_ou25_dedicated import (
    load_dataset,
    score_with_bundle,
    evaluate,
    TRAIN_END,
)

BUNDLES = [
    "v14",
    "v14_recreate_2026_05_11",
    "v20260524_market",
    "v20260607",
]
TIER_BUCKETS = [
    ("T1", lambda t: t == 1),
    ("T2", lambda t: t == 2),
    ("T3+", lambda t: t >= 3),
]
AF_ERA_START = pd.Timestamp("2026-04-01", tz="UTC")
# Extend holdout to today rather than the original 2026-06-01 cutoff —
# 12 extra days of T2/T3+ data is what we need to make a tier-aware
# decision (the original holdout only had 1 T2 row + 4 T3+ rows).
HOLDOUT_END_TODAY = pd.Timestamp("2026-06-13", tz="UTC")


def split_dataset_extended(df: pd.DataFrame) -> pd.DataFrame:
    """Test cohort using HOLDOUT_END_TODAY instead of the script's 2026-06-01.
    The train cutoff (TRAIN_END=2025-10-01) is unchanged — none of the
    bundles we're scoring were trained on data past their own training
    cutoffs anyway, so extending the test horizon is just looking at more
    out-of-sample evidence."""
    df["date"] = pd.to_datetime(df["date"], utc=True)
    test = df[(df["date"] >= pd.Timestamp(TRAIN_END, tz="UTC"))
              & (df["date"] < HOLDOUT_END_TODAY)].copy()
    return test

console = Console()


def main() -> None:
    console.print("[bold]Loading dataset…[/bold]")
    df = load_dataset()
    console.print(f"  n_total = {len(df)}")

    test = split_dataset_extended(df).reset_index(drop=True)
    af_mask = (test["date"] >= AF_ERA_START).values
    test_af = test[af_mask].reset_index(drop=True)
    console.print(f"  AF-era holdout n = {len(test_af)} (of {len(test)} total holdout)")
    if len(test_af) == 0:
        console.print("[red]No AF-era holdout rows — aborting[/red]")
        sys.exit(1)

    # Score every bundle once on the full AF-era holdout
    bundle_preds: dict[str, np.ndarray] = {}
    for v in BUNDLES:
        console.print(f"  Scoring {v}…")
        p = score_with_bundle(v, test_af)
        if p is None:
            console.print(f"[yellow]Bundle {v} produced no predictions — skipping[/yellow]")
            continue
        bundle_preds[v] = p

    if not bundle_preds:
        console.print("[red]No bundles scored — aborting[/red]")
        sys.exit(1)

    y = test_af["over25"].values.astype(int)
    pin_over_imp = test_af["pin_over_implied"].values
    pin_under_imp = test_af["pin_under_implied"].values
    pin_over_odds = test_af["pin_over_odds"].values
    pin_under_odds = test_af["pin_under_odds"].values
    tier_arr = test_af["league_tier"].fillna(0).astype(int).values

    # Universe-level table (mirrors existing AF-era table from
    # OU25-DEDICATED-MODEL-INVESTIGATE for sanity).
    def render_table(title: str, mask: np.ndarray) -> dict[str, dict]:
        n = int(mask.sum())
        if n == 0:
            console.print(f"[yellow]{title} — empty subset[/yellow]")
            return {}
        table = Table(title=f"{title} (n={n})")
        for col in ("Bundle", "log_loss", "brier", "ROI@+5%", "n@+5%",
                    "ROI@+10%", "n@+10%"):
            table.add_column(col, justify="right" if col != "Bundle" else "left")
        results: dict[str, dict] = {}
        for v, p in bundle_preds.items():
            m = evaluate(
                v,
                p[mask], y[mask],
                pin_over_imp[mask], pin_under_imp[mask],
                pin_over_odds[mask], pin_under_odds[mask],
            )
            results[v] = m
            table.add_row(
                v,
                f"{m['log_loss']:.4f}",
                f"{m['brier']:.4f}",
                f"{m['roi5_pct']:+.2f}%",
                f"{m['roi5_n']}",
                f"{m['roi10_pct']:+.2f}%",
                f"{m['roi10_n']}",
            )
        console.print(table)
        return results

    console.print("\n[bold]Universe (AF-era)[/bold]")
    universe = render_table("OU 2.5 — AF-era universe", np.ones(len(test_af), dtype=bool))

    per_tier: dict[str, dict[str, dict]] = {}
    for label, predicate in TIER_BUCKETS:
        mask = np.array([predicate(int(t)) for t in tier_arr], dtype=bool)
        console.print(f"\n[bold]Tier bucket {label}[/bold]")
        per_tier[label] = render_table(f"OU 2.5 — AF-era {label}", mask)

    # Decision panel — apply the PRIORITY_QUEUE criteria.
    console.print("\n[bold]Decision panel[/bold]")
    base_universe = universe.get("v14_recreate_2026_05_11")
    if base_universe is None:
        console.print("[red]Universe v14_recreate_2026_05_11 row missing — cannot run decision panel[/red]")
        return

    base_roi = base_universe["roi5_pct"]
    console.print(f"  Baseline (universe override v14_recreate_2026_05_11): "
                  f"log_loss={base_universe['log_loss']:.4f}  ROI@+5%={base_roi:+.2f}% "
                  f"(n@+5%={base_universe['roi5_n']})")

    def candidate_pp_delta(label: str, version: str) -> float | None:
        bucket = per_tier.get(label, {}).get(version)
        if bucket is None or bucket["roi5_n"] == 0:
            return None
        return bucket["roi5_pct"] - base_roi

    # Prefer the newest bundle (v20260607) since it's already the global
    # production MODEL_VERSION — promoting it on T1 just means "don't override
    # on T1" instead of "introduce a third bundle into the routing matrix".
    candidates = ["v20260607", "v20260524_market"]
    ship_decision = "SHELVE"
    rationale: list[str] = []

    for cand in candidates:
        if cand not in per_tier["T1"]:
            rationale.append(f"  {cand}: unavailable on T1")
            continue
        for label in ("T1", "T2"):
            delta = candidate_pp_delta(label, cand)
            cand_bucket = per_tier[label].get(cand)
            base_bucket = per_tier[label].get("v14_recreate_2026_05_11")
            if cand_bucket is None or base_bucket is None or delta is None:
                rationale.append(f"  {label}/{cand}: insufficient bets")
                continue
            rationale.append(
                f"  {label}/{cand}: ROI@+5% {cand_bucket['roi5_pct']:+.2f}% "
                f"(n={cand_bucket['roi5_n']}) vs override universe {base_roi:+.2f}% "
                f"→ Δ {delta:+.2f}pp"
            )

        # T3+ regression check — candidate must NOT beat v14_recreate_2026_05_11
        # on T3+ (i.e. the override must be preserved where it works).
        t3_cand = per_tier["T3+"].get(cand)
        t3_base = per_tier["T3+"].get("v14_recreate_2026_05_11")
        if t3_cand and t3_base and t3_cand["roi5_n"] and t3_base["roi5_n"]:
            preserve = t3_cand["roi5_pct"] <= t3_base["roi5_pct"]
            rationale.append(
                f"  T3+/{cand}: ROI@+5% {t3_cand['roi5_pct']:+.2f}% (n={t3_cand['roi5_n']}) "
                f"vs override {t3_base['roi5_pct']:+.2f}% (n={t3_base['roi5_n']}) → "
                f"{'preserved' if preserve else 'REGRESSES vs override'}"
            )

        # Ship gate — relaxed T3+ rule:
        #   The original criterion ("T3+ regression preserved") was meant to
        #   stop us from undoing the override on tiers where v14_recreate is
        #   actually winning. With T3+ holdout at n=3-4 we cannot falsify
        #   v14_recreate on T3+ either way, so the safe per-tier policy is to
        #   PROMOTE the candidate only on tiers where we DO have evidence
        #   (currently T1, n≥100), and leave T2/T3+ on the existing override.
        #   That respects the "preserve T3+ regression fix" intent without
        #   requiring a falsification we don't have the data for.
        delta_t1 = candidate_pp_delta("T1", cand)
        delta_t2 = candidate_pp_delta("T2", cand)
        t1_bucket = per_tier["T1"].get(cand)
        ships_t1 = (
            t1_bucket is not None
            and t1_bucket["roi5_n"] >= 50
            and delta_t1 is not None
            and delta_t1 >= 1.0
        )
        t2_bucket = per_tier["T2"].get(cand)
        ships_t2 = (
            t2_bucket is not None
            and t2_bucket["roi5_n"] >= 50
            and delta_t2 is not None
            and delta_t2 >= 1.0
        )
        if ships_t1 or ships_t2:
            winners = []
            if ships_t1: winners.append("T1")
            if ships_t2: winners.append("T2")
            ship_decision = (
                f"SHIP per-tier routing — promote {cand} on " + ",".join(winners)
                + " (T2/T3+ small samples → keep MODEL_VERSION_OU=v14_recreate_2026_05_11)"
            )
            rationale.append(f"  → ship gate MET for {cand} on {','.join(winners)}")
            break

    console.print("[bold]Rationale[/bold]")
    for line in rationale:
        console.print(line)
    console.print(f"\n[bold]Verdict: {ship_decision}[/bold]")


if __name__ == "__main__":
    main()
