"""
ingest_football_data_extras.py — LEVER-1 (TIER-C-EXPAND).

Generalisation of `scripts/add_romanian_league_data.py`. Downloads
historical CSVs from football-data.co.uk's "new" extras directory and
appends rows to `data/processed/targets_poisson_history.csv`, moving the
covered leagues from Tier C → Tier A in the live pipeline (closing odds
present → meets Tier A criteria).

Why this exists: 2026-05-19 had 124 fixtures but only 2 pre-match bets
because 87% of the slate was Tier C (no historical CSV for either team).
TIER-C-AF-XG already unlocked OU/BTTS/AH for Tier C, but those bets still
carry the +8% Tier C edge bump. Adding football-data history moves the
top divisions of these countries to Tier A → no edge bump, form-based
xG instead of AF's noisier xG, and the XGBoost ensemble can fire.

Pattern: for each league code, fetch `new/<CODE>.csv`, map columns to the
targets_poisson_history schema, dedupe by Date+teams, append.

Run:
  python3 scripts/ingest_football_data_extras.py                # full batch
  python3 scripts/ingest_football_data_extras.py --league USA   # one league
  python3 scripts/ingest_football_data_extras.py --dry-run      # no writes

Important: this script writes to a tracked CSV. Commit the resulting
`data/processed/targets_poisson_history.csv` after a successful run so the
production pipeline picks it up. Idempotent — re-running is a no-op once
the league's most recent season is in the file.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys
import urllib.request
from typing import Optional

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "processed" / "targets_poisson_history.csv"
BASE_URL = "https://www.football-data.co.uk/new/{code}.csv"

# Per-league config.
#   code      — football-data 3-letter file code (https://www.football-data.co.uk/new/<code>.csv)
#   league    — pretty name written to the `league` column of targets_poisson_history
#   league_code — short code written to the `league_code` column (kept ≤4 chars for
#                consistency with existing codes like E0/RO1/SC0). Conflicts with an
#                existing league_code MUST be avoided — see the assertion in load_config().
#   tier      — 1 for top division. Lower divisions only ship when football-data
#                publishes a separate CSV (e.g. England has E0/E1/E2/E3); for the
#                "new" extras, top division only is the rule.
#   keep_n_seasons — how many of the most recent seasons to ingest. 5 mirrors the
#                Romanian script. Older seasons can drift in team identity (promotions,
#                rebrands) so we deliberately don't ingest the full back catalog.
LEAGUES: dict[str, dict] = {
    "ARG": {"league": "Liga Profesional",     "league_code": "AR1", "tier": 1, "keep_n_seasons": 5},
    "AUT": {"league": "Bundesliga (Austria)", "league_code": "AT1", "tier": 1, "keep_n_seasons": 5},
    "BRA": {"league": "Brasileirão Série A",  "league_code": "BR1", "tier": 1, "keep_n_seasons": 5},
    "CHN": {"league": "Super League",         "league_code": "CN1", "tier": 1, "keep_n_seasons": 5},
    "DNK": {"league": "Superliga",            "league_code": "DK1", "tier": 1, "keep_n_seasons": 5},
    "FIN": {"league": "Veikkausliiga",        "league_code": "FI1", "tier": 1, "keep_n_seasons": 5},
    "IRL": {"league": "Premier Division",     "league_code": "IE1", "tier": 1, "keep_n_seasons": 5},
    "JPN": {"league": "J1 League",            "league_code": "JP1", "tier": 1, "keep_n_seasons": 5},
    "MEX": {"league": "Liga MX",              "league_code": "MX1", "tier": 1, "keep_n_seasons": 5},
    "NOR": {"league": "Eliteserien",          "league_code": "NO1", "tier": 1, "keep_n_seasons": 5},
    "POL": {"league": "Ekstraklasa",          "league_code": "PL1", "tier": 1, "keep_n_seasons": 5},
    "RUS": {"league": "Premier League (RU)",  "league_code": "RU1", "tier": 1, "keep_n_seasons": 5},
    "SWE": {"league": "Allsvenskan",          "league_code": "SE1", "tier": 1, "keep_n_seasons": 5},
    "CHE": {"league": "Super League (CH)",    "league_code": "CH1", "tier": 1, "keep_n_seasons": 5},
    "USA": {"league": "Major League Soccer",  "league_code": "US1", "tier": 1, "keep_n_seasons": 5},
}


def load_config() -> dict[str, dict]:
    """Sanity-check: no two leagues share the same league_code (would break
    league_code-based slicing in downstream analysis scripts)."""
    seen: set[str] = set()
    for code, cfg in LEAGUES.items():
        lc = cfg["league_code"]
        assert lc not in seen, f"Duplicate league_code {lc!r} in LEAGUES — pick unique codes"
        seen.add(lc)
    return LEAGUES


def download(code: str) -> pd.DataFrame:
    """Fetch and parse the football-data CSV for one country code."""
    url = BASE_URL.format(code=code)
    print(f"  Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), low_memory=False)
    print(f"  Downloaded {len(df):,} rows, {len(df.columns)} columns")
    return df


def parse_season(s: str) -> str:
    """'2024/2025' → '2024-25', '2024' → '2024' (calendar-year leagues)."""
    s = str(s).strip()
    if "/" in s:
        a, b = s.split("/", 1)
        return f"{a}-{b[2:]}" if len(b) >= 2 else s
    return s


def convert_date(d) -> str:
    try:
        return pd.to_datetime(str(d), dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return str(d)


def build_targets_rows(raw: pd.DataFrame, code: str, cfg: dict) -> pd.DataFrame:
    """Map a football-data extras CSV to the targets_poisson_history schema."""
    if "Season" not in raw.columns:
        print(f"  [{code}] WARNING: no 'Season' column — skipping")
        return pd.DataFrame()

    seasons = sorted(raw["Season"].dropna().astype(str).unique())
    keep = set(seasons[-cfg["keep_n_seasons"]:])
    df = raw[raw["Season"].astype(str).isin(keep)].copy()
    print(f"  [{code}] Filtered to {len(df):,} rows across seasons: {sorted(keep)}")

    # Require Pinnacle closing odds (PSCH/D/A). For older USA seasons this may
    # drop a lot of rows — that's fine; we'd rather have calibrated odds than
    # uncalibrated form-only data when targets_poisson_history is meant to be
    # the Tier A (full-calibration) source.
    if "PSCH" not in df.columns:
        print(f"  [{code}] WARNING: PSCH column missing — skipping league")
        return pd.DataFrame()

    before = len(df)
    df = df.dropna(subset=["PSCH"])
    print(f"  [{code}] Dropped {before - len(df)} rows without Pinnacle closing odds → {len(df):,} remaining")

    # Probe for required columns. Football-data "new" extras uses Home/Away/HG/AG/Res
    # consistently (unlike the mainstream files which use HomeTeam/AwayTeam/FTHG/FTAG/FTR).
    needed = ["Home", "Away", "HG", "AG", "Res", "Date"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"  [{code}] WARNING: missing required columns {missing} — skipping league")
        return pd.DataFrame()

    rows: list[dict] = []
    for _, r in df.iterrows():
        try:
            fthg = int(r["HG"])
            ftag = int(r["AG"])
        except (ValueError, TypeError):
            continue
        total = fthg + ftag
        rows.append({
            "Date": convert_date(r["Date"]),
            "home_team": str(r["Home"]).strip(),
            "away_team": str(r["Away"]).strip(),
            "result": str(r.get("Res", "")).strip(),
            "FTHG": fthg,
            "FTAG": ftag,
            "total_goals": total,
            "over_25": 1 if total > 2.5 else 0,
            "btts": 1 if fthg > 0 and ftag > 0 else 0,
            "league_code": cfg["league_code"],
            "league": cfg["league"],
            "season": parse_season(r.get("Season", "")),
            "tier": cfg["tier"],
            # Closing-line odds (averages and bookmaker-specific). football-data uses
            # AvgC*/PSC*/B365C* in the new extras CSVs.
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
        })
    out = pd.DataFrame(rows)
    print(f"  [{code}] Built {len(out):,} valid rows")
    return out


def ingest_one(code: str, cfg: dict, existing: pd.DataFrame, dry_run: bool) -> Optional[pd.DataFrame]:
    """Returns the new rows for one league (or None on failure)."""
    print(f"\n=== {code} — {cfg['league']} (league_code={cfg['league_code']}, tier={cfg['tier']}) ===")
    try:
        raw = download(code)
    except Exception as e:
        print(f"  [{code}] DOWNLOAD FAILED: {type(e).__name__}: {e}")
        return None

    new_rows = build_targets_rows(raw, code, cfg)
    if new_rows.empty:
        return None

    if cfg["league_code"] in existing["league_code"].values:
        ex_count = (existing["league_code"] == cfg["league_code"]).sum()
        print(f"  [{code}] {ex_count} existing rows for league_code={cfg['league_code']} — will dedupe")

    teams = sorted(set(new_rows["home_team"].unique()) | set(new_rows["away_team"].unique()))
    print(f"  [{code}] {len(teams)} unique teams. Sample: {teams[:8]}")
    return new_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="Single 3-letter league code (e.g. USA, ARG). Default = all configured leagues.")
    ap.add_argument("--dry-run", action="store_true", help="Skip the final CSV write")
    args = ap.parse_args()

    cfg_map = load_config()
    if args.league:
        if args.league not in cfg_map:
            print(f"ERROR: --league {args.league} not in config. Available: {sorted(cfg_map)}")
            sys.exit(2)
        codes = [args.league]
    else:
        codes = sorted(cfg_map)
        print(f"Running full batch: {codes}")

    if not HISTORY_PATH.exists():
        print(f"ERROR: {HISTORY_PATH} not found — Tier A history CSV must already exist")
        sys.exit(1)

    existing = pd.read_csv(HISTORY_PATH, low_memory=False)
    print(f"\ntargets_poisson_history.csv currently: {len(existing):,} rows, "
          f"{existing['league_code'].nunique()} league codes")

    pieces: list[pd.DataFrame] = []
    for code in codes:
        piece = ingest_one(code, cfg_map[code], existing, args.dry_run)
        if piece is not None and not piece.empty:
            pieces.append(piece)

    if not pieces:
        print("\nNo rows to add — exiting.")
        return

    all_new = pd.concat(pieces, ignore_index=True)
    combined = pd.concat([existing, all_new], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["Date", "home_team", "away_team"], keep="first")
    print(f"\nAfter dedup: {before} → {len(combined)} rows ({before - len(combined)} duplicates removed)")

    combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
    combined = combined.sort_values("Date")
    combined["Date"] = combined["Date"].dt.strftime("%Y-%m-%d")

    if args.dry_run:
        print(f"\n[DRY RUN] Would write {len(combined):,} rows to {HISTORY_PATH}")
        return

    combined.to_csv(HISTORY_PATH, index=False)
    print(f"\nWritten {len(combined):,} rows to {HISTORY_PATH}")
    added = len(combined) - len(existing)
    print(f"Net new rows: {added}")
    print(f"League codes now in file: {sorted(combined['league_code'].dropna().unique())}")


if __name__ == "__main__":
    main()
