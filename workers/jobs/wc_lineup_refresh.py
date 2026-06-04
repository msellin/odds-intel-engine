"""
OddsIntel — WC2026 T-60min Lineup-Aware Prediction Refresh (WC-A5)

When a confirmed starting XI lands ~60 minutes before kickoff (API-Football
`/fixtures/lineups`), this job re-runs the national-team predictor with a
small ELO adjustment that reflects who is actually on the pitch versus the
"expected" starting XI captured by `team_roster_strength.avg_starting_xi_club_elo`
(populated by the WC-A2 roster-strength scraper).

For each matched WC fixture, we write a fresh `predictions` row tagged with:
  source='national_team_v1_lineup'
  model_version='national_team_v1_lineup'

The original pre-match prediction (`source='national_team_v1'`) is untouched —
downstream consumers can prefer the `_lineup` source when present, otherwise
fall back to the morning prediction.

Pipeline (per fixture):
  1) Query WC fixtures (api_football_id=1) kicking off in the next 90 minutes
     that have `lineups_fetched_at` NOT NULL and no existing prediction row
     for source='national_team_v1_lineup'.
  2) Read confirmed startXI from `matches.lineups_home` / `lineups_away`
     (already-stored JSONB). Refetch from AF only if missing — usually the
     live-tracker has already populated these.
  3) For each side, compare the actual XI to the expected baseline from
     `team_roster_strength` and translate the delta into an ELO adjustment.
     v1 limitation: per-player club ELO is not stored, so we cannot compute
     `actual_xi_avg_elo` precisely yet — we fall back to a 0-ELO adjustment
     while still emitting a `reasoning` payload that names the confirmed XI.
     The hook is wired in so a future A2-extension that writes
     `match_starting_xi_elo` (per-fixture pre-computed by the lineups poller)
     can plug in via the `_actual_xi_elo()` helper without touching the
     scheduler or the predictor call site.
  4) Call the existing `predict_match()` from `workers.model.national_team_predictor`
     with the (possibly adjusted) ELOs to produce a 1X2 + O/U 2.5 + BTTS triple.
  5) Bulk-upsert into `predictions` keyed on
     (match_id, market, source, model_version) — idempotent on re-run.

Usage:
  python -m workers.jobs.wc_lineup_refresh            # run once
  python -m workers.jobs.wc_lineup_refresh --dry-run  # no writes, print plan
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

# Importable standalone (Railway scheduler imports this; CLI also runs)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write, bulk_upsert
from workers.api_clients.api_football import (
    get_fixture_lineups,
    parse_fixture_lineups,
)
from workers.model.national_team_predictor import (
    predict_match,
    TeamGoalStats,
    COMP_WEIGHT,
)

console = Console()

# WC league AF id (FIFA World Cup)
WC_LEAGUE_AF_ID = 1
LINEUP_SOURCE = "national_team_v1_lineup"
LINEUP_MODEL_VERSION = "national_team_v1_lineup"

# Recent-form window (matches must be in same units as the morning predictor
# in `scripts/write_national_team_predictions.py`).
FORM_WINDOW = 20

# Best params copy-pasted from write_national_team_predictions.BEST_PARAMS so
# the lineup-refreshed row uses the same predictor settings as the morning
# row — only the inputs differ. Kept in sync via this comment; if the morning
# params change, update here too.
BEST_PARAMS = {
    "softening_factor": 1.3,
    "draw_base": 0.30,
    "avg_goals_per_team": 1.15,
    "elo_goal_factor": 0.0,
    "goals_smoothing": 0.3,
}

# Conversion: ELO points per (club ELO delta of starting XI). v1 is wired
# to 0 because we can't compute actual_xi_avg_elo yet (see module docstring).
# When per-player ELO storage lands, set this to ~0.15 (rough mapping of
# 1 club-ELO point ≈ 0.15 international-ELO points across the XI), and
# `_actual_xi_elo` will start returning non-None values.
ELO_DELTA_SCALE = 0.15


# ── Helpers ──────────────────────────────────────────────────────────────────

def _start_xi_players(lineup_json: dict | None) -> list[dict]:
    """Extract the confirmed starting XI from an AF lineup payload.

    AF schema (per team):
      { team:{id,name}, formation, startXI:[{player:{id,name,pos,grid,number}}],
        substitutes:[...], coach:{...} }
    """
    if not lineup_json:
        return []
    start = lineup_json.get("startXI") or []
    out = []
    for entry in start:
        p = entry.get("player") if isinstance(entry, dict) else None
        if not p:
            continue
        out.append({
            "id":   p.get("id"),
            "name": p.get("name"),
            "pos":  p.get("pos"),
            "number": p.get("number"),
        })
    return out


def _actual_xi_elo(start_xi: list[dict]) -> float | None:
    """Average club ELO of the confirmed starting XI.

    v1 returns None because per-player club ELO is not yet stored. The function
    is a stub on purpose: the call site uses None as "fall back to no-adjustment"
    and a future enhancement (writing per-player club ELO into a new table or
    using `team_roster_strength_players`) can return a real number here without
    touching anything else.
    """
    if not start_xi:
        return None
    return None


def _expected_xi_elo(team_id: str) -> float | None:
    """Most-recent `avg_starting_xi_club_elo` from team_roster_strength.

    Returns None when the A2 scraper hasn't populated this team yet — caller
    treats that as "fall back to no-adjustment" per the spec.
    """
    rows = execute_query(
        """SELECT avg_starting_xi_club_elo
             FROM team_roster_strength
            WHERE team_id = %s
              AND avg_starting_xi_club_elo IS NOT NULL
            ORDER BY snapshot_date DESC
            LIMIT 1""",
        (team_id,),
    )
    if not rows:
        return None
    val = rows[0].get("avg_starting_xi_club_elo")
    return float(val) if val is not None else None


def _elo_adjustment_for_side(
    team_id: str,
    start_xi: list[dict],
) -> tuple[float, dict]:
    """Compute the ELO adjustment for one side. Returns (delta, audit_dict).

    delta is added to the team's international ELO before the predictor runs.
    audit_dict feeds the `reasoning` JSONB so downstream tools can see why
    the model moved (or why it didn't).
    """
    expected = _expected_xi_elo(team_id)
    actual = _actual_xi_elo(start_xi)

    audit = {
        "expected_xi_avg_elo": expected,
        "actual_xi_avg_elo": actual,
        "n_starters": len(start_xi),
        "starters": [p.get("name") for p in start_xi if p.get("name")],
    }
    if expected is None or actual is None:
        audit["elo_delta_club"] = 0.0
        audit["elo_adjustment"] = 0.0
        audit["fallback"] = (
            "no_roster_strength" if expected is None else "no_actual_xi_elo"
        )
        return 0.0, audit

    delta_club = actual - expected
    adjustment = delta_club * ELO_DELTA_SCALE
    audit["elo_delta_club"] = round(delta_club, 2)
    audit["elo_adjustment"] = round(adjustment, 2)
    return adjustment, audit


def _team_form_stats(team_id: str) -> TeamGoalStats:
    """Recent goal stats from finished internationals (last FORM_WINDOW), weighted
    by competition tier. Mirrors the logic in write_national_team_predictions._stats().

    Inline DB query per side — at T-60 we have at most a handful of fixtures
    firing, so per-call cost is fine. If volume grows, swap to a single batched
    query keyed on team_id IN (...).
    """
    history = execute_query(
        """
        SELECT m.home_team_id, m.away_team_id, m.score_home, m.score_away,
               l.api_football_id AS league_af_id
          FROM matches m
          JOIN leagues l ON l.id = m.league_id
         WHERE l.country = 'World'
           AND m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
           AND (m.home_team_id = %s OR m.away_team_id = %s)
         ORDER BY m.date DESC
         LIMIT %s
        """,
        (team_id, team_id, FORM_WINDOW),
    )
    if not history:
        return TeamGoalStats(0, 0, 0)

    # Map AF league id → category for competition weighting. Same map used
    # in compute_international_elo.py — duplicated to keep this job free of
    # the hard-constraint-protected script.
    cat_map = {1: "tournament", 4: "tournament", 6: "tournament", 7: "tournament",
               9: "tournament", 22: "tournament",
               5: "qualifier_nl", 24: "qualifier_nl", 25: "qualifier_nl"}

    gf_w = ga_w = w_total = 0.0
    n = 0
    for h in history:
        cat = cat_map.get(h.get("league_af_id")) or "friendly"
        w = COMP_WEIGHT.get(cat, 0.3)
        if h["home_team_id"] == team_id:
            gf, ga = float(h["score_home"]), float(h["score_away"])
        else:
            gf, ga = float(h["score_away"]), float(h["score_home"])
        gf_w += gf * w
        ga_w += ga * w
        w_total += w
        n += 1

    if w_total <= 0:
        return TeamGoalStats(0, 0, 0)
    return TeamGoalStats(gf_w / w_total, ga_w / w_total, n)


def _current_elo(team_id: str) -> float | None:
    rows = execute_query(
        """SELECT elo_rating FROM team_elo_international
            WHERE team_id = %s
            ORDER BY match_date DESC
            LIMIT 1""",
        (team_id,),
    )
    if not rows:
        return None
    return float(rows[0]["elo_rating"])


def _ensure_lineups(match: dict, dry_run: bool = False) -> tuple[dict | None, dict | None]:
    """Return (home_lineup_json, away_lineup_json) for the match.

    Reads from `matches.lineups_home/away` first (already populated by the
    live tracker's T-60 fetcher). Falls back to a direct AF fetch when the
    columns are NULL but `lineups_fetched_at` is set — defends against any
    partial-write state. Returns (None, None) on AF errors so the caller
    can skip the fixture without aborting the batch.
    """
    h_json = match.get("lineups_home")
    a_json = match.get("lineups_away")
    if h_json and a_json:
        return h_json, a_json

    af_id = match.get("api_football_id")
    if not af_id:
        return None, None

    if dry_run:
        console.print(f"  [dim]DRY RUN: would refetch lineups for AF {af_id}[/dim]")
        return h_json, a_json

    try:
        raw = get_fixture_lineups(af_id)
    except Exception as e:
        console.print(f"  [yellow]lineup refetch failed AF {af_id}: {e}[/yellow]")
        return None, None
    if not raw or len(raw) < 2:
        return None, None
    parsed = parse_fixture_lineups(raw)
    return parsed.get("lineups_home"), parsed.get("lineups_away")


def _select_candidates() -> list[dict]:
    """Pick WC fixtures that need a lineup-aware refresh right now.

    Filters:
      - league.api_football_id = 1  (FIFA World Cup)
      - matches.status         = 'scheduled'
      - matches.lineups_fetched_at IS NOT NULL  (XI confirmed)
      - kickoff in (now, now + 90min]
      - no predictions row exists yet for source='national_team_v1_lineup'
        (idempotency safeguard layered on top of the ON CONFLICT upsert)
    """
    rows = execute_query(
        """
        SELECT m.id, m.api_football_id, m.date,
               m.home_team_id, m.away_team_id,
               m.lineups_home, m.lineups_away,
               m.lineups_fetched_at,
               th.name AS home_name, ta.name AS away_name
          FROM matches m
          JOIN leagues l ON l.id = m.league_id
          JOIN teams   th ON th.id = m.home_team_id
          JOIN teams   ta ON ta.id = m.away_team_id
         WHERE l.api_football_id = %s
           AND m.status = 'scheduled'
           AND m.lineups_fetched_at IS NOT NULL
           AND m.date > now()
           AND m.date <= now() + interval '90 minutes'
           AND NOT EXISTS (
                 SELECT 1 FROM predictions p
                  WHERE p.match_id = m.id
                    AND p.source = %s
                    AND p.model_version = %s
           )
         ORDER BY m.date ASC
        """,
        (WC_LEAGUE_AF_ID, LINEUP_SOURCE, LINEUP_MODEL_VERSION),
    )
    return rows


def _build_predictions(match: dict, dry_run: bool) -> tuple[list[dict], dict]:
    """Build prediction rows + audit dict for one fixture. Returns ([], audit)
    on any unrecoverable issue so the caller can keep going."""
    audit: dict = {
        "match_id": match["id"],
        "home_team": match.get("home_name"),
        "away_team": match.get("away_name"),
        "lineups_fetched_at": str(match.get("lineups_fetched_at")),
    }

    h_lineup, a_lineup = _ensure_lineups(match, dry_run=dry_run)
    h_xi = _start_xi_players(h_lineup)
    a_xi = _start_xi_players(a_lineup)
    if len(h_xi) < 11 or len(a_xi) < 11:
        audit["skipped"] = f"incomplete_xi(home={len(h_xi)}, away={len(a_xi)})"
        return [], audit

    h_elo = _current_elo(match["home_team_id"])
    a_elo = _current_elo(match["away_team_id"])
    if h_elo is None or a_elo is None:
        audit["skipped"] = "missing_team_elo_international"
        return [], audit

    h_adj, h_audit = _elo_adjustment_for_side(match["home_team_id"], h_xi)
    a_adj, a_audit = _elo_adjustment_for_side(match["away_team_id"], a_xi)
    audit["home"] = h_audit
    audit["away"] = a_audit
    audit["elo_before"] = {"home": round(h_elo, 1), "away": round(a_elo, 1)}
    audit["elo_after"] = {
        "home": round(h_elo + h_adj, 1),
        "away": round(a_elo + a_adj, 1),
    }

    pred = predict_match(
        home_elo=h_elo + h_adj,
        away_elo=a_elo + a_adj,
        home_stats=_team_form_stats(match["home_team_id"]),
        away_stats=_team_form_stats(match["away_team_id"]),
        comp_category="tournament",   # WC fixtures are all tournament-tier
        **BEST_PARAMS,
    )

    reasoning_str = json.dumps(audit, default=str)

    rows: list[dict] = []
    # 1X2 primary
    for mkt, prob in (
        ("1x2_home", pred["1x2_home"]),
        ("1x2_draw", pred["1x2_draw"]),
        ("1x2_away", pred["1x2_away"]),
    ):
        rows.append({
            "match_id": match["id"], "market": mkt,
            "source": LINEUP_SOURCE, "model_version": LINEUP_MODEL_VERSION,
            "model_prob": prob, "confidence": 0.6,
            "reasoning": reasoning_str,
        })
    # O/U + BTTS — secondary, lower confidence (same convention as morning job)
    for mkt, prob in (
        ("over_2_5",  pred["over_2_5"]),
        ("under_2_5", pred["under_2_5"]),
        ("btts_yes",  pred["btts_yes"]),
        ("btts_no",   pred["btts_no"]),
    ):
        rows.append({
            "match_id": match["id"], "market": mkt,
            "source": LINEUP_SOURCE, "model_version": LINEUP_MODEL_VERSION,
            "model_prob": prob, "confidence": 0.3,
            "reasoning": reasoning_str,
        })
    return rows, audit


def _bulk_upsert_lineup_predictions(rows: list[dict]) -> int:
    """Idempotent bulk upsert keyed on (match_id, market, source, model_version).

    Mirrors `bulk_store_predictions` in supabase_client.py but with the lineup
    source/model_version baked in, and uses bulk_upsert from db.py per the
    task constraints. NaN/None probabilities are filtered upstream.
    """
    if not rows:
        return 0

    tuples = []
    for r in rows:
        prob = r.get("model_prob")
        if prob is None:
            continue
        tuples.append((
            r["match_id"], r["market"], r["source"],
            float(prob),
            float(r.get("confidence", 0.5)),
            r.get("reasoning"),
            r["model_version"],
        ))
    if not tuples:
        return 0

    return bulk_upsert(
        "predictions",
        columns=["match_id", "market", "source",
                 "model_probability", "confidence", "reasoning",
                 "model_version"],
        rows=tuples,
        conflict_columns=["match_id", "market", "source", "model_version"],
        update_columns=["model_probability", "confidence", "reasoning"],
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def run_wc_lineup_refresh(dry_run: bool = False) -> dict:
    """Run one pass of the T-60 lineup-aware refresh.

    Returns: {"candidates": N, "refreshed": N, "skipped": N, "rows": N, "dry_run": bool}
    Safe to call any number of times — re-running is a no-op for matches that
    already have a `national_team_v1_lineup` row (filtered out by NOT EXISTS).
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(
        f"[bold cyan]═══ WC T-60 lineup-aware refresh @ {now_str} ═══[/bold cyan]"
    )

    matches = _select_candidates()
    summary = {"candidates": len(matches), "refreshed": 0, "skipped": 0,
               "rows": 0, "dry_run": dry_run}
    if not matches:
        console.print("[dim]No WC fixtures in next 90min with confirmed lineups "
                      "awaiting refresh.[/dim]")
        return summary

    console.print(f"  {len(matches)} candidate fixture(s) — generating refreshed predictions\n")

    all_rows: list[dict] = []
    for m in matches:
        label = f"{m.get('home_name')} vs {m.get('away_name')} ({m['api_football_id']})"
        rows, audit = _build_predictions(m, dry_run=dry_run)
        if not rows:
            summary["skipped"] += 1
            console.print(f"  [yellow]skip[/yellow] {label} — {audit.get('skipped', 'unknown')}")
            continue
        summary["refreshed"] += 1
        all_rows.extend(rows)
        eb = audit.get("elo_before", {})
        ea = audit.get("elo_after", {})
        console.print(
            f"  [green]✓[/green] {label} | "
            f"ELO {eb.get('home')}→{ea.get('home')} vs {eb.get('away')}→{ea.get('away')}"
        )
        if dry_run:
            for r in rows[:3]:
                console.print(f"      sample {r['market']:<10} p={r['model_prob']:.3f}")

    summary["rows"] = len(all_rows)

    if dry_run:
        console.print(f"\n[yellow]Dry run — {len(all_rows)} rows NOT written.[/yellow]")
        return summary

    n = _bulk_upsert_lineup_predictions(all_rows)
    summary["written"] = n
    console.print(
        f"\n[green]✓ wrote {n} prediction rows[/green] "
        f"(source='{LINEUP_SOURCE}', model_version='{LINEUP_MODEL_VERSION}')"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan + rows without writing to DB")
    args = parser.parse_args()
    run_wc_lineup_refresh(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
