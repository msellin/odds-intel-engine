"""
Coolbet shared helpers — edge / odds floors, event search, fixture pairing.

The name is historical. Until 2026-09-25 this module was also a complete API
placer ("Path B": load the day's simulated_bets, find each on Coolbet, POST
/s/bets/bets, write real_bets). That path was retired and DELETED in #162 W4.6
— see the note at the bottom of this file. It places nothing now.

What stays is imported by the live code:
  * the per-market floors — `_MIN_EDGE_BY_MARKET`, `_min_edge_for`,
    `_min_odds_for`, `min_edge_for_pick`, `clears_edge_floor`, `model_edge`,
    `_canon_market` (placement_floor, pick_generator, best_price_router,
    signaler, triggers, gen_frontend_floors);
  * Coolbet event reads — `search_coolbet_event`, `fetch_events_for_league`,
    `fetch_coolbet_events`, `fetch_sidebets`, `fetch_main_markets`
    (coolbet_explorer and the odds collectors);
  * fixture pairing — `fuzzy_match_event`, `unique_pairs`, `_parse_iso_start`,
    `_ascii`, `_team_aliases` (every book feed + the UI placer).

Required .env (reads only):
    COOLBET_IMPERVA_COOKIES   (see coolbet_session.py)
    COOLBET_MIN_EDGE          — global edge fallback (default: 0.03)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rapidfuzz import fuzz
from workers.automation.coolbet_session import CoolbetSession

log = logging.getLogger(__name__)

_SEARCH_URL    = "https://www.coolbet.com/s/sbgate/sports/search/v2"
_CATEGORY_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_SIDEBETS_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-market/sidebets"
_ODDS_URL      = "https://www.coolbet.com/s/sb-odds/odds/current/fo"

_FOOTBALL_CATEGORY_ID = 62
_MIN_EDGE        = float(os.getenv("COOLBET_MIN_EDGE",      "0.03"))
_FUZZY_THRESHOLD = 70

# PER-MARKET-EDGE-V2 (2026-06-06): edge predictiveness varies markedly by
# market — `scripts/edge_threshold_backtest.py` over 3,086 settled
# simulated_bets shows 1X2 needs ≥10% edge to be profitable, o/u is fine at
# 3%, AH is flat, BTTS only profitable ≥10%, DC loses at every threshold.
# The SQL prefilter still uses `_MIN_EDGE` (global 3% floor); this Python
# gate then drops anything below the per-market threshold. Keeps the SQL
# simple while letting us tune one knob per market.
#
# Values are decimal fractions (0.10 = 10%). `None` means "retired — never
# place real money on this market". `_min_edge_for(market)` returns
# `math.inf` for None so the gate trivially rejects.
_MIN_EDGE_BY_MARKET: dict[str, float | None] = {
    # BOT-CONFIG-GOLDEN-MIDDLE-2026-09-08: 0.13 for the POOLED 1x2 floor (all
    # selections), set by scripts/edge_floor_backtest.py (walk-forward, 3 folds).
    # 0.13 is the only POOLED 1x2 floor robust in every fold/basis; pooled 0.10 was
    # NOT (negative fold). This is the paper-daemon + trigger-window floor.
    # ⚠️ FAVLONG-CUTS-2026-09-09: the pooled result HID that home-FAVOURITES are a
    # fold-robust loser at every floor; split by SELECTION TYPE, home-underdogs are
    # the one fold-robust engine and win at 0.10 (odds>=2.80). So the REAL-MONEY 1x2
    # bot does NOT use this pooled 0.13 — it uses _MODEL_1X2_HOME_FLOOR=0.10 via placement_floor.pick_clears (was BOT_THRESHOLDS, #162 W4.3)
    # (home-underdogs only) in scripts/place_coolbet_ui.py. This pooled value stays
    # 0.13. See docs/BETTING_GATE_DECISIONS.md "1x2 by SELECTION TYPE".
    "1x2":            0.13,
    # EDGE-FLOORS-OTHER-MARKETS-2026-09-08: raised 0.03 -> 0.08 via
    # edge_floor_backtest.py. 0.08 is robust in EVERY walk-forward fold across
    # all bases (executable all-bots +14.3% n=831, active +20.5% n=202, idealized
    # 182k +107.6%) and maximises executable profit (€1185). Overturns the old
    # "higher O/U gates don't improve expectation" note — they do.
    "o/u":            0.08,
    # ⚠ EDGE-FLOORS-OTHER-MARKETS-2026-09-08: AH has NO fold-robust floor — the
    # recent fold is negative at EVERY level (the BTTS-retirement pattern). Left
    # at 0.05 rather than tuned to a non-robust result (the 1x2-15% overfit
    # lesson); flagged for a viability review (AH-VIABILITY-REVIEW).
    "asian_handicap": 0.05,
    # BTTS-RETIRED-2026-09-03: shadow BTTS is n=427, ROI -12.76% at prices that
    # were live at pick time, t=-2.87 (p<0.01). Recalibration did not rescue
    # it — ENSEMBLE-RECALIBRATION took BTTS ECE from 0.047 to 0.009, the best
    # of any market, and re-scoring every settled pick under the new
    # coefficients cut volume to 33% while making survivors WORSE (-20.73%).
    # Honest probabilities revealed the absence of edge rather than creating
    # one. It also cannot be validated: 0 of 3,076,350 BTTS snapshots carry
    # Pinnacle, so clv_pinnacle is permanently NULL for this market.
    "btts":           None,   # retired — see BTTS-MARKET-VIABILITY
    "double_chance":  None,   # retired — losing at every threshold
    "combo":          0.10,   # combos use ensemble edge; gate like 1x2
    "draw_no_bet":    0.05,
}


def _canon_market(market: str | None) -> str | None:
    """Normalise a pick's market vocabulary to the canonical floor keys used by
    `_MIN_EDGE_BY_MARKET` / `_MIN_ODDS_BY_MARKET`.

    PLACER-OU-VOCAB-FLOOR-2026-09-09: the O/U model bot writes picks as
    `over_under_25` / `over_under_35` (the line-shop vocab), but the floor maps
    key O/U as `o/u`. Without this, `_min_odds_for('over_under_25')` fell through
    to the 2.80 default instead of the validated 1.80, so the placer silently
    rejected the entire 1.80-2.80 O/U band (median O/U price ~2.15) — exactly the
    REALMONEY-ODDS-BAND-MISMATCH the 2D-GATE fix was meant to end, re-emerging
    through the vocab gap. Caught live 2026-09-09 (a clean +8% O/U @ 2.42 skipped
    as 'below floor 2.80').

    CANONICAL-MARKET-VOCAB-2026-09-09: the mapping now lives in the shared
    `workers.canonical_market.market_family` (COOLBET-PICK-TABLE-AUDIT Stage 1);
    this delegates to it so the placer and every other caller use one source."""
    from workers.canonical_market import market_family
    return market_family(market)


def _min_edge_for(market: str | None) -> float:
    """Per-market edge floor (decimal fraction). Returns `math.inf` for
    retired markets so any comparison `edge >= floor` is False. Unknown
    markets fall back to `_MIN_EDGE` (global default). Canonicalises the
    market vocab first (see `_canon_market`)."""
    if not market:
        return _MIN_EDGE
    val = _MIN_EDGE_BY_MARKET.get(_canon_market(market), _MIN_EDGE)
    if val is None:
        return float("inf")
    return val


# ── 2D-GATE-PER-MARKET-ODDS-FLOOR-2026-09-08 ─────────────────────────────────
# The odds floor used to be a single global `COOLBET_MIN_ODDS` (2.80) applied
# to every market. That number was derived from a 1x2-only CLV finding
# (REALMONEY-ODDS-BAND-MISMATCH: 1x2 CLV goes negative above ~2.8 ungated, and
# the profitable 1x2 gate lives at odds>=2.8). Blanket-applying it to O/U was
# an error: O/U prices cluster at ~1.8-2.2 (median 2.15), so a 2.80 floor
# rejected 84% of O/U bets and left ~€1,100 of profit on the table
# (executable: edge>=8% & odds>=2.8 -> €544, vs edge>=8% & odds>=1.8 -> €1663,
# both fold-robust). The joint (edge x odds) sweep in edge_floor_backtest.py
# (--joint) shows each market has its OWN profit ridge:
#   1x2  peak robust cell = edge>=0.13 & odds>=3.0 (€1082); 2.8 is within noise
#        (€1063) so kept at 2.80 — the validated 2D gate.
#   o/u  peak robust cell = edge>=0.08 & odds>=1.8 (€1663); floor lowered to 1.8.
#        Independently confirmed by CLV: O/U beats the close in every band at
#        1.8+ (CLV +1.7% to +5.8%) and only turns negative BELOW 1.8
#        (CLV +0.8%, ROI -4.7%) — so 1.8 is the safety line for O/U, not 2.8.
#   asian_handicap has no fold-robust cell at any odds floor (marginal at best,
#        edge>=0.17 only) — a 2.8 floor merely killed it (-€64). Left ungated
#        on odds (1.0) pending AH-VIABILITY-REVIEW; the edge floor governs.
#   draw_no_bet n=7 settled — no evidence for any odds floor; left ungated.
# Unknown markets keep the conservative global 2.80. `COOLBET_MIN_ODDS` still
# sets the 1x2/default value so the env override keeps working.
_MIN_ODDS_BY_MARKET: dict[str, float] = {
    "1x2":            2.80,
    "o/u":            1.80,
    "asian_handicap": 1.00,
    "draw_no_bet":    1.00,
}


def _min_odds_for(market: str | None) -> float:
    """Per-market minimum-odds (price) floor. Falls back to the global
    `COOLBET_MIN_ODDS` (default 2.80) for 1x2, unknown, and unset markets so
    the existing env override still tunes the conservative default. Mirrors
    `_min_edge_for` — see `_MIN_ODDS_BY_MARKET` for the per-market evidence."""
    default = float(os.getenv("COOLBET_MIN_ODDS", "2.80"))
    if not market:
        return default
    return _MIN_ODDS_BY_MARKET.get(_canon_market(market), default)


# ── SIGNAL-PLACER-1X2-ALIGN-2026-09-10 / EDGE-FLOOR-ONE-UTILITY-2026-09-10 ───
# `min_edge_for_pick` is THE single, selection-aware edge-floor decision for one
# concrete pick. EVERY pick-level gate (the Telegram signaler, the Mac-daemon
# candidate loader, the live-price re-eval) MUST call it, so the rule lives in
# ONE place and changing it here changes it everywhere. Do NOT re-derive a
# per-pick floor from `_min_edge_for` (market-only) at a call site again — that
# is how the two signalers drifted (coolbet_signaler stayed blind at 13% while
# the placer moved to 10%, so home-underdogs in the 10-13% band were placed but
# never signaled — Stevenage v Luton, Home @3.48, +12%).
#
# The policy it encodes (docs/BETTING_GATE_DECISIONS.md "1x2 by SELECTION TYPE"):
#   * 1x2 home-underdog (selection=home AND odds>=2.80) -> 10% — the ONE
#     fold-robust 1x2 engine, robust down to 10%; shares the mirror's env var.
#   * everything else -> the pooled per-market `_min_edge_for`. The pooled 1x2
#     floor stays 13% ON PURPOSE: the same backtest that found 10% for
#     home-underdogs found pooled 10% is a fold-robust LOSER (home-favs lose at
#     every floor, aways aren't robust), and the paper trigger windows read the
#     pooled value. Home-favs therefore stay on 13% and remain excluded from
#     real money by the 2.80 odds floor.
_MODEL_1X2_HOME_FLOOR = float(os.getenv("COOLBET_MODEL_1X2_EDGE_FLOOR", "0.10"))


def min_edge_for_pick(market: str | None, selection: str | None,
                      odds: float | None) -> float:
    """THE selection-aware edge floor for one pick — the single source of truth
    every pick-level gate (signaler, daemon loader, live re-eval) must use so
    the signal set and the placement set apply the same rule. 1x2
    home-underdogs (selection=home AND odds>=2.80) get the placer's 10% floor;
    everything else falls back to the pooled per-market `_min_edge_for`."""
    if (_canon_market(market) == "1x2"
            and (selection or "").strip().lower() == "home"
            and odds is not None and float(odds) >= _min_odds_for("1x2")):
        return _MODEL_1X2_HOME_FLOOR
    return _min_edge_for(market)

def model_edge(odds, calibrated_prob, stored_edge=None):
    """THE model edge for one pick: `calibrated_prob - 1/odds`. Every gate that
    loads a model-anchored pick out of `simulated_bets` MUST normalise the row
    through this before comparing anything to a floor.

    EDGE-IS-DERIVED-NOT-STORED (2026-09-22, queue #031). Edge is a DERIVATION,
    not a third independent fact, and `simulated_bets.edge_percent` was declared
    `numeric(5,2)` — two decimals, i.e. a granularity of one whole percentage
    POINT. Postgres rounded every write to it silently, so 80 pct of stored
    edges disagreed with `cal_prob - 1/odds`, always by up to +0.005 and always
    in the flattering direction (round-half-up cannot push a clearing pick under
    its floor). Because every per-market floor is itself specified to exactly two
    decimals (0.13 / 0.10 / 0.08 / 0.05), `stored >= floor` was true for the
    whole band `[floor - 0.005, floor)`: measured 114 picks all time, 26 in 90d,
    14 in 30d cleared a floor their real edge did not. The readers that acted on
    that were the Telegram signaler (the operator's manual-placement prompt AND
    the public customer channel) and the pre-kickoff catch-net.

    Migration 367 widened the column, which fixes FUTURE writes. This function is
    what makes the gates right for the ~3,700 rows already written at two
    decimals — and, more importantly, what stops the class of bug returning: a
    gate that derives cannot be fooled by a stored number drifting from the price
    beside it, whatever caused the drift (precision here, a stale price in
    MIRROR-PRICES-AT-ITS-OWN-BOOKS, a foreign book's quote before that).

    NOT for sharp-anchored picks. `shadow_bets.edge_percent` on the sharp bots is
    a MULTIPLICATIVE return (`p_sharp * odds - 1`), a different quantity with a
    different floor — see SYSTEM_MAP section 1 and migration 345. Those rows must
    never be routed through here.

    Falls back to `stored_edge` when the probability or the price is missing
    (in-play rows carry no `calibrated_prob`), so a caller can normalise
    unconditionally.
    """
    if calibrated_prob is None or odds is None:
        return stored_edge
    try:
        o = float(odds)
        p = float(calibrated_prob)
    except (TypeError, ValueError):
        return stored_edge
    if o <= 1.0:
        return stored_edge
    return p - 1.0 / o


def clears_edge_floor(market, selection, odds, edge) -> bool:
    """THE edge-floor predicate. Every gate that asks "does this pick have
    enough edge?" MUST call this — do not re-implement the comparison.

    EDGE-FLOOR-ONE-PREDICATE (2026-09-11). `min_edge_for_pick` already made the
    FLOOR a single source of truth, but each caller still hand-rolled the
    comparison, and that is exactly where the next bug lived: `edge_percent`
    arrives from Postgres as a Decimal while the floor is a Python float, and
    the float literal 0.08 is really 0.08000000000000000166… — fractionally
    LARGER than exact decimal 0.08. So `Decimal("0.0800") < 0.08` is True and a
    pick sitting EXACTLY on the floor was silently dropped.

    The placer happened to cast to float and staked those picks; the signaler
    did not and never told the operator. Same floor, same utility, two
    different answers — the Stevenage failure again, from a TYPE this time.
    Sharing the floor was not enough; the comparison has to be shared too.

    Returns True when the pick MEETS or beats its floor. Meeting the floor
    passes it — `>=`, never `>`.
    """
    if edge is None:
        return False
    try:
        e = float(edge)
    except (TypeError, ValueError):
        return False
    return e >= min_edge_for_pick(market, selection, odds)


# ── Coolbet event fetcher ─────────────────────────────────────────────────────

def fetch_coolbet_events(session: CoolbetSession) -> list[dict]:
    """
    Fetch all pre-match football events from fo-category.
    Returns flat list of {id, home, away, start, bet_offers}.

    bet_offers: [{id, criterion_label, outcomes: [{id, label, odds_decimal}]}]

    NB: legacy single-call sweep at categoryId=62 (football root) — Coolbet
    deprecated this in early 2026 and now returns 404. Kept as a fallback
    for old call sites; new code should use fetch_coolbet_leagues +
    fetch_events_for_league instead.
    """
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": _FOOTBALL_CATEGORY_ID,
        "lang": "en",
        "layout": "EUROPEAN",
    })
    if resp.status_code != 200:
        raise RuntimeError(f"fo-category {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    events: list[dict] = []
    _walk_group(data.get("group") or data, events)
    log.info("Fetched %d Coolbet football events from fo-category", len(events))
    return events


# ── League-based ingest (COOLBET-LEAGUE-INGEST 2026-05-20) ────────────────────
#
# Single category=62 sweep is dead; per-match search has false negatives.
# Two-endpoint architecture replaces both:
#
#   1. POST /s/sports/category/order/explicit/category-page-leagues
#        body: {sportCategoryId: 62, country: "EE", locale: "et"}
#        returns: list of {id, name, fullSlug, ...} — ~136 leagues
#
#   2. GET /s/sbgate/sports/fo-category/?categoryId=<league_id>
#        returns: [{matches: [{id, name, home_team_name, away_team_name, start}]}]
#        — all current/upcoming events in that league, no fuzzy matching needed

_LEAGUES_URL = "https://www.coolbet.com/s/sports/category/order/explicit/category-page-leagues"
_LEAGUES_CACHE_PATH = Path(__file__).parent / "coolbet_leagues_cache.json"


def _load_leagues_cache() -> list[dict]:
    """Fallback: read the static leagues list cached in the repo."""
    try:
        import json as _json
        with open(_LEAGUES_CACHE_PATH, "r") as f:
            data = _json.load(f)
        return [
            {"id": int(e["id"]),
             "name": e.get("name") or "",
             "fullSlug": e.get("fullSlug") or "",
             "sportCategoryId": e.get("sportCategoryId")}
            for e in data if e.get("id")
        ]
    except Exception as e:
        log.warning("Could not load leagues cache from %s: %s", _LEAGUES_CACHE_PATH, e)
        return []


def fetch_coolbet_leagues(session: CoolbetSession) -> list[dict]:
    """Return the full list of Coolbet football leagues exposed to the EE locale.
    Each entry: {id, name, fullSlug, sportCategoryId}. ~132 leagues.

    Tries the live POST endpoint first; falls back to the static cache at
    `coolbet_leagues_cache.json` if Imperva 403's (which it does for our
    Python session as of 2026-05-20 — endpoint has stricter TLS fingerprinting
    than other Coolbet API calls). Cache is curated from the user's browser
    curl and refreshed manually when leagues change (rarely)."""
    resp = session.post(_LEAGUES_URL, json={
        "sportCategoryId": _FOOTBALL_CATEGORY_ID,
        "country":         "EE",
        "locale":          "et",
    })
    if resp.status_code == 200:
        payload = resp.json()
        if isinstance(payload, list) and payload:
            log.info("fetch_coolbet_leagues — live endpoint returned %d leagues", len(payload))
            return [
                {"id": int(e["id"]),
                 "name": e.get("name") or "",
                 "fullSlug": e.get("fullSlug") or "",
                 "sportCategoryId": e.get("sportCategoryId")}
                for e in payload if e.get("id")
            ]
    # Live endpoint failed (403 Imperva, network, schema change) — fall back.
    #
    # COOLBET-LEAGUE-CACHE-SILENT-STALE (2026-09-06): this was logged at INFO,
    # so a fallback that has apparently been permanent since 2026-05-20 never
    # surfaced. The consequence is not cosmetic — the cached list holds 132
    # leagues and none of the high-volume ones Coolbet actually carries (J1
    # League, Süper Lig, Segunda División, Serie C), so `coolbet_league_mapping`
    # could never grow past them. That is why 79% of our fixtures fall through
    # to the cross-league `search_coolbet_event` path, which is the path that
    # priced Liga MX's Atlas vs Querétaro onto Liga Premier Serie A's Acatlan vs
    # Guerreros. WARNING, with the cache age, so the staleness is visible.
    cached = _load_leagues_cache()
    try:
        age_days = (time.time() - _LEAGUES_CACHE_PATH.stat().st_mtime) / 86400.0
        age = f"{age_days:.0f}d old"
    except OSError:
        age = "age unknown"
    log.warning(
        "fetch_coolbet_leagues — live endpoint returned %d, FALLING BACK TO "
        "STALE CACHE (%d leagues, %s). League-scoped ingest is limited to that "
        "list; everything else degrades to cross-league search.",
        resp.status_code, len(cached), age,
    )
    return cached


def fetch_events_for_league(
    session: CoolbetSession, league_id: int, league_slug: str | None = None,
    *, raise_on_error: bool = False,
) -> list[dict]:
    """Return all matches in one Coolbet league.

    Each match dict has at minimum: {id, name, home_team_name, away_team_name,
    match_start, status}. Status='OPEN' = pre-match or live; others (closed,
    etc.) are skipped by caller.

    LEAGUE-EVENTS-PARAMS-FIX (2026-05-20): replicates the browser's exact
    fo-category request shape — `language=et`, `isMobile=0`, `limit=6`, and
    a league-specific referer header. Without these Imperva 403's; with them
    the endpoint works. `league_slug` is optional but improves the referer.
    """
    extra_headers: dict[str, str] = {}
    if league_slug:
        extra_headers["referer"] = f"https://www.coolbet.com/et/sport/{league_slug}"
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": league_id,
        "country":    "EE",
        "isMobile":   0,
        "language":   "et",  # Estonian locale to match browser fingerprint
        "layout":     "EUROPEAN",
        "limit":      6,
    }, headers=extra_headers or None)
    if resp.status_code != 200:
        log.debug("fo-category(league=%d) returned %d", league_id, resp.status_code)
        # BOARD-MEMO-POISON (#091, 2026-09-23): the board sweep's near-term memo
        # read this [] as "category has nothing" and skipped it for hours — during
        # the #108 block every category was learned empty (board matched 19 where
        # run_bulk matched 81). The board sweep asks for a raise instead.
        if raise_on_error:
            raise RuntimeError(f"fo-category({league_id}) HTTP {resp.status_code}")
        return []
    data = resp.json()
    matches: list[dict] = []
    # FO-CATEGORY-ENVELOPE-FIX (2026-09-08): the endpoint now wraps its category
    # list in an envelope — {"categories":[{...,"matches":[...]}], "filterUsed":…,
    # "availableFilters":…}. The old code treated the whole dict as ONE category
    # and read `.matches` off the TOP level, which does not exist there, so it
    # returned 0 events for EVERY league. That silently disabled the entire
    # league-scoped sweep (run_league_sweep) and forced the fallback onto
    # cross-league search/v2 — the source of the wrong-fixture prices this rework
    # exists to kill. Descend into `categories`; still accept a bare list or a
    # single category object for older/other shapes.
    if isinstance(data, dict):
        cats = data.get("categories")
        if cats is None:
            cats = [data]
    else:
        cats = data
    for cat in cats:
        cat_iso = cat.get("region_icon") if isinstance(cat, dict) else None
        for m in (cat.get("matches") or []):
            if not m.get("id"):
                continue
            matches.append({
                "id":         int(m["id"]),
                "home":       (m.get("home_team_name") or "").strip(),
                "away":       (m.get("away_team_name") or "").strip(),
                "start":      m.get("match_start") or m.get("start"),
                "status":     m.get("status"),
                "name":       m.get("name"),
                # COOLBET-INGEST-REWORK: ISO country code for record-linkage
                # blocking (see coolbet_matching). Prefer the match-level value,
                # fall back to the category's.
                "iso":        m.get("region_icon") or cat_iso,
            })
    return matches


def _walk_group(group: dict, out: list) -> None:
    for ev in group.get("events") or []:
        parsed = _parse_event(ev)
        if parsed:
            out.append(parsed)
    for sub in group.get("groups") or []:
        _walk_group(sub, out)


def _parse_event(ev: dict) -> dict | None:
    ev_id = ev.get("id")
    if not ev_id:
        return None

    name = ev.get("name") or ""
    participants = ev.get("participants") or []
    if len(participants) >= 2:
        home = participants[0].get("name", "")
        away = participants[1].get("name", "")
    elif " - " in name:
        home, away = name.split(" - ", 1)
    else:
        return None

    bet_offers = []
    for bo in ev.get("betOffers") or []:
        if bo.get("suspended"):
            continue
        criterion = bo.get("criterion") or {}
        label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()
        outcomes = []
        for oc in bo.get("outcomes") or []:
            raw = oc.get("odds") or 0
            dec = raw / 1000.0 if isinstance(raw, int) and raw > 100 else float(raw or 0)
            outcomes.append({
                "id":           oc.get("id"),
                "label":        (oc.get("englishLabel") or oc.get("label") or "").strip(),
                "odds_decimal": dec,
            })
        if outcomes:
            bet_offers.append({
                "id":              bo.get("id"),
                "criterion_label": label,
                "outcomes":        outcomes,
            })

    return {
        "id":         ev_id,
        "home":       home.strip(),
        "away":       away.strip(),
        # COOLBET-DATE-GUARD-DEAD (2026-08-31): search/v2 calls this field
        # `match_start`, not `start` — the same name fo-category uses. Reading
        # only `start` returned None for EVERY search result, so
        # COOLBET-FUZZY-DATE-GUARD silently no-opped: 217,518 log lines, every
        # one reporting "0 candidates rejected on date", since it was written.
        # Atletico Grau v FBC Melgar was postponed 31 Aug -> 1 Sept and Coolbet
        # moved with it; we kept API-Football's stale date, matched the moved
        # event at score 100 anyway, and stored 82 price rows against the wrong
        # day — which is how bot_coolbet_value_v1 came to pick a draw @ 3.14 on
        # a fixture that will not be played that night.
        "start":      ev.get("match_start") or ev.get("start"),
        "bet_offers": bet_offers,
    }



def _event_side_names(ev: dict | None) -> tuple[str, str]:
    """Best-effort (home, away) display names for a Coolbet candidate.

    COOLBET-EVENT-SHAPE-LOG (2026-09-05): two candidate shapes reach the
    matcher. The parsed shape from `_parse_event` / `fetch_events_for_league`
    carries {"home", "away"}; the RAW fo-category match dict carries
    {"home_team_name", "away_team_name", "name"} and no "home" key at all.
    The scoring loop in `fuzzy_match_event` reads names with `.get()`, so a
    raw-shape candidate scores 0 yet still becomes `best_event` (0 beats the
    -1 seed) — and the log lines then indexed `best_event["home"]` and raised
    `KeyError: 'home'`. A diagnostic was crashing the matcher it exists to
    explain, which made `fuzzy_match_event` unusable from scripts.

    Read both shapes, fall back to splitting the combined `name` on " - ",
    and return "?" for a side we cannot name — never raise.
    """
    if not isinstance(ev, dict):
        return "?", "?"
    home = (ev.get("home") or ev.get("home_team_name") or "").strip()
    away = (ev.get("away") or ev.get("away_team_name") or "").strip()
    if not (home and away):
        name = (ev.get("name") or "").strip()
        if " - " in name:
            n_home, n_away = (part.strip() for part in name.split(" - ", 1))
            home = home or n_home
            away = away or n_away
    return home or "?", away or "?"


class CoolbetSearchBlocked(Exception):
    """Coolbet /search/v2 refused the request (non-200).

    Why: a dead `cbauth` JWT or Incapsula bot challenge returns 4xx/5xx —
    previously swallowed at DEBUG, making 18 doomed searches look like 18
    genuine no-coverage misses (silent-failure trap, 2026-05-26).
    Raising forces the placer to stop after the first failure with a clear
    "refresh COOLBET_MANUAL_JWT" signal.
    """


_SEARCH_RETRY_STATUSES = {429, 500, 502, 503, 504}

# Max query variants tried per fixture — see the block in
# search_coolbet_event() for the measured quality-vs-depth table.
_SEARCH_LADDER_MAX = 5


def _do_search(session: CoolbetSession, query: str) -> list[dict]:
    """Single search call. Returns parsed event candidates (possibly empty).

    SEARCH-RETRY-TRANSIENT: retry once with a short backoff on transient 5xx /
    429 from Coolbet — Imperva occasionally hiccups and a clean second attempt
    succeeds. Persistent non-200 (4xx other than 429, or 5xx that survives the
    retry) still raises CoolbetSearchBlocked so a hard block bails the batch
    rather than masquerading as no-event misses.
    """
    last_status = None
    last_body = ""
    for attempt in (1, 2):
        resp = session.get(_SEARCH_URL, params={
            "search":   query,
            "country":  "EE",
            "language": "en",
            "layout":   "EUROPEAN",
        })
        if resp.status_code == 200:
            data = resp.json()
            raw_events = (
                data if isinstance(data, list)
                else data.get("events") or data.get("results") or []
            )
            # COOLBET-SEARCH-SPORT-FILTER (2026-06-06): /search/v2 returns
            # events across every sport (basketball, esports, rugby, boxing…).
            # `fuzz.partial_ratio` is generous enough that any candidate whose
            # team name contains a substring of our query scores 100 — e.g.
            # football "Shanghai Port II vs Shanghai Second" matched the
            # basketball "Shanghai - Liaoning" (sport_category_id=65035) at
            # score 100 and the placer happily fetched its Match Total Points
            # markets, then dropped every market as "no market" because
            # parse_market doesn't know basketball. Drop non-football here so
            # the fuzzy matcher only ever sees football candidates.
            football = [
                ev for ev in raw_events
                if ev.get("sport_category_id") == _FOOTBALL_CATEGORY_ID
            ]
            return [e for e in (_parse_event(ev) for ev in football) if e]

        last_status = resp.status_code
        last_body = (resp.text or "")[:200].replace("\n", " ")
        if attempt == 1 and resp.status_code in _SEARCH_RETRY_STATUSES:
            log.info("Search HTTP %d for query %r — retrying once after 1s",
                     resp.status_code, query)
            time.sleep(1.0)
            continue
        break

    log.warning("Search HTTP %d for query %r (after retry) — body: %s",
                last_status, query, last_body or "<empty>")
    raise CoolbetSearchBlocked(
        f"search/v2 returned HTTP {last_status} for query {query!r} "
        f"(--record uses anon-read so this is Imperva/Kambi, not a JWT issue). "
        f"If this persists, check Coolbet status or refresh COOLBET_IMPERVA_COOKIES."
    )


def search_coolbet_event(
    session: CoolbetSession, home: str, away: str,
    match_date: datetime | None = None,
) -> dict | None:
    """
    Multi-pass team-name search against /s/sbgate/sports/search/v2.

    SEARCH-MULTIPASS (2026-05-20): single-word search was missing real
    European top-flight matches (Ajax-Groningen, Wolfsburg-Paderborn,
    Hammarby etc.) that Coolbet does cover. Now widens progressively
    until a fuzzy match passes the threshold:

        1. home first 3 letters    (short prefix, broad result set)
        2. home first 4 letters
        3. home first 5 letters
        4. away first 3 letters    (fallback — useful when home name is generic)
        5. away first 4 letters
        6. away first 5 letters
        7. full home name (whitespace-stripped)

    Aggregates all unique candidates across passes and fuzzy-matches once.
    Stops early as soon as a match clears _FUZZY_THRESHOLD.
    """
    def _prefix(name: str, n: int) -> str | None:
        # Strip whitespace + take first n alphabetic chars (skip "FC ", "SC ", etc.)
        clean = "".join(c for c in (name or "") if c.isalpha())
        return clean[:n] if len(clean) >= n else None

    # COOLBET-TEAM-ALIAS (2026-06-09): expand prefix search across all known
    # aliases so a rebranded club (e.g. Evergreen FC ↔ Northern Virginia FC)
    # gets a search query that actually returns the Coolbet event.
    home_variants = _team_aliases(home)
    away_variants = _team_aliases(away)

    queries: list[str] = []
    seen: set[str] = set()
    candidates: list[str | None] = []
    for variant in home_variants:
        candidates.extend([_prefix(variant, 3), _prefix(variant, 4), _prefix(variant, 5)])
    for variant in away_variants:
        candidates.extend([_prefix(variant, 3), _prefix(variant, 4), _prefix(variant, 5)])
    candidates.append(home.strip() if home else None)
    candidates.append(away.strip() if away else None)
    for q in candidates:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)

    if not queries:
        return None

    # COOLBET-NEGATIVE-CACHE layer 3 (2026-09-07): cap the ladder.
    #
    # Measured across ~34,200 logged searches, match quality degrades
    # MONOTONICALLY with ladder depth — later queries are shorter prefixes of
    # aliases, so they trawl progressively weaker evidence:
    #
    #   depth  n        mean score   below 80
    #     1    23,960      94.7         7.7%
    #     4     2,653      91.0        16.8%
    #     5       575      91.1        12.7%
    #     6       183      86.8        34.4%
    #     7     1,176      83.9        38.1%
    #     8        92      75.0        73.9%
    #
    # So this is NOT only a cost cut. Depth 6-8 is where wrong-fixture matches
    # live: at depth 8, 74% of "hits" score below 80, and the confirmed-wrong
    # `Acatlan vs Guerreros` -> `Atlas vs Querétaro` scored 70.6. Truncating the
    # ladder removes the tier that produces false positives.
    #
    # Cut at 5, not the 4 the ticket proposed: depth 5 is as clean as depths 3-4
    # (mean 91.1, 12.7% below 80) and capping at 4 would discard 575 good
    # matches for nothing. The quality cliff is between 5 and 6.
    #
    # Cost: 86% of MISSES currently burn all 8 queries, so this is 3 fewer
    # search calls on the majority of the sweep. 95.8% of hits land by depth 5.
    if len(queries) > _SEARCH_LADDER_MAX:
        queries = queries[:_SEARCH_LADDER_MAX]

    # Run progressively. Stop as soon as fuzzy match clears threshold.
    aggregate: dict[int, dict] = {}  # de-dup by event id
    for q in queries:
        cands = _do_search(session, q)
        for ev in cands:
            try:
                aggregate[int(ev["id"])] = ev
            except (TypeError, ValueError, KeyError):
                continue
        match = fuzzy_match_event(home, away, list(aggregate.values()), match_date) if aggregate else None
        if match:
            m_home, m_away = _event_side_names(match)
            log.info("Search matched '%s vs %s' → Coolbet '%s vs %s' (id=%s) "
                     "via query=%r (queries tried=%d, candidates=%d)",
                     home, away, m_home, m_away, match.get("id"),
                     q, queries.index(q) + 1, len(aggregate))
            return match

    # All queries exhausted without a fuzzy hit
    best = "—"
    if aggregate:
        sample = next(iter(aggregate.values()))
        s_home, s_away = _event_side_names(sample)
        best = f"{s_home} vs {s_away}"
    log.info("Search exhausted %d queries for '%s vs %s' — best candidate '%s' "
             "(%d unique events scanned)",
             len(queries), home, away, best, len(aggregate))
    return None


_FO_MATCH_URL = "https://www.coolbet.com/s/sbgate/sports/fo-match"


def fetch_sidebets(session: CoolbetSession, match_id: int) -> list[dict]:
    """
    GET /s/sbgate/sports/fo-market/sidebets?matchId=... for a single match.
    Returns bet_offers in same format as _parse_event.

    SIDEBETS-PARAMS-FIX (2026-05-20): live capture from Coolbet DevTools shows
    the site sends `marketTypeGroupId=15` + `matchStatus=OPEN` — without them
    the endpoint silently returns empty `betOffers`. Group 15 appears to be
    "all open side markets" (OU/BTTS/AH/etc.). The main 1X2 market comes
    from fetch_main_markets() (POST /fo-match), not from here.
    """
    resp = session.get(_SIDEBETS_URL, params={
        "matchId":            match_id,
        "country":            "EE",
        "language":           "en",
        "layout":             "EUROPEAN",
        "marketTypeGroupId":  15,
        "matchStatus":        "OPEN",
    })
    if resp.status_code != 200:
        log.warning("sidebets %d returned %d", match_id, resp.status_code)
        return []
    return _parse_bet_offers_payload(resp.json().get("betOffers") or [])


def fetch_main_markets(session: CoolbetSession, match_ids: list[int]) -> dict[int, list[dict]]:
    """
    POST /s/sbgate/sports/fo-match — main markets (1X2, OU 2.5, etc.) per match.
    Replacement for the now-404 fo-category endpoint discovered 2026-05-20.
    Body: {language, country, layout, locale, matchIds: [...]}
    Returns: {match_id: [bet_offer, ...]} in same shape as fetch_sidebets.

    Batches well — caller can pass many matchIds at once.
    """
    if not match_ids:
        return {}
    body = {
        "language": "en",
        "country":  "EE",
        "layout":   "EUROPEAN",
        "locale":   "en",
        "matchIds": [str(mid) for mid in match_ids],
    }
    resp = session.post(_FO_MATCH_URL, json=body)
    if resp.status_code != 200:
        log.warning("fo-match POST %s returned %d: %s",
                    match_ids[:3], resp.status_code, resp.text[:200])
        return {}
    payload = resp.json()
    # Response shape varies. Either a list of {matchId, betOffers} OR a dict
    # keyed by matchId OR a single bag of betOffers. Handle all three.
    out: dict[int, list[dict]] = {}
    if isinstance(payload, list):
        for entry in payload:
            mid = int(entry.get("matchId") or entry.get("id") or 0)
            if mid:
                out[mid] = _parse_bet_offers_payload(entry.get("betOffers") or [])
    elif isinstance(payload, dict):
        if "betOffers" in payload and len(match_ids) == 1:
            out[int(match_ids[0])] = _parse_bet_offers_payload(payload["betOffers"])
        else:
            for k, v in payload.items():
                try:
                    mid = int(k)
                except (TypeError, ValueError):
                    continue
                bo = v.get("betOffers") if isinstance(v, dict) else (v if isinstance(v, list) else [])
                out[mid] = _parse_bet_offers_payload(bo or [])
    return out


def _parse_bet_offers_payload(raw_offers: list[dict]) -> list[dict]:
    """Shared parser — turns raw Coolbet betOffers list into the standard
    {id, criterion_label, outcomes:[...]} shape used by both sidebets and
    fo-match callers."""
    bet_offers = []
    for bo in raw_offers:
        if bo.get("suspended"):
            continue
        criterion = bo.get("criterion") or {}
        label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()
        outcomes = []
        for oc in bo.get("outcomes") or []:
            raw = oc.get("odds") or 0
            dec = raw / 1000.0 if isinstance(raw, int) and raw > 100 else float(raw or 0)
            outcomes.append({
                "id":           oc.get("id"),
                "label":        (oc.get("englishLabel") or oc.get("label") or "").strip(),
                "odds_decimal": dec,
            })
        if outcomes:
            bet_offers.append({
                "id":              bo.get("id"),
                "criterion_label": label,
                "outcomes":        outcomes,
            })
    return bet_offers


# ── Matching ──────────────────────────────────────────────────────────────────

_UNICODE_MAP = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'å': 'a', 'Å': 'A',
    'ö': 'o', 'Ö': 'O', 'ü': 'u', 'Ü': 'U',
    'ä': 'a', 'Ä': 'A', 'é': 'e', 'è': 'e',
    'ê': 'e', 'ë': 'e', 'à': 'a', 'â': 'a',
    'î': 'i', 'ï': 'i', 'ô': 'o', 'ù': 'u',
    'û': 'u', 'ç': 'c', 'ñ': 'n', 'æ': 'ae', 'Æ': 'Ae',
})


def _ascii(s: str) -> str:
    """Lowercase + map European chars to ASCII for fuzzy matching (ø→o, å→a, etc.).

    COOLBET-FUZZY-CASE-INSENSITIVE (2026-05-29): rapidfuzz.partial_ratio is
    case-sensitive — `partial_ratio("Pepo", "PEPO")` returns 40, below the
    70 threshold, even though it's clearly the same team. MyPa vs Pepo
    (Finland Kakkonen) was killing combos this way. Lowercasing here fixes
    every consumer of _ascii in one place.
    """
    return s.translate(_UNICODE_MAP).lower()


# COOLBET-TEAM-ALIAS (2026-06-09): a few clubs play under one name in our DB
# (sourced from API-Football) and a different name on Coolbet. Without aliases
# the placer never matches them — prefix search misses the Coolbet name and
# fuzzy_match_event scores the wrong half of the fixture at ~20-30, well under
# the 70 threshold, even when the other team's name nails 100.
#
# Keys + values are stored as raw human strings; lookup is done on the ASCII-
# folded lowercased form so " FC", diacritics, and casing don't matter. Add
# both directions when registering an alias so search/fuzzy work regardless of
# which side our DB lands on.
_TEAM_ALIASES: dict[str, list[str]] = {
    # USL League Two side: Evergreen FC rebranded to Northern Virginia FC
    # (a.k.a. NoVa FC); both names still circulate. Coolbet uses the new name.
    "evergreen fc":         ["Northern Virginia FC", "NoVa FC"],
    "evergreen":            ["Northern Virginia FC", "NoVa FC"],
    "northern virginia fc": ["Evergreen FC"],
    "nova fc":              ["Evergreen FC"],
    # COOLBET-UI-PLACER (2026-08-27): city-name TRANSLATIONS, not spellings —
    # diacritic folding in _ascii cannot help here because Vienna and Wien share
    # no letters to fold. Found live: our 'Austria Vienna' (API-Football) scored
    # too low against Coolbet's 'FK Austria Wien' and the fixture was skipped
    # even though it was sitting in the search results.
    "austria vienna":       ["FK Austria Wien", "Austria Wien"],
    "austria wien":         ["Austria Vienna"],
    "fk austria wien":      ["Austria Vienna"],
    # #112 (2026-09-24, from the Tonybet/Epicbet sweeper audit): renames and
    # official-vs-common names that fuzzy scoring cannot bridge (no shared letters
    # to fold). These go through fuzzy_match_event, i.e. Epicbet / Unibet-Site /
    # Tonybet and the placer.
    "york united":          ["Inter Toronto"],          # club renamed 2025
    "inter toronto":        ["York United"],
    "south korea":          ["Korea Republic"],          # FIFA name
    "korea republic":       ["South Korea"],
    "hapoel nazareth illit": ["Hapoel Nof Hagalil", "Nof Hagalil"],  # city renamed
    "nazareth illit":       ["Nof Hagalil"],
    "hapoel nof hagalil":   ["Hapoel Nazareth Illit"],
    "nof hagalil":          ["Hapoel Nazareth Illit"],
}


def _team_aliases(name: str) -> list[str]:
    """Return [original name, ...alternates] for fuzzy/search expansion.

    Coolbet sometimes lists a club under a different name (rebrand, parent
    company, regional vs full name). When we find an entry in _TEAM_ALIASES
    we feed every alternate through prefix-search and through the fuzzy
    scorer, taking the max — a single canonical alias entry is enough.
    """
    if not name:
        return []
    key = _ascii(name).strip()
    return [name, *_TEAM_ALIASES.get(key, ())]


# COOLBET-FUZZY-DATE-GUARD (2026-05-26): match team names AND kickoff date.
# Same-team double-headers (Reserve vs first team, women vs men, multiple
# legs on different days) were resolving to the wrong event because the
# fuzzy matcher only scored names. Reject any candidate whose kickoff is
# more than this many hours away from our DB match date.
_FUZZY_DATE_TOLERANCE_HOURS = 6
# WRONG-FIXTURE-BOARDS (#120, 2026-09-24): the ±6 h window above paired Epicbet / Unibet
# events with a DIFFERENT match of similar names (Narva v Levadia stored Goias v Avai's
# board; 18:00 vs 21:00, 19:00 vs 14:00, 12:00 vs 11:00). On correct pairings the book's
# start is within 15 min of our kickoff 99% of the time (Coolbet 1,638/1,656, Unibet
# 1,927/1,981, Tonybet 355/364 — book_event_map, 7 d). Beyond this the event goes to the
# DATE-MISMATCH diagnostics below instead of being paired: no price beats a wrong price.
_PAIR_START_TOLERANCE_MIN = 45   # quiet reject; only > _FUZZY_DATE_TOLERANCE_HOURS raises a date dispute


def _parse_iso_start(start: str | None) -> datetime | None:
    if not start:
        return None
    try:
        # Coolbet `start` is ISO-8601 with trailing Z; fromisoformat handles "+00:00".
        s = start.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _record_date_dispute(match_id, book_start, our_date) -> None:
    """Flag a fixture whose kickoff the book and API-Football disagree on.

    Deliberately does NOT write `matches.date`. The book is usually right —
    Coolbet moved with the Atlético Grau postponement and AF did not — but
    "usually" is doing a lot of work when the input is a fuzzy name match. A
    wrong correction moves a fixture we would otherwise have priced correctly
    and is then believed over AF indefinitely, whereas suppressing costs a
    handful of picks a week and cannot invent a wrong one.

    Never raises: this runs inside the odds-snapshot path, and an alerting
    side-effect must not take the snapshot down with it.
    """
    if match_id is None:
        return
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """UPDATE matches
                  SET date_disputed_at    = COALESCE(date_disputed_at, NOW()),
                      date_dispute_source = %s,
                      date_dispute_value  = %s
                WHERE id = %s
                  AND status = 'scheduled'""",
            ("Coolbet", book_start, str(match_id)),
        )
        log.warning("  -> match %s flagged date-disputed; it will not be priced "
                    "until the book and AF agree", match_id)
    except Exception as e:                      # noqa: BLE001 - see docstring
        log.warning("  -> could not record date dispute for %s: %s", match_id, e)


def clear_date_dispute(match_id) -> None:
    """Lift a dispute once the book and our date agree again.

    Called from the matching path on success, so the flag is self-healing: a
    genuine postponement that AF eventually picks up clears on the next
    snapshot run rather than needing a human.
    """
    if match_id is None:
        return
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """UPDATE matches
                  SET date_disputed_at = NULL, date_dispute_source = NULL,
                      date_dispute_value = NULL
                WHERE id = %s AND date_disputed_at IS NOT NULL""",
            (str(match_id),),
        )
    except Exception as e:                      # noqa: BLE001
        log.debug("could not clear date dispute for %s: %s", match_id, e)


def unique_pairs(pairs: list) -> tuple[list, int]:
    """ONE BOOK EVENT → AT MOST ONE FIXTURE (#120). `pairs` = [(fixture, event)] with
    fixture["date"] and event["id"/"start"]. When several fixtures claim one event, keep
    the fixture whose kickoff is closest to the event's start; drop the others (they are
    another match's prices). Returns (kept, n_dropped)."""
    by_event: dict = {}
    for m, ev in pairs:
        by_event.setdefault(str(ev.get("id")), []).append((m, ev))
    kept, dropped = [], 0
    for eid, group in by_event.items():
        if eid == "None" or len(group) == 1:
            kept.extend(group)
            continue
        def _gap(p):
            s, d = _parse_iso_start(p[1].get("start")), p[0].get("date")
            if s is None or d is None:
                return float("inf")
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return abs((s - d).total_seconds())
        def _names(p):
            m, ev = p
            try:
                return -min(fuzz.token_set_ratio(_ascii(m.get("home") or ""), _ascii(ev.get("home") or "")),
                            fuzz.token_set_ratio(_ascii(m.get("away") or ""), _ascii(ev.get("away") or "")))
            except Exception:  # noqa: BLE001
                return 0
        # closest kickoff first; on a tie, the better name match (not list order — review #2)
        group.sort(key=lambda p: (_gap(p), _names(p)))
        kept.append(group[0])
        dropped += len(group) - 1
        log.warning("one-event-one-fixture: event %s claimed by %d fixtures — kept %s", eid,
                    len(group), group[0][0].get("id"))
    return kept, dropped


def fuzzy_match_event(
    home: str, away: str, events: list[dict],
    match_date: datetime | None = None,
    match_id: str | None = None,
) -> dict | None:
    """Find the Coolbet event whose home+away names best match ours.

    COOLBET-SEARCH-LAVAL (2026-05-24): the previous whole-string
    token_set_ratio failed when our short club names ("Laval", "Rouen")
    matched against Coolbet's full names ("Stade Lavallois", "FC Rouen") —
    score 62 < threshold 70 because "Laval" and "Lavallois" don't share
    tokens. Per-team partial_ratio handles prefix/substring overlap
    correctly: partial_ratio("Laval", "Stade Lavallois") = 100 (Laval is
    inside Lavallois). We score each event by min(home_score, away_score)
    so a great home match can't paper over a bad away match.

    COOLBET-FUZZY-DATE-GUARD (2026-05-26): when `match_date` is supplied,
    only candidate events whose `start` is within ±6h are considered. Stops
    same-team-different-day false matches (e.g. Coolbet has Racing Club's
    first-team fixture tomorrow but not today's reserves fixture — names
    score 100 but the date is wrong).
    """
    if not events:
        return None
    # Shared with the UI placer and the Epicbet explorer so all three matching
    # paths agree on what counts as a different squad.
    from workers.automation.epicbet_explorer import _squads_compatible
    # COOLBET-TEAM-ALIAS (2026-06-09): score each side against every known
    # alias and take the max — so Evergreen FC ↔ Northern Virginia FC clears
    # threshold even though the two strings share no tokens.
    home_variants = [_ascii(n) for n in _team_aliases(home) if n]
    away_variants = [_ascii(n) for n in _team_aliases(away) if n]
    if not home_variants:
        home_variants = [_ascii(home)]
    if not away_variants:
        away_variants = [_ascii(away)]
    if match_date is not None and match_date.tzinfo is None:
        match_date = match_date.replace(tzinfo=timezone.utc)
    tol_seconds = _FUZZY_DATE_TOLERANCE_HOURS * 3600

    best_event = None
    best_score = -1
    best_swapped, best_swapped_exact, best_exact = -1, -1.0, -1.0   # #001 review (see below)
    # ── COOLBET-FUZZY-MATCH-FALSE-POSITIVES-2026-09-06, step 1 of 2 ────────
    # We are matching fixtures Coolbet does not carry: 'Acatlan vs Guerreros'
    # (owner-verified absent from Coolbet) was matched to 'Atlas vs Queretaro'
    # on 4 sweeps and its prices stored against our fixture.
    #
    # A threshold cannot fix it. Scored per-side with min(home, away), the
    # confirmed-WRONG 'Acatlan v Guerreros -> Atlas v Queretaro' and the
    # confirmed-RIGHT 'Al Bukayriyah v Al Anwar -> Albukiryah v Al-Anwar Club'
    # BOTH score partial 70.6 / token_set 66.7 — identical on both metrics. A
    # sweep of every combination of partial>=70..95 and token_set>=0..95
    # against 8 confirmed-bad and 7 confirmed-good pairs finds no separating
    # rule, so name similarity has no information left to give.
    #
    # The fix therefore has to be a corroborating signal, and picking its
    # threshold by guessing is exactly the mistake that produced three wrong
    # values for the sidebets limit today. So: MEASURE FIRST. These two
    # trackers are logged on every accepted match and cost nothing:
    #
    #   * kickoff delta — the date guard tolerates +/-6h, which is a whole
    #     afternoon of fixtures, and both feeds schedule to the minute. If
    #     correct matches cluster inside a few minutes, tightening this kills
    #     same-day impostors at near-zero cost to real coverage.
    #   * runner-up margin — a correct match usually stands clear of the
    #     field, while an impostor is one of several equally-mediocre
    #     candidates. A thin margin is a better ambiguity signal than a low
    #     absolute score, because the absolute score demonstrably is not one.
    #
    # Step 2 tightens using the distributions this produces. Nothing is
    # rejected on these yet — this commit only makes the decision measurable.
    runner_up_score = -1
    best_start: datetime | None = None
    skipped_date = 0
    skipped_squad = 0
    # Candidates rejected purely on date, kept so a postponement can be told
    # apart from Coolbet simply not offering the fixture. Both look like "no
    # coverage" otherwise, and they call for opposite responses.
    date_rejects: list[tuple[dict, datetime]] = []
    for ev in events:
        # Parsed unconditionally so the kickoff delta can be logged even when
        # no match_date was supplied by the caller.
        ev_start_seen = _parse_iso_start(ev.get("start"))
        if match_date is not None:
            ev_start = ev_start_seen
            if ev_start is not None:
                gap_s = abs((ev_start - match_date).total_seconds())
                if gap_s > tol_seconds:
                    skipped_date += 1
                    date_rejects.append((ev, ev_start))
                    continue
                if gap_s > _PAIR_START_TOLERANCE_MIN * 60:
                    # QUIET reject (review #2, 2026-09-24): NOT a date_reject. date_rejects feed
                    # _record_date_dispute, which drops OUR fixture from pricing — and a
                    # 45 min – 6 h neighbour is usually a different match of similar names
                    # (Atlante U21 v Monterrey U21 vs Iceland U21 v France U21), not proof
                    # our kickoff is stale. Disputes stay reserved for > 6 h, as before.
                    skipped_date += 1
                    continue
        ev_home = _ascii(ev.get("home") or "")
        ev_away = _ascii(ev.get("away") or "")

        # COOLBET-SQUAD-GUARD on the API path (2026-09-02). This path had NO
        # squad check at all — only the UI placer did — so a women's fixture
        # scored 80 against the men's match and a first team scored 100
        # against its own reserves. Both were verified to MATCH when the two
        # kickoffs coincide, which would store the wrong book's price under
        # our fixture and feed it straight to the value bots.
        #
        # It only ever looked safe because the date guard happened to fire
        # first: 'Seoul W vs Incheon Red Angels W' was being offered
        # 'FC Seoul vs Incheon United' at a different time, so the mismatch
        # surfaced as a DATE MISMATCH warning rather than as bad odds. That is
        # luck, not a guard — same kickoff and it goes through.
        #
        # Reject on squad qualifier BEFORE scoring: no fuzzy score should be
        # able to bridge women/men, first team/reserves or U21/senior.
        if not _squads_compatible(home, away, ev):
            skipped_squad += 1
            continue

        # Each side can match either Coolbet's home or away (handles flipped
        # fixtures) AND can match against any registered alias.
        # ORIENTATION-CONSISTENT (#120): our two teams must match DIFFERENT sides of
        # the book's event. Each side used to take its best of EITHER book side, so both
        # of ours could match the same book team. Both orientations are still allowed —
        # a transposed event is the mirror guard's business.
        def _best(variants, side):
            return max(fuzz.partial_ratio(v, side) for v in variants)
        direct = min(_best(home_variants, ev_home), _best(away_variants, ev_away))
        swapped = min(_best(home_variants, ev_away), _best(away_variants, ev_home))
        # #001 (2026-09-24): ORIENTATION-STRICT — was `max(direct, swapped)`, which
        # accepted a book event listing our AWAY team as its home side, and every
        # side-mapper (Epicbet / Unibet / Tonybet / Coolbet parsers) then wrote the
        # book's "1" into our "home": mirrored 1X2, sign-flipped AH, swapped team
        # totals. A candidate that fits better swapped is now refused and logged;
        # never re-oriented silently. See coolbet_matching.ORIENTATION_REJECTS.
        if swapped > direct:
            _se = (max(fuzz.ratio(v, ev_away) for v in home_variants)
                   + max(fuzz.ratio(v, ev_home) for v in away_variants)) / 2.0
            if (swapped, _se) > (best_swapped, best_swapped_exact):
                best_swapped, best_swapped_exact = swapped, _se
            if swapped >= _FUZZY_THRESHOLD:
                from workers.automation.coolbet_matching import ORIENTATION_REJECTS
                ORIENTATION_REJECTS.append((home, away, ev.get("home"), ev.get("away"), swapped))
                log.info("ORIENTATION: '%s v %s' matches '%s v %s' only SWAPPED (%d vs direct %d) — refused",
                         home, away, ev.get("home"), ev.get("away"), swapped, direct)
            continue
        score = direct
        if score > best_score:
            runner_up_score = best_score      # the score this one just beat
            best_score = score
            best_event = ev
            best_start = ev_start_seen
            best_exact = (max(fuzz.ratio(v, ev_home) for v in home_variants)
                          + max(fuzz.ratio(v, ev_away) for v in away_variants)) / 2.0
        elif score > runner_up_score:
            runner_up_score = score

    # #001 review (2026-09-24): a REFUSED reversed listing still competes. If the best fit
    # overall is reversed, that is the real fixture — refuse, instead of falling through to
    # a weaker direct candidate ("Penarol v Nacional" matched "Penarol Rivera v Nacional
    # Potosi" at 100 once the reversed real event was dropped). Ties at 100 are common
    # (subset names), so a tie is broken on EXACT name similarity.
    if best_event is not None and best_score >= _FUZZY_THRESHOLD and best_swapped >= _FUZZY_THRESHOLD and (
            best_swapped > best_score
            or (best_swapped == best_score and best_swapped_exact > best_exact)):
        log.info("ORIENTATION: '%s v %s' — best fit is a reversed listing (%s >= %s); fixture refused",
                 home, away, best_swapped, best_score)
        return None

    if best_event is not None and best_score >= _FUZZY_THRESHOLD and match_id:
        # Book and AF agree again — lift any standing dispute so a fixture is
        # not suppressed forever once AF catches up with a postponement.
        clear_date_dispute(match_id)

    if best_event is None or best_score < _FUZZY_THRESHOLD:
        # A candidate that would have matched on NAME and lost only on date is
        # not "Coolbet does not offer this" — it is "Coolbet and we disagree
        # about when this is played", and the book is the likelier one to be
        # right. Atletico Grau v FBC Melgar was postponed 31 Aug -> 1 Sept;
        # Coolbet moved, API-Football did not, and we would have gone on
        # pricing a fixture that is not played that night. Say so loudly rather
        # than letting it read as no coverage.
        for ev, ev_start in date_rejects:
            # DATE-MISMATCH-FALSE-ALARM (2026-09-02). `date_rejects` is filled
            # by the date check at the TOP of the loop, before the squad guard
            # below it ever runs — so it still holds candidates that could
            # never have been this fixture. Without this check the warning
            # fired on 'Seoul W vs Incheon Red Angels W' offered as 'FC Seoul
            # vs Incheon United', and on 'Gremio U17' offered as 'Grêmio
            # FBPA', both reported as "our fixture date is probably stale".
            #
            # It matters because AF-STALE-FIXTURE-DATES wants to alert off
            # this warning: of 13 unique firings in the logs, only 3 were real
            # date discrepancies. An alert that is 77% false teaches the
            # operator to ignore the 23% that are real.
            if not _squads_compatible(home, away, ev):
                continue
            name_score = min(
                max(fuzz.partial_ratio(v, _ascii(ev.get("home") or "")) for v in home_variants),
                max(fuzz.partial_ratio(v, _ascii(ev.get("away") or "")) for v in away_variants),
            )
            if name_score >= _FUZZY_THRESHOLD:
                log.warning(
                    "DATE MISMATCH for '%s vs %s' — Coolbet offers '%s vs %s' (score %d) at %s, "
                    "we have %s (%.1fh apart). OUR fixture date is probably stale; "
                    "no odds stored and no bet placeable until it is corrected.",
                    home, away, *_event_side_names(ev), name_score,
                    ev_start.isoformat(), match_date.isoformat(),
                    abs((ev_start - match_date).total_seconds()) / 3600,
                )
                # AF-STALE-FIXTURE-DATES step 4: a warning in a log nobody
                # greps is not a control. Record the dispute so the betting
                # pipeline stops PRICING a fixture we cannot date.
                _record_date_dispute(match_id, ev_start, match_date)
                break
        if best_event is not None:
            b_home, b_away = _event_side_names(best_event)
            best_label = f"{b_home} {b_away}"
        else:
            best_label = "—"
        log.info(
            "Fuzzy match FAILED for '%s vs %s' — best was '%s' (score %d < threshold %d, "
            "%d rejected on date, %d rejected on squad)",
            home, away, best_label, best_score, _FUZZY_THRESHOLD, skipped_date, skipped_squad,
        )
        return None
    matched_home, matched_away = _event_side_names(best_event)
    # COOLBET-FUZZY-MATCH-FALSE-POSITIVES step 1: emit the two corroborating
    # signals on every accepted match so step 2 can pick their thresholds from
    # a distribution instead of a guess. `ko_delta_min` is minutes between our
    # kickoff and Coolbet's; `margin` is how far the winner beat the runner-up.
    ko_delta_min = None
    if match_date is not None and best_start is not None:
        ko_delta_min = round(abs((best_start - match_date).total_seconds()) / 60.0, 1)
    margin = round(best_score - runner_up_score, 1) if runner_up_score >= 0 else None
    log.info(
        "Fuzzy matched '%s vs %s' → book event '%s vs %s' (score %d, margin %s, "
        "ko_delta_min %s, %d date-mismatched, %d squad-mismatched candidates skipped)",
        home, away, matched_home, matched_away, best_score,
        "n/a" if margin is None else margin,
        "n/a" if ko_delta_min is None else ko_delta_min,
        skipped_date, skipped_squad,
    )
    # #001: expose the score so feeds can store it in book_event_map.match_score —
    # Epicbet / Unibet-Site / Tonybet pairings were recorded with NULL, so none of
    # them could be audited. Set on the event dict itself (callers keep identity).
    best_event["_match_score"] = best_score
    return best_event


# ── RETIRED: the API "Path B" placer ───────────────────────────────────────────
#
# DELETED 2026-09-25 (#162 W4.6). This module used to also carry a complete
# second placement path: load_qualified_bets / load_qualified_combo_bets /
# load_qualified_inplay_bets → place_all_bets / _place_combo_bets /
# place_all_inplay_bets → _place_bet_api (POST /s/bets/bets), plus
# place_bet_by_id for the Telegram "Record at Coolbet" drain and the
# PlacementGuard stake/rate limiter they shared. Nothing live reached it: the
# Mac daemon that drove it was retired 2026-09-10, the VPS manual drain was
# pinned paper, in-play betting was retired 2026-08-21, and its --execute mode
# refused every simulated_bets bot. A second path that inherits no gates is the
# RELIABILITY_LEDGER pattern, so it went rather than rotting. It is in git
# history (parent of the #162 W4.6 commit) if an API placement path is ever
# wanted again — it would have to go through placement_gate + placement_floor.
#
# What stays here is SHARED, and imported by the live code: the edge / odds
# floors, event search, fixture pairing and the Coolbet read endpoints. Real
# money is placed ONLY by scripts/place_coolbet_ui.py and
# workers/automation/best_price_router.py (see placement_gate.py).
