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

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write
from workers.model.improvements import compute_kelly

console = Console()


# ── Acca bot config — keep here, not in BOTS_CONFIG, since the bot lives
#    outside the per-match BOTS_CONFIG iteration loop in daily_pipeline_v2.
ACCA_CONFIG = {
    "name":               "bot_acca_value",
    "min_legs":           3,
    "max_legs":           5,
    "min_per_leg_edge":   0.05,    # Each leg needs ≥5% EV (matches min single-bet threshold)
    "max_per_leg_odds":   2.50,    # Cap per-leg odds — balanced selection bias toward
                                    # higher hit rate over bigger payout (backtest insight)
    "min_per_leg_odds":   1.40,    # Skip near-certain legs that add nothing to payout
    "min_combined_edge":  0.10,    # Combo must show ≥10% combined edge (compounded)
    "max_combined_odds":  50.0,    # Cap to avoid absurd-odds combos (variance protection)
    "kelly_fraction":     0.05,    # 1/3 of singles' Kelly fraction — combos = higher variance
    "max_stake_pct":      0.005,   # Cap stake at 0.5% bankroll (vs singles' 1%)
    "min_stake":          1.0,
}


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


def _fetch_todays_singles() -> list[CandidateLeg]:
    """Pull today's pending pre-match singles. Excludes:
      • Inplay bets (different cohort / not combinable with pre-match)
      • Combo bets themselves (no nested combos)
      • Bets that already settled (we want forward-looking)
    """
    today_utc = datetime.now(timezone.utc).date().isoformat()
    rows = execute_query(
        """
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
          AND b.name <> %s
        """,
        [today_utc, ACCA_CONFIG["name"]],
    ) or []
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


def run_acca_pass(dry_run: bool = False) -> dict:
    """Generate today's combo bet if criteria are met. Returns a dict with
    `placed` (bool), `legs`, `combined_odds`, `combined_edge`, `stake`, etc.
    Called from daily_pipeline_v2.run_morning after singles are placed."""
    cfg = ACCA_CONFIG
    bot_id = _get_bot_id(cfg["name"])
    if not bot_id:
        console.print(f"[yellow]Acca bot: {cfg['name']} not registered — migration 108 not applied?[/yellow]")
        return {"placed": False, "reason": "bot_not_registered"}

    candidates = _fetch_todays_singles()
    legs = _pick_legs(candidates, cfg)

    if len(legs) < cfg["min_legs"]:
        console.print(f"[dim]Acca bot: only {len(legs)} qualifying legs today (need ≥{cfg['min_legs']}). Skipping.[/dim]")
        return {"placed": False, "reason": "not_enough_legs", "legs": len(legs)}

    combined_odds = math.prod(l.odds for l in legs)
    combined_prob = math.prod(l.prob for l in legs)
    combined_edge = combined_prob * combined_odds - 1

    if combined_edge < cfg["min_combined_edge"]:
        console.print(f"[dim]Acca bot: combined edge {combined_edge:.2%} below threshold {cfg['min_combined_edge']:.2%}. Skipping.[/dim]")
        return {"placed": False, "reason": "edge_below_threshold", "combined_edge": combined_edge}

    if combined_odds > cfg["max_combined_odds"]:
        console.print(f"[dim]Acca bot: combined odds {combined_odds:.2f} above cap {cfg['max_combined_odds']}. Skipping.[/dim]")
        return {"placed": False, "reason": "odds_above_cap", "combined_odds": combined_odds}

    bankroll = _get_bankroll(cfg["name"]) or 1000.0
    kelly = compute_kelly(combined_prob, combined_odds)
    if kelly <= 0:
        return {"placed": False, "reason": "non_positive_kelly"}
    stake = min(kelly * cfg["kelly_fraction"] * bankroll, cfg["max_stake_pct"] * bankroll)
    stake = round(stake, 2)
    if stake < cfg["min_stake"]:
        return {"placed": False, "reason": "stake_below_minimum", "stake": stake}

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

    summary = {
        "placed": True,
        "n_legs": len(legs),
        "combined_odds": round(combined_odds, 4),
        "combined_prob": round(combined_prob, 4),
        "combined_edge": round(combined_edge, 4),
        "stake": stake,
        "legs": legs_json,
    }

    console.print(
        f"[bold green]ACCA: {len(legs)} legs @ combined odds {combined_odds:.2f}, "
        f"edge {combined_edge:+.1%}, stake €{stake:.2f}[/bold green]"
    )
    for l in legs:
        console.print(f"  • {l.bot_source}: {l.market}/{l.selection} @ {l.odds:.2f} (edge {l.edge:+.1%})")

    if dry_run:
        return {**summary, "dry_run": True}

    # Store the combo bet. Use first leg's match_id as placeholder (settlement
    # uses combo_legs JSON for actual outcomes).
    execute_write(
        """
        INSERT INTO simulated_bets (
            bot_id, match_id, market, selection,
            odds_at_pick, model_probability, calibrated_prob,
            stake, result, combo_legs, combo_size,
            reasoning
        ) VALUES (%s, %s, 'combo', %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
        """,
        [
            bot_id,
            legs[0].match_id,
            f"{len(legs)}-leg",
            combined_odds,
            combined_prob,
            combined_prob,
            stake,
            json.dumps(legs_json),
            len(legs),
            json.dumps({
                "strategy": "bot_acca_value",
                "combined_edge": round(combined_edge, 4),
                "kelly": round(kelly, 4),
                "leg_summary": [f"{l.bot_source}:{l.market}/{l.selection}" for l in legs],
            }),
        ],
    )
    return summary
