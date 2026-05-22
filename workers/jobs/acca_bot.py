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
    eligible = market_whitelist if market_whitelist is not None else ACCA_ELIGIBLE_MARKETS

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
    # Cache scan results by market_whitelist (frozenset or None → "ALL")
    scan_cache: dict = {}

    def _legs_for(cfg: dict) -> list[CandidateLeg]:
        mwl = cfg.get("market_whitelist")
        cache_key = "ALL" if mwl is None else tuple(sorted(mwl))
        if cache_key not in scan_cache:
            candidates = _scan_todays_candidates(mwl)
            scan_cache[cache_key] = _pick_legs(candidates, cfg)
        return scan_cache[cache_key]

    if dry_run:
        out_legs = {name: [l.__dict__ for l in _legs_for(cfg)] for name, cfg in ACCA_VARIANTS.items()}
        return {"dry_run": True, "legs_per_variant": out_legs}

    results = {}
    for bot_name, cfg in ACCA_VARIANTS.items():
        legs = _legs_for(cfg)
        if len(legs) < cfg["min_legs"]:
            console.print(f"[dim]Acca {bot_name}: only {len(legs)} qualifying legs (need ≥{cfg['min_legs']}). Skipping.[/dim]")
            results[bot_name] = {"placed": False, "reason": "not_enough_legs", "n_legs": len(legs)}
            continue
        results[bot_name] = _place_one(bot_name, cfg, legs)
    return {"variants": results}
