"""Coolbet read-only odds explorer.

Companion to `coolbet_placer.py`. Shares the authenticated CoolbetSession + the
fo-category / search / sidebets helpers but never POSTs a bet — purely
explore-and-ingest. Built to answer "what does Coolbet actually price for the
matches in our DB?", which is the data we need to evaluate COOLBET-OR-PIN-REQUIRED
as a replacement for the current Pinnacle-only OU quality gate.

Two modes:
  --match-id <uuid>            One-shot: print all Coolbet markets for one match.
  (default)                    Bulk: snapshot all matches in DB kicking off
                               within --days days, store in odds_snapshots.

Behaviour notes:
  • Matches our matches → Coolbet events via the same search-then-fo-category
    fallback the placer uses (and the same fuzzy threshold). Skips matches with
    no Coolbet fixture.
  • Stores via store_coolbet_odds_snapshot — one row per (market, selection).
  • Sleeps 0.25s between sidebets calls (one per match) to be polite.
  • --dry-run prints what would be stored without writing.
  • --no-store with one-shot mode is implied (the one-shot view always prints).

Markets parsed: 1X2, OU 0.5/1.5/2.5/3.5/4.5, BTTS, double_chance,
asian_handicap (with line). Anything else is dropped on the floor with a debug
log; extend MARKET_PARSERS below if a missing market matters.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.supabase_client import (
    execute_query,
    store_coolbet_odds_snapshot,
)
from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    _parse_event,
    _FO_MATCH_URL,
    _ODDS_URL,
    _SIDEBETS_URL,
    fetch_coolbet_events,
    fuzzy_match_event,
    search_coolbet_event,
)

# Odds for line markets (OU, AH, handicap) live at a different endpoint than
# simple markets (1X2, BTTS, DC). Discovered 2026-05-20 via DevTools capture.
_ODDS_LINE_URL = "https://www.coolbet.com/s/sb-odds/odds/current/fo-line/"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coolbet_explorer")
console = Console()


# ── Coolbet API: markets + odds (new schema, 2026-05-20) ─────────────────────
#
# Coolbet now serves markets and odds from separate endpoints:
#   POST /s/sbgate/sports/fo-match               → matches[].markets[] (no odds)
#   GET  /s/sbgate/sports/fo-market/sidebets     → markets[].markets[] (no odds)
#   POST /s/sb-odds/odds/current/fo              → simple-market odds (line=0)
#   POST /s/sb-odds/odds/current/fo-line/        → line-market odds (OU, AH)
#
# Both odds endpoints return: { "<outcome_id>": {value: <decimal>, status, ...} }
# Markets carry `market_type_id` (stable, locale-independent) and outcomes carry
# `result_key` ("[Home]"/"Draw"/"[Away]"/"Over"/"Under"/"Yes"/"No"/"1X"/...).
# Mapping by these instead of English labels is way more robust than the old
# Kambi-style criterion-label substring matching.


def fetch_match_markets(session: CoolbetSession, match_id: int) -> list[dict]:
    """Combine fo-match + sidebets into one flat list of markets for a match.
    Each market: {id, name, line, market_type_id, outcomes:[{id, name, result_key}]}.
    Odds are NOT included — call fetch_odds_for_markets to fill those in."""
    flat: list[dict] = []

    # fo-match: main markets (1X2 + headline OU/BTTS for the league)
    r = session.post(_FO_MATCH_URL, json={
        "language": "en", "country": "EE", "layout": "EUROPEAN",
        "locale": "en", "matchIds": [str(match_id)],
    })
    if r.status_code == 200:
        for m in (r.json().get("matches") or []):
            flat.extend(m.get("markets") or [])
    else:
        log.warning("fo-match %s returned %d", match_id, r.status_code)

    # sidebets: side markets. Response groups individual line-markets under
    # `markets[].markets[]` (group → line variants). Flatten.
    r = session.get(_SIDEBETS_URL, params={
        "matchId": match_id, "country": "EE", "language": "en",
        "layout": "EUROPEAN", "limit": 13, "matchStatus": "OPEN",
    })
    if r.status_code == 200:
        for group in (r.json().get("markets") or []):
            mtid = group.get("market_type_id")
            for sub in (group.get("markets") or []):
                if mtid and "market_type_id" not in sub:
                    sub["market_type_id"] = mtid
                flat.append(sub)
    else:
        log.warning("sidebets %s returned %d", match_id, r.status_code)
    return flat


def fetch_odds_for_markets(
    session: CoolbetSession, markets: list[dict],
) -> dict[int, float]:
    """Resolve odds for every outcome across the given markets. Splits market_ids
    by simple (line==0) vs line (line!=0) and POSTs to the matching endpoint.
    Returns {outcome_id: decimal_odds}."""
    simple_ids: list[int] = []
    line_ids:   list[int] = []
    for mkt in markets:
        mid = mkt.get("id")
        if not mid:
            continue
        if _is_simple(mkt):
            simple_ids.append(int(mid))
        else:
            line_ids.append(int(mid))

    out: dict[int, float] = {}
    if simple_ids:
        r = session.post(_ODDS_URL, json={
            "where": {"market_id": {"in": simple_ids}},
        })
        if r.status_code == 200:
            _harvest_odds(r.json(), out)
    if line_ids:
        # /fo-line/ takes a nested array (groups of related lines). Single group
        # of everything works in practice and avoids guessing the grouping.
        r = session.post(_ODDS_LINE_URL, json={"marketIds": [line_ids]})
        if r.status_code == 200:
            _harvest_odds(r.json(), out)
    return out


def _is_simple(mkt: dict) -> bool:
    line = mkt.get("line")
    if line is None:
        return True
    try:
        return float(line) == 0.0
    except (TypeError, ValueError):
        return str(line).strip() in ("", "0", "0.0")


def _harvest_odds(payload, into: dict[int, float]) -> None:
    """Flatten Coolbet's {outcome_id_str: {value, ...}} odds responses into a
    single int→float map."""
    if not isinstance(payload, dict):
        return
    for k, v in payload.items():
        try:
            oid = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            val = v.get("value")
            if val is None:
                continue
            try:
                into[oid] = float(val)
            except (TypeError, ValueError):
                pass


# ── Market → our schema mapping ───────────────────────────────────────────────
#
# Map (market_type_id, result_key, line) → our (market_name, selection,
# handicap_line). Fallbacks on market.name when market_type_id is unknown so
# new markets degrade gracefully instead of silently dropping.
#
# Known market_type_ids (confirmed from probe responses):
#   81  → Match Result (1X2)
#   818 → Total Goals Over / Under
# More will be added as we observe them.

_MTID_1X2  = {81}
_MTID_OU   = {818}
_MTID_BTTS = set()          # populate when observed
_MTID_DC   = set()
_MTID_AH   = set()


def _ou_market_for_line(line: float) -> str | None:
    """OU .5 lines we ingest: 0.5, 1.5, 2.5, 3.5, 4.5."""
    if line is None:
        return None
    if abs(line * 10 - round(line * 10)) > 1e-6:
        return None
    cents = round(line * 10)
    if cents not in {5, 15, 25, 35, 45}:
        return None
    return f"over_under_{cents:02d}"


def parse_market(mkt: dict, odds_map: dict[int, float]) -> list[tuple[str, str, float, float | None]]:
    """Return list of (market_name, selection, odds, handicap_line) rows
    for one Coolbet market dict, looking up odds by outcome_id."""
    rows: list[tuple[str, str, float, float | None]] = []
    mtid = mkt.get("market_type_id")
    name = (mkt.get("name") or "").lower()
    line_raw = mkt.get("line")
    try:
        line_val = float(line_raw) if line_raw not in (None, "") else None
    except (TypeError, ValueError):
        line_val = None

    def _add(market: str, selection: str, oid, hline: float | None = None) -> None:
        try:
            odds = odds_map.get(int(oid))
        except (TypeError, ValueError):
            return
        if odds and odds > 1.0:
            rows.append((market, selection, odds, hline))

    is_1x2  = mtid in _MTID_1X2  or "match result" in name or "1x2" in name
    is_ou   = mtid in _MTID_OU   or "total goals over" in name or "over / under" in name
    is_btts = mtid in _MTID_BTTS or "both teams to score" in name or "btts" in name
    is_dc   = mtid in _MTID_DC   or "double chance" in name
    is_ah   = mtid in _MTID_AH   or "asian handicap" in name

    if is_1x2:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]")
            if rk == "Home":
                _add("1x2", "Home", oc.get("id"))
            elif rk == "Draw":
                _add("1x2", "Draw", oc.get("id"))
            elif rk == "Away":
                _add("1x2", "Away", oc.get("id"))
        return rows

    if is_ou:
        market = _ou_market_for_line(line_val) if line_val is not None else None
        if market is None:
            return rows
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            if rk == "over":
                _add(market, "over", oc.get("id"))
            elif rk == "under":
                _add(market, "under", oc.get("id"))
        return rows

    if is_btts:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            if rk == "yes":
                _add("btts", "yes", oc.get("id"))
            elif rk == "no":
                _add("btts", "no", oc.get("id"))
        return rows

    if is_dc:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip()
            if rk in {"1X", "X2", "12"}:
                _add("double_chance", rk, oc.get("id"))
        return rows

    if is_ah:
        # AH: market.line is the home-perspective handicap. Outcomes are Home/Away.
        # Both rows share the same handicap_line (already home-perspective).
        if line_val is None:
            return rows
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]")
            if rk == "Home":
                _add("asian_handicap", "home", oc.get("id"), line_val)
            elif rk == "Away":
                _add("asian_handicap", "away", oc.get("id"), line_val)
        return rows

    return rows  # Unknown — degrade silently


# ── DB layer ──────────────────────────────────────────────────────────────────


def load_matches_in_window(days: int) -> list[dict]:
    """Pull pre-KO matches from our DB kicking off within `days` days."""
    return execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.date > NOW()
          AND m.date < NOW() + INTERVAL '%s days'
          AND m.status = 'scheduled'
        ORDER BY m.date
        """ % int(days),
        (),
    )


def load_value_bet_matches(days: int) -> list[dict]:
    """Pull matches that currently have at least one pending value bet from any
    active bot, kicking off within `days` days. This is the actionable set —
    matches we'd actually place real money on. Far smaller (and far better-
    targeted) than `load_matches_in_window`."""
    return execute_query(
        """
        SELECT DISTINCT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league,
               COUNT(*) OVER (PARTITION BY m.id) AS bet_count
        FROM simulated_bets sb
        JOIN bots b   ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        JOIN teams ht  ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE sb.result = 'pending'
          AND m.date > NOW()
          AND m.date < NOW() + INTERVAL '%s days'
          AND b.is_active = true
          AND b.retired_at IS NULL
        ORDER BY m.date
        """ % int(days),
        (),
    )


def store_coolbet_snapshots_for_match(
    match_id: str,
    coolbet_markets: list[dict],
    odds_map: dict[int, float],
    *,
    dry_run: bool,
    kickoff_iso: str = "",
) -> tuple[int, int, dict[str, int]]:
    """Parse + store all markets for one match. Returns (parsed, stored, by_market)."""
    parsed = 0
    stored = 0
    by_market: dict[str, int] = {}
    minutes_to_ko = _minutes_to_kickoff(kickoff_iso)

    for mkt in coolbet_markets:
        for market, selection, odds, line in parse_market(mkt, odds_map):
            parsed += 1
            by_market[market] = by_market.get(market, 0) + 1
            if dry_run:
                continue
            try:
                if market == "asian_handicap":
                    _store_with_handicap(match_id, market, selection, odds, line, minutes_to_ko)
                else:
                    store_coolbet_odds_snapshot(match_id, market, selection, odds, minutes_to_ko)
                stored += 1
            except Exception as e:
                log.warning("Store failed for %s %s: %s", market, selection, e)
    return parsed, stored, by_market


def _store_with_handicap(
    match_id: str, market: str, selection: str, odds: float,
    line: float | None, minutes_to_ko: int | None,
) -> None:
    """AH rows need handicap_line populated. store_coolbet_odds_snapshot doesn't
    expose handicap_line, so insert directly."""
    from workers.api_clients.db import get_conn
    now = datetime.now(timezone.utc).isoformat()
    is_closing = minutes_to_ko is not None and abs(minutes_to_ko) <= 5
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO odds_snapshots
                   (match_id, bookmaker, market, selection, odds, timestamp,
                    is_closing, minutes_to_kickoff, handicap_line)
                   VALUES (%s, 'Coolbet', %s, %s, %s, %s, %s, %s, %s)""",
                (match_id, market, selection, odds, now,
                 is_closing, minutes_to_ko, line),
            )
            conn.commit()


def _minutes_to_kickoff(iso: str) -> int | None:
    if not iso:
        return None
    try:
        ko = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = ko - datetime.now(timezone.utc)
    return int(delta.total_seconds() // 60)


# ── Bulk + one-shot drivers ───────────────────────────────────────────────────


def run_bulk(
    days: int, dry_run: bool, sleep_s: float, limit: int | None,
    *, bets_only: bool = False,
) -> None:
    matches = load_value_bet_matches(days) if bets_only else load_matches_in_window(days)
    if limit:
        matches = matches[:limit]
    if not matches:
        msg = "No pending value-bet matches in window." if bets_only else "No upcoming matches in DB window."
        console.print(f"[yellow]{msg}[/yellow]")
        return
    label = "value-bet matches" if bets_only else "matches from DB"
    console.print(f"[cyan]Loaded {len(matches)} {label} (window={days}d){' [DRY-RUN]' if dry_run else ''}[/cyan]")

    session = CoolbetSession()
    category_cache: list[dict] | None = None

    matched = 0
    parsed_total = 0
    stored_total = 0
    by_market: dict[str, int] = {}
    missed_leagues: dict[str, int] = {}
    matched_leagues: dict[str, int] = {}

    for i, m in enumerate(matches, 1):
        home, away = m["home"], m["away"]
        league = m.get("league") or "—"
        ev = search_coolbet_event(session, home, away)
        if ev is None:
            if category_cache is None:
                console.print("[dim]Search miss — loading full fo-category once[/dim]")
                try:
                    category_cache = fetch_coolbet_events(session)
                except Exception as e:
                    # fo-category endpoint has 404'd in production at least once
                    # (Coolbet seems to have moved or retired it). Degrade to
                    # search-only — matches the search misses are skipped.
                    log.warning("fo-category unavailable (%s) — falling back to search-only", e)
                    category_cache = []
            ev = fuzzy_match_event(home, away, category_cache) if category_cache else None
        if ev is None:
            missed_leagues[league] = missed_leagues.get(league, 0) + 1
            log.info("[%d/%d] no Coolbet event: %s vs %s (%s)", i, len(matches), home, away, league)
            continue
        matched_leagues[league] = matched_leagues.get(league, 0) + 1

        # New flow: fetch markets (fo-match + sidebets), then fetch odds
        # (split by simple vs line endpoint), then stitch.
        markets = fetch_match_markets(session, int(ev["id"]))
        odds_map = fetch_odds_for_markets(session, markets)
        parsed, stored, mkt_counts = store_coolbet_snapshots_for_match(
            m["id"], markets, odds_map,
            dry_run=dry_run, kickoff_iso=ev.get("start") or "",
        )
        matched += 1
        parsed_total += parsed
        stored_total += stored
        for k, v in mkt_counts.items():
            by_market[k] = by_market.get(k, 0) + v
        log.info("[%d/%d] %s vs %s → %d markets, %d odds, %d stored",
                 i, len(matches), home, away, len(markets), parsed, stored)
        time.sleep(sleep_s)

    console.print()
    t = Table(show_header=True, title="Coolbet ingest summary")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    t.add_row("Matches in DB window", str(len(matches)))
    t.add_row("Matched on Coolbet", str(matched))
    t.add_row("Rows parsed (all markets)", str(parsed_total))
    t.add_row("Rows stored", "0 (dry-run)" if dry_run else str(stored_total))
    console.print(t)

    if by_market:
        t2 = Table(show_header=True, title="By market")
        t2.add_column("Market")
        t2.add_column("Rows", justify="right")
        for k, v in sorted(by_market.items(), key=lambda kv: -kv[1]):
            t2.add_row(k, str(v))
        console.print(t2)

    # League-level match/miss split — most actionable view for "what does
    # Coolbet actually cover for us".
    all_leagues = sorted(set(matched_leagues) | set(missed_leagues))
    if all_leagues:
        t3 = Table(show_header=True, title="By league (matched / missed)")
        t3.add_column("League")
        t3.add_column("Matched", justify="right")
        t3.add_column("Missed", justify="right")
        t3.add_column("Match %", justify="right")
        for lg in all_leagues:
            mt = matched_leagues.get(lg, 0)
            ms = missed_leagues.get(lg, 0)
            pct = 100.0 * mt / (mt + ms) if (mt + ms) else 0
            t3.add_row(lg, str(mt), str(ms), f"{pct:.0f}%")
        console.print(t3)


def run_one_shot(match_id: str) -> None:
    rows = execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.id = %s
        """,
        (match_id,),
    )
    if not rows:
        console.print(f"[red]No match found with id={match_id}[/red]")
        return
    m = rows[0]
    console.print(f"[cyan]{m['home']} vs {m['away']} — {m['league']} — {m['date']}[/cyan]")

    session = CoolbetSession()
    ev = search_coolbet_event(session, m["home"], m["away"])
    if ev is None:
        console.print("[dim]Search miss — loading full fo-category[/dim]")
        try:
            ev = fuzzy_match_event(m["home"], m["away"], fetch_coolbet_events(session))
        except Exception as e:
            console.print(f"[yellow]fo-category unavailable ({e}). Search-only mode.[/yellow]")
            ev = None
    if ev is None:
        console.print("[yellow]No matching Coolbet event.[/yellow]")
        return

    markets = fetch_match_markets(session, int(ev["id"]))
    odds_map = fetch_odds_for_markets(session, markets)
    t = Table(show_header=True, title=f"Coolbet markets for event #{ev['id']}")
    t.add_column("Market")
    t.add_column("Selection")
    t.add_column("Line", justify="right")
    t.add_column("Odds", justify="right")
    seen = 0
    for mkt in markets:
        for market, selection, odds, line in parse_market(mkt, odds_map):
            t.add_row(market, selection, f"{line:+.2f}" if line is not None else "—", f"{odds:.3f}")
            seen += 1
    if seen == 0:
        console.print(f"[yellow]Event found ({len(markets)} markets / {len(odds_map)} odds) "
                      f"but parser recognised none. Probable cause: market_type_id mappings "
                      f"need extension. Run probe and update _MTID_* sets.[/yellow]")
        return
    console.print(t)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--match-id", help="Inspect a single match by our matches.id")
    ap.add_argument("--days", type=int, default=2, help="Bulk window in days (default 2)")
    ap.add_argument("--limit", type=int, help="Cap bulk to first N matches (testing)")
    ap.add_argument("--sleep", type=float, default=0.25, help="Seconds between sidebets calls")
    ap.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    ap.add_argument("--bets-only", action="store_true",
                    help="Bulk only over matches that have a pending value bet (active bots)")
    args = ap.parse_args()

    if args.match_id:
        run_one_shot(args.match_id)
    else:
        run_bulk(args.days, args.dry_run, args.sleep, args.limit, bets_only=args.bets_only)


if __name__ == "__main__":
    main()
