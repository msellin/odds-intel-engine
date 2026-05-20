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
    fetch_coolbet_events,
    fetch_main_markets,
    fetch_sidebets,
    fuzzy_match_event,
    search_coolbet_event,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coolbet_explorer")
console = Console()


# ── Market parsing ────────────────────────────────────────────────────────────
#
# Coolbet bet_offers come in as { criterion_label, outcomes: [{label, odds_decimal, ...}] }.
# Map each to one or more (market, selection, handicap_line) tuples in our schema.
# `criterion_label` is already lower-cased by _parse_event/fetch_sidebets.

_OU_LINE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
_AH_LINE_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)")


def _to_line(text: str) -> float | None:
    m = _OU_LINE_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _signed_line(text: str) -> float | None:
    """Pull a signed line (eg -1.25, +0.5) out of a criterion label like
    'asian handicap -1.25'. Returns None if none found."""
    m = _AH_LINE_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _market_for_ou_line(line: float) -> str | None:
    """Map an OU line float to our market name."""
    # We only track .5 lines (we don't bet AH-style quarter-totals here).
    if abs(line * 10 - round(line * 10)) > 1e-6:
        return None
    cents = round(line * 10)
    if cents not in {5, 15, 25, 35, 45}:
        return None
    return f"over_under_{cents:02d}"


def parse_bet_offer(bo: dict) -> list[tuple[str, str, float, float | None]]:
    """Return list of (market, selection, odds, handicap_line_or_None) tuples
    for one Coolbet bet_offer. Empty list if criterion is unknown / unsupported."""
    label = bo.get("criterion_label", "")
    outs = bo.get("outcomes") or []
    rows: list[tuple[str, str, float, float | None]] = []

    def _add(market: str, selection: str, odds: float, line: float | None = None) -> None:
        if odds and odds > 1.0:
            rows.append((market, selection, odds, line))

    # ── 1X2 ────────────────────────────────────────────────────────────────
    if any(p in label for p in ("match result", "full time result", "1x2")):
        for oc in outs:
            ol = (oc.get("label") or "").strip().lower()
            if ol in {"1", "home"}:
                _add("1x2", "Home", oc["odds_decimal"])
            elif ol in {"x", "draw"}:
                _add("1x2", "Draw", oc["odds_decimal"])
            elif ol in {"2", "away"}:
                _add("1x2", "Away", oc["odds_decimal"])
        return rows

    # ── OU total goals ─────────────────────────────────────────────────────
    if any(p in label for p in ("over/under", "total goals", "goal line")):
        line = _to_line(label)
        if line is None:
            return rows
        market = _market_for_ou_line(line)
        if market is None:
            return rows
        for oc in outs:
            ol = (oc.get("label") or "").lower()
            if "over" in ol:
                _add(market, "over", oc["odds_decimal"])
            elif "under" in ol:
                _add(market, "under", oc["odds_decimal"])
        return rows

    # ── BTTS ───────────────────────────────────────────────────────────────
    if any(p in label for p in ("both teams to score", "btts")):
        for oc in outs:
            ol = (oc.get("label") or "").lower()
            if ol == "yes":
                _add("btts", "yes", oc["odds_decimal"])
            elif ol == "no":
                _add("btts", "no", oc["odds_decimal"])
        return rows

    # ── Double chance ──────────────────────────────────────────────────────
    if "double chance" in label:
        for oc in outs:
            ol = (oc.get("label") or "").strip()
            if ol in {"1X", "X2", "12"}:
                _add("double_chance", ol, oc["odds_decimal"])
        return rows

    # ── Asian handicap ─────────────────────────────────────────────────────
    if "asian handicap" in label:
        # Two layouts seen on Kambi-stack books like Coolbet:
        #   A) one bet_offer per line: criterion_label = "asian handicap -1.25"
        #      (signed from home perspective), outcomes = [{label:"Home"|"1"},
        #      {label:"Away"|"2"}]. Both home/away rows share the same line.
        #   B) outcomes carry side AND line: [{label:"1 -1.25"}, {label:"2 +1.25"}].
        #      Each outcome's line is from its own team's perspective; flip the
        #      away one so handicap_line always describes home (pipeline convention).
        criterion_line = _signed_line(label)
        for oc in outs:
            tokens = (oc.get("label") or "").strip().split()
            side = tokens[0].lower() if tokens else ""
            per_outcome_line: float | None = None
            if len(tokens) >= 2:
                try:
                    per_outcome_line = float(tokens[-1].replace(",", "."))
                except ValueError:
                    per_outcome_line = None

            if side in {"1", "home"}:
                line = per_outcome_line if per_outcome_line is not None else criterion_line
                if line is not None:
                    _add("asian_handicap", "home", oc["odds_decimal"], line)
            elif side in {"2", "away"}:
                if per_outcome_line is not None:
                    line = -per_outcome_line  # away-perspective → home-perspective
                else:
                    line = criterion_line     # already home-perspective from criterion
                if line is not None:
                    _add("asian_handicap", "away", oc["odds_decimal"], line)
        return rows

    return rows  # Unknown / unsupported market — skip silently


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
    coolbet_event: dict,
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """Store all parsable bet_offers for one match. Returns (parsed, stored)."""
    parsed = 0
    stored = 0
    kickoff_iso = coolbet_event.get("start") or ""
    minutes_to_ko = _minutes_to_kickoff(kickoff_iso)

    for bo in coolbet_event.get("bet_offers") or []:
        for market, selection, odds, line in parse_bet_offer(bo):
            parsed += 1
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
    return parsed, stored


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

        # search doesn't include bet_offers. fo-match POST gives main markets
        # (1X2 + main OU 2.5 etc.); sidebets gives the rest (OU other lines,
        # BTTS, AH, DC, ...). Concat both — duplicates are rare and harmless.
        main_offers = fetch_main_markets(session, [ev["id"]]).get(int(ev["id"]), [])
        side_offers = fetch_sidebets(session, ev["id"])
        ev["bet_offers"] = main_offers + side_offers
        parsed, stored = store_coolbet_snapshots_for_match(m["id"], ev, dry_run=dry_run)
        matched += 1
        parsed_total += parsed
        stored_total += stored
        for bo in ev.get("bet_offers") or []:
            for market, *_ in parse_bet_offer(bo):
                by_market[market] = by_market.get(market, 0) + 1
        log.info("[%d/%d] %s vs %s → %d markets parsed, %d stored",
                 i, len(matches), home, away, parsed, stored)
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

    main_offers = fetch_main_markets(session, [ev["id"]]).get(int(ev["id"]), [])
    side_offers = fetch_sidebets(session, ev["id"])
    ev["bet_offers"] = main_offers + side_offers
    t = Table(show_header=True, title=f"Coolbet markets for event #{ev['id']}")
    t.add_column("Market")
    t.add_column("Selection")
    t.add_column("Line", justify="right")
    t.add_column("Odds", justify="right")
    seen = 0
    for bo in ev.get("bet_offers") or []:
        for market, selection, odds, line in parse_bet_offer(bo):
            t.add_row(market, selection, f"{line:+.2f}" if line is not None else "—", f"{odds:.3f}")
            seen += 1
    if seen == 0:
        console.print("[yellow]Event found but no parseable markets.[/yellow]")
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
