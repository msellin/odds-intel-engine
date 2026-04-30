"""
OddsIntel — Live Poller (Tiered Polling)

Multi-frequency polling loop for live match data.
Replaces the 5-minute APScheduler cron with:
  - Fast tier (15s): bulk fixtures + live odds (2 API calls)
  - Medium tier (60s): per-match statistics + events (2N API calls)
  - Slow tier (5min): lineups for upcoming + refresh match map

Runs in its own thread alongside the APScheduler.
Uses direct SQL (db.py) for all DB writes.

API budget (AF Ultra 75K/day):
  - Fast: ~5,280 calls/day (2 calls × 4/min × 60min × 11h)
  - Medium: ~8,640 calls/day (avg 12 matches × 2 calls × 60min × 6h)
  - Total: ~14,000-20,000 calls/day (was ~3,400 at 5min)
"""

import time
import threading
from datetime import datetime, timezone, date
from rich.console import Console

console = Console()


class LivePoller:
    """
    Tiered live match polling loop.

    Fast tier (every 15s): 2 bulk API calls for all live fixtures + odds
    Medium tier (every 60s): per-match stats + events
    Slow tier (every 5min): lineups + match map refresh
    """

    # Match window: UTC hours when live matches are expected
    MATCH_WINDOW_START = 10  # 10:00 UTC (early Asian/Australian matches)
    MATCH_WINDOW_END = 23    # 23:00 UTC (late European/South American)

    # Polling intervals (seconds) — configurable, start conservative
    FAST_INTERVAL = 30       # Bulk fixtures + odds (can go to 15s later)
    MEDIUM_MULTIPLIER = 2    # Stats every 2nd fast cycle (= 60s at 30s fast)
    SLOW_MULTIPLIER = 10     # Lineups every 10th fast cycle (= 5min at 30s fast)

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

    def run_forever(self):
        """Main polling loop. Call from a daemon thread."""
        console.print("[bold cyan]LivePoller started — 15s/60s/5min tiered polling[/bold cyan]")

        while not self._should_stop():
            cycle_start = time.time()

            if not self._in_match_window():
                # Outside match hours — sleep longer
                if self._cycle % self.SLOW_MULTIPLIER == 0:
                    console.print("[dim]LivePoller: outside match window, sleeping...[/dim]")
                self._cycle += 1
                time.sleep(self.FAST_INTERVAL)
                continue

            try:
                self._run_cycle()
            except Exception as e:
                console.print(f"[red]LivePoller cycle error: {e}[/red]")

            self._cycle += 1

            # Sleep to maintain fast interval
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.FAST_INTERVAL - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        console.print("[yellow]LivePoller stopped.[/yellow]")

    def _in_match_window(self) -> bool:
        """Check if current UTC hour is within the match window."""
        hour = datetime.now(timezone.utc).hour
        return self.MATCH_WINDOW_START <= hour <= self.MATCH_WINDOW_END

    def _run_cycle(self):
        """Execute one polling cycle."""
        from workers.jobs.live_tracker import (
            fetch_live_bulk, fetch_match_stats_for, fetch_match_events_for,
            build_snapshot, _fetch_lineups_for_upcoming, _lookup_db_match,
        )
        from workers.api_clients.db import (
            store_live_snapshots_batch, store_live_odds_batch,
            store_match_events_batch, update_match_status_sql,
            build_af_id_map,
        )
        from workers.api_clients.supabase_client import get_client

        # ── Slow tier: refresh match map every ~5 min ──────────────────────
        if self._cycle % self.SLOW_MULTIPLIER == 0:
            self._af_id_map = build_af_id_map()
            self._last_map_refresh = time.time()

            # Lineups for upcoming matches
            try:
                _fetch_lineups_for_upcoming(self._af_id_map)
            except Exception as e:
                console.print(f"[yellow]Lineup fetch error: {e}[/yellow]")

        # ── Fast tier: bulk fixtures + odds (every cycle = 15s) ────────────
        if not self.budget.can_call():
            if self._cycle % self.SLOW_MULTIPLIER == 0:
                console.print("[yellow]LivePoller: API budget low, skipping cycle[/yellow]")
            return

        fixtures, odds_by_fixture = fetch_live_bulk()

        if not fixtures:
            return  # No live matches

        # Log periodically (every ~2 min)
        if self._cycle % (self.SLOW_MULTIPLIER // 2 or 4) == 0:
            console.print(
                f"[dim]LivePoller cycle {self._cycle}: "
                f"{len(fixtures)} live, budget {self.budget.remaining()}/75K[/dim]"
            )

        # ── Build snapshots from fixtures + odds ───────────────────────────
        pending_snapshots = []
        pending_odds = []
        pending_status = []

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

            # ── Medium tier: stats + events (every Nth cycle = ~60s) ────
            if self._cycle % self.MEDIUM_MULTIPLIER == 0 and af_id:
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

                # Events
                events = fetch_match_events_for(af_id)
                if events:
                    home_api_id = af_fix.get("home_team_api_id")
                    try:
                        store_match_events_batch(match_id, events,
                                                 home_team_api_id=home_api_id)
                    except Exception:
                        pass

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
                pending_status.append((match_id, "finished"))

        # ── Batch write ────────────────────────────────────────────────────
        try:
            store_live_snapshots_batch(pending_snapshots)
        except Exception as e:
            console.print(f"[red]Batch snapshot error: {e}[/red]")

        try:
            store_live_odds_batch(pending_odds)
        except Exception as e:
            console.print(f"[yellow]Batch odds error: {e}[/yellow]")

        for match_id, status in pending_status:
            try:
                update_match_status_sql(match_id, status)
            except Exception:
                pass
