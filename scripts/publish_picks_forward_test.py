#!/usr/bin/env python3
"""Publish the pre-registered sharp-edge PICKS forward test.

THE RULE IS LOCKED. It lives in dev/active/picks-forward-test-preregistration.md
and is restated in RULE below. Changing any constant here invalidates the test
and starts a new one with a new start date — that is the entire point of a
pre-registration, so if you are about to edit a number, edit the doc first and
say why.

    python3 scripts/publish_picks_forward_test.py            # dry run, prints
    python3 scripts/publish_picks_forward_test.py --send     # records + posts

WHY THIS IS NOT PART OF THE PIPELINE (yet): the pipeline's signaler path reads
`simulated_bets` and applies the MODEL edge floors. This rule uses no model and
a different anchor. Wiring it into the pipeline before the forward test resolves
would put an unproven rule behind the same machinery that published manufactured
edge for months. It runs standalone until n=800 decides it.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timezone

from workers.api_clients.db import (
    execute_query, execute_write, execute_write_returning,
)
from workers.model.devig import devig, devig_by
from workers.utils.book_display import display_book
from workers.notify.telegram import send_telegram_public

log = logging.getLogger("picks_forward")

RULE_VERSION = "sharp_edge_v4_2026_09_15"

# ── the locked rule ──────────────────────────────────────────────────────────
MIN_EDGE   = 0.03   # sharp edge floor
MAX_ODDS   = 4.0    # uncapped, the edge collapses into longshot noise
ALIGN_MIN  = 60.0   # anchor and bet quote within 60 min — see note below
# NO DAILY CAP (owner decision, 2026-09-15, amended into v4 before its first pick).
#
# `TOP_N = 8` was pre-registered and carried forward unexamined. Measured over the
# intact window, qualifying legs per day were 10, 10, 7, 4, **32**, 11, 2 — mean
# 10.9, and the cap bound on 4 of 7 days, publishing **45 of 76 (59%)** and
# dropping 31 legs that had cleared the bar.
#
# Worse, under the 30-minute cadence a daily cap is not a quality filter at all:
# it fills with whichever legs qualify EARLIEST in the day, because a live feed
# cannot know the day's best in advance. So it was discarding 41% of picks on a
# rule unrelated to how good they were.
#
# What remains is a CIRCUIT BREAKER, not a selection rule. It exists only to stop
# a data fault (a bad de-vig, a corrupted feed) firing hundreds of messages at 62
# subscribers. At a measured max of 32/day it should never bind in normal
# operation; if it ever does, that is a signal to investigate, not a day's picks.
TOP_N = None                    # no selection cap — publish every qualifying leg
DAILY_RUNAWAY_LIMIT = 60        # circuit breaker only; see above

# RULE-V4-2026-09-15 — cadence. v1-v3 published in ONE batch at 10:00 UTC. The
# candidate window is `now+45min .. now+14h`, so a single 10:00 run can never see
# a kickoff before ~10:45 and can never see 00:00-03:00 kickoffs at all: measured,
# 47% of qualifying legs were structurally unreachable, and an independent replay
# had the 10:00 slot catching 5 of 16 legs on a two-day sample.
#
# v4 runs every 30 minutes. Three things had to be true first, and now are:
#   1. claim-before-send (PUBLISH-CLAIM-BEFORE-SEND) — a leg re-qualifies in a
#      median of 6 consecutive runs, so send-then-record would have posted the
#      same pick ~6 times to 62 subscribers.
#   2. a DB-backed daily cap — `select()` alone caps per CALL, which is
#      meaningless when there are 48 calls a day.
#   3. price persistence measured — 94% of qualifying prices still clear the
#      floor at the same book 30 minutes later (62% at 60), so a 30-minute
#      cadence publishes prices a reader can still get.
#
# WHY THIS IS v4 AND NOT AN EDIT TO v3: it changes WHICH bets are selected, not
# just when they are looked at. The pre-registration is explicit that this starts
# a new test with a new start date. The cost is zero — v2 and v3 published
# nothing at all, so no accumulated n is being discarded.
MAX_RATIO  = 0.20   # book price may not exceed the anchor by more than this
MAX_ANCHOR_OVERROUND = 0.04   # [v3] the anchor must actually BE a sharp line

# ── CONSENSUS ANCHOR ARM (2026-09-22, [[#068]], owner-approved) ──────────────
# WHY A SECOND ARM AND NOT A CHANGED RULE. `MAX_ANCHOR_OVERROUND` is a
# PRE-REGISTERED parameter of the live arm. Loosening it mid-test would forfeit
# the pre-registration, which is the entire basis of /picks' honesty claim. So
# the live arm is left byte-identical and this runs beside it, recorded under
# its own `arm` and its own `rule_version`.
#
# WHY IT EXISTS. The live arm published 294 picks on 2026-09-19 and ZERO on
# 09-22: of 173 markets carrying a Pinnacle price in the window, 173 failed the
# <=4% overround gate (min 5.40%, median 8.40%). That is not a regression — the
# gate has always admitted only ~10-17% of fixtures and it admits them on
# big-liquidity weekend cards, so the channel goes dark midweek by design.
#
# WHY A CONSENSUS IS A LEGITIMATE ANCHOR, measured and not assumed: scoring each
# book's own de-vigged 1x2 probabilities against realised results over 45 days,
# n=11,419 matches, AF-"Pinnacle" alone gives log-loss 0.98401 while a consensus
# EXCLUDING Pinnacle gives 0.98339. Our single-book anchor is statistically
# indistinguishable from an average of the others (t=+1.76), and its median
# closing overround is 10.24% against those books' 7.95% — wider than the books
# it is supposed to be sharper than. So this arm does not lower the bar; it
# stops using a ruler that turned out not to be one.
CONSENSUS_ARM = "consensus_anchor"
CONSENSUS_RULE_VERSION = "consensus_edge_v2_2026_09_24"
# v2 (2026-09-24, [[#106]], owner "yes"): the CREDIBLE-METHOD GATE. v1 published
# whenever edge >= 3% under Shin. The de-vig bake-off (docs/DEVIG_BAKEOFF_2026_09_24.md)
# found no method beats Shin, but three are equally well calibrated — Shin, additive
# and power — and on this arm (each soft book de-vigged at an 8-10% margin, then
# averaged) they move a leg's edge by a median 2.1pp, 90th pct 4.5pp. A leg whose
# edge clears 3% under one credible method and not another exists because of the
# formula, not the market. v2 publishes only when edge >= 3% under ALL of them
# (~7% fewer picks on the frozen panel). Proportional and odds-ratio are NOT in the
# set: both are measurably worse calibrated, and gating on them would reject short
# favourites on a formula known to be wrong. v1 is closed at its n; the
# leaderboard row pools versions, the pre-registered test count restarts.
CREDIBLE_DEVIG_METHODS = ("shin", "additive", "power")
CONSENSUS_MIN_BOOKS = 5     # a consensus of four books is four books
# ⚠️ A CEILING, AND IT IS NOT OPTIONAL — the lesson of [[#007]] arrives here
# unchanged. `edge = p * odds - 1` is MAXIMISED by a wrong price, so the biggest
# apparent edges are our own data faults, not opportunities. Measured on the
# first run of this arm: 7 of 15 qualifying legs cleared +6.6% and 5 cleared +8%
# AGAINST A 7-11 BOOK CONSENSUS, which should be tighter than a single book, not
# looser (a +14.7% edge on a draw at 3.94 against ten books is a broken price).
# The live arm has NO ceiling and must not gain one — it is pre-registered.
CONSENSUS_MAX_EDGE = 0.08
# ── CONSENSUS-ARM GRADING (2026-09-23, [[#094]], owner-approved) ─────────────
# A LABEL, NOT A GATE. Every consensus pick is still published; each one now
# carries grade 'B' (standard) or 'C' (weak) plus the reasons, so a reader can
# choose, and so the arm can later be split into two bots and either retired on
# its own record. It does not change WHICH legs are selected, so
# CONSENSUS_RULE_VERSION is unchanged.
#
# Measured on a replay of this exact rule over 56 days (n=677,
# scripts/consensus_arm_replay.py, docs/PUBLISHED_PICKS_GRADING_2026_09_23.md),
# each condition checked in both chronological halves:
#   * league tier 0 (unclassified: youth, women, reserves, regional cups)
#       n=77   ROI -34.0% (disc -31 / hold -39)
#   * ANOTHER panel book — one that is not offering the price — sees no edge at
#     the published price (its own de-vigged p * odds <= 1)
#       n=122  ROI -36.6% (disc -42 / hold -22)
#   * edge above 6% — the [[#007]] shape again: the biggest edges are the
#     wrong prices, even under the 8% ceiling
#       n=138  ROI -16.6% (disc -14 / hold -21)
#   Grade C (any of the above): n=285 ROI -25.6% (disc -26.0 / hold -24.7)
#   Grade B (none):             n=392 ROI +10.6% (disc +18.3 / hold -3.4)
# ⚠️ B is NOT proven profitable — its holdout is negative and its CI spans zero.
# C is consistently worse in both halves. Hence two grades and no "A".
#
# ── RE-TIERED 2026-09-23 ([[#098]], owner) — THE LETTERS SHIFTED DOWN ─────────
# Owner: "make grade B serve grade A picks and grade C serve grade B picks … we
# don't publish grade C picks at all", and "leave grade A for some model picks,
# not the consensus bot". So the consensus arm now uses B / C / D:
#   B  strongest  — passes every check AND odds 1.20-1.60. The only rule positive
#                   in all three samples: our 56 d +17.8% (48), unseen May-Jul
#                   +14.7% (29), Beat the Bookie 2015-16 +9.8% (696, Holm p<1e-4).
#                   Mechanism: favourite-longshot bias. Floor 1.20 (owner: "better
#                   if we don't bet at 1.1") — <1.20 is ~empty and costs nothing.
#   C  standard   — passes every check, any other odds. +2.9% unseen (n=150),
#                   +3.3% external — positive but unproven.
#   D  weak       — any of the three conditions below. NOT PUBLISHED: claimed to
#                   the ledger (so its record stays honest and checkable) but never
#                   sent. It loses on our own data (-25.6% / -2.8%).
# Grade A is reserved for future MODEL picks and never used by this arm.
#
# WHY A PANEL AND NOT ONE BOOK. Scored against 10,900 results, no book in our
# feed is measurably sharper than any other: Pinnacle's median margin is 9.1%
# and Marathonbet's log-loss is within noise of it (t=-1.6). So no single book
# can be "the" second opinion; any of these five disagreeing is the signal.
# The book offering the price is excluded from its own check — its own de-vig
# always "disagrees" with its own price by roughly its margin, which measures
# nothing.
GRADE_PANEL = ("Pinnacle", "Marathonbet", "Betfair", "1xBet", "SBO")
WEAK_MAX_EDGE = 0.06                # above this edge the pick is weak (grade D)
STRONG_ODDS_MIN, STRONG_ODDS_MAX = 1.20, 1.60   # grade B band
GRADE_REASON_TEXT = {
    "tier0": "lower-profile league",
    "panel": "{books} sees no value at this price",
    "edge": "edge looks too big to be real (often a stale price)",
}


def grade_consensus_pick(edge: float, odds: float, bookmaker: str,
                         league_tier, panel_probs: dict) -> tuple[str, list[str]]:
    """('B'|'C'|'D', reasons). `panel_probs` maps panel book -> its own de-vigged
    probability for THIS selection (books that did not price the full market
    are simply absent). Reasons are machine keys; `panel:<Book>` names the
    dissenting book."""
    reasons = []
    if league_tier == 0:
        reasons.append("tier0")
    for book in GRADE_PANEL:
        p = panel_probs.get(book)
        if book != bookmaker and p is not None and p * odds - 1.0 <= 0.0:
            reasons.append(f"panel:{book}")
    if edge > WEAK_MAX_EDGE:
        reasons.append("edge")
    if reasons:
        return "D", reasons                    # weak — recorded, never published
    return ("B" if STRONG_ODDS_MIN <= odds <= STRONG_ODDS_MAX else "C"), reasons


def _grade_line(c: dict) -> str:
    """The reader-facing grade. Empty for ungraded (live-arm) picks.

    Since [[#095]] each grade is its own tracked bot — B `beta`, C `testing` —
    so the line names the status as well as the grade: a reader seeing a C
    pick knows it is still on trial and scored separately."""
    grade = c.get("grade")
    if not grade:
        return ""
    if grade == "B":
        return "🟢 Grade <b>B</b> — strongest · <i>beta</i>\n"
    if grade == "C":
        return "🔵 Grade <b>C</b> — standard · <i>testing</i>\n"
    why, dissent = [], []
    for r in c.get("grade_reasons") or []:
        if r.startswith("panel:"):
            dissent.append(r.split(":", 1)[1])
        else:
            why.append(GRADE_REASON_TEXT[r])
    if dissent:
        why.insert(0, GRADE_REASON_TEXT["panel"].format(books=" & ".join(dissent)))
    # Grade D is never SENT. This line only appears when a post published before
    # the re-tier (then labelled C) is re-rendered by regrade_consensus_telegram_posts.
    return f"⚪ Weak pick — this type is no longer published: {'; '.join(why)}\n"


def _book_probs(sides, side_q, book):
    """One book's own de-vigged probabilities for a complete market, or None."""
    quotes = [(side_q.get(s) or {}).get(book) for s in sides]
    if any(q is None or q[0] <= 1.0 for q in quotes):
        return None
    return devig([q[0] for q in quotes])


# Arms that actually reach the channel. The dedupe and the runaway breaker are
# about what a READER sees, so they must span every published arm, not one.
PUBLISHED_ARMS = ("live", CONSENSUS_ARM)


def arm_bot_sends(c: dict, arm: str, sent_bots: set | None) -> bool:
    """[[#155]] ONE STATUS DECIDES DISTRIBUTION: may this published-arm pick be SENT?
    Only when the bot the ledger row belongs to (bot_status.forward_test_bot — the same
    mapping as picks_public_all) has a status that sends (bot_distribution.sent_public).
    `sent_bots` None (unreadable) = send nothing. Recording is never affected."""
    if sent_bots is None or arm not in PUBLISHED_ARMS:
        return False
    from workers.utils.bot_status import forward_test_bot
    return forward_test_bot(arm, c.get("market"), c.get("grade")) in sent_bots

# ── TWIN ARMS (2026-09-25, [[#161]], owner-approved) — RECORDED, NEVER PUBLISHED ──
# Two hypothesis arms, each = its parent's rule in EVERY gate plus ONE extra gate the
# [[#156]] audit pointed to. Pre-registered in dev/active/picks-forward-test-
# preregistration.md ("TWIN ARMS — 2026-09-25") before their first pick. Like the junk
# control they are NOT in PUBLISHED_ARMS: never sent, never on /picks or /performance
# (every public view filters an explicit arm allow-list), never in daily_room(). They
# dedupe against their OWN ledger only — a twin is a subset of its parent's legs, so
# seeding it from the published arms would suppress every twin pick.
#
# (A) own-book quote alignment. At our own direct books the book's quote and the
# Pinnacle anchor quote must be <= 5 min apart. Audit: sharp picks at our books read
# +3.9% vs the sharp close when <= 5 min, -0.6% at 5-60 min. API-Football books arrive
# in the same fetch as Pinnacle (gap 0), so they are untouched. The set is frozen HERE
# rather than imported from board_guard.DIRECT_BOOKS: a pre-registered rule must not
# change because an unrelated module's set did.
ALIGNED_ARM = "sharp_own_book_aligned"
ALIGNED_RULE_VERSION = "sharp_edge_v4_ownbook_align5_2026_09_25"
OWN_DIRECT_BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Tonybet")
OWN_BOOK_ALIGN_MIN = 5.0
# (B) sharp confirmation. Where a fresh TIGHT Pinnacle anchor exists
# (workers/utils/anchor.py `pinnacle_tight`), the consensus leg must ALSO clear
# EV >= 0% against it. Audit: consensus picks at our books read -2.1% vs the sharp close.
PINCONF_ARM = "consensus_pin_confirmed"
PINCONF_RULE_VERSION = "consensus_edge_v2_pinconf_2026_09_25"
PINCONF_MIN_EV = 0.0
TWIN_ARMS = (ALIGNED_ARM, PINCONF_ARM)

# Every arm's rule_version, in one place. claim() raises KeyError on an unknown arm —
# the ledger's CHECK constraint would refuse it anyway, this just fails before the DB.
ARM_RULE_VERSION = {
    "live": RULE_VERSION,
    "junk_anchor": RULE_VERSION,
    CONSENSUS_ARM: CONSENSUS_RULE_VERSION,
    ALIGNED_ARM: ALIGNED_RULE_VERSION,
    PINCONF_ARM: PINCONF_RULE_VERSION,
}

# MAX_ANCHOR_OVERROUND is v3's one change (PICKS-ANCHOR-QUALITY-GATE-2026-09-14).
#
# `anchor_overround` was already COMPUTED on every leg (see load_candidates) and
# then thrown away. This is the project's dominant failure shape — a number
# computed but never surfaced — reproduced on a PUBLIC, live, Telegram-published
# feed whose own HEADER below claims the picks are "priced directly against the
# sharpest line in the market". On a third of the slate that is false.
#
# Measured 2026-09-14, this rule's own population, 90d, Shin de-vig, aligned
# <=60 min, the three bettable books (n=326, ROI -13.54% overall):
#
#     anchor overround band          n     ROI
#     <4%   sharp-grade anchor      72    -3.36%
#     4-6%                          76   -22.07%
#     6-9%                          58   -16.00%
#     >=9%  goodwill quote         120   -13.06%   <- 34.1% of the slate
#
# And a paired live-Pinnacle test the same day (n=92, 39 leagues) confirms the
# >=9% band is NOT a feed artefact: where our AF row says 9.28%, real Pinnacle
# says 9.26% (+0.00pp). Pinnacle genuinely charges 9%+ there. A price with no
# size behind it is a quote, not a line, and an "edge" against it is two soft
# prices disagreeing. 4% is the same threshold docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md
# uses for "plausibly a real line", and it is the band where our stored anchor
# is verified accurate (paired delta +0.10pp vs +1.05pp in the 4-6% band).
#
# ⚠️ THIS IS AN HONESTY FIX, NOT AN ALPHA FIX — state it plainly whenever the
# rule is described. Gating does NOT make the rule profitable: the retained band
# is still -3.36% at n=72. It stops us publishing an edge computed against a
# quote with no size behind it. If the honest answer stays "this rule has no
# demonstrable edge at any anchor quality", that belongs on /performance and in
# the Telegram feed — publishing +X% while the honest number is negative is the
# exact pattern CLAUDE.md was written to prevent.
#
# 👥 PICKS — it changes what readers are told, not what we stake.
#
# VOLUME COST, stated up front because it is the owner's call and not an
# implementation detail (CLAUDE.md, "when the two directions conflict, say so"):
# the <4% band was 72 of 326 legs (22%) in the ROI split and 11 of 92 fixtures
# (12%) in the paired test. This will often publish only a handful of
# picks. Loosening to 0.06 roughly doubles volume and admits the worst-measured
# band (-22.07%). That trade-off is the owner's to make; the constant is here.

# MAX_RATIO is v2's one change, and it is the ONLY change (RULE-V2-2026-09-15).
#
# Production has carried ODDS-OUTLIER-FILTER-2026-08-18 since August — >35 pct
# over anchor for 1x2 — because AF's Bet365 quotes run ~26.6 pct above
# contemporaneous Pinnacle: stale or shell prices nobody can take. This
# standalone publisher never applied it. Six of the eight picks published on
# 2026-09-14 under v1 sat above 20 pct, and the top one at +35.7 pct.
#
# Measured on the time-aligned backtest, ROI by book/anchor ratio band:
#     0-10 pct   n=202  +11.75 pct
#    10-20 pct   n=763   +6.24 pct
#    20-35 pct   n=225  -12.13 pct   <-- the leak
#    35+  pct    n= 44   +7.32 pct   (n too small to read)
#
# So the loss lives in 20-35 pct, BELOW the existing 35 pct filter — exactly what
# the BET365-EXECUTION-AUDIT note predicted ("the ~20-30 pct band still leaks
# through and generates -20 pct ROI picks"). Capping at 20 pct moves the rule
# from +3.83 pct to +7.39 pct (n 1234 -> 965).
#
# WHY THIS IS A NEW TEST, NOT A PATCH. The pre-registration says changing the
# rule after the first publication invalidates the test and starts a new one
# with a new start date. It does. v1 is 8 picks on 2026-09-14 and is CLOSED at
# that n; v2 starts fresh. Quietly tightening a running pre-registered rule and
# carrying the n forward is precisely the discipline failure the whole document
# exists to prevent.
LOOKAHEAD_H = 14    # publish for fixtures kicking off inside this window
MIN_LEAD_MIN = 45   # never publish a price a reader cannot reach in time

# ALIGNMENT is the load-bearing constant. Unaligned, this rule backtested
# +8.47pct; aligned it reads +5.5pct. The selection was picking STALE soft-book
# quotes — median gap on selected legs was 360 minutes. Pinnacle refreshes ~5
# min before kickoff; Coolbet is frozen 57pct of the time. Comparing a fresh
# anchor against a stale price measures drift, not mispricing.

# Feeds that quote prices the book does not honour. Measured against the books'
# own sites: AF Unibet 33.1pct phantom-high (and dead since 2026-09-12),
# Unibet-Kambi 38pct. Max/Avg are synthetic aggregates and the rest are
# football-data.co.uk CSV imports, not live books.
EXCLUDED_BOOKS = ("Max", "Avg", "Betfair Exchange", "BetWin", "Betfred",
                  "Unibet-Kambi", "Unibet")
# TONYBET (#101, 2026-09-23): briefly fenced out here while unverified, then let
# in the same day with the owner's go-ahead. Not a rule change: the locked rule is
# "best across ALL books, phantom feeds excluded by name", and Tonybet is a real
# book verified against its own site (14/15 prices identical). Excluding it was
# the departure from the rule. Dated note in the pre-registration doc.

MARKETS = {"1x2": ["home", "draw", "away"],
           "over_under_25": ["over", "under"]}

PICK_LABEL = {
    ("1x2", "home"): "Home win",
    ("1x2", "away"): "Away win",
    ("1x2", "draw"): "Draw",
    ("over_under_25", "over"): "Over 2.5 goals",
    ("over_under_25", "under"): "Under 2.5 goals",
}

HEADER = (
    "📊 <b>New selection method — starting today</b>\n\n"
    "We've rebuilt how these picks are chosen. They're now priced directly "
    "against the sharpest line in the market rather than against our own model."
    "\n\n<b>No past performance is claimed for this method.</b> It starts today "
    "at zero, and we'll publish the result — win or lose — as it accumulates."
    "\n\nToday: {n} picks."
)


def _consensus_anchor(sides, side_q, devig_fn=None):
    """Fair-value probabilities from a CONSENSUS of books, or None.

    Each book that prices the COMPLETE market is de-vigged on its own, then the
    resulting probabilities are averaged. De-vig-then-average, never
    average-then-de-vig: averaging raw prices across books with different
    margins produces a market that does not sum to anything meaningful, and the
    margin you then strip is an artifact of the mix (ANALYSIS_GOTCHAS §62 is the
    within-book version of this trap).

    A partial line is skipped rather than guessed — the same rule the live arm
    applies to Pinnacle. Returns (probs_by_selection, anchor_ts, n_books)."""
    per, stamps = [], []
    books = {b for s in sides for b in (side_q.get(s) or {})}
    for b in books:
        quotes = [(side_q.get(s) or {}).get(b) for s in sides]
        if any(q is None or q[0] <= 1.0 for q in quotes):
            continue
        probs = (devig_fn or devig)([q[0] for q in quotes])
        if probs:
            per.append(probs)
            stamps.append(max(q[1] for q in quotes))
    if len(per) < CONSENSUS_MIN_BOOKS:
        return None
    avg = [sum(p[i] for p in per) / len(per) for i in range(len(sides))]
    return dict(zip(sides, avg)), max(stamps), len(per)


def load_candidates(anchor: str = "pinnacle") -> tuple[list[dict], list[dict]]:
    """Returns (live picks, full candidate pool).

    `anchor="pinnacle"` is the PRE-REGISTERED live arm and its behaviour is
    frozen. `anchor="consensus"` swaps ONLY the source of the fair-value
    probability ([[#068]]); every downstream guard — alignment window, MAX_ODDS,
    MAX_RATIO, the 3% edge floor, the lead time, the lookahead — is shared, so
    the two arms differ in exactly one variable.

    The POOL is every leg that clears the odds cap and the alignment window,
    with the edge floor NOT yet applied. The live arm is the pool filtered at
    MIN_EDGE (no daily cap). The junk arm needs the unfiltered pool because it
    re-runs the SAME rule — floor and all — on a shuffled anchor; selecting from
    the live picks instead would make the control a relabelling of the live arm
    rather than an independent draw. See junk_anchor_arm().
    """
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id, o.market, o.selection, o.bookmaker,
               o.odds::float AS odds, o.timestamp,
               m.date AS kickoff,
               ht.name AS home_team, at.name AS away_team, l.name AS league,
               l.tier AS league_tier
          FROM odds_snapshots o
          JOIN matches m  ON m.id  = o.match_id
          JOIN teams   ht ON ht.id = m.home_team_id
          JOIN teams   at ON at.id = m.away_team_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE o.market = ANY(%s)
           AND o.is_live IS NOT TRUE
           AND NOT (o.bookmaker = ANY(%s))
           AND m.date > now() + (%s || ' minutes')::interval
           AND m.date < now() + (%s || ' hours')::interval
           AND o.timestamp > now() - interval '6 hours'
           -- PUBLISHED-A-POSTPONED-FIXTURE (2026-09-22, [[#068]]). There was NO
           -- status filter here, on either arm. On the consensus arm's first day
           -- that put 3 of 20 picks on POSTPONED matches in front of 62
           -- subscribers — Chippenham v Sholing, Yate Town v Evesham, Poole Town
           -- v Wimborne, all English non-league, all 18:45.
           --
           -- We knew. All three were stamped `postponed` at 09:15:33 UTC and we
           -- published them at 12:15:55 — three hours later. They void harmlessly
           -- in the ledger, which is exactly why this could have run for months
           -- unnoticed: the P&L is unaffected and only a reader sees the problem.
           --
           -- `= 'scheduled'` and not `<> 'postponed'`: the live values are
           -- scheduled / finished / postponed / live, and an allow-list refuses
           -- a status nobody has thought about yet. A pick on a finished or
           -- in-play match is just as wrong as one on a postponed match, and
           -- this feed is pre-match only.
           AND m.status = 'scheduled'
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker,
                  o.timestamp DESC
        """,
        (list(MARKETS), list(EXCLUDED_BOOKS), str(MIN_LEAD_MIN), str(LOOKAHEAD_H)),
    )

    quotes: dict = {}
    meta: dict = {}
    for r in rows:
        meta[r["match_id"]] = r
        quotes.setdefault(r["match_id"], {}).setdefault(r["market"], {}) \
              .setdefault(r["selection"], {})[r["bookmaker"]] = (
                  r["odds"], r["timestamp"])

    out: list[dict] = []
    for mid, by_market in quotes.items():
        for market, sides in MARKETS.items():
            side_q = by_market.get(market) or {}
            if anchor == "consensus":
                got = _consensus_anchor(sides, side_q)
                if got is None:
                    continue
                prob_by_sel, anchor_ts, n_books = got
                # [[#106]] the same consensus under every OTHER credible de-vig
                # method, so each leg can carry its worst credible edge (v2 gate).
                alt_probs = {}
                for meth in CREDIBLE_DEVIG_METHODS:
                    if meth == "shin":
                        continue
                    ga = _consensus_anchor(sides, side_q,
                                           lambda o, meth=meth: devig_by(meth, o))
                    if ga is not None:
                        alt_probs[meth] = ga[0]
                # The de-vigged consensus IS the fair line, so its overround is
                # 0 by construction and MAX_ANCHOR_OVERROUND cannot be the
                # quality test here. Book COUNT is (CONSENSUS_MIN_BOOKS above);
                # recording 0.0 keeps the column honest rather than implying a
                # margin was measured and passed.
                overround = 0.0
                anchor_odds = {s: 1.0 / p for s, p in prob_by_sel.items()}
                anchor_book = f"consensus:{n_books}"
                probs = [prob_by_sel[s] for s in sides]
            else:
                pin = {s: side_q.get(s, {}).get("Pinnacle") for s in sides}
                if any(pin[s] is None for s in sides):
                    continue
                anchor_ts = max(pin[s][1] for s in sides)
                anchor_odds = {s: pin[s][0] for s in sides}
                anchor_book = "Pinnacle"
                overround = sum(1.0 / o for o in anchor_odds.values()) - 1.0
                if overround > MAX_ANCHOR_OVERROUND:
                    continue      # [v3] not a sharp line — see MAX_ANCHOR_OVERROUND.
                                  # Applied to the POOL, not in select(), so the
                                  # junk arm is gated identically: whatever test
                                  # the live arm gets, every control arm gets.
                probs = devig([anchor_odds[s] for s in sides])
                if probs is None:
                    continue
            for s, p_sharp in zip(sides, probs):
                aligned = {
                    b: (o, t) for b, (o, t) in (side_q.get(s) or {}).items()
                    if abs((t - anchor_ts).total_seconds()) / 60.0 <= ALIGN_MIN
                }
                if not aligned:
                    continue
                book, (odds, ts) = max(aligned.items(), key=lambda kv: kv[1][0])
                if odds > MAX_ODDS:
                    continue
                if odds / anchor_odds[s] - 1.0 > MAX_RATIO:
                    continue          # phantom/stale price — see MAX_RATIO
                edge = p_sharp * odds - 1.0
                edge_credible_min = edge
                if anchor == "consensus":
                    for ap in alt_probs.values():
                        edge_credible_min = min(edge_credible_min, ap[s] * odds - 1.0)
                grade, grade_reasons = None, None
                if anchor == "consensus":
                    idx = sides.index(s)
                    panel = {}
                    for pb in GRADE_PANEL:
                        bp = _book_probs(sides, side_q, pb)
                        if bp:
                            panel[pb] = bp[idx]
                    grade, grade_reasons = grade_consensus_pick(
                        edge, odds, book, meta[mid].get("league_tier"), panel)
                # NOTE: the MIN_EDGE floor is applied by select() below, not
                # here — the junk arm must see the same unfiltered pool.
                m = meta[mid]
                out.append({
                    "match_id": mid, "market": market, "selection": s,
                    "odds": odds, "bookmaker": book, "edge": edge,
                    "p_sharp": p_sharp, "anchor_odds": anchor_odds,
                    "anchor_overround": overround, "anchor_quoted_at": anchor_ts,
                    "anchor_bookmaker": anchor_book,
                    "price_ratio": odds / anchor_odds[s] - 1.0,
                    "odds_quoted_at": ts,
                    "alignment_gap_minutes":
                        abs((ts - anchor_ts).total_seconds()) / 60.0,
                    "kickoff_at": m["kickoff"], "home_team": m["home_team"],
                    "away_team": m["away_team"], "league": m["league"] or "",
                    "grade": grade, "grade_reasons": grade_reasons,
                    "edge_credible_min": edge_credible_min,
                })

    return select(out), out


def already_published_markets(arms: tuple = PUBLISHED_ARMS) -> set:
    """(match_id, market) pairs that already have a LIVE pick out.

    `arms` defaults to PUBLISHED_ARMS; only the recorded-never-published twin arms
    ([[#161]]) pass their own arm, so their dedupe is against their own ledger.

    ONE-SELECTION-PER-MARKET, part 2 (2026-09-15). `select()` dedupes within a
    single run, which was enough at one run a day and is not enough at 48. On
    the day the cadence shipped, Platense v Fluminense published HOME at 15:05
    and then the DRAW at 17:35 — the second leg qualified in a later run, by
    which time the first was no longer in the pool to dedupe against.

    To a reader that is covering both ways on one match, and it makes the day's
    pick count not a count of opinions. The dedupe has to consult the LEDGER,
    not just the current pool.
    """
    # ARM-SPANNING (2026-09-22, [[#068]]): this used to read `arm = 'live'`.
    # With a second PUBLISHED arm that would let the consensus arm post the
    # opposite side of a match the live arm already sent — the reader sees one
    # channel, so the dedupe has to span every arm that reaches it. The junk
    # control is deliberately NOT in PUBLISHED_ARMS: it is never sent, so it must
    # never suppress a real pick.
    try:
        rows = execute_query(
            """SELECT DISTINCT match_id::text AS m, market
                 FROM picks_forward_test WHERE arm = ANY(%s)""",
            (list(arms),),
        )
        return {(r["m"], r["market"]) for r in rows}
    except Exception as e:
        # Fail CLOSED would mean publishing nothing; fail open would mean
        # risking a contradictory pair. Prefer the contradiction being
        # impossible: an unreadable ledger blocks publication for this pass.
        log.warning("already_published_markets unreadable — skipping this pass: %s", e)
        return None


def daily_room() -> int:
    """How many more live picks may publish today, against the RUNAWAY BREAKER.

    This is no longer a cap on the day's picks — the owner removed that on
    2026-09-15 because it was dropping 41% of qualifying legs and selecting by
    earliness rather than quality. It is a fault breaker: if a bad de-vig or a
    corrupted feed makes hundreds of legs "qualify", this stops the channel being
    flooded. It should never bind in normal operation (measured max 32/day).


    RULE-V4: `select()` caps per CALL. Under a 30-minute cadence that is 48 calls
    a day and the cap stops meaning anything, so the count has to come from the
    database.

    Counted on `published_at::date` in UTC, deliberately, NOT on kickoff date:
    with LOOKAHEAD_H=14 a single run spans two kickoff dates, which makes a
    kickoff-date cap unenforceable. The visible consequence is that 00:00-03:00
    kickoffs are only ever in window from ~10:00-13:00 the previous day and so
    consume the PREVIOUS day's allowance. That is a real quirk and it is written
    down rather than discovered later.

    Falls CLOSED (returns 0) if the count cannot be read. A cap that fails open
    is not a cap, and the surface it protects is a public channel."""
    try:
        rows = execute_query(
            """SELECT count(*) AS n FROM picks_forward_test
                WHERE arm = ANY(%s)
                  AND published_at::date = (now() AT TIME ZONE 'utc')::date""",
            (list(PUBLISHED_ARMS),),
        )
        return max(0, DAILY_RUNAWAY_LIMIT - int(rows[0]["n"]))
    except Exception as e:
        log.warning("daily_room unreadable — publishing nothing this pass: %s", e)
        return 0


def select(cands: list[dict], room: int | None = None,
           max_edge: float | None = None, credible_gate: bool = False,
           dedupe_arms: tuple | None = None) -> list[dict]:
    """Edge floor, then the best `room` by edge.

    `max_edge` is the [[#007]] ceiling and is used ONLY by the consensus arm —
    the live arm is pre-registered without one and must not acquire one. An edge
    above the ceiling is discarded as a broken price, not published as a big
    opportunity: `edge = p * odds - 1` is maximised by a wrong price, so the
    largest values in any anchored feed are its data faults.

    `room=None` means NO truncation — publish every leg that clears the floor.
    The live path passes `daily_room()`, which is the runaway breaker rather than
    a selection cap."""
    # `credible_gate` ([[#106]], consensus v2 only): the floor must hold under every
    # credible de-vig method, not just Shin. The live arm never passes it — its rule
    # is pre-registered, and on Pinnacle the methods differ by only ~0.6pp.
    keep = [c for c in cands if c["edge"] >= MIN_EDGE
            and (not credible_gate or c.get("edge_credible_min", c["edge"]) >= MIN_EDGE)
            and (max_edge is None or c["edge"] <= max_edge)]
    keep.sort(key=lambda c: -c["edge"])

    # ONE-SELECTION-PER-MARKET (2026-09-15). Nothing stopped both sides of the
    # same market publishing. Measured over 7 days: FOUR matches went out with
    # two selections of the same 1x2 — Club Brugge home AND away, Stevenage home
    # AND away, Independiente away AND draw **in the same run**, Sudtirol draw
    # AND away. To a reader that is covering both ways, and it means a day's
    # "8 picks" are not 8 opinions. Keep the highest-edge side only.
    # Seed the dedupe with markets that ALREADY have a published pick, so a
    # later run cannot publish the opposite side of one we have already sent.
    # `dedupe_arms` is passed ONLY by the recorded twin arms ([[#161]]): they dedupe
    # against their own ledger. Every other caller keeps the published-arms seed.
    published = (already_published_markets() if dedupe_arms is None
                 else already_published_markets(dedupe_arms))
    if published is None:
        return []
    seen: set = {(m, mk) for m, mk in published}
    deduped = []
    for c in keep:
        key = (str(c["match_id"]), c["market"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped if room is None else deduped[:room]


def published_selection_keys(match_ids) -> set:
    """(match_id, market, selection) already in the ledger for the published
    arms — so a leg published on an earlier pass is not re-labelled as a
    rejection on every later pass (found by review before the first live run)."""
    ids = list({str(m) for m in match_ids})
    if not ids:
        return set()
    rows = execute_query(
        """SELECT match_id::text AS m, market, selection FROM picks_forward_test
            WHERE arm = ANY(%s) AND match_id = ANY(%s::uuid[])""",
        (list(PUBLISHED_ARMS), ids))
    return {(r["m"], r["market"], r["selection"]) for r in rows}


def funnel_rows(pool: list[dict], picked: list[dict], source: str,
                max_edge: float | None = None, published: set | None = None,
                credible_gate: bool = False) -> list[dict]:
    """CANDIDATE-FUNNEL ([[#082]]): every pool leg within 5pp of the floor, with
    the reason it was or was not published. `select()` keeps nothing it drops,
    so without this no floor / ceiling / grade question has a population.

    Steps: selected | selected_unsent (grade D — claimed, never sent) |
    below_floor | above_ceiling | deduped_or_capped (one-side-per-market,
    already published, or the runaway breaker). Stores price + p_sharp, never
    the edge (derived on read)."""
    from workers.utils.candidate_funnel import NEAR_FLOOR_PP
    now = datetime.now(timezone.utc)
    chosen = {(str(c["match_id"]), c["market"], c["selection"]) for c in picked}
    if published is None:
        published = published_selection_keys(c["match_id"] for c in pool)
    out = []
    for c in pool:
        e = c.get("edge")
        if e is None or e < MIN_EDGE - NEAR_FLOOR_PP:
            continue
        key = (str(c["match_id"]), c["market"], c["selection"])
        if key in published and key not in chosen:
            continue            # published on an earlier pass — already recorded as selected
        if key in chosen:
            step = "selected_unsent" if c.get("grade") == "D" else "selected"
        elif e < MIN_EDGE:
            step = "below_floor"
        elif max_edge is not None and e > max_edge:
            step = "above_ceiling"
        elif credible_gate and c.get("edge_credible_min", e) < MIN_EDGE:
            step = "method_sensitive"     # [[#106]]: clears 3% under Shin, not under every credible method
        else:
            step = "deduped_or_capped"
        if source == "publisher_consensus":
            bot = {"B": "bot_consensus_b_v1", "C": "bot_consensus_c_v1",
                   "D": "bot_consensus_d_v1"}.get(c.get("grade"), "bot_consensus_c_v1")
        else:
            # [[#122]] the sharp arm is owned by market since migration 402
            bot = "bot_sharp_ou_v1" if c["market"] == "over_under_25" else "bot_sharp_1x2_v1"
        anchor = c.get("anchor_bookmaker") or "Pinnacle"
        qt = c.get("odds_quoted_at")
        out.append({
            "source": source, "bot": bot, "match_id": str(c["match_id"]),
            "market": c["market"], "selection": c["selection"],
            "bookmaker": c.get("bookmaker"), "odds": c["odds"],
            "fair_prob": c.get("p_sharp"),
            "fair_source": anchor if anchor.startswith("consensus:") else "pinnacle_shin",
            "raw_prob": None, "threshold": MIN_EDGE, "step": step,
            "quote_age_min": round((now - qt).total_seconds() / 60, 1) if qt else None,
        })
    return out


def junk_anchor_arm(pool: list[dict]) -> list[dict]:
    """Negative control: the SAME rule, anchor shuffled to a DIFFERENT fixture.

    Expected to lose roughly the vig. If this arm makes money the harness is
    broken and the live arm means nothing. Not published — recorded only.

    JUNK-ARM-DEGENERATE-2026-09-14 — what this used to do, and why it could not
    work. The first version took the eight LIVE picks, overwrote each one's
    `p_sharp` with a donor's, and recorded them. Selection never changed: the
    junk rows were the same eight fixtures, markets, selections and prices as
    the live arm, so they were guaranteed to settle to identical outcomes. The
    control could not disagree with the live arm about anything. Verified on the
    day-one rows — all eight junk rows match a live row on
    (match_id, market, selection, odds, bookmaker).

    A junk anchor has to change WHICH BETS ARE CHOSEN, because that is the only
    thing the anchor does in this rule. So: assign every candidate in the pool a
    donor `p_sharp` from a different fixture in the same market, recompute the
    edge, and run the same floor-and-top-N selection over the result. The picks
    that come out are a different set, chosen by a number that carries no
    information — which is exactly the null this test needs.
    """
    if len(pool) < 2:
        return []
    # RULE-V4: seed per (date, run) rather than with a fixed constant. Under one
    # run a day a constant seed was merely reproducible; under 48 runs it makes
    # every run's draw IDENTICAL, so the control stops being an independent
    # sample and starts being the same draw counted 48 times.
    rng = random.Random(int(datetime.now(timezone.utc).timestamp()) // 1800)
    junk: list[dict] = []
    for c in pool:
        others = [o for o in pool if o["match_id"] != c["match_id"]
                  and o["market"] == c["market"]]
        if not others:
            continue
        donor = rng.choice(others)
        d = dict(c)
        d["p_sharp"] = donor["p_sharp"]
        d["edge"] = donor["p_sharp"] * c["odds"] - 1.0
        d["arm"] = "junk_anchor"
        junk.append(d)
    return select(junk)


def own_book_aligned(c: dict) -> bool:
    """[[#161]] twin (A)'s ONE extra gate: at our own direct books, quote and anchor
    must be <= OWN_BOOK_ALIGN_MIN apart. Every other book passes unchanged."""
    return (c["bookmaker"] not in OWN_DIRECT_BOOKS
            or float(c["alignment_gap_minutes"]) <= OWN_BOOK_ALIGN_MIN)


def aligned_twin_arm(pool: list[dict], room: int | None = None) -> list[dict]:
    """Twin (A): the live v4 rule over the SAME pool, plus own_book_aligned().

    The gate filters the pool BEFORE select(), exactly where every other v4 gate sits,
    so one-selection-per-market picks the best side among legs that pass. A failing
    leg is dropped, never re-routed to another book: every twin pick is the same
    (book, price) v4 would have taken. Recorded, never published."""
    out = []
    for c in pool:
        if not own_book_aligned(c):
            continue
        d = dict(c)
        d["arm"] = ALIGNED_ARM
        d["twin_gate"] = {"own_book": c["bookmaker"] in OWN_DIRECT_BOOKS,
                          "gap_min": round(float(c["alignment_gap_minutes"]), 2),
                          "max_gap_min": OWN_BOOK_ALIGN_MIN}
        out.append(d)
    return select(out, room, dedupe_arms=(ALIGNED_ARM,))


def pinnacle_tight_probs(match_id, market: str, at: datetime) -> dict | None:
    """{selection: Shin p} from a fresh TIGHT Pinnacle set, or None — the anchor.py
    `pinnacle_tight` tier on its own (one complete set from one fetch, <= 60 min old,
    overround <= 4%). Consensus members are deliberately not passed in: this asks only
    whether the sharp line is there and tight."""
    from workers.utils.anchor import PIN, PIN_MAX_AGE_MIN, compute_anchor, load_sets, market_sides
    sides = market_sides(market)
    if not sides:
        return None
    pin = load_sets(str(match_id), market, sides, at=at, lookback_min=PIN_MAX_AGE_MIN).get(PIN)
    if not pin:
        return None
    a = compute_anchor({PIN: pin}, sides, at=at)
    return dict(a.probs) if a.source == "pinnacle_tight" else None


def pin_confirmed_twin_arm(consensus_pool: list[dict], room: int | None = None,
                           at: datetime | None = None, pin_lookup=None) -> list[dict]:
    """Twin (B): consensus v2 over the SAME pool (same ceiling, same credible-method
    gate, grades as the parent), plus: where a fresh tight Pinnacle anchor exists the
    leg must ALSO have EV >= PINCONF_MIN_EV against it. No tight Pinnacle -> the leg
    passes unchanged. Recorded, never published.

    Pinnacle is looked up only for legs already at or above MIN_EDGE (select() drops
    the rest anyway), once per (match, market). `pin_lookup` is injectable for tests."""
    at = at or datetime.now(timezone.utc)
    lookup = pin_lookup or pinnacle_tight_probs
    cache: dict = {}
    out = []
    for c in consensus_pool:
        d = dict(c)
        d["arm"] = PINCONF_ARM
        if c["edge"] >= MIN_EDGE:
            key = (str(c["match_id"]), c["market"])
            if key not in cache:
                cache[key] = lookup(c["match_id"], c["market"], at)
            probs = cache[key]
            p = (probs or {}).get(c["selection"])
            if p:
                ev = p * c["odds"] - 1.0
                d["twin_gate"] = {"pin_tight": True, "pin_p": round(p, 5),
                                  "pin_ev": round(ev, 5), "min_ev": PINCONF_MIN_EV}
                if ev < PINCONF_MIN_EV:
                    continue
            else:
                d["twin_gate"] = {"pin_tight": False}
        out.append(d)
    return select(out, room, max_edge=CONSENSUS_MAX_EDGE, credible_gate=True,
                  dedupe_arms=(PINCONF_ARM,))


def record_twin_arms(pool: list[dict], consensus_pool: list[dict],
                     room: int, consensus_room: int) -> dict:
    """Claim both twin arms' picks. Never sends. Never raises: a twin is an experiment
    and must not be able to take down the published arms or the junk control — a
    failure is returned in the counts (job metadata) and logged."""
    out = {}
    for arm, fn in ((ALIGNED_ARM, lambda: aligned_twin_arm(pool, max(0, room))),
                    (PINCONF_ARM, lambda: pin_confirmed_twin_arm(consensus_pool,
                                                                 max(0, consensus_room)))):
        try:
            picks = fn()
            out[arm] = sum(1 for c in picks if claim(c, arm) is not None)
        except Exception as e:  # noqa: BLE001
            log.warning("twin arm %s failed (non-fatal, nothing published): %s", arm, e)
            out[arm] = f"error: {type(e).__name__}"
    return out


def claim(c: dict, arm: str) -> str | None:
    """Reserve this leg BEFORE sending, returning its new row id — or None if it
    was already published.

    PUBLISH-CLAIM-BEFORE-SEND (2026-09-15). `record()` used to run AFTER the
    Telegram send, and `ON CONFLICT ... DO NOTHING` then suppressed the duplicate
    ROW while the duplicate MESSAGE had already gone out to 62 subscribers. That
    was survivable at one run a day and is not survivable at any other cadence: a
    qualifying leg re-qualifies in a median of 6 consecutive runs (mean 6.8, max
    13), so 13 picks would have produced ~88 channel messages.

    It also broke the invariant the read path states in as many words — that the
    published set and the recorded ledger are the same set. With send-then-record
    the ledger kept the FIRST row and the channel showed the LAST message, and on
    a measured day those disagreed on price by 3.6% (2.75 -> 2.85, edge +4.3% ->
    +8.1%).

    The database is the only thing that can arbitrate this. `_LAST_SENT` in
    workers/notify/telegram.py cannot: `send_telegram_public` has no dedup window
    at all, and `_LAST_SENT` is an in-process dict wiped on every scheduler
    restart — which is exactly how RELIABILITY_LEDGER #13 happened, four restarts
    in ninety minutes.

    So: INSERT ... RETURNING id. A returned id means WE created the row and may
    send. An empty result means somebody already did, and we must not.
    """
    # [[#164]] VIP FIRST — a PUBLISHED arm's pick that a VIP bot holds, or that is in VIP's
    # range at this price now, is still claimed and counted (the pre-registered test keeps
    # every pick; prereg deviation 2026-09-25: such picks publish at kickoff, not before) but
    # is stamped held_back_until = kickoff in the SAME insert, and `c["held_back_reason"]` tells
    # every sender to skip it. The ONE rule: workers/utils/vip_guard.py. Recorded-only arms
    # (junk control, twins) are never public, so they are not judged.
    held_until = held_reason = None
    if arm in PUBLISHED_ARMS:
        from workers.utils.vip_guard import is_held_back
        held_reason, _ko = is_held_back(c["match_id"], c["market"], c["selection"], c["odds"],
                                        kickoff=c.get("kickoff_at"))
        held_until = _ko if held_reason else None
    c["held_back_reason"] = held_reason
    rows = execute_write_returning(
        """
        INSERT INTO picks_forward_test
            (match_id, market, selection, odds, bookmaker, edge, p_sharp,
             anchor_odds, anchor_overround, anchor_quoted_at, odds_quoted_at,
             alignment_gap_minutes, arm, rule_version, kickoff_at,
             telegram_message_id, anchor_bookmaker, grade, grade_reasons,
             held_back_until, held_back_reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (match_id, market, selection, arm) DO NOTHING
        RETURNING id
        """,
        (c["match_id"], c["market"], c["selection"], c["odds"], c["bookmaker"],
         c["edge"], c["p_sharp"], json.dumps(c["anchor_odds"]),
         c["anchor_overround"], c["anchor_quoted_at"], c["odds_quoted_at"],
         c["alignment_gap_minutes"], arm,
         # ANCHOR-BASIS-RECORDED (2026-09-22): `anchor_bookmaker` is on the table
         # and was never written, so no row could say what it was priced against.
         # With two anchors live that is no longer a tidiness issue — it is the
         # difference between two arms' results being separable and not.
         ARM_RULE_VERSION[arm],
         c["kickoff_at"], None, c.get("anchor_bookmaker"),
         # [[#094]] consensus-arm grade; NULL on the live/junk arms.
         c.get("grade"), c.get("grade_reasons"), held_until, held_reason),
    )
    pick_id = str(rows[0]["id"]) if rows else None
    # [[#161]] the twin arms' extra-gate evidence, written in a SEPARATE statement and
    # only for twin rows: the published arms' INSERT above stays column-for-column what
    # it was, so a deploy that lands before migration 434 cannot break the channel.
    if pick_id and arm in TWIN_ARMS and c.get("twin_gate") is not None:
        execute_write("UPDATE picks_forward_test SET twin_gate = %s WHERE id = %s",
                      (json.dumps(c["twin_gate"], default=float), pick_id))
    return pick_id


def attach_message_id(pick_id: str, message_id: int | None) -> None:
    """Stamp the Telegram message id onto a row we already claimed. Separate from
    `claim` because the send sits between them — and a send that fails must leave
    the row in place, recorded and unpublished, rather than rolling back a claim
    another run would then re-send."""
    if message_id is None:
        return
    execute_write(
        "UPDATE picks_forward_test SET telegram_message_id = %s WHERE id = %s",
        (message_id, pick_id),
    )


GRADE_B_EDGE = 0.03   # == MIN_EDGE: the price at which a leg becomes a pick
GRADE_A_EDGE = 0.05


def required_odds(p_sharp: float) -> tuple[float, float, float]:
    """(break-even, grade-B price, grade-A price) for a given sharp probability.

    edge = p_sharp * odds - 1, so the price needed for a target edge t is
    (1 + t) / p_sharp. Exact arithmetic off the sharp line — it states what the
    pick is worth taking at, and predicts nothing about whether it wins.
    """
    return 1.0 / p_sharp, (1.0 + GRADE_B_EDGE) / p_sharp, (1.0 + GRADE_A_EDGE) / p_sharp


def write_board(pool: list[dict]) -> int:
    """Replace the live candidate board that /picks renders as a watchlist.

    PICKS-BOARD-WATCHLIST (2026-09-15). The rule publishes only legs over the
    floor, and on a flat day that is nothing at all — 0 of 31 on the day this
    was written. Rather than lower the floor, show the board with the price each
    leg would need. A reader who finds 2.18 has a pick; one who only finds 2.15
    knows to leave it.

    ⚠️ This writes `picks_board`, NEVER `picks_forward_test`. A row here did not
    qualify; counting it in the pre-registered ledger would inflate n with bets
    nobody was told to take. Separate table, separate view, no `arm` column.

    WHAT IS KEPT (widened 2026-09-15, same day): every candidate leg, not just
    those at or above break-even. The first cut kept only break-even-or-better
    and yielded **6 of 34** — arbitrary, and needlessly thin. Measured on the
    day: the whole pool sits within 5% of break-even (the anchor gate already
    selects fixtures books price tightly), and the distance to the grade-B target
    runs +1.0% at the closest to +7.6% at the furthest. So "below break-even" is
    not a different kind of leg here, it is a slightly worse price on the same
    board — and the target column states exactly what it would take either way.
    The page sorts by edge and shows the closest dozen, so the far tail never
    surfaces without being filtered out in the writer.
    """
    try:
        from workers.utils.vip_guard import is_held_back
        rows = []
        for c in pool:
            be, b3, a5 = required_odds(c["p_sharp"])
            # [[#164]] VIP FIRST: a watchlist leg VIP holds (or would take at this price) is
            # kept on the board's record but hidden by picks_board_public until kickoff —
            # otherwise the "prices to watch" panel would print the paid pick.
            _hr, _ko = is_held_back(c["match_id"], c["market"], c["selection"], c["odds"],
                                    kickoff=c.get("kickoff_at"))
            rows.append((c["match_id"], c["market"], c["selection"], c["odds"],
                         c["bookmaker"], c["p_sharp"], c["edge"], be, b3, a5,
                         c["anchor_overround"], c["kickoff_at"],
                         _ko if _hr else None, _hr))
        if not rows:
            return 0
        # PICKS-BOARD-TRACKED: rows are NO LONGER deleted after kickoff. They
        # are the record now — the whole point is to settle them and split the
        # result by whether the target was ever met.
        for r in rows:
            execute_write(
                """INSERT INTO picks_board
                     (match_id, market, selection, odds, bookmaker, p_sharp,
                      edge, odds_breakeven, odds_grade_b, odds_grade_a,
                      anchor_overround, kickoff_at, held_back_until, held_back_reason, updated_at,
                      first_seen_at, best_odds_seen, best_odds_book, best_odds_at,
                      target_b_met_at, target_a_met_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW(),
                           NOW(), %s, %s, NOW(),
                           CASE WHEN %s >= %s THEN NOW() END,
                           CASE WHEN %s >= %s THEN NOW() END)
                   ON CONFLICT (match_id, market, selection) DO UPDATE SET
                     odds = EXCLUDED.odds, bookmaker = EXCLUDED.bookmaker,
                     p_sharp = EXCLUDED.p_sharp, edge = EXCLUDED.edge,
                     -- TARGET-FROZEN-AT-FIRST-SIGHTING (2026-09-15). These are
                     -- deliberately NOT updated. `odds_grade_b = (1+0.03)/p_sharp`
                     -- was recomputed every run while `target_b_met_at` was
                     -- tested against the PREVIOUS run's value, so a target
                     -- could be "met" with the quote never moving — the bar
                     -- dropped because p_sharp rose. Measured on a replay of
                     -- this exact upsert: 19 pct of "met" events were the
                     -- goalpost moving toward the pick on information that
                     -- arrived AFTER
                     -- publication, which a reader could never have acted on.
                     -- A published target is a promise about a number; it has to
                     -- be the number we published.
                     anchor_overround = EXCLUDED.anchor_overround,
                     -- [[#164]] once held back, held until kickoff (VIP never gives it up)
                     held_back_until = COALESCE(picks_board.held_back_until, EXCLUDED.held_back_until),
                     held_back_reason = COALESCE(picks_board.held_back_reason, EXCLUDED.held_back_reason),
                     updated_at = NOW(),
                     -- HIGH-WATER MARK, never a running value. The question the
                     -- record has to answer is "was the target ever reachable",
                     -- so a price that rose and fell must leave a trace.
                     best_odds_seen = GREATEST(
                         COALESCE(picks_board.best_odds_seen, 0), EXCLUDED.odds),
                     best_odds_book = CASE
                         WHEN EXCLUDED.odds > COALESCE(picks_board.best_odds_seen, 0)
                         THEN EXCLUDED.bookmaker ELSE picks_board.best_odds_book END,
                     best_odds_at = CASE
                         WHEN EXCLUDED.odds > COALESCE(picks_board.best_odds_seen, 0)
                         THEN NOW() ELSE picks_board.best_odds_at END,
                     -- COALESCE keeps the FIRST crossing: a target met at 14:05
                     -- stays met even if the price falls back at 14:35.
                     target_b_met_at = COALESCE(
                         picks_board.target_b_met_at,
                         CASE WHEN EXCLUDED.odds >= picks_board.odds_grade_b
                              THEN NOW() END),
                     target_a_met_at = COALESCE(
                         picks_board.target_a_met_at,
                         CASE WHEN EXCLUDED.odds >= picks_board.odds_grade_a
                              THEN NOW() END)""",
                # best_odds_seen, best_odds_book, then the two target CASEs.
                # r = (match, market, selection, odds, book, p_sharp, edge,
                #      breakeven, grade_b, grade_a, overround, kickoff, held_until, held_reason)
                r + (r[3], r[4], r[3], r[8], r[3], r[9]))
    except Exception as e:
        log.warning("write_board failed (non-fatal — publishing is unaffected): %s", e)
        return 0
    return len(rows)


def _break_even(c: dict) -> float:
    """1/p_sharp — the price at which this stops being value.

    Falls back to deriving p_sharp from the edge when the key is absent.
    `edge = p_sharp * odds - 1` by construction, so `p_sharp = (1+edge)/odds`
    is exact, not an approximation.

    Defensive on purpose: this is the ONLY scheduled job that writes to a public
    surface, and a KeyError here does not degrade the message — it kills the
    publish. Every real candidate carries p_sharp (set at the point it is built,
    and again for donor rows), so this branch should never run; it exists so a
    future caller that assembles a candidate by hand cannot silently take the
    public feed down.
    """
    p = c.get("p_sharp")
    if not p:
        edge, odds = c.get("edge"), c.get("odds")
        if edge is None or not odds:
            raise KeyError("p_sharp")          # genuinely unpriceable — fail loudly
        p = (1.0 + float(edge)) / float(odds)
    return 1.0 / float(p)


def _anchor_line(c: dict) -> str:
    """One line naming the fair-value basis, in the reader's terms.

    Deliberately NOT a percentage. TELEGRAM-EDGE-LABEL (2026-09-22) removed the
    published edge % because a percentage reads as a promise about returns and
    this test's n cannot support one; re-adding it beside the break-even price
    would undo that decision by the back door. What a reader gains here is the
    strength of the evidence — one book's opinion versus eleven books agreeing —
    which is exactly what separates the two arms."""
    book = c.get("anchor_bookmaker") or "Pinnacle"
    if book.startswith("consensus:"):
        n = book.split(":", 1)[1]
        return f"⚖️ Fair price from <b>{n} bookmakers</b> agreeing (margin removed)"
    return "⚖️ Fair price from the <b>sharp line</b> (margin removed)"


def render(c: dict) -> str:
    pick = PICK_LABEL.get((c["market"], c["selection"]),
                          f"{c['market']} {c['selection']}")
    ko = c["kickoff_at"].astimezone(timezone.utc)
    return (
        f"⚽ <b>{c['home_team']} vs {c['away_team']}</b>\n"
        f"{c['league']} · {ko:%a %d %b · %H:%M UTC}\n\n"
        f"✅ Pick: <b>{pick}</b> @ <b>{c['odds']:.2f}</b> "
        # DISPLAY NAME, not the key (2026-09-22). `Unibet-Site` is an internal
        # distinction from the dead AF `Unibet` feed and the retired
        # `Unibet-Kambi`; to a reader it is just noise. The ledger keeps the key.
        f"at <b>{display_book(c['bookmaker'])}</b>\n"
        # TELEGRAM-EDGE-LABEL (2026-09-22, owner-approved). Publish the PRICE,
        # not a percentage.
        #
        # `edge` here is a genuine expected return (p_sharp * odds - 1), so "%"
        # was not the unit error it is on the model arm. It is still the wrong
        # thing to publish: a percentage reads as a promise about returns, and
        # the forward test's own n cannot support one. A break-even price makes
        # no such claim, says the same thing in the unit a bettor acts on, and
        # degrades gracefully — 1x2 shortens ~11% by kickoff 86% of the time,
        # so a reader arriving late can check the price themselves instead of
        # trusting a number computed at a moment that has passed.
        #
        # Derived from the SHARP line (1/p_sharp), deliberately: it is
        # independent of our own model's calibration, which is currently
        # +12.3pp overconfident (n=154 post-recalibration) and therefore cannot
        # back a published fair price. See the model arm in coolbet_signaler,
        # which for that reason publishes no replacement number at all.
        f"📊 Break-even price: <b>{_break_even(c):.2f}</b> — "
        f"value while the price stays above it\n"
        # ANCHOR BASIS ON THE MESSAGE (2026-09-22, [[#068]], owner: "we just need
        # to show the edge or something in number, so users can see and decide
        # themselves"). The number a reader can act on is the break-even price
        # above; this line says what it was derived FROM, which is the part that
        # differs between the two published arms. Without it the channel mixes
        # two rules with no way for a reader — or us — to tell them apart.
        f"{_anchor_line(c)}\n"
        # [[#094]] B/C grade on consensus picks — a label so readers can choose.
        f"{_grade_line(c)}\n"
        f"<a href='https://oddsintel.app/picks'>Live picks</a>"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually post to the public channel and record")
    ap.add_argument("--no-header", action="store_true",
                    help="skip the method-change header (use after day 1)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    picks, pool = load_candidates()
    # RULE-V4: re-select against the DB-backed daily allowance. load_candidates()
    # caps per call (correct for offline analysis); the live path must cap per DAY.
    room = daily_room()
    picks = select(pool, room)
    if args.send:
        print(f"board refreshed: {write_board(pool)} legs at/above break-even")
    if not picks:
        print(f"no qualifying picks — nothing to publish (valid outcome; "
              f"{room} of {DAILY_RUNAWAY_LIMIT} before the runaway breaker)")
        return 0

    print(f"{len(picks)} picks under {RULE_VERSION}\n")
    for c in picks:
        print(f"  +{c['edge']*100:5.2f}%  {c['kickoff_at']:%H:%M}  "
              f"{c['home_team']} v {c['away_team']}  "
              f"{c['market']}/{c['selection']} @{c['odds']:.2f} {c['bookmaker']}  "
              f"gap={c['alignment_gap_minutes']:.0f}m "
              f"ratio={c['price_ratio']*100:+.0f}%")

    if not args.send:
        print("\n(dry run — pass --send to publish)")
        return 0

    # The operator's /pausepicks applies to the manual send too (#139 review, 2026-09-24);
    # the scheduled job already honours it. Refuse rather than record-without-send: a
    # manual --send is an explicit "post these now", so a pause should stop it outright.
    from workers.automation.coolbet_state import is_publishing_paused
    _paused, _why = is_publishing_paused()
    if _paused:
        print(f"\npublishing is paused ({_why or 'no reason given'}) — /resumepicks first. Nothing sent.")
        return 1

    if not args.no_header:
        send_telegram_public(HEADER.format(n=len(picks)))

    from workers.utils.bot_status import load_sent_public_bots
    sent_bots = load_sent_public_bots()   # [[#155]] the bot's status decides the send
    sent = 0
    for c in picks:
        pick_id = claim(c, "live")
        if pick_id is None:
            log.info("already published, not re-sending: %s v %s",
                     c["home_team"], c["away_team"])
            continue
        if not arm_bot_sends(c, "live", sent_bots):
            log.info("status does not send (#155), recorded not sent: %s v %s",
                     c["home_team"], c["away_team"])
            continue
        if c.get("held_back_reason"):      # [[#164]] VIP FIRST: recorded, never sent
            log.info("held back (%s), recorded not sent: %s v %s", c["held_back_reason"],
                     c["home_team"], c["away_team"])
            continue
        mid = send_telegram_public(render(c))
        if mid is None:
            log.warning("send FAILED: %s v %s — row kept, unpublished",
                        c["home_team"], c["away_team"])
        else:
            sent += 1
            attach_message_id(pick_id, mid)

    # Negative control — recorded, never published. Same cadence AND same daily
    # room as the live arm: an uncapped control accumulates the union of every
    # run's draw while live accumulates one capped set, and the two arms stop
    # being the same rule.
    for c in junk_anchor_arm(pool)[:max(0, room)]:
        claim(c, "junk_anchor")

    print(f"\npublished {sent}/{len(picks)} to the channel, all recorded")
    return 0 if sent == len(picks) else 1


if __name__ == "__main__":
    sys.exit(main())
