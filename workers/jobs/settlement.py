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
import json
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
# combo_legs is included so the settle loop can dispatch combo bets through
# settle_combo_bet() instead of the single-bet path (COMBO-PHASE-D).
_PENDING_BETS_SQL = """
SELECT
    sb.id, sb.bot_id, sb.match_id, sb.market, sb.selection, sb.stake,
    sb.odds_at_pick, sb.model_probability, sb.edge_percent, sb.result,
    sb.pnl, sb.clv, sb.calibrated_prob, sb.alignment_class, sb.kelly_fraction,
    sb.odds_drift, sb.news_impact_score, sb.reasoning, sb.bankroll_after,
    sb.closing_odds, sb.pick_time, sb.combo_legs, sb.combo_size, sb.system_type,
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


def settle_combo_bet(combo_bet: dict, match_scores: dict) -> dict | None:
    """COMBO-PHASE-D: settle a multi-leg accumulator or system bet.

    Branches on `system_type`:
      • NULL or 'straight' → standard accumulator (all-win or all-lose)
      • 'no_singles'       → system bet covering all sub-combos of size 2..N
                              (Trixie/Yankee/Canadian/Heinz depending on N)

    Returns None if any leg's match hasn't finished yet (bet stays pending).

    Straight accumulator rules:
      • All legs won → won at full combined odds
      • Any leg lost → lost (-stake)
      • Voided legs → reduce combined odds, settle on remaining winners

    No-singles system rules:
      • For each sub-combo of size 2..N: if all its legs won, it pays at its
        own product odds; else it loses its share of the stake
      • Total stake split equally across sub-combos: per_sub = stake / N_subs
      • Result reported as 'won' if total_payout > total_stake, else 'lost'
        (or 'void' if every leg voided)

    match_scores: dict mapping match_id (str) → (home_goals, away_goals).
    """
    legs = combo_bet.get("combo_legs")
    if isinstance(legs, str):
        legs = json.loads(legs)
    if not legs:
        return None
    stake = float(combo_bet["stake"])
    system_type = combo_bet.get("system_type")

    # Compute each leg's outcome
    leg_results = []
    for leg in legs:
        mid = str(leg["match_id"])
        if mid not in match_scores:
            return None  # leg's match not finished yet — bet stays pending
        score_h, score_a = match_scores[mid]
        synthetic = {
            "market": leg["market"],
            "selection": leg["selection"],
            "stake": 1.0,
            "odds_at_pick": float(leg["odds"]),
        }
        leg_settled = settle_bet_result(synthetic, score_h, score_a, None)
        leg_results.append((leg, leg_settled["result"]))

    if system_type == "no_singles":
        return _settle_system_no_singles(leg_results, stake)
    if system_type == "fours_up":
        return _settle_system_fours_up(leg_results, stake)

    # Default: straight accumulator
    if any(r == "lost" for _, r in leg_results):
        return {"result": "lost", "pnl": round(-stake, 2), "clv": None}
    surviving = [(leg, r) for leg, r in leg_results if r == "won"]
    if not surviving:
        return {"result": "void", "pnl": 0.0, "clv": None}
    reduced_odds = 1.0
    for leg, _ in surviving:
        reduced_odds *= float(leg["odds"])
    pnl = round(stake * (reduced_odds - 1), 2)
    return {"result": "won", "pnl": pnl, "clv": None}


def _settle_system_no_singles(leg_results: list, total_stake: float) -> dict:
    """Settle a no-singles system bet (Trixie/Yankee/Canadian/Heinz).

    Enumerates all sub-combos of size 2..N. Per-sub-combo stake is
    total_stake / num_sub_combos. Each sub-combo wins (pays at product odds)
    only if every leg in it won (voided legs treated as not-won-not-lost:
    they reduce the sub-combo's effective product or void the sub-combo).

    Simplification used here: voided legs are dropped from any sub-combo they
    appear in. A sub-combo with only voided legs is itself voided. The
    surviving sub-combos settle on their non-voided product.
    """
    from itertools import combinations as _combos

    n_legs = len(leg_results)
    n_sub_combos = sum(math.comb(n_legs, k) for k in range(2, n_legs + 1))
    if n_sub_combos == 0:
        return {"result": "void", "pnl": 0.0, "clv": None}
    per_sub_stake = total_stake / n_sub_combos

    total_payout = 0.0   # gross payout from winning sub-combos (includes stake)
    voided_subs = 0
    for size in range(2, n_legs + 1):
        for sub in _combos(leg_results, size):
            statuses = [r for _, r in sub]
            if any(s == "lost" for s in statuses):
                continue  # this sub-combo lost its per_sub stake
            non_void = [(leg, r) for leg, r in sub if r == "won"]
            if not non_void:
                voided_subs += 1
                total_payout += per_sub_stake  # void = stake refunded
                continue
            # All non-voided legs won → sub-combo pays at product odds of the
            # winning legs only (void legs drop out, standard bookie rule)
            prod = 1.0
            for leg, _ in non_void:
                prod *= float(leg["odds"])
            total_payout += per_sub_stake * prod

    pnl = round(total_payout - total_stake, 2)
    if pnl > 0:
        result = "won"
    elif pnl < 0:
        result = "lost"
    else:
        # All sub-combos voided → stake refunded, pnl = 0
        result = "void" if voided_subs == n_sub_combos else "lost"
    return {"result": result, "pnl": pnl, "clv": None}


def _settle_system_fours_up(leg_results: list, total_stake: float) -> dict:
    """Settle a fours_up system bet: all sub-combos of size 4..N.

    For N=5: 5 four-folds + 1 five-fold = 6 tickets. Tolerates one losing leg
    (the five 4-folds that don't include the loser still pay). Same void logic
    as no_singles: voided legs drop out of each sub-combo.
    """
    from itertools import combinations as _combos

    n_legs = len(leg_results)
    min_size = min(4, n_legs)
    n_sub_combos = sum(math.comb(n_legs, k) for k in range(min_size, n_legs + 1))
    if n_sub_combos == 0:
        return {"result": "void", "pnl": 0.0, "clv": None}
    per_sub_stake = total_stake / n_sub_combos

    total_payout = 0.0
    voided_subs = 0
    for size in range(min_size, n_legs + 1):
        for sub in _combos(leg_results, size):
            statuses = [r for _, r in sub]
            if any(s == "lost" for s in statuses):
                continue
            non_void = [(leg, r) for leg, r in sub if r == "won"]
            if not non_void:
                voided_subs += 1
                total_payout += per_sub_stake
                continue
            prod = 1.0
            for leg, _ in non_void:
                prod *= float(leg["odds"])
            total_payout += per_sub_stake * prod

    pnl = round(total_payout - total_stake, 2)
    if pnl > 0:
        result = "won"
    elif pnl < 0:
        result = "lost"
    else:
        result = "void" if voided_subs == n_sub_combos else "lost"
    return {"result": result, "pnl": pnl, "clv": None}


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

    # CLOSING-PRE-KO-FALLBACK (2026-05-23): when no is_closing snapshot
    # exists, use the latest snapshot taken *before kickoff*. The previous
    # fallback returned the absolute-latest snapshot which could be an
    # in-play tick from the api-football-live feed (legitimate live price,
    # e.g. 151.0 mid-match) — that produced garbage CLV (-98%) for any
    # match where pre-KO snapshotting hadn't fired.
    result2 = execute_query(
        """SELECT os.odds
           FROM odds_snapshots os
           JOIN matches m ON m.id = os.match_id
           WHERE os.match_id = %s AND os.market = %s AND os.selection = %s
             AND os.timestamp <= m.date
           ORDER BY os.timestamp DESC LIMIT 1""",
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

    # GROWTH-ACCURACY-PICKS-LOG (2026-06-05): mark outcome on any published_picks
    # rows for these matches. Pure outcome accuracy (hit/miss) — independent
    # from bet/odds settlement. Best-effort; any error doesn't break match
    # settlement.
    try:
        from workers.jobs.publish_daily_picks import settle_published_picks_for_matches
        settle_published_picks_for_matches(match_ids)
    except Exception as e:
        console.print(f"  [yellow]published_picks settlement error: {e}[/yellow]")

    # Mark settled regardless of whether there were any pending bets/picks.
    # This stops the 15-min sweep from re-querying the same finished matches.
    execute_write(
        "UPDATE matches SET settlement_status = 'done' WHERE id = ANY(%s::uuid[])",
        [match_ids]
    )


def _settle_real_bets_for_matches(match_ids: list[str]):
    """SELF-USE-VALIDATION Phase 2.2 — settle real_bets for finished matches.

    Handles both singles and combo real_bets:
    - Singles: settled directly against match score (unchanged behaviour).
    - Combos (combo_legs IS NOT NULL): only settle when ALL leg matches are
      finished, regardless of which match_ids triggered this call.
    """
    if not match_ids:
        return

    # ── Singles ────────────────────────────────────────────────────────────
    pending_singles = execute_query(
        """SELECT rb.id, rb.match_id, rb.market, rb.selection,
                  rb.actual_odds AS odds_at_pick, rb.stake,
                  m.score_home, m.score_away
           FROM real_bets rb
           JOIN matches m ON m.id = rb.match_id
           WHERE rb.result = 'pending'
             AND rb.combo_legs IS NULL
             AND rb.match_id = ANY(%s::uuid[])
             AND m.status = 'finished'
             AND m.score_home IS NOT NULL
             AND m.score_away IS NOT NULL""",
        [match_ids],
    )
    settled = 0
    for bet in (pending_singles or []):
        try:
            # REAL-BETS-CLV-EDGE (2026-05-23): pull closing line so
            # settle_bet_result() can compute CLV against actual_odds.
            # REAL-BETS-CLV-NORMALIZE (2026-05-24): real_bets market/selection
            # come in as raw labels ('1X2', 'O/U', 'o/u', 'over 2.5') that
            # don't match odds_snapshots canonical form — normalize first.
            closing_odds = get_closing_odds(
                str(bet["match_id"]),
                _normalize_bet_market(bet["market"], bet["selection"]),
                _normalize_bet_selection(bet["selection"]),
            )
            outcome = settle_bet_result(
                bet,
                int(bet["score_home"]),
                int(bet["score_away"]),
                closing_odds,
            )
            execute_write(
                """UPDATE real_bets SET result=%s, pnl=%s, resolved_at=NOW(),
                                        clv=%s
                   WHERE id=%s""",
                [outcome["result"], outcome["pnl"], outcome.get("clv"),
                 bet["id"]],
            )
            settled += 1
        except Exception as e:
            console.print(f"[yellow]Real-bet settle error for {bet['id']}: {e}[/yellow]")

    # ── Combos ─────────────────────────────────────────────────────────────
    settled += _settle_real_combo_bets()

    if settled:
        console.print(f"[green]Settled {settled} real bet(s) across {len(match_ids)} match(es)[/green]")


def _void_real_bets_on_dead_matches() -> int:
    """Void any pending real_bet whose match was postponed / cancelled / abandoned.

    Mirrors what Coolbet (and every other book) does automatically: stake is
    refunded, pnl = 0. Singles only — combo legs are handled inside
    settle_combo_bet (a postponed leg makes the combo settle on the remaining
    legs at the reduced product odds, not a flat void).
    """
    # match_status enum has 'postponed' and 'cancelled'. store_match() collapses
    # AF's PST/CANC/ABD/WO/AWD all to 'postponed', so 'postponed' is what we
    # actually see today; 'cancelled' is covered for completeness.
    rows = execute_query(
        """SELECT rb.id
           FROM real_bets rb
           JOIN matches m ON m.id = rb.match_id
           WHERE rb.result = 'pending'
             AND rb.combo_legs IS NULL
             AND m.status IN ('postponed', 'cancelled')""",
        [],
    )
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    execute_write(
        """UPDATE real_bets
           SET result='void', pnl=0, resolved_at=NOW(),
               notes = COALESCE(notes,'') ||
                       CASE WHEN notes IS NULL OR notes='' THEN '' ELSE ' | ' END ||
                       'auto-voided: match postponed/cancelled'
           WHERE id = ANY(%s::uuid[])""",
        [ids],
    )
    console.print(f"[green]Voided {len(ids)} real bet(s) on postponed/cancelled match(es)[/green]")
    return len(ids)


def _settle_real_combo_bets() -> int:
    """Settle any pending combo real_bets whose ALL leg matches are now finished.

    Called from _settle_real_bets_for_matches on every settlement run. Scans
    all pending combo rows, checks if every leg's match has a final score, and
    settles using settle_combo_bet() (same logic as simulated combo bets).
    """
    pending = execute_query(
        """SELECT rb.id, rb.stake, rb.combo_legs, rb.system_type
           FROM real_bets rb
           WHERE rb.result = 'pending'
             AND rb.combo_legs IS NOT NULL""",
        [],
    )
    if not pending:
        return 0

    settled = 0
    for bet in pending:
        legs = bet["combo_legs"]
        if isinstance(legs, str):
            import json as _json
            legs = _json.loads(legs)
        if not legs:
            continue

        leg_match_ids = [str(l["match_id"]) for l in legs]
        score_rows = execute_query(
            """SELECT id::text AS match_id, score_home, score_away
               FROM matches
               WHERE id = ANY(%s::uuid[])
                 AND status = 'finished'
                 AND score_home IS NOT NULL
                 AND score_away IS NOT NULL""",
            [leg_match_ids],
        )
        match_scores = {r["match_id"]: (int(r["score_home"]), int(r["score_away"]))
                        for r in (score_rows or [])}

        if len(match_scores) < len(leg_match_ids):
            continue  # not all legs finished yet

        try:
            outcome = settle_combo_bet(
                {"combo_legs": legs, "stake": float(bet["stake"]),
                 "system_type": bet.get("system_type")},
                match_scores,
            )
            if outcome is None:
                continue
            execute_write(
                """UPDATE real_bets SET result=%s, pnl=%s, resolved_at=NOW()
                   WHERE id=%s""",
                [outcome["result"], outcome["pnl"], bet["id"]],
            )
            settled += 1
        except Exception as e:
            console.print(f"[yellow]Real-combo settle error for {bet['id']}: {e}[/yellow]")

    return settled


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

    # SETTLEMENT-POSTPONED-VOID (2026-05-25): scan-and-void real_bets on
    # postponed/cancelled/abandoned matches. Independent of the finished-match
    # path below — postponed matches never reach status='finished', so without
    # this they sit pending forever.
    try:
        _void_real_bets_on_dead_matches()
    except Exception as e:
        console.print(f"  [yellow]Postponed-void sweep error: {e}[/yellow]")

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

    # 4a. WC-F2 — auto-post Twitter/X recap for every freshly-settled
    # World Cup fixture. Idempotent via wc_match_tweets PK on match_id —
    # safe to call on settlement reruns (already-tweeted matches are
    # silently skipped). Wrapped in its own try block so a Twitter API
    # blip can never block downstream settlement steps.
    try:
        _post_wc_match_recaps()
    except Exception as e:
        console.print(f"  [yellow]WC recap tweet error: {e}[/yellow]")

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

    Opening = earliest pre-kickoff snapshot (timestamp < match.date).
    Filtering to pre-kickoff prevents in-play snapshots (near-1.0 odds
    captured during the match) from being used as "opening", which would
    produce wildly inflated CLV values (e.g. +2800%).

    Values outside ±50% are clamped to None — these indicate a data
    quality issue (wrong opening captured) and are meaningless to display.
    """
    # Fetch match IDs + kickoff times for these dates
    all_matches: list[dict] = []
    for d in sorted(fetch_dates):
        rows = execute_query(
            "SELECT id, date FROM matches WHERE status = 'finished' AND date >= %s AND date <= %s",
            [f"{d}T00:00:00", f"{d}T23:59:59"]
        )
        all_matches.extend(rows)

    if not all_matches:
        return 0, 0

    all_match_ids = [r["id"] for r in all_matches]
    kickoff_by_id = {str(r["id"]): r["date"] for r in all_matches}

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

        kickoff = kickoff_by_id.get(str(match_id))

        # Group by selection
        by_sel: dict[str, list] = {}
        for s in snaps:
            by_sel.setdefault(s["selection"].lower(), []).append(s)

        pseudo_clvs = {}
        for sel in ("home", "draw", "away"):
            sel_snaps = by_sel.get(sel, [])

            # Opening = earliest pre-kickoff snapshot only
            if kickoff:
                pre_kick = [s for s in sel_snaps if s["timestamp"] < kickoff]
            else:
                pre_kick = sel_snaps  # fallback if kickoff unknown
            opening_snaps = pre_kick if pre_kick else sel_snaps

            if not opening_snaps:
                pseudo_clvs[sel] = None
                continue

            # Closing = last is_closing snapshot, else last overall
            closing_snaps = [s for s in sel_snaps if s.get("is_closing")]
            if not closing_snaps:
                pseudo_clvs[sel] = None
                continue

            opening_odds = float(opening_snaps[0]["odds"])
            closing_odds = float(closing_snaps[-1]["odds"])

            if opening_odds <= 1.0 or closing_odds <= 1.0:
                pseudo_clvs[sel] = None
                continue

            clv = round((1.0 / opening_odds) / (1.0 / closing_odds) - 1, 5)
            # Discard implausible values — almost certainly a data artifact
            pseudo_clvs[sel] = clv if abs(clv) <= 0.5 else None

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


def _build_upcoming_model_summary() -> dict | None:
    """PERF-HERO-NEXT-MODEL (2026-06-01) — compare the newest unpromoted model
    against current production using model_versions.cv_metrics offline eval.

    Returns a dict for the /performance "Next upgrade" callout, or None when:
      • no candidate model exists (e.g. just after promotion)
      • candidate or production cv_metrics are unparseable
      • candidate has zero markets improving vs production

    Production version is the one whose name matches MODEL_VERSION env. The
    candidate is the most recent model_versions row trained strictly after
    production AND containing offline-eval metrics (cv_metrics.metrics shape).
    """
    import os, json
    production_version = os.environ.get("MODEL_VERSION", "v14")

    rows = execute_query("""
        SELECT version, trained_at, cv_metrics, notes
        FROM model_versions
        WHERE cv_metrics IS NOT NULL
          AND promoted_at IS NULL
          AND demoted_at IS NULL
        ORDER BY trained_at DESC NULLS LAST
        LIMIT 10
    """, [])

    def _metrics(cv):
        if cv is None:
            return None
        if isinstance(cv, str):
            try:
                cv = json.loads(cv)
            except Exception:
                return None
        m = cv.get("metrics") if isinstance(cv, dict) else None
        return m if isinstance(m, dict) and m else None

    prod_row = execute_query(
        "SELECT cv_metrics, trained_at FROM model_versions WHERE version = %s LIMIT 1",
        (production_version,),
    )
    prod_metrics = _metrics(prod_row[0]["cv_metrics"]) if prod_row else None
    if not prod_metrics:
        return None

    candidate = None
    for r in rows:
        if r["version"] == production_version:
            continue
        if r["trained_at"] and prod_row and prod_row[0]["trained_at"] and \
           r["trained_at"] <= prod_row[0]["trained_at"]:
            continue
        m = _metrics(r["cv_metrics"])
        if m:
            candidate = (r["version"], r["trained_at"], m)
            break
    if not candidate:
        return None

    cand_version, cand_trained_at, cand_metrics = candidate

    # Group markets by head. Average log_loss delta per group, count wins/losses.
    groups = {
        "1x2": ["1x2_home", "1x2_draw", "1x2_away"],
        "ah":  ["ah_home_+0.5", "ah_home_+1.5", "ah_home_-0.5", "ah_home_-1.5"],
        "btts": ["btts_yes", "btts_no"],
        "ou":  ["over25", "under25"],
    }

    group_deltas: dict[str, float] = {}
    better = worse = ties = 0
    for label, markets in groups.items():
        cand_lls = [cand_metrics[m]["log_loss"] for m in markets
                    if m in cand_metrics and m in prod_metrics and prod_metrics[m].get("log_loss")]
        prod_lls = [prod_metrics[m]["log_loss"] for m in markets
                    if m in cand_metrics and m in prod_metrics and prod_metrics[m].get("log_loss")]
        if not cand_lls:
            continue
        cand_avg = sum(cand_lls) / len(cand_lls)
        prod_avg = sum(prod_lls) / len(prod_lls)
        delta_pct = (cand_avg - prod_avg) / prod_avg * 100
        group_deltas[label] = round(delta_pct, 1)

    for mkt in cand_metrics:
        if mkt not in prod_metrics: continue
        c = cand_metrics[mkt].get("log_loss")
        p = prod_metrics[mkt].get("log_loss")
        if c is None or p is None: continue
        delta_pct = (c - p) / p * 100
        if delta_pct < -1: better += 1
        elif delta_pct > 1: worse += 1
        else: ties += 1

    if better == 0:
        return None

    return {
        "candidate":   cand_version,
        "production":  production_version,
        "trained_at":  cand_trained_at.date().isoformat() if cand_trained_at else None,
        "markets_better": better,
        "markets_worse":  worse,
        "markets_tied":   ties,
        "group_deltas": group_deltas,
        "holdout_n": cand_metrics.get(next(iter(cand_metrics))).get("n") if cand_metrics else None,
    }


def write_dashboard_cache():
    """
    Pre-compute all dashboard stats and write to dashboard_cache table.
    Called at end of settlement (21:00 UTC). Frontend reads latest row — fast.

    PERF-HONEST-HEADLINE (2026-05-17): writes two headlines (all-time incl.
    retired + active strategies only) and a separate retired_bot_breakdown
    block so /performance can show a transparent picture without re-querying
    simulated_bets.
    """
    console.print("[cyan]Writing dashboard cache...[/cyan]")
    try:
        # Per-bot rollup. Voids excluded from settled/won/staked/pnl/clv.
        # Void rows retain their original pnl/stake (we only flip `result`), so any
        # `result != 'pending'` filter would silently double-count voided bets.
        # ACTIVE bots only — this feeds the per-bot leaderboard.
        # COMBO-HIDE-FROM-PUBLIC (2026-05-18): exclude combo/acca bots from the
        # public leaderboard — they're paper experiments with 0 settled bets,
        # don't belong on /performance until they prove themselves. Still
        # visible on /admin/bots (different query path).
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
              AND b.name NOT LIKE 'bot_acca%%'
              AND b.name NOT LIKE 'bot_combo%%'
            GROUP BY b.id, b.name
        """, [])

        # Retired bot rollup — feeds the collapsed "Retired Strategies" section.
        # Includes retired_at + retired_reason so the page can show *why*.
        retired_rows = execute_query("""
            SELECT
                b.name,
                b.retired_at,
                b.retired_reason,
                COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
                COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as total_pnl,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as total_staked,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
            FROM bots b
            LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
            WHERE b.is_active = false OR b.retired_at IS NOT NULL
            GROUP BY b.id, b.name, b.retired_at, b.retired_reason
        """, [])

        # Grand total counts — ALL bots including experimental and retired.
        # Shown on /performance as "Settled Bets" to represent total work done,
        # not filtered performance. Experimental bots are excluded from ROI/CLV
        # math below so the headline numbers stay meaningful.
        _bets_join = "FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id"
        total_bets = execute_query(f"SELECT COUNT(*) as n {_bets_join} WHERE sb.result != 'void'", [])[0]["n"]
        settled_bets = execute_query(f"SELECT COUNT(*) as n {_bets_join} WHERE sb.result IN ('won','lost')", [])[0]["n"]
        pending_bets = int(total_bets) - int(settled_bets)

        # ROI/CLV math — still excludes experimental (acca/combo) bots whose
        # results would drag the headline into a misleading number.
        _excl = "AND b.maturity_label != 'experimental'"
        won = execute_query(f"SELECT COUNT(*) as n {_bets_join} WHERE sb.result = 'won' {_excl}", [])[0]["n"]
        lost = execute_query(f"SELECT COUNT(*) as n {_bets_join} WHERE sb.result = 'lost' {_excl}", [])[0]["n"]
        staked_row = execute_query(f"SELECT SUM(sb.stake) as s, SUM(sb.pnl) as p, AVG(sb.clv) as c {_bets_join} WHERE sb.result IN ('won','lost') {_excl}", [])[0]
        total_staked = float(staked_row["s"] or 0)
        total_pnl = float(staked_row["p"] or 0)
        avg_clv = float(staked_row["c"] or 0) if staked_row["c"] else None
        non_exp_settled = execute_query(f"SELECT COUNT(*) as n {_bets_join} WHERE sb.result IN ('won','lost') {_excl}", [])[0]["n"]
        hit_rate = (int(won) / int(non_exp_settled) * 100) if int(non_exp_settled) > 0 else None
        roi_pct = (total_pnl / total_staked * 100) if total_staked > 0 and int(non_exp_settled) > 0 else None

        # Active-only headline (excludes retired bots). The "what's currently
        # running" number. Same math, scoped via JOIN to bots.
        active_total_bets_row = execute_query("""
            SELECT
                COUNT(*) FILTER (WHERE sb.result != 'void') as total_bets,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
                COUNT(*) FILTER (WHERE sb.result = 'won') as won,
                COUNT(*) FILTER (WHERE sb.result = 'lost') as lost,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
            FROM simulated_bets sb
            JOIN bots b ON b.id = sb.bot_id
            WHERE b.is_active = true AND b.retired_at IS NULL
              AND b.maturity_label != 'experimental'
        """, [])[0]
        active_total_bets = int(active_total_bets_row["total_bets"] or 0)
        active_settled = int(active_total_bets_row["settled"] or 0)
        active_won = int(active_total_bets_row["won"] or 0)
        active_lost = int(active_total_bets_row["lost"] or 0)
        active_staked = float(active_total_bets_row["staked"] or 0)
        active_pnl = float(active_total_bets_row["pnl"] or 0)
        active_avg_clv = float(active_total_bets_row["avg_clv"] or 0) if active_total_bets_row["avg_clv"] else None
        active_roi_pct = (active_pnl / active_staked * 100) if active_staked > 0 and active_settled > 0 else None

        # PERF-HERO-COHORT-SPLIT (2026-06-01) — split last-30d ROI by cohort so
        # /performance can render separate Pre-match and In-play hero tiles.
        # Excludes experimental and retired bots (same scope as active headline).
        cohort_rows = execute_query("""
            SELECT
                CASE WHEN b.name LIKE 'inplay_%%' THEN 'inplay' ELSE 'prematch' END AS cohort,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) AS settled,
                COUNT(*) FILTER (WHERE sb.result = 'won') AS won,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) AS staked,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) AS pnl,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) AS avg_clv
            FROM simulated_bets sb
            JOIN bots b ON b.id = sb.bot_id
            WHERE b.is_active = true
              AND b.retired_at IS NULL
              AND b.maturity_label != 'experimental'
              AND sb.pick_time >= now() - interval '30 days'
            GROUP BY 1
        """, [])
        cohort_map = {r["cohort"]: r for r in cohort_rows}

        def _cohort_fields(label: str, include_clv: bool):
            r = cohort_map.get(label) or {}
            settled = int(r.get("settled") or 0)
            won = int(r.get("won") or 0)
            staked = float(r.get("staked") or 0)
            pnl = float(r.get("pnl") or 0)
            roi = (pnl / staked * 100) if staked > 0 and settled > 0 else None
            clv = float(r["avg_clv"]) if include_clv and r.get("avg_clv") is not None else None
            return settled, won, staked, pnl, roi, clv

        prematch_settled, prematch_won, prematch_staked, prematch_pnl, prematch_roi, prematch_clv = \
            _cohort_fields("prematch", include_clv=True)
        # Inplay CLV intentionally excluded — semantics differ (live closing vs
        # pre-match closing) and produce misleading aggregates.
        inplay_settled,  inplay_won,  inplay_staked,  inplay_pnl,  inplay_roi,  _ = \
            _cohort_fields("inplay", include_clv=False)

        # PERF-HERO-EQUITY-SPARKLINE (2026-06-01) — daily cumulative P&L on the
        # active+non-experimental cohort. The hero "Last 31d" sparkline reads
        # `daily_pnl_curve_30d`; the PerformanceExtras "Last 90d" cumulative
        # chart reads `daily_pnl_curve_90d`. Both are now derived from a SINGLE
        # 90-day query so endpoints can't drift — the 30d series is just the
        # 90d series sliced to its tail. UI-METRIC-SOT (2026-06-06).
        daily_pnl_rows_90d = execute_query("""
            SELECT
                DATE(sb.pick_time) AS d,
                ROUND(SUM(sb.pnl)::numeric, 2) AS daily_pnl
            FROM simulated_bets sb
            JOIN bots b ON b.id = sb.bot_id
            WHERE sb.result IN ('won','lost')
              AND b.is_active = true AND b.retired_at IS NULL
              AND b.maturity_label != 'experimental'
              AND sb.pick_time >= now() - interval '90 days'
            GROUP BY 1 ORDER BY 1
        """, [])
        # Walk twice: first build the 90d cumulative, then slice the last 30d
        # and re-zero its baseline so the sparkline reads "last 30 days of P&L"
        # rather than "30 days starting from whatever cum was 60d ago".
        cum_90 = 0.0
        full_curve_90d = []
        for r in daily_pnl_rows_90d:
            cum_90 += float(r["daily_pnl"] or 0)
            full_curve_90d.append({
                "d": r["d"].isoformat(),
                "daily": float(r["daily_pnl"] or 0),
                "cum_full": round(cum_90, 2),
            })
        daily_pnl_curve_90d = [
            {"d": p["d"], "cum": p["cum_full"]} for p in full_curve_90d
        ]
        thirty_days_ago = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        tail_30 = [p for p in full_curve_90d if p["d"] >= thirty_days_ago]
        cum_30 = 0.0
        daily_pnl_curve_30d = []
        for p in tail_30:
            cum_30 += p["daily"]
            daily_pnl_curve_30d.append({"d": p["d"], "cum": round(cum_30, 2)})

        # PERF-HERO-RECENT-WINS (2026-06-01) — top 8 unique wins last 14d by
        # CLV beat. Story = "model picked these and was right + beat closing
        # line by X%". Deduplicated by (match, market, selection) so the same
        # call from multiple bots renders once. No P&L / stake (free-tier
        # visible).
        recent_top_wins_rows = execute_query("""
            WITH ranked AS (
                SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                    sb.id,
                    sb.market,
                    sb.selection,
                    sb.odds_at_pick AS odds,
                    COALESCE(sb.clv_pinnacle, sb.clv) AS clv_used,
                    ht.name AS home,
                    at2.name AS away,
                    l.name AS league,
                    l.country AS country,
                    sb.pick_time
                FROM simulated_bets sb
                JOIN bots b ON b.id = sb.bot_id
                JOIN matches m ON m.id = sb.match_id
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at2 ON at2.id = m.away_team_id
                LEFT JOIN leagues l ON l.id = m.league_id
                WHERE sb.result = 'won'
                  AND sb.pick_time >= now() - interval '14 days'
                  AND b.is_active = true AND b.retired_at IS NULL
                  AND b.maturity_label != 'experimental'
                  AND COALESCE(sb.clv_pinnacle, sb.clv) IS NOT NULL
                  AND sb.odds_at_pick >= 1.50
                ORDER BY sb.match_id, sb.market, sb.selection,
                         COALESCE(sb.clv_pinnacle, sb.clv) DESC
            )
            SELECT * FROM ranked
            ORDER BY clv_used DESC
            LIMIT 8
        """, [])
        recent_top_wins = [
            {
                "home":      r["home"],
                "away":      r["away"],
                "league":    r["league"],
                "country":   r["country"],
                "market":    r["market"],
                "selection": r["selection"],
                "odds":      float(r["odds"] or 0),
                "clv":       float(r["clv_used"] or 0),
                "pick_time": r["pick_time"].isoformat() if r["pick_time"] else None,
            }
            for r in recent_top_wins_rows
        ]

        # PERF-HERO-NEXT-MODEL (2026-06-01) — build summary of the most-recent
        # candidate model's offline eval vs production. Surfaces the "next
        # upgrade" callout on /performance. Null when no fresh candidate
        # exists. Production model is identified by MODEL_VERSION env (the
        # operator-controlled flag); candidate is the latest model_versions
        # row newer than production with cv_metrics populated.
        upcoming_model_summary = _build_upcoming_model_summary()

        # PRO-TIER-V2 (2026-06-02) — rolling-30d hero stats per /value-bets tier.
        # Pro hero shows calibrated-cohort stats; Elite hero shows all-active.
        # See migration 168_dashboard_cache_value_bets_cohort.sql.
        def _value_bets_cohort(where_clause: str) -> dict | None:
            row = execute_query(f"""
                SELECT
                    COUNT(*) FILTER (WHERE sb.result IN ('won','lost'))             AS n,
                    COUNT(*) FILTER (WHERE sb.result = 'won')                       AS won,
                    SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost'))        AS staked,
                    SUM(sb.pnl)   FILTER (WHERE sb.result IN ('won','lost'))        AS pnl,
                    AVG(sb.clv)   FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) AS avg_clv
                FROM simulated_bets sb
                JOIN bots b ON b.id = sb.bot_id
                WHERE sb.pick_time >= now() - interval '30 days'
                  AND {where_clause}
            """, [])[0]
            n = int(row["n"] or 0)
            if n == 0:
                return None
            won = int(row["won"] or 0)
            staked = float(row["staked"] or 0)
            pnl = float(row["pnl"] or 0)
            roi = (pnl / staked * 100) if staked > 0 else None
            clv = float(row["avg_clv"]) * 100 if row.get("avg_clv") is not None else None
            return {
                "n":            n,
                "won":          won,
                "win_rate_pct": round(won / n * 100, 1) if n > 0 else None,
                "roi_pct":      round(roi, 2) if roi is not None else None,
                "clv_pct":      round(clv, 2) if clv is not None else None,
            }

        pro_value_bets_30d = _value_bets_cohort(
            "b.is_active = true AND b.maturity_label = 'calibrated'"
        )
        elite_value_bets_30d = _value_bets_cohort(
            "b.is_active = true"
        )

        # GROWTH-COPY-DENSITY-AUDIT Day 1 (2026-06-06) — cumulative since
        # chain start. Drives the landing hero line.
        #
        # CHAIN-START-ALIGN (2026-06-06): canonical chain start is 2026-05-01
        # — matches the /performance page's "since May 1" display. Earlier
        # value (2026-05-03) caused 12-bet + 2-day drift vs perf page. No
        # data is lost going back further; chain has no settled picks
        # before 2026-05-04 anyway, so 2026-05-01 boundary is purely
        # narrative.
        #
        # COHORT-ALIGN (2026-06-06): filter mirrors /performance's
        # activeBotNames: is_active=true AND maturity != 'experimental'.
        # Earlier filter ('is_active=true' only) included 6 acca/combo
        # bots with ~42 settled bets, making landing show more bets than
        # /performance.
        #
        # SETTLED-DEFINITION (2026-06-06): matches /performance's
        # grandTotalSettled — result IS NOT NULL AND NOT IN ('pending','void').
        # Includes 'push' (stake-refunded) bets — they are settled, just
        # P&L-neutral.
        _CUMULATIVE_CHAIN_START = '2026-05-01'

        def _value_bets_cumulative() -> dict | None:
            row = execute_query(f"""
                SELECT
                    COUNT(*) FILTER (WHERE sb.result IS NOT NULL AND sb.result NOT IN ('pending','void')) AS n_settled,
                    COUNT(*) FILTER (WHERE sb.result = 'won')                          AS won,
                    SUM(sb.stake) FILTER (WHERE sb.result IS NOT NULL AND sb.result NOT IN ('pending','void')) AS staked,
                    SUM(sb.pnl)   FILTER (WHERE sb.result IS NOT NULL AND sb.result NOT IN ('pending','void')) AS pnl,
                    AVG(sb.clv)   FILTER (WHERE sb.result IS NOT NULL AND sb.result NOT IN ('pending','void') AND sb.clv IS NOT NULL) AS avg_clv,
                    SUM(sb.clv * sb.stake) FILTER (WHERE sb.result IS NOT NULL AND sb.result NOT IN ('pending','void') AND sb.clv IS NOT NULL) AS cumulative_clv_eur,
                    MIN(sb.pick_time) AS first_pick,
                    MAX(sb.pick_time) AS last_pick
                FROM simulated_bets sb
                JOIN bots b ON b.id = sb.bot_id
                WHERE sb.pick_time >= %s
                  AND b.is_active = true
                  AND b.maturity_label != 'experimental'
                  AND b.retired_at IS NULL
            """, [_CUMULATIVE_CHAIN_START])[0]
            n = int(row["n_settled"] or 0)
            if n == 0:
                return None
            won = int(row["won"] or 0)
            staked = float(row["staked"] or 0)
            pnl = float(row["pnl"] or 0)
            avg_clv = float(row["avg_clv"]) * 100 if row.get("avg_clv") is not None else None
            cum_clv = float(row["cumulative_clv_eur"]) if row.get("cumulative_clv_eur") is not None else None
            first_pick = row.get("first_pick")
            last_pick = row.get("last_pick")
            # Days = calendar days from chain_start to settlement_run_time.
            # Matches /performance's Math.floor((Date.now() - chainStart)/day)
            # — both tick up by 1 each day at midnight UTC.
            from datetime import datetime, timezone, date
            chain_start_dt = datetime.fromisoformat(_CUMULATIVE_CHAIN_START).replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            days = (now_utc - chain_start_dt).days
            return {
                "n_settled":         n,
                "won":               won,
                "win_rate_pct":      round(won / n * 100, 1) if n > 0 else None,
                "staked":            round(staked, 2),
                "pnl":               round(pnl, 2),
                "avg_clv_pct":       round(avg_clv, 2) if avg_clv is not None else None,
                "cumulative_clv_eur": round(cum_clv, 2) if cum_clv is not None else None,
                "chain_start":       _CUMULATIVE_CHAIN_START,
                "first_pick":        first_pick.isoformat() if first_pick else None,
                "last_pick":         last_pick.isoformat() if last_pick else None,
                "days":              days,
            }

        elite_value_bets_cumulative = _value_bets_cumulative()

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

        retired_bot_breakdown = []
        for r in retired_rows:
            s = int(r.get("settled") or 0)
            w = int(r.get("won") or 0)
            p = float(r.get("total_pnl") or 0)
            st = float(r.get("total_staked") or 0)
            retired_bot_breakdown.append({
                "name": r["name"],
                "settled": s,
                "won": w,
                "total_pnl": round(p, 2),
                "roi_pct": round(p / st * 100, 1) if st > 0 and s > 0 else None,
                "avg_clv": round(float(r["avg_clv"]), 4) if r.get("avg_clv") else None,
                "retired_at": r["retired_at"].isoformat() if r.get("retired_at") else None,
                "retired_reason": r.get("retired_reason"),
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
                pseudo_clv_count, live_snapshot_matches, alignment_settled_count,
                active_total_bets, active_settled_bets, active_won_bets, active_lost_bets,
                active_total_staked, active_total_pnl, active_roi_pct, active_avg_clv,
                retired_bot_breakdown,
                prematch_settled_bets, prematch_won_bets, prematch_total_staked,
                prematch_total_pnl, prematch_roi_pct, prematch_avg_clv,
                inplay_settled_bets, inplay_won_bets, inplay_total_staked,
                inplay_total_pnl, inplay_roi_pct,
                daily_pnl_curve_30d, daily_pnl_curve_90d,
                recent_top_wins,
                upcoming_model_summary,
                pro_value_bets_30d,
                elite_value_bets_30d,
                elite_value_bets_cumulative
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, [
            int(total_bets), int(settled_bets), int(pending_bets), int(won), int(lost),
            hit_rate, total_staked, total_pnl, roi_pct, avg_clv,
            json.dumps(bot_breakdown), json.dumps(market_breakdown),
            model_accuracy_pct, n,
            int(pseudo_clv_count), int(live_snapshot_matches), int(alignment_settled),
            active_total_bets, active_settled, active_won, active_lost,
            active_staked, active_pnl, active_roi_pct, active_avg_clv,
            json.dumps(retired_bot_breakdown),
            prematch_settled, prematch_won, prematch_staked,
            prematch_pnl, prematch_roi, prematch_clv,
            inplay_settled, inplay_won, inplay_staked,
            inplay_pnl, inplay_roi,
            json.dumps(daily_pnl_curve_30d), json.dumps(daily_pnl_curve_90d),
            json.dumps(recent_top_wins),
            json.dumps(upcoming_model_summary) if upcoming_model_summary else None,
            json.dumps(pro_value_bets_30d) if pro_value_bets_30d else None,
            json.dumps(elite_value_bets_30d) if elite_value_bets_30d else None,
            json.dumps(elite_value_bets_cumulative) if elite_value_bets_cumulative else None,
        ])
        console.print(
            f"  Dashboard cache written: {int(settled_bets)} settled bets (all-time) · "
            f"{active_settled} active · accuracy={model_accuracy_pct}%"
        )
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


def _normalize_bet_market(market: str, selection: str | None = None) -> str:
    """
    Map bet market strings (as stored in simulated_bets / real_bets) to
    odds_snapshots market values. Handles uppercase and inspects `selection`
    for the OU line (e.g. "o/u" + "over 3.5" → "over_under_35"), so OU bets
    on lines other than 2.5 get CLV computed against the correct closing line.

    CLV-OU-LINE-FIX (2026-05-24): previous version returned a hardcoded
    "over_under_25" for every "o/u" bet — sim_bets on OU 3.5 / 1.5 were
    pulling closing odds from the OU 2.5 line, producing bogus +59-76% CLV.
    """
    m = (market or "").strip().lower()
    if m in ("1x2", "1×2"):
        return "1x2"
    if m == "ou15":
        return "over_under_15"
    if m == "ou35":
        return "over_under_35"
    if m == "ou25":
        return "over_under_25"
    if m in ("o/u", "ou", "over/under"):
        # Pull the line from selection ("over 2.5" / "under 3.5" / etc.).
        if selection:
            import re
            mo = re.match(r"^\s*(over|under)\s+(\d+(?:\.\d+)?)\s*$",
                          selection.strip().lower())
            if mo:
                line = float(mo.group(2))
                # 2.5 → "25", 3.5 → "35", 1.5 → "15", 0.5 → "05", 1.25 → "125"
                line_str = str(int(round(line * 10))).zfill(2)
                return f"over_under_{line_str}"
        return "over_under_25"  # fallback
    # Already in DB format (e.g. "over_under_25", "btts", "asian_handicap")
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


def _post_wc_match_recaps() -> int:
    """WC-F2 — post a Twitter/X recap for every finished WC fixture that
    hasn't already been tweeted.

    Looks up every match on the FIFA World Cup league (api_football_id=1)
    that is `finished` AND has scores AND has NOT yet got a row in
    wc_match_tweets, then calls post_wc_match_recap(match_id) for each.

    Returns the number of tweets actually posted. Never raises — Twitter
    creds may be missing in dev/CI; the caller wraps us defensively too.
    """
    try:
        from workers.jobs.wc_match_recap_tweet import post_wc_match_recap
    except Exception as e:
        console.print(f"  [yellow]WC recap import failed: {e}[/yellow]")
        return 0

    try:
        rows = execute_query(
            """
            SELECT m.id::text AS match_id
            FROM matches m
            JOIN leagues l ON l.id = m.league_id
            LEFT JOIN wc_match_tweets t ON t.match_id = m.id
            WHERE l.api_football_id = 1
              AND m.status = 'finished'
              AND m.score_home IS NOT NULL
              AND m.score_away IS NOT NULL
              AND t.match_id IS NULL
            ORDER BY m.date ASC
            """,
            [],
        ) or []
    except Exception as e:
        # Table 181 may not be migrated yet (or DB is down). Fail closed.
        console.print(f"  [yellow]WC recap lookup failed: {e}[/yellow]")
        return 0

    if not rows:
        return 0

    console.print(f"\n[cyan]Posting WC recap tweets for {len(rows)} fixture(s)...[/cyan]")
    posted = 0
    for r in rows:
        try:
            if post_wc_match_recap(r["match_id"]):
                posted += 1
        except Exception as e:
            # post_wc_match_recap is documented to never raise, but belt+braces.
            console.print(f"  [yellow]recap {r['match_id']} failed: {e}[/yellow]")
    console.print(f"  {posted} tweet(s) posted")
    return posted


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
        # COMBO-PHASE-D: combo bets don't have a single "score" — their match_id
        # is just the first leg's placeholder. Skip the per-bet score lookup for
        # combos and defer entirely to the combo branch further down, which does
        # its own per-leg match lookups.
        is_combo_row = bet.get("combo_legs") is not None
        # Hoisted above the branch so the per-bet display row below works for
        # combo bets too (combos used to UnboundLocalError on the rich table
        # render — see SETTLE-READY-UNBOUNDLOCAL).
        home_name_display = bet.get("home_team_name", "?")
        away_name_display = bet.get("away_team_name", "?")
        if is_combo_row:
            score_home = None
            score_away = None
        else:
            # Flat SQL row: score_home/score_away are directly on bet
            score_home = bet.get("score_home")
            score_away = bet.get("score_away")

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
        odds_market = _normalize_bet_market(raw_market, raw_selection)
        odds_selection = _normalize_bet_selection(raw_selection)

        # Bot identity (needed before CLV computation)
        bot_id = str(bet["bot_id"])
        bot_name = by_bot.get(bot_id, {}).get("name", "")
        is_inplay = bot_name.startswith("inplay_")

        # COMBO-PHASE-D: dispatch combo bets to settle_combo_bet.
        # The combo's match_id is just the first leg's; settlement uses combo_legs
        # JSON to look up each leg's match and aggregate outcomes.
        is_combo = bet.get("combo_legs") is not None
        if is_combo:
            # Build match_scores dict for every leg whose match has finished.
            legs_data = bet["combo_legs"]
            if isinstance(legs_data, str):
                legs_data = json.loads(legs_data)
            leg_match_ids = [str(l["match_id"]) for l in legs_data]
            score_rows = execute_query(
                "SELECT id::text AS id, score_home, score_away FROM matches "
                "WHERE id = ANY(%s::uuid[]) AND status = 'finished' "
                "AND score_home IS NOT NULL AND score_away IS NOT NULL",
                [leg_match_ids],
            ) or []
            match_scores = {r["id"]: (int(r["score_home"]), int(r["score_away"])) for r in score_rows}
            combo_settlement = settle_combo_bet(bet, match_scores)
            if combo_settlement is None:
                # At least one leg's match isn't finished — combo stays pending
                continue
            settlement = combo_settlement
            closing_odds = None
            clv_pinnacle = None
        elif is_inplay:
            # CLV is meaningless for inplay bets: live odds reflect game state (goals, cards)
            # not market efficiency, so closing_odds is just whatever snapshot happened to be
            # last captured — producing arbitrarily large/small CLV with no signal value.
            closing_odds = None
            clv_pinnacle = None
            settlement = settle_bet_result(bet, score_home, score_away, closing_odds)
        else:
            closing_odds = get_closing_odds(match_id, odds_market, odds_selection)
            # PIN-5: Pinnacle-anchored CLV — the industry-standard EV validator
            pinnacle_closing = get_pinnacle_closing_odds(match_id, odds_market, odds_selection)
            odds_at_pick = float(bet["odds_at_pick"])
            clv_pinnacle = (
                round((odds_at_pick / pinnacle_closing) - 1, 4)
                if pinnacle_closing and pinnacle_closing > 1.0
                else None
            )
            settlement = settle_bet_result(bet, score_home, score_away, closing_odds)

        # Bot bankroll tracking
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
        odds_market = _normalize_bet_market(bet["market"], bet["selection"])
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
        by_market[_normalize_bet_market(b["market"])].append(b)

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

    # POST-MORTEM-BALANCE (2026-05-24): pre-compute a per-conviction-bucket hit-rate
    # table from today's bets so the LLM can ground MODEL_ERROR vs VARIANCE judgments
    # in empirical baseline data rather than narrative cherry-picking. The OU-UNDER-CAP
    # investigation showed the LLM was flagging "high conviction model error" on
    # losses without checking that the bot was actually winning at similar confidence
    # on the same day — pure availability bias.
    def _conv_bucket(p):
        if p is None:
            return None
        p = float(p)
        if p < 0.40: return "<40%"
        if p < 0.50: return "40-50%"
        if p < 0.60: return "50-60%"
        if p < 0.70: return "60-70%"
        if p < 0.80: return "70-80%"
        return ">=80%"

    bucket_stats: dict[str, dict] = {}
    for b in bets:
        if b["result"] not in ("won", "lost"):
            continue
        p = b.get("calibrated_prob") or b.get("model_probability")
        bucket = _conv_bucket(p)
        if bucket is None:
            continue
        s = bucket_stats.setdefault(bucket, {"n": 0, "wins": 0, "total_pred": 0.0})
        s["n"] += 1
        s["wins"] += 1 if b["result"] == "won" else 0
        s["total_pred"] += float(p)

    calib_rows = []
    for bucket in ["<40%", "40-50%", "50-60%", "60-70%", "70-80%", ">=80%"]:
        s = bucket_stats.get(bucket)
        if not s or s["n"] == 0:
            continue
        pred = 100.0 * s["total_pred"] / s["n"]
        actual = 100.0 * s["wins"] / s["n"]
        delta = actual - pred
        calib_rows.append(
            f"  {bucket:<8}  n={s['n']:>3}  predicted={pred:5.1f}%  actual={actual:5.1f}%  Δ={delta:+5.1f}pp"
        )
    calib_block = "\n".join(calib_rows) if calib_rows else "  (no calibrated bets today)"

    prompt = f"""You are a sports betting analyst performing a daily post-mortem.

CONTEXT (POST-MORTEM-CONTEXT, 2026-05-24): Bets come from an INDEPENDENT PORTFOLIO of
~24 active bots, each with its own strategy (different markets, edges, league filters,
selection sides). It is NORMAL and EXPECTED for two different bots to back opposite
sides of the same match — e.g., one bot betting Home @5.00 and another betting Away
@4.33 in the same fixture. That is portfolio diversification, NOT a bug in probability
generation or bet selection. Do not flag opposite-side picks across different bots as
"contradictory" or "non-normalized probabilities." Only flag conflicts when the SAME
bot has placed mutually exclusive bets on a single match (which the dedup constraint
prevents anyway).

TODAY'S SETTLED BETS ({len(bets)} total: {len(wins)} won, {len(losses)} lost):

{chr(10).join(bet_summaries)}

DAILY CALIBRATION SNAPSHOT (POST-MORTEM-BALANCE, 2026-05-24): per-confidence-bucket
hit rate from today's settled bets — predicted % is the avg model conviction in the
bucket, actual % is the realised win rate. Use this BEFORE classifying any loss as
MODEL_ERROR.

{calib_block}

For each LOST bet, classify the likely cause into exactly one category:
- VARIANCE: Model assessment was reasonable (good edge, maybe good CLV) but result went against us. Bad luck, not a model flaw. **Default to VARIANCE when the bet's confidence bucket above shows actual ≈ predicted (Δ within ±10pp on n≥5 bets).** A high-conviction loss is not by itself MODEL_ERROR; check the bucket's win rate first.
- INFORMATION_GAP: Odds moved against us (negative drift) or news impacted the match in a way our model didn't capture. We were missing information.
- MODEL_ERROR: Model probability was significantly wrong — the team was simply not as strong/weak as predicted. **Only assign MODEL_ERROR when the bet's confidence bucket as a WHOLE underperforms its predicted hit rate by 15pp+, OR when the loss is structurally extreme (e.g., 91% under that ended 4-4) AND the bucket has too few wins to call it VARIANCE.** Do not call MODEL_ERROR for a single loss when other bets in the same conviction bucket won today.
- TIMING: The pick might have been right earlier but conditions changed (lineup, late injury). Better timing would have helped.

If today's settled count is small (n < 20), buckets will be sparse and you should default to VARIANCE for losses unless the score is structurally extreme — the LLM availability bias caught on 2026-05-24 came from over-confident MODEL_ERROR calls on small-sample days. Note this in `patterns_noticed` if applicable.

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

            # Store in model_evaluations.
            # POST-MORTEM-SCHEMA (2026-05-24): the previous `[:2000]` slice truncated
            # JSON mid-string on days with 6+ losses (5 of 14 historical rows ended
            # up unparseable as a result). `notes` is TEXT (no DB-side limit), so we
            # serialize in full and validate the JSON round-trips before insert. If
            # validation fails, store a sanitized minimal payload rather than corrupt
            # JSON so downstream consumers (val_post_mortem.py, future meta-tuner)
            # always get parseable data.
            try:
                notes_str = json.dumps(analysis, ensure_ascii=False)
                try:
                    json.loads(notes_str)  # validate round-trip
                except Exception:
                    notes_str = json.dumps({
                        "loss_classifications": [],
                        "daily_summary": "[post-mortem validation failed — see settlement logs]",
                        "raw_text_preview": text[:500],
                    })
                store_model_evaluation(
                    eval_date=today_str,
                    league_id=None,
                    market="post_mortem",
                    total_bets=len(bets),
                    hits=len(wins),
                    roi=sum(b["pnl"] or 0 for b in bets) / max(sum(b["stake"] for b in bets), 1) * 100,
                    avg_clv=None,
                    notes=notes_str,
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
