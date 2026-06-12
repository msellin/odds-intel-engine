"""
Coolbet bet-signaler — Telegram safety-net for value picks.

WHY THIS EXISTS (COOLBET-SIGNALER-A, 2026-06-12): the auto-placer chain
(Imperva 403 from Railway IPs → FlareSolverr Chrome tab → 30-min JWT TTL
→ SMS-2FA on re-login) is structurally fragile. Each link in that chain
broke at least once this week. The signaler bypasses the entire chain:
it reads qualified picks from `simulated_bets` and sends a Telegram
message with everything the operator needs to place the bet manually
from their phone. Zero Coolbet API calls. Zero auth. Cannot break from
upstream Coolbet changes.

WHERE IT FITS:
- Stage 1 (now): primary path is signal-only. Pipeline detects edge →
  signaler fires → operator places manually. Auto-placement disabled.
- Stage 2 (next): a Mac-at-home daemon (option B) will consume the same
  qualified-bets queue and auto-place from a residential IP. The signal
  stays on as a safety net — even if the daemon misses, you still see
  the bet on your phone.

DEDUP: `simulated_bets.signaled_at` (mig 246) is the single source of
truth. Set on successful send. Never resignal. If the operator restarts
the pipeline, already-signaled rows are skipped.

EDGE GATES: same per-market floors as the placer (`_MIN_EDGE_BY_MARKET`)
so we don't spam picks the auto-placer would have rejected anyway.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, execute_write
from workers.automation.coolbet_placer import _min_edge_for, _MIN_EDGE
from workers.notify.telegram import send_telegram

log = logging.getLogger(__name__)


def load_signal_candidates(*, lookahead_hours: int = 36) -> list[dict]:
    """Return simulated_bets that should trigger a signal:
      - match hasn't kicked off (+ within next `lookahead_hours`)
      - edge_percent passes the global floor (per-market floors checked in Python)
      - signaled_at IS NULL (never signaled)
      - NOT EXISTS in real_bets (operator may have already placed it manually
        and clicked /confirm — that path inserts a real_bets row)
      - combo singles only (combos handled separately for now)

    Returns dicts with the fields the Telegram message renderer expects:
    home_team, away_team, market, selection, odds_at_pick, edge_percent,
    stake, model_probability, kelly_fraction, match_date, bot_name, league.
    Sorted by edge descending so the most valuable picks render first
    when several are sent in the same run."""
    # DISTINCT ON (match_id, market, selection) collapses multi-bot
    # picks of the same bet into ONE signal — same pattern the placer
    # uses (workers/automation/coolbet_placer.py load_qualified_bets).
    # We pick the row with highest edge_percent as the canonical entry;
    # _mark_signaled() will then mark ALL rows for that combo so the
    # next pipeline run doesn't re-fire from sibling bot picks whose
    # signaled_at is still NULL.
    rows = execute_query(
        """
        SELECT * FROM (
          SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                 sb.id            AS simulated_bet_id,
                 sb.match_id,
                 sb.market,
                 sb.selection,
                 sb.odds_at_pick,
                 sb.edge_percent,
                 sb.stake,
                 sb.model_probability,
                 sb.calibrated_prob,
                 sb.kelly_fraction,
                 sb.bot_id,
                 b.name            AS bot_name,
                 m.date            AS match_date,
                 m.coolbet_match_id AS coolbet_match_id,
                 ht.name           AS home_team,
                 at2.name          AS away_team,
                 l.name            AS league,
                 COUNT(*) OVER (PARTITION BY sb.match_id, sb.market, sb.selection) AS bot_count
          FROM simulated_bets sb
          JOIN bots          b   ON b.id   = sb.bot_id
          JOIN matches       m   ON m.id   = sb.match_id
          JOIN teams         ht  ON ht.id  = m.home_team_id
          JOIN teams         at2 ON at2.id = m.away_team_id
          LEFT JOIN leagues  l   ON l.id   = m.league_id
          WHERE sb.combo_legs IS NULL
            AND sb.signaled_at IS NULL
            AND sb.edge_percent >= %s
            AND m.date > NOW()
            AND m.date < NOW() + (%s * INTERVAL '1 hour')
            AND NOT EXISTS (
                SELECT 1 FROM real_bets rb
                WHERE rb.match_id  = sb.match_id
                  AND rb.market    = sb.market
                  AND rb.selection = sb.selection
            )
          ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
        ) q
        ORDER BY q.match_date ASC, q.edge_percent DESC
        """,
        (_MIN_EDGE, lookahead_hours),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # Per-market floor in Python — mirrors the placer's gating exactly
        # so the signal set is a superset-then-filter of the placement set.
        floor = _min_edge_for(d.get("market"))
        if (d.get("edge_percent") or 0) < floor:
            continue
        out.append(d)
    return out


def _format_signal(b: dict) -> str:
    """Render the Telegram message for one qualified bet. Designed to be
    readable on a phone screen in 2 seconds — operator sees the bet, taps
    the link, places it manually. Includes the simulated_bet_id so the
    operator can correlate against the admin tools later, but doesn't
    expose anything PII / account-specific.

    Format kept terse on purpose — long messages get truncated in mobile
    push notifications. The Telegram preview will show the first 2 lines."""
    edge_pct = float(b["edge_percent"] or 0) * 100
    odds = float(b["odds_at_pick"] or 0)
    stake = float(b["stake"] or 0)
    kelly = float(b["kelly_fraction"] or 0) * 100
    ko = b["match_date"]
    if isinstance(ko, datetime):
        ko_str = ko.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        ko_str = str(ko)

    market = (b.get("market") or "").upper()
    selection = (b.get("selection") or "").upper()
    league = b.get("league") or ""

    # Deep link: if we know Coolbet's per-match event id (cached in
    # matches.coolbet_match_id), build the direct match-page URL. Falls
    # back to the sports landing page when the id hasn't been resolved
    # yet (first signal for the match; next pipeline run will succeed
    # once the lazy resolver below populates the column).
    cb_id = b.get("coolbet_match_id")
    if cb_id:
        coolbet_link = f"https://www.coolbet.com/et/sport/match/{cb_id}"
    else:
        coolbet_link = "https://www.coolbet.com/et/sport/jalgpall"

    lines = [
        f"🎯 BET SIGNAL — {b['home_team']} vs {b['away_team']}",
        f"🏆 {market} → {selection}  @ {odds:.2f}",
        f"💰 Stake €{stake:.2f}  (Kelly {kelly:.0f}%, edge +{edge_pct:.1f}%)",
        f"⏰ {ko_str}  ·  {league}",
        f"🤖 {b.get('bot_name') or '?'}",
        f"🔗 {coolbet_link}",
        f"id: {b['simulated_bet_id']}",
    ]
    return "\n".join(lines)


def _resolve_coolbet_match_id(match_id, home: str, away: str) -> int | None:
    """Look up Coolbet's per-match event id and cache it. Returns the id
    (int) if found, None otherwise.

    Lazy strategy: only call the Coolbet search API when matches.coolbet_match_id
    is NULL. Once resolved, every subsequent signal reads from DB without
    hitting Coolbet again. The search itself is anon (uses Imperva cookies
    from env, no JWT) so it doesn't depend on the JWT-DB chain.

    Failure is silent — None means "no deep link, fall back to landing page".
    We never block a signal on a missing match id."""
    try:
        from workers.automation.coolbet_session import coolbet_match_url
        url = coolbet_match_url(home, away)
        if not url:
            return None
        # URL shape: https://www.coolbet.com/et/sport/match/{id}
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        cb_id = int(tail) if tail.isdigit() else None
        if cb_id is not None:
            try:
                execute_write(
                    "UPDATE matches SET coolbet_match_id = %s WHERE id = %s AND coolbet_match_id IS NULL",
                    (cb_id, match_id),
                )
            except Exception as e:
                log.debug("cache coolbet_match_id failed (non-fatal): %s", e)
        return cb_id
    except Exception as e:
        log.debug("coolbet match id lookup failed for %s vs %s: %s", home, away, e)
        return None


def _mark_signaled(match_id, market: str, selection: str) -> None:
    """Mark ALL simulated_bets rows for this (match, market, selection) as
    signaled — not just the canonical row from DISTINCT ON. Without this,
    sibling bot picks of the same bet would re-qualify next pipeline run
    because their signaled_at is still NULL.

    Best-effort: a transient DB error shouldn't crash the loop. Worst
    case: the bet resignals on the next pipeline run (~15 min later).
    Logged at WARNING so a recurring failure surfaces."""
    try:
        execute_write(
            """UPDATE simulated_bets
               SET signaled_at = NOW()
               WHERE match_id = %s
                 AND market   = %s
                 AND selection = %s
                 AND signaled_at IS NULL""",
            (match_id, market, selection),
        )
    except Exception as e:
        log.warning("failed to mark signaled_at for %s/%s/%s: %s",
                    match_id, market, selection, e)


def signal_all_bets(*, lookahead_hours: int = 36,
                     dry_run: bool = False) -> list[dict]:
    """Main entry point. Loads candidates, sends a Telegram for each,
    marks signaled_at. Returns a list of {simulated_bet_id, outcome,
    telegram_message_id} dicts so callers (pipeline + smoke tests) can
    introspect what happened.

    dry_run=True: load + format candidates, but skip the Telegram send
    AND skip the signaled_at mark. Used by smoke + manual probes."""
    candidates = load_signal_candidates(lookahead_hours=lookahead_hours)
    results: list[dict] = []
    if not candidates:
        return results

    log.info("Coolbet signaler — %d candidate%s",
             len(candidates), "" if len(candidates) == 1 else "s")

    for b in candidates:
        # Lazy resolve Coolbet match id so the signal carries a direct
        # match-page deep link. Best-effort — None just means we fall
        # back to the sports landing page; signal still fires.
        if not b.get("coolbet_match_id"):
            cb_id = _resolve_coolbet_match_id(b["match_id"], b["home_team"], b["away_team"])
            if cb_id is not None:
                b["coolbet_match_id"] = cb_id

        msg = _format_signal(b)
        if dry_run:
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "dry_run",
                "telegram_message_id": None,
                "preview": msg,
            })
            continue
        # Inline buttons so the operator can mark placed / skipped with one
        # tap from the chat. Callback handler in
        # odds-intel-web/src/app/api/telegram/webhook/route.ts updates the
        # corresponding column on simulated_bets and edits the message to
        # append a status footer.
        sim_id = str(b["simulated_bet_id"])
        reply_markup = {
            "inline_keyboard": [[
                {"text": "✅ Placed", "callback_data": f"sigplaced:{sim_id}"},
                {"text": "⏭ Skip",    "callback_data": f"sigskip:{sim_id}"},
            ]],
        }
        tg_id = send_telegram(
            msg,
            dedup_key=f"signal-{sim_id}",
            dedup_window_s=900,
            reply_markup=reply_markup,
        )
        if tg_id is not None:
            _mark_signaled(b["match_id"], b["market"], b["selection"])
            # Cache the message_id so the callback handler can edit the
            # original message to add a placement-status footer.
            try:
                execute_write(
                    """UPDATE simulated_bets SET signal_message_id = %s
                       WHERE match_id = %s AND market = %s AND selection = %s""",
                    (tg_id, b["match_id"], b["market"], b["selection"]),
                )
            except Exception as e:
                log.debug("cache signal_message_id failed (non-fatal): %s", e)
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "signaled",
                "telegram_message_id": tg_id,
            })
        else:
            # send_telegram returns None on dedup-skip OR on missing creds.
            # In both cases we DON'T mark signaled_at — caller can retry.
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "skipped",
                "telegram_message_id": None,
            })
    return results
