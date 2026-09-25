"""VIP FIRST — the ONE hold-back rule for free picks ([[#164]], owner decision 2026-09-25).

WHAT. The VIP bots (`bots.vip`: bot_combined_1x2_ev5_v1 "1x2 NEW+ EV5", bot_ou_sharp_early_v1
"O/U EARLY") and their `hide_pending` twins sell their live picks to Pro/Elite before kickoff.
They never give a pick up. A FREE pick (any other bot's, and the published forward-test arms'
`live` / `consensus_anchor`) is still RECORDED exactly as its own rule decided — the bot's
record and the pre-registered forward test are unchanged — but it is HELD BACK (not shown on
/picks, not sent to the public Telegram channel, not listed as pending anywhere public) until
kickoff when either

  (a) VIP-HELD: a VIP / hide_pending bot holds a PENDING pick on the same match + market +
      selection. Read from the ledger (simulated_bets), never re-derived; or
  (b) IN VIP'S RANGE at the free pick's decision time and price:
        1X2 — NEW+ (rating_1x2_predictions, r1x2_comb_v1, gated) EV = p x odds - 1 >= the EV5
              bot's own floor, at odds inside the EV5 bot's own odds range (both read from its
              BOTS_CONFIG entry, never copied);
        O/U — workers/jobs/ou_sharp_outlier.early_rule(): EV vs Pinnacle's power-de-vigged
              latest price in the 5-15% band, odds 1.30-6.00, >= 12 h to kickoff.
      (b) is what removes the "free first, VIP later" overlap: VIP would take this price now.

WHY ONE MODULE. The leak that opened [[#164]] was a second, re-derived copy of the VIP rule
(`vip_exclude` in daily_pipeline_v2) that used hard-coded constants and the public bot's CURRENT
price, so after a price move it passed a pick the VIP bot already held — and three other public
paths had no check at all. So the rule lives HERE, it is applied where picks are WRITTEN
(`store_bet` for simulated_bets, `claim` for picks_forward_test) and stored on the row as
`held_back_until` (= kickoff) + `held_back_reason`; every public surface filters on that column
(migration 438: picks_public_all, picks_board_public, the simulated_bets anon policy; the
signaler's candidate query; the web's pending views). When a VIP pick is written AFTER free
picks on the same selection, `hold_back_followers` stamps them too.

FAILS CLOSED. If the guard cannot evaluate a free pick it is held back ('guard_error'): a free
pick shown late costs nothing; a VIP pick leaked early is the defect this exists to stop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

VIP_1X2_BOT = "bot_combined_1x2_ev5_v1"
# The SQL predicate for "this bot's pending picks are private before kickoff". Same flags the
# simulated_bets anon policy and /performance's settled-only rule read (migrations 420/421).
PROTECTED_BOT_SQL = "(b.vip OR b.hide_pending)"
# Forward-test arms that reach a public surface (picks_public_all's allow-list).
PUBLISHED_FT_ARMS = ("live", "consensus_anchor")

REASON_HELD = "vip_held"
REASON_RANGE_1X2 = "vip_range_1x2"
REASON_RANGE_OU = "vip_range_ou"
REASON_ERROR = "guard_error"

_bot_flags: dict[str, bool] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def is_protected_bot(bot_id: str) -> bool:
    """True for a VIP / hide_pending bot (its own picks are never held back — they ARE the
    thing being protected). Cached per process: the flags change by migration only."""
    key = str(bot_id)
    if key not in _bot_flags:
        from workers.api_clients.db import execute_query
        rows = execute_query(f"SELECT {PROTECTED_BOT_SQL} AS p FROM bots b WHERE b.id = %s", (key,))
        _bot_flags[key] = bool(rows and rows[0]["p"])
    return _bot_flags[key]


# #162 W8.8: the VIP set lives twice — `bot_registry.VIP_BOTS` (the signaler exclusion, the VIP
# Telegram branch, ou_sharp_outlier) and `bots.vip` (the RLS policies and this module's SQL). If
# they drift, one path treats a bot as VIP and another publishes its live pick. Same predicate as
# PROTECTED_BOT_SQL's `b.vip` — no retired_at filter, because the DB flag is what the RLS reads.
VIP_DB_SQL = "SELECT name FROM bots WHERE vip"


def vip_registry_drift() -> tuple[set[str], set[str]]:
    """(in code only, in DB only) between VIP_BOTS and `bots.vip`. Both empty = in step.
    Raises on a DB error — callers decide (smoke fails; the scheduler only warns)."""
    from workers.api_clients.db import execute_query
    from workers.registry.bot_registry import VIP_BOTS
    db = {r["name"] for r in (execute_query(VIP_DB_SQL) or [])}
    return set(VIP_BOTS) - db, db - set(VIP_BOTS)


def vip_held(match_id: str, market: str, selection: str) -> bool:
    """(a) — a VIP / hide_pending bot holds a PENDING pick on this exact selection (the ledger)."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        f"""SELECT EXISTS (SELECT 1 FROM simulated_bets s JOIN bots b ON b.id = s.bot_id
                            WHERE {PROTECTED_BOT_SQL} AND s.result = 'pending' AND s.combo_legs IS NULL
                              AND s.match_id = %s AND s.market = %s AND s.selection = %s) AS held""",
        (str(match_id), market, selection),
    )
    return bool(rows and rows[0]["held"])


def _vip_1x2_rule() -> tuple[float, float, float]:
    """The EV5 bot's OWN floor and odds range, read from its config (never copied)."""
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG
    cfg = BOTS_CONFIG[VIP_1X2_BOT]
    lo, hi = cfg["odds_range"]
    return float(cfg["edge_thresholds"][1]["1x2_fav"]), float(lo), float(hi)


def in_vip_range(match_id: str, market: str, selection: str, odds: float,
                 kickoff: datetime, at: datetime | None = None) -> str | None:
    """(b) — would a VIP bot take THIS selection at THIS price at time `at`? Returns the reason
    or None. Markets VIP does not trade are never in range."""
    from workers.api_clients.db import execute_query
    at = _aware(at) or _now()
    odds = float(odds)
    if market == "1x2" and selection in ("home", "draw", "away"):
        from workers.jobs.rating_1x2_shadow import COMB_VERSION
        rows = execute_query(
            f"SELECT p_{selection}::float8 AS p FROM rating_1x2_predictions "
            "WHERE match_id = %s AND model_version = %s AND gated",
            (str(match_id), COMB_VERSION),
        )
        if not rows or rows[0]["p"] is None:
            return None
        ev_min, lo, hi = _vip_1x2_rule()
        return REASON_RANGE_1X2 if (lo <= odds <= hi and float(rows[0]["p"]) * odds - 1 >= ev_min) else None
    from workers.jobs import ou_sharp_outlier as ou
    if market in ou.LINES and selection in ("over", "under"):
        rows = execute_query(
            """SELECT DISTINCT ON (selection) selection, odds::float8 AS odds
                 FROM odds_snapshots
                WHERE match_id = %s AND market = %s AND bookmaker = 'Pinnacle'
                  AND selection IN ('over', 'under') AND is_live IS NOT TRUE AND odds > 1.01
                  AND "timestamp" <= %s AND "timestamp" > %s
                ORDER BY selection, "timestamp" DESC""",
            (str(match_id), market, at, at - timedelta(hours=ou.QUOTE_MAX_AGE_H)),
        )
        q = {r["selection"]: r["odds"] for r in rows}
        if "over" not in q or "under" not in q:
            return None
        p_over = ou.power_devig(q["over"], q["under"])
        if p_over is None:
            return None
        p = p_over if selection == "over" else 1 - p_over
        hours = (kickoff - at).total_seconds() / 3600
        return REASON_RANGE_OU if ou.early_rule(odds, p, hours) else None
    return None


def is_held_back(match_id: str, market: str, selection: str, odds: float,
                 at: datetime | None = None, kickoff: datetime | None = None
                 ) -> tuple[str | None, datetime | None]:
    """The rule. Returns (reason, kickoff); reason None = publish normally. Never raises —
    an evaluation failure holds the pick back (fails closed)."""
    at = _aware(at) or _now()
    try:
        if kickoff is None:
            from workers.api_clients.db import execute_query
            r = execute_query("SELECT date FROM matches WHERE id = %s", (str(match_id),))
            kickoff = _aware(r[0]["date"]) if r else None
        kickoff = _aware(kickoff)
        if kickoff is None or kickoff <= at:
            return None, kickoff               # after kickoff nothing is held back
        if vip_held(match_id, market, selection):
            return REASON_HELD, kickoff
        return in_vip_range(match_id, market, selection, odds, kickoff, at), kickoff
    except Exception as e:  # noqa: BLE001 — fail CLOSED, never lose the pick
        log.warning("vip_guard: evaluation failed for %s %s/%s — holding back: %s",
                    match_id, market, selection, e)
        return REASON_ERROR, kickoff or (at + timedelta(hours=72))


def hold_back_fields(bot_id: str, match_id: str, market: str, selection: str, odds: float,
                     at=None) -> dict:
    """For a WRITER about to insert a simulated_bets row: the columns to add ({} when the pick
    publishes normally, or when the bot is itself VIP / hide_pending)."""
    try:
        if is_protected_bot(bot_id):
            return {}
    except Exception as e:  # noqa: BLE001 — unknown bot: treat as free, fail closed below
        log.warning("vip_guard: bot flag lookup failed for %s: %s", bot_id, e)
    reason, kickoff = is_held_back(match_id, market, selection, odds, at=at)
    return {"held_back_until": kickoff, "held_back_reason": reason} if reason else {}


def hold_back_followers(match_id: str, market: str, selection: str) -> dict:
    """After a VIP / hide_pending pick is written: hold back every PENDING free pick on the same
    selection that is not held yet (simulated_bets + the published forward-test arms). A pick
    that was ALREADY SENT or is already settled keeps its record and is flagged
    `vip_rule_breach` — never deleted, never unsent."""
    from workers.api_clients.db import execute_write
    n_sim = execute_write(
        f"""UPDATE simulated_bets s
               SET held_back_until = m.date, held_back_reason = %s,
                   vip_rule_breach = (s.signaled_at IS NOT NULL) OR s.vip_rule_breach
              FROM matches m, bots b
             WHERE m.id = s.match_id AND b.id = s.bot_id AND NOT {PROTECTED_BOT_SQL}
               AND s.match_id = %s AND s.market = %s AND s.selection = %s
               AND s.result = 'pending' AND s.held_back_until IS NULL AND m.date > NOW()""",
        (REASON_HELD, str(match_id), market, selection),
    )
    n_ft = execute_write(
        """UPDATE picks_forward_test p
              SET held_back_until = p.kickoff_at, held_back_reason = %s,
                  vip_rule_breach = (p.telegram_message_id IS NOT NULL) OR p.vip_rule_breach
            WHERE p.arm = ANY(%s) AND p.match_id = %s AND p.market = %s AND p.selection = %s
              AND p.outcome IS NULL AND p.held_back_until IS NULL AND p.kickoff_at > NOW()""",
        (REASON_HELD, list(PUBLISHED_FT_ARMS), str(match_id), market, selection),
    )
    return {"simulated_bets": n_sim, "picks_forward_test": n_ft}


def sweep(dry_run: bool = True, check_range: bool = True) -> dict:
    """RETROACTIVE pass ([[#164]]) over every free pick — the same rule, applied to history.

    * PENDING, kickoff ahead, rule broken -> held_back_until = kickoff (hidden now, visible
      at kickoff); also `vip_rule_breach` when it was already sent to the public channel.
    * SETTLED (or kicked off), rule broken, and it WAS public (sent, a /picks bot, or a published
      forward-test arm) -> `vip_rule_breach` only; it stays counted. Never-public bots: nothing.
    Only picks made since the first VIP pick are judged (before it there was nothing to leak).
    (a) is judged against the VIP ledger (a VIP pick on the selection at or before the free
    pick, or pending now); (b) at the free pick's own pick time and price. ⚠️ (b) for 1X2
    uses the CURRENT NEW+ row (rating_1x2_predictions keeps no history), an approximation
    stated in the report; O/U uses the Pinnacle quotes as of pick time."""
    from workers.api_clients.db import execute_query, execute_write
    from collections import Counter
    out: dict = {"hold": Counter(), "breach": Counter(), "checked": 0}
    sim = execute_query(
        f"""SELECT s.id::text id, b.name bot, s.match_id::text match_id, s.market, s.selection,
                   s.odds_at_pick::float8 odds, s.pick_time, m.date kickoff, s.result::text result,
                   (s.signaled_at IS NOT NULL) sent, b.show_on_picks public, 'sim' src
              FROM simulated_bets s JOIN bots b ON b.id = s.bot_id JOIN matches m ON m.id = s.match_id
             WHERE NOT {PROTECTED_BOT_SQL} AND b.retired_at IS NULL AND s.combo_legs IS NULL
               AND s.match_minute_at_pick IS NULL AND s.held_back_until IS NULL
               AND NOT s.vip_rule_breach
               AND (s.market = '1x2' OR s.market LIKE 'over_under_%%')
               AND s.pick_time >= (SELECT MIN(v.pick_time) FROM simulated_bets v JOIN bots b ON b.id = v.bot_id
                                    WHERE b.vip)""")
    ft = execute_query(
        """SELECT p.id::text id, p.arm bot, p.match_id::text match_id, p.market, p.selection,
                  p.odds::float8 odds, p.published_at pick_time, p.kickoff_at kickoff,
                  COALESCE(p.outcome, 'pending') result, (p.telegram_message_id IS NOT NULL) sent,
                  NOT (p.grade IS NOT DISTINCT FROM 'D' AND p.telegram_message_id IS NULL) public,
                  'ft' src
             FROM picks_forward_test p
            WHERE p.arm = ANY(%s) AND p.held_back_until IS NULL AND NOT p.vip_rule_breach
              AND p.published_at >= (SELECT MIN(v.pick_time) FROM simulated_bets v JOIN bots b ON b.id = v.bot_id
                                      WHERE b.vip)""", (list(PUBLISHED_FT_ARMS),))
    # VIP picks per selection with their pick time (a) — one read, not one per row.
    vip = {}
    for r in execute_query(
            f"""SELECT s.match_id::text match_id, s.market, s.selection, MIN(s.pick_time) first_at,
                       bool_or(s.result = 'pending') pending
                  FROM simulated_bets s JOIN bots b ON b.id = s.bot_id
                 WHERE {PROTECTED_BOT_SQL} AND s.combo_legs IS NULL
                 GROUP BY 1, 2, 3"""):
        vip[(r["match_id"], r["market"], r["selection"])] = r
    now = _now()
    for r in sim + ft:
        out["checked"] += 1
        kickoff, at = _aware(r["kickoff"]), _aware(r["pick_time"])
        v = vip.get((r["match_id"], r["market"], r["selection"]))
        reason = None
        if v is not None and (v["pending"] or _aware(v["first_at"]) <= at):
            reason = REASON_HELD
        elif check_range and kickoff is not None and at is not None and at < kickoff:
            try:
                reason = in_vip_range(r["match_id"], r["market"], r["selection"], r["odds"], kickoff, at)
            except Exception as e:  # noqa: BLE001
                log.warning("sweep: range check failed for %s: %s", r["id"], e)
        if not reason:
            continue
        table = "simulated_bets" if r["src"] == "sim" else "picks_forward_test"
        live = r["result"] == "pending" and kickoff is not None and kickoff > now
        if live:
            out["hold"][r["bot"]] += 1
            if r["sent"]:
                out["breach"][r["bot"] + " (sent, pending)"] += 1
            if not dry_run:
                execute_write(f"UPDATE {table} SET held_back_until = %s, held_back_reason = %s, "
                              f"vip_rule_breach = %s WHERE id = %s AND held_back_until IS NULL",
                              (kickoff, reason, bool(r["sent"]), r["id"]))
        elif r["sent"] or r["public"]:    # it was public before the fix: flag, keep, count
            out["breach"][r["bot"]] += 1
            if not dry_run:
                execute_write(f"UPDATE {table} SET vip_rule_breach = TRUE, held_back_reason = %s "
                              f"WHERE id = %s", (reason, r["id"]))
    return out


if __name__ == "__main__":
    import sys
    res = sweep(dry_run="--apply" not in sys.argv)
    print(("APPLIED" if "--apply" in sys.argv else "DRY RUN"), res)
