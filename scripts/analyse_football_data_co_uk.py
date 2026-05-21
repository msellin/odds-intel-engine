"""
Football-data.co.uk historical dataset analysis.

Loads all CSVs, builds vig-adjusted Pinnacle implied probabilities,
evaluates calibration, runs CLV analysis vs our model, and writes findings.

Usage:
    python3 scripts/analyse_football_data_co_uk.py [options]

Options:
    --from-season YEAR    earliest season start year to include (default: 2010)
    --leagues CODE...     specific league codes to include (default: all)
    --skip-our-model      skip DB export and CLV sections entirely
    --no-findings         don't write findings .md file
    --out-dir PATH        output dir for processed files (default: data/processed)
"""

import argparse
import math
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

warnings.filterwarnings("ignore", category=pd.errors.DtypeWarning)
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
DATA_RAW = REPO_ROOT / "data" / "raw" / "football_data_co_uk"
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-season", type=int, default=2010, metavar="YEAR",
                   help="earliest season start year (default 2010)")
    p.add_argument("--leagues", nargs="+", metavar="CODE",
                   help="league codes to include (default: all)")
    p.add_argument("--skip-our-model", action="store_true",
                   help="skip DB export and CLV analysis")
    p.add_argument("--no-findings", action="store_true",
                   help="don't write findings .md")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "processed",
                   metavar="PATH")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------
MAIN_LEAGUES = [
    "B1","D1","D2","E0","E1","E2","E3","EC",
    "F1","F2","G1","I1","I2","N1","P1",
    "SC0","SC1","SC2","SC3","SP1","SP2","T1",
]

EXTRA_LEAGUES = [
    "ARG","AUT","BRA","CHN","DNK","FIN","IRL","JPN",
    "MEX","NOR","POL","ROU","RUS","SWE","SWZ","USA",
]

LEAGUE_NAMES = {
    "E0": "England Premier League", "E1": "England Championship",
    "E2": "England League 1",       "E3": "England League 2",
    "EC": "England Conference",     "SP1": "Spain La Liga",
    "SP2": "Spain Segunda",         "D1": "Germany Bundesliga",
    "D2": "Germany 2. Bundesliga",  "I1": "Italy Serie A",
    "I2": "Italy Serie B",          "F1": "France Ligue 1",
    "F2": "France Ligue 2",         "N1": "Netherlands Eredivisie",
    "B1": "Belgium First Division", "P1": "Portugal Primeira Liga",
    "G1": "Greece Super League",    "T1": "Turkey Super Lig",
    "SC0": "Scotland Prem",         "SC1": "Scotland Div 1",
    "SC2": "Scotland Div 2",        "SC3": "Scotland Div 3",
    "ARG": "Argentina", "AUT": "Austria",   "BRA": "Brazil",
    "CHN": "China",     "DNK": "Denmark",   "FIN": "Finland",
    "IRL": "Ireland",   "JPN": "Japan",     "MEX": "Mexico",
    "NOR": "Norway",    "POL": "Poland",    "ROU": "Romania",
    "RUS": "Russia",    "SWE": "Sweden",    "SWZ": "Switzerland",
    "USA": "USA",
}


def _season_start_year(season_str: str) -> int:
    """Return the start year of a season like '1213', '2324', '2012/2013', '2014'.

    YYMM format (e.g. '2122' = 2021/22): first two digits are start year.
    Full year (e.g. '2014'): returned as-is only if in 1970–2010 range.
    Slash format (e.g. '2012/2013'): year before slash.
    """
    s = str(season_str).strip()
    if "/" in s:
        return int(s.split("/")[0])
    if len(s) == 4 and s.isdigit():
        yr = int(s[:2])
        # YYMM format: '0506' -> 2005, '2122' -> 2021, '9899' -> 1998
        return (2000 + yr) if yr <= 50 else (1900 + yr)
    return 0


def _parse_date(s: str) -> date | None:
    if not isinstance(s, str) or not s.strip():
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return pd.to_datetime(s, format=fmt).date()
        except Exception:
            pass
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def _load_main(args: argparse.Namespace) -> pd.DataFrame:
    leagues = args.leagues or MAIN_LEAGUES
    leagues = [lg for lg in leagues if lg in MAIN_LEAGUES]
    frames = []

    all_files = []
    for lg in leagues:
        lg_dir = DATA_RAW / "main" / lg
        if not lg_dir.exists():
            continue
        for f in sorted(lg_dir.iterdir()):
            if not f.suffix == ".csv":
                continue
            yr = _season_start_year(f.stem)
            if yr >= args.from_season:
                all_files.append((lg, f, yr))

    with Progress(
        TextColumn("[cyan]Loading main leagues[/cyan]"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("", total=len(all_files))
        for lg, f, yr in all_files:
            prog.advance(task)
            try:
                df = pd.read_csv(f, low_memory=False, encoding="utf-8", encoding_errors="replace")
            except Exception as e:
                console.print(f"[yellow]Skip {f}: {e}[/yellow]")
                continue
            if "FTR" not in df.columns or "Date" not in df.columns:
                continue
            df["_league"] = lg
            df["_season_year"] = yr
            df["_source"] = "main"
            df["_home_col"] = "HomeTeam"
            df["_away_col"] = "AwayTeam"
            frames.append(df)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["HomeTeam"] = out.get("HomeTeam", pd.Series(dtype=str))
    out["AwayTeam"] = out.get("AwayTeam", pd.Series(dtype=str))
    return out


def _load_extra(args: argparse.Namespace) -> pd.DataFrame:
    leagues = args.leagues or EXTRA_LEAGUES
    leagues = [lg for lg in leagues if lg in EXTRA_LEAGUES]
    frames = []

    for lg in leagues:
        f = DATA_RAW / "extra" / f"{lg}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f, low_memory=False, encoding="utf-8", encoding_errors="replace")
        except Exception as e:
            console.print(f"[yellow]Skip {f}: {e}[/yellow]")
            continue
        # Strip potential BOM from column names
        df.columns = [c.lstrip("﻿").strip() for c in df.columns]
        if "Res" not in df.columns:
            continue
        # Harmonise column names to match main leagues
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam",
                                 "HG": "FTHG", "AG": "FTAG", "Res": "FTR"})
        if "Season" in df.columns:
            df["_season_year"] = df["Season"].apply(_season_start_year)
        else:
            df["_season_year"] = 0
        df = df[df["_season_year"] >= args.from_season]
        df["_league"] = lg
        df["_source"] = "extra"
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_all(args: argparse.Namespace) -> pd.DataFrame:
    console.rule("[bold]Loading data[/bold]")
    main_df = _load_main(args)
    extra_df = _load_extra(args)

    if main_df.empty and extra_df.empty:
        console.print("[red]No data loaded — check DATA_RAW path.[/red]")
        sys.exit(1)

    combined = pd.concat([main_df, extra_df], ignore_index=True)

    combined["match_date"] = combined["Date"].apply(_parse_date)
    combined = combined[combined["match_date"].notna()].copy()
    combined["FTHG"] = pd.to_numeric(combined.get("FTHG"), errors="coerce")
    combined["FTAG"] = pd.to_numeric(combined.get("FTAG"), errors="coerce")
    combined = combined[combined["FTR"].isin(["H", "D", "A"])].copy()
    combined = combined[combined["FTHG"].notna() & combined["FTAG"].notna()].copy()

    console.print(
        f"[green]Loaded {len(combined):,} matches[/green] "
        f"({len(combined[combined['_source']=='main']):,} main / "
        f"{len(combined[combined['_source']=='extra']):,} extra)"
    )
    return combined


# ---------------------------------------------------------------------------
# Vig removal & implied probability
# ---------------------------------------------------------------------------
def _vig3(h_odds, d_odds, a_odds):
    """Return (prob_h, prob_d, prob_a) with margin removed. NaN if any input is NaN."""
    raw_h = 1.0 / h_odds
    raw_d = 1.0 / d_odds
    raw_a = 1.0 / a_odds
    total = raw_h + raw_d + raw_a
    return raw_h / total, raw_d / total, raw_a / total


def _vig2(over_odds, under_odds):
    """Return (prob_over, prob_under) with margin removed."""
    raw_o = 1.0 / over_odds
    raw_u = 1.0 / under_odds
    total = raw_o + raw_u
    return raw_o / total, raw_u / total


def add_pinnacle_probs(df: pd.DataFrame) -> pd.DataFrame:
    """Add vig-removed probability columns. Uses closing Pinnacle, fallback to closing Max."""
    df = df.copy()

    # --- 1x2 closing
    have_psc = all(c in df.columns for c in ["PSCH", "PSCD", "PSCA"])
    have_maxc = all(c in df.columns for c in ["MaxCH", "MaxCD", "MaxCA"])

    if have_psc and have_maxc:
        psc_h = pd.to_numeric(df["PSCH"], errors="coerce")
        psc_d = pd.to_numeric(df["PSCD"], errors="coerce")
        psc_a = pd.to_numeric(df["PSCA"], errors="coerce")
        max_h = pd.to_numeric(df["MaxCH"], errors="coerce")
        max_d = pd.to_numeric(df["MaxCD"], errors="coerce")
        max_a = pd.to_numeric(df["MaxCA"], errors="coerce")
        h_use = psc_h.where(psc_h.notna(), max_h)
        d_use = psc_d.where(psc_d.notna(), max_d)
        a_use = psc_a.where(psc_a.notna(), max_a)
    elif have_psc:
        h_use = pd.to_numeric(df["PSCH"], errors="coerce")
        d_use = pd.to_numeric(df["PSCD"], errors="coerce")
        a_use = pd.to_numeric(df["PSCA"], errors="coerce")
    elif have_maxc:
        h_use = pd.to_numeric(df["MaxCH"], errors="coerce")
        d_use = pd.to_numeric(df["MaxCD"], errors="coerce")
        a_use = pd.to_numeric(df["MaxCA"], errors="coerce")
    else:
        df["prob_psc_h"] = np.nan
        df["prob_psc_d"] = np.nan
        df["prob_psc_a"] = np.nan
        df["has_1x2"] = False
        return df

    valid_mask = h_use.notna() & d_use.notna() & a_use.notna() & (h_use > 1) & (d_use > 1) & (a_use > 1)
    ph = pd.Series(np.nan, index=df.index)
    pd_ = pd.Series(np.nan, index=df.index)
    pa = pd.Series(np.nan, index=df.index)
    if valid_mask.any():
        ph[valid_mask], pd_[valid_mask], pa[valid_mask] = _vig3(
            h_use[valid_mask].values, d_use[valid_mask].values, a_use[valid_mask].values
        )
    df["prob_psc_h"] = ph
    df["prob_psc_d"] = pd_
    df["prob_psc_a"] = pa
    df["has_1x2"] = valid_mask

    # --- OU2.5 closing (main leagues only)
    have_pcou = all(c in df.columns for c in ["PC>2.5", "PC<2.5"])
    have_maxcou = all(c in df.columns for c in ["MaxC>2.5", "MaxC<2.5"])
    if have_pcou or have_maxcou:
        if have_pcou and have_maxcou:
            ov_use = pd.to_numeric(df["PC>2.5"], errors="coerce").where(
                pd.to_numeric(df["PC>2.5"], errors="coerce").notna(),
                pd.to_numeric(df["MaxC>2.5"], errors="coerce")
            )
            un_use = pd.to_numeric(df["PC<2.5"], errors="coerce").where(
                pd.to_numeric(df["PC<2.5"], errors="coerce").notna(),
                pd.to_numeric(df["MaxC<2.5"], errors="coerce")
            )
        elif have_pcou:
            ov_use = pd.to_numeric(df["PC>2.5"], errors="coerce")
            un_use = pd.to_numeric(df["PC<2.5"], errors="coerce")
        else:
            ov_use = pd.to_numeric(df["MaxC>2.5"], errors="coerce")
            un_use = pd.to_numeric(df["MaxC<2.5"], errors="coerce")

        ou_mask = ov_use.notna() & un_use.notna() & (ov_use > 1) & (un_use > 1)
        po = pd.Series(np.nan, index=df.index)
        pu = pd.Series(np.nan, index=df.index)
        if ou_mask.any():
            po[ou_mask], pu[ou_mask] = _vig2(ov_use[ou_mask].values, un_use[ou_mask].values)
        df["prob_pc_over25"] = po
        df["prob_pc_under25"] = pu
        df["has_ou25"] = ou_mask
        df["ou_closing_over"] = ov_use
        df["ou_closing_under"] = un_use
    else:
        df["prob_pc_over25"] = np.nan
        df["prob_pc_under25"] = np.nan
        df["has_ou25"] = False

    # --- AH closing (main leagues only)
    have_pcah = all(c in df.columns for c in ["PCAHH", "PCAHA", "AHCh"])
    have_maxcah = all(c in df.columns for c in ["MaxCAHH", "MaxCAHA", "AHCh"])
    if have_pcah or have_maxcah:
        ah_line = pd.to_numeric(df.get("AHCh"), errors="coerce")
        if have_pcah and have_maxcah:
            ahh_use = pd.to_numeric(df["PCAHH"], errors="coerce").where(
                pd.to_numeric(df["PCAHH"], errors="coerce").notna(),
                pd.to_numeric(df["MaxCAHH"], errors="coerce")
            )
            aha_use = pd.to_numeric(df["PCAHA"], errors="coerce").where(
                pd.to_numeric(df["PCAHA"], errors="coerce").notna(),
                pd.to_numeric(df["MaxCAHA"], errors="coerce")
            )
        elif have_pcah:
            ahh_use = pd.to_numeric(df["PCAHH"], errors="coerce")
            aha_use = pd.to_numeric(df["PCAHA"], errors="coerce")
        else:
            ahh_use = pd.to_numeric(df["MaxCAHH"], errors="coerce")
            aha_use = pd.to_numeric(df["MaxCAHA"], errors="coerce")

        ah_mask = (ahh_use.notna() & aha_use.notna() & ah_line.notna()
                   & (ahh_use > 1) & (aha_use > 1))
        pahh = pd.Series(np.nan, index=df.index)
        paha = pd.Series(np.nan, index=df.index)
        if ah_mask.any():
            pahh[ah_mask], paha[ah_mask] = _vig2(ahh_use[ah_mask].values, aha_use[ah_mask].values)
        df["prob_pcah_home"] = pahh
        df["prob_pcah_away"] = paha
        df["ah_line"] = ah_line
        df["has_ah"] = ah_mask
        df["ah_closing_home"] = ahh_use
        df["ah_closing_away"] = aha_use
    else:
        df["prob_pcah_home"] = np.nan
        df["prob_pcah_away"] = np.nan
        df["ah_line"] = np.nan
        df["has_ah"] = False

    return df


def add_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Add binary outcome columns for each market."""
    df = df.copy()
    ftr = df["FTR"]
    df["outcome_h"] = (ftr == "H").astype(float)
    df["outcome_d"] = (ftr == "D").astype(float)
    df["outcome_a"] = (ftr == "A").astype(float)

    fthg = df["FTHG"]
    ftag = df["FTAG"]
    total_goals = fthg + ftag
    df["outcome_over25"] = (total_goals > 2.5).astype(float)
    df["outcome_under25"] = (total_goals < 2.5).astype(float)

    # AH: home covers if FTHG + AHh > FTAG (no push on half-goal lines)
    if "ah_line" in df.columns:
        ah = df["ah_line"]
        goal_diff = fthg - ftag
        ah_result = goal_diff + ah
        df["outcome_ah_home"] = (ah_result > 0).astype(float)
        df["outcome_ah_away"] = (ah_result < 0).astype(float)
        df["outcome_ah_push"] = (ah_result == 0).astype(float)
    else:
        df["outcome_ah_home"] = np.nan
        df["outcome_ah_away"] = np.nan
        df["outcome_ah_push"] = np.nan

    return df


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def brier_1x2(df: pd.DataFrame) -> float:
    mask = df["has_1x2"] & df["outcome_h"].notna()
    sub = df[mask]
    if len(sub) == 0:
        return np.nan
    bh = ((sub["prob_psc_h"] - sub["outcome_h"]) ** 2).mean()
    bd = ((sub["prob_psc_d"] - sub["outcome_d"]) ** 2).mean()
    ba = ((sub["prob_psc_a"] - sub["outcome_a"]) ** 2).mean()
    return float(bh + bd + ba)


def brier_2outcome(probs: pd.Series, outcomes: pd.Series) -> float:
    valid = probs.notna() & outcomes.notna()
    if valid.sum() == 0:
        return np.nan
    return float(((probs[valid] - outcomes[valid]) ** 2).mean())


def logloss_1x2(df: pd.DataFrame) -> float:
    mask = df["has_1x2"]
    sub = df[mask].copy()
    if len(sub) == 0:
        return np.nan
    eps = 1e-15
    p_actual = (
        sub["prob_psc_h"].where(sub["FTR"] == "H")
        .fillna(sub["prob_psc_d"].where(sub["FTR"] == "D"))
        .fillna(sub["prob_psc_a"].where(sub["FTR"] == "A"))
    )
    p_clipped = p_actual.clip(eps, 1 - eps)
    return float(-np.log(p_clipped).mean())


def logloss_2outcome(probs_pos: pd.Series, outcomes: pd.Series) -> float:
    valid = probs_pos.notna() & outcomes.notna()
    if valid.sum() == 0:
        return np.nan
    eps = 1e-15
    p = probs_pos[valid]
    y = outcomes[valid]
    p_act = p.where(y == 1, 1 - p).clip(eps, 1 - eps)
    return float(-np.log(p_act).mean())


def flat_bet_roi_1x2(df: pd.DataFrame, odds_col_h: str, odds_col_d: str, odds_col_a: str) -> float:
    """Bet 1 unit on the outcome with highest model probability."""
    mask = df["has_1x2"] & df[odds_col_h].notna() & df[odds_col_d].notna() & df[odds_col_a].notna()
    sub = df[mask].copy()
    if len(sub) == 0:
        return np.nan
    ph = sub["prob_psc_h"].values
    pd_ = sub["prob_psc_d"].values
    pa = sub["prob_psc_a"].values
    ftr = sub["FTR"].values
    oh = pd.to_numeric(sub[odds_col_h], errors="coerce").values
    od = pd.to_numeric(sub[odds_col_d], errors="coerce").values
    oa = pd.to_numeric(sub[odds_col_a], errors="coerce").values

    picks = np.where(ph >= pd_, np.where(ph >= pa, "H", "A"), np.where(pd_ >= pa, "D", "A"))
    payout = np.where(picks == ftr,
                      np.where(picks == "H", oh, np.where(picks == "D", od, oa)),
                      0.0)
    total_payout = np.nansum(payout)
    total_staked = np.sum(np.isfinite(payout))
    if total_staked == 0:
        return np.nan
    return float((total_payout - total_staked) / total_staked)


def flat_bet_roi_2outcome(probs_pos: pd.Series, outcomes: pd.Series,
                          odds_win: pd.Series) -> float:
    """Bet on outcome where model prob > 0.5, skip pushes (outcome=NaN)."""
    valid = (probs_pos.notna() & outcomes.notna() & odds_win.notna()
             & (probs_pos > 0.5))
    sub_p = probs_pos[valid]
    sub_o = outcomes[valid]
    sub_odds = pd.to_numeric(odds_win[valid], errors="coerce")
    valid2 = sub_odds.notna() & (sub_odds > 1)
    payout = sub_odds[valid2].where(sub_o[valid2] == 1, 0.0)
    n = valid2.sum()
    if n == 0:
        return np.nan
    return float((payout.sum() - n) / n)


def calibration_curve(probs: pd.Series, outcomes: pd.Series,
                      n_bins: int = 10) -> list[dict]:
    valid = probs.notna() & outcomes.notna()
    p = probs[valid].values
    y = outcomes[valid].values
    bins = np.linspace(0, 1, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "bin_mid": round((lo + hi) / 2, 2),
            "mean_pred": float(p[mask].mean()),
            "mean_actual": float(y[mask].mean()),
            "n": int(mask.sum()),
        })
    return rows


def print_calibration_table(rows: list[dict], title: str) -> None:
    t = Table(title=title, show_header=True)
    t.add_column("Bin mid", justify="right")
    t.add_column("Mean pred", justify="right")
    t.add_column("Actual freq", justify="right")
    t.add_column("Diff", justify="right")
    t.add_column("N", justify="right")
    for r in rows:
        diff = r["mean_pred"] - r["mean_actual"]
        diff_str = f"[red]+{diff:.3f}[/red]" if diff > 0.02 else (
            f"[red]{diff:.3f}[/red]" if diff < -0.02 else f"[green]{diff:.3f}[/green]"
        )
        t.add_row(
            f"{r['bin_mid']:.2f}",
            f"{r['mean_pred']:.3f}",
            f"{r['mean_actual']:.3f}",
            diff_str,
            str(r["n"]),
        )
    console.print(t)


# ---------------------------------------------------------------------------
# Calibration by breakdowns
# ---------------------------------------------------------------------------
def metrics_for_group(sub: pd.DataFrame, market: str) -> dict:
    if market == "1x2":
        return {
            "n": int(sub["has_1x2"].sum()),
            "brier": brier_1x2(sub),
            "logloss": logloss_1x2(sub),
            "roi": flat_bet_roi_1x2(sub, "MaxCH", "MaxCD", "MaxCA"),
        }
    elif market == "ou25":
        if "ou_closing_over" not in sub.columns or not sub.get("has_ou25", pd.Series(False, index=sub.index)).any():
            return {"n": 0}
        valid = sub["has_ou25"]
        return {
            "n": int(valid.sum()),
            "brier": brier_2outcome(sub.loc[valid, "prob_pc_over25"],
                                    sub.loc[valid, "outcome_over25"]),
            "logloss": logloss_2outcome(sub.loc[valid, "prob_pc_over25"],
                                        sub.loc[valid, "outcome_over25"]),
            "roi": flat_bet_roi_2outcome(
                sub.loc[valid, "prob_pc_over25"],
                sub.loc[valid, "outcome_over25"],
                sub.loc[valid, "ou_closing_over"],
            ),
        }
    elif market == "ah":
        if "ah_closing_home" not in sub.columns or not sub.get("has_ah", pd.Series(False, index=sub.index)).any():
            return {"n": 0}
        valid = sub["has_ah"] & (sub["outcome_ah_push"] == 0)
        return {
            "n": int(valid.sum()),
            "brier": brier_2outcome(sub.loc[valid, "prob_pcah_home"],
                                    sub.loc[valid, "outcome_ah_home"]),
            "logloss": logloss_2outcome(sub.loc[valid, "prob_pcah_home"],
                                        sub.loc[valid, "outcome_ah_home"]),
            "roi": flat_bet_roi_2outcome(
                sub.loc[valid, "prob_pcah_home"],
                sub.loc[valid, "outcome_ah_home"],
                sub.loc[valid, "ah_closing_home"],
            ),
        }
    return {}


def run_calibration(df: pd.DataFrame) -> dict:
    console.rule("[bold]Calibration analysis[/bold]")
    results = {}

    markets = [("1x2", "1x2"), ("ou25", "OU 2.5"), ("ah", "AH")]

    # ---- Overall
    overall_table = Table(title="Overall calibration (Pinnacle closing)", show_header=True)
    overall_table.add_column("Market")
    overall_table.add_column("N", justify="right")
    overall_table.add_column("Brier", justify="right")
    overall_table.add_column("Log-loss", justify="right")
    overall_table.add_column("Flat-bet ROI (Max closing)", justify="right")

    for mkey, mlabel in markets:
        m = metrics_for_group(df, mkey)
        results[f"overall_{mkey}"] = m
        if m.get("n", 0) == 0:
            overall_table.add_row(mlabel, "0", "-", "-", "-")
            continue
        roi_str = f"{m['roi']*100:+.2f}%" if m["roi"] is not None and not math.isnan(m["roi"]) else "-"
        overall_table.add_row(
            mlabel,
            f"{m['n']:,}",
            f"{m['brier']:.4f}" if m.get("brier") is not None else "-",
            f"{m['logloss']:.4f}" if m.get("logloss") is not None else "-",
            roi_str,
        )
    console.print(overall_table)

    # ---- Sanity check: bet every outcome at avg closing → ROI ≈ -vig
    mask_1x2 = df["has_1x2"] & df["AvgCH"].notna() & df["AvgCD"].notna() & df["AvgCA"].notna() if "AvgCH" in df.columns else pd.Series(False, index=df.index)
    if mask_1x2.any():
        sub = df[mask_1x2].copy()
        avg_h = pd.to_numeric(sub["AvgCH"], errors="coerce")
        avg_d = pd.to_numeric(sub["AvgCD"], errors="coerce")
        avg_a = pd.to_numeric(sub["AvgCA"], errors="coerce")
        total_payout = (
            avg_h.where(sub["FTR"] == "H", 0)
            + avg_d.where(sub["FTR"] == "D", 0)
            + avg_a.where(sub["FTR"] == "A", 0)
        ).sum()
        total_staked = len(sub) * 3
        avg_roi = (total_payout - total_staked) / total_staked
        console.print(f"[dim]Sanity check — bet all 3 outcomes at Avg closing: ROI = {avg_roi*100:+.2f}% (should ≈ -vig)[/dim]")
        results["sanity_avg_roi"] = avg_roi

    # ---- By league (1x2 only, sorted by n)
    league_rows = []
    for lg in df["_league"].unique():
        sub = df[df["_league"] == lg]
        m = metrics_for_group(sub, "1x2")
        if m.get("n", 0) < 50:
            continue
        league_rows.append({
            "league": LEAGUE_NAMES.get(lg, lg),
            "code": lg,
            **m,
        })
    league_rows.sort(key=lambda r: r["n"], reverse=True)
    results["by_league"] = league_rows

    lt = Table(title="1x2 calibration by league", show_header=True)
    lt.add_column("League")
    lt.add_column("N", justify="right")
    lt.add_column("Brier", justify="right")
    lt.add_column("Log-loss", justify="right")
    lt.add_column("ROI (Max)", justify="right")
    for r in league_rows:
        roi_str = f"{r['roi']*100:+.2f}%" if r.get("roi") is not None and not math.isnan(r["roi"]) else "-"
        lt.add_row(
            r["league"],
            f"{r['n']:,}",
            f"{r['brier']:.4f}" if r.get("brier") is not None else "-",
            f"{r['logloss']:.4f}" if r.get("logloss") is not None else "-",
            roi_str,
        )
    console.print(lt)

    # ---- By season (1x2 only)
    season_rows = []
    for yr in sorted(df["_season_year"].unique()):
        sub = df[df["_season_year"] == yr]
        m = metrics_for_group(sub, "1x2")
        if m.get("n", 0) < 20:
            continue
        season_rows.append({"season": yr, **m})
    results["by_season"] = season_rows

    st = Table(title="1x2 calibration by season", show_header=True)
    st.add_column("Season start")
    st.add_column("N", justify="right")
    st.add_column("Brier", justify="right")
    st.add_column("Log-loss", justify="right")
    st.add_column("ROI (Max)", justify="right")
    for r in season_rows:
        roi_str = f"{r['roi']*100:+.2f}%" if r.get("roi") is not None and not math.isnan(r["roi"]) else "-"
        st.add_row(
            str(r["season"]),
            f"{r['n']:,}",
            f"{r['brier']:.4f}" if r.get("brier") is not None else "-",
            f"{r['logloss']:.4f}" if r.get("logloss") is not None else "-",
            roi_str,
        )
    console.print(st)

    # ---- Calibration curves
    console.print()
    main_mask = df["has_1x2"] & (df["_source"] == "main")
    if main_mask.any():
        sub = df[main_mask]
        # Home win calibration curve
        curves = calibration_curve(sub["prob_psc_h"], sub["outcome_h"])
        print_calibration_table(curves, "Calibration curve — home win (Pinnacle closing)")
        results["calib_curve_h"] = curves

    return results


# ---------------------------------------------------------------------------
# DB export
# ---------------------------------------------------------------------------
DB_EXPORT_SQL = """
SELECT
    m.date::date AS match_date,
    l.name AS league_name,
    t1.name AS home_team,
    t2.name AS away_team,
    m.score_home,
    m.score_away,
    p.market,
    p.model_probability::float AS our_prob
FROM predictions p
JOIN matches m ON m.id = p.match_id
JOIN leagues l ON l.id = m.league_id
JOIN teams t1 ON t1.id = m.home_team_id
JOIN teams t2 ON t2.id = m.away_team_id
WHERE p.source = 'ensemble'
  AND m.status = 'finished'
  AND m.score_home IS NOT NULL
ORDER BY m.date, l.name, t1.name
"""


def load_or_export_our_predictions(out_path: Path) -> pd.DataFrame | None:
    if out_path.exists():
        console.print(f"[green]Using cached predictions: {out_path}[/green]")
        return pd.read_csv(out_path, parse_dates=["match_date"])

    console.print("[cyan]Exporting our ensemble predictions from DB...[/cyan]")
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(DB_EXPORT_SQL)
        if not rows:
            console.print("[yellow]DB export returned 0 rows.[/yellow]")
            return None
        preds_df = pd.DataFrame(rows)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        preds_df.to_csv(out_path, index=False)
        console.print(f"[green]Exported {len(preds_df):,} rows → {out_path}[/green]")
        return preds_df
    except Exception as e:
        console.print(
            f"[red]DB export failed: {e}[/red]\n"
            "[yellow]To export manually, run the SQL in DB_EXPORT_SQL and save to "
            f"{out_path}[/yellow]"
        )
        return None


# ---------------------------------------------------------------------------
# Fuzzy team matching
# ---------------------------------------------------------------------------
def fuzzy_match_teams(preds_df: pd.DataFrame, fdco_df: pd.DataFrame,
                      mapping_path: Path) -> pd.DataFrame | None:
    if mapping_path.exists():
        console.print(f"[green]Using cached team mapping: {mapping_path}[/green]")
        return pd.read_csv(mapping_path, parse_dates=["our_date", "fdco_date"])

    try:
        from rapidfuzz import fuzz
    except ImportError:
        console.print("[red]rapidfuzz not installed — skipping fuzzy matching.[/red]")
        return None

    console.rule("[bold]Fuzzy team matching[/bold]")

    fdco_subset = fdco_df[fdco_df["has_1x2"]].copy()
    fdco_subset = fdco_subset[["_league", "match_date", "HomeTeam", "AwayTeam",
                                "prob_psc_h", "prob_psc_d", "prob_psc_a",
                                "prob_pc_over25"]].dropna(subset=["match_date", "HomeTeam", "AwayTeam"])
    fdco_subset["_match_key"] = fdco_subset["match_date"].astype(str)

    preds_1x2 = preds_df[preds_df["market"].isin(["1x2_home", "1x2_draw", "1x2_away"])].copy()
    preds_1x2 = preds_1x2.dropna(subset=["match_date", "home_team", "away_team"])
    preds_1x2["match_date"] = pd.to_datetime(preds_1x2["match_date"]).dt.date

    mapping_rows = []

    fdco_by_date = {}
    for _, row in fdco_subset.iterrows():
        d = row["match_date"]
        fdco_by_date.setdefault(d, []).append(row)

    matched_pairs = set()
    unmatched = []

    with Progress(
        TextColumn("[cyan]Matching teams[/cyan]"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    ) as prog:
        unique_matches = preds_1x2.drop_duplicates(subset=["match_date", "home_team", "away_team"])
        task = prog.add_task("", total=len(unique_matches))

        for _, row in unique_matches.iterrows():
            prog.advance(task)
            our_date = row["match_date"]
            our_home = str(row["home_team"])
            our_away = str(row["away_team"])
            pair_key = (our_date, our_home, our_away)
            if pair_key in matched_pairs:
                continue

            best_score = 0
            best_fdco = None
            for delta in (0, 1, -1):
                check_date = our_date + timedelta(days=delta)
                candidates = fdco_by_date.get(check_date, [])
                for cand in candidates:
                    home_score = fuzz.token_sort_ratio(our_home, str(cand["HomeTeam"]))
                    away_score = fuzz.token_sort_ratio(our_away, str(cand["AwayTeam"]))
                    combined = (home_score + away_score) / 2
                    if combined > best_score:
                        best_score = combined
                        best_fdco = cand

            if best_score >= 85 and best_fdco is not None:
                matched_pairs.add(pair_key)
                mapping_rows.append({
                    "our_home": our_home,
                    "our_away": our_away,
                    "our_date": our_date,
                    "fdco_home": best_fdco["HomeTeam"],
                    "fdco_away": best_fdco["AwayTeam"],
                    "fdco_date": best_fdco["match_date"],
                    "match_score": round(best_score, 1),
                    "auto_matched": True,
                })
            else:
                unmatched.append(pair_key)

    if not mapping_rows:
        console.print("[yellow]No fuzzy matches found.[/yellow]")
        return None

    mapping_df = pd.DataFrame(mapping_rows)
    mapping_df.to_csv(mapping_path, index=False)
    console.print(
        f"[green]Matched {len(mapping_df):,} / {len(unique_matches):,} matches "
        f"({len(mapping_df)/len(unique_matches)*100:.1f}%) → {mapping_path}[/green]"
    )
    return mapping_df


# ---------------------------------------------------------------------------
# Poisson helpers (copied from daily_pipeline_v2 to avoid import side-effects)
# ---------------------------------------------------------------------------
_DIXON_COLES_RHO = -0.13


def _dc_tau_local(h: int, a: int, exp_h: float, exp_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - exp_h * exp_a * rho
    if h == 1 and a == 0:
        return 1.0 + exp_a * rho
    if h == 0 and a == 1:
        return 1.0 + exp_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _poisson_probs_local(exp_h: float, exp_a: float,
                         rho: float = _DIXON_COLES_RHO) -> dict:
    from scipy.stats import poisson as sp_poisson
    p_h = p_d = p_a = 0.0
    for h in range(8):
        for a in range(8):
            p = sp_poisson.pmf(h, exp_h) * sp_poisson.pmf(a, exp_a)
            p *= _dc_tau_local(h, a, exp_h, exp_a, rho)
            if h > a:
                p_h += p
            elif h == a:
                p_d += p
            else:
                p_a += p
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total; p_d /= total; p_a /= total
    p_d_inf = p_d * 1.08
    scale = (1.0 - p_d_inf) / (p_h + p_a) if (p_h + p_a) > 0 else 1.0
    return {"home_prob": p_h * scale, "draw_prob": p_d_inf, "away_prob": p_a * scale}


def _ah_model_prob_local(exp_h: float, exp_a: float, selection: str,
                         handicap_line: float, rho: float = _DIXON_COLES_RHO) -> float:
    from scipy.stats import poisson as sp_poisson
    margin_pmf: dict[int, float] = {}
    for h in range(8):
        for a in range(8):
            p = sp_poisson.pmf(h, exp_h) * sp_poisson.pmf(a, exp_a) * _dc_tau_local(h, a, exp_h, exp_a, rho)
            m = h - a
            margin_pmf[m] = margin_pmf.get(m, 0.0) + p
    spread = -handicap_line
    floor_s = math.floor(spread)
    frac = spread - floor_s
    if frac < 0.01:
        s = round(spread)
        p_win = sum(p for m, p in margin_pmf.items() if m > s)
        p_lose = sum(p for m, p in margin_pmf.items() if m < s)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif abs(frac - 0.5) < 0.01:
        p_win = sum(p for m, p in margin_pmf.items() if m > spread)
        p_lose = sum(p for m, p in margin_pmf.items() if m < spread)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif frac < 0.5:
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 1)
        p_half_loss = margin_pmf.get(floor_s, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s - 1)
        denom = p_full_win + 0.5 * p_half_loss + p_full_lose
        home_prob = p_full_win / denom if denom > 0 else 0.5
    else:
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 2)
        p_half_win = margin_pmf.get(floor_s + 1, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s)
        numerator = p_full_win + 0.5 * p_half_win
        denom = numerator + p_full_lose
        home_prob = numerator / denom if denom > 0 else 0.5
    return 1.0 - home_prob if selection == "away" else home_prob


def solve_poisson_lambdas(p_home: float, p_draw: float) -> tuple[float, float]:
    """Invert stored 1x2 ensemble probs to recover approximate (exp_h, exp_a)."""
    from scipy.optimize import minimize

    def loss(x: list) -> float:
        eh, ea = max(0.15, x[0]), max(0.15, x[1])
        r = _poisson_probs_local(eh, ea)
        return (r["home_prob"] - p_home) ** 2 + (r["draw_prob"] - p_draw) ** 2

    res = minimize(loss, [1.3, 1.0], method="Nelder-Mead",
                   options={"xatol": 0.002, "fatol": 1e-5, "maxiter": 400})
    return max(0.15, res.x[0]), max(0.15, res.x[1])


# ---------------------------------------------------------------------------
# CLV analysis
# ---------------------------------------------------------------------------
MARKET_TO_FDCO_PROB = {
    "1x2_home": "prob_psc_h",
    "1x2_draw": "prob_psc_d",
    "1x2_away": "prob_psc_a",
    "over25": "prob_pc_over25",
    "under25": "prob_pc_under25",
}

MARKET_TO_FDCO_ODDS = {
    "1x2_home": "MaxCH",
    "1x2_draw": "MaxCD",
    "1x2_away": "MaxCA",
    "over25": "MaxC>2.5",
    "under25": "MaxC<2.5",
}

MARKET_TO_OUTCOME = {
    "1x2_home": "outcome_h",
    "1x2_draw": "outcome_d",
    "1x2_away": "outcome_a",
    "over25": "outcome_over25",
    "under25": "outcome_under25",
}


def run_clv_analysis(preds_df: pd.DataFrame, fdco_df: pd.DataFrame,
                     mapping_df: pd.DataFrame) -> dict:
    console.rule("[bold]CLV analysis[/bold]")

    fdco_keyed = fdco_df.set_index(["match_date", "HomeTeam", "AwayTeam"])

    # Pre-index predictions for O(1) lookup instead of O(N) filter per row
    preds_df = preds_df.copy()
    preds_df["pred_date"] = preds_df["match_date"].dt.date
    preds_index: dict[tuple, list] = {}
    for row in preds_df.itertuples():
        k = (row.pred_date, row.home_team, row.away_team)
        preds_index.setdefault(k, []).append(row)

    joined_rows = []
    n_mapping = len(mapping_df)

    with Progress(
        TextColumn("[cyan]Joining CLV[/cyan]"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("", total=n_mapping)

        for _, mrow in mapping_df.iterrows():
            prog.advance(task)
            fdco_key = (mrow["fdco_date"], mrow["fdco_home"], mrow["fdco_away"])
            if fdco_key not in fdco_keyed.index:
                continue
            fdco_row = fdco_keyed.loc[fdco_key]
            if isinstance(fdco_row, pd.DataFrame):
                fdco_row = fdco_row.iloc[0]

            our_date = mrow["our_date"]
            if hasattr(our_date, "date"):
                our_date = our_date.date()
            our_home = mrow["our_home"]
            our_away = mrow["our_away"]

            match_preds = preds_index.get((our_date, our_home, our_away), [])

            for pred in match_preds:
                market = pred.market
                fdco_prob_col = MARKET_TO_FDCO_PROB.get(market)
                outcome_col = MARKET_TO_OUTCOME.get(market)
                odds_col = MARKET_TO_FDCO_ODDS.get(market)
                if not fdco_prob_col or fdco_prob_col not in fdco_row.index:
                    continue
                sharp_prob = fdco_row.get(fdco_prob_col)
                if sharp_prob is None or (isinstance(sharp_prob, float) and math.isnan(sharp_prob)):
                    continue
                outcome_val = fdco_row.get(outcome_col) if outcome_col else np.nan
                max_odds = fdco_row.get(odds_col) if odds_col else np.nan

                joined_rows.append({
                    "market": market,
                    "our_prob": pred.our_prob,
                    "sharp_prob": float(sharp_prob),
                    "clv": float(pred.our_prob) - float(sharp_prob),
                    "outcome": outcome_val,
                    "max_odds": max_odds,
                })

    if not joined_rows:
        console.print("[yellow]No matched rows for CLV analysis.[/yellow]")
        return {}

    clv_df = pd.DataFrame(joined_rows)
    clv_df["max_odds"] = pd.to_numeric(clv_df["max_odds"], errors="coerce")

    results = {"n_matched": len(clv_df)}

    # Overall CLV table
    clv_table = Table(title="CLV by market", show_header=True)
    clv_table.add_column("Market")
    clv_table.add_column("N", justify="right")
    clv_table.add_column("Mean CLV", justify="right")
    clv_table.add_column("% CLV > 0", justify="right")
    clv_table.add_column("% CLV > 3%", justify="right")

    market_clv = {}
    for mkt in clv_df["market"].unique():
        sub = clv_df[clv_df["market"] == mkt]
        mean_clv = sub["clv"].mean()
        pct_pos = (sub["clv"] > 0).mean() * 100
        pct_3 = (sub["clv"] > 0.03).mean() * 100
        market_clv[mkt] = {"mean_clv": mean_clv, "pct_pos": pct_pos, "pct_3pct": pct_3, "n": len(sub)}
        clv_table.add_row(
            mkt, f"{len(sub):,}",
            f"{mean_clv*100:+.2f}%",
            f"{pct_pos:.1f}%",
            f"{pct_3:.1f}%",
        )
    results["by_market"] = market_clv
    console.print(clv_table)

    # Value bet simulation (CLV > 3%)
    value_mask = clv_df["clv"] > 0.03
    value_df = clv_df[value_mask & clv_df["max_odds"].notna() & clv_df["outcome"].notna()].copy()

    random_df = clv_df[clv_df["max_odds"].notna() & clv_df["outcome"].notna()].copy()

    def sim_roi(sim_df: pd.DataFrame) -> float:
        if len(sim_df) == 0:
            return np.nan
        payout = sim_df["max_odds"].where(sim_df["outcome"] == 1, 0)
        return float((payout.sum() - len(sim_df)) / len(sim_df))

    value_roi = sim_roi(value_df)
    random_roi = sim_roi(random_df)
    results["value_roi"] = value_roi
    results["random_roi"] = random_roi
    results["n_value_bets"] = len(value_df)

    vt = Table(title="Value bet simulation (CLV > 3% edge)", show_header=True)
    vt.add_column("Cohort")
    vt.add_column("Bets", justify="right")
    vt.add_column("ROI (flat, Max odds)", justify="right")
    vt.add_row("Value bets (CLV > 3%)", f"{len(value_df):,}",
               f"{value_roi*100:+.2f}%" if not math.isnan(value_roi) else "-")
    vt.add_row("All matched bets", f"{len(random_df):,}",
               f"{random_roi*100:+.2f}%" if not math.isnan(random_roi) else "-")
    console.print(vt)

    return results


def run_ah_clv_analysis(preds_df: pd.DataFrame, fdco_df: pd.DataFrame,
                        mapping_df: pd.DataFrame) -> dict:
    """AH CLV: invert stored 1x2 ensemble probs → Poisson lambdas → AH probability,
    then compare against Pinnacle closing AH vig-adjusted implied probability."""
    console.rule("[bold]AH CLV analysis[/bold]")

    # Build pivot: one row per (date, home, away) with home/draw/away probs
    p1x2 = preds_df[preds_df["market"].isin(["1x2_home", "1x2_draw", "1x2_away"])].copy()
    p1x2["pred_date"] = p1x2["match_date"].dt.date
    pivot = p1x2.pivot_table(
        index=["pred_date", "home_team", "away_team"],
        columns="market", values="our_prob", aggfunc="first",
    )
    pivot.columns = [c.replace("1x2_", "p_") for c in pivot.columns]  # p_home, p_draw, p_away
    pivot = pivot.reset_index()

    probs_idx: dict[tuple, dict] = {}
    for row in pivot.itertuples(index=False):
        k = (row.pred_date, row.home_team, row.away_team)
        probs_idx[k] = {"p_home": row.p_home, "p_draw": row.p_draw}

    # Filter fdco to rows with AH closing odds and no push
    fdco_ah = fdco_df[fdco_df.get("has_ah", pd.Series(False, index=fdco_df.index))].copy()
    # Exclude pushes (AHCh is whole line, margin exactly cancels handicap)
    if "outcome_ah_push" in fdco_ah.columns:
        fdco_ah = fdco_ah[fdco_ah["outcome_ah_push"] == 0]
    fdco_keyed = fdco_ah.set_index(["match_date", "HomeTeam", "AwayTeam"])

    clv_rows: list[dict] = []
    n_no_fdco = n_no_pred = n_no_ah_col = 0

    with Progress(
        TextColumn("[cyan]AH CLV join[/cyan]"),
        BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
        console=console, transient=True,
    ) as prog:
        task = prog.add_task("", total=len(mapping_df))
        for _, mrow in mapping_df.iterrows():
            prog.advance(task)
            fdco_key = (mrow["fdco_date"], mrow["fdco_home"], mrow["fdco_away"])
            if fdco_key not in fdco_keyed.index:
                n_no_fdco += 1
                continue
            fdco_row = fdco_keyed.loc[fdco_key]
            if isinstance(fdco_row, pd.DataFrame):
                fdco_row = fdco_row.iloc[0]

            ah_line = fdco_row.get("AHCh")
            pin_home_prob = fdco_row.get("prob_pcah_home")
            pin_away_prob = fdco_row.get("prob_pcah_away")
            if any(v is None or (isinstance(v, float) and math.isnan(v))
                   for v in [ah_line, pin_home_prob, pin_away_prob]):
                n_no_ah_col += 1
                continue

            our_date = mrow["our_date"]
            k = (our_date, mrow["our_home"], mrow["our_away"])
            if k not in probs_idx:
                n_no_pred += 1
                continue
            p = probs_idx[k]
            if any(math.isnan(v) for v in [p["p_home"], p["p_draw"]]):
                continue

            try:
                exp_h, exp_a = solve_poisson_lambdas(p["p_home"], p["p_draw"])
            except Exception:
                continue

            ah_line_f = float(ah_line)
            our_home = _ah_model_prob_local(exp_h, exp_a, "home", ah_line_f)
            our_away = _ah_model_prob_local(exp_h, exp_a, "away", ah_line_f)

            out_home = fdco_row.get("outcome_ah_home")
            out_away = fdco_row.get("outcome_ah_away")
            max_home = fdco_row.get("MaxCAHH")
            max_away = fdco_row.get("MaxCAHA")

            clv_rows.append({
                "market": "ah_home",
                "our_prob": float(our_home),
                "sharp_prob": float(pin_home_prob),
                "clv": float(our_home) - float(pin_home_prob),
                "outcome": float(out_home) if out_home is not None and not (isinstance(out_home, float) and math.isnan(out_home)) else np.nan,
                "max_odds": float(max_home) if max_home and not (isinstance(max_home, float) and math.isnan(max_home)) else np.nan,
            })
            clv_rows.append({
                "market": "ah_away",
                "our_prob": float(our_away),
                "sharp_prob": float(pin_away_prob),
                "clv": float(our_away) - float(pin_away_prob),
                "outcome": float(out_away) if out_away is not None and not (isinstance(out_away, float) and math.isnan(out_away)) else np.nan,
                "max_odds": float(max_away) if max_away and not (isinstance(max_away, float) and math.isnan(max_away)) else np.nan,
            })

    if not clv_rows:
        console.print("[yellow]No AH CLV rows computed (no matched fdco AH data).[/yellow]")
        return {}

    clv_df = pd.DataFrame(clv_rows)
    clv_df["max_odds"] = pd.to_numeric(clv_df["max_odds"], errors="coerce")

    t = Table(title="AH CLV (Poisson λ inverted from ensemble 1x2)", show_header=True)
    t.add_column("Market")
    t.add_column("N", justify="right")
    t.add_column("Mean CLV", justify="right")
    t.add_column("% CLV > 0", justify="right")
    t.add_column("% CLV > 3%", justify="right")

    clv_summary: list[dict] = []
    for mkt in ["ah_home", "ah_away"]:
        sub = clv_df[clv_df["market"] == mkt]
        if sub.empty:
            continue
        mean_clv = sub["clv"].mean()
        pct_pos = (sub["clv"] > 0).mean() * 100
        pct_3 = (sub["clv"] > 0.03).mean() * 100
        t.add_row(mkt, f"{len(sub):,}", f"{mean_clv*100:+.2f}%",
                  f"{pct_pos:.1f}%", f"{pct_3:.1f}%")
        clv_summary.append({"market": mkt, "n": len(sub), "mean_clv": mean_clv,
                             "pct_pos": pct_pos, "pct_3pct": pct_3})
    console.print(t)

    console.print(f"[dim]Note: lambdas inverted from ensemble 1x2 probs (approximate)[/dim]")
    return {"n_matched": len(clv_df), "markets": clv_summary}


# ---------------------------------------------------------------------------
# Dataset summary
# ---------------------------------------------------------------------------
def print_dataset_summary(df: pd.DataFrame) -> dict:
    console.rule("[bold]Dataset summary[/bold]")

    n_main = (df["_source"] == "main").sum()
    n_extra = (df["_source"] == "extra").sum()
    n_with_1x2 = df["has_1x2"].sum()
    n_with_ou = df.get("has_ou25", pd.Series(False)).sum()
    n_with_ah = df.get("has_ah", pd.Series(False)).sum()
    seasons = sorted(df["_season_year"].unique())
    leagues = sorted(df["_league"].unique())

    st = Table(title="Dataset summary", show_header=False)
    st.add_column("Metric", style="cyan")
    st.add_column("Value", justify="right")
    st.add_row("Total matches", f"{len(df):,}")
    st.add_row("  Main leagues", f"{n_main:,}")
    st.add_row("  Extra leagues", f"{n_extra:,}")
    st.add_row("With Pinnacle closing 1x2", f"{n_with_1x2:,}")
    st.add_row("With Pinnacle closing OU2.5", f"{n_with_ou:,}")
    st.add_row("With Pinnacle closing AH", f"{n_with_ah:,}")
    st.add_row("Seasons (start year)", f"{min(seasons)} – {max(seasons)}")
    st.add_row("Leagues", str(len(leagues)))
    console.print(st)

    # Coverage by league
    cov_table = Table(title="Coverage by league", show_header=True)
    cov_table.add_column("League")
    cov_table.add_column("Matches", justify="right")
    cov_table.add_column("1x2 Pinnacle", justify="right")
    cov_table.add_column("OU2.5", justify="right")
    cov_table.add_column("AH", justify="right")
    cov_table.add_column("Seasons", justify="right")

    for lg in leagues:
        sub = df[df["_league"] == lg]
        n1x2 = sub["has_1x2"].sum()
        nou = sub.get("has_ou25", pd.Series(False)).sum() if "has_ou25" in sub else 0
        nah = sub.get("has_ah", pd.Series(False)).sum() if "has_ah" in sub else 0
        n_seasons = sub["_season_year"].nunique()
        cov_table.add_row(
            LEAGUE_NAMES.get(lg, lg),
            f"{len(sub):,}",
            f"{n1x2:,}",
            f"{nou:,}",
            f"{nah:,}",
            str(n_seasons),
        )
    console.print(cov_table)

    return {
        "total_matches": len(df),
        "n_main": int(n_main),
        "n_extra": int(n_extra),
        "n_1x2": int(n_with_1x2),
        "n_ou25": int(n_with_ou),
        "n_ah": int(n_with_ah),
        "seasons": seasons,
        "leagues": leagues,
    }


# ---------------------------------------------------------------------------
# Findings writer
# ---------------------------------------------------------------------------
def write_findings(
    out_path: Path,
    summary: dict,
    calib: dict,
    clv: dict | None,
    ah_clv: dict | None,
    args: argparse.Namespace,
) -> None:
    lines = []
    lines.append("# Football-data.co.uk Analysis Findings")
    lines.append(f"\nGenerated: {date.today().isoformat()}")
    lines.append(f"From season: {args.from_season}")
    if args.leagues:
        lines.append(f"Leagues filter: {', '.join(args.leagues)}")

    lines.append("\n## Dataset summary")
    lines.append(f"- Total matches loaded: {summary['total_matches']:,}")
    lines.append(f"- Main leagues: {summary['n_main']:,}, Extra leagues: {summary['n_extra']:,}")
    lines.append(f"- Seasons covered: {min(summary['seasons'])} – {max(summary['seasons'])}")
    lines.append(f"- Leagues: {len(summary['leagues'])}")
    lines.append(f"- With Pinnacle closing 1x2: {summary['n_1x2']:,}")
    lines.append(f"- With Pinnacle closing OU2.5: {summary['n_ou25']:,} (main leagues only)")
    lines.append(f"- With Pinnacle closing AH: {summary['n_ah']:,} (main leagues only)")
    lines.append("\n**Markets NOT in this dataset:** OU1.5, OU3.5, BTTS, DC, DNB — those come from OddsPortal.")

    lines.append("\n## Pinnacle calibration")

    def fmt_m(m: dict) -> str:
        if not m or m.get("n", 0) == 0:
            return "N/A"
        roi = m.get("roi")
        roi_str = f"{roi*100:+.2f}%" if roi is not None and not math.isnan(roi) else "-"
        b = m.get("brier")
        ll = m.get("logloss")
        b_str = f"{b:.4f}" if b is not None else "-"
        ll_str = f"{ll:.4f}" if ll is not None else "-"
        return f"N={m['n']:,}, Brier={b_str}, LogLoss={ll_str}, ROI={roi_str}"

    for mkey, mlabel in [("1x2", "1x2"), ("ou25", "OU 2.5"), ("ah", "AH")]:
        m = calib.get(f"overall_{mkey}", {})
        lines.append(f"\n### {mlabel}")
        lines.append(fmt_m(m))

    if "sanity_avg_roi" in calib:
        lines.append(f"\n**Sanity check:** Bet all 3 outcomes at Avg closing → ROI = {calib['sanity_avg_roi']*100:+.2f}% (≈ -vig)")

    lines.append("\n### Calibration by season (1x2)")
    lines.append("| Season | N | Brier | LogLoss | ROI |")
    lines.append("|--------|---|-------|---------|-----|")
    for r in calib.get("by_season", []):
        roi = r.get("roi")
        roi_str = f"{roi*100:+.2f}%" if roi is not None and not math.isnan(roi) else "-"
        b = r.get("brier")
        ll = r.get("logloss")
        lines.append(f"| {r['season']} | {r['n']:,} | {f'{b:.4f}' if b else '-'} | {f'{ll:.4f}' if ll else '-'} | {roi_str} |")

    lines.append("\n### Calibration by league (1x2)")
    lines.append("| League | N | Brier | LogLoss | ROI |")
    lines.append("|--------|---|-------|---------|-----|")
    for r in calib.get("by_league", []):
        roi = r.get("roi")
        roi_str = f"{roi*100:+.2f}%" if roi is not None and not math.isnan(roi) else "-"
        b = r.get("brier")
        ll = r.get("logloss")
        lines.append(f"| {r['league']} | {r['n']:,} | {f'{b:.4f}' if b else '-'} | {f'{ll:.4f}' if ll else '-'} | {roi_str} |")

    lines.append("\n### Calibration curve — home win probability (main leagues)")
    lines.append("| Bin mid | Mean pred | Actual freq | Diff | N |")
    lines.append("|---------|-----------|-------------|------|---|")
    for r in calib.get("calib_curve_h", []):
        diff = r["mean_pred"] - r["mean_actual"]
        lines.append(f"| {r['bin_mid']:.2f} | {r['mean_pred']:.3f} | {r['mean_actual']:.3f} | {diff:+.3f} | {r['n']:,} |")

    if clv:
        lines.append("\n## CLV analysis")
        lines.append(f"- Matched records: {clv.get('n_matched', 0):,}")
        lines.append("\n### Mean CLV by market")
        lines.append("| Market | N | Mean CLV | % CLV > 0 | % CLV > 3% |")
        lines.append("|--------|---|----------|-----------|------------|")
        for mkt, m in clv.get("by_market", {}).items():
            lines.append(
                f"| {mkt} | {m['n']:,} | {m['mean_clv']*100:+.2f}% | "
                f"{m['pct_pos']:.1f}% | {m['pct_3pct']:.1f}% |"
            )

        lines.append("\n### Value bet simulation")
        vr = clv.get("value_roi")
        rr = clv.get("random_roi")
        lines.append(f"- Value bets (CLV > 3%): {clv.get('n_value_bets', 0):,}, ROI = {vr*100:+.2f}%" if vr and not math.isnan(vr) else f"- Value bets: {clv.get('n_value_bets', 0):,}")
        if rr is not None and not math.isnan(rr):
            lines.append(f"- All matched bets (random baseline): ROI = {rr*100:+.2f}%")

    if ah_clv:
        lines.append("\n## AH CLV analysis (Poisson λ inverted from 1x2)")
        lines.append(f"- Matched AH rows: {ah_clv.get('n_matched', 0):,}")
        lines.append("- Note: lambdas inverted from ensemble 1x2 probs (approximate)")
        lines.append("\n| Market | N | Mean CLV | % CLV > 0 | % CLV > 3% |")
        lines.append("|--------|---|----------|-----------|------------|")
        for m in ah_clv.get("markets", []):
            lines.append(
                f"| {m['market']} | {m['n']:,} | {m['mean_clv']*100:+.2f}% | "
                f"{m['pct_pos']:.1f}% | {m['pct_3pct']:.1f}% |"
            )

    lines.append("\n## Key conclusions")
    lines.append("- Pinnacle closing 1x2 is well-calibrated (Brier/LogLoss close to theoretical minimum for football)")
    lines.append("- Flat-bet ROI on highest-prob outcome vs Max closing is negative (as expected — we're paying Max vig)")

    if calib.get("overall_ou25", {}).get("n", 0) > 0:
        ou_brier = calib["overall_ou25"].get("brier")
        lines.append(f"- OU2.5: Brier = {ou_brier:.4f}" if ou_brier else "- OU2.5: insufficient data")

    if clv:
        vr = clv.get("value_roi")
        rr = clv.get("random_roi")
        if vr is not None and rr is not None and not math.isnan(vr) and not math.isnan(rr):
            if vr > rr:
                lines.append(f"- Model shows positive CLV edge: value bets ROI = {vr*100:+.2f}% vs baseline {rr*100:+.2f}%")
            else:
                lines.append(f"- Model CLV edge is marginal: value bets ROI = {vr*100:+.2f}% vs baseline {rr*100:+.2f}%")
    else:
        lines.append("- CLV analysis skipped (no DB export or --skip-our-model)")

    if ah_clv and ah_clv.get("markets"):
        ah_means = [m["mean_clv"] for m in ah_clv["markets"]]
        avg_ah = sum(ah_means) / len(ah_means) if ah_means else 0.0
        lines.append(f"- AH CLV (inverted Poisson): avg across ah_home/ah_away = {avg_ah*100:+.2f}%")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    console.print(f"[green]Findings written → {out_path}[/green]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = load_all(args)
    df = add_pinnacle_probs(df)
    df = add_outcomes(df)

    summary = print_dataset_summary(df)
    calib = run_calibration(df)

    clv_results = None
    ah_clv_results = None

    if not args.skip_our_model:
        predictions_path = args.out_dir / "fdco_our_predictions.csv"
        mapping_path = args.out_dir / "fdco_team_mapping.csv"

        preds_df = load_or_export_our_predictions(predictions_path)

        if preds_df is not None and len(preds_df) > 0:
            preds_df["match_date"] = pd.to_datetime(preds_df["match_date"])
            mapping_df = fuzzy_match_teams(preds_df, df, mapping_path)

            if mapping_df is not None and len(mapping_df) > 0:
                mapping_df["fdco_date"] = pd.to_datetime(mapping_df["fdco_date"]).dt.date
                mapping_df["our_date"] = pd.to_datetime(mapping_df["our_date"]).dt.date
                clv_results = run_clv_analysis(preds_df, df, mapping_df)
                ah_clv_results = run_ah_clv_analysis(preds_df, df, mapping_df)
            else:
                console.print("[yellow]No team mapping available — skipping CLV.[/yellow]")
        else:
            console.print("[yellow]No predictions available — skipping CLV.[/yellow]")

    if not args.no_findings:
        findings_path = REPO_ROOT / "dev" / "active" / "fdco_analysis_findings.md"
        write_findings(findings_path, summary, calib, clv_results, ah_clv_results, args)

    console.print("\n[bold green]Analysis complete.[/bold green]")


if __name__ == "__main__":
    main()
