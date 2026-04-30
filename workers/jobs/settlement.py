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
    get_client,
    store_team_form,
    store_model_evaluation,
    compute_team_form_from_db,
    store_match_stats_full,
    store_match_events_af,
    store_match_player_stats,
    build_match_feature_vectors,
)

console = Console()


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

def settle_bet_result(bet: dict, home_goals: int, away_goals: int,
                      closing_odds: float | None) -> dict:
    """
    Determine if a bet won or lost.
    Returns dict with result, pnl, clv.
    """
    market = bet["market"].lower()
    selection = bet["selection"].lower()
    stake = bet["stake"]
    odds = bet["odds_at_pick"]
    total_goals = home_goals + away_goals

    won = False

    if market == "1x2":
        if selection == "home" and home_goals > away_goals:
            won = True
        elif selection in ("draw", "x") and home_goals == away_goals:
            won = True
        elif selection == "away" and away_goals > home_goals:
            won = True

    elif "over_under" in market or "o/u" in market:
        # Extract line from market name: "over_under_25" → 2.5
        line = 2.5
        for part in market.split("_"):
            try:
                line = int(part) / 10 if len(part) == 2 else float(part)
                if 0 < line < 10:
                    break
            except ValueError:
                continue

        if "over" in selection and total_goals > line:
            won = True
        elif "under" in selection and total_goals < line:
            won = True

    pnl = round((odds - 1) * stake if won else -stake, 2)

    # CLV: positive = we got better odds than closing line
    clv = None
    if closing_odds and closing_odds > 0:
        clv = round((odds / closing_odds) - 1, 4)

    return {
        "result": "won" if won else "lost",
        "pnl": pnl,
        "clv": clv,
    }


# ─── Closing odds lookup ─────────────────────────────────────────────────────

def get_closing_odds(client, match_id: str, market: str, selection: str) -> float | None:
    """Get the closing odds for a match/market/selection from odds_snapshots"""
    result = client.table("odds_snapshots").select("odds").eq(
        "match_id", match_id
    ).eq("market", market).eq("selection", selection).eq(
        "is_closing", True
    ).order("timestamp", desc=True).limit(1).execute()

    if result.data:
        return float(result.data[0]["odds"])

    # Fallback: use the latest snapshot (closest to closing)
    result2 = client.table("odds_snapshots").select("odds").eq(
        "match_id", match_id
    ).eq("market", market).eq("selection", selection).order(
        "timestamp", desc=True
    ).limit(1).execute()

    return float(result2.data[0]["odds"]) if result2.data else None


# ─── Post-match enrichment (T4, T8, T12) ─────────────────────────────────────

def fetch_post_match_enrichment(client) -> dict:
    """
    T4: Half-time stats, T8: Match events, T12: Player stats.
    Runs after settlement for recently finished matches.

    Skips matches already enriched (match_stats row exists) — idempotent.
    Uses ThreadPoolExecutor to parallelize API calls (4 concurrent).
    Returns counts dict.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from workers.api_clients.api_football import (
        get_fixture_statistics, parse_fixture_stats,
        get_fixture_statistics_halftime, parse_fixture_stats_halftime,
        get_fixture_events, parse_fixture_events,
        get_fixture_players, parse_fixture_players,
    )

    counts = {"stats": 0, "halftime": 0, "events": 0, "players": 0, "skipped": 0}

    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get recently finished matches with AF IDs
    db_finished = client.table("matches").select(
        "id, api_football_id"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

    if not db_finished:
        return counts

    all_match_ids = [m["id"] for m in db_finished]

    # Batch query: which matches already have stats (single query, not N+1)
    existing_stats = client.table("match_stats").select("match_id").in_(
        "match_id", all_match_ids
    ).execute()
    match_ids_with_stats = {r["match_id"] for r in (existing_stats.data or [])}

    # Batch query: look up home_team_api_id from match_injuries for all matches
    inj_rows = client.table("match_injuries").select(
        "match_id, team_api_id"
    ).in_("match_id", all_match_ids).eq("team_side", "home").execute()
    home_api_id_by_match: dict[str, int] = {
        r["match_id"]: r["team_api_id"] for r in (inj_rows.data or []) if r.get("team_api_id")
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

    def _enrich_one_match(match: dict) -> dict:
        """Enrich a single match — runs in a thread."""
        af_id = match["api_football_id"]
        match_id = match["id"]
        home_api_id = home_api_id_by_match.get(match_id)
        result = {"stats": 0, "halftime": 0, "events": 0, "players": 0}

        # T4 + Full stats
        try:
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

        # T8: Match events
        try:
            raw_events = get_fixture_events(af_id)
            parsed_events = parse_fixture_events(raw_events)
            if parsed_events:
                result["events"] = store_match_events_af(
                    match_id, parsed_events, home_team_api_id=home_api_id
                )
        except Exception as e:
            console.print(f"    [yellow]Events error for fixture {af_id}: {e}[/yellow]")

        # T12: Player stats
        try:
            raw_players = get_fixture_players(af_id)
            parsed_players = parse_fixture_players(
                raw_players, home_team_api_id=home_api_id
            )
            if parsed_players:
                result["players"] = store_match_player_stats(match_id, af_id, parsed_players)
        except Exception as e:
            console.print(f"    [yellow]Player stats error for fixture {af_id}: {e}[/yellow]")

        return result

    # Run enrichment in parallel (4 threads — stay within API rate limits)
    with ThreadPoolExecutor(max_workers=4) as pool:
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


# ─── Main settlement ──────────────────────────────────────────────────────────

def run_settlement():
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    console.print(f"[bold green]═══ OddsIntel Settlement: {today} ═══[/bold green]\n")

    client = get_client()

    # 1. Get pending bets with match info (may be empty — that's fine)
    console.print("[cyan]Loading pending bets...[/cyan]")
    bets_result = client.table("simulated_bets").select(
        "*, matches(id, date, score_home, score_away, result, status, "
        "home_team:home_team_id(name), away_team:away_team_id(name))"
    ).eq("result", "pending").execute()

    pending = bets_result.data
    console.print(f"  {len(pending)} pending bets")

    # 2. Determine which dates to fetch results for.
    # Always include today + yesterday to catch late finishes.
    # Also include any dates that have pending bets.
    fetch_dates = {today, yesterday}
    for bet in pending:
        match_info = bet.get("matches", {})
        if match_info and match_info.get("date"):
            fetch_dates.add(match_info["date"][:10])

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
    # This gives us a complete labeled dataset for every match we tracked,
    # regardless of whether any bot placed a bet on it.
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
    db_matches = client.table("matches").select(
        "id, api_football_id, home_team_id, away_team_id, status"
    ).gte("date", f"{date_min}T00:00:00").lte(
        "date", f"{date_max}T23:59:59"
    ).execute().data

    # Pre-load all team names in one batch query (instead of 2 per unmatched match)
    all_team_ids = set()
    for m in db_matches:
        all_team_ids.add(m["home_team_id"])
        all_team_ids.add(m["away_team_id"])
    team_name_map: dict[str, str] = {}
    team_id_list = list(all_team_ids)
    for i in range(0, len(team_id_list), 200):
        chunk = team_id_list[i:i + 200]
        tr = client.table("teams").select("id, name").in_("id", chunk).execute()
        for t in (tr.data or []):
            team_name_map[t["id"]] = t["name"]

    for db_match in db_matches:
        if db_match.get("status") == "finished":
            continue  # already settled

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
        client.table("matches").update({
            "score_home": hg, "score_away": ag,
            "result": result_str, "status": "finished",
        }).eq("id", db_match["id"]).execute()
        db_updated += 1

    console.print(f"  {db_updated} matches updated | {db_skipped} no result found yet")

    # 4. Settle each bet (skip gracefully if none pending)
    if not pending:
        console.print("\n[yellow]No pending bets to settle — skipping bet settlement.[/yellow]")
    else:
        _settle_pending_bets(client, pending, finished)

    # 4b. Settle user picks (frontend prediction tracker)
    try:
        _settle_user_picks(client)
    except Exception as e:
        console.print(f"  [yellow]User picks settlement error: {e}[/yellow]")

    # Post-match enrichment and analytics always run (not gated on bets)

    # P1.3: Update ELO ratings for all finished matches
    console.print("\n[cyan]Updating ELO ratings...[/cyan]")
    try:
        elo_count = update_elo_ratings(client)
        console.print(f"  {elo_count} team ratings updated")
    except Exception as e:
        console.print(f"  [yellow]ELO update error: {e}[/yellow]")

    # P1.4: Aggregate model evaluations
    console.print("[cyan]Computing model evaluations...[/cyan]")
    try:
        eval_count = compute_model_evaluations(client)
        console.print(f"  {eval_count} evaluation records stored")
    except Exception as e:
        console.print(f"  [yellow]Model evaluation error: {e}[/yellow]")

    # P1.5: Update form cache for teams that played
    console.print("[cyan]Updating team form cache...[/cyan]")
    try:
        form_count = update_team_form_cache(client)
        console.print(f"  {form_count} team forms updated")
    except Exception as e:
        console.print(f"  [yellow]Form cache error: {e}[/yellow]")

    # T4/T8/T12: Post-match enrichment (stats, half-time, events, player stats)
    console.print("[cyan]Fetching post-match enrichment (T4/T8/T12)...[/cyan]")
    try:
        enrichment_counts = fetch_post_match_enrichment(client)
        console.print(
            f"  {enrichment_counts['stats']} match stats | "
            f"{enrichment_counts['halftime']} with half-time | "
            f"{enrichment_counts['events']} events | "
            f"{enrichment_counts['players']} player stat rows | "
            f"{enrichment_counts.get('skipped', 0)} already enriched (skipped)"
        )
    except Exception as e:
        console.print(f"  [yellow]Post-match enrichment error: {e}[/yellow]")

    # 11.4: Daily post-mortem LLM analysis (only if bets were settled)
    if pending:
        console.print("\n[cyan]Running AI post-mortem analysis...[/cyan]")
        try:
            run_post_mortem(client)
        except Exception as e:
            console.print(f"  [yellow]Post-mortem error (non-critical): {e}[/yellow]")

    console.print("\n[bold green]Core settlement complete.[/bold green]")


def _compute_pseudo_clv_batched(client, fetch_dates: list[str]) -> tuple[int, int]:
    """
    Compute pseudo-CLV for all finished matches in the given dates.
    Bulk-loads all odds_snapshots, computes in-memory, batch-updates matches.
    Returns (computed_count, skipped_count).
    """
    # Get all finished match IDs for these dates
    all_match_ids = []
    for d in sorted(fetch_dates):
        rows = client.table("matches").select("id").eq(
            "status", "finished"
        ).gte("date", f"{d}T00:00:00").lte(
            "date", f"{d}T23:59:59"
        ).execute().data or []
        all_match_ids.extend(r["id"] for r in rows)

    if not all_match_ids:
        return 0, 0

    # Bulk-load all 1x2 odds snapshots for these matches
    odds_by_match: dict[str, list] = {}
    for i in range(0, len(all_match_ids), 200):
        chunk = all_match_ids[i:i + 200]
        result = client.table("odds_snapshots").select(
            "match_id, selection, odds, timestamp, is_closing"
        ).in_("match_id", chunk).eq("market", "1x2").order(
            "timestamp", desc=False
        ).limit(50000).execute()
        for row in (result.data or []):
            odds_by_match.setdefault(row["match_id"], []).append(row)

    # Compute pseudo-CLV in-memory
    computed = 0
    skipped = 0
    update_rows = []

    for match_id in all_match_ids:
        snaps = odds_by_match.get(match_id, [])
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

        update_rows.append({
            "id": match_id,
            "pseudo_clv_home": pseudo_clvs.get("home"),
            "pseudo_clv_draw": pseudo_clvs.get("draw"),
            "pseudo_clv_away": pseudo_clvs.get("away"),
        })
        computed += 1

    # Batch update matches (chunks of 50)
    for i in range(0, len(update_rows), 50):
        chunk = update_rows[i:i + 50]
        for row in chunk:
            match_id = row.pop("id")
            try:
                client.table("matches").update(row).eq("id", match_id).execute()
            except Exception:
                pass

    return computed, skipped


def run_ml_etl():
    """
    ML ETL phase — runs separately from core settlement.
    Computes pseudo-CLV and builds match_feature_vectors for recently finished matches.
    Split out because these are query-heavy (~10 queries/match) and can safely run later.
    """
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    console.print(f"[bold green]═══ OddsIntel ML ETL: {today} ═══[/bold green]\n")

    client = get_client()
    fetch_dates = [yesterday, today]

    # B-ML1: Compute pseudo-CLV for ALL finished matches (batched)
    console.print("[cyan]Computing pseudo-CLV for all finished matches...[/cyan]")
    try:
        pclv_count, pclv_skipped = _compute_pseudo_clv_batched(client, fetch_dates)
        console.print(f"  {pclv_count} pseudo-CLV computed | {pclv_skipped} skipped (insufficient odds data)")
    except Exception as e:
        console.print(f"  [yellow]Pseudo-CLV error: {e}[/yellow]")

    # B-ML2: Build match_feature_vectors wide table (ML training table)
    console.print("[cyan]Building match feature vectors...[/cyan]")
    try:
        fv_total = 0
        for d in sorted(fetch_dates):
            fv_count = build_match_feature_vectors(client, d)
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


def _settle_pending_bets(client, pending: list, finished: list):
    """Settle all pending bets against finished match results."""
    console.print("\n[cyan]Settling bets...[/cyan]\n")

    settled = 0
    skipped = 0
    total_pnl = 0.0
    clv_values = []

    by_bot: dict[str, dict] = {}

    t = Table(title="Settlement Results")
    t.add_column("Match", style="cyan")
    t.add_column("Bet")
    t.add_column("Score")
    t.add_column("Result")
    t.add_column("P&L", justify="right")
    t.add_column("CLV", justify="right")

    for bet in pending:
        match = bet.get("matches", {})
        if not match:
            skipped += 1
            continue

        # Check if match result is already in DB
        score_home = match.get("score_home")
        score_away = match.get("score_away")

        # If not in DB, try to find in external results
        if score_home is None:
            home_name = (match["home_team"][0]["name"] if isinstance(match.get("home_team"), list)
                        else match.get("home_team", {}).get("name", ""))
            away_name = (match["away_team"][0]["name"] if isinstance(match.get("away_team"), list)
                        else match.get("away_team", {}).get("name", ""))

            result_match = find_result_for_match(home_name, away_name, finished)
            if not result_match:
                skipped += 1
                continue

            score_home = int(result_match["home_goals"])
            score_away = int(result_match["away_goals"])
            home_name_display = home_name
            away_name_display = away_name
        else:
            home_name_display = (match["home_team"][0]["name"] if isinstance(match.get("home_team"), list)
                                else match.get("home_team", {}).get("name", "?"))
            away_name_display = (match["away_team"][0]["name"] if isinstance(match.get("away_team"), list)
                                else match.get("away_team", {}).get("name", "?"))

        # Get closing odds for CLV.
        # Bets store market as "1X2"/"O/U" and selection as "Home"/"Over 2.5" etc.
        # odds_snapshots uses "1x2"/"over_under_25" and "home"/"over" etc.
        # Normalize before lookup so CLV is actually computed.
        match_id = match["id"]
        raw_market = bet["market"]
        raw_selection = bet["selection"]
        odds_market = _normalize_bet_market(raw_market)
        odds_selection = _normalize_bet_selection(raw_selection)
        closing_odds = get_closing_odds(client, match_id, odds_market, odds_selection)

        # Settle
        settlement = settle_bet_result(bet, score_home, score_away, closing_odds)

        # Get bot bankroll
        bot_id = bet["bot_id"]
        if bot_id not in by_bot:
            bot_data = client.table("bots").select("current_bankroll, name").eq("id", bot_id).execute()
            by_bot[bot_id] = {
                "bankroll": float(bot_data.data[0]["current_bankroll"]) if bot_data.data else 1000.0,
                "name": bot_data.data[0]["name"] if bot_data.data else "unknown",
            }

        new_bankroll = by_bot[bot_id]["bankroll"] + settlement["pnl"]
        by_bot[bot_id]["bankroll"] = new_bankroll

        # Update DB
        client.table("simulated_bets").update({
            "result": settlement["result"],
            "pnl": settlement["pnl"],
            "bankroll_after": new_bankroll,
            "closing_odds": closing_odds,
            "clv": settlement["clv"],
        }).eq("id", bet["id"]).execute()

        settled += 1
        total_pnl += settlement["pnl"]
        if settlement["clv"] is not None:
            clv_values.append(settlement["clv"])

        result_color = "green" if settlement["result"] == "won" else "red"
        clv_str = f"{settlement['clv']:+.1%}" if settlement["clv"] is not None else "-"

        t.add_row(
            f"{home_name_display[:10]} v {away_name_display[:10]}",
            f"{market} {selection}",
            f"{score_home}-{score_away}",
            f"[{result_color}]{settlement['result'].upper()}[/{result_color}]",
            f"[{result_color}]{settlement['pnl']:+.2f}[/{result_color}]",
            clv_str,
        )

    # Update bot bankrolls
    for bot_id, data in by_bot.items():
        client.table("bots").update({
            "current_bankroll": data["bankroll"]
        }).eq("id", bot_id).execute()

    console.print(t)

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None
    wins = sum(1 for b in pending[:settled] if b.get("result") == "pending")

    console.print(f"\n[bold]Settlement complete:[/bold]")
    console.print(f"  Settled: {settled} | Skipped (no result): {skipped}")
    console.print(f"  Total P&L: [{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.2f}[/]")
    if avg_clv is not None:
        clv_color = "green" if avg_clv > 0 else "red"
        console.print(f"  Avg CLV: [{clv_color}]{avg_clv:+.1%}[/] ({'beating' if avg_clv > 0 else 'behind'} closing line)")

    return settled


def _settle_user_picks(client):
    """Settle user picks (from the frontend prediction tracker) against finished match results."""
    console.print("\n[cyan]Settling user picks...[/cyan]")

    # Fetch pending user picks with their match data
    resp = client.table("user_picks").select(
        "id, match_id, selection, odds, matches(id, score_home, score_away, result, status)"
    ).eq("result", "pending").execute()

    picks = resp.data or []
    if not picks:
        console.print("  No pending user picks.")
        return 0

    settled = 0
    skipped = 0

    for pick in picks:
        match = pick.get("matches")
        if not match or match.get("status") != "finished":
            skipped += 1
            continue

        score_home = match.get("score_home")
        score_away = match.get("score_away")
        if score_home is None or score_away is None:
            skipped += 1
            continue

        # Determine result: compare user selection against match outcome
        selection = pick["selection"].lower()
        match_result = match.get("result", "").lower()  # 'home', 'draw', 'away'

        if selection in ("home", "draw", "away") and match_result:
            won = selection == match_result
        else:
            skipped += 1
            continue

        result_str = "won" if won else "lost"
        client.table("user_picks").update({
            "result": result_str,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", pick["id"]).execute()
        settled += 1

    console.print(f"  {settled} user picks settled | {skipped} skipped (match not finished)")
    return settled


def update_elo_ratings(client):
    """
    P1.3: Update ELO ratings for teams in recently finished matches.
    Simple ELO with K=30, home advantage +100, goal diff multiplier.
    Uses batch load + batch upsert instead of per-team queries.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get yesterday's and today's finished matches with team IDs
    finished = client.table("matches").select(
        "id, home_team_id, away_team_id, score_home, score_away"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

    if not finished:
        return 0

    # Collect all involved team IDs
    team_ids = set()
    for m in finished:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    # Batch load current ELO ratings (one query instead of ~520)
    elo_cache: dict[str, float] = {}
    team_id_list = list(team_ids)
    for i in range(0, len(team_id_list), 200):
        chunk = team_id_list[i:i + 200]
        result = client.table("team_elo_daily").select(
            "team_id, elo_rating, date"
        ).in_("team_id", chunk).order("date", desc=True).limit(5000).execute()
        for r in (result.data or []):
            # Keep only most recent per team (first seen due to desc order)
            if r["team_id"] not in elo_cache:
                elo_cache[r["team_id"]] = float(r["elo_rating"])

    K = 30
    HOME_ADV = 100
    elo_rows = []

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
        gd_mult = max(1.0, (gd + 1) ** 0.5)  # goal diff multiplier

        if m["score_home"] > m["score_away"]:
            actual_h, actual_a = 1.0, 0.0
        elif m["score_home"] < m["score_away"]:
            actual_h, actual_a = 0.0, 1.0
        else:
            actual_h, actual_a = 0.5, 0.5

        new_h = (elo_cache.get(h_id, 1500.0) +
                 K * gd_mult * (actual_h - exp_h))
        new_a = (elo_cache.get(a_id, 1500.0) +
                 K * gd_mult * (actual_a - exp_a))

        elo_cache[h_id] = new_h
        elo_cache[a_id] = new_a

        elo_rows.append({"team_id": h_id, "date": today_str, "elo_rating": round(new_h, 2)})
        elo_rows.append({"team_id": a_id, "date": today_str, "elo_rating": round(new_a, 2)})

    # Batch upsert all ELO rows (chunks of 100 instead of 1 per team)
    updated = 0
    # Deduplicate: keep last computed value per team (a team may appear in multiple matches)
    seen_teams: dict[str, dict] = {}
    for row in elo_rows:
        seen_teams[row["team_id"]] = row
    deduped_rows = list(seen_teams.values())

    for i in range(0, len(deduped_rows), 100):
        chunk = deduped_rows[i:i + 100]
        try:
            client.table("team_elo_daily").upsert(
                chunk, on_conflict="team_id,date"
            ).execute()
            updated += len(chunk)
        except Exception:
            # Fallback to one-by-one
            for row in chunk:
                try:
                    client.table("team_elo_daily").upsert(
                        row, on_conflict="team_id,date"
                    ).execute()
                    updated += 1
                except Exception:
                    pass

    return updated


def update_team_form_cache(client):
    """
    P1.5: Update form cache for teams that played recently.
    Computes rolling 10-match form from DB and stores in team_form_cache.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get yesterday's and today's finished matches
    finished = client.table("matches").select(
        "home_team_id, away_team_id"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

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


def compute_model_evaluations(client):
    """
    P1.4: Aggregate settled bets into model_evaluations by date/market.
    Runs after all bets are settled for the day.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # Get recently settled bets with league info
    bets = client.table("simulated_bets").select(
        "id, market, result, pnl, stake, clv, "
        "match:match_id(league_id)"
    ).neq("result", "pending").gte(
        "pick_time", f"{yesterday_str}T00:00:00"
    ).execute().data

    if not bets:
        return 0

    # Group by market
    from collections import defaultdict
    by_market: dict[str, list] = defaultdict(list)
    for b in bets:
        by_market[b["market"]].append(b)

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
                league_id=None,  # aggregate across all leagues
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


def run_post_mortem(client):
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

    # Get today's settled bets with full context
    bets = client.table("simulated_bets").select(
        "id, market, selection, odds_at_pick, model_probability, edge_percent, "
        "result, pnl, stake, clv, calibrated_prob, alignment_class, kelly_fraction, "
        "odds_drift, news_impact_score, reasoning, "
        "matches(score_home, score_away, "
        "home_team:home_team_id(name), away_team:away_team_id(name), "
        "leagues(name, country, tier))"
    ).neq("result", "pending").gte(
        "pick_time", f"{today_str}T00:00:00"
    ).execute().data

    if not bets:
        return

    # Also get match stats if available
    losses = [b for b in bets if b["result"] == "lost"]
    wins = [b for b in bets if b["result"] == "won"]

    if not losses:
        console.print("  [green]No losses today — no post-mortem needed![/green]")
        return

    # Build context for LLM
    bet_summaries = []
    for b in bets:
        match = b.get("matches", {})
        home = match.get("home_team", [{}])
        away = match.get("away_team", [{}])
        league = match.get("leagues", [{}])
        home_name = home[0]["name"] if isinstance(home, list) else home.get("name", "?")
        away_name = away[0]["name"] if isinstance(away, list) else away.get("name", "?")
        league_name = league[0]["name"] if isinstance(league, list) else league.get("name", "?")
        tier = league[0]["tier"] if isinstance(league, list) else league.get("tier", "?")

        summary = (
            f"{'✗ LOST' if b['result'] == 'lost' else '✓ WON'}: "
            f"{home_name} vs {away_name} ({league_name}, T{tier}) "
            f"| Score: {match.get('score_home', '?')}-{match.get('score_away', '?')} "
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
                console.print(f"  [bold]Patterns:[/bold]")
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
            except Exception:
                pass

    except Exception as e:
        console.print(f"  [yellow]Post-mortem LLM error: {e}[/yellow]")


def run_report():
    """Show cumulative P&L and CLV across all settled bets"""
    client = get_client()
    console.print("[bold]═══ OddsIntel P&L Report ═══[/bold]\n")

    bots = client.table("bots").select("id, name, starting_bankroll, current_bankroll").execute().data

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
        bets = client.table("simulated_bets").select(
            "result, pnl, stake, clv"
        ).eq("bot_id", bot["id"]).neq("result", "pending").execute().data

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
