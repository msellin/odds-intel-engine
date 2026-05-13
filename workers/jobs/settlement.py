"""
OddsIntel — Settlement Pipeline
Fetches finished match results and settles all pending bets.
Also computes Closing Line Value (CLV) for each settled bet.

Run this in the evening after matches finish (21:00 UTC / midnight EET).

Usage:
  python settlement.py           # Settle today's finished matches
  python settlement.py --report  # Show settled P&L summary
"""

import sys
import os
import math
import argparse
from pathlib import Path
from datetime import datetime, timezone, date, timedelta
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import get_results_for_settlement as get_api_football_results
from workers.scrapers.espn_results import get_finished_matches_espn
from workers.api_clients.supabase_client import (
    store_team_form,
    store_model_evaluation,
    compute_team_form_from_db,
    store_match_stats_full,
    store_match_events_af,
    store_match_player_stats,
    build_match_feature_vectors,
    build_referee_stats,
)
from workers.api_clients.db import execute_query, execute_write, bulk_upsert

console = Console()

# SQL query to load pending bets with match + team join
_PENDING_BETS_SQL = """
SELECT
    sb.id, sb.bot_id, sb.match_id, sb.market, sb.selection, sb.stake,
    sb.odds_at_pick, sb.model_probability, sb.edge_percent, sb.result,
    sb.pnl, sb.clv, sb.calibrated_prob, sb.alignment_class, sb.kelly_fraction,
    sb.odds_drift, sb.news_impact_score, sb.reasoning, sb.bankroll_after,
    sb.closing_odds, sb.pick_time,
    m.id as m_id, m.date as m_date, m.score_home, m.score_away,
    m.result as match_result, m.status as match_status,
    ht.name as home_team_name, ta.name as away_team_name
FROM simulated_bets sb
LEFT JOIN matches m ON sb.match_id = m.id
LEFT JOIN teams ht ON m.home_team_id = ht.id
LEFT JOIN teams ta ON m.away_team_id = ta.id
WHERE sb.result = 'pending'
"""


# BET-TIMING-MONITOR: settle shadow_bets the same way as simulated_bets.
# Distinct query because shadow_bets has fewer columns (no bankroll/alignment).
_PENDING_SHADOW_BETS_SQL = """
SELECT
    sb.id, sb.bot_id, sb.match_id, sb.market, sb.selection, sb.stake,
    sb.odds_at_pick, sb.model_probability, sb.edge_percent, sb.result,
    sb.closing_odds, sb.pick_time, sb.shadow_cohort, sb.timing_cohort,
    m.id as m_id, m.date as m_date, m.score_home, m.score_away,
    m.result as match_result, m.status as match_status,
    ht.name as home_team_name, ta.name as away_team_name
FROM shadow_bets sb
LEFT JOIN matches m ON sb.match_id = m.id
LEFT JOIN teams ht ON m.home_team_id = ht.id
LEFT JOIN teams ta ON m.away_team_id = ta.id
WHERE sb.result = 'pending'
"""


# ─── Result matching ─────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip common suffixes for fuzzy matching"""
    name = name.lower().strip()
    for suffix in [" fc", " sc", " cf", " ac", " fk", " sk", " bk", " if", " afc", " utd", " united"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def match_score(db_name: str, result_name: str) -> float:
    """0-1 similarity score between two team names"""
    a = normalize_name(db_name)
    b = normalize_name(result_name)
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    # Common prefix
    min_len = min(len(a), len(b))
    if min_len >= 4:
        prefix_match = sum(1 for i in range(min_len) if a[i] == b[i])
        if prefix_match >= min_len * 0.7:
            return 0.7
    return 0.0


def find_result_for_match(db_home: str, db_away: str,
                          results: list[dict]) -> dict | None:
    """Find the matching result for a DB match from results list"""
    best_score = 0
    best_match = None

    for r in results:
        if r.get("home_goals") is None:
            continue  # not finished

        h_score = match_score(db_home, r["home_team"])
        a_score = match_score(db_away, r["away_team"])
        combined = (h_score + a_score) / 2

        if combined > best_score and combined >= 0.7:
            best_score = combined
            best_match = r

    return best_match


# ─── Bet settlement logic ────────────────────────────────────────────────────

def _parse_ou_line(market: str, selection: str) -> float | None:
    """Extract the O/U line from market or selection tokens.

    Daily-pipeline bets store the line in market (`over_under_25`); inplay bets
    store it in selection (`over 1.5`). Walk both, return the first numeric in
    (0, 10). `25` (no dot) is interpreted as 2.5 to match the legacy encoding.
    """
    for token in market.replace("/", "_").split("_") + selection.split():
        if token in ("over", "under", "o", "u", ""):
            continue
        try:
            if "." in token:
                v = float(token)
            elif token.isdigit() and len(token) == 2:
                v = int(token) / 10
            else:
                v = float(token)
        except (ValueError, TypeError):
            continue
        if 0 < v < 10:
            return v
    return None


def settle_bet_result(bet: dict, home_goals: int, away_goals: int,
                      closing_odds: float | None) -> dict:
    """
    Determine if a bet won or lost.
    Returns dict with result, pnl, clv.
    """
    market = bet["market"].lower().strip()
    selection = bet["selection"].lower().strip()
    stake = float(bet["stake"])
    odds = float(bet["odds_at_pick"])
    total_goals = home_goals + away_goals

    won = False

    if market == "1x2":
        if selection == "home" and home_goals > away_goals:
            won = True
        elif selection in ("draw", "x") and home_goals == away_goals:
            won = True
        elif selection == "away" and away_goals > home_goals:
            won = True

    elif "over_under" in market or "o/u" in market or market == "ou":
        line = _parse_ou_line(market, selection)
        if line is not None:
            if "over" in selection and total_goals > line:
                won = True
            elif "under" in selection and total_goals < line:
                won = True

    elif market == "btts":
        both_scored = home_goals >= 1 and away_goals >= 1
        if selection == "yes" and both_scored:
            won = True
        elif selection == "no" and not both_scored:
            won = True

    elif market == "double_chance":
        home_wins = home_goals > away_goals
        draw = home_goals == away_goals
        away_wins = away_goals > home_goals
        if selection == "1x" and (home_wins or draw):
            won = True
        elif selection == "x2" and (draw or away_wins):
            won = True
        elif selection == "12" and (home_wins or away_wins):
            won = True

    elif market == "asian_handicap":
        # selection = "home -1.25" or "away +0.5" (team + handicap in one string)
        parts = selection.split(" ", 1)
        if len(parts) == 2:
            sel_team, hl_str = parts[0], parts[1]
            try:
                hl = float(hl_str)
            except ValueError:
                pass
            else:
                spread = -hl  # goals home must win by; negative spread = home receives goals
                margin = home_goals - away_goals
                floor_s = math.floor(spread)
                frac = spread - floor_s  # [0, 1)
                if frac < 0.01:  # whole line — push at margin == spread
                    spread_int = round(spread)
                    if sel_team == "home":
                        if margin > spread_int:
                            won = True
                        elif margin == spread_int:
                            won = None  # push → void (stake returned)
                    else:  # away
                        if margin < spread_int:
                            won = True
                        elif margin == spread_int:
                            won = None  # push → void
                else:
                    # Half or quarter line — strict comparison, no push
                    if sel_team == "home":
                        won = margin > spread
                    else:
                        won = margin < spread

    elif market == "draw_no_bet":
        # Draw → void (stake returned); home/away win → won/lost as normal
        home_wins = home_goals > away_goals
        draw = home_goals == away_goals
        away_wins = away_goals > home_goals
        if draw:
            won = None
        elif selection == "home":
            won = home_wins
        else:  # away
            won = away_wins

    if won is None:
        pnl = 0.0  # push — stake returned
    else:
        pnl = round((odds - 1) * stake if won else -stake, 2)

    # CLV: positive = we got better odds than closing line
    clv = None
    if closing_odds and closing_odds > 0:
        clv = round((float(odds) / float(closing_odds)) - 1, 4)

    return {
        "result": "void" if won is None else ("won" if won else "lost"),
        "pnl": pnl,
        "clv": clv,
    }


# ─── Closing odds lookup ─────────────────────────────────────────────────────

def get_closing_odds(match_id: str, market: str, selection: str) -> float | None:
    """Get the closing odds for a match/market/selection from odds_snapshots"""
    if market == "asian_handicap":
        # selection = "home -1.25" or "away +0.5" — parse team + handicap_line
        parts = selection.split(" ", 1)
        if len(parts) == 2:
            sel_team, hl_str = parts[0], parts[1]
            try:
                hl = float(hl_str)
            except ValueError:
                return None
            result = execute_query(
                "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
                "AND selection = %s AND handicap_line = %s AND is_closing = TRUE "
                "ORDER BY timestamp DESC LIMIT 1",
                [match_id, market, sel_team, hl]
            )
            if result:
                return float(result[0]["odds"])
            result2 = execute_query(
                "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
                "AND selection = %s AND handicap_line = %s ORDER BY timestamp DESC LIMIT 1",
                [match_id, market, sel_team, hl]
            )
            return float(result2[0]["odds"]) if result2 else None
        return None

    result = execute_query(
        "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
        "AND selection = %s AND is_closing = TRUE ORDER BY timestamp DESC LIMIT 1",
        [match_id, market, selection]
    )
    if result:
        return float(result[0]["odds"])

    # Fallback: use the latest snapshot (closest to closing)
    result2 = execute_query(
        "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
        "AND selection = %s ORDER BY timestamp DESC LIMIT 1",
        [match_id, market, selection]
    )
    return float(result2[0]["odds"]) if result2 else None


def get_pinnacle_closing_odds(match_id: str, market: str, selection: str) -> float | None:
    """PIN-5: Get Pinnacle-specific closing odds for clv_pinnacle calculation.
    Pinnacle CLV is the industry-standard bet model validator — consistently
    positive = finding edge before sharp money moves the line.
    Falls back to latest Pinnacle snapshot if is_closing not marked."""
    result = execute_query(
        "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
        "AND selection = %s AND bookmaker = 'Pinnacle' AND is_closing = TRUE "
        "ORDER BY timestamp DESC LIMIT 1",
        [match_id, market, selection]
    )
    if result:
        return float(result[0]["odds"])

    result2 = execute_query(
        "SELECT odds FROM odds_snapshots WHERE match_id = %s AND market = %s "
        "AND selection = %s AND bookmaker = 'Pinnacle' ORDER BY timestamp DESC LIMIT 1",
        [match_id, market, selection]
    )
    return float(result2[0]["odds"]) if result2 else None


# ─── Post-match enrichment (T4, T8, T12) ─────────────────────────────────────

def fetch_post_match_enrichment() -> dict:
    """
    T4: Half-time stats, T8: Match events, T12: Player stats.
    Runs after settlement for recently finished matches.

    Skips matches already enriched (match_stats row exists) — idempotent.
    Uses ThreadPoolExecutor to parallelize API calls (4 concurrent).
    Returns counts dict.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from workers.api_clients.api_football import (
        get_fixtures_batch,
        get_fixture_statistics, parse_fixture_stats,
        get_fixture_statistics_halftime, parse_fixture_stats_halftime,
        get_fixture_events, parse_fixture_events,
        get_fixture_players, parse_fixture_players,
    )

    counts = {"stats": 0, "halftime": 0, "events": 0, "players": 0, "skipped": 0}

    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get recently finished matches with AF IDs
    db_finished = execute_query(
        "SELECT id, api_football_id FROM matches WHERE status = 'finished' "
        "AND date >= %s AND date <= %s",
        [f"{yesterday_str}T00:00:00", f"{today_str}T23:59:59"]
    )

    if not db_finished:
        return counts

    all_match_ids = [m["id"] for m in db_finished]

    # Batch query: which matches already have stats
    existing_stats = execute_query(
        "SELECT match_id FROM match_stats WHERE match_id = ANY(%s::uuid[])",
        [all_match_ids]
    )
    match_ids_with_stats = {r["match_id"] for r in existing_stats}

    # Batch query: look up home_team_api_id from match_injuries for all matches
    inj_rows = execute_query(
        "SELECT match_id, team_api_id FROM match_injuries "
        "WHERE match_id = ANY(%s::uuid[]) AND team_side = 'home'",
        [all_match_ids]
    )
    home_api_id_by_match: dict[str, int] = {
        r["match_id"]: r["team_api_id"] for r in inj_rows if r.get("team_api_id")
    }

    # Filter to matches that need enrichment
    to_enrich = []
    for match in db_finished:
        af_id = match.get("api_football_id")
        if not af_id:
            continue
        if match["id"] in match_ids_with_stats:
            counts["skipped"] += 1
            continue
        to_enrich.append(match)

    # Batch-fetch all fixture data upfront (ceil(N/20) API calls instead of 4N).
    # Each fixture in the batch response includes nested statistics, events,
    # lineups, and players — so threads just parse pre-fetched data.
    af_ids_to_enrich = [m["api_football_id"] for m in to_enrich]
    prefetched: dict[int, dict] = {}
    if af_ids_to_enrich:
        try:
            prefetched = get_fixtures_batch(af_ids_to_enrich)
            console.print(f"  Batch-fetched {len(prefetched)}/{len(af_ids_to_enrich)} fixtures")
        except Exception as e:
            console.print(f"  [yellow]Batch fetch failed, will fall back per-fixture: {e}[/yellow]")

    def _enrich_one_match(match: dict) -> dict:
        """Enrich a single match — runs in a thread. Uses pre-fetched batch data where available."""
        af_id = match["api_football_id"]
        match_id = match["id"]
        home_api_id = home_api_id_by_match.get(match_id)
        result = {"stats": 0, "halftime": 0, "events": 0, "players": 0}
        batch_fix = prefetched.get(af_id)

        # T4 + Full stats — use batch data if available, fall back to individual call
        try:
            if batch_fix and batch_fix.get("statistics"):
                raw_full = batch_fix["statistics"]
            else:
                raw_full = get_fixture_statistics(af_id)
            full_stats = parse_fixture_stats(raw_full)

            ht_response = get_fixture_statistics_halftime(af_id)
            ht_stats = parse_fixture_stats_halftime(ht_response)

            merged_stats = {**full_stats, **ht_stats}
            if merged_stats:
                store_match_stats_full(match_id, merged_stats)
                result["stats"] = 1
                if ht_stats:
                    result["halftime"] = 1
        except Exception as e:
            console.print(f"    [yellow]Stats error for fixture {af_id}: {e}[/yellow]")

        # T8: Match events — use batch data if available
        try:
            if batch_fix and batch_fix.get("events"):
                raw_events = batch_fix["events"]
            else:
                raw_events = get_fixture_events(af_id)
            parsed_events = parse_fixture_events(raw_events)
            if parsed_events:
                result["events"] = store_match_events_af(
                    match_id, parsed_events, home_team_api_id=home_api_id
                )
        except Exception as e:
            console.print(f"    [yellow]Events error for fixture {af_id}: {e}[/yellow]")

        # T12: Player stats — use batch data if available
        try:
            if batch_fix and batch_fix.get("players"):
                raw_players = batch_fix["players"]
            else:
                raw_players = get_fixture_players(af_id)
            parsed_players = parse_fixture_players(
                raw_players, home_team_api_id=home_api_id
            )
            if parsed_players:
                result["players"] = store_match_player_stats(match_id, af_id, parsed_players)
        except Exception as e:
            console.print(f"    [yellow]Player stats error for fixture {af_id}: {e}[/yellow]")

        return result

    # Run enrichment in parallel (2 threads — bounds DB conn fan-out).
    # Each thread can hold up to 3 conns simultaneously (stats + events + player_stats
    # writes), so 2 threads × 3 = worst-case 6 conns from this function. With 4 threads
    # the worst case was 12, which combined with LivePoller and APScheduler workers
    # could blow past the 20-conn pool. AF rate limits are not the binding constraint.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_enrich_one_match, m): m for m in to_enrich}
        for future in as_completed(futures):
            try:
                r = future.result()
                counts["stats"] += r["stats"]
                counts["halftime"] += r["halftime"]
                counts["events"] += r["events"]
                counts["players"] += r["players"]
            except Exception:
                pass

    return counts


# ─── Per-match settlement (called by live poller on FT) ──────────────────────

def settle_finished_matches(match_ids: list[str]):
    """
    Settle bets for specific finished matches. Called by the live poller
    immediately when it detects FT/AET/PEN, so bets are settled in real-time
    instead of waiting for the 21:00 UTC bulk settlement.

    Also called by settle_ready_matches() (15-min sweep) for any match the
    live poller missed (outside 10-23 UTC window, or if it errored).

    Always marks settlement_status = 'done' at the end, even if there were
    no pending bets, so the sweep doesn't re-visit the same match.
    """
    if not match_ids:
        return

    # Get pending bets for these specific matches (with match + team info via JOIN)
    pending = execute_query(
        _PENDING_BETS_SQL + " AND sb.match_id = ANY(%s::uuid[])",
        [match_ids]
    )

    if not pending:
        # Still settle user picks even if no bot bets
        try:
            _settle_user_picks_for_matches(match_ids)
        except Exception:
            pass
    else:
        console.print(f"[cyan]Live settlement: {len(pending)} pending bets "
                      f"for {len(match_ids)} finished match(es)[/cyan]")
        _settle_pending_bets(pending, finished=[])

        # Also settle user picks for these matches
        try:
            _settle_user_picks_for_matches(match_ids)
        except Exception as e:
            console.print(f"  [yellow]User picks settlement error: {e}[/yellow]")

    # SELF-USE-VALIDATION: settle any superadmin real-money bets on the same cadence.
    try:
        _settle_real_bets_for_matches(match_ids)
    except Exception as e:
        console.print(f"  [yellow]Real-bet settlement error: {e}[/yellow]")

    # Mark settled regardless of whether there were any pending bets/picks.
    # This stops the 15-min sweep from re-querying the same finished matches.
    execute_write(
        "UPDATE matches SET settlement_status = 'done' WHERE id = ANY(%s::uuid[])",
        [match_ids]
    )


def _settle_real_bets_for_matches(match_ids: list[str]):
    """SELF-USE-VALIDATION Phase 2.2 — settle real_bets for finished matches.

    Mirrors _settle_simulated_bets / settle_finished_matches semantics but
    against the real_bets table. Real bets carry actual taken odds in
    `actual_odds` (vs simulated_bets' `odds_at_pick`); we feed it into the
    same settle_bet_result() by aliasing.
    """
    if not match_ids:
        return

    pending = execute_query(
        """SELECT rb.id, rb.match_id, rb.market, rb.selection,
                  rb.actual_odds AS odds_at_pick, rb.stake,
                  m.score_home, m.score_away
           FROM real_bets rb
           JOIN matches m ON m.id = rb.match_id
           WHERE rb.result = 'pending'
             AND rb.match_id = ANY(%s::uuid[])
             AND m.status = 'finished'
             AND m.score_home IS NOT NULL
             AND m.score_away IS NOT NULL""",
        [match_ids],
    )
    if not pending:
        return

    settled = 0
    for bet in pending:
        try:
            outcome = settle_bet_result(
                bet,
                int(bet["score_home"]),
                int(bet["score_away"]),
                None,  # CLV not tracked on real_bets — taken price IS the closing line for our purposes
            )
            execute_write(
                """UPDATE real_bets
                   SET result = %s,
                       pnl = %s,
                       resolved_at = NOW()
                   WHERE id = %s""",
                [outcome["result"], outcome["pnl"], bet["id"]],
            )
            settled += 1
        except Exception as e:
            console.print(f"[yellow]Real-bet settle error for {bet['id']}: {e}[/yellow]")

    if settled:
        console.print(f"[green]Settled {settled} real bet(s) across {len(match_ids)} match(es)[/green]")


def fix_stale_live_matches():
    """
    Detect matches stuck on status='live' OR 'scheduled' that have actually finished.

    The live poller's fetch_live_bulk() only returns fixtures with status
    1H/2H/HT. If the poller misses a match entirely (e.g. Railway restart,
    stale deploy, race condition at startup), the DB status stays 'scheduled'
    forever. This function catches both cases:
      1. Finding matches with status IN ('live','scheduled') kicked off >130
         minutes ago (90 min + 40 min buffer for extra time / delays).
      2. Fetching each fixture individually from AF API to get its real status.
      3. Updating the DB to 'finished' with the final score.

    Called by settle_ready_matches() so it runs on the same 15-min cadence.
    """
    from workers.api_clients.api_football import get_fixture_by_id
    from workers.api_clients.supabase_client import update_match_result

    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=130)

    rows = execute_query(
        """SELECT m.id, m.api_football_id, m.status
           FROM matches m
           WHERE m.status IN ('live', 'scheduled')
             AND m.date < %s
             AND m.api_football_id IS NOT NULL""",
        [stale_cutoff.isoformat()],
    )

    if not rows:
        return

    live_count = sum(1 for r in rows if r["status"] == "live")
    sched_count = sum(1 for r in rows if r["status"] == "scheduled")
    console.print(f"[yellow]Stale-match check: {len(rows)} match(es) overdue "
                  f"({live_count} live, {sched_count} scheduled) — querying AF API[/yellow]")
    fixed = 0
    for row in rows:
        match_id = row["id"]
        af_id = row["api_football_id"]
        db_status = row["status"]
        try:
            fixture = get_fixture_by_id(int(af_id))
            if not fixture:
                continue
            status_short = fixture.get("fixture", {}).get("status", {}).get("short", "")
            if status_short in ("FT", "AET", "PEN", "ABD", "WO"):
                goals = fixture.get("goals", {})
                home_goals = goals.get("home")
                away_goals = goals.get("away")
                if home_goals is None or away_goals is None:
                    # ABD/WO with no score — mark finished with 0-0
                    if status_short in ("ABD", "WO"):
                        home_goals, away_goals = 0, 0
                    else:
                        continue
                update_match_result(match_id, int(home_goals), int(away_goals))
                console.print(f"[green]Fixed stale match {match_id} ({db_status}→finished): "
                              f"{status_short} {home_goals}-{away_goals}[/green]")
                fixed += 1
            elif status_short in ("PST", "CANC", "SUSP", "AWD", "INT"):
                # Postponed/cancelled — remove from live/scheduled without a result.
                # SETTLE-VOID-POSTPONED: also void any pending paper bets on the
                # match. Without this, bets sit as result='pending' forever and
                # show up on bot detail pages as open positions on a fixture
                # that will never resolve.
                execute_write(
                    "UPDATE matches SET status='postponed' WHERE id=%s",
                    [match_id],
                )
                voided = execute_write(
                    """UPDATE simulated_bets
                       SET result='void', pnl=0
                       WHERE match_id=%s AND result='pending'""",
                    [match_id],
                )
                msg = f"[yellow]Stale match {match_id} ({db_status}→postponed): {status_short}"
                if voided:
                    msg += f" — voided {voided} pending bet(s)"
                console.print(msg + "[/yellow]")
                fixed += 1
        except Exception as e:
            console.print(f"[red]Stale-match fix error for {match_id}: {e}[/red]")

    if fixed:
        console.print(f"[green]Stale-match check: fixed {fixed} match(es)[/green]")


def settle_ready_matches():
    """
    Lightweight catch-all settlement sweep — runs every 15 minutes.

    Settles bets for any finished match that has not yet been marked 'done'.
    This catches two cases the live poller can't handle:
      1. settlement_status = 'ready': live poller detected FT but the inline
         settle_finished_matches() call errored (exception was swallowed).
      2. settlement_status = 'none': match finished outside the 10-23 UTC live
         window (e.g. very early Asian matches, or late night games after 23:00),
         or the match was written as 'finished' by the bulk settlement run but
         no subsequent per-match settlement was ever triggered.

    Safe to run while the live poller is also running: settle_finished_matches()
    only touches bets with result='pending', and the final UPDATE to 'done' is
    idempotent.
    """
    # First: fix any matches stuck on 'live' that have actually finished
    fix_stale_live_matches()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    rows = execute_query(
        """SELECT id FROM matches
           WHERE status = 'finished'
             AND settlement_status IS DISTINCT FROM 'done'
             AND date >= %s AND date <= %s""",
        [f"{yesterday}T00:00:00", f"{today}T23:59:59"]
    )

    if not rows:
        console.print("[dim]Settle-ready sweep: nothing to do.[/dim]")
        return

    match_ids = [r["id"] for r in rows]
    console.print(f"[cyan]Settle-ready sweep: {len(match_ids)} match(es) need settlement[/cyan]")
    settle_finished_matches(match_ids)


def _settle_user_picks_for_matches(match_ids: list[str]):
    """Settle user picks for specific finished matches."""
    picks = execute_query(
        """SELECT up.id, up.match_id, up.selection, up.odds,
                  m.score_home, m.score_away, m.result as match_result, m.status as match_status
           FROM user_picks up
           LEFT JOIN matches m ON up.match_id = m.id
           WHERE up.result = 'pending' AND up.match_id = ANY(%s::uuid[])""",
        [match_ids]
    )

    settled = 0
    for pick in picks:
        if pick.get("match_status") != "finished":
            continue
        score_home = pick.get("score_home")
        score_away = pick.get("score_away")
        if score_home is None or score_away is None:
            continue

        selection = pick["selection"].lower()
        match_result = pick.get("match_result", "").lower()
        if selection in ("home", "draw", "away") and match_result:
            won = selection == match_result
            execute_write(
                "UPDATE user_picks SET result = %s, resolved_at = %s WHERE id = %s",
                ["won" if won else "lost", datetime.now(timezone.utc).isoformat(), pick["id"]]
            )
            settled += 1

    if settled:
        console.print(f"  {settled} user picks settled (live)")


# ─── Main settlement ──────────────────────────────────────────────────────────

def run_settlement():
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    console.print(f"[bold green]═══ OddsIntel Settlement: {today} ═══[/bold green]\n")

    # 1. Get pending bets with match info (may be empty — that's fine)
    console.print("[cyan]Loading pending bets...[/cyan]")
    pending = execute_query(_PENDING_BETS_SQL, [])
    console.print(f"  {len(pending)} pending bets")

    # 2. Determine which dates to fetch results for.
    # Always include today + yesterday to catch late finishes.
    # Also include any dates that have pending bets.
    fetch_dates = {today, yesterday}
    for bet in pending:
        m_date = bet.get("m_date")
        if m_date:
            fetch_dates.add(str(m_date)[:10])

    # 2a. API-Football as primary source (paid, reliable, 1236 leagues)
    console.print(f"\n[cyan]Fetching results from API-Football for {len(fetch_dates)} date(s)...[/cyan]")
    finished = []
    try:
        for d in sorted(fetch_dates):
            af_results = get_api_football_results(d)
            console.print(f"  {d}: {len(af_results)} finished matches from API-Football")
            finished.extend(af_results)
    except Exception as e:
        console.print(f"  [yellow]API-Football error: {e}[/yellow]")

    # 2b. ESPN as backup (free, no auth)
    if len(finished) < 10:
        console.print("[cyan]Trying ESPN as backup...[/cyan]")
        for d in sorted(fetch_dates):
            espn_results = get_finished_matches_espn(d)
            day_finished = [r for r in espn_results
                            if r.get("status") == "FT"
                            and r.get("home_goals") is not None]
            if day_finished:
                console.print(f"  {d}: {len(day_finished)} from ESPN")
                finished.extend(day_finished)

    console.print(f"  [bold]{len(finished)} total finished matches[/bold]")

    if not finished:
        console.print("[yellow]No finished matches found from any source. Try again later.[/yellow]")
        return

    # 3. Update ALL match results in DB — not just bet matches.
    # Match by api_football_id (direct, reliable) with team-name fallback.
    console.print("\n[cyan]Updating all match results in Supabase...[/cyan]")
    db_updated = 0
    db_skipped = 0

    # Build lookup: api_football_id -> result row
    af_id_to_result = {
        int(r["api_football_id"]): r
        for r in finished
        if r.get("api_football_id") and r.get("home_goals") is not None
    }

    # Fetch all DB matches for the fetch window (today + yesterday + bet dates)
    date_min = min(fetch_dates)
    date_max = max(fetch_dates)
    db_matches = execute_query(
        "SELECT id, api_football_id, home_team_id, away_team_id, status FROM matches "
        "WHERE date >= %s AND date <= %s",
        [f"{date_min}T00:00:00", f"{date_max}T23:59:59"]
    )

    # Pre-load all team names in one batch query
    all_team_ids = set()
    for m in db_matches:
        all_team_ids.add(m["home_team_id"])
        all_team_ids.add(m["away_team_id"])
    team_name_map: dict[str, str] = {}
    if all_team_ids:
        tr = execute_query(
            "SELECT id::text, name FROM teams WHERE id = ANY(%s::uuid[])",
            [list(all_team_ids)]
        )
        team_name_map = {t["id"]: t["name"] for t in tr}

    db_already_finished = 0
    for db_match in db_matches:
        if db_match.get("status") == "finished":
            db_already_finished += 1
            continue  # live tracker already settled this

        result_row = None

        # Primary: match by api_football_id
        af_id = db_match.get("api_football_id")
        if af_id and int(af_id) in af_id_to_result:
            result_row = af_id_to_result[int(af_id)]

        # Fallback: team name lookup (for ESPN-sourced results)
        if not result_row:
            home_name = team_name_map.get(db_match["home_team_id"])
            away_name = team_name_map.get(db_match["away_team_id"])
            if home_name and away_name:
                result_row = find_result_for_match(home_name, away_name, finished)

        if not result_row:
            db_skipped += 1
            continue

        hg = int(result_row["home_goals"])
        ag = int(result_row["away_goals"])
        result_str = "home" if hg > ag else "away" if ag > hg else "draw"
        execute_write(
            "UPDATE matches SET score_home = %s, score_away = %s, result = %s, status = %s WHERE id = %s",
            [hg, ag, result_str, "finished", db_match["id"]]
        )
        db_updated += 1

    console.print(f"  {db_updated} matches updated | {db_already_finished} already settled by live tracker | {db_skipped} no result yet (unplayed or outside AF coverage)")

    # 4. Settle each bet (skip gracefully if none pending)
    if not pending:
        console.print("\n[yellow]No pending bets to settle — skipping bet settlement.[/yellow]")
    else:
        _settle_pending_bets(pending, finished)

    # 4b. Settle user picks (frontend prediction tracker)
    try:
        _settle_user_picks()
    except Exception as e:
        console.print(f"  [yellow]User picks settlement error: {e}[/yellow]")

    # 4c. BET-TIMING-MONITOR — settle shadow_bets (parallel table, no bankroll).
    # Wrapped in its own try block: a shadow-settlement failure must NEVER block
    # the rest of run_settlement (real-bet settlement already succeeded above).
    try:
        shadow_pending = execute_query(_PENDING_SHADOW_BETS_SQL, [])
        if shadow_pending:
            _settle_pending_shadow_bets(shadow_pending, finished)
    except Exception as e:
        console.print(f"  [yellow]Shadow settlement error: {e}[/yellow]")

    # Post-match enrichment and analytics always run (not gated on bets)

    # P1.3: Update ELO ratings for all finished matches
    console.print("\n[cyan]Updating ELO ratings...[/cyan]")
    try:
        elo_count = update_elo_ratings()
        console.print(f"  {elo_count} team ratings updated")
    except Exception as e:
        console.print(f"  [yellow]ELO update error: {e}[/yellow]")

    # P1.4: Aggregate model evaluations
    console.print("[cyan]Computing model evaluations...[/cyan]")
    try:
        eval_count = compute_model_evaluations()
        console.print(f"  {eval_count} evaluation records stored")
    except Exception as e:
        console.print(f"  [yellow]Model evaluation error: {e}[/yellow]")

    # P1.5: Update form cache for teams that played
    console.print("[cyan]Updating team form cache...[/cyan]")
    try:
        form_count = update_team_form_cache()
        console.print(f"  {form_count} team forms updated")
    except Exception as e:
        console.print(f"  [yellow]Form cache error: {e}[/yellow]")

    # T4/T8/T12: Post-match enrichment (stats, half-time, events, player stats)
    console.print("[cyan]Fetching post-match enrichment (T4/T8/T12)...[/cyan]")
    try:
        enrichment_counts = fetch_post_match_enrichment()
        console.print(
            f"  {enrichment_counts['stats']} match stats | "
            f"{enrichment_counts['halftime']} with half-time | "
            f"{enrichment_counts['events']} events | "
            f"{enrichment_counts['players']} player stat rows | "
            f"{enrichment_counts.get('skipped', 0)} already enriched (skipped)"
        )
    except Exception as e:
        console.print(f"  [yellow]Post-match enrichment error: {e}[/yellow]")

    # 11.4a: Rebuild referee_stats from all finished matches so tomorrow's signals
    # have up-to-date cards_per_game / home_win_pct / over_25_pct.
    console.print("\n[cyan]Rebuilding referee stats...[/cyan]")
    try:
        n_refs = build_referee_stats()
        console.print(f"  {n_refs} referee records upserted")
    except Exception as e:
        console.print(f"  [yellow]Referee stats rebuild error (non-critical): {e}[/yellow]")

    # 11.4: Daily post-mortem LLM analysis
    # Note: run unconditionally — settle_ready_matches() settles bets every 15min
    # so by 21:00 UTC pending is often empty, but there are still losses to analyse.
    # run_post_mortem() has its own dedup guard (skips if already ran today).
    console.print("\n[cyan]Running AI post-mortem analysis...[/cyan]")
    try:
        run_post_mortem()
    except Exception as e:
        console.print(f"  [yellow]Post-mortem error (non-critical): {e}[/yellow]")

    # Write pre-computed stats to dashboard_cache for fast frontend loads
    write_dashboard_cache()

    # Mark all finished matches in the settlement window as done.
    # This is the bulk run's safety net: any match that slipped through
    # the live poller or the 15-min sweep gets marked here.
    try:
        execute_write(
            """UPDATE matches SET settlement_status = 'done'
               WHERE status = 'finished'
                 AND settlement_status IS DISTINCT FROM 'done'
                 AND date >= %s AND date <= %s""",
            [f"{date_min}T00:00:00", f"{date_max}T23:59:59"]
        )
    except Exception as e:
        console.print(f"  [yellow]settlement_status cleanup error: {e}[/yellow]")

    console.print("\n[bold green]Core settlement complete.[/bold green]")

    from workers.api_clients.supabase_client import write_ops_snapshot
    write_ops_snapshot(today)


def _compute_pseudo_clv_batched(fetch_dates: list[str]) -> tuple[int, int]:
    """
    Compute pseudo-CLV for all finished matches in the given dates.
    Bulk-loads all odds_snapshots, computes in-memory, batch-updates matches.
    Returns (computed_count, skipped_count).
    """
    # Get all finished match IDs for these dates
    all_match_ids = []
    for d in sorted(fetch_dates):
        rows = execute_query(
            "SELECT id FROM matches WHERE status = 'finished' AND date >= %s AND date <= %s",
            [f"{d}T00:00:00", f"{d}T23:59:59"]
        )
        all_match_ids.extend(r["id"] for r in rows)

    if not all_match_ids:
        return 0, 0

    # Bulk-load all 1x2 odds snapshots for these matches
    odds_rows = execute_query(
        "SELECT match_id, selection, odds, timestamp, is_closing FROM odds_snapshots "
        "WHERE match_id = ANY(%s::uuid[]) AND market = '1x2' ORDER BY timestamp ASC",
        [all_match_ids]
    )
    odds_by_match: dict[str, list] = {}
    for row in odds_rows:
        odds_by_match.setdefault(str(row["match_id"]), []).append(row)

    # Compute pseudo-CLV in-memory
    computed = 0
    skipped = 0

    for match_id in all_match_ids:
        snaps = odds_by_match.get(str(match_id), [])
        if not snaps:
            skipped += 1
            continue

        # Group by selection
        by_sel: dict[str, list] = {}
        for s in snaps:
            by_sel.setdefault(s["selection"].lower(), []).append(s)

        pseudo_clvs = {}
        for sel in ("home", "draw", "away"):
            sel_snaps = by_sel.get(sel, [])
            if len(sel_snaps) < 2:
                pseudo_clvs[sel] = None
                continue

            opening_odds = float(sel_snaps[0]["odds"])
            closing_snaps = [s for s in sel_snaps if s.get("is_closing")]
            closing_odds = float(closing_snaps[-1]["odds"]) if closing_snaps else float(sel_snaps[-1]["odds"])

            if opening_odds <= 1.0 or closing_odds <= 1.0:
                pseudo_clvs[sel] = None
                continue

            pseudo_clvs[sel] = round((1.0 / opening_odds) / (1.0 / closing_odds) - 1, 5)

        if all(v is None for v in pseudo_clvs.values()):
            skipped += 1
            continue

        try:
            execute_write(
                "UPDATE matches SET pseudo_clv_home = %s, pseudo_clv_draw = %s, pseudo_clv_away = %s WHERE id = %s",
                [pseudo_clvs.get("home"), pseudo_clvs.get("draw"), pseudo_clvs.get("away"), match_id]
            )
            computed += 1
        except Exception:
            pass

    return computed, skipped


def write_dashboard_cache():
    """
    Pre-compute all dashboard stats and write to dashboard_cache table.
    Called at end of settlement (21:00 UTC). Frontend reads latest row — fast.
    """
    console.print("[cyan]Writing dashboard cache...[/cyan]")
    try:
        # Bot performance — voids are excluded from settled/won/staked/pnl/clv.
        # Void rows retain their original pnl/stake (we only flip `result`), so any
        # `result != 'pending'` filter would silently double-count voided bets.
        bot_rows = execute_query("""
            SELECT
                b.name,
                COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
                COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as total_pnl,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as total_staked,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
            FROM bots b
            LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
            WHERE b.is_active = true
              AND b.retired_at IS NULL
            GROUP BY b.id, b.name
        """, [])

        total_bets = execute_query("SELECT COUNT(*) as n FROM simulated_bets WHERE result != 'void'", [])[0]["n"]
        settled_bets = execute_query("SELECT COUNT(*) as n FROM simulated_bets WHERE result IN ('won','lost')", [])[0]["n"]
        pending_bets = int(total_bets) - int(settled_bets)
        won = execute_query("SELECT COUNT(*) as n FROM simulated_bets WHERE result = 'won'", [])[0]["n"]
        lost = execute_query("SELECT COUNT(*) as n FROM simulated_bets WHERE result = 'lost'", [])[0]["n"]
        staked_row = execute_query("SELECT SUM(stake) as s, SUM(pnl) as p, AVG(clv) as c FROM simulated_bets WHERE result IN ('won','lost')", [])[0]
        total_staked = float(staked_row["s"] or 0)
        total_pnl = float(staked_row["p"] or 0)
        avg_clv = float(staked_row["c"] or 0) if staked_row["c"] else None
        hit_rate = (int(won) / int(settled_bets) * 100) if int(settled_bets) > 0 else None
        roi_pct = (total_pnl / total_staked * 100) if total_staked > 0 and int(settled_bets) > 0 else None

        bot_breakdown = []
        for r in bot_rows:
            s = int(r.get("settled") or 0)
            w = int(r.get("won") or 0)
            p = float(r.get("total_pnl") or 0)
            st = float(r.get("total_staked") or 0)
            bot_breakdown.append({
                "name": r["name"],
                "settled": s,
                "won": w,
                "total_pnl": round(p, 2),
                "roi_pct": round(p / st * 100, 1) if st > 0 and s > 0 else None,
                "avg_clv": round(float(r["avg_clv"]), 4) if r.get("avg_clv") else None,
            })

        market_rows = execute_query("""
            SELECT market,
                COUNT(*) FILTER (WHERE result IN ('won','lost')) as bets,
                COUNT(*) FILTER (WHERE result = 'won') as won,
                AVG(clv) FILTER (WHERE result IN ('won','lost') AND clv IS NOT NULL) as avg_clv
            FROM simulated_bets
            GROUP BY market ORDER BY bets DESC
        """, [])
        market_breakdown = [
            {"market": r["market"], "bets": int(r["bets"] or 0), "won": int(r["won"] or 0),
             "avg_clv": round(float(r["avg_clv"]), 4) if r.get("avg_clv") else None}
            for r in market_rows
        ]

        # Model accuracy (simple: % of matches where highest ensemble prob = actual result)
        acc_row = execute_query("""
            SELECT
                COUNT(*) as n,
                SUM(CASE
                    WHEN m.result = 'home' AND p1.model_probability >= p2.model_probability AND p1.model_probability >= p3.model_probability THEN 1
                    WHEN m.result = 'draw' AND p2.model_probability >= p1.model_probability AND p2.model_probability >= p3.model_probability THEN 1
                    WHEN m.result = 'away' AND p3.model_probability >= p1.model_probability AND p3.model_probability >= p2.model_probability THEN 1
                    ELSE 0
                END) as correct
            FROM matches m
            JOIN predictions p1 ON p1.match_id = m.id AND p1.market = '1x2_home' AND p1.source = 'ensemble'
            JOIN predictions p2 ON p2.match_id = m.id AND p2.market = '1x2_draw' AND p2.source = 'ensemble'
            JOIN predictions p3 ON p3.match_id = m.id AND p3.market = '1x2_away' AND p3.source = 'ensemble'
            WHERE m.status = 'finished' AND m.result IS NOT NULL
        """, [])
        acc = acc_row[0] if acc_row else {}
        n = int(acc.get("n") or 0)
        correct = int(acc.get("correct") or 0)
        model_accuracy_pct = round(correct / n * 100, 1) if n > 0 else None

        # Data accumulation counts
        pseudo_clv_count = execute_query("SELECT COUNT(*) as n FROM matches WHERE status='finished' AND pseudo_clv_home IS NOT NULL", [])[0]["n"]
        live_snapshot_matches = execute_query("SELECT COUNT(DISTINCT match_id) as n FROM live_match_snapshots", [])[0]["n"]
        alignment_settled = execute_query("SELECT COUNT(*) as n FROM simulated_bets WHERE result IN ('won','lost') AND alignment_class IS NOT NULL", [])[0]["n"]

        import json
        execute_write("""
            INSERT INTO dashboard_cache (
                total_bets, settled_bets, pending_bets, won_bets, lost_bets,
                hit_rate, total_staked, total_pnl, roi_pct, avg_clv,
                bot_breakdown, market_breakdown,
                model_accuracy_pct, prediction_sample_size,
                pseudo_clv_count, live_snapshot_matches, alignment_settled_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, [
            int(total_bets), int(settled_bets), int(pending_bets), int(won), int(lost),
            hit_rate, total_staked, total_pnl, roi_pct, avg_clv,
            json.dumps(bot_breakdown), json.dumps(market_breakdown),
            model_accuracy_pct, n,
            int(pseudo_clv_count), int(live_snapshot_matches), int(alignment_settled)
        ])
        console.print(f"  Dashboard cache written: {int(settled_bets)} settled bets, accuracy={model_accuracy_pct}%")
    except Exception as e:
        console.print(f"  [yellow]Dashboard cache error (non-critical): {e}[/yellow]")
        import traceback; traceback.print_exc()


def run_ml_etl():
    """
    ML ETL phase — runs separately from core settlement.
    Computes pseudo-CLV and builds match_feature_vectors for recently finished matches.
    Split out because these are query-heavy (~10 queries/match) and can safely run later.
    """
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    console.print(f"[bold green]═══ OddsIntel ML ETL: {today} ═══[/bold green]\n")

    fetch_dates = [yesterday, today]

    # B-ML1: Compute pseudo-CLV for ALL finished matches (batched)
    console.print("[cyan]Computing pseudo-CLV for all finished matches...[/cyan]")
    try:
        pclv_count, pclv_skipped = _compute_pseudo_clv_batched(fetch_dates)
        console.print(f"  {pclv_count} pseudo-CLV computed | {pclv_skipped} skipped (insufficient odds data)")
    except Exception as e:
        console.print(f"  [yellow]Pseudo-CLV error: {e}[/yellow]")

    # B-ML2: Build match_feature_vectors wide table (ML training table)
    console.print("[cyan]Building match feature vectors...[/cyan]")
    try:
        fv_total = 0
        for d in sorted(fetch_dates):
            fv_count = build_match_feature_vectors(None, d)
            fv_total += fv_count
        console.print(f"  {fv_total} feature vector rows upserted")
    except Exception as e:
        console.print(f"  [yellow]Feature vectors error: {e}[/yellow]")

    console.print("\n[bold green]ML ETL complete.[/bold green]")


def _normalize_bet_market(market: str) -> str:
    """
    Map bet market strings (as stored in simulated_bets) to odds_snapshots market values.
    e.g. "1X2" → "1x2", "O/U" → "over_under_25"
    """
    m = market.strip().lower()
    if m in ("1x2", "1×2"):
        return "1x2"
    if m in ("o/u", "ou", "over/under"):
        return "over_under_25"
    # Already in DB format (e.g. "over_under_25")
    return m


def _normalize_bet_selection(selection: str) -> str:
    """
    Map bet selection strings (as stored in simulated_bets) to odds_snapshots selection values.
    e.g. "Home" → "home", "Over 2.5" → "over", "Under 2.5" → "under"
    """
    s = selection.strip().lower()
    if s in ("home", "h"):
        return "home"
    if s in ("away", "a"):
        return "away"
    if s in ("draw", "d", "x"):
        return "draw"
    if s.startswith("over"):
        return "over"
    if s.startswith("under"):
        return "under"
    return s


def _settle_pending_bets(pending: list, finished: list):
    """Settle all pending bets against finished match results."""
    console.print("\n[cyan]Settling bets...[/cyan]\n")

    settled = 0
    skipped = 0
    total_pnl = 0.0
    clv_values = []

    by_bot: dict[str, dict] = {}

    # Pre-load all bot bankrolls in one query
    bot_rows = execute_query("SELECT id, name, current_bankroll FROM bots", [])
    for b in bot_rows:
        by_bot[str(b["id"])] = {
            "bankroll": float(b["current_bankroll"]),
            "name": b["name"],
        }

    t = Table(title="Settlement Results")
    t.add_column("Match", style="cyan")
    t.add_column("Bet")
    t.add_column("Score")
    t.add_column("Result")
    t.add_column("P&L", justify="right")
    t.add_column("CLV", justify="right")

    for bet in pending:
        # Flat SQL row: score_home/score_away are directly on bet
        score_home = bet.get("score_home")
        score_away = bet.get("score_away")
        home_name_display = bet.get("home_team_name", "?")
        away_name_display = bet.get("away_team_name", "?")

        # If not in DB (match not yet updated), try to find in external results
        if score_home is None:
            result_match = find_result_for_match(home_name_display, away_name_display, finished)
            if not result_match:
                skipped += 1
                continue
            score_home = int(result_match["home_goals"])
            score_away = int(result_match["away_goals"])
        else:
            score_home = int(score_home)
            score_away = int(score_away)

        # Get closing odds for CLV
        match_id = bet["match_id"]
        raw_market = bet["market"]
        raw_selection = bet["selection"]
        odds_market = _normalize_bet_market(raw_market)
        odds_selection = _normalize_bet_selection(raw_selection)
        closing_odds = get_closing_odds(match_id, odds_market, odds_selection)

        # PIN-5: Pinnacle-anchored CLV — the industry-standard EV validator
        pinnacle_closing = get_pinnacle_closing_odds(match_id, odds_market, odds_selection)
        odds_at_pick = float(bet["odds_at_pick"])
        clv_pinnacle = (
            round((odds_at_pick / pinnacle_closing) - 1, 4)
            if pinnacle_closing and pinnacle_closing > 1.0
            else None
        )

        # Settle
        settlement = settle_bet_result(bet, score_home, score_away, closing_odds)

        # Bot bankroll tracking
        bot_id = str(bet["bot_id"])
        if bot_id not in by_bot:
            by_bot[bot_id] = {"bankroll": 1000.0, "name": "unknown"}

        new_bankroll = by_bot[bot_id]["bankroll"] + settlement["pnl"]
        by_bot[bot_id]["bankroll"] = new_bankroll

        # Update DB
        execute_write(
            "UPDATE simulated_bets SET result = %s, pnl = %s, bankroll_after = %s, "
            "closing_odds = %s, clv = %s, clv_pinnacle = %s WHERE id = %s",
            [settlement["result"], settlement["pnl"], new_bankroll,
             closing_odds, settlement["clv"], clv_pinnacle, bet["id"]]
        )

        settled += 1
        total_pnl += settlement["pnl"]
        if settlement["clv"] is not None:
            clv_values.append(settlement["clv"])

        result_color = "green" if settlement["result"] == "won" else "red"
        clv_str = f"{settlement['clv']:+.1%}" if settlement["clv"] is not None else "-"

        t.add_row(
            f"{home_name_display[:10]} v {away_name_display[:10]}",
            f"{raw_market} {raw_selection}",
            f"{score_home}-{score_away}",
            f"[{result_color}]{settlement['result'].upper()}[/{result_color}]",
            f"[{result_color}]{settlement['pnl']:+.2f}[/{result_color}]",
            clv_str,
        )

    # Update bot bankrolls
    for bot_id, data in by_bot.items():
        execute_write(
            "UPDATE bots SET current_bankroll = %s WHERE id = %s",
            [data["bankroll"], bot_id]
        )

    console.print(t)

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None

    console.print("\n[bold]Settlement complete:[/bold]")
    console.print(f"  Settled: {settled} | Skipped (no result): {skipped}")
    console.print(f"  Total P&L: [{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.2f}[/]")
    if avg_clv is not None:
        clv_color = "green" if avg_clv > 0 else "red"
        console.print(f"  Avg CLV: [{clv_color}]{avg_clv:+.1%}[/] ({'beating' if avg_clv > 0 else 'behind'} closing line)")

    return settled


def _settle_pending_shadow_bets(pending: list, finished: list) -> int:
    """BET-TIMING-MONITOR — settle shadow_bets against finished match results.

    Mirrors _settle_pending_bets() but: targets shadow_bets table, never touches
    bot bankrolls, no clv_pinnacle column (not needed for the timing question).
    No fancy Rich table — only a single summary line so this never crowds out
    the real settlement output.
    """
    if not pending:
        return 0

    settled = 0
    skipped = 0
    total_pnl = 0.0
    clv_values: list[float] = []

    for bet in pending:
        score_home = bet.get("score_home")
        score_away = bet.get("score_away")
        home_name_display = bet.get("home_team_name", "?")
        away_name_display = bet.get("away_team_name", "?")

        if score_home is None:
            result_match = find_result_for_match(home_name_display, away_name_display, finished)
            if not result_match:
                skipped += 1
                continue
            score_home = int(result_match["home_goals"])
            score_away = int(result_match["away_goals"])
        else:
            score_home = int(score_home)
            score_away = int(score_away)

        match_id = bet["match_id"]
        odds_market = _normalize_bet_market(bet["market"])
        odds_selection = _normalize_bet_selection(bet["selection"])
        closing_odds = get_closing_odds(match_id, odds_market, odds_selection)

        settlement = settle_bet_result(bet, score_home, score_away, closing_odds)

        try:
            execute_write(
                "UPDATE shadow_bets SET result = %s, pnl = %s, "
                "closing_odds = %s, clv = %s WHERE id = %s",
                [settlement["result"], settlement["pnl"],
                 closing_odds, settlement["clv"], bet["id"]]
            )
        except Exception as e:
            console.print(f"  [yellow]Shadow-settle error for {bet['id']}: {e}[/yellow]")
            continue

        settled += 1
        total_pnl += settlement["pnl"]
        if settlement["clv"] is not None:
            clv_values.append(settlement["clv"])

    avg_clv = (sum(clv_values) / len(clv_values)) if clv_values else None
    clv_str = f"avg_clv={avg_clv:+.1%}" if avg_clv is not None else "avg_clv=n/a"
    pnl_color = "green" if total_pnl >= 0 else "red"
    console.print(
        f"[dim]Shadow settlement: {settled} settled · {skipped} no-result · "
        f"PnL [{pnl_color}]{total_pnl:+.2f}[/{pnl_color}] · {clv_str}[/dim]"
    )
    return settled


def _settle_user_picks():
    """Settle user picks (from the frontend prediction tracker) against finished match results."""
    console.print("\n[cyan]Settling user picks...[/cyan]")

    picks = execute_query(
        """SELECT up.id, up.match_id, up.selection, up.odds,
                  m.score_home, m.score_away, m.result as match_result, m.status as match_status
           FROM user_picks up
           LEFT JOIN matches m ON up.match_id = m.id
           WHERE up.result = 'pending'""",
        []
    )

    if not picks:
        console.print("  No pending user picks.")
        return 0

    settled = 0
    skipped = 0

    for pick in picks:
        if pick.get("match_status") != "finished":
            skipped += 1
            continue

        score_home = pick.get("score_home")
        score_away = pick.get("score_away")
        if score_home is None or score_away is None:
            skipped += 1
            continue

        selection = pick["selection"].lower()
        match_result = pick.get("match_result", "").lower()

        if selection in ("home", "draw", "away") and match_result:
            won = selection == match_result
            execute_write(
                "UPDATE user_picks SET result = %s, resolved_at = %s WHERE id = %s",
                ["won" if won else "lost", datetime.now(timezone.utc).isoformat(), pick["id"]]
            )
            settled += 1
        else:
            skipped += 1

    console.print(f"  {settled} user picks settled | {skipped} skipped (match not finished)")
    return settled


def update_elo_ratings():
    """
    P1.3: Update ELO ratings for teams in recently finished matches.
    Simple ELO with K=30, home advantage +100, goal diff multiplier.
    Uses batch load + batch upsert instead of per-team queries.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get yesterday's and today's finished matches with team IDs
    finished = execute_query(
        "SELECT id, home_team_id, away_team_id, score_home, score_away FROM matches "
        "WHERE status = 'finished' AND date >= %s AND date <= %s",
        [f"{yesterday_str}T00:00:00", f"{today_str}T23:59:59"]
    )

    if not finished:
        return 0

    # Collect all involved team IDs
    team_ids = set()
    for m in finished:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    # Batch load ELO baseline from BEFORE today.
    # Deliberately excludes today's date so this function is idempotent:
    # running it twice on the same day (21:00 + 23:30 safety run) always
    # starts from the same pre-day baseline and re-computes today's ELO
    # from scratch, rather than double-applying today's match deltas.
    elo_cache: dict[str, float] = {}
    elo_rows = execute_query(
        "SELECT DISTINCT ON (team_id) team_id, elo_rating FROM team_elo_daily "
        "WHERE team_id = ANY(%s::uuid[]) AND date < %s ORDER BY team_id, date DESC",
        [list(team_ids), today_str]
    )
    for r in elo_rows:
        elo_cache[r["team_id"]] = float(r["elo_rating"])

    K = 30
    HOME_ADV = 100
    new_elo_rows = []

    for m in finished:
        if m["score_home"] is None or m["score_away"] is None:
            continue

        h_id = m["home_team_id"]
        a_id = m["away_team_id"]
        h_elo = elo_cache.get(h_id, 1500.0) + HOME_ADV
        a_elo = elo_cache.get(a_id, 1500.0)

        # Expected scores
        exp_h = 1 / (1 + 10 ** ((a_elo - h_elo) / 400))
        exp_a = 1 - exp_h

        # Actual scores
        gd = abs(m["score_home"] - m["score_away"])
        gd_mult = max(1.0, (gd + 1) ** 0.5)

        if m["score_home"] > m["score_away"]:
            actual_h, actual_a = 1.0, 0.0
        elif m["score_home"] < m["score_away"]:
            actual_h, actual_a = 0.0, 1.0
        else:
            actual_h, actual_a = 0.5, 0.5

        new_h = (elo_cache.get(h_id, 1500.0) + K * gd_mult * (actual_h - exp_h))
        new_a = (elo_cache.get(a_id, 1500.0) + K * gd_mult * (actual_a - exp_a))

        elo_cache[h_id] = new_h
        elo_cache[a_id] = new_a

        new_elo_rows.append((h_id, today_str, round(new_h, 2)))
        new_elo_rows.append((a_id, today_str, round(new_a, 2)))

    # Deduplicate: keep last computed value per team
    seen_teams: dict[str, tuple] = {}
    for row in new_elo_rows:
        seen_teams[row[0]] = row
    deduped_rows = list(seen_teams.values())

    if not deduped_rows:
        return 0

    updated = bulk_upsert(
        table="team_elo_daily",
        columns=["team_id", "date", "elo_rating"],
        rows=deduped_rows,
        conflict_columns=["team_id", "date"],
        update_columns=["elo_rating"],
    )
    return updated


def update_team_form_cache():
    """
    P1.5: Update form cache for teams that played recently.
    Computes rolling 10-match form from DB and stores in team_form_cache.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    finished = execute_query(
        "SELECT home_team_id, away_team_id FROM matches WHERE status = 'finished' "
        "AND date >= %s AND date <= %s",
        [f"{yesterday_str}T00:00:00", f"{today_str}T23:59:59"]
    )

    if not finished:
        return 0

    team_ids = set()
    for m in finished:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    updated = 0
    for tid in team_ids:
        form = compute_team_form_from_db(tid, today_str)
        if form:
            try:
                store_team_form(tid, today_str, form)
                updated += 1
            except Exception:
                pass

    return updated


def compute_model_evaluations():
    """
    P1.4: Aggregate settled bets into model_evaluations by date/market.
    Runs after all bets are settled for the day.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get recently settled bets with league info — exclude voids; they're not real outcomes.
    bets = execute_query(
        "SELECT sb.id, sb.market, sb.result, sb.pnl, sb.stake, sb.clv, m.league_id "
        "FROM simulated_bets sb "
        "LEFT JOIN matches m ON sb.match_id = m.id "
        "WHERE sb.result IN ('won','lost') AND sb.pick_time >= %s",
        [f"{yesterday_str}T00:00:00"]
    )

    if not bets:
        return 0

    # Group by market
    from collections import defaultdict
    by_market: dict[str, list] = defaultdict(list)
    for b in bets:
        by_market[b["market"]].append(b)

    # Delete today's auto-generated records before re-inserting.
    # Prevents duplicate rows when run_settlement() runs twice (21:00 + 23:30).
    # Preserves manually written rows and post_mortem records (different market keys).
    auto_markets = list(by_market.keys())
    if auto_markets:
        try:
            placeholders = ", ".join(["%s"] * len(auto_markets))
            execute_write(
                f"DELETE FROM model_evaluations WHERE date = %s AND league_id IS NULL "
                f"AND market IN ({placeholders})",
                [today_str] + auto_markets,
            )
        except Exception:
            pass

    evals_stored = 0
    for market, market_bets in by_market.items():
        total = len(market_bets)
        hits = sum(1 for b in market_bets if b["result"] == "won")
        total_stake = sum(b["stake"] for b in market_bets)
        total_pnl = sum(b["pnl"] or 0 for b in market_bets)
        roi = (total_pnl / total_stake * 100) if total_stake > 0 else 0
        clv_vals = [b["clv"] for b in market_bets if b.get("clv") is not None]
        avg_clv = sum(clv_vals) / len(clv_vals) if clv_vals else None

        try:
            store_model_evaluation(
                eval_date=today_str,
                league_id=None,
                market=market,
                total_bets=total,
                hits=hits,
                roi=roi,
                avg_clv=avg_clv,
                notes=f"Auto-generated from {total} settled bets",
            )
            evals_stored += 1
        except Exception:
            pass

    return evals_stored


def run_post_mortem():
    """
    11.4: Daily AI post-mortem analysis.
    After settlement, sends today's settled bets to Gemini for loss classification.
    Classifies each loss as: Variance, Information Gap, Model Error, or Timing.
    Stores classification in model_evaluations.notes for pattern tracking.

    Cost: ~$0.01-0.02/day (one Gemini call with batch context).
    See MODEL_ANALYSIS.md Section 11.4.
    """
    import json
    import re

    today_str = date.today().isoformat()

    # Skip if post-mortem already ran today (prevents double Gemini call at 23:30).
    already_done = execute_query(
        "SELECT id FROM model_evaluations WHERE date = %s AND market = 'post_mortem' LIMIT 1",
        [today_str],
    )
    if already_done:
        console.print("[dim]Post-mortem: already ran today — skipping.[/dim]")
        return

    # Get today's settled bets with full context
    bets = execute_query(
        """SELECT sb.id, sb.market, sb.selection, sb.odds_at_pick, sb.model_probability,
                  sb.edge_percent, sb.result, sb.pnl, sb.stake, sb.clv, sb.calibrated_prob,
                  sb.alignment_class, sb.kelly_fraction, sb.odds_drift, sb.news_impact_score,
                  sb.reasoning,
                  m.score_home, m.score_away,
                  ht.name as home_team_name, ta.name as away_team_name,
                  l.name as league_name, l.country as league_country, l.tier as league_tier
           FROM simulated_bets sb
           LEFT JOIN matches m ON sb.match_id = m.id
           LEFT JOIN teams ht ON m.home_team_id = ht.id
           LEFT JOIN teams ta ON m.away_team_id = ta.id
           LEFT JOIN leagues l ON m.league_id = l.id
           WHERE sb.result IN ('won','lost') AND sb.pick_time >= %s""",
        [f"{today_str}T00:00:00"]
    )

    if not bets:
        return

    losses = [b for b in bets if b["result"] == "lost"]
    wins = [b for b in bets if b["result"] == "won"]

    if not losses:
        console.print("  [green]No losses today — no post-mortem needed![/green]")
        return

    # Build context for LLM
    bet_summaries = []
    for b in bets:
        home_name = b.get("home_team_name", "?")
        away_name = b.get("away_team_name", "?")
        league_name = b.get("league_name", "?")
        tier = b.get("league_tier", "?")

        summary = (
            f"{'✗ LOST' if b['result'] == 'lost' else '✓ WON'}: "
            f"{home_name} vs {away_name} ({league_name}, T{tier}) "
            f"| Score: {b.get('score_home', '?')}-{b.get('score_away', '?')} "
            f"| Bet: {b['market']} {b['selection']} @{b['odds_at_pick']:.2f} "
            f"| Model prob: {b['model_probability']:.1%}"
        )
        if b.get("calibrated_prob"):
            summary += f", Cal: {b['calibrated_prob']:.1%}"
        if b.get("odds_drift") and b["odds_drift"] != 0:
            summary += f", Drift: {b['odds_drift']:+.3f}"
        if b.get("clv") is not None:
            summary += f", CLV: {b['clv']:+.1%}"
        if b.get("news_impact_score") and b["news_impact_score"] != 0:
            summary += f", News: {b['news_impact_score']:+.2f}"
        if b.get("alignment_class"):
            summary += f", Align: {b['alignment_class']}"
        bet_summaries.append(summary)

    prompt = f"""You are a sports betting analyst performing a daily post-mortem.

TODAY'S SETTLED BETS ({len(bets)} total: {len(wins)} won, {len(losses)} lost):

{chr(10).join(bet_summaries)}

For each LOST bet, classify the likely cause into exactly one category:
- VARIANCE: Model assessment was reasonable (good edge, maybe good CLV) but result went against us. Bad luck, not a model flaw.
- INFORMATION_GAP: Odds moved against us (negative drift) or news impacted the match in a way our model didn't capture. We were missing information.
- MODEL_ERROR: Model probability was significantly wrong — the team was simply not as strong/weak as predicted. The pick was bad, not unlucky.
- TIMING: The pick might have been right earlier but conditions changed (lineup, late injury). Better timing would have helped.

Also provide:
1. A one-paragraph overall assessment of today's performance
2. Any patterns you notice (e.g., "all losses were in Tier 1", "negative CLV on every loss")
3. One specific actionable suggestion for improving tomorrow

Respond with ONLY a JSON object:
{{
  "loss_classifications": [
    {{"match": "Home vs Away", "category": "VARIANCE|INFORMATION_GAP|MODEL_ERROR|TIMING", "reason": "brief explanation"}}
  ],
  "daily_summary": "one paragraph",
  "patterns_noticed": ["pattern 1", "pattern 2"],
  "suggestion": "one specific action"
}}"""

    try:
        from google import genai
        gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()

        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            analysis = json.loads(json_match.group())

            # Display results
            console.print(f"\n  [bold]Post-Mortem ({len(losses)} losses analyzed):[/bold]")

            for lc in analysis.get("loss_classifications", []):
                cat_color = {
                    "VARIANCE": "blue",
                    "INFORMATION_GAP": "yellow",
                    "MODEL_ERROR": "red",
                    "TIMING": "magenta",
                }.get(lc.get("category", ""), "white")
                console.print(f"  [{cat_color}]{lc.get('category', '?'):18s}[/{cat_color}] {lc.get('match', '?')} — {lc.get('reason', '')}")

            console.print(f"\n  [bold]Summary:[/bold] {analysis.get('daily_summary', 'N/A')}")

            patterns = analysis.get("patterns_noticed", [])
            if patterns:
                console.print("  [bold]Patterns:[/bold]")
                for p in patterns:
                    console.print(f"    • {p}")

            suggestion = analysis.get("suggestion", "")
            if suggestion:
                console.print(f"  [bold]Suggestion:[/bold] {suggestion}")

            # Store in model_evaluations
            try:
                store_model_evaluation(
                    eval_date=today_str,
                    league_id=None,
                    market="post_mortem",
                    total_bets=len(bets),
                    hits=len(wins),
                    roi=sum(b["pnl"] or 0 for b in bets) / max(sum(b["stake"] for b in bets), 1) * 100,
                    avg_clv=None,
                    notes=json.dumps(analysis, ensure_ascii=False)[:2000],
                )
            except Exception as e:
                console.print(f"  [yellow]Post-mortem store error: {e}[/yellow]")

    except Exception as e:
        console.print(f"  [yellow]Post-mortem LLM error: {e}[/yellow]")


def run_report():
    """Show cumulative P&L and CLV across all settled bets"""
    console.print("[bold]═══ OddsIntel P&L Report ═══[/bold]\n")

    bots = execute_query(
        "SELECT id, name, starting_bankroll, current_bankroll FROM bots",
        []
    )

    t = Table(title="Bot Performance")
    t.add_column("Bot", style="cyan")
    t.add_column("Bets", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Hit %", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("Bankroll", justify="right")

    for bot in bots:
        bets = execute_query(
            "SELECT result, pnl, stake, clv FROM simulated_bets "
            "WHERE bot_id = %s AND result IN ('won','lost')",
            [bot["id"]]
        )

        if not bets:
            continue

        total = len(bets)
        won = sum(1 for b in bets if b["result"] == "won")
        total_stake = sum(b["stake"] for b in bets)
        total_pnl = sum(b["pnl"] or 0 for b in bets)
        roi = total_pnl / total_stake if total_stake > 0 else 0
        clv_vals = [b["clv"] for b in bets if b.get("clv") is not None]
        avg_clv = sum(clv_vals) / len(clv_vals) if clv_vals else None

        roi_color = "green" if roi > 0 else "red"
        clv_str = f"{avg_clv:+.1%}" if avg_clv is not None else "-"

        t.add_row(
            bot["name"],
            str(total),
            str(won),
            f"{won/total:.1%}" if total else "-",
            f"[{roi_color}]{roi:+.1%}[/]",
            f"[{roi_color}]{total_pnl:+.2f}[/]",
            clv_str,
            f"{bot['current_bankroll']:.2f}",
        )

    console.print(t)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Show P&L report")
    parser.add_argument("--ml-etl", action="store_true",
                        help="Run ML ETL only (pseudo-CLV + feature vectors)")
    args = parser.parse_args()

    if args.report:
        run_report()
    elif args.ml_etl:
        run_ml_etl()
    else:
        run_settlement()
