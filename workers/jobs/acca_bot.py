"""
COMBO-RESEARCH-PHASE-D — paper acca bot.

Runs after the morning betting pipeline. Reads today's freshly placed
pending bets, picks the top-edge ones (one per match, independence enforced),
and stores a single combo bet covering 3-5 legs.

Why this design (post-processor vs deeply integrated):
  • Other bots run their own candidate generation; copying that logic into a
    combo-aware variant would multiply the per-match-per-bot loop's complexity.
  • Reading already-placed bets gives us a clean, deduplicated single-bet menu
    to combine from.
  • Independence (one match per leg) is enforced by GROUP BY match_id with
    edge-descending selection.

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
from itertools import combinations

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write
from workers.model.improvements import compute_kelly

console = Console()


# ── Acca bot variants. Both share picking logic (same N legs/day) but place
#    their stake differently — straight = one max-leg combo, no_singles =
#    spread across all sub-combos of size 2..N (Trixie / Yankee / Canadian /
#    Heinz depending on N). Running both as paper bots lets us compare
#    variance-reduction value of system bets vs straight EV per €.
# Whitelist of "proven" source bots — used by the bot_acca_proven /
# bot_combo_proven_system variants. Selection criteria (decided 2026-05-18
# after the expanded historical backtest):
#   • bot_ou15_defensive — +86% backtest / +47% live / +18% CLV (strongest dual)
#   • bot_ou35_attacking — +27% backtest (deep historical edge in OU 3.5)
#   • bot_v10_all        — -56% raw / +30% live / +22% CLV (filter-stack wins)
#   • bot_ou25_global    — -2% raw / +29% live / +6.5% CLV
#   • bot_ah_away_dog    — +45% live / +8% CLV (AH not in backtest scope)
#   • bot_btts_all       — flat raw / +14% live / +4.4% CLV (volume contributor)
# Other bots excluded — either raw-pessimistic without live filter-stack
# benefit, or insufficient sample to trust.
PROVEN_BOTS_WHITELIST = {
    "bot_ou15_defensive",
    "bot_ou35_attacking",
    "bot_v10_all",
    "bot_ou25_global",
    "bot_ah_away_dog",
    "bot_btts_all",
}


ACCA_VARIANTS = {
    "bot_acca_value": {
        "structure":          "straight",   # one combo at max-leg N
        "bot_whitelist":      None,         # None = all bots
        "min_legs":           3,
        "max_legs":           5,
        "min_per_leg_edge":   0.05,
        "max_per_leg_odds":   2.50,
        "min_per_leg_odds":   1.40,
        "min_combined_edge":  0.10,
        "max_combined_odds":  50.0,
        "kelly_fraction":     0.05,
        "max_stake_pct":      0.005,
        "min_stake":          1.0,
    },
    "bot_combo_system": {
        "structure":          "no_singles",  # all sub-combos of size 2..N
        "bot_whitelist":      None,
        "min_legs":           3,
        "max_legs":           5,
        "min_per_leg_edge":   0.05,
        "max_per_leg_odds":   2.50,
        "min_per_leg_odds":   1.40,
        "min_combined_edge":  0.10,
        "max_combined_odds":  50.0,
        "kelly_fraction":     0.05,
        "max_stake_pct":      0.005,
        # System bets deploy more total stake (N_subcombos × per_sub). At N=5
        # that's 26 sub-combos. To keep daily-budget comparable to the straight
        # variant, the per-sub stake is total_stake / num_sub_combos.
        "min_stake":          1.0,
    },
    # COMBO-PROVEN (2026-05-18): same picking + structure logic, restricted
    # to legs from PROVEN_BOTS_WHITELIST. Tests whether the "good legs only"
    # combo strategy holds up live.
    "bot_acca_proven": {
        "structure":          "straight",
        "bot_whitelist":      PROVEN_BOTS_WHITELIST,
        "min_legs":           2,   # fewer source bots = some days only 2 legs available
        "max_legs":           5,
        "min_per_leg_edge":   0.05,
        "max_per_leg_odds":   2.50,
        "min_per_leg_odds":   1.40,
        "min_combined_edge":  0.10,
        "max_combined_odds":  50.0,
        "kelly_fraction":     0.05,
        "max_stake_pct":      0.005,
        "min_stake":          1.0,
    },
    "bot_combo_proven_system": {
        "structure":          "no_singles",
        "bot_whitelist":      PROVEN_BOTS_WHITELIST,
        "min_legs":           2,
        "max_legs":           5,
        "min_per_leg_edge":   0.05,
        "max_per_leg_odds":   2.50,
        "min_per_leg_odds":   1.40,
        "min_combined_edge":  0.10,
        "max_combined_odds":  50.0,
        "kelly_fraction":     0.05,
        "max_stake_pct":      0.005,
        "min_stake":          1.0,
    },
}


# Back-compat alias for existing callers; equals the straight variant config.
ACCA_CONFIG = ACCA_VARIANTS["bot_acca_value"]


def _subcombo_count(n_legs: int, structure: str) -> int:
    """Number of sub-bets a structure produces for N picks."""
    if structure == "straight":
        return 1
    if structure == "no_singles":
        # All combos of size 2..N
        return sum(math.comb(n_legs, k) for k in range(2, n_legs + 1))
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


def _fetch_todays_singles(whitelist: set | None = None) -> list[CandidateLeg]:
    """Pull today's pending pre-match singles. Excludes:
      • Inplay bets (different cohort / not combinable with pre-match)
      • Combo bets themselves (no nested combos)
      • Bets that already settled (we want forward-looking)
      • All acca/combo bots' own bets (filter via name NOT LIKE)

    `whitelist`: optional set of bot names to restrict to (for proven variants).
    """
    today_utc = datetime.now(timezone.utc).date().isoformat()
    params = [today_utc]
    sql = """
        SELECT sb.id::text       AS bet_id,
               sb.match_id::text AS match_id,
               sb.market,
               sb.selection,
               sb.odds_at_pick   AS odds,
               COALESCE(sb.calibrated_prob, sb.model_probability) AS prob,
               b.name            AS bot_source
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.result = 'pending'
          AND sb.combo_legs IS NULL
          AND DATE(sb.pick_time AT TIME ZONE 'UTC') = %s
          AND b.name NOT LIKE 'inplay%%'
          AND b.name NOT LIKE 'bot_acca%%'
          AND b.name NOT LIKE 'bot_combo%%'
    """
    if whitelist:
        sql += " AND b.name = ANY(%s)"
        params.append(list(whitelist))
    rows = execute_query(sql, params) or []
    out: list[CandidateLeg] = []
    for r in rows:
        odds = float(r["odds"] or 0)
        prob = float(r["prob"] or 0)
        if odds <= 1.0 or prob <= 0 or prob >= 1:
            continue
        edge = prob * odds - 1
        out.append(CandidateLeg(
            bet_id=r["bet_id"], match_id=r["match_id"], market=r["market"],
            selection=r["selection"], odds=odds, prob=prob, edge=edge,
            bot_source=r["bot_source"],
        ))
    return out


def _pick_legs(candidates: list[CandidateLeg], config: dict) -> list[CandidateLeg]:
    """Pick N legs from different matches. Balanced selection: prefer shorter
    odds where edge is at-or-above threshold (higher hit rate, smaller payout
    — but compound EV is the same)."""
    qualified = [
        c for c in candidates
        if c.edge >= config["min_per_leg_edge"]
        and config["min_per_leg_odds"] <= c.odds <= config["max_per_leg_odds"]
    ]
    # Balanced selection: short-odds first, edge as tiebreaker
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
        # display_odds is informational for system bets — store the max-leg
        # combined odds (what the biggest sub-combo could pay)
        display_odds = combined_odds
        log_prefix = f"SYSTEM ({struct_name})"
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
            stake, result, combo_legs, combo_size, system_type,
            reasoning
        ) VALUES (%s, %s, 'combo', %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s)
        """,
        [
            bot_id,
            legs[0].match_id,
            selection_label,
            display_odds,
            combined_prob,
            combined_prob,
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
    based on its `bot_whitelist`:
      • bot_acca_value / bot_combo_system  → whitelist=None → all bots' legs
      • bot_acca_proven / bot_combo_proven_system → restricted to proven bots
    Variants sharing the same whitelist get identical legs (clean comparison
    between straight vs system stake distribution on the same picks).

    Returns dict with per-variant `placed/reason`.
    """
    # Cache leg pools by whitelist tuple (so each unique whitelist only queries DB once)
    leg_cache: dict = {}

    def _legs_for(cfg: dict) -> list[CandidateLeg]:
        wl = cfg.get("bot_whitelist")
        cache_key = "ALL" if wl is None else tuple(sorted(wl))
        if cache_key not in leg_cache:
            candidates = _fetch_todays_singles(wl)
            leg_cache[cache_key] = _pick_legs(candidates, cfg)
        return leg_cache[cache_key]

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
