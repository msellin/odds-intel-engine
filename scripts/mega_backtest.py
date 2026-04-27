"""
OddsIntel — Mega Backtest: Beat the Bookie Dataset
===================================================
479K matches, 818 leagues, 2005-2015, avg 16 bookmakers including Pinnacle.

Strategy:
1. Load Beat the Bookie closing_odds.csv (479K matches with avg/max odds)
2. Merge with global ELO ratings via fuzzy team name matching (optimized)
3. Run Poisson + form-based model for each league with 200+ matches
4. Output per-league ROI ranked table
5. Answer: which leagues show consistent edge?

Key findings from prior backtests (18 leagues, 133K matches):
- Tier 3-4 / lower leagues showed positive ROI (+4.8% in 2023-24)
- Top leagues are efficiently priced
- 1X2 market outperforms O/U in lower leagues

Optimization: fuzzy match unique team names ONCE (8.5K unique names) rather than per-match.
Vectorized Poisson: numpy broadcasting for all probabilities at once.
"""

import json
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from scipy.special import factorial

warnings.filterwarnings("ignore")

try:
    from rapidfuzz import fuzz, process as rfprocess
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    print("WARNING: rapidfuzz not found, ELO merge will be skipped")

ENGINE_DIR = Path(__file__).parent.parent
RAW_DIR = ENGINE_DIR / "data" / "raw" / "beat_the_bookie"
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

STAKE = 10.0
MIN_MATCHES_PER_LEAGUE = 200
MIN_BOOKMAKERS = 3
FUZZY_THRESHOLD = 75  # WRatio score cutoff - prevents false positives
ROLLING_N = 8         # Rolling form window size

LEAGUE_TIER_MAP = {
    "England: Premier League": 1, "England: Championship": 2,
    "England: League One": 3, "England: League Two": 4,
    "England: Blue Square Premier": 5, "England: Conference National": 5,
    "England: Conference North": 6, "England: Conference South": 6,
    "England: Southern Premier League": 6, "England: Northern Premier League": 6,
    "England: Ryman League": 6,
    "Spain: Primera Division": 1, "Spain: Segunda Division": 2,
    "Spain: Segunda B - Group 1": 3, "Spain: Segunda B - Group 2": 3,
    "Spain: Segunda B - Group 3": 3, "Spain: Segunda B - Group 4": 3,
    "Germany: Bundesliga": 1, "Germany: 2. Bundesliga": 2,
    "Germany: 3. Liga": 3, "Germany: Regionalliga North": 4,
    "Germany: Regionalliga South": 4, "Germany: Regionalliga West": 4,
    "Germany: Regionalliga Bayern": 4,
    "Italy: Serie A": 1, "Italy: Serie B": 2,
    "Italy: Lega Pro/Girone A": 3, "Italy: Lega Pro/Girone B": 3,
    "Italy: Lega Pro/Girone C": 3,
    "France: Ligue 1": 1, "France: Ligue 2": 2, "France: National": 3, "France: CFA": 4,
    "Netherlands: Eredivisie": 1, "Netherlands: Eerste Divisie": 2,
    "Portugal: Primeira Liga": 1, "Portugal: Segunda Liga": 2,
    "Turkey: Super Lig": 1, "Turkey: 1. Lig": 2, "Turkey: 2. Lig": 3,
    "Turkey: 3. Lig - Group 1": 4, "Turkey: 3. Lig - Group 2": 4,
    "Belgium: Jupiler League": 1, "Belgium: First Amateur Division": 2,
    "Scotland: Premiership": 1, "Scotland: Premier League": 1,
    "Scotland: Championship": 2, "Scotland: First Division": 2,
    "Scotland: League One": 3, "Scotland: Second Division": 3,
    "Scotland: League Two": 4, "Scotland: Third Division": 4,
    "Russia: Premier League": 1, "Russia: National League": 2, "Russia: FNL": 2,
    "Greece: Super League": 1, "Greece: Football League": 2,
    "Norway: Tippeligaen": 1, "Norway: Adeccoligaen": 2,
    "Sweden: Allsvenskan": 1, "Sweden: Superettan": 2,
    "Denmark: Superliga": 1, "Denmark: 1. Division": 2,
    "Czech Republic: Gambrinus Liga": 1, "Czech Republic: FNL": 2,
    "Romania: Liga I": 1, "Romania: Liga II": 2,
    "Poland: Ekstraklasa": 1, "Poland: 1. Liga": 2,
    "Austria: Bundesliga": 1, "Austria: Erste Liga": 2,
    "Switzerland: Super League": 1, "Switzerland: Challenge League": 2,
    "Bulgaria: A Group": 1, "Bulgaria: B Group": 2,
    "Hungary: OTP Bank Liga": 1, "Hungary: NB II": 2,
    "Croatia: 1. HNL": 1, "Croatia: 2. HNL": 2,
    "Serbia: SuperLiga": 1, "Serbia: 1. League": 2,
    "Ukraine: Premier League": 1, "Ukraine: First League": 2,
    "Argentina: Primera Division": 1, "Argentina: Nacional B": 2,
    "Argentina: Torneo Federal A": 3,
    "Brazil: Serie A": 1, "Brazil: Serie B": 2, "Brazil: Serie C": 3,
    "Chile: Primera Division": 1, "Chile: Primera B": 2,
    "Colombia: Liga Postobon": 1, "Colombia: Torneo Aguila": 2,
    "Mexico: Primera Division": 1, "Mexico: Ascenso MX": 2,
    "Japan: J-League": 1, "Japan: J. League Division 1": 1,
    "Japan: J-League Division 2": 2, "Japan: J2 League": 2,
    "South Korea: K League 1": 1, "South Korea: K League Classic": 1,
    "South Korea: K League Challenge": 2,
    "Australia: A-League": 1, "USA: MLS": 1, "USA: USL Championship": 2,
}

BTB_COUNTRY_TO_ELO = {
    "england": "england", "scotland": "scotland", "wales": "wales",
    "germany": "germany", "italy": "italy", "spain": "spain", "france": "france",
    "netherlands": "netherlands", "portugal": "portugal", "turkey": "turkey",
    "belgium": "belgium", "russia": "russia", "greece": "greece",
    "norway": "norway", "sweden": "sweden", "denmark": "denmark",
    "finland": "finland", "austria": "austria", "switzerland": "switzerland",
    "poland": "poland", "czech republic": "czech republic", "romania": "romania",
    "bulgaria": "bulgaria", "hungary": "hungary", "croatia": "croatia",
    "serbia": "serbia", "ukraine": "ukraine", "argentina": "argentina",
    "brazil": "brazil", "chile": "chile", "colombia": "colombia",
    "mexico": "mexico", "usa": "usa", "japan": "japan",
    "australia": "australia", "south korea": "south korea", "china": "china",
}


def assign_tier(league: str) -> int:
    if league in LEAGUE_TIER_MAP:
        return LEAGUE_TIER_MAP[league]
    comp = league.split(":", 1)[1].strip().lower() if ":" in league else league.lower()
    if any(k in comp for k in ["premier league", "primera division", "serie a", "bundesliga",
                                "ligue 1", "eredivisie", "primeira liga", "super lig",
                                "allsvenskan", "tippeligaen", "superliga", "ekstraklasa",
                                "mls", "j-league", "a-league", "k league"]):
        return 1
    if any(k in comp for k in ["championship", "segunda", "serie b", "2. bundesliga",
                                "ligue 2", "eerste", "segunda liga", "primera b",
                                "national league", "superettan", "1. division", "adecco"]):
        return 2
    if any(k in comp for k in ["league one", "tercera", "national", "3. liga", "lega pro"]):
        return 3
    return 3


def is_cup_competition(league: str) -> bool:
    comp = league.lower()
    return any(k in comp for k in [
        "cup", "trophy", "shield", "copa", "coppa", "coupe", "pokal",
        "friendly", "international", "supercup", "playoff", "play-off",
        "qualifier", "qualif", "world:", "europe:", "africa:", "asia:",
        "south america:", "concacaf:", "olympic", "u-21", "u21", "u-20",
        "u20", "u-19", "u19", "u-18", "u18", "youth", "women", "womens",
        "super cup", "relegation play", "promotion play",
    ])


def get_season(dt: pd.Timestamp) -> str:
    m, y = dt.month, dt.year
    return f"{y}-{str(y+1)[-2:]}" if m >= 7 else f"{y-1}-{str(y)[-2:]}"


def build_name_mapping(btb_teams: dict, elo_country_teams: dict,
                        min_score: int = FUZZY_THRESHOLD) -> dict:
    """
    Build mapping: {(btb_country_lower, btb_team_name) -> elo_team_name}
    Using WRatio scorer which handles abbreviations + partial words.
    Only processes unique team names (not per-match) - runs in < 1 second.
    """
    if not HAS_RAPIDFUZZ:
        return {}

    print("Building fuzzy name mapping (unique teams)...")
    mapping = {}
    matched = attempted = 0

    for btb_country, team_names in btb_teams.items():
        elo_country = BTB_COUNTRY_TO_ELO.get(btb_country.lower())
        if not elo_country or elo_country not in elo_country_teams:
            continue

        elo_list = elo_country_teams[elo_country]
        for btb_name in team_names:
            attempted += 1
            result = rfprocess.extractOne(
                btb_name, elo_list,
                scorer=fuzz.WRatio,
                score_cutoff=min_score
            )
            if result:
                mapping[(btb_country.lower(), btb_name)] = result[0]
                matched += 1

    print(f"  {attempted} teams attempted, {matched} matched ({matched/max(attempted,1)*100:.1f}%)")
    return mapping


def merge_elo_to_btb(df: pd.DataFrame, df_elo: pd.DataFrame,
                      name_mapping: dict) -> pd.DataFrame:
    """
    Vectorized ELO merge using pandas merge_asof for each country.
    For each BTB match, find the closest ELO entry by date (within 45 days).
    """
    print("Merging ELO (vectorized merge_asof per country)...")

    df = df.copy()
    df["_country"] = df["league"].str.split(":").str[0].str.strip().str.lower()

    # Map BTB team names to ELO team names
    df["_home_elo_name"] = df.apply(
        lambda r: name_mapping.get((r["_country"], r["home_team"])), axis=1
    )
    df["_away_elo_name"] = df.apply(
        lambda r: name_mapping.get((r["_country"], r["away_team"])), axis=1
    )

    has_mapping = df["_home_elo_name"].notna() & df["_away_elo_name"].notna()
    print(f"  Rows with name mapping: {has_mapping.sum():,} / {len(df):,}")

    # ELO ratings table (long format: country, team, date, elo)
    home_elo = df_elo[["home_country", "home", "date", "home_elo"]].rename(
        columns={"home": "team", "home_elo": "elo", "home_country": "elo_country"})
    away_elo = df_elo[["home_country", "away", "date", "away_elo"]].rename(
        columns={"away": "team", "away_elo": "elo", "home_country": "elo_country"})
    ratings = pd.concat([home_elo, away_elo], ignore_index=True)
    ratings = ratings.dropna(subset=["team"]).copy()
    ratings["team"] = ratings["team"].astype(str)
    ratings = ratings.sort_values("date").reset_index(drop=True)

    df["home_elo"] = np.nan
    df["away_elo"] = np.nan

    # Process countries with mapped teams
    countries_to_process = df[has_mapping]["_country"].unique()

    for btb_country in countries_to_process:
        elo_country = BTB_COUNTRY_TO_ELO.get(btb_country)
        if not elo_country:
            continue

        mask = (df["_country"] == btb_country) & has_mapping
        btb_sub = df[mask].copy()
        if len(btb_sub) == 0:
            continue

        elo_sub = ratings[ratings["elo_country"] == elo_country].copy()
        if len(elo_sub) == 0:
            continue

        # For each team in this country, merge_asof by date
        unique_elo_teams = set(btb_sub["_home_elo_name"].dropna().unique()) | \
                           set(btb_sub["_away_elo_name"].dropna().unique())

        elo_by_team = {}
        for team in unique_elo_teams:
            t_df = elo_sub[elo_sub["team"] == team][["date", "elo"]].drop_duplicates("date").sort_values("date")
            if len(t_df) > 0:
                elo_by_team[team] = t_df

        # Merge for home teams
        for team in btb_sub["_home_elo_name"].dropna().unique():
            if team not in elo_by_team:
                continue
            t_rows = btb_sub[btb_sub["_home_elo_name"] == team].copy()
            merged = pd.merge_asof(
                t_rows[["match_date"]].sort_values("match_date"),
                elo_by_team[team].rename(columns={"elo": "h_elo"}),
                left_on="match_date", right_on="date",
                tolerance=pd.Timedelta("45 days"),
                direction="nearest"
            )
            # Map back to original index
            idx = t_rows[t_rows["_home_elo_name"] == team].index
            valid = merged["h_elo"].notna().values
            df.loc[idx[valid], "home_elo"] = merged.loc[merged["h_elo"].notna(), "h_elo"].values

        # Merge for away teams
        for team in btb_sub["_away_elo_name"].dropna().unique():
            if team not in elo_by_team:
                continue
            t_rows = btb_sub[btb_sub["_away_elo_name"] == team].copy()
            merged = pd.merge_asof(
                t_rows[["match_date"]].sort_values("match_date"),
                elo_by_team[team].rename(columns={"elo": "a_elo"}),
                left_on="match_date", right_on="date",
                tolerance=pd.Timedelta("45 days"),
                direction="nearest"
            )
            idx = t_rows[t_rows["_away_elo_name"] == team].index
            valid = merged["a_elo"].notna().values
            df.loc[idx[valid], "away_elo"] = merged.loc[merged["a_elo"].notna(), "a_elo"].values

    df["elo_diff"] = df["home_elo"] - df["away_elo"]
    elo_cov = df["home_elo"].notna().mean() * 100
    print(f"  ELO coverage: {elo_cov:.1f}%")

    df = df.drop(columns=["_country", "_home_elo_name", "_away_elo_name"], errors="ignore")
    return df


def compute_rolling_form_fast(df: pd.DataFrame, n: int = ROLLING_N) -> pd.DataFrame:
    """Fast rolling form computation per league."""
    df = df.copy()
    df["ftr"] = np.where(df["home_score"] > df["away_score"], "H",
                np.where(df["home_score"] < df["away_score"], "A", "D"))
    df["total_goals"] = df["home_score"] + df["away_score"]
    df["over_25"] = (df["total_goals"] > 2.5).astype(int)

    home_df = df[["match_date", "match_id", "home_team", "home_score",
                   "away_score", "ftr", "total_goals", "over_25"]].copy()
    home_df["team"] = home_df["home_team"]
    home_df["venue"] = "home"
    home_df["gs"] = home_df["home_score"]
    home_df["gc"] = home_df["away_score"]
    home_df["win"] = (home_df["ftr"] == "H").astype(float)
    home_df["pts"] = home_df["win"] * 3 + (home_df["ftr"] == "D").astype(float)

    away_df = df[["match_date", "match_id", "home_team", "away_team", "home_score",
                   "away_score", "ftr", "total_goals", "over_25"]].copy()
    away_df["team"] = away_df["away_team"]
    away_df["venue"] = "away"
    away_df["gs"] = away_df["away_score"]
    away_df["gc"] = away_df["home_score"]
    away_df["win"] = (away_df["ftr"] == "A").astype(float)
    away_df["pts"] = away_df["win"] * 3 + (away_df["ftr"] == "D").astype(float)

    long = pd.concat([home_df, away_df], ignore_index=True).sort_values("match_date")

    all_stats = []
    for team_name, g in long.groupby("team"):
        g = g.sort_values("match_date")
        s = pd.DataFrame(index=g.index)
        s["team"] = team_name
        s["match_date"] = g["match_date"]
        s["venue"] = g["venue"]
        s["ppg"] = g["pts"].shift(1).rolling(n, min_periods=3).mean()
        s["gs_avg"] = g["gs"].shift(1).rolling(n, min_periods=3).mean()
        s["gc_avg"] = g["gc"].shift(1).rolling(n, min_periods=3).mean()
        all_stats.append(s)

    rolling = pd.concat(all_stats, ignore_index=True)

    home_r = rolling[rolling["venue"] == "home"].drop(columns=["venue"]).rename(
        columns={"ppg": "h_ppg", "gs_avg": "h_gs_avg", "gc_avg": "h_gc_avg"})
    away_r = rolling[rolling["venue"] == "away"].drop(columns=["venue"]).rename(
        columns={"ppg": "a_ppg", "gs_avg": "a_gs_avg", "gc_avg": "a_gc_avg"})

    df = df.merge(
        home_r[["team", "match_date", "h_ppg", "h_gs_avg", "h_gc_avg"]],
        left_on=["home_team", "match_date"], right_on=["team", "match_date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    df = df.merge(
        away_r[["team", "match_date", "a_ppg", "a_gs_avg", "a_gc_avg"]],
        left_on=["away_team", "match_date"], right_on=["team", "match_date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    return df


def predict_poisson_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """Fully vectorized Poisson prediction with attack/defense ratings + ELO."""
    df = df.copy()

    league_home_avg = df.groupby("league")["home_score"].transform("mean").fillna(1.4)
    league_away_avg = df.groupby("league")["away_score"].transform("mean").fillna(1.1)

    league_gs_h = df.groupby("league")["h_gs_avg"].transform("mean").clip(0.5, 3.0)
    league_gc_h = df.groupby("league")["h_gc_avg"].transform("mean").clip(0.5, 3.0)
    league_gs_a = df.groupby("league")["a_gs_avg"].transform("mean").clip(0.5, 3.0)
    league_gc_a = df.groupby("league")["a_gc_avg"].transform("mean").clip(0.5, 3.0)

    has_h = df["h_gs_avg"].notna() & df["a_gc_avg"].notna()
    has_a = df["a_gs_avg"].notna() & df["h_gc_avg"].notna()

    pred_home = np.where(
        has_h,
        (df["h_gs_avg"].fillna(league_home_avg) / league_gs_h.clip(0.5) *
         df["a_gc_avg"].fillna(league_gc_a) / league_gc_a.clip(0.5) *
         league_home_avg).clip(0.2, 5.0),
        league_home_avg.values
    )
    pred_away = np.where(
        has_a,
        (df["a_gs_avg"].fillna(league_away_avg) / league_gs_a.clip(0.5) *
         df["h_gc_avg"].fillna(league_gc_h) / league_gc_h.clip(0.5) *
         league_away_avg).clip(0.2, 5.0),
        league_away_avg.values
    )

    if "elo_diff" in df.columns:
        elo_adj = np.tanh(df["elo_diff"].fillna(0).values / 400) * 0.12
        pred_home = (pred_home * (1 + elo_adj)).clip(0.2, 5.0)
        pred_away = (pred_away * (1 - elo_adj)).clip(0.2, 5.0)

    df["pred_home"] = pred_home
    df["pred_away"] = pred_away

    # Vectorized Poisson via numpy broadcasting
    max_g = 7
    k = np.arange(max_g, dtype=np.float64)
    fac_k = factorial(k)

    mu_h = pred_home[:, np.newaxis]  # (n, 1)
    mu_a = pred_away[:, np.newaxis]  # (n, 1)

    pmf_h = np.exp(-mu_h) * (mu_h ** k) / fac_k  # (n, max_g)
    pmf_a = np.exp(-mu_a) * (mu_a ** k) / fac_k  # (n, max_g)

    score_matrix = pmf_h[:, :, np.newaxis] * pmf_a[:, np.newaxis, :]  # (n, max_g, max_g)

    h_win_idx, a_win_idx = np.triu_indices(max_g, k=1)  # h > a
    h_los_idx, a_los_idx = np.tril_indices(max_g, k=-1)  # h < a (away wins)

    prob_home = score_matrix[:, h_win_idx, a_win_idx].sum(axis=1)
    prob_draw = score_matrix[:, np.arange(max_g), np.arange(max_g)].sum(axis=1)
    prob_away = score_matrix[:, h_los_idx, a_los_idx].sum(axis=1)

    h_mat, a_mat = np.meshgrid(k, k, indexing="ij")
    over_mask = (h_mat + a_mat) > 2
    prob_over25 = (score_matrix * over_mask[np.newaxis, :, :]).sum(axis=(1, 2))

    df["prob_home"] = prob_home
    df["prob_draw"] = prob_draw
    df["prob_away"] = prob_away
    df["prob_over25"] = prob_over25

    return df


def simulate_bets_vectorized(df: pd.DataFrame, min_edge: float = 0.05) -> pd.DataFrame:
    """Vectorized bet simulation using flat stakes."""
    bets_list = []

    # Home win bets
    h_mask = df["avg_odds_home_win"].notna() & (df["avg_odds_home_win"] > 1.0) & (df["avg_odds_home_win"] <= 4.5)
    h_df = df[h_mask].copy()
    if len(h_df) > 0:
        h_df["ip"] = 1 / h_df["avg_odds_home_win"]
        h_df["edge"] = h_df["prob_home"] - h_df["ip"]
        h_df["req_edge"] = np.where(h_df["avg_odds_home_win"] < 1.7, min_edge,
                            np.where(h_df["avg_odds_home_win"] < 2.5, min_edge + 0.01, min_edge + 0.02))
        sel = h_df[(h_df["edge"] >= h_df["req_edge"]) &
                   (h_df["avg_odds_home_win"] >= 1.30) &
                   (h_df["prob_home"] >= 0.33)].copy()
        if len(sel) > 0:
            sel["selection"] = "Home"
            sel["odds"] = sel["avg_odds_home_win"]
            sel["mp"] = sel["prob_home"]
            sel["won"] = sel["ftr"] == "H"
            sel["pnl"] = (sel["odds"] - 1) * STAKE * sel["won"] - STAKE * (~sel["won"])
            bets_list.append(sel[["match_id", "league", "season", "match_date",
                                   "tier", "selection", "odds", "mp", "ip", "edge", "won", "pnl"]])

    # Away win bets
    a_mask = df["avg_odds_away_win"].notna() & (df["avg_odds_away_win"] > 1.0) & (df["avg_odds_away_win"] <= 5.0)
    a_df = df[a_mask].copy()
    if len(a_df) > 0:
        a_df["ip"] = 1 / a_df["avg_odds_away_win"]
        a_df["edge"] = a_df["prob_away"] - a_df["ip"]
        a_df["req_edge"] = np.where(a_df["avg_odds_away_win"] < 2.0, min_edge + 0.01,
                            np.where(a_df["avg_odds_away_win"] < 3.0, min_edge + 0.02, min_edge + 0.04))
        sel = a_df[(a_df["edge"] >= a_df["req_edge"]) &
                   (a_df["avg_odds_away_win"] >= 1.60) &
                   (a_df["prob_away"] >= 0.27)].copy()
        if len(sel) > 0:
            sel["selection"] = "Away"
            sel["odds"] = sel["avg_odds_away_win"]
            sel["mp"] = sel["prob_away"]
            sel["won"] = sel["ftr"] == "A"
            sel["pnl"] = (sel["odds"] - 1) * STAKE * sel["won"] - STAKE * (~sel["won"])
            bets_list.append(sel[["match_id", "league", "season", "match_date",
                                   "tier", "selection", "odds", "mp", "ip", "edge", "won", "pnl"]])

    return pd.concat(bets_list, ignore_index=True) if bets_list else pd.DataFrame()


def compute_league_stats(bets_df: pd.DataFrame, min_bets: int = 20) -> pd.DataFrame:
    """Compute per-league ROI statistics with per-season breakdown."""
    if bets_df.empty:
        return pd.DataFrame()

    results = []
    for league, grp in bets_df.groupby("league"):
        if len(grp) < min_bets:
            continue
        n = len(grp)
        wins = grp["won"].sum()
        pnl = grp["pnl"].sum()
        roi = pnl / (n * STAKE) * 100

        seasons_pos = 0
        seasons_total = 0
        season_rois = {}
        for season, sg in grp.groupby("season"):
            if len(sg) >= 10:
                s_roi = sg["pnl"].sum() / (len(sg) * STAKE) * 100
                season_rois[str(season)] = round(float(s_roi), 2)
                seasons_total += 1
                if s_roi > 0:
                    seasons_pos += 1

        results.append({
            "league": league,
            "tier": int(grp["tier"].mode().iloc[0]) if len(grp) > 0 else 3,
            "total_bets": int(n),
            "wins": int(wins),
            "hit_rate": round(float(wins / n), 4),
            "total_pnl": round(float(pnl), 2),
            "roi": round(float(roi), 2),
            "avg_odds": round(float(grp["odds"].mean()), 3),
            "avg_edge": round(float(grp["edge"].mean()), 4),
            "seasons_positive": int(seasons_pos),
            "seasons_total": int(seasons_total),
            "season_rois": season_rois,
        })

    return pd.DataFrame(results).sort_values("roi", ascending=False).reset_index(drop=True)


def main():
    t_start = datetime.now()
    print("=" * 70)
    print("OddsIntel — Mega Backtest: Beat the Bookie Dataset")
    print(f"Started: {t_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ── 1. Load BTB data ──────────────────────────────────────────────────────
    print("\n[1/6] Loading Beat the Bookie data...")
    df_btb = pd.read_csv(RAW_DIR / "closing_odds.csv", encoding="latin-1")
    print(f"  Loaded {len(df_btb):,} matches, {df_btb['league'].nunique()} leagues")
    print(f"  Date range: {df_btb['match_date'].min()} — {df_btb['match_date'].max()}")

    # ── 2. Filter ─────────────────────────────────────────────────────────────
    print("\n[2/6] Filtering...")
    df_btb["match_date"] = pd.to_datetime(df_btb["match_date"]).astype("datetime64[ns]")

    # Remove cups/friendlies
    df = df_btb[~df_btb["league"].apply(is_cup_competition)].copy()
    print(f"  After cup/friendly filter: {len(df):,}")

    # Min bookmakers
    df = df[df["n_odds_home_win"] >= MIN_BOOKMAKERS].copy()
    print(f"  After {MIN_BOOKMAKERS}+ bookmakers: {len(df):,}")

    # Valid scores
    df = df[df["home_score"].notna() & df["away_score"].notna()].copy()

    # Leagues with 200+ matches
    lg_counts = df["league"].value_counts()
    df = df[df["league"].isin(lg_counts[lg_counts >= MIN_MATCHES_PER_LEAGUE].index)].copy()
    n_leagues = df["league"].nunique()
    print(f"  After {MIN_MATCHES_PER_LEAGUE}+ matches/league: {len(df):,} matches, {n_leagues} leagues")

    # Enrich
    df["tier"] = df["league"].apply(assign_tier)
    df["season"] = df["match_date"].apply(get_season)
    df = df.sort_values("match_date").reset_index(drop=True)

    print(f"  Seasons: {sorted(df['season'].unique())}")
    print(f"  Tier distribution: {df['tier'].value_counts().sort_index().to_dict()}")

    # ── 3. ELO merge ─────────────────────────────────────────────────────────
    print("\n[3/6] Merging ELO ratings...")
    df_elo = pd.read_parquet(PROCESSED_DIR / "global_matches_with_elo.parquet")
    df_elo_period = df_elo[
        (df_elo["date"] >= "2004-06-01") &
        (df_elo["date"] <= "2015-12-31") &
        (df_elo["level"] == "national") &
        df_elo["home"].notna()
    ].copy()
    # Normalize datetime types for merge_asof compatibility
    df_elo_period["date"] = df_elo_period["date"].astype("datetime64[ns]")
    print(f"  ELO data (national, 2004-2015): {len(df_elo_period):,} matches")

    # Build ELO team index per country
    elo_country_teams = {}
    for country, grp in df_elo_period.groupby("home_country"):
        teams = sorted(
            set(str(t) for t in grp["home"].dropna().unique()) |
            set(str(t) for t in grp["away"].dropna().unique())
        )
        elo_country_teams[country] = teams

    # Build BTB teams per country
    df["_country"] = df["league"].str.split(":").str[0].str.strip().str.lower()
    btb_teams = {}
    for country, grp in df.groupby("_country"):
        btb_teams[country] = (
            set(grp["home_team"].dropna().unique()) |
            set(grp["away_team"].dropna().unique())
        )
    df = df.drop(columns=["_country"])

    # Fuzzy match unique team names
    name_mapping = build_name_mapping(btb_teams, elo_country_teams)

    # Vectorized ELO merge
    df = merge_elo_to_btb(df, df_elo_period, name_mapping)
    elo_cov = df["home_elo"].notna().mean() * 100

    print(f"  ELO coverage: {elo_cov:.1f}% of all matches")
    tier1_cov = df[df["tier"] == 1]["home_elo"].notna().mean() * 100
    print(f"  ELO coverage tier 1: {tier1_cov:.1f}%")

    # ── 4. Rolling form per league ────────────────────────────────────────────
    print(f"\n[4/6] Computing rolling form ({n_leagues} leagues)...")

    all_processed = []
    for i, (league, lg_df) in enumerate(df.groupby("league")):
        if (i + 1) % 50 == 0:
            elapsed = (datetime.now() - t_start).total_seconds()
            print(f"  {i+1}/{n_leagues} ({elapsed:.0f}s): {league[:50]}")
        try:
            lg_df = compute_rolling_form_fast(lg_df.copy())
        except Exception as e:
            print(f"  Warning: {league}: {e}")
        all_processed.append(lg_df)

    df_proc = pd.concat(all_processed, ignore_index=True)
    print(f"  Done: {len(df_proc):,} matches")

    # ── 5. Poisson predictions ────────────────────────────────────────────────
    print("\n[5/6] Running Poisson model (vectorized)...")
    df_proc = predict_poisson_vectorized(df_proc)

    bets_df = simulate_bets_vectorized(df_proc, min_edge=0.05)
    print(f"  Bets generated: {len(bets_df):,}")

    if bets_df.empty:
        print("ERROR: no bets generated. Exiting.")
        return

    total_roi = bets_df["pnl"].sum() / (len(bets_df) * STAKE) * 100
    total_hr = bets_df["won"].mean() * 100
    print(f"  Overall: {len(bets_df):,} bets | {total_hr:.1f}% hit | {total_roi:+.2f}% ROI")

    # ── 6. Per-league stats ───────────────────────────────────────────────────
    print("\n[6/6] Per-league ROI analysis...")
    league_roi = compute_league_stats(bets_df, min_bets=20)

    print(f"\n{'League':<50} {'Bets':>5} {'ROI':>8} {'Hit%':>6} {'S+':>5}")
    print("-" * 78)
    for _, row in league_roi.head(40).iterrows():
        print(f"{row['league']:<50} {row['total_bets']:>5} {row['roi']:>+7.1f}% "
              f"{row['hit_rate']*100:>5.1f}% {row['seasons_positive']}/{row['seasons_total']}")

    print(f"\n... bottom 15 ...")
    for _, row in league_roi.tail(15).iterrows():
        print(f"{row['league']:<50} {row['total_bets']:>5} {row['roi']:>+7.1f}%")

    # Tier breakdown
    print("\nROI by tier:")
    for tier, grp in bets_df.groupby("tier"):
        roi = grp["pnl"].sum() / (len(grp) * STAKE) * 100
        print(f"  Tier {tier}: {len(grp):,} bets | {grp['won'].mean()*100:.1f}% hit | {roi:+.2f}% ROI | avg odds {grp['odds'].mean():.2f}")

    # Country breakdown
    bets_df["country"] = bets_df["league"].str.split(":").str[0].str.strip()
    country_stats = bets_df.groupby("country").apply(lambda g: pd.Series({
        "bets": len(g),
        "roi": round(g["pnl"].sum() / (len(g) * STAKE) * 100, 2),
        "hit_rate": round(g["won"].mean() * 100, 1),
        "avg_odds": round(g["odds"].mean(), 2),
    })).reset_index()
    country_stats = country_stats[country_stats["bets"] >= 50].sort_values("roi", ascending=False)
    print(f"\nROI by country (50+ bets, top 25):")
    print(country_stats.head(25).to_string(index=False))

    # Consistently profitable
    consistent = league_roi[
        (league_roi["seasons_positive"] >= 2) &
        (league_roi["total_bets"] >= 30)
    ].copy()
    print(f"\nConsistently profitable leagues (2+ positive seasons, 30+ bets): {len(consistent)}")
    for _, row in consistent.iterrows():
        s_str = " | ".join([f"{s}: {v:+.0f}%" for s, v in sorted(row["season_rois"].items())])
        print(f"  [{row['tier']}] {row['league']:<48} ROI={row['roi']:+.1f}% ({row['total_bets']} bets) | {s_str}")

    # ── Save ─────────────────────────────────────────────────────────────────
    elapsed = (datetime.now() - t_start).total_seconds()
    print(f"\nSaving results (elapsed: {elapsed:.0f}s)...")

    bets_df.to_csv(RESULTS_DIR / "mega_backtest_bets.csv", index=False)
    league_roi.to_csv(RESULTS_DIR / "mega_backtest_league_roi.csv", index=False)

    tier_stats_list = []
    for tier, grp in bets_df.groupby("tier"):
        tier_stats_list.append({
            "tier": int(tier),
            "bets": int(len(grp)),
            "roi_pct": round(float(grp["pnl"].sum() / (len(grp) * STAKE) * 100), 2),
            "hit_rate": round(float(grp["won"].mean()), 4),
            "avg_odds": round(float(grp["odds"].mean()), 3),
        })

    results_json = {
        "run_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "dataset": "Beat the Bookie closing_odds.csv (2005-2015)",
        "total_matches": int(len(df_proc)),
        "total_leagues": int(n_leagues),
        "elo_coverage_pct": round(float(elo_cov), 1),
        "total_bets": int(len(bets_df)),
        "overall_roi_pct": round(float(total_roi), 2),
        "overall_hit_rate_pct": round(float(total_hr), 2),
        "avg_edge": round(float(bets_df["edge"].mean()), 4),
        "avg_odds": round(float(bets_df["odds"].mean()), 3),
        "by_tier": tier_stats_list,
        "top_50_leagues_by_roi": league_roi.head(50).to_dict(orient="records"),
        "consistently_profitable_leagues": consistent.to_dict(orient="records"),
        "bottom_20_leagues": league_roi.tail(20).to_dict(orient="records"),
        "country_stats": country_stats.head(30).to_dict(orient="records"),
    }

    with open(RESULTS_DIR / "mega_backtest_results.json", "w") as f:
        json.dump(results_json, f, indent=2, default=str)

    print(f"  data/model_results/mega_backtest_results.json")
    print(f"  data/model_results/mega_backtest_bets.csv")
    print(f"  data/model_results/mega_backtest_league_roi.csv")

    print("\n" + "=" * 70)
    print("MEGA BACKTEST COMPLETE")
    print(f"  Matches: {len(df_proc):,} | Leagues: {n_leagues} | Bets: {len(bets_df):,}")
    print(f"  Overall ROI: {total_roi:+.2f}% | ELO coverage: {elo_cov:.1f}%")
    print(f"  Consistent leagues: {len(consistent)}")
    print(f"  Total time: {elapsed:.0f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
