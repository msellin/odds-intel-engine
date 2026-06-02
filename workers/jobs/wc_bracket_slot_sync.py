"""
OddsIntel — WC Bracket Slot Sync (WC-BRACKET-STAGE-GATED)

Maps WC knockout fixtures in `matches` (identified by `matches.round_label`)
onto bracket slot assignments in `wc_bracket_slot_assignments`. Runs every
30 min during the WC window (gated in the scheduler) so as soon as AF
publishes the R32 / R16 / QF / SF / Final fixtures, the FE sees them
and the matching round's `locked_at` is set to the first match kickoff.

After a NEW round seeds (i.e. a row that previously had match_id=NULL now
has match_id set), the job also re-runs the AI ghost generator for that
round so AI picks are ready before users open the page.

Idempotent — re-running just refreshes timestamps; never overwrites a
`locked_at` that's already in the past.

AF round labels (text) → bracket round:
    'Round of 32 - N'     → r32  (N is 1..16; position = N-1)
    'Round of 16 - N'     → r16  (N is 1..8;  position = N-1)
    'Quarter-finals - N'  → qf   (N is 1..4;  position = N-1)
    'Semi-finals - N'     → sf   (N is 1..2;  position = N-1)
    'Final'               → final (single match; position = 0)

Unknowns are logged + skipped; no slot row is corrupted.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from rich.console import Console

console = Console()
log = logging.getLogger(__name__)

WC_LEAGUE_AF_ID = 1  # FIFA World Cup api_football_id


# ── Round label parsing ────────────────────────────────────────────────────

_ROUND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("r32",   re.compile(r"^\s*Round of 32\s*-\s*(\d+)\s*$", re.IGNORECASE)),
    ("r16",   re.compile(r"^\s*Round of 16\s*-\s*(\d+)\s*$", re.IGNORECASE)),
    ("qf",    re.compile(r"^\s*Quarter[- ]?finals?\s*-\s*(\d+)\s*$", re.IGNORECASE)),
    ("sf",    re.compile(r"^\s*Semi[- ]?finals?\s*-\s*(\d+)\s*$", re.IGNORECASE)),
    ("final", re.compile(r"^\s*Final\s*$", re.IGNORECASE)),
]


def parse_round_label(label: Optional[str]) -> Optional[tuple[str, int]]:
    """Parse AF round-label text into (round_key, position).

    Returns None for unknown / unparseable / third-place-playoff labels.
    Position is 0-indexed."""
    if not label:
        return None
    for key, pat in _ROUND_PATTERNS:
        m = pat.match(label)
        if not m:
            continue
        if key == "final":
            return ("final", 0)
        try:
            n = int(m.group(1))
        except (IndexError, ValueError):
            return None
        # AF uses 1-indexed match numbers (e.g. "Round of 32 - 1"). Our
        # slot position is 0-indexed.
        return (key, n - 1)
    return None


# ── Loaders ────────────────────────────────────────────────────────────────

def _load_wc_knockout_matches() -> list[dict]:
    """All WC knockout-stage matches in our DB (any status). Joined to league
    by api_football_id=1. Filters out matches with null round_label."""
    from workers.api_clients.db import execute_query
    return execute_query(
        """
        SELECT m.id::text AS id,
               m.date,
               m.round_label,
               m.status,
               m.home_team_id::text AS home_team_id,
               m.away_team_id::text AS away_team_id
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = %s
          AND m.round_label IS NOT NULL
        ORDER BY m.date ASC
        """,
        (WC_LEAGUE_AF_ID,),
    )


def refresh_round_labels_from_af() -> int:
    """Backfill `matches.round_label` for every WC fixture by querying AF.
    AF fixture objects carry `league.round` (e.g. 'Round of 32 - 1', 'Final').
    Returns number of rows updated.

    Safe to run repeatedly — only WRITES when the stored label differs from
    AF's current value. AF is the source of truth for WC round labels."""
    from workers.api_clients.api_football import get_fixtures_by_league_season
    from workers.api_clients.db import execute_query, execute_write

    try:
        # WC 2026 season is 2026 in our convention (matches migration 167).
        af_fixtures = get_fixtures_by_league_season(WC_LEAGUE_AF_ID, 2026)
    except Exception as e:
        log.warning("refresh_round_labels_from_af: AF call failed: %s", e)
        return 0
    if not af_fixtures:
        return 0

    # AF fixture id → round label
    af_round_by_id: dict[int, str] = {}
    for f in af_fixtures:
        fid = f.get("fixture", {}).get("id")
        rnd = f.get("league", {}).get("round")
        if fid is not None and rnd:
            af_round_by_id[int(fid)] = str(rnd)
    if not af_round_by_id:
        return 0

    rows = execute_query(
        """SELECT m.id::text AS id, m.api_football_id AS afid, m.round_label
           FROM matches m
           JOIN leagues l ON l.id = m.league_id
           WHERE l.api_football_id = %s""",
        (WC_LEAGUE_AF_ID,),
    )
    updated = 0
    for r in rows:
        afid = r.get("afid")
        if afid is None:
            continue
        want = af_round_by_id.get(int(afid))
        if want and r.get("round_label") != want:
            execute_write(
                "UPDATE matches SET round_label = %s, updated_at = NOW() WHERE id = %s::uuid",
                (want, r["id"]),
            )
            updated += 1
    if updated:
        console.print(f"[cyan]refresh_round_labels_from_af: updated {updated} matches.round_label[/cyan]")
    return updated


def _load_existing_assignments() -> dict[tuple[str, int], dict]:
    """{(round, position): row}. Used to detect newly-seeded rounds + skip
    overwriting historical locked_at values."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT id::text AS id, round, position,
                  match_id::text AS match_id,
                  seeded_at, locked_at
           FROM wc_bracket_slot_assignments""",
        (),
    )
    return {(r["round"], r["position"]): r for r in rows}


# ── Sync core ──────────────────────────────────────────────────────────────

def sync_slot_assignments() -> dict:
    """Walk WC knockout matches, map each one to (round, position), upsert
    `wc_bracket_slot_assignments`. Returns a summary dict with counts and
    the set of rounds that NEWLY seeded this run (so the caller can fire
    `generate_ai_brackets --round <r>` for each one).

    Steps:
      1. Load all WC matches with non-null round_label.
      2. Parse each round_label → (round_key, position).
      3. Group matches by round_key. For each round, the FIRST kickoff is the
         lock time (all matches in a round lock together — fairer than per-
         match lock since users would see leaks otherwise).
      4. For each (round, position):
            - if match_id changed → set seeded_at = NOW()
            - if locked_at not yet in the past → set locked_at = first kickoff
            - else preserve historical locked_at (audit integrity)
      5. Return {newly_seeded_rounds: ['r32', ...], total_assigned: N}
    """
    from workers.api_clients.db import execute_write
    from datetime import datetime, timezone

    # Pull fresh round labels from AF before reading our own table so newly-
    # seeded knockout fixtures pick up the right round on the first sync run
    # after group stage settles.
    try:
        refresh_round_labels_from_af()
    except Exception as e:
        log.warning("wc_bracket_slot_sync: round-label refresh failed: %s", e)

    matches = _load_wc_knockout_matches()
    if not matches:
        console.print("[dim]wc_bracket_slot_sync: no WC knockout matches with round_label[/dim]")
        return {"newly_seeded_rounds": [], "total_assigned": 0, "skipped_unknown": 0}

    # Parse + bucket by round → (matchup info, position)
    parsed: dict[str, list[tuple[int, dict]]] = {}
    skipped_unknown = 0
    for m in matches:
        res = parse_round_label(m["round_label"])
        if res is None:
            skipped_unknown += 1
            log.debug("wc_bracket_slot_sync: skip unknown round_label=%r", m["round_label"])
            continue
        round_key, position = res
        parsed.setdefault(round_key, []).append((position, m))

    # Compute per-round first-kickoff (for locked_at) once
    first_kickoff_by_round: dict[str, object] = {}
    for round_key, items in parsed.items():
        kickoffs = [m["date"] for _, m in items if m.get("date") is not None]
        if kickoffs:
            first_kickoff_by_round[round_key] = min(kickoffs)

    existing = _load_existing_assignments()
    now = datetime.now(timezone.utc)

    newly_seeded: set[str] = set()
    total_assigned = 0

    for round_key, items in parsed.items():
        first_kickoff = first_kickoff_by_round.get(round_key)
        for position, match in items:
            key = (round_key, position)
            existing_row = existing.get(key)
            prev_match_id = existing_row.get("match_id") if existing_row else None
            prev_locked = existing_row.get("locked_at") if existing_row else None

            # If the locked_at is already in the past, we DO NOT touch
            # match_id either — locking the slot freezes it for audit.
            locked_in_past = (
                prev_locked is not None
                and hasattr(prev_locked, "timestamp")
                and prev_locked <= now
            )
            if locked_in_past:
                continue

            new_match_id = match["id"]
            should_set_seeded = (prev_match_id != new_match_id)
            if should_set_seeded and prev_match_id is None:
                newly_seeded.add(round_key)

            # Write
            execute_write(
                """
                INSERT INTO wc_bracket_slot_assignments
                    (round, position, match_id, seeded_at, locked_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (round, position) DO UPDATE SET
                    match_id   = EXCLUDED.match_id,
                    seeded_at  = CASE
                                   WHEN wc_bracket_slot_assignments.match_id IS DISTINCT FROM EXCLUDED.match_id
                                     THEN NOW()
                                   ELSE wc_bracket_slot_assignments.seeded_at
                                 END,
                    locked_at  = EXCLUDED.locked_at,
                    updated_at = NOW()
                """,
                (round_key, position, new_match_id,
                 now if should_set_seeded else (existing_row.get("seeded_at") if existing_row else None),
                 first_kickoff),
            )
            total_assigned += 1

    console.print(
        f"[green]wc_bracket_slot_sync: assigned {total_assigned} slots; "
        f"newly seeded rounds: {sorted(newly_seeded) or 'none'}; "
        f"skipped unknown labels: {skipped_unknown}[/green]"
    )
    return {
        "newly_seeded_rounds": sorted(newly_seeded),
        "total_assigned": total_assigned,
        "skipped_unknown": skipped_unknown,
    }


def run_slot_sync_and_ai_refresh() -> dict:
    """Top-level entry-point for the scheduler. After slot-sync seeds new
    rounds, fire the per-round AI ghost generator so AI picks are ready
    before any human user lands on the page.

    Returns the combined summary."""
    result = sync_slot_assignments()
    newly = result.get("newly_seeded_rounds", [])
    ai_runs: list[dict] = []
    if newly:
        from scripts.generate_ai_brackets import generate_all
        for round_key in newly:
            try:
                r = generate_all(round_key=round_key)
                ai_runs.append({"round": round_key, "ok": r.get("ok"), "n": len(r.get("results", []))})
            except Exception as e:
                log.exception("AI bracket gen failed for round %s", round_key)
                ai_runs.append({"round": round_key, "ok": False, "error": str(e)})
    result["ai_runs"] = ai_runs
    return result


def main():
    """Manual run: `python -m workers.jobs.wc_bracket_slot_sync`."""
    out = run_slot_sync_and_ai_refresh()
    console.print(out)


if __name__ == "__main__":
    main()
