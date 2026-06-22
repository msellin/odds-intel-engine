#!/usr/bin/env python3
"""
CS2 settlement from supplementary sources — one-shot tool.

PURPOSE: settle cs2_simulated_bets rows whose matches never landed in
cs2_results (because bo3.gg was failing). Reads from cs2_hltv_matches
and cs2_pandascore_matches, applies a confidence-scored match heuristic,
and OPTIONALLY writes to cs2_results + closes the bet via cs2_bot --settle.

This is NOT cron-wired — it's the Phase-1 prototype of the eventual
settlement refactor (see CS2-PIPELINE-TRUTHFUL-LOGGING followups). The
caller reviews dry-run output and runs --apply only after sanity check.

Heuristics (in order, first hit wins):
  1. HLTV exact team-pair within an id window inferred from kickoff_time.
     hltv_match_id is monotonic (~10k IDs/year), so 2026-06-10 → 2026-06-12
     fall in the [2,390,000, 2,400,000] band empirically.
     Why id-window vs match_date: cs2_hltv_matches.match_date is corrupted
     on 26k/28k rows (fetched_at was bulk-backfilled into it per the
     2026-06-13 ELO-PHASE-2 commit).
  2. PandaScore exact team-pair within ±6h of kickoff_time. PandaScore
     has reliable begin_at but result-update lag — many rows stay at
     status='not_started' even days after the match.
  3. (Not implemented yet) HLTV match-details queue: if the match is
     queued but not yet processed, defer settlement.

Usage:
    python3 scripts/esports/cs2_settle_from_supplementary.py            # dry-run
    python3 scripts/esports/cs2_settle_from_supplementary.py --apply    # write
    python3 scripts/esports/cs2_settle_from_supplementary.py --bo3gg-id 121927  # single match

CS2-PIPELINE-TRUTHFUL-LOGGING followup, 2026-06-22.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402


# Empirical anchors (verified against known matches 2026-06-09 → 2026-06-12):
#   FUT v G2 @ IEM Cologne Major 2026 (KO 2026-06-12) → hltv_id 2394887
#   GenOne v Virtus.pro (KO 2026-06-12)               → hltv_id 2394811
#   PARIVISION v Monte @ ESL Pro League S23 S1        → hltv_id 2391035
# ~10k IDs per year of growth (per ELO-PHASE-2 commit calibration).
# We use a 10k-id window per kickoff_time to find the most-recent match.
HLTV_ID_WINDOW = 10_000


def _hltv_id_window_for_kickoff(ko: datetime) -> tuple[int, int]:
    """Infer a plausible hltv_match_id band for matches kicking off at `ko`.

    Anchored at (2026-06-12, ~2,395,000). Window is asymmetric — lower
    bound is permissive (results often have older IDs from earlier in the
    tournament), upper bound is recent IDs only.
    """
    anchor_date = datetime(2026, 6, 12, tzinfo=timezone.utc)
    anchor_id = 2_395_000
    days_offset = (ko.date() - anchor_date.date()).days
    center = anchor_id + int(days_offset * 27)  # ~10k/year ÷ 365
    return (center - HLTV_ID_WINDOW, center + HLTV_ID_WINDOW)


def _find_hltv_match(team1: str, team2: str, kickoff_time: datetime) -> dict | None:
    """Return the most-recent HLTV match in the id-window matching the pair."""
    lo, hi = _hltv_id_window_for_kickoff(kickoff_time)
    rows = execute_query("""
        SELECT hltv_match_id, team1_name, team2_name, score1, score2,
               winner_name, best_of, event_name
        FROM cs2_hltv_matches
        WHERE ((LOWER(team1_name) = LOWER(%s) AND LOWER(team2_name) = LOWER(%s))
            OR (LOWER(team1_name) = LOWER(%s) AND LOWER(team2_name) = LOWER(%s)))
          AND hltv_match_id BETWEEN %s AND %s
          AND winner_name IS NOT NULL
        ORDER BY hltv_match_id DESC
        LIMIT 1
    """, (team1, team2, team2, team1, lo, hi))
    return rows[0] if rows else None


def _find_pandascore_match(team1: str, team2: str, kickoff_time: datetime) -> dict | None:
    """Find PandaScore match within ±6h of kickoff with a real winner."""
    rows = execute_query("""
        SELECT pandascore_id, team1_name, team2_name, score1, score2,
               winner, begin_at, status
        FROM cs2_pandascore_matches
        WHERE ((LOWER(team1_name) = LOWER(%s) AND LOWER(team2_name) = LOWER(%s))
            OR (LOWER(team1_name) = LOWER(%s) AND LOWER(team2_name) = LOWER(%s)))
          AND begin_at BETWEEN %s::timestamptz - INTERVAL '6 hours'
                           AND %s::timestamptz + INTERVAL '6 hours'
          AND winner IS NOT NULL
        ORDER BY ABS(EXTRACT(EPOCH FROM (begin_at - %s::timestamptz)))
        LIMIT 1
    """, (team1, team2, team2, team1, kickoff_time, kickoff_time, kickoff_time))
    return rows[0] if rows else None


def find_settlement(bet: dict) -> dict:
    """Return a structured settlement proposal for one bet.

    {
      'source': 'hltv' | 'pandascore' | None,
      'ext_id': int | None,
      'winner_team_name': str | None,  # e.g. 'Monte' (canonical name)
      'winner_side': 'team1' | 'team2' | None,
      'score1': int | None, 'score2': int | None,
      'confidence': 'high' | 'medium' | 'none',
      'reason': str,
    }
    """
    # Strategy 1: HLTV id-window
    h = _find_hltv_match(bet["team1"], bet["team2"], bet["kickoff_time"])
    if h:
        # Determine winner_side relative to the bet's team1/team2 orientation
        # (HLTV may have stored the match with teams swapped).
        if h["winner_name"].lower() == bet["team1"].lower():
            winner_side = "team1"
        elif h["winner_name"].lower() == bet["team2"].lower():
            winner_side = "team2"
        else:
            return {"source": "hltv", "ext_id": h["hltv_match_id"],
                    "winner_team_name": h["winner_name"], "winner_side": None,
                    "score1": None, "score2": None, "confidence": "none",
                    "reason": f"HLTV winner '{h['winner_name']}' doesn't match either bet team"}
        # Normalize scores to the bet's team1/team2 orientation
        if h["team1_name"].lower() == bet["team1"].lower():
            s1, s2 = h["score1"], h["score2"]
        else:
            s1, s2 = h["score2"], h["score1"]
        return {"source": "hltv", "ext_id": h["hltv_match_id"],
                "winner_team_name": h["winner_name"], "winner_side": winner_side,
                "score1": s1, "score2": s2, "confidence": "medium",
                "reason": f"HLTV id {h['hltv_match_id']} ({h['event_name']})"}

    # Strategy 2: PandaScore exact KO ±6h
    p = _find_pandascore_match(bet["team1"], bet["team2"], bet["kickoff_time"])
    if p:
        # winner is 'team1' / 'team2' relative to PandaScore's orientation
        ps_winner_name = p["team1_name"] if p["winner"] == "team1" else p["team2_name"]
        if ps_winner_name.lower() == bet["team1"].lower():
            winner_side = "team1"
        elif ps_winner_name.lower() == bet["team2"].lower():
            winner_side = "team2"
        else:
            return {"source": "pandascore", "ext_id": p["pandascore_id"],
                    "winner_team_name": ps_winner_name, "winner_side": None,
                    "score1": None, "score2": None, "confidence": "none",
                    "reason": f"PS winner '{ps_winner_name}' doesn't match either bet team"}
        if p["team1_name"].lower() == bet["team1"].lower():
            s1, s2 = p["score1"], p["score2"]
        else:
            s1, s2 = p["score2"], p["score1"]
        return {"source": "pandascore", "ext_id": p["pandascore_id"],
                "winner_team_name": ps_winner_name, "winner_side": winner_side,
                "score1": s1, "score2": s2, "confidence": "high",
                "reason": f"PandaScore id {p['pandascore_id']} (begin_at exact match)"}

    return {"source": None, "ext_id": None, "winner_team_name": None,
            "winner_side": None, "score1": None, "score2": None,
            "confidence": "none", "reason": "no match in any supplementary source"}


def _load_open_bets(only_bo3gg_id: int | None = None) -> list[dict]:
    """Distinct (bo3gg_id, team1, team2, kickoff_time) for bets needing
    settlement. One row per match (cs2_simulated_bets has multiple rows
    per match for different markets — they all share the same result-row
    requirement in cs2_results)."""
    where = "result IS NULL AND kickoff_time < NOW() - INTERVAL '6 hours'"
    params: tuple = ()
    if only_bo3gg_id is not None:
        where += " AND bo3gg_id = %s"
        params = (only_bo3gg_id,)
    # best_of lives on cs2_upcoming_matches, not cs2_simulated_bets. Join
    # is LEFT — if the upcoming row was purged, fall back to NULL and the
    # cs2_results insert will accept NULL best_of.
    return execute_query(f"""
        SELECT DISTINCT ON (sb.bo3gg_id)
               sb.bo3gg_id, sb.team1, sb.team2, sb.kickoff_time,
               um.best_of
        FROM cs2_simulated_bets sb
        LEFT JOIN cs2_upcoming_matches um ON um.bo3gg_id = sb.bo3gg_id
        WHERE {where.replace("result IS NULL", "sb.result IS NULL")
                    .replace("kickoff_time", "sb.kickoff_time")
                    .replace("bo3gg_id =", "sb.bo3gg_id =")}
        ORDER BY sb.bo3gg_id, sb.kickoff_time DESC
    """, params)


def _insert_result(bet: dict, proposal: dict) -> bool:
    """Write to cs2_results so cs2_bot --settle can close the open rows."""
    raw_status = f"resolved_via_{proposal['source']}_supplementary"
    res = execute_write("""
        INSERT INTO cs2_results
            (bo3gg_id, team1, team2, kickoff_time, best_of, winner, score1, score2, raw_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bo3gg_id) DO UPDATE SET
            winner = EXCLUDED.winner,
            score1 = EXCLUDED.score1,
            score2 = EXCLUDED.score2,
            raw_status = EXCLUDED.raw_status
    """, (bet["bo3gg_id"], bet["team1"], bet["team2"], bet["kickoff_time"],
          bet["best_of"], proposal["winner_side"],
          proposal["score1"], proposal["score2"], raw_status))
    return bool(res)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--apply", action="store_true",
                   help="Actually write to cs2_results (default: dry-run only)")
    p.add_argument("--bo3gg-id", type=int, default=None,
                   help="Restrict to a single match by bo3gg_id")
    p.add_argument("--min-confidence", choices=["high", "medium"], default="medium",
                   help="Only --apply settlements at or above this confidence")
    args = p.parse_args()

    bets = _load_open_bets(args.bo3gg_id)
    print(f"\n=== CS2 supplementary-source settlement  {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z ===")
    print(f"  {len(bets)} distinct matches with open bets > 6h post-kickoff")
    print(f"  mode: {'APPLY' if args.apply else 'dry-run'}  min-confidence: {args.min_confidence}\n")

    confidence_rank = {"high": 2, "medium": 1, "none": 0}
    min_rank = confidence_rank[args.min_confidence]

    applied = skipped = unresolved = 0
    for b in bets:
        proposal = find_settlement(b)
        side = proposal["winner_side"]
        if not side:
            print(f"  ✗ {b['team1']:18} vs {b['team2']:18} KO={b['kickoff_time']:%m-%d %H:%M}  "
                  f"bo3gg={b['bo3gg_id']}  UNRESOLVED — {proposal['reason']}")
            unresolved += 1
            continue
        # Bet-on-team display
        winner_str = b['team1'] if side == 'team1' else b['team2']
        s1, s2 = proposal['score1'], proposal['score2']
        tag = {"high": "HIGH", "medium": "MED", "none": "LOW"}[proposal["confidence"]]
        print(f"  ✓ {b['team1']:18} vs {b['team2']:18} KO={b['kickoff_time']:%m-%d %H:%M}  "
              f"bo3gg={b['bo3gg_id']}  → {winner_str} {s1}-{s2}  [{tag}: {proposal['reason']}]")

        if args.apply and confidence_rank[proposal["confidence"]] >= min_rank:
            if _insert_result(b, proposal):
                applied += 1
            else:
                print(f"    [!] insert returned falsy — already present?")
        elif args.apply:
            print(f"    [skip] below min-confidence {args.min_confidence}")
            skipped += 1

    print(f"\n  applied: {applied}  skipped-low-conf: {skipped}  unresolved: {unresolved}")
    if args.apply and applied > 0:
        print(f"\n  Next: run `python3 scripts/esports/cs2_bot.py --settle` to close the open bets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
