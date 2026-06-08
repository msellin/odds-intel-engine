"""OU25-DEDICATED-MODEL-INVESTIGATE — train + evaluate a dedicated OU 2.5 model.

Plan: dev/active/ou25-dedicated-model-plan.md
Context: dev/active/ou25-dedicated-model-context.md

Steps:
  1. Load training universe (Pinnacle paired OU 2.5 closing + MFV + finished matches)
  2. Time-ordered split: train pre-2025-10-01, test 2025-10-01+
  3. Train two XGBoost count:poisson regressors (home_goals, away_goals)
  4. Wrap into `Ou25PoissonWrapper` (sklearn-classifier interface)
  5. Evaluate on holdout — log_loss / brier / ECE / ROI@+5% / ROI@+10%
  6. Rescore SAME holdout matches with baseline bundles (v14, v14_recreate_2026_05_11,
     v20260524_market) and compare
  7. Apply ship gate; if pass, save bundle to data/models/soccer/ou25_dedicated_v1/

Usage:
  python3 scripts/train_ou25_dedicated.py
  python3 scripts/train_ou25_dedicated.py --eval-only   # skip retraining if bundle exists
  python3 scripts/train_ou25_dedicated.py --save-anyway # save bundle even if gate fails
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.ou25_dedicated import Ou25PoissonWrapper, _over25_prob_from_lambdas  # noqa: E402

console = Console()

FEATURE_COLS = [
    "elo_home", "elo_away", "elo_diff",
    "league_tier",
    "pinnacle_drift_home", "pinnacle_drift_draw", "pinnacle_drift_away",
    "form_ppg_home", "form_ppg_away",
]

TRAIN_END = "2025-10-01"     # exclusive
HOLDOUT_END = "2026-06-01"   # exclusive — picks up 2025-10 through 2026-05
BUNDLE_VERSION = "ou25_dedicated_v1"
BASELINE_BUNDLES = ["v14", "v14_recreate_2026_05_11", "v20260524_market"]
SHIP_GATE_BASELINE = "v14_recreate_2026_05_11"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_dataset() -> pd.DataFrame:
    """Pinnacle paired OU 2.5 closing + finished match + MFV + Pinnacle OU 2.5 implied."""
    feat_sql = ", ".join(f"mfv.{c}" for c in FEATURE_COLS)
    sql = f"""
        WITH paired AS (
            SELECT match_id
            FROM odds_snapshots
            WHERE market='over_under_25' AND is_closing=TRUE AND bookmaker='Pinnacle'
            GROUP BY match_id HAVING COUNT(DISTINCT selection)=2
        ),
        pin_ou AS (
            SELECT match_id,
                   MAX(odds) FILTER (WHERE selection='over')  AS pin_over_odds,
                   MAX(odds) FILTER (WHERE selection='under') AS pin_under_odds
            FROM odds_snapshots
            WHERE market='over_under_25' AND is_closing=TRUE AND bookmaker='Pinnacle'
            GROUP BY match_id
        )
        SELECT m.id AS match_id,
               m.date,
               m.score_home,
               m.score_away,
               {feat_sql},
               pin_ou.pin_over_odds,
               pin_ou.pin_under_odds
        FROM paired p
        JOIN matches m ON m.id = p.match_id
        JOIN match_feature_vectors mfv ON mfv.match_id = p.match_id
        JOIN pin_ou ON pin_ou.match_id = p.match_id
        WHERE m.score_home IS NOT NULL AND m.score_away IS NOT NULL
        ORDER BY m.date
    """
    rows = execute_query(sql)
    df = pd.DataFrame(rows)
    # Numeric (DECIMAL) columns come back as decimal.Decimal — cast to float
    for c in FEATURE_COLS + ["pin_over_odds", "pin_under_odds", "score_home", "score_away"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["total_goals"] = df["score_home"] + df["score_away"]
    df["over25"] = (df["total_goals"] > 2).astype(int)

    # Implied probability = 1 / odds (raw, includes book margin)
    df["pin_over_implied"] = 1.0 / df["pin_over_odds"].astype(float)
    df["pin_under_implied"] = 1.0 / df["pin_under_odds"].astype(float)

    # Devig Pinnacle OU 2.5 implied (overround → fair). Margin ~5-6%; small but
    # using raw implied as the benchmark exaggerates ROI in our favour.
    s = df["pin_over_implied"] + df["pin_under_implied"]
    df["pin_over_devig"] = df["pin_over_implied"] / s
    df["pin_under_devig"] = df["pin_under_implied"] / s

    # Impute missing form_ppg with league_tier-stratified mean
    for col in ("form_ppg_home", "form_ppg_away"):
        means = df.groupby("league_tier")[col].transform("mean")
        df[col] = df[col].fillna(means)
        df[col] = df[col].fillna(df[col].mean())  # last-resort global

    return df


def split_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df["date"] = pd.to_datetime(df["date"], utc=True)
    train = df[df["date"] < pd.Timestamp(TRAIN_END, tz="UTC")].copy()
    test = df[(df["date"] >= pd.Timestamp(TRAIN_END, tz="UTC"))
              & (df["date"] < pd.Timestamp(HOLDOUT_END, tz="UTC"))].copy()
    return train, test


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_poisson_regressors(train: pd.DataFrame, max_depth: int = 5, n_estimators: int = 400):
    import xgboost as xgb

    X = train[FEATURE_COLS]
    yh = train["score_home"]
    ya = train["score_away"]

    kw = dict(
        objective="count:poisson",
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=4,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )

    home_model = xgb.XGBRegressor(**kw)
    away_model = xgb.XGBRegressor(**kw)
    home_model.fit(X, yh)
    away_model.fit(X, ya)
    return home_model, away_model


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def log_loss(y_true: np.ndarray, p_pred: np.ndarray, eps: float = 1e-9) -> float:
    p = np.clip(p_pred, eps, 1.0 - eps)
    return float(-np.mean(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)))


def brier(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    return float(np.mean((p_pred - y_true) ** 2))


def ece(y_true: np.ndarray, p_pred: np.ndarray, n_bins: int = 10) -> float:
    bin_edges = np.linspace(0, 1, n_bins + 1)
    e = 0.0
    n = len(y_true)
    for i in range(n_bins):
        mask = (p_pred >= bin_edges[i]) & (p_pred < bin_edges[i + 1] if i < n_bins - 1 else p_pred <= bin_edges[i + 1])
        if mask.sum() == 0:
            continue
        avg_p = p_pred[mask].mean()
        avg_y = y_true[mask].mean()
        e += (mask.sum() / n) * abs(avg_p - avg_y)
    return float(e)


def roi_at_edge(p_over: np.ndarray, y_over: np.ndarray,
                pin_over_implied: np.ndarray, pin_under_implied: np.ndarray,
                pin_over_odds: np.ndarray, pin_under_odds: np.ndarray,
                edge_threshold: float) -> tuple[float, int]:
    """Flat-stake ROI: bet over (or under) whichever side has model edge ≥ threshold.

    Edge = model_prob − pinnacle_devig_implied. Stakes 1u per bet, settles at the
    Pinnacle odds for that side.
    """
    pnl = 0.0
    n = 0
    for i in range(len(p_over)):
        po = p_over[i]
        pu = 1.0 - po
        # Devig implied (use the raw implied / sum)
        s = pin_over_implied[i] + pin_under_implied[i]
        pin_o = pin_over_implied[i] / s
        pin_u = pin_under_implied[i] / s
        edge_over = po - pin_o
        edge_under = pu - pin_u
        # Pick the side with the bigger positive edge above threshold
        if edge_over >= edge_threshold and edge_over >= edge_under:
            n += 1
            won = y_over[i] == 1
            pnl += (pin_over_odds[i] - 1.0) if won else -1.0
        elif edge_under >= edge_threshold:
            n += 1
            won = y_over[i] == 0
            pnl += (pin_under_odds[i] - 1.0) if won else -1.0
    return (pnl / n * 100.0) if n > 0 else 0.0, n


def evaluate(name: str, p_over: np.ndarray, y_over: np.ndarray,
             pin_over_implied: np.ndarray, pin_under_implied: np.ndarray,
             pin_over_odds: np.ndarray, pin_under_odds: np.ndarray) -> dict:
    roi5, n5 = roi_at_edge(p_over, y_over, pin_over_implied, pin_under_implied,
                           pin_over_odds, pin_under_odds, 0.05)
    roi10, n10 = roi_at_edge(p_over, y_over, pin_over_implied, pin_under_implied,
                             pin_over_odds, pin_under_odds, 0.10)
    return {
        "name": name,
        "n": len(y_over),
        "log_loss": log_loss(y_over, p_over),
        "brier": brier(y_over, p_over),
        "ece": ece(y_over, p_over),
        "roi5_pct": roi5,
        "roi5_n": n5,
        "roi10_pct": roi10,
        "roi10_n": n10,
    }


# ---------------------------------------------------------------------------
# Baseline bundle scoring
# ---------------------------------------------------------------------------

def score_with_bundle(bundle_version: str, test: pd.DataFrame) -> np.ndarray | None:
    """Load a saved bundle and run its over_under head on the holdout rows.

    Mirrors production `_build_row_from_mfv`: pulls full MFV row per match,
    fills missing columns with 0 (same as `_build_row_from_mfv` line 242),
    recomputes `<col>_missing` indicator columns from raw MFV values."""
    model_path = MODELS_DIR / bundle_version
    if not (model_path / "feature_cols.pkl").exists():
        console.print(f"[yellow]Bundle {bundle_version} missing — skip.[/yellow]")
        return None
    try:
        feature_cols = joblib.load(model_path / "feature_cols.pkl")
        ou_model = joblib.load(model_path / "over_under.pkl")
    except Exception as e:
        console.print(f"[yellow]Bundle {bundle_version} load error: {e} — skip.[/yellow]")
        return None

    # Pull full MFV rows for the holdout (SELECT * — matches _build_row_from_mfv)
    ids = tuple(str(x) for x in test["match_id"].tolist())
    if not ids:
        return None
    mfv_rows = execute_query(
        "SELECT * FROM match_feature_vectors WHERE match_id IN %s",
        params=(ids,),
    )
    mfv = pd.DataFrame(mfv_rows)
    if mfv.empty:
        console.print(f"[yellow]Bundle {bundle_version}: no MFV rows for holdout[/yellow]")
        return None
    mfv["match_id"] = mfv["match_id"].astype(str)

    # Order MFV rows to match `test`
    order = pd.DataFrame({"match_id": test["match_id"].astype(str).values})
    mfv = order.merge(mfv, on="match_id", how="left")

    # Build the X dataframe column-by-column per bundle's feature_cols
    X = pd.DataFrame(index=range(len(test)))
    for col in feature_cols:
        if col == "tier":
            X[col] = test["league_tier"].astype(float).fillna(0)
            continue
        if col.endswith("_missing"):
            base = col[:-len("_missing")]
            base_vals = mfv[base] if base in mfv.columns else pd.Series([None] * len(mfv))
            X[col] = base_vals.isna().astype(int)
            continue
        if col in mfv.columns:
            X[col] = pd.to_numeric(mfv[col], errors="coerce").fillna(0)
        else:
            X[col] = 0.0  # production zero-fill (line 242)

    try:
        probs = ou_model.predict_proba(X)
    except Exception as e:
        console.print(f"[yellow]Bundle {bundle_version} predict_proba error: {e}[/yellow]")
        return None

    classes = list(getattr(ou_model, "classes_", []))
    if True in classes:
        idx = classes.index(True)
    elif 1 in classes:
        idx = classes.index(1)
    else:
        idx = probs.shape[1] - 1  # last col = over
    return probs[:, idx]


# ---------------------------------------------------------------------------
# Bundle save
# ---------------------------------------------------------------------------

def save_bundle(wrapper: Ou25PoissonWrapper, home_model, away_model,
                feature_cols: list[str], stub_source: str = "v14_recreate_2026_05_11") -> None:
    dest = MODELS_DIR / BUNDLE_VERSION
    dest.mkdir(parents=True, exist_ok=True)
    joblib.dump(feature_cols, dest / "feature_cols.pkl")
    joblib.dump(wrapper, dest / "over_under.pkl")
    joblib.dump(home_model, dest / "home_goals.pkl")
    joblib.dump(away_model, dest / "away_goals.pkl")

    # Stub result_1x2.pkl from a working bundle so _load_bundle's all-or-nothing
    # loader doesn't reject ours. result_1x2 is never called for OU inference
    # (production routes 1X2 to MODEL_VERSION, not MODEL_VERSION_OU).
    stub_src = MODELS_DIR / stub_source / "result_1x2.pkl"
    stub_dst = dest / "result_1x2.pkl"
    if stub_src.exists():
        shutil.copy2(stub_src, stub_dst)
    else:
        console.print(f"[yellow]Stub source {stub_src} missing — bundle will fail to load.[/yellow]")

    console.print(f"[green]Bundle saved: {dest}[/green]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-anyway", action="store_true",
                    help="Save bundle even if ship gate fails")
    ap.add_argument("--no-save", action="store_true",
                    help="Never save bundle (eval only)")
    ap.add_argument("--results-out", default=str(ENGINE_DIR / "dev" / "active"
                                                  / "ou25-dedicated-model-results.md"))
    args = ap.parse_args()

    console.print("[bold]Loading dataset…[/bold]")
    df = load_dataset()
    console.print(f"  n_total = {len(df)}, over25 base rate = {df['over25'].mean():.3f}")

    train, test = split_dataset(df)
    console.print(f"  n_train = {len(train)} (≤ {TRAIN_END}), n_test = {len(test)} ({TRAIN_END} – {HOLDOUT_END})")

    console.print("\n[bold]Training XGBoost count:poisson regressors…[/bold]")
    home_model, away_model = train_poisson_regressors(train)

    # Show training-set fit quality (sanity)
    yh_pred = np.maximum(0.05, home_model.predict(train[FEATURE_COLS]))
    ya_pred = np.maximum(0.05, away_model.predict(train[FEATURE_COLS]))
    train_over_p = np.array([_over25_prob_from_lambdas(h, a) for h, a in zip(yh_pred, ya_pred)])
    console.print(f"  train log_loss = {log_loss(train['over25'].values, train_over_p):.4f}")
    console.print(f"  train mean exp_h = {yh_pred.mean():.3f}, exp_a = {ya_pred.mean():.3f}, actual h={train['score_home'].mean():.3f}, a={train['score_away'].mean():.3f}")

    wrapper = Ou25PoissonWrapper(home_model, away_model, FEATURE_COLS)

    console.print("\n[bold]Scoring holdout — dedicated model[/bold]")
    p_over_dedicated = wrapper.predict_proba(test[FEATURE_COLS])[:, 1]
    y_test = test["over25"].values
    pin_over_imp = test["pin_over_implied"].values
    pin_under_imp = test["pin_under_implied"].values
    pin_over_odds_ = test["pin_over_odds"].values
    pin_under_odds_ = test["pin_under_odds"].values

    # Per-bundle predictions on full holdout
    bundle_preds = {BUNDLE_VERSION: p_over_dedicated}
    console.print("\n[bold]Scoring holdout — baseline bundles[/bold]")
    for v in BASELINE_BUNDLES:
        p = score_with_bundle(v, test)
        if p is None:
            continue
        bundle_preds[v] = p
    bundle_preds["pinnacle_close_devig"] = test["pin_over_devig"].values

    def metrics_subset(label_suffix: str, mask: np.ndarray) -> dict:
        out = {}
        if mask.sum() == 0:
            return out
        ys = y_test[mask]
        pi = pin_over_imp[mask]
        ui = pin_under_imp[mask]
        po = pin_over_odds_[mask]
        uo = pin_under_odds_[mask]
        for name, p in bundle_preds.items():
            out[name] = evaluate(name + label_suffix, p[mask], ys, pi, ui, po, uo)
        return out

    full_mask = np.ones(len(test), dtype=bool)
    metrics = metrics_subset("", full_mask)

    # Per-era split: CSV-era = pre-2026-04-30 (sparse MFV), AF-era = 2026-05+
    test = test.reset_index(drop=True)
    af_era_mask = (test["date"] >= pd.Timestamp("2026-04-01", tz="UTC")).values
    csv_era_mask = ~af_era_mask

    console.print(f"\n[bold]Per-era split:[/bold] CSV-era n={csv_era_mask.sum()}, AF-era n={af_era_mask.sum()}")

    csv_metrics = metrics_subset(" (CSV)", csv_era_mask)
    af_metrics = metrics_subset(" (AF)", af_era_mask)

    # ----- Results table
    def print_table(title: str, m_dict: dict) -> None:
        table = Table(title=title)
        table.add_column("Bundle")
        table.add_column("log_loss", justify="right")
        table.add_column("brier", justify="right")
        table.add_column("ECE", justify="right")
        table.add_column("ROI@+5%", justify="right")
        table.add_column("n@+5%", justify="right")
        table.add_column("ROI@+10%", justify="right")
        table.add_column("n@+10%", justify="right")
        for v, m in m_dict.items():
            table.add_row(v,
                          f"{m['log_loss']:.4f}",
                          f"{m['brier']:.4f}",
                          f"{m['ece']:.4f}",
                          f"{m['roi5_pct']:+.2f}%",
                          f"{m['roi5_n']}",
                          f"{m['roi10_pct']:+.2f}%",
                          f"{m['roi10_n']}")
        console.print(table)

    print_table(f"OU 2.5 — full holdout n={len(test)}", metrics)
    print_table(f"OU 2.5 — CSV-era (pre-2026-04) n={csv_era_mask.sum()}", csv_metrics)
    print_table(f"OU 2.5 — AF-era (2026-04+) n={af_era_mask.sum()}", af_metrics)

    # ----- Ship gate
    base = metrics.get(SHIP_GATE_BASELINE)
    cand = metrics[BUNDLE_VERSION]
    ship = False
    rationale = []
    if base is None:
        rationale.append(f"Baseline {SHIP_GATE_BASELINE} unavailable — cannot apply ship gate; ABORT.")
    else:
        ll_delta = (base["log_loss"] - cand["log_loss"]) / base["log_loss"]
        roi_delta = cand["roi5_pct"] - base["roi5_pct"]
        rationale.append(f"log_loss vs {SHIP_GATE_BASELINE}: cand={cand['log_loss']:.4f} base={base['log_loss']:.4f} → {ll_delta:+.2%}")
        rationale.append(f"ROI@+5% vs {SHIP_GATE_BASELINE}: cand={cand['roi5_pct']:+.2f}% base={base['roi5_pct']:+.2f}% → {roi_delta:+.2f}pp")
        rationale.append(f"Ship gate: ≥5% log_loss OR ≥2pp ROI@+5%")
        if ll_delta >= 0.05 or roi_delta >= 2.0:
            ship = True
            rationale.append("→ SHIP")
        else:
            rationale.append("→ SHELVE")

    console.print("\n[bold]Ship gate[/bold]")
    for line in rationale:
        console.print(f"  {line}")

    # ----- Persist bundle
    if (ship or args.save_anyway) and not args.no_save:
        save_bundle(wrapper, home_model, away_model, FEATURE_COLS)
    else:
        console.print("[yellow]Bundle not saved.[/yellow]")

    # ----- Results md
    write_results_md(Path(args.results_out), metrics, csv_metrics, af_metrics,
                     ship, rationale, len(train), len(test),
                     csv_era_mask.sum(), af_era_mask.sum())
    console.print(f"[green]Results written to {args.results_out}[/green]")


def write_results_md(path: Path, metrics: dict, csv_metrics: dict, af_metrics: dict,
                     ship: bool, rationale: list[str],
                     n_train: int, n_test: int, n_csv: int, n_af: int) -> None:
    def table_md(m_dict: dict) -> list[str]:
        out = ["| Bundle | log_loss | brier | ECE | ROI@+5% | n@+5% | ROI@+10% | n@+10% |",
               "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for v, m in m_dict.items():
            out.append(f"| `{v}` | {m['log_loss']:.4f} | {m['brier']:.4f} | {m['ece']:.4f} | "
                       f"{m['roi5_pct']:+.2f}% | {m['roi5_n']} | "
                       f"{m['roi10_pct']:+.2f}% | {m['roi10_n']} |")
        return out

    lines = ["# OU25-DEDICATED-MODEL — Backtest Results", "",
             f"Generated by `scripts/train_ou25_dedicated.py` on {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M UTC}.",
             "",
             f"- Training universe: {n_train} matches < {TRAIN_END}",
             f"- Holdout: {n_test} matches in [{TRAIN_END}, {HOLDOUT_END})",
             f"- Features: {', '.join(FEATURE_COLS)}",
             "",
             "## Full holdout", ""]
    lines += table_md(metrics)
    lines += ["", f"## CSV-era subset (pre-2026-04, sparse MFV) — n={n_csv}", ""]
    lines += table_md(csv_metrics)
    lines += ["", f"## AF-era subset (2026-04+, rich MFV) — n={n_af}", ""]
    lines += table_md(af_metrics)
    lines += ["", "## Ship gate", ""]
    for r in rationale:
        lines.append(f"- {r}")
    lines += ["", f"**Verdict: {'SHIP' if ship else 'SHELVE'}**", ""]
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
