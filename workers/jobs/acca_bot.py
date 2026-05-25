"""
COMBO-RESEARCH-PHASE-D — paper acca bot.

ACCA-REDESIGN (2026-05-20): data source changed from simulated_bets to
direct DB scan of predictions + odds_snapshots.

Original design read today's pending singles from simulated_bets. Problem:
silent coupling — if source bots are retired, skipped, or slow, acca bots
see 0 legs and silently skip with no diagnostic output. A retired source
bot (e.g. bot_ou15_defensive) produces no rows at all, so the combo bot
degrades invisibly.

New design: _scan_todays_candidates() queries the DB directly.
  • predictions table: latest ensemble probability per market for today's
    pre-KO matches (source='ensemble', created before kickoff).
  • odds_snapshots: best pre-kickoff odds for each (match, market, selection).
  • Compute edge = model_prob × bookmaker_odds - 1 inline.
  • Filter: edge ≥ 5%, odds 1.40–2.50, market in (btts, ou25, ou35, ou15).
  • Exclude 1x2/DC/DNB — 3-year backtest shows -62/-69% ROI for 1x2 combos
    and negative combo ROI for DC/DNB.
  • One candidate per match (best edge per match).

This makes the acca bot independent of source bot execution order, cohort
timing, or retirement status. If there are qualifying +EV matches today, the
acca bot finds them regardless of what other bots did.

Storage: one row in simulated_bets per combo, with:
  market       = 'combo'
  selection    = '{N}-leg'
  odds_at_pick = product of leg odds
  model_probability = product of leg probs (assumes independence — true across matches)
  combo_legs   = JSONB array of {bet_id, match_id, market, selection, odds, prob}
  combo_size   = N
  match_id     = first leg's match_id (placeholder for the NOT NULL schema constraint;
                 settlement reads combo_legs for the real outcomes)

Phase A finding (2026-05-17): Coolbet doesn't compound margin on
accumulators (ratio 0.9999 over a 3-fold test), so combined odds = product
of leg odds and combined EV = product of leg EVs minus 1. A combo of three
+5% EV singles is +15.8% EV.

Phase D backtest: at the 'once a week hit' target (~14% combo hit rate),
4-5 legs balanced selection lands closest in the small-sample historical
window. Defaults below reflect that — tune via BOTS_CONFIG entries once
~30 settled combos accumulate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write
from workers.model.improvements import compute_kelly

console = Console()


# ── Acca bot variants. Both share picking logic (same N legs/day) but place
#    their stake differently — straight = one max-leg combo, no_singles =
#    spread across all sub-combos of size 2..N (Trixie / Yankee / Canadian /
#    Heinz depending on N). Running both as paper bots lets us compare
#    variance-reduction value of system bets vs straight EV per €.
# Markets eligible for acca legs. Excludes 1x2/DC/DNB — 3-year backtest
# shows those markets have -62/-69% ROI in combo context (1x2 home/away
# combo ROI is deeply negative; DC/DNB similarly). OU and BTTS are the
# only markets where the Poisson model has reliable edge for combos.
ACCA_ELIGIBLE_MARKETS = frozenset({"btts", "ou25", "ou35", "ou15"})

# Market label → (predictions.market key, odds_snapshots.market, selection)
# Used by _scan_todays_candidates to map from probability fields to odds fields.
_MARKET_SPEC = [
    # (acca_market_key, pred_market, odds_market, selection, prob_field)
    ("ou25",  "over25",   "over_under_25", "over",  "over_25_prob"),
    ("ou25",  "under25",  "over_under_25", "under", "under_25_prob"),
    ("ou35",  "over35",   "over_under_35", "over",  "over_35_prob"),
    ("ou35",  "under35",  "over_under_35", "under", "under_35_prob"),
    ("ou15",  "over15",   "over_under_15", "over",  "over_15_prob"),
    ("ou15",  "under15",  "over_under_15", "under", "under_15_prob"),
    ("btts",  "btts_yes", "btts",          "yes",   "btts_yes_prob"),
    ("btts",  "btts_no",  "btts",          "no",    "btts_no_prob"),
]

# Whitelist of "proven" markets/bots for the bot_acca_proven /
# bot_combo_proven_system variants. With the ACCA-REDESIGN, the whitelist
# concept now filters by acca_market_key rather than source bot name —
# the acca bot scans candidates directly, not via simulated_bets.
# NOTE: bot_ou15_defensive was retired 2026-05-20; ou15 market still
# eligible but only included if shadow_bets show recovery (≥30 bets ≥3% ROI).
# For now "proven" restricts to the highest-ROI markets from backtest.
PROVEN_MARKETS_WHITELIST = frozenset({"ou25", "ou35", "btts"})


# COMBO-RESTRUCTURE (2026-05-22): backtest over 3 years + multiple edge-filter
# cuts showed two clear findings:
#
#   1. N=5 is the only leg count with consistently positive ROI. N=3 and N=4
#      are negative on the full 3-year window; only go positive in recent 12mo
#      which is too short to trust. All variants now require exactly 5 legs.
#
#   2. OU15/over is the edge driver. Days with OU15/over in the top-5 legs
#      have 73% avg leg win rate vs 44% without it, and straight N=5 ROI
#      of +1199% vs -0.9%. All variants now require OU15/over in the picked
#      legs (`require_ou15=True`). On days without OU15, no combo fires.
#
#   3. Fewer sub-combos = higher ROI. `no_singles` (26 tickets for N=5) is
#      consistently 5-60 ROI-points worse than `fours_up` (6 tickets). The
#      system bots switch to `fours_up` (5-fold + five 4-folds). For 8% edge
#      filter, fours_up/top2_sizes delivers +95% ROI vs +37% for no_singles.
#
#   Script: scripts/backtest_system_variants.py

ACCA_VARIANTS = {
    # Pure 5-fold. All 5 legs must win. Highest ROI (+101-177% at 8% edge),
    # ~17-24 day dry streaks on qualifying days.
    "bot_acca_value": {
        "structure":           "straight",
        "market_whitelist":    None,
        "require_ou15":        True,
        "min_legs":            5,
        "max_legs":            5,
        "min_per_leg_edge":    0.08,
        "max_per_leg_odds":    2.50,
        "min_per_leg_odds":    1.40,
        "min_combined_edge":   0.10,
        "max_combined_odds":   100.0,
        "kelly_fraction":      0.05,
        "max_stake_pct":       0.005,
        "min_stake":           1.0,
    },
    # 5-fold + five 4-folds = 6 tickets. Tolerates one leg failing.
    # +49-95% ROI at 8% edge, ~12 day dry streaks on qualifying days.
    "bot_combo_system": {
        "structure":           "fours_up",
        "market_whitelist":    None,
        "require_ou15":        True,
        "min_legs":            5,
        "max_legs":            5,
        "min_per_leg_edge":    0.08,
        "max_per_leg_odds":    2.50,
        "min_per_leg_odds":    1.40,
        "min_combined_edge":   0.10,
        "max_combined_odds":   100.0,
        "kelly_fraction":      0.05,
        "max_stake_pct":       0.005,
        "min_stake":           1.0,
    },
    # Straight 5-fold, proven markets only (ou25, ou35, btts + ou15 required).
    "bot_acca_proven": {
        "structure":           "straight",
        "market_whitelist":    PROVEN_MARKETS_WHITELIST,
        "require_ou15":        True,
        "min_legs":            5,
        "max_legs":            5,
        "min_per_leg_edge":    0.08,
        "max_per_leg_odds":    2.50,
        "min_per_leg_odds":    1.40,
        "min_combined_edge":   0.10,
        "max_combined_odds":   100.0,
        "kelly_fraction":      0.05,
        "max_stake_pct":       0.005,
        "min_stake":           1.0,
    },
    # fours_up, proven markets only.
    "bot_combo_proven_system": {
        "structure":           "fours_up",
        "market_whitelist":    PROVEN_MARKETS_WHITELIST,
        "require_ou15":        True,
        "min_legs":            5,
        "max_legs":            5,
        "min_per_leg_edge":    0.08,
        "max_per_leg_odds":    2.50,
        "min_per_leg_odds":    1.40,
        "min_combined_edge":   0.10,
        "max_combined_odds":   100.0,
        "kelly_fraction":      0.05,
        "max_stake_pct":       0.005,
        "min_stake":           1.0,
    },
    # COMBO-NEW (2026-05-23): straight 5-fold restricted to matches in leagues
    # that exist on Coolbet — so the user can actually place the combo.
    # Tradeoff vs bot_acca_value:
    #   - Coolbet's coverage is ~130 leagues (top tiers only), giving 50-80
    #     pre-KO matches/day vs ~1100 across our full API-Football feed.
    #   - In that smaller pool, OU15/over is priced below 1.40 (top-league
    #     tightness), so require_ou15=True + min_per_leg_odds=1.40 produces
    #     0 fires. We drop require_ou15 here and lower min_per_leg_odds to
    #     1.25 to let it fire. That's a deviation from the backtest-validated
    #     "OU15 drives ROI" finding — Coolbet variant should be treated as
    #     paper-only until ≥30 settled combos accumulate, then re-evaluate.
    "bot_acca_coolbet": {
        "structure":           "straight",
        "market_whitelist":    None,
        "coolbet_only":        True,
        "require_ou15":        False,
        "min_legs":            5,
        "max_legs":            5,
        "min_per_leg_edge":    0.08,
        "max_per_leg_odds":    2.50,
        "min_per_leg_odds":    1.25,
        "min_combined_edge":   0.10,
        "max_combined_odds":   100.0,
        "kelly_fraction":      0.05,
        "max_stake_pct":       0.005,
        "min_stake":           1.0,
    },
}


# Back-compat alias for existing callers; equals the straight variant config.
ACCA_CONFIG = ACCA_VARIANTS["bot_acca_value"]


def _subcombo_count(n_legs: int, structure: str) -> int:
    """Number of sub-bets a structure produces for N picks."""
    if structure == "straight":
        return 1
    if structure == "no_singles":
        return sum(math.comb(n_legs, k) for k in range(2, n_legs + 1))
    if structure == "fours_up":
        # All combos of size 4..N  (for N=5: 5 four-folds + 1 five-fold = 6)
        return sum(math.comb(n_legs, k) for k in range(4, n_legs + 1))
    raise ValueError(f"Unknown structure: {structure}")


# ── Coolbet league filter ─────────────────────────────────────────────────────
# COMBO-NEW (2026-05-23): bot_acca_coolbet restricts candidates to matches whose
# league is offered on Coolbet, so the resulting combo is actually placeable by
# the user. Coolbet's league cache stores names + slugs in Estonian (e.g.
# "jalgpall/inglismaa/meistriliiga" for the Premier League) — we map both the
# English-ish translated names and the slug's country segment to filter our
# matches table.

# Estonian country slug → English country name (covers the ~60 football leagues
# in coolbet_leagues_cache.json; safe to extend over time).
_COOLBET_COUNTRY_SLUG_TO_EN = {
    "inglismaa": "England",
    "hispaania": "Spain",
    "itaalia": "Italy",
    "saksamaa": "Germany",
    "prantsusmaa": "France",
    "holland": "Netherlands",
    "portugal": "Portugal",
    "belgia": "Belgium",
    "tuerkei": "Turkey", "tuerki": "Turkey", "turki": "Turkey",
    "shotimaa": "Scotland",
    "iirimaa": "Ireland",
    "norra": "Norway",
    "rootsi": "Sweden",
    "soome": "Finland",
    "taani": "Denmark",
    "island": "Iceland",
    "poola": "Poland",
    "ungari": "Hungary",
    "tsehhi": "Czech-Republic",
    "slovakkia": "Slovakia",
    "rumeenia": "Romania",
    "bulgaaria": "Bulgaria",
    "horvaatia": "Croatia",
    "serbia": "Serbia",
    "kreeka": "Greece",
    "shveits": "Switzerland",
    "austria": "Austria",
    "ukraina": "Ukraine",
    "venemaa": "Russia",
    "eesti": "Estonia",
    "laeti": "Latvia", "lati": "Latvia",
    "leedu": "Lithuania",
    "usa": "USA",
    "mehhiko": "Mexico",
    "brasiilia": "Brazil",
    "argentina": "Argentina",
    "tshiili": "Chile",
    "uruguay": "Uruguay",
    "kolumbia": "Colombia",
    "peruu": "Peru",
    "jaapan": "Japan",
    "lounakorea": "South-Korea",
    "hiina": "China",
    "austraalia": "Australia",
    "euroopa": "World",  # UEFA competitions show as "World" in API-Football leagues
    "maailm": "World",
}


def _coolbet_match_ids() -> set[str]:
    """Set of match_id strings for today's pre-KO matches whose league is on
    Coolbet (per coolbet_leagues_cache.json, football sportCategoryId=62).

    Match heuristic: a DB league is considered Coolbet-covered if a Coolbet
    league exists where (a) the country segment of the Coolbet slug maps to the
    DB league's country (via _COOLBET_COUNTRY_SLUG_TO_EN), AND (b) the Coolbet
    league name appears (case-insensitive) as a substring of the DB league name
    or vice versa. Loose match on purpose — Estonian vs English label drift
    means we'd miss most leagues with exact equality.
    """
    import json as _json
    from pathlib import Path as _Path

    cache_path = (_Path(__file__).resolve().parents[1] / "automation"
                  / "coolbet_leagues_cache.json")
    try:
        coolbet = _json.loads(cache_path.read_text())
    except Exception as e:
        console.print(f"[yellow]bot_acca_coolbet: cache read failed ({e}) — empty filter[/yellow]")
        return set()

    # Build {country_en: [normalized_league_name, ...]} from Coolbet football entries.
    cb_by_country: dict[str, list[str]] = {}
    for row in coolbet:
        if int(row.get("sportCategoryId") or 0) != 62:
            continue
        slug = (row.get("fullSlug") or "").lower()
        parts = slug.split("/")
        if len(parts) < 3:
            continue
        country_slug = parts[1]
        country_en = _COOLBET_COUNTRY_SLUG_TO_EN.get(country_slug)
        if not country_en:
            continue
        name = (row.get("name") or "").lower().strip()
        if not name:
            continue
        cb_by_country.setdefault(country_en, []).append(name)

    if not cb_by_country:
        return set()

    today_utc = datetime.now(timezone.utc).date().isoformat()
    rows = execute_query(
        """SELECT m.id::text AS match_id, l.name AS league_name, l.country
           FROM matches m
           JOIN leagues l ON l.id = m.league_id
           WHERE DATE(m.date AT TIME ZONE 'UTC') = %s
             AND m.status NOT IN ('finished', 'cancelled', 'postponed')
        """,
        [today_utc],
    ) or []

    matched: set[str] = set()
    for r in rows:
        country = (r["country"] or "").strip()
        names = cb_by_country.get(country)
        if not names:
            continue
        db_name = (r["league_name"] or "").lower().strip()
        if not db_name:
            continue
        for cb_name in names:
            if cb_name in db_name or db_name in cb_name:
                matched.add(r["match_id"])
                break
    return matched


@dataclass
class CandidateLeg:
    bet_id: str
    match_id: str
    market: str
    selection: str
    odds: float
    prob: float           # max(calibrated_prob, model_probability)
    edge: float           # prob × odds - 1
    bot_source: str       # which bot placed this single (for context only)


def _scan_todays_candidates(
    market_whitelist: frozenset | None = None,
    *,
    always_include_markets: frozenset | None = None,
    match_id_filter: set | None = None,
) -> list[CandidateLeg]:
    """Scan today's pre-KO matches directly from predictions + odds_snapshots.

    ACCA-REDESIGN (2026-05-20): replaces _fetch_todays_singles() which read
    from simulated_bets of other bots — creating silent coupling where retired
    or slow source bots caused 0 legs with no diagnostic output.

    This function queries the DB directly:
      • predictions (source='ensemble', created before kickoff) for model probs
      • odds_snapshots for best pre-kickoff odds per (match, market, selection)
      • Inline edge = model_prob × bookmaker_odds - 1
      • Filters: edge ≥ 5%, odds 1.40–2.50, market in ACCA_ELIGIBLE_MARKETS
      • One candidate per match (best edge wins)

    market_whitelist: optional frozenset of acca_market_key strings to restrict
    to (e.g. PROVEN_MARKETS_WHITELIST = {'ou25', 'ou35', 'btts'}).
    None = all ACCA_ELIGIBLE_MARKETS.
    """
    today_utc = datetime.now(timezone.utc).date().isoformat()
    # COMBO-FIX-1 (2026-05-23): `eligible` filters which markets we return as
    # candidates. `always_include_markets` is a separate union that gets merged
    # in regardless of whitelist — used by callers like the "proven" variants
    # which require an ou15 leg (via require_ou15=True) but otherwise only want
    # to consider proven markets {ou25, ou35, btts}. Without this, the proven
    # variants could never satisfy require_ou15 and silently fired 0 combos.
    base_eligible = market_whitelist if market_whitelist is not None else ACCA_ELIGIBLE_MARKETS
    eligible = base_eligible | (always_include_markets or frozenset())

    # Step 1: load today's pre-KO match IDs
    match_rows = execute_query(
        """SELECT m.id::text AS match_id
           FROM matches m
           WHERE DATE(m.date AT TIME ZONE 'UTC') = %s
             AND m.status NOT IN ('finished', 'cancelled', 'postponed')
        """,
        [today_utc],
    ) or []
    if not match_rows:
        return []
    match_ids = [r["match_id"] for r in match_rows]
    if match_id_filter is not None:
        match_ids = [mid for mid in match_ids if mid in match_id_filter]
        if not match_ids:
            return []

    # Step 2: load latest ensemble predictions for each match × market
    placeholders = ",".join(["%s"] * len(match_ids))
    pred_rows = execute_query(
        f"""SELECT DISTINCT ON (p.match_id, p.market)
               p.match_id::text AS match_id,
               p.market,
               p.model_probability
           FROM predictions p
           JOIN matches m ON m.id = p.match_id
           WHERE p.match_id::text IN ({placeholders})
             AND p.source = 'ensemble'
             AND p.created_at < m.date
           ORDER BY p.match_id, p.market, p.created_at DESC
        """,
        match_ids,
    ) or []
    # {match_id: {pred_market_key: prob}}
    probs_by_match: dict[str, dict[str, float]] = {}
    for r in pred_rows:
        mid = r["match_id"]
        prob = float(r["model_probability"]) if r["model_probability"] is not None else None
        if prob is None:
            continue
        if mid not in probs_by_match:
            probs_by_match[mid] = {}
        probs_by_match[mid][r["market"]] = prob

    # Step 3: load best pre-kickoff odds for the relevant markets
    # Use the same pattern as _load_pre_kickoff_odds in backtest_pre_match_bots.py
    eligible_snap_markets = set()
    for spec in _MARKET_SPEC:
        if spec[0] in eligible:
            eligible_snap_markets.add(spec[2])  # odds_snapshots.market value
    if not eligible_snap_markets:
        return []
    market_placeholders = ",".join(["%s"] * len(eligible_snap_markets))
    odds_rows = execute_query(
        f"""SELECT os.match_id::text AS match_id,
               os.market,
               os.selection,
               MAX(os.odds) AS odds
           FROM odds_snapshots os
           JOIN matches m ON m.id = os.match_id
           WHERE os.match_id::text IN ({placeholders})
             AND os.market IN ({market_placeholders})
             AND os.is_live = false
             AND os.timestamp < m.date
           GROUP BY os.match_id, os.market, os.selection
        """,
        match_ids + list(eligible_snap_markets),
    ) or []
    # {match_id: {(market, selection): odds}}
    odds_by_match: dict[str, dict[tuple, float]] = {}
    for r in odds_rows:
        mid = r["match_id"]
        if mid not in odds_by_match:
            odds_by_match[mid] = {}
        odds_by_match[mid][(r["market"].lower(), (r["selection"] or "").lower())] = float(r["odds"])

    # Step 4: build candidates
    # {match_id: best CandidateLeg} — one per match
    best_by_match: dict[str, CandidateLeg] = {}
    for spec in _MARKET_SPEC:
        acca_key, pred_key, snap_market, snap_sel, prob_field = spec
        if acca_key not in eligible:
            continue
        for mid in match_ids:
            prob = probs_by_match.get(mid, {}).get(pred_key)
            if prob is None:
                continue
            odds = odds_by_match.get(mid, {}).get((snap_market.lower(), snap_sel.lower()))
            if not odds or odds <= 1.0:
                continue
            edge = prob * odds - 1
            if edge < 0.05:
                continue
            if not (1.40 <= odds <= 2.50):
                continue
            leg = CandidateLeg(
                bet_id="",           # no source bet — scanned from predictions
                match_id=mid,
                market=acca_key,
                selection=snap_sel,
                odds=odds,
                prob=prob,
                edge=edge,
                bot_source=f"scan:{pred_key}",
            )
            # One candidate per match: keep the one with best edge
            existing = best_by_match.get(mid)
            if existing is None or edge > existing.edge:
                best_by_match[mid] = leg

    return list(best_by_match.values())


def _pick_legs(candidates: list[CandidateLeg], config: dict) -> list[CandidateLeg]:
    """Pick N legs from different matches. Balanced selection: prefer shorter
    odds where edge is at-or-above threshold (higher hit rate, smaller payout
    — but compound EV is the same).

    If require_ou15=True: returns [] unless at least one OU15/over leg is among
    the top-N picks. Backtest showed the entire positive ROI for N=5 comes from
    days with OU15/over in the pool (73% avg leg win rate vs 44% without it).
    """
    qualified = [
        c for c in candidates
        if c.edge >= config["min_per_leg_edge"]
        and config["min_per_leg_odds"] <= c.odds <= config["max_per_leg_odds"]
    ]
    qualified.sort(key=lambda c: (c.odds, -c.edge))
    legs: list[CandidateLeg] = []
    seen_matches: set[str] = set()
    for c in qualified:
        if c.match_id in seen_matches:
            continue
        legs.append(c)
        seen_matches.add(c.match_id)
        if len(legs) >= config["max_legs"]:
            break

    if config.get("require_ou15"):
        has_ou15 = any(
            l.market == "ou15" and l.selection.lower() == "over"
            for l in legs
        )
        if not has_ou15:
            return []

    return legs


def _get_bankroll(bot_name: str) -> float | None:
    rows = execute_query(
        "SELECT id::text AS id, current_bankroll FROM bots WHERE name = %s",
        [bot_name],
    )
    if not rows:
        return None
    return float(rows[0]["current_bankroll"])


def _get_bot_id(bot_name: str) -> str | None:
    rows = execute_query("SELECT id::text AS id FROM bots WHERE name = %s", [bot_name])
    return rows[0]["id"] if rows else None


_STRUCTURE_NAME = {
    3: "Trixie",
    4: "Yankee",
    5: "Canadian",
    6: "Heinz",
    7: "Super Heinz",
    8: "Goliath",
}


def _place_one(bot_name: str, cfg: dict, legs: list[CandidateLeg]) -> dict:
    """Place one combo or system bet for the given variant.

    Both variants share the same picks/legs (already chosen by `_pick_legs`).
    The structure determines stake allocation:
      • straight     → one row at the max-leg combined odds
      • no_singles   → one row representing the system ticket; settlement
                       enumerates sub-combos at payout time
    """
    bot_id = _get_bot_id(bot_name)
    if not bot_id:
        console.print(f"[yellow]Acca: {bot_name} not registered. Skipping.[/yellow]")
        return {"placed": False, "reason": "bot_not_registered", "bot": bot_name}

    # COMBO-DEDUP (2026-05-23): one combo per bot per UTC day. Without this,
    # running run_acca_pass twice today would duplicate every existing combo
    # — exactly the failure mode we have to clean up below. Cheap idempotency
    # check; bot table has bot_name → bot_id mapping cached.
    existing = execute_query(
        """SELECT id FROM simulated_bets
           WHERE bot_id = %s
             AND market = 'combo'
             AND DATE(pick_time AT TIME ZONE 'UTC') = (NOW() AT TIME ZONE 'UTC')::date
           LIMIT 1""",
        [bot_id],
    ) or []
    if existing:
        return {"placed": False, "reason": "already_placed_today", "bot": bot_name,
                "existing_id": str(existing[0]["id"])}

    n_legs = len(legs)
    combined_odds = math.prod(l.odds for l in legs)
    combined_prob = math.prod(l.prob for l in legs)
    combined_edge = combined_prob * combined_odds - 1

    if combined_edge < cfg["min_combined_edge"]:
        return {"placed": False, "reason": "edge_below_threshold", "bot": bot_name}
    if combined_odds > cfg["max_combined_odds"] and cfg["structure"] == "straight":
        # Cap only applies to straight (system bets distribute across smaller sub-combos)
        return {"placed": False, "reason": "odds_above_cap", "bot": bot_name}

    bankroll = _get_bankroll(bot_name) or 1000.0
    kelly = compute_kelly(combined_prob, combined_odds)
    if kelly <= 0:
        return {"placed": False, "reason": "non_positive_kelly", "bot": bot_name}
    stake = min(kelly * cfg["kelly_fraction"] * bankroll, cfg["max_stake_pct"] * bankroll)
    stake = round(stake, 2)
    if stake < cfg["min_stake"]:
        return {"placed": False, "reason": "stake_below_minimum", "bot": bot_name}

    legs_json = [
        {
            "bet_id": l.bet_id,
            "match_id": l.match_id,
            "market": l.market,
            "selection": l.selection,
            "odds": l.odds,
            "prob": l.prob,
            "bot_source": l.bot_source,
        }
        for l in legs
    ]

    structure = cfg["structure"]
    n_subbets = _subcombo_count(n_legs, structure)

    if structure == "straight":
        selection_label = f"{n_legs}-leg"
        system_type = None
        display_odds = combined_odds
        log_prefix = "ACCA"
    elif structure == "no_singles":
        struct_name = _STRUCTURE_NAME.get(n_legs, f"{n_legs}-pick system")
        selection_label = f"{struct_name} ({n_legs} picks, {n_subbets} sub-combos)"
        system_type = "no_singles"
        display_odds = combined_odds
        log_prefix = f"SYSTEM ({struct_name})"
    elif structure == "fours_up":
        # For N=5: 5 four-folds + 1 five-fold = 6 tickets. Tolerates one failure.
        selection_label = f"fours_up ({n_legs} picks, {n_subbets} sub-combos)"
        system_type = "fours_up"
        display_odds = combined_odds
        log_prefix = f"FOURS-UP ({n_legs}-pick)"
    else:
        return {"placed": False, "reason": f"unknown_structure_{structure}", "bot": bot_name}

    console.print(
        f"[bold green]{log_prefix}: {bot_name} | {n_legs} legs | {n_subbets} sub-bet(s) | "
        f"combined odds {combined_odds:.2f} | edge {combined_edge:+.1%} | stake €{stake:.2f}[/bold green]"
    )

    execute_write(
        """
        INSERT INTO simulated_bets (
            bot_id, match_id, market, selection,
            odds_at_pick, model_probability, calibrated_prob,
            edge_percent,
            stake, result, combo_legs, combo_size, system_type,
            reasoning
        ) VALUES (%s, %s, 'combo', %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s)
        """,
        [
            bot_id,
            legs[0].match_id,
            selection_label,
            display_odds,
            combined_prob,
            combined_prob,
            round(combined_edge, 4),
            stake,
            json.dumps(legs_json),
            n_legs,
            system_type,
            json.dumps({
                "strategy": bot_name,
                "structure": structure,
                "n_subbets": n_subbets,
                "combined_edge": round(combined_edge, 4),
                "kelly": round(kelly, 4),
                "leg_summary": [f"{l.bot_source}:{l.market}/{l.selection}" for l in legs],
            }),
        ],
    )
    return {
        "placed": True, "bot": bot_name, "structure": structure,
        "n_legs": n_legs, "n_subbets": n_subbets, "stake": stake,
        "combined_odds": round(combined_odds, 4), "combined_edge": round(combined_edge, 4),
    }


# ACCA-LEG-SHADOW (2026-05-25): map our internal acca market keys to the
# canonical odds_snapshots market labels used by singles bots — so shadow_bets
# rows group cleanly with bot_ou25_global / bot_btts_all / etc. in slice
# analysis. Settlement's _normalize_bet_market handles both forms but reports
# and `slice_live_validate.py` key off the stored value as-is.
_SHADOW_MARKET_MAP = {
    "ou15": "over_under_15",
    "ou25": "over_under_25",
    "ou35": "over_under_35",
    "btts": "btts",
}


def _write_legs_as_shadow(legs_by_variant: dict) -> int:
    """ACCA-LEG-SHADOW — write each picked leg as a shadow_bets row attributed
    to virtual bot `bot_acca_leg_shadow`.

    Treats each leg as a hypothetical single bet at the acca leg odds (MAX
    across accessible books). After settlement, lets us answer: "if we widened
    the singles bots' filters to include these matches, would the singles have
    been +EV?". Acca uses looser filters than singles (no Platt calibration,
    no Pinnacle-disagreement veto, no sharp-consensus gate) — so this is a
    controlled way to gather settled evidence before touching singles config.

    Dedupes across variants by (match_id, market, selection): if multiple acca
    variants pick the same leg, only one shadow_bet row is written.

    One shadow_run_id per acca pass. `shadow_cohort='morning'` because acca
    runs from `run_morning(cohort='morning')` only. Settlement is wired via
    the existing `_settle_pending_shadow_bets` pass — no extra work needed.
    """
    import uuid as _uuid
    from workers.api_clients.supabase_client import bulk_store_shadow_bets

    shadow_bot_id = _get_bot_id("bot_acca_leg_shadow")
    if not shadow_bot_id:
        console.print("[dim]acca leg shadow: bot_acca_leg_shadow not registered — skip[/dim]")
        return 0

    seen: set = set()
    unique_legs: list = []
    for legs in legs_by_variant.values():
        for leg in legs:
            key = (leg.match_id, leg.market, leg.selection)
            if key in seen:
                continue
            seen.add(key)
            unique_legs.append(leg)
    if not unique_legs:
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for leg in unique_legs:
        rows.append({
            "bot_id": shadow_bot_id,
            "match_id": leg.match_id,
            "market": _SHADOW_MARKET_MAP.get(leg.market, leg.market),
            "selection": leg.selection,
            "odds": leg.odds,
            "model_prob": leg.prob,
            "calibrated_prob": leg.prob,  # acca skips Platt — store raw prob in both slots
            "edge": round(leg.edge, 4),
            "kelly_fraction": None,
            "placed_at": now_iso,
            "timing_cohort": "all",
            "recommended_bookmaker": None,
        })

    shadow_run_id = str(_uuid.uuid4())
    try:
        n = bulk_store_shadow_bets(rows, shadow_run_id, "morning")
    except Exception as e:
        console.print(f"[yellow]acca leg shadow: write failed ({e})[/yellow]")
        return 0
    console.print(f"[dim]acca leg shadow: {n} unique leg(s) written (run_id={shadow_run_id[:8]})[/dim]")
    return n


def run_acca_pass(dry_run: bool = False) -> dict:
    """Run the acca-style bots for the day. Each variant uses its own leg pool
    based on its `market_whitelist`:
      • bot_acca_value / bot_combo_system  → market_whitelist=None → all eligible markets
      • bot_acca_proven / bot_combo_proven_system → PROVEN_MARKETS_WHITELIST

    ACCA-REDESIGN (2026-05-20): leg pool now comes from _scan_todays_candidates()
    (direct DB scan of predictions + odds_snapshots) rather than _fetch_todays_singles()
    (read from simulated_bets of other bots). Variants sharing the same market_whitelist
    get identical legs — clean comparison between straight vs system structures.

    Returns dict with per-variant `placed/reason`.
    """
    # Cache scan results by (market_whitelist, require_ou15, match_filter).
    # COMBO-FIX-1 (2026-05-23): require_ou15 forces ou15 into the candidate
    # pool even when market_whitelist excludes it, so the proven variants
    # can actually satisfy their gate.
    # COMBO-NEW (2026-05-23): coolbet_only restricts the candidate pool to
    # matches whose league has a Coolbet mapping (see _coolbet_match_ids).
    scan_cache: dict = {}

    def _legs_for(cfg: dict) -> list[CandidateLeg]:
        mwl = cfg.get("market_whitelist")
        always_include = frozenset({"ou15"}) if cfg.get("require_ou15") else frozenset()
        coolbet_only = cfg.get("coolbet_only", False)
        cache_key = (
            "ALL" if mwl is None else tuple(sorted(mwl)),
            tuple(sorted(always_include)),
            coolbet_only,
        )
        if cache_key not in scan_cache:
            match_filter = _coolbet_match_ids() if coolbet_only else None
            candidates = _scan_todays_candidates(
                mwl,
                always_include_markets=always_include,
                match_id_filter=match_filter,
            )
            scan_cache[cache_key] = _pick_legs(candidates, cfg)
        return scan_cache[cache_key]

    if dry_run:
        out_legs = {name: [l.__dict__ for l in _legs_for(cfg)] for name, cfg in ACCA_VARIANTS.items()}
        return {"dry_run": True, "legs_per_variant": out_legs}

    results = {}
    legs_by_variant: dict = {}
    for bot_name, cfg in ACCA_VARIANTS.items():
        legs = _legs_for(cfg)
        legs_by_variant[bot_name] = legs
        if len(legs) < cfg["min_legs"]:
            console.print(f"[dim]Acca {bot_name}: only {len(legs)} qualifying legs (need ≥{cfg['min_legs']}). Skipping.[/dim]")
            results[bot_name] = {"placed": False, "reason": "not_enough_legs", "n_legs": len(legs)}
            continue
        results[bot_name] = _place_one(bot_name, cfg, legs)

    # ACCA-LEG-SHADOW (2026-05-25): write each picked leg as a hypothetical
    # single for later ROI evaluation. Never blocks placement; non-critical.
    try:
        shadow_n = _write_legs_as_shadow(legs_by_variant)
    except Exception as e:
        console.print(f"[yellow]acca leg shadow non-critical failure: {e}[/yellow]")
        shadow_n = 0
    return {"variants": results, "leg_shadow_written": shadow_n}
