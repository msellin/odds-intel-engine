"""
OddsIntel — Mega Backtest: Beat the Bookie Dataset
===================================================
479K matches, 818 leagues, 2005-2015, avg 16 bookmakers including Pinnacle.

Strategy:
1. Load Beat the Bookie closing_odds.csv (479K matches with avg/max odds)
2. Merge with global ELO ratings via fuzzy team name matching
3. Run Poisson + form-based model for each league with 200+ matches
4. Output per-league ROI ranked table
5. Answer: which leagues show consistent edge?

Key insight from prior backtests:
- Tier 3-4 / lower leagues showed positive ROI (+4.8% in 2023-24)
- Top leagues are efficiently priced (Premier League, La Liga, etc.)
- 1X2 market outperforms O/U in lower leagues

This script tests whether that pattern holds across 818 global leagues.
"""

import json
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from scipy.stats import poisson
from rapidfuzz import fuzz, process

warnings.filterwarnings("ignore")

ENGINE_DIR = Path(__file__).parent.parent
RAW_DIR = ENGINE_DIR / "data" / "raw" / "beat_the_bookie"
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

STAKE = 10.0
MIN_MATCHES_PER_LEAGUE = 200
MIN_MATCHES_FOR_SEASONAL_ROI = 30  # Min bets per season to report ROI
MIN_BOOKMAKERS = 5  # Only use matches with 5+ bookmakers (more reliable odds)

# Known tier mappings for major leagues
LEAGUE_TIER_MAP = {
    # England
    "England: Premier League": 1,
    "England: Championship": 2,
    "England: League One": 3,
    "England: League Two": 4,
    "England: Blue Square Premier": 5,
    "England: Conference National": 5,
    "England: Southern Premier League": 6,
    "England: Northern Premier League": 6,
    "England: Ryman League": 6,
    "England: FA Cup": None,
    "England: FA Trophy": None,
    "England: League Cup": None,
    # Spain
    "Spain: Primera Division": 1,
    "Spain: Segunda Division": 2,
    "Spain: Segunda B": 3,
    "Spain: Tercera Division": 4,
    # Germany
    "Germany: Bundesliga": 1,
    "Germany: 2. Bundesliga": 2,
    "Germany: 3. Liga": 3,
    "Germany: Regionalliga North": 4,
    "Germany: Regionalliga South": 4,
    "Germany: Regionalliga West": 4,
    "Germany: Regionalliga Bayern": 4,
    # Italy
    "Italy: Serie A": 1,
    "Italy: Serie B": 2,
    "Italy: Lega Pro/Girone A": 3,
    "Italy: Lega Pro/Girone B": 3,
    "Italy: Lega Pro/Girone C": 3,
    # France
    "France: Ligue 1": 1,
    "France: Ligue 2": 2,
    "France: National": 3,
    # Netherlands
    "Netherlands: Eredivisie": 1,
    "Netherlands: Eerste Divisie": 2,
    # Portugal
    "Portugal: Primeira Liga": 1,
    "Portugal: Segunda Liga": 2,
    # Turkey
    "Turkey: Super Lig": 1,
    "Turkey: 1. Lig": 2,
    "Turkey: 2. Lig": 3,
    # Belgium
    "Belgium: Jupiler League": 1,
    "Belgium: First Amateur Division": 2,
    # Scotland
    "Scotland: Premiership": 1,
    "Scotland: Championship": 2,
    "Scotland: League One": 3,
    "Scotland: League Two": 4,
    # Russia
    "Russia: Premier League": 1,
    "Russia: National League": 2,
    # Greece
    "Greece: Super League": 1,
    "Greece: Football League": 2,
}


def country_from_league(league: str) -> str:
    """Extract country from BTB league name like 'England: Premier League'"""
    parts = league.split(":")
    return parts[0].strip().lower() if parts else "unknown"


def comp_from_league(league: str) -> str:
    """Extract competition from BTB league name"""
    parts = league.split(":", 1)
    return parts[1].strip() if len(parts) > 1 else league


def assign_tier(league: str) -> int:
    """Assign tier based on league name. Default tier=2 for unknowns (lower leagues)."""
    if league in LEAGUE_TIER_MAP:
        t = LEAGUE_TIER_MAP[league]
        return t if t is not None else 99  # Cup competitions
    # Heuristic: if name contains "Premier" or "Primera" or "Serie A", likely tier 1
    comp = comp_from_league(league).lower()
    if any(k in comp for k in ["premier league", "primera division", "serie a", "bundesliga",
                                "ligue 1", "eredivisie", "primera liga", "super lig"]):
        return 1
    if any(k in comp for k in ["second", "segunda", "serie b", "2. bundesliga", "ligue 2", "eerste"]):
        return 2
    if any(k in comp for k in ["third", "tercera", "national", "3. liga", "lega pro"]):
        return 3
    # Default to 3 for unknowns (tends to be lower leagues)
    return 3


def is_cup_competition(league: str) -> bool:
    """Detect cup competitions to exclude them"""
    comp = comp_from_league(league).lower()
    cup_keywords = ["cup", "trophy", "shield", "super copa", "copa del rey", "coppa",
                    "coupe", "pokal", "friendly", "friendly international", "supercup",
                    "playoff", "play-off", "play off", "relegation", "promotion",
                    "qualif", "test match", "club friendly"]
    return any(k in comp for k in cup_keywords)


def build_elo_lookup(df_elo: pd.DataFrame) -> dict:
    """
    Build a fast lookup structure: {date -> country -> [(home, away, home_elo, away_elo)]}
    Filtered to national competitions only.
    """
    print("Building ELO lookup structure...")
    df_elo = df_elo[df_elo["level"] == "national"].copy()
    df_elo["date_str"] = df_elo["date"].dt.strftime("%Y-%m-%d")

    lookup = {}
    for _, row in df_elo.iterrows():
        date_key = row["date_str"]
        country = row["home_country"]
        if date_key not in lookup:
            lookup[date_key] = {}
        if country not in lookup[date_key]:
            lookup[date_key][country] = []
        lookup[date_key][country].append({
            "home": row["home"],
            "away": row["away"],
            "home_elo": row["home_elo"],
            "away_elo": row["away_elo"],
        })

    print(f"ELO lookup: {len(lookup)} dates, {df_elo['home_country'].nunique()} countries")
    return lookup


def fuzzy_match_teams(btb_home: str, btb_away: str, candidates: list) -> tuple:
    """
    Find the best matching ELO entry for a BTB match using fuzzy name matching.
    Returns (home_elo, away_elo, match_score) or (None, None, 0) if no good match.
    """
    if not candidates:
        return None, None, 0

    best_score = 0
    best_home_elo = None
    best_away_elo = None

    for cand in candidates:
        # Score home team match
        h_score = max(
            fuzz.ratio(btb_home.lower(), cand["home"].lower()),
            fuzz.partial_ratio(btb_home.lower(), cand["home"].lower()),
            fuzz.token_sort_ratio(btb_home.lower(), cand["home"].lower())
        )
        # Score away team match
        a_score = max(
            fuzz.ratio(btb_away.lower(), cand["away"].lower()),
            fuzz.partial_ratio(btb_away.lower(), cand["away"].lower()),
            fuzz.token_sort_ratio(btb_away.lower(), cand["away"].lower())
        )

        combined = (h_score + a_score) / 2
        if combined > best_score:
            best_score = combined
            best_home_elo = cand["home_elo"]
            best_away_elo = cand["away_elo"]

    return best_home_elo, best_away_elo, best_score


# Country name mapping BTB -> ELO
BTB_COUNTRY_TO_ELO = {
    "england": "england",
    "scotland": "scotland",
    "germany": "germany",
    "italy": "italy",
    "spain": "spain",
    "france": "france",
    "netherlands": "netherlands",
    "portugal": "portugal",
    "turkey": "turkey",
    "belgium": "belgium",
    "russia": "russia",
    "greece": "greece",
    "norway": "norway",
    "sweden": "sweden",
    "denmark": "denmark",
    "finland": "finland",
    "austria": "austria",
    "switzerland": "switzerland",
    "poland": "poland",
    "czech republic": "czech republic",
    "romania": "romania",
    "bulgaria": "bulgaria",
    "hungary": "hungary",
    "croatia": "croatia",
    "serbia": "serbia",
    "ukraine": "ukraine",
    "argentina": "argentina",
    "brazil": "brazil",
    "chile": "chile",
    "colombia": "colombia",
    "mexico": "mexico",
    "usa": "usa",
    "japan": "japan",
    "australia": "australia",
    "south korea": "south korea",
    "china": "china",
}


def merge_elo_to_btb(df_btb: pd.DataFrame, elo_lookup: dict,
                      min_score: int = 65) -> pd.DataFrame:
    """
    Match BTB matches to ELO ratings using fuzzy team name matching.
    Only attempt matching for leagues where country appears in ELO data.
    """
    print(f"\nMerging ELO to BTB ({len(df_btb):,} matches)...")

    home_elos = np.full(len(df_btb), np.nan)
    away_elos = np.full(len(df_btb), np.nan)
    match_scores = np.zeros(len(df_btb))

    n_matched = 0
    n_attempted = 0

    # Process in chunks by date+country for speed
    df_btb = df_btb.reset_index(drop=True)
    df_btb["_country"] = df_btb["league"].apply(country_from_league)

    for (date_str, country), group in df_btb.groupby(["match_date", "_country"]):
        elo_country = BTB_COUNTRY_TO_ELO.get(country.lower())
        if elo_country is None:
            continue
        if date_str not in elo_lookup:
            continue
        if elo_country not in elo_lookup[date_str]:
            continue

        candidates = elo_lookup[date_str][elo_country]
        n_attempted += len(group)

        for idx, row in group.iterrows():
            h_elo, a_elo, score = fuzzy_match_teams(
                row["home_team"], row["away_team"], candidates
            )
            if score >= min_score:
                home_elos[idx] = h_elo
                away_elos[idx] = a_elo
                match_scores[idx] = score
                n_matched += 1

    df_btb["home_elo"] = home_elos
    df_btb["away_elo"] = away_elos
    df_btb["elo_match_score"] = match_scores
    df_btb["elo_diff"] = df_btb["home_elo"] - df_btb["away_elo"]

    pct_matched = n_matched / max(n_attempted, 1) * 100
    print(f"ELO merge: {n_attempted:,} attempted, {n_matched:,} matched ({pct_matched:.1f}%)")
    print(f"Matches with ELO: {df_btb['home_elo'].notna().sum():,} / {len(df_btb):,}")

    return df_btb


def compute_rolling_form(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """
    Compute rolling form features for each team.
    Returns df with added home/away form columns.
    Works on the BTB dataset format.
    """
    df = df.copy()
    df["ftr"] = np.where(df["home_score"] > df["away_score"], "H",
                np.where(df["home_score"] < df["away_score"], "A", "D"))
    df["total_goals"] = df["home_score"] + df["away_score"]
    df["over_25"] = (df["total_goals"] > 2.5).astype(int)

    # Create long format
    home_df = df[["match_date", "match_id", "home_team", "away_team",
                   "home_score", "away_score", "ftr", "total_goals", "over_25", "league"]].copy()
    home_df["team"] = home_df["home_team"]
    home_df["venue"] = "home"
    home_df["gs"] = home_df["home_score"]
    home_df["gc"] = home_df["away_score"]
    home_df["win"] = (home_df["ftr"] == "H").astype(float)
    home_df["pts"] = home_df["win"] * 3 + (home_df["ftr"] == "D").astype(float)
    home_df["cs"] = (home_df["away_score"] == 0).astype(float)

    away_df = df[["match_date", "match_id", "home_team", "away_team",
                   "home_score", "away_score", "ftr", "total_goals", "over_25", "league"]].copy()
    away_df["team"] = away_df["away_team"]
    away_df["venue"] = "away"
    away_df["gs"] = away_df["away_score"]
    away_df["gc"] = away_df["home_score"]
    away_df["win"] = (away_df["ftr"] == "A").astype(float)
    away_df["pts"] = away_df["win"] * 3 + (away_df["ftr"] == "D").astype(float)
    away_df["cs"] = (away_df["home_score"] == 0).astype(float)

    long = pd.concat([home_df, away_df], ignore_index=True)
    long = long.sort_values("match_date").reset_index(drop=True)

    # Rolling stats per team
    all_stats = []
    for team_name, g in long.groupby("team"):
        g = g.sort_values("match_date")
        s = pd.DataFrame(index=g.index)
        s["team"] = team_name
        s["match_date"] = g["match_date"]
        s["venue"] = g["venue"]
        s["win_pct"] = g["win"].shift(1).rolling(n, min_periods=3).mean()
        s["ppg"] = g["pts"].shift(1).rolling(n, min_periods=3).mean()
        s["gs_avg"] = g["gs"].shift(1).rolling(n, min_periods=3).mean()
        s["gc_avg"] = g["gc"].shift(1).rolling(n, min_periods=3).mean()
        s["cs_pct"] = g["cs"].shift(1).rolling(n, min_periods=3).mean()
        s["over25_pct"] = g["over_25"].shift(1).rolling(n, min_periods=3).mean()
        all_stats.append(s)

    rolling = pd.concat(all_stats, ignore_index=True)

    # Merge home stats
    home_r = rolling[rolling["venue"] == "home"].drop(columns=["venue"]).rename(
        columns={c: f"h_{c}" for c in ["win_pct", "ppg", "gs_avg", "gc_avg", "cs_pct", "over25_pct"]}
    )
    away_r = rolling[rolling["venue"] == "away"].drop(columns=["venue"]).rename(
        columns={c: f"a_{c}" for c in ["win_pct", "ppg", "gs_avg", "gc_avg", "cs_pct", "over25_pct"]}
    )

    df = df.merge(
        home_r[["team", "match_date", "h_win_pct", "h_ppg", "h_gs_avg", "h_gc_avg", "h_cs_pct", "h_over25_pct"]],
        left_on=["home_team", "match_date"], right_on=["team", "match_date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    df = df.merge(
        away_r[["team", "match_date", "a_win_pct", "a_ppg", "a_gs_avg", "a_gc_avg", "a_cs_pct", "a_over25_pct"]],
        left_on=["away_team", "match_date"], right_on=["team", "match_date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    df["form_diff"] = df["h_ppg"].fillna(1.3) - df["a_ppg"].fillna(1.3)
    df["gs_diff"] = df["h_gs_avg"].fillna(1.3) - df["a_gs_avg"].fillna(1.3)

    return df


def predict_poisson(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simple Poisson model for goal expectation.
    Uses attack/defense ratings computed from team form.
    Returns df with predicted_home_goals, predicted_away_goals, and outcome probabilities.
    """
    # Use league averages as baseline
    # Attack strength = team's avg goals scored / league avg
    # Defense strength = team's avg goals conceded / league avg

    df = df.copy()

    # Use the h_gs_avg and a_gs_avg as predicted goals (form-based simple Poisson)
    # Fill missing with league average
    league_avg_home = df.groupby("league")["home_score"].transform("mean").fillna(1.4)
    league_avg_away = df.groupby("league")["away_score"].transform("mean").fillna(1.1)

    df["pred_home_goals"] = np.where(
        df["h_gs_avg"].notna() & df["a_gc_avg"].notna(),
        (df["h_gs_avg"] * (df["a_gc_avg"] / df["a_gc_avg"].groupby(df["league"]).transform("mean").clip(0.5, 2.0))).clip(0.3, 4.0),
        league_avg_home
    )
    df["pred_away_goals"] = np.where(
        df["a_gs_avg"].notna() & df["h_gc_avg"].notna(),
        (df["a_gs_avg"] * (df["h_gc_avg"] / df["h_gc_avg"].groupby(df["league"]).transform("mean").clip(0.5, 2.0))).clip(0.3, 4.0),
        league_avg_away
    )

    # ELO adjustment: modify predicted goals based on ELO difference
    if "elo_diff" in df.columns:
        elo_factor = np.tanh(df["elo_diff"].fillna(0) / 400) * 0.15
        df["pred_home_goals"] = (df["pred_home_goals"] * (1 + elo_factor)).clip(0.3, 4.5)
        df["pred_away_goals"] = (df["pred_away_goals"] * (1 - elo_factor)).clip(0.3, 4.5)

    # Compute Poisson probabilities
    max_goals = 8
    prob_home = np.zeros(len(df))
    prob_draw = np.zeros(len(df))
    prob_away = np.zeros(len(df))
    prob_over25 = np.zeros(len(df))

    for i in range(len(df)):
        mu_h = df.iloc[i]["pred_home_goals"]
        mu_a = df.iloc[i]["pred_away_goals"]
        for h in range(max_goals):
            for a in range(max_goals):
                p = poisson.pmf(h, mu_h) * poisson.pmf(a, mu_a)
                if h > a:
                    prob_home[i] += p
                elif h == a:
                    prob_draw[i] += p
                else:
                    prob_away[i] += p
                if h + a > 2:
                    prob_over25[i] += p

    df["prob_home"] = prob_home
    df["prob_draw"] = prob_draw
    df["prob_away"] = prob_away
    df["prob_over25"] = prob_over25
    df["prob_under25"] = 1 - prob_over25

    return df


def simulate_bets(df: pd.DataFrame, min_edge: float = 0.05,
                  odds_min: float = 1.35, odds_max: float = 4.0) -> pd.DataFrame:
    """
    Simulate flat stake betting with edge threshold.
    Uses avg odds from BTB dataset (avg of 16+ bookmakers = close to true market).
    """
    bets = []

    for _, row in df.iterrows():
        # Home win
        if pd.notna(row["avg_odds_home_win"]) and row["avg_odds_home_win"] > 1.0:
            odds = row["avg_odds_home_win"]
            ip = 1 / odds
            mp = row["prob_home"]
            edge = mp - ip
            me = min_edge if odds < 2.0 else min_edge + 0.02
            if edge >= me and odds_min <= odds <= odds_max and mp >= 0.35:
                won = row["ftr"] == "H"
                bets.append({
                    "match_id": row["match_id"],
                    "league": row["league"],
                    "season": row.get("season", "unknown"),
                    "match_date": row["match_date"],
                    "market": "1X2",
                    "selection": "Home",
                    "odds": odds,
                    "mp": mp,
                    "ip": ip,
                    "edge": edge,
                    "won": won,
                    "pnl": (odds - 1) * STAKE if won else -STAKE,
                    "tier": row.get("tier", 3),
                })

        # Away win
        if pd.notna(row["avg_odds_away_win"]) and row["avg_odds_away_win"] > 1.0:
            odds = row["avg_odds_away_win"]
            ip = 1 / odds
            mp = row["prob_away"]
            edge = mp - ip
            me = min_edge + 0.02 if odds < 2.5 else min_edge + 0.04
            if edge >= me and 1.60 <= odds <= odds_max and mp >= 0.28:
                won = row["ftr"] == "A"
                bets.append({
                    "match_id": row["match_id"],
                    "league": row["league"],
                    "season": row.get("season", "unknown"),
                    "match_date": row["match_date"],
                    "market": "1X2",
                    "selection": "Away",
                    "odds": odds,
                    "mp": mp,
                    "ip": ip,
                    "edge": edge,
                    "won": won,
                    "pnl": (odds - 1) * STAKE if won else -STAKE,
                    "tier": row.get("tier", 3),
                })

        # Over 2.5 (only if we have total goals info)
        # BTB doesn't have O/U specific odds — skip for now

    return pd.DataFrame(bets) if bets else pd.DataFrame()


def compute_league_roi(bets_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-league ROI stats.
    """
    if bets_df.empty:
        return pd.DataFrame()

    results = []
    for league, grp in bets_df.groupby("league"):
        total_bets = len(grp)
        wins = grp["won"].sum()
        total_pnl = grp["pnl"].sum()
        roi = total_pnl / (total_bets * STAKE) * 100
        hit_rate = wins / total_bets if total_bets > 0 else 0
        avg_odds = grp["odds"].mean()
        avg_edge = grp["edge"].mean()

        # Per-season breakdown
        seasons_positive = 0
        seasons_total = 0
        season_rois = {}
        for season, sgrp in grp.groupby("season"):
            if len(sgrp) >= 10:
                s_roi = sgrp["pnl"].sum() / (len(sgrp) * STAKE) * 100
                season_rois[season] = round(s_roi, 2)
                seasons_total += 1
                if s_roi > 0:
                    seasons_positive += 1

        results.append({
            "league": league,
            "total_bets": total_bets,
            "wins": int(wins),
            "hit_rate": round(hit_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "roi": round(roi, 2),
            "avg_odds": round(avg_odds, 3),
            "avg_edge": round(avg_edge, 4),
            "seasons_positive": seasons_positive,
            "seasons_total": seasons_total,
            "season_rois": season_rois,
        })

    return pd.DataFrame(results).sort_values("roi", ascending=False)


def main():
    print("=" * 70)
    print("OddsIntel — Mega Backtest: Beat the Bookie Dataset")
    print(f"Run at: {datetime.now().isoformat()}")
    print("=" * 70)

    # ── 1. Load BTB data ──────────────────────────────────────────────────────
    print("\n[1/6] Loading Beat the Bookie closing_odds.csv...")
    btb_path = RAW_DIR / "closing_odds.csv"
    df_btb = pd.read_csv(btb_path, encoding="latin-1")
    print(f"  Loaded {len(df_btb):,} matches")
    print(f"  Date range: {df_btb['match_date'].min()} to {df_btb['match_date'].max()}")
    print(f"  Leagues: {df_btb['league'].nunique()}")

    # ── 2. Filter & clean ────────────────────────────────────────────────────
    print("\n[2/6] Filtering and cleaning...")

    # Remove cup/friendly competitions
    df_btb["is_cup"] = df_btb["league"].apply(is_cup_competition)
    df_league = df_btb[~df_btb["is_cup"]].copy()
    print(f"  After removing cups/friendlies: {len(df_league):,} matches")

    # Require minimum bookmakers
    df_league = df_league[df_league["n_odds_home_win"] >= MIN_BOOKMAKERS]
    print(f"  After requiring {MIN_BOOKMAKERS}+ bookmakers: {len(df_league):,} matches")

    # Require valid scores
    df_league = df_league[df_league["home_score"].notna() & df_league["away_score"].notna()]
    print(f"  After requiring valid scores: {len(df_league):,} matches")

    # Filter to leagues with 200+ matches
    lg_counts = df_league["league"].value_counts()
    valid_leagues = lg_counts[lg_counts >= MIN_MATCHES_PER_LEAGUE].index
    df_league = df_league[df_league["league"].isin(valid_leagues)].copy()
    print(f"  After requiring {MIN_MATCHES_PER_LEAGUE}+ matches per league: {len(df_league):,} matches, {df_league['league'].nunique()} leagues")

    # Assign tiers
    df_league["tier"] = df_league["league"].apply(assign_tier)
    # Remove cup placeholder
    df_league = df_league[df_league["tier"] != 99]

    # Parse dates
    df_league["match_date"] = pd.to_datetime(df_league["match_date"])
    df_league = df_league.sort_values("match_date").reset_index(drop=True)

    # Add season column (Aug-May football season)
    def get_season(dt):
        y = dt.year
        m = dt.month
        if m >= 7:
            return f"{y}-{str(y+1)[-2:]}"
        else:
            return f"{y-1}-{str(y)[-2:]}"

    df_league["season"] = df_league["match_date"].apply(get_season)
    print(f"  Seasons: {sorted(df_league['season'].unique())}")

    # ── 3. Load and merge ELO ────────────────────────────────────────────────
    print("\n[3/6] Loading global ELO ratings...")
    df_elo = pd.read_parquet(PROCESSED_DIR / "global_matches_with_elo.parquet")
    print(f"  ELO matches: {len(df_elo):,}")

    # Filter to the BTB time period + national competitions
    df_elo_filtered = df_elo[
        (df_elo["date"] >= "2005-01-01") &
        (df_elo["date"] <= "2015-12-31") &
        (df_elo["level"] == "national")
    ].copy()
    print(f"  ELO matches in period (national only): {len(df_elo_filtered):,}")

    elo_lookup = build_elo_lookup(df_elo_filtered)

    # Merge ELO
    df_league = merge_elo_to_btb(df_league, elo_lookup, min_score=65)
    elo_coverage = df_league["home_elo"].notna().mean() * 100
    print(f"  ELO coverage: {elo_coverage:.1f}%")

    # ── 4. Compute rolling form features ─────────────────────────────────────
    print(f"\n[4/6] Computing rolling form features for {df_league['league'].nunique()} leagues...")

    # Process per-league for isolation (teams only compete within leagues)
    all_with_form = []
    leagues = sorted(df_league["league"].unique())

    for i, league in enumerate(leagues):
        if (i + 1) % 50 == 0:
            print(f"  Processing league {i+1}/{len(leagues)}: {league}")
        lg_df = df_league[df_league["league"] == league].copy()
        try:
            lg_df = compute_rolling_form(lg_df, n=8)
            all_with_form.append(lg_df)
        except Exception as e:
            print(f"  Warning: form computation failed for {league}: {e}")
            all_with_form.append(lg_df)

    print(f"  Processed {len(all_with_form)} leagues")
    df_processed = pd.concat(all_with_form, ignore_index=True)
    print(f"  Combined: {len(df_processed):,} matches")

    # ── 5. Run Poisson model ─────────────────────────────────────────────────
    print("\n[5/6] Running Poisson predictions...")
    df_processed = predict_poisson(df_processed)
    print("  Predictions complete")

    # Simulate bets
    print("  Simulating bets...")
    bets_df = simulate_bets(df_processed, min_edge=0.05, odds_min=1.35, odds_max=4.0)
    print(f"  Total bets: {len(bets_df):,}")

    if bets_df.empty:
        print("ERROR: No bets generated!")
        return

    # Overall stats
    total_roi = bets_df["pnl"].sum() / (len(bets_df) * STAKE) * 100
    total_hr = bets_df["won"].mean() * 100
    print(f"\n  Overall: {len(bets_df):,} bets, {total_hr:.1f}% hit rate, {total_roi:+.2f}% ROI")

    # ── 6. Per-league analysis ───────────────────────────────────────────────
    print("\n[6/6] Computing per-league ROI...")
    league_roi = compute_league_roi(bets_df)

    print(f"\n  Top 30 leagues by ROI:")
    print(league_roi.head(30)[["league", "total_bets", "hit_rate", "roi",
                                "avg_odds", "seasons_positive", "seasons_total"]].to_string(index=False))

    print(f"\n  Leagues with positive ROI in 2+ seasons:")
    consistent = league_roi[league_roi["seasons_positive"] >= 2]
    print(consistent[["league", "total_bets", "roi", "seasons_positive", "seasons_total"]].to_string(index=False))

    # By tier
    print("\n  ROI by tier:")
    bets_df["tier"] = bets_df["tier"].fillna(3).astype(int)
    tier_stats = bets_df.groupby("tier").apply(lambda g: pd.Series({
        "bets": len(g),
        "roi": g["pnl"].sum() / (len(g) * STAKE) * 100,
        "hit_rate": g["won"].mean() * 100,
        "avg_odds": g["odds"].mean(),
    })).reset_index()
    print(tier_stats.to_string(index=False))

    # ── Save results ─────────────────────────────────────────────────────────
    print("\nSaving results...")

    # Full bets log
    bets_df.to_csv(RESULTS_DIR / "mega_backtest_bets.csv", index=False)
    print(f"  Saved bets to data/model_results/mega_backtest_bets.csv")

    # League ROI table
    league_roi.to_csv(RESULTS_DIR / "mega_backtest_league_roi.csv", index=False)
    print(f"  Saved league ROI to data/model_results/mega_backtest_league_roi.csv")

    # JSON results
    results_json = {
        "run_at": datetime.now().isoformat(),
        "dataset": "Beat the Bookie (2005-2015)",
        "total_matches": len(df_processed),
        "total_leagues": df_processed["league"].nunique(),
        "total_bets": len(bets_df),
        "overall_roi": round(total_roi, 3),
        "overall_hit_rate": round(total_hr, 3),
        "avg_edge": round(bets_df["edge"].mean(), 4),
        "avg_odds": round(bets_df["odds"].mean(), 3),
        "elo_coverage_pct": round(elo_coverage, 1),
        "by_tier": tier_stats.to_dict(orient="records"),
        "top_leagues_by_roi": league_roi.head(50).to_dict(orient="records"),
        "consistent_leagues": consistent.to_dict(orient="records"),
        "bottom_leagues": league_roi.tail(20).to_dict(orient="records"),
    }

    with open(RESULTS_DIR / "mega_backtest_results.json", "w") as f:
        json.dump(results_json, f, indent=2, default=str)
    print(f"  Saved JSON to data/model_results/mega_backtest_results.json")

    # ── Print summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("MEGA BACKTEST SUMMARY")
    print("=" * 70)
    print(f"Dataset: 479K match Beat the Bookie (2005-2015), {df_processed['league'].nunique()} leagues after filtering")
    print(f"ELO coverage: {elo_coverage:.1f}% of matches")
    print(f"Total bets: {len(bets_df):,} | Overall ROI: {total_roi:+.2f}%")
    print(f"\nTop 10 leagues by ROI (min {MIN_MATCHES_FOR_SEASONAL_ROI} bets):")
    top = league_roi[league_roi["total_bets"] >= MIN_MATCHES_FOR_SEASONAL_ROI].head(10)
    for _, row in top.iterrows():
        seasons = f"{row['seasons_positive']}/{row['seasons_total']} seasons +"
        print(f"  {row['league']:<45} ROI: {row['roi']:+.1f}%, {row['total_bets']:3d} bets, {seasons}")

    print(f"\nConsistently profitable leagues (2+ positive seasons):")
    for _, row in consistent.iterrows():
        print(f"  {row['league']:<45} ROI: {row['roi']:+.1f}%, {row['seasons_positive']}/{row['seasons_total']} seasons+")

    print("\n" + "=" * 70)
    print("Done!")


if __name__ == "__main__":
    main()
