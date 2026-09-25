"""ONE STATUS DECIDES DISTRIBUTION ([[#155]], owner 2026-09-25).

A bot's status (`bots.maturity_label`; `retired_at` = retired) is the ONLY per-bot input that
decides where its picks go. No second per-bot switch may drift from it:

    EXPERIMENTAL  admins only (/admin/bots) · nothing sent · nothing public (not even pending rows)
    TESTING       row on /performance marked TESTING · picks SENT (/picks + public Telegram)
                  · counted in its own record · NOT in the headline totals
    BETA          sent · own record · headline totals
    CALIBRATED    same as BETA, with the strongest evidence
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

PUBLIC_STATUSES: frozenset[str] = frozenset({"testing", "beta", "calibrated"})
HEADLINE_STATUSES: frozenset[str] = frozenset({"beta", "calibrated"})

# SQL fragment for "this bot's settled picks count in the headline totals" over `bots b`.
# VIP bots never count in the headline (they have their own record). Used by the
# dashboard_cache headline aggregates in workers/jobs/settlement.py.
HEADLINE_BOT_SQL = "(b.maturity_label IN ('beta','calibrated') AND NOT b.vip)"


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
    picks_public_all (migration 442). Only the two published arms map to bots here."""
    if arm == "consensus_anchor":
        if grade == "D":
            return "bot_consensus_d_v1"
        if grade == "C":
            return "bot_consensus_c_v1"
        return "bot_consensus_b_v1"
    if market == "over_under_25":
        return "bot_sharp_ou_v1"
    return "bot_sharp_1x2_v1"


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
