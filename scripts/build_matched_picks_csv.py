"""
Build ledger/picks_matched.csv — the landing "Verify · view raw picks" CSV.

Joins per-source per-pick rows from:
    ledger/picks_oddsintel.csv    (536 rows on last audit)
    ledger/picks_forebet.csv      (1832 rows — 1x2 + OU 2.5)
    ledger/picks_signalodds.csv   (1157 rows — 1x2 only)

Sources dropped from the matrix (still exposed via per-source CSVs):
    ledger/picks_deepbetting.csv  — no per-pick team names on the DB endpoint
    ledger/picks_winnerodds.csv   — GraphQL feed exposes country only, no teams
    ledger/picks_tipstrr.csv      — monthly aggregate, per-pick paywalled

Row schema (one row per fixture × market):
    kickoff_date, home_team, away_team, market,
    oddsintel_pick,  oddsintel_odds,  oddsintel_result,
    forebet_pick,    forebet_odds,    forebet_result,
    signalodds_pick, signalodds_odds, signalodds_result

Team name matching uses rapidfuzz token-set ratio ≥ 85 on the (home, away)
concatenation within the same kickoff_date. Same-day fuzzy join tolerates
"Real Madrid" vs "Real Madrid CF" vs "R. Madrid" without needing an
authoritative fixture-id lookup table.

Usage:
    python3 scripts/build_matched_picks_csv.py

Emits stats about how many picks per source were matched vs unmatched — an
audit trail of the join quality.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    print("FATAL: rapidfuzz not installed. pip install rapidfuzz", flush=True)
    raise

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
OUT_PATH = LEDGER / "picks_matched.csv"

# Sources we can fuzzy-join because they publish per-pick teams
JOINABLE = ("oddsintel", "forebet", "signalodds")

# Sources whose per-pick CSV exists but can't join to fixtures. Users hit
# their per-source CSVs directly.
UNJOINABLE = ("deepbetting", "winnerodds", "tipstrr")


def load_source(name: str) -> list[dict]:
    p = LEDGER / f"picks_{name}.csv"
    if not p.exists():
        print(f"  warn: {p.name} missing — skipping")
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def norm_team(s: str) -> str:
    """Best-effort normalisation for fuzzy match."""
    return (s or "").strip().lower()


def norm_market(s: str) -> str:
    m = (s or "").strip().lower().replace(" ", "_")
    if m in ("o/u", "ou", "over_under_25", "over_under25", "over/under_2.5"):
        return "over_under_25"
    if m in ("1x2", "moneyline", "match_result", "1_x_2"):
        return "1x2"
    return m


def fixture_key_candidates(row: dict) -> tuple[str, str, str, str]:
    """Return (date, home, away, market) with normalisation applied."""
    return (
        (row.get("kickoff_date") or "").strip(),
        norm_team(row.get("home_team")),
        norm_team(row.get("away_team")),
        norm_market(row.get("market")),
    )


def fuzzy_match(target: tuple[str, str], pool: Iterable[tuple[str, str]],
                 threshold: int = 85) -> tuple[str, str] | None:
    """Return best (home, away) match from `pool` for `target` or None.

    Uses token_set_ratio on the "home vs away" concatenation so word order
    doesn't matter (some sources publish "Away @ Home", some "Home vs Away").
    """
    if not target[0] or not target[1]:
        return None
    target_str = f"{target[0]} vs {target[1]}"
    best_score = 0
    best: tuple[str, str] | None = None
    for cand in pool:
        cand_str = f"{cand[0]} vs {cand[1]}"
        s = fuzz.token_set_ratio(target_str, cand_str)
        if s > best_score:
            best_score = s
            best = cand
    if best_score >= threshold:
        return best
    return None


def main() -> int:
    print(f"Building {OUT_PATH.name} from per-source picks CSVs")

    picks = {name: load_source(name) for name in JOINABLE}
    for name, rows in picks.items():
        print(f"  loaded {len(rows):>5} rows for {name}")

    # Index each source by (date, market) → list[(home_norm, away_norm, row)]
    by_date_market: dict[str, dict[tuple[str, str], list[tuple[str, str, dict]]]] = {
        name: defaultdict(list) for name in JOINABLE
    }
    for name, rows in picks.items():
        for r in rows:
            date, home, away, market = fixture_key_candidates(r)
            if not date or not home or not away:
                continue
            by_date_market[name][(date, market)].append((home, away, r))

    # Fixtures are keyed off OddsIntel's canonical rows. Every OddsIntel pick
    # gets a row; competitor columns fill in when a fuzzy match is found on
    # the same (date, market). If no OddsIntel pick exists on a fixture but
    # multiple competitors did, we ALSO emit that fixture as its own row so
    # the CSV covers the union, not just our intersection.
    seen_fixtures: set[tuple[str, str, str, str]] = set()
    out_rows: list[dict] = []

    def find_match(source: str, date: str, market: str,
                   home: str, away: str) -> dict | None:
        pool_index = by_date_market[source].get((date, market))
        if not pool_index:
            return None
        pool_keys = [(h, a) for (h, a, _row) in pool_index]
        match_key = fuzzy_match((home, away), pool_keys)
        if match_key is None:
            return None
        for h, a, row in pool_index:
            if (h, a) == match_key:
                return row
        return None

    match_stats = {name: {"matched": 0, "unmatched": 0} for name in JOINABLE if name != "oddsintel"}

    # Pass 1 — walk OddsIntel picks, join competitor rows to each
    for r_oi in picks["oddsintel"]:
        date, home, away, market = fixture_key_candidates(r_oi)
        if not date or not home or not away:
            continue
        fx_key = (date, home, away, market)
        seen_fixtures.add(fx_key)

        row = _empty_row(date, r_oi.get("home_team"), r_oi.get("away_team"),
                         market, r_oi.get("league"))
        _fill_source(row, "oddsintel", r_oi)

        for competitor in ("forebet", "signalodds"):
            comp_row = find_match(competitor, date, market, home, away)
            if comp_row is not None:
                _fill_source(row, competitor, comp_row)
                match_stats[competitor]["matched"] += 1

        out_rows.append(row)

    # Pass 2 — competitor-only fixtures (no OddsIntel pick on that date+market)
    for competitor in ("forebet", "signalodds"):
        for r_comp in picks[competitor]:
            date, home, away, market = fixture_key_candidates(r_comp)
            if not date or not home or not away:
                continue
            # Was this fixture already covered by an OddsIntel row (fuzzy)?
            already_covered = False
            for (d, h, a, m) in seen_fixtures:
                if d != date or m != market:
                    continue
                if fuzz.token_set_ratio(f"{home} vs {away}", f"{h} vs {a}") >= 85:
                    already_covered = True
                    break
            if already_covered:
                continue

            fx_key = (date, home, away, market)
            seen_fixtures.add(fx_key)
            row = _empty_row(date, r_comp.get("home_team"), r_comp.get("away_team"),
                             market, r_comp.get("league"))
            _fill_source(row, competitor, r_comp)

            # Cross-fill the OTHER competitor if they picked the same fixture
            other = "signalodds" if competitor == "forebet" else "forebet"
            other_row = find_match(other, date, market, home, away)
            if other_row is not None:
                _fill_source(row, other, other_row)
                match_stats[other]["matched"] += 1
            match_stats[competitor]["unmatched"] += 1
            out_rows.append(row)

    print(f"\n  fixtures on the matrix: {len(out_rows)}")
    for k, v in match_stats.items():
        total = v["matched"] + v["unmatched"]
        pct = (100.0 * v["matched"] / total) if total else 0.0
        print(f"    {k}: {v['matched']} matched to an OddsIntel fixture, "
              f"{v['unmatched']} competitor-only ({pct:.1f}% overlap)")

    _write(out_rows)
    return 0


COLUMNS = [
    "kickoff_date", "league", "home_team", "away_team", "market",
    "oddsintel_pick", "oddsintel_odds", "oddsintel_result",
    "forebet_pick", "forebet_odds", "forebet_result",
    "signalodds_pick", "signalodds_odds", "signalodds_result",
]


def _empty_row(date: str, home: str | None, away: str | None,
               market: str, league: str | None) -> dict:
    return {
        "kickoff_date": date,
        "league": league or "",
        "home_team": home or "",
        "away_team": away or "",
        "market": market,
        "oddsintel_pick": "", "oddsintel_odds": "", "oddsintel_result": "",
        "forebet_pick": "", "forebet_odds": "", "forebet_result": "",
        "signalodds_pick": "", "signalodds_odds": "", "signalodds_result": "",
    }


def _fill_source(row: dict, source: str, pick_row: dict) -> None:
    row[f"{source}_pick"] = pick_row.get("pick") or ""
    row[f"{source}_odds"] = pick_row.get("odds") or ""
    row[f"{source}_result"] = pick_row.get("result") or ""


def _write(rows: list[dict]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Sort by date desc for readability — recent fixtures first
    rows.sort(key=lambda r: (r.get("kickoff_date", ""), r.get("home_team", "")),
              reverse=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    raise SystemExit(main())
