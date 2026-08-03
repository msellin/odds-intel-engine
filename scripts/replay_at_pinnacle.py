"""
Replay every source's picks at Pinnacle-close odds — same basis for all.

Motivation: FOREBET-OU-VERIFY-2026-08-01 showed Forebet publishes odds that
were never reachable in the market (57% of their OU winners quote odds
higher than the best-book max we ever saw; median inflation +0.36 vs
Pinnacle close). Their published +12.91% headline is largely an artifact
of inflated odds on winning picks, not a real model edge.

If we settle EVERY source's picks at the same Pinnacle-close price we
captured in our own odds_snapshots, every ROI is apples-to-apples and no
one can win by cherry-picking odds. This is the cleanest possible
head-to-head basis.

Constraints:
  - Requires per-pick team names to fuzzy-join. So this covers Forebet,
    SignalOdds, and OddsIntel. DeepBetting, Tipstrr and WinnerOdds don't
    publish per-pick team names in their public feeds — they stay on
    their as-published aggregate for now.
  - Requires a Pinnacle snapshot for the relevant (match, market, selection)
    tuple. Fixtures without Pinnacle coverage are dropped from the
    replay stats but kept in the CSV with `dropped_reason` for
    auditability.

Output:
  ledger/pinnacle_replay.csv      — one row per (source × pick) with
                                     stated odds vs pinnacle_close side by side
  ledger/pinnacle_replay_summary.json — per-source aggregate ROI at
                                        Pinnacle-close vs as-published

Usage:
  python3 scripts/replay_at_pinnacle.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

LEDGER = ROOT / "ledger"
OUT_CSV = LEDGER / "pinnacle_replay.csv"
OUT_JSON = LEDGER / "pinnacle_replay_summary.json"

JOINABLE_SOURCES = ("oddsintel", "forebet", "signalodds")

# Fuzzy match threshold on "home vs away"
FIXTURE_MATCH_THRESHOLD = 88

# Per-source pick vocabulary → canonical (market, selection)
def canonicalize(source: str, market: str, pick: str) -> tuple[str, str] | None:
    m = (market or "").strip().lower().replace(" ", "_")
    p = (pick or "").strip().lower()
    if source == "oddsintel":
        if m == "1x2":
            if p in ("home", "1"): return ("1x2", "home")
            if p in ("draw", "x"): return ("1x2", "draw")
            if p in ("away", "2"): return ("1x2", "away")
        if m in ("over_under_25", "o/u"):
            if "over" in p: return ("over_under_25", "over")
            if "under" in p: return ("over_under_25", "under")
    elif source == "forebet":
        if m == "1x2":
            if p == "1": return ("1x2", "home")
            if p == "x": return ("1x2", "draw")
            if p == "2": return ("1x2", "away")
        if m == "over_under_25":
            if p == "over": return ("over_under_25", "over")
            if p == "under": return ("over_under_25", "under")
    elif source == "signalodds":
        if m in ("match_result", "1x2"):
            # SignalOdds picks look like "TeamName Win"; we need to look
            # at the actual selection team relative to home/away. Caller
            # handles this outside canonicalize().
            return None
    return None


def signalodds_pick_to_1x2(pick: str, home: str, away: str) -> str | None:
    """SignalOdds writes 'TeamName Win' / 'Draw' — resolve to home/draw/away."""
    p = (pick or "").strip()
    if p.lower() == "draw":
        return "draw"
    if p.lower().endswith(" win"):
        team = p[:-4].strip().lower()
        h = (home or "").strip().lower()
        a = (away or "").strip().lower()
        # Prefer exact, fall back to fuzzy
        if team == h or fuzz.token_set_ratio(team, h) >= 90:
            return "home"
        if team == a or fuzz.token_set_ratio(team, a) >= 90:
            return "away"
    return None


def load_source(name: str) -> list[dict]:
    p = LEDGER / f"picks_{name}.csv"
    if not p.exists():
        return []
    with p.open() as fh:
        return [r for r in csv.DictReader(fh) if r["home_team"] and r["away_team"] and r["kickoff_date"]]


def load_candidate_matches(dates: set[str]) -> list[dict]:
    if not dates:
        return []
    sd = sorted(dates)
    start = sd[0]
    end = (datetime.fromisoformat(sd[-1]) + timedelta(days=1)).date().isoformat()
    return execute_query(
        """
        SELECT
          m.id, m.date, ht.name AS home_team, at.name AS away_team,
          m.score_home, m.score_away, l.name AS league
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE m.date >= %s::date
          AND m.date <  %s::date
          AND m.status = 'finished'
        """,
        (start, end),
    )


def index_by_date(matches: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        d = m["date"].date().isoformat() if hasattr(m["date"], "date") else str(m["date"])[:10]
        out[d].append(m)
    return out


def match_fixture(pick_row: dict, by_date: dict[str, list[dict]]) -> dict | None:
    date = pick_row["kickoff_date"]
    home = (pick_row["home_team"] or "").lower()
    away = (pick_row["away_team"] or "").lower()
    if not (date and home and away):
        return None
    target = f"{home} vs {away}"
    best_score = 0
    best: dict | None = None
    for m in by_date.get(date, []):
        cand = f"{(m['home_team'] or '').lower()} vs {(m['away_team'] or '').lower()}"
        s = fuzz.token_set_ratio(target, cand)
        if s > best_score:
            best_score = s
            best = m
    return best if best_score >= FIXTURE_MATCH_THRESHOLD else None


def load_pinnacle_closes(match_ids: list[str]) -> dict[tuple[str, str, str], float]:
    """Return {(match_id, market, selection): pinnacle_close_odds}."""
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT DISTINCT ON (match_id, market, selection)
          match_id, market, selection, odds::float, timestamp
        FROM odds_snapshots
        WHERE match_id = ANY(%s::uuid[])
          AND market IN ('1x2','over_under_25')
          AND selection IN ('home','draw','away','over','under')
          AND LOWER(bookmaker) = 'pinnacle'
        ORDER BY match_id, market, selection, timestamp DESC
        """,
        (match_ids,),
    )
    return {(str(r["match_id"]), r["market"], r["selection"]): float(r["odds"]) for r in rows}


def settle_1x2(selection: str, score_home: int, score_away: int) -> bool:
    if selection == "home": return score_home > score_away
    if selection == "draw": return score_home == score_away
    if selection == "away": return score_home < score_away
    return False


def settle_ou(selection: str, score_home: int, score_away: int) -> bool | None:
    total = score_home + score_away
    if selection == "over":  return True if total >= 3 else False
    if selection == "under": return True if total <= 2 else False
    return None


def main() -> int:
    all_picks_by_source = {s: load_source(s) for s in JOINABLE_SOURCES}
    for s, rows in all_picks_by_source.items():
        print(f"loaded {len(rows):>5} rows for {s}")

    dates: set[str] = set()
    for rows in all_picks_by_source.values():
        for r in rows:
            dates.add(r["kickoff_date"])
    matches = load_candidate_matches(dates)
    by_date = index_by_date(matches)
    print(f"loaded {len(matches)} finished matches across {len(dates)} dates from our DB")

    # For each source, fuzzy-match every pick to a match
    matched: dict[str, list[tuple[dict, dict, tuple[str, str]]]] = {}
    for source, rows in all_picks_by_source.items():
        matched[source] = []
        for r in rows:
            m = match_fixture(r, by_date)
            if not m:
                continue
            if source == "signalodds":
                sel = signalodds_pick_to_1x2(r["pick"], m["home_team"], m["away_team"])
                if sel is None:
                    continue
                canon = ("1x2", sel)
            else:
                canon = canonicalize(source, r["market"], r["pick"])
                if canon is None:
                    continue
            matched[source].append((r, m, canon))
        print(f"  {source}: fuzzy-matched {len(matched[source]):>5} / {len(rows)} picks")

    all_match_ids = list({str(m["id"]) for lst in matched.values() for (_, m, _) in lst})
    print(f"unique fixtures across all sources: {len(all_match_ids)}")
    pinnacle_closes = load_pinnacle_closes(all_match_ids)
    print(f"loaded {len(pinnacle_closes)} Pinnacle-close snapshots")

    out_rows: list[dict] = []
    summary: dict[str, dict] = {}
    for source, hits in matched.items():
        replay_pnl_pin = 0.0
        replay_pnl_pub = 0.0
        replay_n = 0
        won_at_score = 0
        dropped_no_pinnacle = 0
        dropped_no_score = 0
        dropped_no_odds = 0
        for pick_row, match_row, (market, selection) in hits:
            sh, sa = match_row["score_home"], match_row["score_away"]
            if sh is None or sa is None:
                dropped_no_score += 1
                continue
            if market == "1x2":
                won = settle_1x2(selection, sh, sa)
            else:
                won = settle_ou(selection, sh, sa)
            if won is None:
                dropped_no_score += 1
                continue
            try:
                pub_odds = float(pick_row["odds"]) if pick_row["odds"] else None
            except ValueError:
                pub_odds = None
            pin = pinnacle_closes.get((str(match_row["id"]), market, selection))
            row = {
                "source": source,
                "kickoff_date": pick_row["kickoff_date"],
                "home_team": match_row["home_team"],
                "away_team": match_row["away_team"],
                "league": match_row.get("league") or "",
                "market": market,
                "selection": selection,
                "final_score": f"{sh}-{sa}",
                "won": won,
                "published_odds": pub_odds if pub_odds else "",
                "pinnacle_close_odds": pin if pin else "",
                "pnl_published": (pub_odds - 1 if won else -1) if pub_odds else "",
                "pnl_pinnacle": (pin - 1 if won else -1) if pin else "",
                "dropped_reason": "" if pin else "no_pinnacle_close",
            }
            out_rows.append(row)
            if pin is None:
                dropped_no_pinnacle += 1
                continue
            replay_n += 1
            if won:
                replay_pnl_pin += (pin - 1)
                won_at_score += 1
            else:
                replay_pnl_pin += -1
            if pub_odds is not None:
                replay_pnl_pub += ((pub_odds - 1) if won else -1)
            else:
                dropped_no_odds += 1

        summary[source] = {
            "picks_matched_to_fixture": len(hits),
            "picks_replayed": replay_n,
            "hit_rate_pct": round(100 * won_at_score / replay_n, 2) if replay_n else None,
            "roi_at_pinnacle_close_pct": round(100 * replay_pnl_pin / replay_n, 2) if replay_n else None,
            "roi_at_published_odds_pct": round(100 * replay_pnl_pub / replay_n, 2) if replay_n else None,
            "dropped_no_pinnacle": dropped_no_pinnacle,
            "dropped_no_score": dropped_no_score,
        }

    # Write outputs
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()) if out_rows else [
            "source","kickoff_date","home_team","away_team","league","market",
            "selection","final_score","won","published_odds","pinnacle_close_odds",
            "pnl_published","pnl_pinnacle","dropped_reason"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nwrote {len(out_rows)} rows to {OUT_CSV}")

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "notes": (
            "Replays each source's fuzzy-matched picks at the Pinnacle-close "
            "odds we captured in our own odds_snapshots. Same basis for all. "
            "Sources without per-pick team names (DeepBetting, Tipstrr, "
            "WinnerOdds public feed) are not included."
        ),
        "sources": summary,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"wrote summary to {OUT_JSON}\n")

    print("=" * 78)
    print("APPLES-TO-APPLES ROI — every source settled at OUR Pinnacle-close odds")
    print("=" * 78)
    for s, v in summary.items():
        print(f"  {s:12s}  n={v['picks_replayed']:>4}  "
              f"published_ROI={v['roi_at_published_odds_pct']:>+6.2f}%   "
              f"pinnacle_ROI={v['roi_at_pinnacle_close_pct']:>+6.2f}%   "
              f"hit={v['hit_rate_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
