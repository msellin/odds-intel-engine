"""
Coolbet bet-signaler — Telegram safety-net for value picks.

WHY THIS EXISTS (COOLBET-SIGNALER-A, 2026-06-12): the auto-placer chain
(Imperva 403 from the VPS IPs → FlareSolverr Chrome tab → 30-min JWT TTL
→ SMS-2FA on re-login) is structurally fragile. Each link in that chain
broke at least once this week. The signaler bypasses the entire chain:
it reads qualified picks from `simulated_bets` and sends a Telegram
message with everything the operator needs to place the bet manually
from their phone. Zero Coolbet API calls. Zero auth. Cannot break from
upstream Coolbet changes.

WHERE IT FITS:
- Stage 1 (2026-06, HISTORICAL): primary path was signal-only. Pipeline
  detects edge → signaler fires → operator places manually.
- Stage 2 (CURRENT): the UI placer places real money unattended from the
  operator's Mac. The manual prompt is therefore no longer the primary path,
  and as of 2026-09-11 it is off by default — see SIGNALER-PUBLIC-ONLY below.
  ⚠️ Consequence the owner accepted: there is no manual fallback PROMPT if the
  placer stops. Placement readiness is monitored separately
  (`coolbet_control --status`, the feed watchdog).

DEDUP: `simulated_bets.signaled_at` (mig 246) is the single source of
truth. Set on successful send. Never resignal. If the operator restarts
the pipeline, already-signaled rows are skipped.

WHAT IT SENDS NOW (SIGNALER-PUBLIC-ONLY + PUBLIC-CHANNEL-DECOUPLED, 2026-09-11):
the public @oddsintelpicks channel is the ONLY sink. The operator's private
per-pick prompt is off by default (`OPERATOR_PICK_ALERTS`, the ONE flag that
also governs daily_pipeline_v2's `[OI] 🎯 PRE-MATCH` alert) — auto-placement
works now, so it had become a duplicate of every public pick in the owner's own
chat. It is kept behind the flag for a possible paid invite-only channel later.

Two bugs fixed at the same time, both of which made the CUSTOMER feed a function
of OUR OWN staking — the exact opposite of what it should be:
  * the candidate query excluded anything already in `real_bets`, so a pick we
    had backed with real money never reached customers. Those are our
    highest-conviction picks (8 lost in 30d, edges 0.08-0.15), and placing every
    pick on a given day would have left the channel EMPTY.
  * the public post was nested inside the operator send's success branch, so an
    operator-side dedup-skip or missing operator creds silently dropped a
    customer pick with nothing logged as a failure.
Publishing a pick we staked is if anything MORE warranted, not less.

EDGE GATES: the ONE shared selection-aware floor `min_edge_for_pick`
(coolbet_placer) — the same utility the placer/daemon use, so the signal set
and the placement set apply an identical rule and cannot drift. In particular
1x2 home-underdogs (home, odds>=2.80) gate at 10%, matching the real-money bot,
so a bet we place also signals (EDGE-FLOOR-ONE-UTILITY-2026-09-10).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, execute_write
from workers.automation.coolbet_placer import (
    clears_edge_floor, min_edge_for_pick, _MIN_EDGE,
)
from workers.notify.telegram import (
    send_telegram, send_telegram_public, operator_pick_alerts_enabled,
)

log = logging.getLogger(__name__)

# SIGNALER-PUBLIC-ONLY (2026-09-11). The operator's private per-pick prompt is
# OFF by default: the UI placer now places unattended, so the prompt had become
# a duplicate of every public pick in the owner's own chat.
#
# Governed by the SHARED `operator_pick_alerts_enabled()` rather than a flag of
# its own. Two independent paths were sending the owner per-pick private
# messages (this prompt and daily_pipeline_v2's `[OI] 🎯 PRE-MATCH` alert) — 16
# messages for 10 picks on the day this was switched off. Giving each its own
# env var is how one intent becomes two settings that drift apart, which is the
# exact failure this whole day's work was about. One flag, one place.


def load_signal_candidates(*, lookahead_hours: int = 36) -> list[dict]:
    """Return simulated_bets that should trigger a signal:
      - match hasn't kicked off (+ within next `lookahead_hours`)
      - edge_percent passes the global floor (per-market floors checked in Python)
      - signaled_at IS NULL (never signaled)
      - NOT filtered on real_bets. Already-placed picks ARE returned, carrying
        `already_placed=True`, because that fact must only suppress the
        OPERATOR prompt — never the customer post (PUBLIC-CHANNEL-DECOUPLED
        2026-09-11; see the note in the WHERE clause and the send loop).
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
                 sb.recommended_bookmaker,
                 b.name            AS bot_name,
                 b.maturity_label  AS maturity,
                 m.date            AS match_date,
                 m.coolbet_match_id AS coolbet_match_id,
                 ht.name           AS home_team,
                 at2.name          AS away_team,
                 l.name            AS league,
                 l.country         AS country,
                 COUNT(*) OVER (PARTITION BY sb.match_id, sb.market, sb.selection) AS bot_count,
                 -- Per-row, NOT a filter (see PUBLIC-CHANNEL-DECOUPLED below):
                 -- the operator does not need a manual-placement prompt for a
                 -- bet already placed, but the audience still gets the pick.
                 EXISTS (
                   SELECT 1 FROM real_bets rb
                    WHERE rb.match_id  = sb.match_id
                      AND rb.market    = sb.market
                      AND rb.selection = sb.selection
                 ) AS already_placed,
                 -- SIGNALER-MATURITY-SHADOWING (2026-08-28): whether ANY bot in
                 -- this (match, market, selection) group is calibrated — not
                 -- just the canonical highest-edge row DISTINCT ON happens to
                 -- keep. The public-channel gate reads this instead of the
                 -- canonical row's maturity; see the note above the gate.
                 bool_or(b.maturity_label = 'calibrated')
                   OVER (PARTITION BY sb.match_id, sb.market, sb.selection)
                   AS group_has_calibrated
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
            -- PUBLIC-CHANNEL-DECOUPLED (2026-09-11): this used to be
            -- `AND NOT EXISTS (... real_bets ...)`, which silently made the
            -- CUSTOMER channel a function of OUR OWN staking. Suppressing an
            -- already-placed pick is right for the operator's manual-placement
            -- prompt and WRONG for the audience feed: a pick we backed with real
            -- money is our highest-conviction pick, so customers were denied
            -- exactly the best ones (measured: 8 publishable picks in 30d, edges
            -- 0.08-0.15). In the limit, placing all of today's picks would have
            -- left the public channel EMPTY. So it is no longer a filter on the
            -- candidate set — it is surfaced per row and applied ONLY to the
            -- operator send below.
            AND TRUE
          ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
        ) q
        ORDER BY q.match_date ASC, q.edge_percent DESC
        """,
        (_MIN_EDGE, lookahead_hours),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # EDGE-FLOOR-ONE-UTILITY-2026-09-10: the SINGLE selection-aware floor
        # shared with the placer — 1x2 home-underdogs (home, odds>=2.80) at 10%,
        # everything else at the pooled per-market floor. This is the LIVE
        # Telegram path; it previously called the blind market-only _min_edge_for
        # and so never signaled home-underdogs in the 10-13% band that the placer
        # would place (Stevenage v Luton). One utility now, so the two can't drift.
        # EDGE-FLOOR-ONE-PREDICATE (2026-09-11): one shared predicate, not a
        # shared floor plus a hand-rolled comparison. Sharing only the floor is
        # what let the signaler and placer disagree AGAIN — see
        # clears_edge_floor() for the Decimal-vs-float trap that silently
        # dropped every pick sitting exactly ON its floor.
        if not clears_edge_floor(d.get("market"), d.get("selection"),
                                 d.get("odds_at_pick"), d.get("edge_percent")):
            continue
        out.append(d)
    return out


def is_public_eligible(b: dict) -> bool:
    """Would this candidate actually be POSTED to the public channel?

    Extracted 2026-09-11. The public channel is the only sink now, so "is this
    pick going anywhere?" is a question two callers need — the send loop and
    `health_alerts.check_signal_silence`, which measures publishable picks that
    are stuck. Both must ask it the same way: a second hand-rolled copy of this
    rule is precisely how the floors and the gates drifted everywhere else.

    Gates on whether ANY bot in the group is calibrated (SIGNALER-MATURITY-
    SHADOWING 2026-08-28), not the canonical row's own maturity.
    """
    return (bool(b.get("group_has_calibrated"))
            and b.get("market") in _PUBLIC_MARKETS)


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


# Markets eligible for the public Telegram channel. Asian Handicap is
# excluded because calibrated AH was a known money loser (bot_ah_home_fav
# retired 2026-06-24). We can re-add AH once a calibrated AH bot returns.
_PUBLIC_MARKETS = {"1x2", "o/u", "over_under_25", "btts"}


def _format_market_public(market: str, selection: str) -> str:
    """Human-readable pick label for the public channel. The operator
    channel uses raw MARKET → SELECTION (e.g. '1X2 → HOME'); public
    readers don't want that."""
    m = (market or "").lower()
    s = (selection or "").lower()
    if m == "1x2":
        if s == "home": return "Home win"
        if s == "away": return "Away win"
        if s == "draw": return "Draw"
    if m in ("o/u", "over_under_25"):
        if "over" in s: return "Over 2.5 goals"
        if "under" in s: return "Under 2.5 goals"
    if m == "btts":
        return "Both teams to score: Yes" if "yes" in s else "Both teams to score: No"
    return f"{market} · {selection}"


def _format_public_signal(b: dict) -> str:
    """Render the Telegram message for the PUBLIC @oddsintelpicks channel.
    Differs from _format_signal:
      - No operator-internal data (no Kelly %, no stake, no bot_name, no
        simulated_bet_id, no Coolbet deep link)
      - Clean human-readable market labels via _format_market_public
      - Always links to /picks (live feed) + /performance (track record)
      - No '[OI]' prefix (send_telegram_public skips the prefix)
    """
    edge_pct = float(b["edge_percent"] or 0) * 100
    odds = float(b["odds_at_pick"] or 0)
    ko = b["match_date"]
    if isinstance(ko, datetime):
        ko_str = ko.astimezone(timezone.utc).strftime("%a %d %b · %H:%M UTC")
    else:
        ko_str = str(ko)

    pick = _format_market_public(b.get("market", ""), b.get("selection", ""))
    league = b.get("league") or ""
    country = b.get("country") or ""
    league_str = f"{country} {league}".strip() if country else league
    bookmaker = b.get("recommended_bookmaker") or ""
    bk_str = f" at <b>{bookmaker}</b>" if bookmaker else ""

    return (
        f"⚽ <b>{b['home_team']} vs {b['away_team']}</b>\n"
        f"{league_str} · {ko_str}\n\n"
        f"✅ Pick: <b>{pick}</b> @ <b>{odds:.2f}</b>{bk_str}\n"
        f"📈 Edge: <b>+{edge_pct:.1f}%</b>\n\n"
        f"<a href='https://oddsintel.app/picks'>Live picks</a> · "
        f"<a href='https://oddsintel.app/performance'>Track record</a>"
    )


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
            # Preview what would ACTUALLY be sent. Until 2026-09-11 this always
            # previewed the OPERATOR message even though the public channel is
            # the only sink by default — a dry run that shows a message the real
            # run would not send is worse than no preview.
            _pub_ok = is_public_eligible(b)
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "dry_run",
                "telegram_message_id": None,
                "would_post_public": _pub_ok,
                "would_prompt_operator": bool(
                    operator_pick_alerts_enabled() and not b.get("already_placed")),
                "preview": (_format_public_signal(b) if _pub_ok
                            else "(not public-eligible — nothing would be sent)"),
                "preview_operator": msg if operator_pick_alerts_enabled() else None,
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
        # ── OPERATOR PROMPT — OFF BY DEFAULT since 2026-09-11 ────────────
        # SIGNALER-PUBLIC-ONLY (owner decision 2026-09-11): "not sure we need
        # that at all, its legacy... we wanna send picks to our public channel,
        # no need to duplicate this to my own private channel."
        #
        # It IS legacy: this module was built in 2026-06 when auto-placement was
        # disabled and a manual prompt on the operator's phone was the PRIMARY
        # path. The UI placer now places real money unattended (7 bets the day
        # this was switched off), so the prompt had become a duplicate of every
        # public pick landing in the owner's private chat.
        #
        # KEPT, not deleted, and deliberately: the owner flagged a likely future
        # use — "maybe someday when we have private channel with invites (paid
        # tier?)". Deleting it would also orphan three things that still work:
        # `_format_signal`, the ✅ Placed / ⏭ Skip inline buttons handled by
        # odds-intel-web `/api/telegram/webhook` (sigplaced:/sigskip:), and the
        # `signal_message_id` the handler edits. So it is one env flag away.
        #
        # ⚠️ Trade-off the owner accepted: with this off there is no manual
        # fallback prompt if the auto-placer stops — that safety net was the
        # module's original reason for existing. Placement readiness is
        # monitored separately (`coolbet_control --status`, the feed watchdog).
        #
        # NOTE the owner's private chat ALSO receives a per-pick alert from
        # daily_pipeline_v2 (the `[OI] 🎯 PRE-MATCH` messages recorded in
        # bet_telegram_alerts — 10 of them the same day). That is a SEPARATE
        # path and is untouched here; silencing it is its own decision.
        tg_id = None
        if operator_pick_alerts_enabled() and not b.get("already_placed"):
            tg_id = send_telegram(
                msg,
                dedup_key=f"signal-{sim_id}",
                dedup_window_s=900,
                reply_markup=reply_markup,
            )
        if tg_id is not None:
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

        # ── PUBLIC CHANNEL — the audience surface, and now the ONLY sink ─────
        # PUBLIC-CHANNEL-DECOUPLED (2026-09-11). This used to be nested inside
        # `if tg_id is not None`, which made the customer feed a side-effect of
        # the operator message in two silent ways:
        #   * a pick we had already placed with real money never reached
        #     customers at all — and those are our HIGHEST-conviction picks, the
        #     ones we backed with our own money (measured: 8 publishable picks in
        #     30d, edges 0.08-0.15). Placing every pick on a given day would have
        #     left the public channel EMPTY.
        #   * an operator-side dedup-skip or missing operator creds
        #     (`send_telegram` returns None for both) dropped a customer pick
        #     with nothing logged as a failure.
        # Publishing a pick we staked is if anything MORE warranted, not less.
        #
        # SIGNALER-MATURITY-SHADOWING (2026-08-28) — gate on whether ANY bot in
        # the group is calibrated, NOT the canonical row's own maturity.
        # load_signal_candidates collapses multi-bot picks with DISTINCT ON ...
        # ORDER BY edge_percent DESC, so the canonical row is just the
        # highest-edge one. When a BETA bot quoted a higher edge than a
        # calibrated bot on the identical (match, market, selection),
        # `b["maturity"]` read 'beta' and the public post was silently skipped
        # even though a calibrated bot backed that exact pick — 7 of 109
        # calibrated picks (6.4%) over 60d. The canonical row still supplies the
        # message CONTENT; re-ordering to prefer calibrated rows would have let a
        # lower-edge calibrated row fall under the floor and drop the pick.
        public_eligible = is_public_eligible(b)
        public_msg_id = None
        if public_eligible:
            try:
                public_msg_id = send_telegram_public(_format_public_signal(b))
                if public_msg_id is None:
                    log.warning(
                        "PUBLIC-CHANNEL-POST: send_telegram_public returned "
                        "None for sim_id=%s — check that "
                        "TELEGRAM_PUBLIC_CHANNEL is set and the bot is an "
                        "admin of the channel.", sim_id,
                    )
            except Exception as e:
                log.warning("PUBLIC-CHANNEL-POST failed for sim_id=%s "
                            "(non-fatal): %s", sim_id, e)

        # ── DEDUP BOOKKEEPING ────────────────────────────────────────────────
        # `signaled_at` retires a pick from the candidate set. Mark it when a
        # send actually landed. A pick that is NOT public-eligible is left
        # unmarked on purpose: `group_has_calibrated` can flip to true before
        # kickoff (a calibrated bot joins the group), and marking it now would
        # permanently deny a pick that becomes publishable later.
        delivered = (tg_id is not None) or (public_msg_id is not None)
        if delivered:
            _mark_signaled(b["match_id"], b["market"], b["selection"])
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "signaled",
                "telegram_message_id": tg_id,
                "public_channel_message_id": public_msg_id,
            })
        else:
            results.append({
                "simulated_bet_id": b["simulated_bet_id"],
                "outcome": "not_public" if not public_eligible else "skipped",
                "telegram_message_id": None,
                "public_channel_message_id": None,
            })
    return results
