"""Inplay snapshot capture — measures slippage between an inplay bot's
decision point and what Coolbet's live markets show at the moment.

Triggered by Postgres LISTEN on `inplay_bet_fired`. One Coolbet GET per
signal (markets+odds), zero polling, fully reactive.

Three modes (see COOLBET-INPLAY-SNAPSHOTS migration 115):
  • capture (A) — write snapshot only. No real_bets row, no POST. Default.
  • paper   (B) — A + write real_bets row with notes='inplay paper'.
                  No POST to Coolbet. For full-flow simulation.
  • execute (C) — A + POST /s/bets/bets + write real_bets row with the
                  Coolbet ticket_id. REAL MONEY.

The capture function returns a dict that the listener thread inserts into
coolbet_inplay_snapshots. Modes B/C may additionally create a real_bets row
and update real_bet_id in the same insert.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_explorer import (
    fetch_match_markets, fetch_odds_for_markets, resolve_placement_target,
)
from workers.automation.coolbet_placer import (
    search_coolbet_event, _place_bet_api,
)
from workers.api_clients.supabase_client import (
    execute_query, store_real_bet,
)

log = logging.getLogger(__name__)

# Same tolerance as prematch — but inplay typically swings harder
_INPLAY_ODDS_DROP_TOL = 0.20  # 20% drop allowed before we tag odds_drop_too_large


def _resolve_match_teams(match_id: str) -> tuple[str, str] | None:
    """Look up our internal matches row → team names. Needed because the
    NOTIFY payload only has match_id (UUID), not team names."""
    rows = execute_query(
        """SELECT ht.name AS home, at2.name AS away
             FROM matches m
             JOIN teams ht  ON ht.id  = m.home_team_id
             JOIN teams at2 ON at2.id = m.away_team_id
            WHERE m.id = %s""",
        (match_id,),
    )
    if not rows:
        return None
    return rows[0]["home"], rows[0]["away"]


def _decision_payload_to_pick_time_iso(payload: dict[str, Any]) -> str | None:
    """pick_time arrives as a Postgres timestamptz JSON-serialized — typically
    a string like '2026-05-20T19:42:33.456+00:00'. Return as-is for re-insert."""
    return payload.get("pick_time")


def capture_inplay_snapshot(
    session: CoolbetSession,
    payload: dict[str, Any],
    mode: str = "capture",
) -> dict[str, Any]:
    """Process one inplay_bet_fired NOTIFY payload. Returns a snapshot dict
    ready to insert into coolbet_inplay_snapshots.

    payload shape (from migration 115's NOTIFY trigger):
      {bet_id, match_id, market, selection, odds_at_pick, pick_time}

    Returns dict with keys:
      simulated_bet_id, decision_pick_time, latency_ms,
      model_odds, coolbet_odds, coolbet_match_id, coolbet_market_id,
      coolbet_outcome_id, capture_outcome, error, inplay_mode, real_bet_id

    `mode` must be one of 'capture', 'paper', 'execute' (matches the
    coolbet_inplay_snapshots.inplay_mode CHECK constraint)."""
    if mode not in ("capture", "paper", "execute"):
        raise ValueError(f"unknown inplay mode: {mode}")

    t0 = time.time()
    bet_id        = str(payload["bet_id"])
    match_id      = str(payload["match_id"])
    market        = payload["market"]
    selection     = payload["selection"]
    model_odds    = float(payload.get("odds_at_pick") or 0) or None

    snap: dict[str, Any] = {
        "simulated_bet_id":    bet_id,
        "decision_pick_time":  _decision_payload_to_pick_time_iso(payload),
        "model_odds":          model_odds,
        "coolbet_odds":        None,
        "coolbet_match_id":    None,
        "coolbet_market_id":   None,
        "coolbet_outcome_id":  None,
        "capture_outcome":     "api_error",   # safe default; overwritten on success
        "error":               None,
        "inplay_mode":         mode,
        "real_bet_id":         None,
        # latency_ms set just before return
    }

    # Resolve team names
    teams = _resolve_match_teams(match_id)
    if not teams:
        snap["capture_outcome"] = "no_match"
        snap["error"] = f"matches row not found for {match_id}"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap
    home, away = teams

    # Find Coolbet live match (search/v2 returns matches in any status)
    try:
        ev = search_coolbet_event(session, home, away)
    except Exception as e:
        snap["error"] = f"search_coolbet_event raised: {e}"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap
    if not ev:
        snap["capture_outcome"] = "no_match"
        snap["error"] = f"no Coolbet event for '{home} vs {away}'"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap
    cb_match_id = int(ev["id"])
    snap["coolbet_match_id"] = cb_match_id

    # Fetch LIVE markets (matchStatus=LIVE in sidebets endpoint)
    try:
        markets = fetch_match_markets(session, cb_match_id, live=True)
    except Exception as e:
        snap["error"] = f"fetch_match_markets raised: {e}"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap
    if not markets:
        snap["capture_outcome"] = "no_market"
        snap["error"] = f"no live markets for cb_match_id={cb_match_id}"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap

    # Fetch odds for all markets in one shot, then resolve our bet's
    # (market, selection) to Coolbet's (outcome_id, odds_id, current_odds)
    try:
        odds_map = fetch_odds_for_markets(session, markets)
    except Exception as e:
        snap["error"] = f"fetch_odds_for_markets raised: {e}"
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap

    target = resolve_placement_target(markets, odds_map, market, selection)
    if not target:
        snap["capture_outcome"] = "no_market"
        snap["error"] = (
            f"market/selection {market!r}/{selection!r} not exposed in "
            f"live markets for cb_match_id={cb_match_id}"
        )
        snap["latency_ms"] = int((time.time() - t0) * 1000)
        return snap
    cb_market_id, cb_outcome_id, odds_uuid, coolbet_odds = target
    snap["coolbet_market_id"]  = cb_market_id
    snap["coolbet_outcome_id"] = cb_outcome_id
    snap["coolbet_odds"]       = coolbet_odds

    # Slippage sanity guard: very large drops mean we either hit a stale
    # cache or Coolbet has moved hard against us. Tag the row but don't
    # bail — the data is still useful for the slippage histogram.
    if model_odds and coolbet_odds and coolbet_odds < model_odds * (1 - _INPLAY_ODDS_DROP_TOL):
        snap["capture_outcome"] = "odds_drop_too_large"
        snap["error"] = (
            f"odds dropped from {model_odds:.3f} to {coolbet_odds:.3f} "
            f"(> {_INPLAY_ODDS_DROP_TOL*100:.0f}% tolerance)"
        )
    else:
        snap["capture_outcome"] = "captured"

    # ── Modes B (paper) and C (execute) — additional side effects ──────────
    # Only fire these on a clean capture; abort on odds_drop_too_large for
    # safety (don't place real money on collapsed odds).
    if snap["capture_outcome"] == "captured" and mode in ("paper", "execute"):
        try:
            # Pull bot_id from the simulated_bets row so the real_bets row
            # attributes correctly.
            rows = execute_query(
                "SELECT bot_id::text AS bot_id, stake FROM simulated_bets WHERE id = %s",
                (bet_id,),
            )
            bot_id = rows[0]["bot_id"] if rows else None
            stake = float(rows[0]["stake"]) if rows and rows[0].get("stake") else 5.0

            ticket_id = None
            notes = "inplay paper"
            if mode == "execute":
                # REAL MONEY — POST to /s/bets/bets
                cb_market_name = ""
                for _m in markets:
                    if int(_m.get("id") or 0) == cb_market_id:
                        cb_market_name = _m.get("name") or ""
                        break
                match_name = f"{ev['home']} - {ev['away']}"
                ticket_id = _place_bet_api(
                    session, cb_outcome_id, odds_uuid, stake, match_name,
                    cb_market_name, "",
                )
                notes = f"inplay execute ticket={ticket_id}"
                log.info("✓ Coolbet inplay ticket placed: %s", ticket_id)

            real_bet_id = store_real_bet(
                match_id=match_id,
                market=market,
                selection=selection,
                bookmaker="Coolbet",
                captured_odds=coolbet_odds,
                actual_odds=coolbet_odds,
                stake=stake,
                bot_id=bot_id,
                simulated_bet_id=bet_id,
                notes=notes,
            )
            snap["real_bet_id"] = real_bet_id
        except Exception as e:
            # Don't fail the snapshot — capture still landed. Just log + record
            # the error in the snapshot's error field.
            log.warning("inplay mode=%s side-effect failed for bet %s: %s",
                        mode, bet_id, e)
            snap["error"] = (snap.get("error") or "") + f" | mode={mode} failed: {e}"

    snap["latency_ms"] = int((time.time() - t0) * 1000)
    return snap


def insert_snapshot(snap: dict[str, Any]) -> str | None:
    """Insert a snapshot dict into coolbet_inplay_snapshots. Returns the
    new row's id, or None on conflict (already snapshotted)."""
    from workers.api_clients.supabase_client import execute_write_returning
    rows = execute_write_returning(
        """INSERT INTO coolbet_inplay_snapshots
              (simulated_bet_id, decision_pick_time, latency_ms,
               model_odds, coolbet_odds, coolbet_match_id, coolbet_market_id,
               coolbet_outcome_id, capture_outcome, error, inplay_mode, real_bet_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (simulated_bet_id) DO NOTHING
           RETURNING id""",
        [
            snap["simulated_bet_id"], snap["decision_pick_time"], snap["latency_ms"],
            snap["model_odds"], snap["coolbet_odds"], snap["coolbet_match_id"],
            snap["coolbet_market_id"], snap["coolbet_outcome_id"],
            snap["capture_outcome"], snap["error"], snap["inplay_mode"],
            snap["real_bet_id"],
        ],
    )
    return str(rows[0]["id"]) if rows else None
