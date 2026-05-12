"""
add_romanian_league_data.py

Downloads Romanian Liga I (Superliga) historical data from football-data.co.uk
and appends it to data/processed/targets_poisson_history.csv.

This gives FCSB and other Romanian Liga I teams Tier A (bookmaker-calibrated)
predictions instead of falling back to targets_global, which mixes in European
competition data and inverts FCSB's expected goals.

Run once: python3 scripts/add_romanian_league_data.py
"""

import io
import pathlib
import sys
import urllib.request

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "processed" / "targets_poisson_history.csv"

ROU_URL = "https://www.football-data.co.uk/new/ROU.csv"

# Keep last 5 seasons of Liga I data — enough history for the Poisson model
KEEP_SEASONS = {"2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025"}


def download_rou() -> pd.DataFrame:
    print(f"  Downloading {ROU_URL} ...")
    req = urllib.request.Request(ROU_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), low_memory=False)
    print(f"  Downloaded {len(df):,} rows, columns: {list(df.columns)}")
    return df


def parse_season(s: str) -> str:
    """'2024/2025' → '2024-25'"""
    parts = s.split("/")
    if len(parts) == 2:
        return f"{parts[0]}-{parts[1][2:]}"
    return s


def convert_date(d: str) -> str:
    """'20/07/2012' → '2012-07-20'"""
    try:
        return pd.to_datetime(d, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return d


def build_targets_rows(rou: pd.DataFrame) -> pd.DataFrame:
    df = rou[rou["Season"].isin(KEEP_SEASONS)].copy()
    print(f"  Filtered to {len(df):,} rows for seasons: {sorted(KEEP_SEASONS)}")

    # Require Pinnacle odds (best coverage for Romanian Liga I; B365 stopped ~2018)
    before = len(df)
    if "PSCH" in df.columns:
        df = df.dropna(subset=["PSCH"])
    print(f"  Dropped {before - len(df)} rows with no Pinnacle odds → {len(df):,} remaining")

    rows = []
    for _, r in df.iterrows():
        try:
            fthg = int(r["HG"])
            ftag = int(r["AG"])
        except (ValueError, TypeError):
            continue
        total = fthg + ftag
        row = {
            "Date": convert_date(str(r.get("Date", ""))),
            "home_team": str(r["Home"]).strip(),
            "away_team": str(r["Away"]).strip(),
            "result": str(r.get("Res", "")).strip(),
            "FTHG": fthg,
            "FTAG": ftag,
            "total_goals": total,
            "over_25": 1 if total > 2.5 else 0,
            "btts": 1 if fthg > 0 and ftag > 0 else 0,
            "league_code": "RO1",
            "league": "Liga I",
            "season": parse_season(str(r.get("Season", ""))),
            "tier": 1,
            "AvgH":    r.get("AvgCH", ""),
            "AvgD":    r.get("AvgCD", ""),
            "AvgA":    r.get("AvgCA", ""),
            "Avg>2.5": "",
            "Avg<2.5": "",
            "B365H":   r.get("B365CH", ""),
            "B365D":   r.get("B365CD", ""),
            "B365A":   r.get("B365CA", ""),
            "PSH":     r.get("PSCH", ""),
            "PSD":     r.get("PSCD", ""),
            "PSA":     r.get("PSCA", ""),
        }
        rows.append(row)

    out = pd.DataFrame(rows)
    print(f"  Built {len(out):,} valid rows")
    return out


def main():
    if not HISTORY_PATH.exists():
        print(f"ERROR: {HISTORY_PATH} not found")
        sys.exit(1)

    existing = pd.read_csv(HISTORY_PATH, low_memory=False)
    print(f"  targets_poisson_history.csv currently: {len(existing):,} rows")

    # Skip if Romanian data already present
    if "RO1" in existing["league_code"].values:
        print("  RO1 data already present — checking row count ...")
        ro1_count = (existing["league_code"] == "RO1").sum()
        print(f"  {ro1_count} existing RO1 rows. Re-running will de-duplicate by Date+teams.")

    rou_raw = download_rou()
    new_rows = build_targets_rows(rou_raw)

    if len(new_rows) == 0:
        print("  No rows to add — exiting.")
        sys.exit(0)

    # Show sample teams found
    teams = sorted(set(new_rows["home_team"].unique()) | set(new_rows["away_team"].unique()))
    print(f"\n  Teams in new data ({len(teams)} total):")
    fcsb_names = [t for t in teams if "FCSB" in t or "Steaua" in t or "Bucharest" in t]
    print(f"  FCSB-related: {fcsb_names}")
    slobozia_names = [t for t in teams if "Slobozia" in t or "Unirea" in t]
    print(f"  Slobozia-related: {slobozia_names}")
    print(f"  All teams: {teams[:20]} ...")

    # De-duplicate against existing (by Date + home_team + away_team)
    combined = pd.concat([existing, new_rows], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["Date", "home_team", "away_team"], keep="first")
    print(f"\n  After dedup: {before} → {len(combined)} rows ({before - len(combined)} duplicates removed)")

    # Sort by Date
    combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
    combined = combined.sort_values("Date")
    combined["Date"] = combined["Date"].dt.strftime("%Y-%m-%d")

    combined.to_csv(HISTORY_PATH, index=False)
    print(f"\n  Written {len(combined):,} rows to {HISTORY_PATH}")
    added = len(combined) - len(existing)
    print(f"  Net rows added: {added}")

    # Quick sanity check: show predicted league for FCSB using last 10 home games
    fcsb_rows = combined[
        (combined["home_team"].str.contains("FCSB", na=False)) |
        (combined["away_team"].str.contains("FCSB", na=False))
    ].tail(20)
    if len(fcsb_rows) > 0:
        gf, ga = [], []
        for _, row in fcsb_rows.iterrows():
            if "FCSB" in str(row["home_team"]):
                gf.append(float(row["FTHG"]) if pd.notna(row["FTHG"]) else 0)
                ga.append(float(row["FTAG"]) if pd.notna(row["FTAG"]) else 0)
            else:
                gf.append(float(row["FTAG"]) if pd.notna(row["FTAG"]) else 0)
                ga.append(float(row["FTHG"]) if pd.notna(row["FTHG"]) else 0)
        import numpy as np
        print(f"\n  FCSB last {len(gf)} matches in v9: GF avg={np.mean(gf):.2f}, GA avg={np.mean(ga):.2f}")
        exp_h_raw = max(0.3, float(np.mean(gf[-10:]))) * 1.08
        print(f"  → exp_home raw (before blending with opponent): {exp_h_raw:.2f}")
    else:
        print("\n  WARNING: FCSB not found in new data — check team name in ROU.csv")


if __name__ == "__main__":
    main()
