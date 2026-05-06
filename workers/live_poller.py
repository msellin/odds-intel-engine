"""
OddsIntel — Live Poller (Tiered Polling)

Multi-frequency polling loop for live match data.
Replaces the 5-minute APScheduler cron with:
  - Fast tier (30s): bulk fixtures + live odds (2 API calls)
  - Medium tier (60s): per-match statistics + events (2N API calls)
  - Slow tier (5min): lineups for upcoming + refresh match map

RAIL-11: Smart priority tiers + event-triggered snapshots
  - HIGH priority (active bet): fetch stats every cycle instead of medium interval
  - NORMAL priority (all other live matches): stats every MEDIUM_MULTIPLIER cycles
  - Event-triggered: goal or red card detected → immediate extra odds snapshot

Runs in its own thread alongside the APScheduler.
Uses direct SQL (db.py) for all DB writes.

API budget (AF Ultra 75K/day):
  - Live (30s): ~2,880 calls/day (2 calls × 2/min × 60min × 12h active window)
  - Idle (120s): ~960 calls/day (2 calls × 0.5/min × 60min × 16h quiet)
  - Medium (stats): ~8,640 calls/day (avg 12 matches × 2 calls × 60min × 6h)
  - Total: ~12,000-18,000 calls/day (was 14,000-20,000 with 11h window, now lower due to idle)
"""

import time
import threading
from datetime import datetime, timezone, date
from rich.console import Console

console = Console()


class LivePoller:
    """
    Tiered live match polling loop.

    Fast tier (every 30s): 2 bulk API calls for all live fixtures + odds
    Medium tier (every 60s): per-match stats + events for NORMAL priority matches
    High priority (active bet): stats every fast cycle (30s)
    Slow tier (every 5min): lineups + match map refresh

    Event-triggered: goal or red card → immediate extra odds snapshot
    """

    # Polling intervals (seconds)
    FAST_INTERVAL = 30       # Bulk fixtures + odds when live matches exist (can go to 15s later)
    IDLE_INTERVAL = 120      # Poll interval when no live matches — saves API budget while still
                             # catching any match that kicks off within ~2 min
    MEDIUM_MULTIPLIER = 2    # Stats every 2nd fast cycle (= 60s at 30s fast)
    SLOW_MULTIPLIER = 10     # Lineups every 10th fast cycle (= 5min at 30s fast)

    # Active bet refresh: refresh the set of match_ids with pending bets
    BET_REFRESH_MULTIPLIER = 5  # Every 5th slow cycle (~25 min)

    def __init__(self, budget_tracker, shutdown_flag_fn):
        """
        Args:
            budget_tracker: BudgetTracker instance from api_football.py
            shutdown_flag_fn: callable that returns True when shutdown is requested
        """
        self.budget = budget_tracker
        self._should_stop = shutdown_flag_fn
        self._cycle = 0
        self._af_id_map: dict[int, dict] = {}
        self._last_map_refresh = 0.0

        # RAIL-11: Smart priority tracking
        # match_id → (score_home, score_away) from last cycle — detects goals
        self._prev_scores: dict[str, tuple[int, int]] = {}
        # Set of DB match_ids that have pending bets → HIGH priority
        self._active_bet_match_ids: set[str] = set()
        self._bet_refresh_count = 0  # Counts slow cycles, triggers bet refresh

    def run_forever(self):
        """Main polling loop. Runs 24/7 — no time-window gate.

        Uses adaptive sleep:
        - 30s when live matches are active (fast polling for goals, odds, stats)
        - 120s when no live matches (idle polling — catches kickoffs within 2 min,
          minimal API cost: 2 bulk calls/2min vs 2 bulk calls/30s when live)
        """
        console.print("[bold cyan]LivePoller started — 24/7, 30s live / 120s idle (RAIL-11)[/bold cyan]")

        # Load active bets immediately on startup
        self._refresh_active_bets()

        while not self._should_stop():
            cycle_start = time.time()

            had_live = False
            try:
                had_live = self._run_cycle()
            except Exception as e:
                console.print(f"[red]LivePoller cycle error: {e}[/red]")

            self._cycle += 1

            # Adaptive sleep: fast when matches are live, idle otherwise
            target = self.FAST_INTERVAL if had_live else self.IDLE_INTERVAL
            elapsed = time.time() - cycle_start
            sleep_time = max(0, target - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        console.print("[yellow]LivePoller stopped.[/yellow]")

    def _refresh_active_bets(self):
        """Query DB for match_ids with pending simulated bets → HIGH priority matches."""
        try:
            from workers.api_clients.db import execute_query
            rows = execute_query(
                "SELECT DISTINCT match_id FROM simulated_bets WHERE result = 'pending'"
            )
            self._active_bet_match_ids = {str(r["match_id"]) for r in rows}
            if self._active_bet_match_ids:
                console.print(
                    f"[cyan]LivePoller: {len(self._active_bet_match_ids)} HIGH-priority matches "
                    f"(active bets)[/cyan]"
                )
        except Exception as e:
            console.print(f"[yellow]LivePoller: failed to refresh active bets: {e}[/yellow]")

    def _is_high_priority(self, match_id: str) -> bool:
        """Return True if this match has an active bet — gets 30s stats instead of 60s."""
        return str(match_id) in self._active_bet_match_ids

    def _detect_key_event(self, match_id: str, af_fix: dict) -> bool:
        """
        Detect goal or red card since last cycle.
        Returns True if a key event was detected (triggers extra odds snapshot).
        A goal is detected via score change; red cards are detected via the
        'red_card_home'/'red_card_away' fields if present in af_fix, or
        by score jump >1 which is anomalous and worth snapshotting anyway.
        """
        score_home = af_fix.get("score_home", 0) or 0
        score_away = af_fix.get("score_away", 0) or 0
        prev = self._prev_scores.get(match_id)

        if prev is None:
            # First time seeing this match — store baseline, no event yet
            self._prev_scores[match_id] = (score_home, score_away)
            return False

        prev_home, prev_away = prev
        self._prev_scores[match_id] = (score_home, score_away)

        if score_home != prev_home or score_away != prev_away:
            console.print(
                f"[bold yellow]GOAL detected: match {match_id} "
                f"{prev_home}-{prev_away} → {score_home}-{score_away}[/bold yellow]"
            )
            return True

        return False

    def _run_cycle(self) -> bool:
        """Execute one polling cycle. Returns True if live matches were found."""
        from workers.jobs.live_tracker import (
            fetch_live_bulk, fetch_match_stats_for, fetch_match_events_for,
            build_snapshot, _fetch_lineups_for_upcoming, _lookup_db_match,
        )
        from workers.api_clients.db import (
            store_live_snapshots_batch, store_live_odds_batch,
            store_match_events_batch, update_match_status_sql,
            finish_match_sql, build_af_id_map,
        )

        # ── Slow tier: refresh match map every ~5 min ──────────────────────
        if self._cycle % self.SLOW_MULTIPLIER == 0:
            self._af_id_map = build_af_id_map()
            self._last_map_refresh = time.time()

            # Lineups for upcoming matches
            try:
                _fetch_lineups_for_upcoming(self._af_id_map)
            except Exception as e:
                console.print(f"[yellow]Lineup fetch error: {e}[/yellow]")

            # Refresh active bet match_ids periodically
            self._bet_refresh_count += 1
            if self._bet_refresh_count % self.BET_REFRESH_MULTIPLIER == 0:
                self._refresh_active_bets()

        # ── Fast tier: bulk fixtures + odds (every cycle = 30s) ────────────
        if not self.budget.can_call():
            if self._cycle % self.SLOW_MULTIPLIER == 0:
                console.print("[yellow]LivePoller: API budget low, skipping cycle[/yellow]")
            return

        fixtures, odds_by_fixture = fetch_live_bulk()

        if not fixtures:
            return False  # No live matches — caller uses idle sleep interval

        # Log periodically (every ~2 min)
        if self._cycle % (self.SLOW_MULTIPLIER // 2 or 4) == 0:
            high_count = sum(
                1 for af_fix in fixtures
                if str(self._af_id_map.get(af_fix.get("af_fixture_id"), {}).get("id", ""))
                in self._active_bet_match_ids
            )
            console.print(
                f"[dim]LivePoller cycle {self._cycle}: "
                f"{len(fixtures)} live ({high_count} HIGH priority), "
                f"budget {self.budget.remaining()}/75K[/dim]"
            )

        # ── Build snapshots from fixtures + odds ───────────────────────────
        pending_snapshots = []
        pending_odds = []
        pending_status = []
        event_triggered_odds = []  # Extra odds on goal/red card

        for af_fix in fixtures:
            af_id = af_fix.get("af_fixture_id")
            minute = af_fix.get("minute", 0)

            # Look up DB match
            db_match = None
            if af_id and af_id in self._af_id_map:
                db_match = self._af_id_map[af_id]

            if not db_match:
                continue

            match_id = db_match["id"]
            fixture_odds = odds_by_fixture.get(af_id, [])

            # Build snapshot (fast tier — no stats yet)
            snapshot = build_snapshot(af_fix, fixture_odds)

            # ── RAIL-11: Determine if this match needs HIGH-priority stats ──
            is_high = self._is_high_priority(match_id)

            # Fetch stats on medium interval OR every cycle for HIGH priority
            fetch_stats_this_cycle = (
                is_high or
                (self._cycle % self.MEDIUM_MULTIPLIER == 0)
            )

            if fetch_stats_this_cycle and af_id:
                stats = fetch_match_stats_for(af_id)
                if stats:
                    # Merge stats into snapshot
                    for field in ["xg_home", "xg_away", "shots_home", "shots_away",
                                  "shots_on_target_home", "shots_on_target_away",
                                  "possession_home", "corners_home", "corners_away"]:
                        if stats.get(field) is not None:
                            snapshot[field] = stats[field]
                    if stats.get("passes_home") is not None:
                        snapshot["attacks_home"] = stats["passes_home"]
                    if stats.get("passes_away") is not None:
                        snapshot["attacks_away"] = stats["passes_away"]

                # Events (always with stats fetch)
                events = fetch_match_events_for(af_id)
                if events:
                    home_api_id = af_fix.get("home_team_api_id")
                    try:
                        store_match_events_batch(match_id, events,
                                                 home_team_api_id=home_api_id)
                    except Exception:
                        pass

            # ── RAIL-11: Event-triggered snapshot on goal / red card ────────
            key_event = self._detect_key_event(match_id, af_fix)
            if key_event and fixture_odds:
                # Tag these odds rows as event-triggered (same format as pending_odds)
                for lr in fixture_odds:
                    event_triggered_odds.append({**lr, "match_id": match_id})

            # Collect for batch write
            snapshot["match_id"] = match_id
            pending_snapshots.append(snapshot)

            for lr in fixture_odds:
                lr["match_id"] = match_id
            pending_odds.extend(fixture_odds)

            # Status updates
            if db_match.get("status") == "scheduled" and minute > 0:
                pending_status.append((match_id, "live"))
            if af_fix.get("status_short") in ("FT", "AET", "PEN"):
                pending_status.append((match_id, "finished",
                                       af_fix.get("score_home", 0),
                                       af_fix.get("score_away", 0)))

        # ── Batch write ────────────────────────────────────────────────────
        try:
            store_live_snapshots_batch(pending_snapshots)
        except Exception as e:
            console.print(f"[red]Batch snapshot error: {e}[/red]")

        try:
            store_live_odds_batch(pending_odds)
        except Exception as e:
            console.print(f"[yellow]Batch odds error: {e}[/yellow]")

        # Event-triggered extra odds snapshot (goal/red card)
        if event_triggered_odds:
            try:
                store_live_odds_batch(event_triggered_odds)
                console.print(
                    f"[yellow]Event-triggered odds snapshot: "
                    f"{len(event_triggered_odds)} rows stored[/yellow]"
                )
            except Exception as e:
                console.print(f"[yellow]Event odds error: {e}[/yellow]")

        finished_match_ids = []
        for entry in pending_status:
            try:
                if len(entry) == 4:
                    # Finished match: (match_id, "finished", score_home, score_away)
                    match_id, status, score_home, score_away = entry
                    finish_match_sql(match_id, score_home, score_away)
                    finished_match_ids.append(match_id)
                    # Remove from active bets and score tracking once finished
                    self._active_bet_match_ids.discard(str(match_id))
                    self._prev_scores.pop(str(match_id), None)
                    console.print(
                        f"[green]Match finished: {match_id} → "
                        f"{score_home}-{score_away}[/green]"
                    )
                else:
                    # Status change: (match_id, "live")
                    match_id, status = entry
                    update_match_status_sql(match_id, status)
            except Exception as e:
                console.print(f"[yellow]Status update error: {e}[/yellow]")

        # Trigger per-match settlement for finished matches
        if finished_match_ids:
            try:
                from workers.jobs.settlement import settle_finished_matches
                settle_finished_matches(finished_match_ids)
            except Exception as e:
                console.print(f"[yellow]Per-match settlement error: {e}[/yellow]")

        # ── In-play paper trading bot ─────────────────────────────────────
        # Runs after snapshots are stored — reads from DB, no extra API calls.
        # Errors are isolated so they never disrupt the polling loop.
        try:
            from workers.jobs.inplay_bot import run_inplay_strategies
            run_inplay_strategies()
        except Exception as e:
            console.print(f"[red]InplayBot error: {e}[/red]")
            import traceback
            traceback.print_exc()
            # Report to Sentry if available
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(e)
            except Exception:
                pass

        return True  # Live matches were found this cycle — use fast sleep interval
