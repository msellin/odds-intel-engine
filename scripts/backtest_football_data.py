"""
scripts/backtest_football_data.py

Download football-data.co.uk historical CSVs and build a large backtest
dataset for discover_strategies.py.

Strategy simulated: Buchdahl "Wisdom of Crowds" — bet when Bet365 odds
exceed Pinnacle fair odds (margin-stripped) by >= min_edge percent.
This is the closest historical proxy for what our pipeline does.

Pinnacle fair odds = strip Pinnacle's overround → gives the "true"
probability estimate. CLV = B365_odds / Pinnacle_closing - 1.

Available markets from football-data.co.uk:
  - 1x2     (PSH/PSD/PSA → PSCH/PSCD/PSCA closing, B365H/D/A)
  - ou25    (P>2.5/P<2.5 → PC>2.5/PC<2.5 closing, B365>2.5/B365<2.5)
  NOTE: no BTTS, no OU15, no OU35 — those aren't on football-data

Output: dev/active/backtest-football-data.csv
  Format is discover_strategies.py-compatible (same columns as
  backtest-3year.csv) plus a `clv` column for CLV analysis.

Usage:
    python3 scripts/backtest_football_data.py
    python3 scripts/backtest_football_data.py --min-edge 2  # stricter filter
    python3 scripts/backtest_football_data.py --no-cache    # re-download all
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from rich.console import Console
from rich.table import Table

ROOT  = Path(__file__).parent.parent
CACHE = ROOT / "dev/active/fd_cache"
OUT   = ROOT / "dev/active/backtest-football-data.csv"

console = Console()

# ── League catalogue ─────────────────────────────────────────────────────────
# (code: (league_name, country, tier))
LEAGUES: dict[str, tuple[str, str, int]] = {
    "E0":  ("Premier League",     "England",     1),
    "E1":  ("Championship",       "England",     2),
    "E2":  ("League One",         "England",     3),
    "D1":  ("Bundesliga",         "Germany",     1),
    "D2":  ("2. Bundesliga",      "Germany",     2),
    "SP1": ("La Liga",            "Spain",       1),
    "SP2": ("La Liga 2",          "Spain",       2),
    "I1":  ("Serie A",            "Italy",       1),
    "I2":  ("Serie B",            "Italy",       2),
    "F1":  ("Ligue 1",            "France",      1),
    "F2":  ("Ligue 2",            "France",      2),
    "N1":  ("Eredivisie",         "Netherlands", 1),
    "P1":  ("Primeira Liga",      "Portugal",    1),
    "B1":  ("First Division A",   "Belgium",     1),
    "T1":  ("Super Lig",          "Turkey",      1),
    "G1":  ("Super League",       "Greece",      1),
    "SC0": ("Scottish Prem",      "Scotland",    1),
}

SEASONS = ["1819", "1920", "2021", "2122", "2223", "2324", "2425"]

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"

# ── Buchdahl margin stripping ─────────────────────────────────────────────────

def fair_probs_3way(h: float, d: float, a: float) -> tuple[float, float, float]:
    total = 1/h + 1/d + 1/a
    return 1/h/total, 1/d/total, 1/a/total

def fair_probs_2way(over: float, under: float) -> tuple[float, float]:
    total = 1/over + 1/under
    return 1/over/total, 1/under/total

# ── Download / cache ──────────────────────────────────────────────────────────

def _cache_path(season: str, league: str) -> Path:
    return CACHE / season / f"{league}.csv"


def fetch_csv(season: str, league: str, no_cache: bool = False) -> pd.DataFrame | None:
    path = _cache_path(season, league)
    if path.exists() and not no_cache:
        try:
            return pd.read_csv(path, encoding="latin-1")
        except Exception:
            pass

    url = BASE_URL.format(season=season, league=league)
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        time.sleep(0.15)  # polite
        return pd.read_csv(path, encoding="latin-1")
    except Exception as e:
        console.print(f"  [yellow]  {league}/{season}: {e}[/yellow]")
        return None

# ── Parse one CSV ─────────────────────────────────────────────────────────────

def _match_id(league: str, season: str, home: str, away: str) -> str:
    raw = f"{league}|{season}|{home}|{away}"
    return hashlib.md5(raw.encode()).hexdigest()


def _to_float(val) -> float | None:
    try:
        f = float(val)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def parse_csv(df_raw: pd.DataFrame, league: str, season: str,
              league_name: str, country: str, tier: int,
              min_edge: float) -> list[dict]:
    rows = []
    year_start = 2000 + int(season[:2])

    for _, r in df_raw.iterrows():
        # Skip rows with missing date or result
        raw_date = r.get("Date")
        result   = str(r.get("FTR", "")).strip().upper()
        if not raw_date or result not in ("H", "D", "A"):
            continue

        try:
            date = pd.to_datetime(raw_date, dayfirst=True)
        except Exception:
            continue

        home = str(r.get("HomeTeam", "")).strip()
        away = str(r.get("AwayTeam", "")).strip()
        if not home or not away:
            continue

        try:
            score_home = int(r.get("FTHG", 0) or 0)
            score_away = int(r.get("FTAG", 0) or 0)
        except (ValueError, TypeError):
            score_home = score_away = 0

        mid = _match_id(league, season, home, away)
        season_str = f"{year_start}/{year_start+1}"

        # ── 1X2 ─────────────────────────────────────────────────────────────
        b365h = _to_float(r.get("B365H"))
        b365d = _to_float(r.get("B365D"))
        b365a = _to_float(r.get("B365A"))
        psh   = _to_float(r.get("PSH"))
        psd   = _to_float(r.get("PSD"))
        psa   = _to_float(r.get("PSA"))
        psch  = _to_float(r.get("PSCH"))  # Pinnacle closing home
        pscd  = _to_float(r.get("PSCD"))  # Pinnacle closing draw
        psca  = _to_float(r.get("PSCA"))  # Pinnacle closing away

        if all(x is not None for x in [b365h, b365d, b365a, psh, psd, psa]):
            fp_h, fp_d, fp_a = fair_probs_3way(psh, psd, psa)
            for sel, b365_odds, fair_p, cl_odds, won_cond in [
                ("home", b365h, fp_h, psch, result == "H"),
                ("draw", b365d, fp_d, pscd, result == "D"),
                ("away", b365a, fp_a, psca, result == "A"),
            ]:
                edge = fair_p * b365_odds - 1
                if edge < min_edge / 100:
                    continue
                clv = (b365_odds / cl_odds - 1) if cl_odds else None
                won = bool(won_cond)
                pnl = (b365_odds - 1) * 10.0 if won else -10.0
                rows.append({
                    "bot":         f"fd_1x2_{sel[:3]}",
                    "match_id":    mid,
                    "date":        date,
                    "league":      league_name,
                    "country":     country,
                    "tier":        tier,
                    "season":      season_str,
                    "market":      "1x2",
                    "selection":   sel,
                    "odds":        b365_odds,
                    "model_prob":  round(fair_p, 6),
                    "implied_prob": round(1/b365_odds, 6),
                    "edge":        round(edge, 6),
                    "stake":       10.0,
                    "won":         won,
                    "pnl":         round(pnl, 4),
                    "score_home":  score_home,
                    "score_away":  score_away,
                    "clv":         round(clv, 6) if clv is not None else None,
                })

        # ── Over/Under 2.5 ───────────────────────────────────────────────────
        b365_ov = _to_float(r.get("B365>2.5"))
        b365_un = _to_float(r.get("B365<2.5"))
        p_ov    = _to_float(r.get("P>2.5"))
        p_un    = _to_float(r.get("P<2.5"))
        pc_ov   = _to_float(r.get("PC>2.5"))   # Pinnacle closing over
        pc_un   = _to_float(r.get("PC<2.5"))   # Pinnacle closing under

        if all(x is not None for x in [b365_ov, b365_un, p_ov, p_un]):
            total_goals = score_home + score_away
            fp_ov, fp_un = fair_probs_2way(p_ov, p_un)
            for sel, b365_odds, fair_p, cl_odds, won_cond in [
                ("over",  b365_ov, fp_ov, pc_ov, total_goals > 2),
                ("under", b365_un, fp_un, pc_un, total_goals < 3),
            ]:
                edge = fair_p * b365_odds - 1
                if edge < min_edge / 100:
                    continue
                clv = (b365_odds / cl_odds - 1) if cl_odds else None
                won = bool(won_cond)
                pnl = (b365_odds - 1) * 10.0 if won else -10.0
                rows.append({
                    "bot":         f"fd_ou25_{sel[:3]}",
                    "match_id":    mid,
                    "date":        date,
                    "league":      league_name,
                    "country":     country,
                    "tier":        tier,
                    "season":      season_str,
                    "market":      "over_under_25",
                    "selection":   sel,
                    "odds":        b365_odds,
                    "model_prob":  round(fair_p, 6),
                    "implied_prob": round(1/b365_odds, 6),
                    "edge":        round(edge, 6),
                    "stake":       10.0,
                    "won":         won,
                    "pnl":         round(pnl, 4),
                    "score_home":  score_home,
                    "score_away":  score_away,
                    "clv":         round(clv, 6) if clv is not None else None,
                })

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Build football-data.co.uk backtest CSV")
    ap.add_argument("--min-edge", type=float, default=0.0,
                    help="Minimum edge vs Pinnacle fair to include a bet (%%)")
    ap.add_argument("--no-cache", action="store_true",
                    help="Re-download all CSVs even if cached")
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES.keys()),
                    help="League codes to include (default: all)")
    ap.add_argument("--seasons", nargs="+", default=SEASONS,
                    help="Season codes to include (default: all)")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    total_files = 0
    skipped = 0

    console.rule("[bold]football-data.co.uk backtest builder[/bold]")
    console.print(f"  Leagues: {', '.join(args.leagues)}")
    console.print(f"  Seasons: {', '.join(args.seasons)}")
    console.print(f"  Min edge: {args.min_edge:.1f}%")

    for season in args.seasons:
        for code in args.leagues:
            if code not in LEAGUES:
                console.print(f"  [yellow]Unknown league code: {code}[/yellow]")
                continue
            league_name, country, tier = LEAGUES[code]
            df_raw = fetch_csv(season, code, no_cache=args.no_cache)
            if df_raw is None or df_raw.empty:
                skipped += 1
                continue

            # Drop empty trailing rows (football-data often has them)
            df_raw = df_raw.dropna(subset=["HomeTeam", "AwayTeam"]).copy()
            df_raw = df_raw[df_raw["HomeTeam"].astype(str).str.strip() != ""]

            rows = parse_csv(df_raw, code, season, league_name, country, tier,
                             args.min_edge)
            all_rows.extend(rows)
            total_files += 1
            console.print(
                f"  [dim]{league_name} {season}:[/dim] "
                f"{len(df_raw)} matches → {len(rows)} value bets"
            )

    if not all_rows:
        console.print("[red]No rows produced. Check connectivity or --min-edge.[/red]")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Summary
    console.rule("[bold]Summary[/bold]")
    n_matches   = df["match_id"].nunique()
    n_with_clv  = df["clv"].notna().sum()
    avg_clv     = df["clv"].mean() * 100 if n_with_clv > 0 else None

    console.print(f"  Files processed : {total_files}  (skipped: {skipped})")
    console.print(f"  Total rows      : {len(df):,}")
    console.print(f"  Unique matches  : {n_matches:,}")
    console.print(f"  CLV data        : {n_with_clv:,} rows  "
                  f"(avg {avg_clv:+.2f}%)" if avg_clv is not None else "  CLV data: 0")

    t = Table(title="Rows by market")
    t.add_column("Market"); t.add_column("N", justify="right")
    t.add_column("Won%", justify="right"); t.add_column("ROI%", justify="right")
    t.add_column("Avg CLV%", justify="right")
    for mkt, g in df.groupby("market"):
        hit  = g["won"].mean() * 100
        roi  = g["pnl"].sum() / (g["stake"].sum()) * 100
        cval = g["clv"].mean() * 100 if g["clv"].notna().sum() > 100 else None
        cstr = f"{cval:+.2f}%" if cval is not None else "—"
        roi_str = f"[green]{roi:+.1f}%[/green]" if roi > 0 else f"[red]{roi:+.1f}%[/red]"
        t.add_row(mkt, f"{len(g):,}", f"{hit:.1f}%", roi_str, cstr)
    console.print(t)

    df.to_csv(OUT, index=False)
    console.print(f"\n[green]Saved → {OUT}[/green]")
    console.print("  Run: python3 scripts/discover_strategies.py --fd")

    console.rule("[bold]Done[/bold]")


if __name__ == "__main__":
    main()
