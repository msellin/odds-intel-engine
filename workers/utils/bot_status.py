"""ONE STATUS DECIDES DISTRIBUTION ([[#155]], owner 2026-09-25).

A bot's status (`bots.maturity_label`; `retired_at` = retired) is the ONLY per-bot input that
decides where its picks go. No second per-bot switch may drift from it:

    EXPERIMENTAL  admins only (/admin/bots) · nothing sent · nothing public (not even pending rows)
    TESTING       row on /performance marked TESTING · every pick on /picks · public Telegram only
                  at EV >= 7% ([[#174]]; 5% until [[#184]] 2026-09-26; public_channel_skip_reason below)
                  · counted in its own record · NOT in the headline totals
    ACTIVE        sent (/picks + every pick to public Telegram) · own record · COUNTS IN THE
                  HEADLINE TOTALS. [[#175]] (owner 2026-09-26, migration 462): BETA and CALIBRATED
                  were merged into ACTIVE — they differed in nothing a reader saw or received.
                  Promotion TESTING -> ACTIVE: 50 settled picks with sharp-anchor CLV > 0.
    VIP           a CHANNEL on top of a public status ("VIP · TESTING"): live picks to the paid
                  channel only, the public sees settled picks, own record, never the headline
    RETIRED       nothing sent; its picks keep counting in the retired totals (#157)

The SQL source of truth is the view `bot_distribution` (migrations 437 + 442) and the function
`bot_public_status(label, retired_at)`; the columns `bots.show_on_picks` /
`bots.show_on_performance` are DERIVED from status by the trigger in migration 442 (an update
that contradicts the status is rejected). This module is the Python face of the same rule, for
callers that decide in code (the forward-test publisher, the config export). Smoke
`ONE-STATUS-DECIDES-DISTRIBUTION` pins the sets here to the SQL.

Real money is NOT a status — it is the per-bot switch on /admin/bots (coolbet_placer_bots).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PUBLIC_STATUSES: frozenset[str] = frozenset({"testing", "active"})
HEADLINE_STATUSES: frozenset[str] = frozenset({"active"})

# SQL fragment for "this bot's settled picks count in the headline totals" over `bots b`.
# VIP bots never count in the headline (they have their own record). Used by the
# dashboard_cache headline aggregates in workers/jobs/settlement.py.
HEADLINE_BOT_SQL = "(b.maturity_label = 'active' AND NOT b.vip)"


def status_of(maturity_label: str | None, retired_at=None, is_active: bool | None = True) -> str:
    """Same CASE as bot_distribution.status."""
    if retired_at is not None or maturity_label == "retired" or is_active is False:
        return "retired"
    return maturity_label or "experimental"


def sends_public(maturity_label: str | None, retired_at=None, vip: bool | None = False,
                 is_active: bool | None = True) -> bool:
    """bot_distribution.sent_public: /picks + the public Telegram channel."""
    return status_of(maturity_label, retired_at, is_active) in PUBLIC_STATUSES and not vip


def on_performance(maturity_label: str | None, retired_at=None, is_active: bool | None = True) -> bool:
    """bot_distribution.on_performance (VIP bots included — settled picks only)."""
    return status_of(maturity_label, retired_at, is_active) in PUBLIC_STATUSES


def in_headline(maturity_label: str | None, retired_at=None, vip: bool | None = False,
                is_active: bool | None = True) -> bool:
    return status_of(maturity_label, retired_at, is_active) in HEADLINE_STATUSES and not vip


def forward_test_bot(arm: str, market: str | None, grade: str | None) -> str:
    """Which bot a forward-test ledger row belongs to — the SAME mapping as the CASE in
    picks_public_all (since migration 454: forward_test_arm_bots, read through the
    forward_test_leg_arm view). Only the two published arms map to bots here; smoke
    FORWARD-TEST-ARM-REGISTRY pins this function to that table for every grade and market."""
    if arm == "consensus_anchor":
        if grade == "D":
            return "bot_consensus_d_v1"
        if grade == "C":
            return "bot_consensus_c_v1"
        return "bot_consensus_b_v1"
    if market == "over_under_25":
        return "bot_sharp_ou_v1"
    return "bot_sharp_1x2_v1"


# ── THE STATUS LINE ON EVERY PUBLIC TELEGRAM PICK ([[#183]], owner 2026-09-26) ───────────────────
# The channel mixed ACTIVE (proven, in the totals) and TESTING (on trial) picks from five methods
# with no way to tell them apart — model O/U picks carried no label at all. Every public pick now
# opens with ONE line: the status word + the bot's public name (the same name as its /performance
# row, so a reader can look up its record). Stamped inside pick_sender.send_pick — no caller can
# skip it. The channel description explains the two words. The consensus B/C grade stays as a
# second line: it grades picks WITHIN one method; the status grades the METHOD's evidence.
PUBLIC_STATUS_BADGE = {
    "active": "🟢 <b>ACTIVE</b>",
    "testing": "🧪 <b>TESTING</b>",
}


def public_status_line(status: str | None, display_name: str | None) -> str:
    """First line of a public Telegram pick, e.g. '🟢 <b>ACTIVE</b> · Sharp-line picks — 1x2'.
    An unknown/unreadable status prints the name only (never a guessed status word)."""
    import html
    badge = PUBLIC_STATUS_BADGE.get((status or "").lower())
    name = html.escape(display_name) if display_name else None
    parts = [x for x in (badge, name) if x]
    return (" · ".join(parts) + "\n") if parts else ""


# ── THE PUBLIC TELEGRAM CHANNEL RULE ([[#174]], owner decision 2026-09-26) ─────────────────────
# /picks shows EVERY pick of a TESTING / ACTIVE bot (unchanged). The public Telegram
# channel is a stricter subset, so each post feels special:
#   * every pick of an ACTIVE bot (BETA / CALIBRATED before [[#175]]), plus
#   * a TESTING pick only when its EV >= PUBLIC_TESTING_MIN_EV (7% since [[#184]], 2026-09-26 —
#     was 5%; raised to match /picks' picks_page_rule.testing_min_ev after 29 TESTING posts in 2 days,
#     17 of them O/U-model picks in one minute; consensus picks, capped at 6% EV, no longer reach
#     the channel; the most-profitable-config audit is [[#185]]), where
#     EV = the bot's own probability x the pick's published odds - 1
#     (simulated_bets: calibrated_prob x odds_at_pick; picks_forward_test: fair_prob (= p_sharp)
#     x odds, i.e. its `edge` column);
#   * never VIP / EXPERIMENTAL / retired (bot_distribution.sent_public) and never a VIP-held pick
#     (#164 held_back_until — checked by the callers, unchanged).
# ONE function, used by BOTH public senders (coolbet_signaler and the forward-test publisher) AND
# enforced again inside pick_sender.send_pick for the public channel, so no third caller can post a
# TESTING pick without passing its EV. The Coolbet real-money edge floors (13pp 1x2 / 8pp O/U) are
# 🤖 OWN placement gates and are NOT part of this rule (they stay on the placer / operator prompt).
PUBLIC_TESTING_MIN_EV = 0.07
# A stable CODE, kept as written when the floor was 5%: the scheduler matches 'testing_' and earlier
# pick_sends rows carry it. It means "below the TESTING EV floor", whatever the floor is.
SKIP_TESTING_BELOW_EV = "testing_below_ev5"
SKIP_TESTING_EV_UNKNOWN = "testing_ev_unknown"


def public_channel_skip_reason(status: str | None, ev, *, sent_public: bool = True) -> str | None:
    """None = this pick may be posted to the public Telegram channel; else the pick_sends reason.
    `status` is bot_distribution.status (lower-case), `ev` the pick's expected return (0.05 = 5%).
    Fails CLOSED: a TESTING pick whose EV cannot be read is not posted."""
    if not sent_public or status not in PUBLIC_STATUSES:
        return f"not_distributed: {status or 'unknown'} does not send to public"
    if status in HEADLINE_STATUSES:
        return None
    try:
        ev_f = float(ev)          # Decimal from psycopg2 -> float (the Decimal-vs-float trap)
    except (TypeError, ValueError):
        return SKIP_TESTING_EV_UNKNOWN
    if ev_f != ev_f:              # NaN
        return SKIP_TESTING_EV_UNKNOWN
    # 1e-9 tolerance: an EV of exactly 5% computed as 0.0499999999 must MEET the threshold.
    return None if ev_f >= PUBLIC_TESTING_MIN_EV - 1e-9 else SKIP_TESTING_BELOW_EV


def public_channel_eligible(status: str | None, ev, *, sent_public: bool = True) -> bool:
    """Bool face of public_channel_skip_reason — the ONE public-Telegram rule ([[#174]])."""
    return public_channel_skip_reason(status, ev, sent_public=sent_public) is None


def load_sent_public_bots() -> set[str] | None:
    """Names of bots whose picks may be SENT now (bot_distribution.sent_public).
    None when unreadable — callers must then send NOTHING (fail closed: a pick we cannot
    prove is allowed out must not go out)."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query("SELECT bot_name FROM bot_distribution WHERE sent_public", [])
        return {r["bot_name"] for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("bot_distribution unreadable — sending nothing this pass: %s", e)
        return None


def bot_is_live(name: str) -> bool:
    """True only for an ACTIVE, non-retired bot. #162 (owner 13A, 2026-09-25): a retired bot writes no new picks
    in ANY writer — the pipeline gate (daily_pipeline_v2) and every standalone paper job that inserts
    shadow_bets on its own schedule (team-total / corners / first-half 1x2 were retired 2026-09-14 and kept
    writing). Their existing picks still settle and keep counting. Fails CLOSED (an unreadable row = not live:
    skipping a paper pick is safe)."""
    try:
        from workers.api_clients.db import execute_query
        r = execute_query("SELECT is_active AND retired_at IS NULL AS live FROM bots WHERE name = %s", [name])
        return bool(r and r[0]["live"])
    except Exception:  # noqa: BLE001
        return False


def sync_rule_versions() -> int:
    """[[#162]] (b): write every registry bot's `rule_version` to `bots.rule_version`, so the
    migration-453 trigger stamps the CURRENT rule on each new pick. Called at scheduler start-up
    (the deploy that ships a rule change restarts it) and every 30 min (covers a deploy that raced
    the migration). Idempotent; returns rows changed; never raises — a failed sync leaves the old
    tag in place, which is logged loudly because it mislabels picks made after a rule change."""
    try:
        from workers.api_clients.db import execute_write
        from workers.registry.bot_registry import BOTS
        pairs = [(b.name, b.rule_version) for b in BOTS]
        if not pairs:
            return 0
        values = ", ".join(["(%s, %s)"] * len(pairs))
        flat = [x for pair in pairs for x in pair]
        n = execute_write(
            f"UPDATE bots SET rule_version = v.rv FROM (VALUES {values}) AS v(name, rv) "
            f"WHERE bots.name = v.name AND bots.rule_version IS DISTINCT FROM v.rv",
            flat,
        ) or 0
        if n:
            log.warning("rule_version sync: %d bot(s) now tag new picks with a new rule version", n)
        return n
    except Exception as e:  # noqa: BLE001
        log.error("rule_version sync FAILED (new picks keep the previous tag): %s", e)
        return 0

