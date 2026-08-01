"""
Verify Forebet's OU 2.5 pick_odds against odds we actually captured in our
own odds_snapshots.

The 2026-08-01 head-to-head audit surfaced Forebet at +30.20% ROI on n=806
Over/Under 2.5 picks — outsized enough that we suspect their published
pick_odds may be cherry-picked (best-book quote on winners rather than the
kickoff-time market price a real bettor could take).

This script:
  1. Loads Forebet OU picks from ledger/picks_forebet.csv.
  2. Fuzzy-matches each Forebet pick to a match in our `matches` table
     using (home_team, away_team, kickoff_date). rapidfuzz token_set_ratio
     ≥ 88 on the concatenated fixture string.
  3. For each successful match, pulls the FULL set of `odds_snapshots` we
     stored for that match × `market='over_under_25'` × the matching
     selection (over/under). Computes:
       - max_odds_any_book    : the highest odds we ever saw at any book
       - median_odds_any_book : median across all snapshots
       - closing_odds_pinnacle: last snapshot before kickoff at Pinnacle
       - closing_odds_any     : last snapshot before kickoff at any book
  4. Emits ledger/forebet_ou_verify.csv with one row per matched pick +
     side-by-side (forebet_odds, our_max, our_median, pinnacle_close),
     plus computed deltas.
  5. Prints a summary: on the matched subset, does Forebet's claimed
     +30.20% ROI hold if we replace their pick_odds with Pinnacle-close?

Usage:
    python3 scripts/verify_forebet_ou_odds.py
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

PICKS_CSV = ROOT / "ledger" / "picks_forebet.csv"
OUT_CSV = ROOT / "ledger" / "forebet_ou_verify.csv"

# Fuzzy match threshold on (home + " vs " + away)
FIXTURE_MATCH_THRESHOLD = 88


def load_forebet_ou_picks() -> list[dict]:
    with PICKS_CSV.open() as fh:
        rows = [r for r in csv.DictReader(fh)
                 if r["market"] == "over_under_25" and r["home_team"] and r["away_team"]]
    print(f"Loaded {len(rows)} Forebet OU 2.5 picks with team names")
    return rows


def load_candidate_matches(dates: set[str]) -> list[dict]:
    """Pull `matches` rows on the same dates as any Forebet OU pick."""
    if not dates:
        return []
    sorted_dates = sorted(dates)
    start = sorted_dates[0]
    end = (datetime.fromisoformat(sorted_dates[-1]) + timedelta(days=1)).date().isoformat()
    rows = execute_query(
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
          AND m.status IN ('finished')
        """,
        (start, end),
    )
    print(f"Loaded {len(rows)} candidate matches from our DB "
          f"across {start} → {end}")
    return rows


def fuzzy_match_to_match(fb_row: dict, candidates_by_date: dict[str, list[dict]]) -> dict | None:
    date = fb_row["kickoff_date"]
    home = (fb_row["home_team"] or "").lower()
    away = (fb_row["away_team"] or "").lower()
    if not date or not home or not away:
        return None
    target = f"{home} vs {away}"
    best_score = 0
    best_match: dict | None = None
    for m in candidates_by_date.get(date, []):
        cand = f"{(m['home_team'] or '').lower()} vs {(m['away_team'] or '').lower()}"
        s = fuzz.token_set_ratio(target, cand)
        if s > best_score:
            best_score = s
            best_match = m
    if best_score >= FIXTURE_MATCH_THRESHOLD:
        return best_match
    return None


def load_ou_snapshots_for_matches(match_ids: list[str]) -> dict[str, list[dict]]:
    """Return match_id → list of (bookmaker, selection, odds, timestamp)."""
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT match_id, bookmaker, selection, odds::float, timestamp
        FROM odds_snapshots
        WHERE match_id = ANY(%s::uuid[])
          AND market = 'over_under_25'
          AND selection IN ('over','under')
        """,
        (match_ids,),
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["match_id"]), []).append(r)
    return out


def summarize_snapshots(snapshots: list[dict], selection: str) -> dict:
    """Return summary odds stats for a single (match, selection)."""
    matching = [s for s in snapshots if s["selection"] == selection]
    if not matching:
        return {}
    all_odds = [float(s["odds"]) for s in matching]
    matching_sorted = sorted(matching, key=lambda s: s["timestamp"])
    # Pinnacle-close: last Pinnacle snapshot (any timestamp)
    pinnacle = [s for s in matching if (s["bookmaker"] or "").lower() == "pinnacle"]
    pinnacle_sorted = sorted(pinnacle, key=lambda s: s["timestamp"])
    return {
        "our_odds_max": max(all_odds),
        "our_odds_median": sorted(all_odds)[len(all_odds) // 2],
        "our_odds_last_any_book": float(matching_sorted[-1]["odds"]),
        "our_odds_pinnacle_close": (float(pinnacle_sorted[-1]["odds"])
                                     if pinnacle_sorted else None),
        "snapshot_count": len(matching),
        "book_count": len({s["bookmaker"] for s in matching}),
    }


def main() -> int:
    fb_picks = load_forebet_ou_picks()
    dates = {r["kickoff_date"] for r in fb_picks}

    candidates = load_candidate_matches(dates)
    by_date: dict[str, list[dict]] = {}
    for m in candidates:
        d = m["date"].date().isoformat() if hasattr(m["date"], "date") else str(m["date"])[:10]
        by_date.setdefault(d, []).append(m)

    matched_rows: list[dict] = []
    unmatched = 0
    for r in fb_picks:
        m = fuzzy_match_to_match(r, by_date)
        if m is None:
            unmatched += 1
            continue
        matched_rows.append({
            "forebet": r,
            "match": m,
        })
    print(f"Fuzzy-matched {len(matched_rows)} / {len(fb_picks)} Forebet OU "
          f"picks to matches in our DB ({unmatched} unmatched)")

    if not matched_rows:
        print("No matches — bailing.")
        return 0

    match_ids = [str(mr["match"]["id"]) for mr in matched_rows]
    snapshots = load_ou_snapshots_for_matches(match_ids)
    print(f"Pulled odds_snapshots for {len(snapshots)} / {len(match_ids)} "
          "matched fixtures.")

    out_rows = []
    delta_pinnacle_pos = 0   # Forebet HIGHER than Pinnacle-close (suspect)
    delta_pinnacle_neg = 0
    delta_max_pos = 0        # Forebet HIGHER than our best book (very suspect)
    replay_pnl_forebet = 0.0
    replay_pnl_pinnacle = 0.0
    replay_pnl_max = 0.0
    replay_n = 0
    for mr in matched_rows:
        fb = mr["forebet"]
        m = mr["match"]
        selection = (fb["pick"] or "").strip().lower()
        if selection not in ("over", "under"):
            continue
        fb_odds = float(fb["odds"]) if fb["odds"] else None
        if fb_odds is None:
            continue
        stats = summarize_snapshots(snapshots.get(str(m["id"]), []), selection)
        if not stats:
            continue
        # Verify Forebet's "won" status against our score
        sh, sa = m.get("score_home"), m.get("score_away")
        actual_total = (sh + sa) if sh is not None and sa is not None else None
        if actual_total is None:
            continue
        picked_over = selection == "over"
        won_selection = (actual_total >= 3) if picked_over else (actual_total <= 2)
        # (Forebet marks push as tie in our fetch but 2.5 line never pushes)

        row = {
            "kickoff_date": fb["kickoff_date"],
            "home_team": fb["home_team"],
            "away_team": fb["away_team"],
            "league_forebet": fb.get("league"),
            "league_ours": m.get("league"),
            "selection": selection,
            "final_score": f"{sh}-{sa}",
            "actually_won": won_selection,
            "forebet_says_won": fb["result"] == "won",
            "forebet_odds": fb_odds,
            "our_odds_median": stats.get("our_odds_median"),
            "our_odds_max": stats.get("our_odds_max"),
            "our_odds_last_any_book": stats.get("our_odds_last_any_book"),
            "our_odds_pinnacle_close": stats.get("our_odds_pinnacle_close"),
            "snapshot_count": stats.get("snapshot_count"),
            "book_count": stats.get("book_count"),
            "delta_fb_vs_pinnacle": (round(fb_odds - stats["our_odds_pinnacle_close"], 3)
                                      if stats.get("our_odds_pinnacle_close") else ""),
            "delta_fb_vs_our_max": round(fb_odds - stats["our_odds_max"], 3),
        }
        out_rows.append(row)
        if stats.get("our_odds_pinnacle_close"):
            if fb_odds > stats["our_odds_pinnacle_close"]:
                delta_pinnacle_pos += 1
            elif fb_odds < stats["our_odds_pinnacle_close"]:
                delta_pinnacle_neg += 1
        if fb_odds > stats["our_odds_max"]:
            delta_max_pos += 1

        # Replay ROI on 1-unit stake — Forebet claims vs Pinnacle-close vs max
        replay_n += 1
        replay_pnl_forebet += (fb_odds - 1) if won_selection else -1
        if stats.get("our_odds_pinnacle_close"):
            replay_pnl_pinnacle += (stats["our_odds_pinnacle_close"] - 1) if won_selection else -1
        else:
            replay_pnl_pinnacle += (stats["our_odds_last_any_book"] - 1) if won_selection else -1
        replay_pnl_max += (stats["our_odds_max"] - 1) if won_selection else -1

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with OUT_CSV.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"Wrote {len(out_rows)} rows to {OUT_CSV}")

    print()
    print("=" * 72)
    print("VERDICT — on the fixture-matched subset:")
    print("=" * 72)
    print(f"  fixtures compared: {len(out_rows)}")
    print(f"  Forebet odds HIGHER than Pinnacle close: {delta_pinnacle_pos}")
    print(f"  Forebet odds LOWER than Pinnacle close:  {delta_pinnacle_neg}")
    print(f"  Forebet odds HIGHER than our best-book max: {delta_max_pos}"
          " (odds beyond ANY reachable book — mathematically suspect)")
    print()
    print("Replay ROI on same picks, replacing Forebet's stated odds:")
    if replay_n:
        print(f"  Forebet stated odds:       {100.0*replay_pnl_forebet/replay_n:+.2f}% "
              f"(n={replay_n})")
        print(f"  Our Pinnacle close (or best last snapshot if no Pinnacle): "
              f"{100.0*replay_pnl_pinnacle/replay_n:+.2f}%")
        print(f"  Best-price across every book we saw: "
              f"{100.0*replay_pnl_max/replay_n:+.2f}% (upper-bound theoretical)")

    # Discrepancy check — did Forebet mark WON/LOST rows the same as actual score?
    result_mismatches = [r for r in out_rows
                          if r["forebet_says_won"] != r["actually_won"]]
    if result_mismatches:
        print(f"\n⚠  {len(result_mismatches)} rows where Forebet's WON/LOST flag "
              "disagrees with the actual final score — sample of 5:")
        for r in result_mismatches[:5]:
            print(f"    {r['kickoff_date']} {r['home_team']} vs {r['away_team']} "
                  f"{r['selection']} → score {r['final_score']} "
                  f"(actually_won={r['actually_won']} vs "
                  f"forebet_says_won={r['forebet_says_won']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
