#!/usr/bin/env python3
"""
CS2 paper-trading bot registry.

Scans cs2_upcoming_matches for value opportunities and writes
cs2_simulated_bets rows. Each bot config in BOTS_CONFIG defines its own
gates (edge floor, odds range, anomaly threshold, model sources, markets)
and runs against the same fixture pool — soccer's 16-bot pattern, applied
to CS2.

Value rule (per bot):
  - Row's model source is in cfg['sources'].
  - Bookie price is within cfg['min_odds']..cfg['max_odds'].
  - Bookie offers >= our threshold odds for the side.
  - Implied extra edge above threshold >= cfg's edge floor.
  - Plus consensus, anomaly, and divergence gates (per cfg).

Markets supported: match_winner (1x2), atleast1map (BO3/BO5 only).

Settlement happens in cs2_settlement.py — that job populates cs2_results,
which we join to here to mark won/lost/pnl.

Usage:
    python3 scripts/esports/cs2_bot.py                    # dry run, all bots
    python3 scripts/esports/cs2_bot.py --record           # write simulated_bets, all bots
    python3 scripts/esports/cs2_bot.py --bot bot_cs2_dog_v1  # one bot only
    python3 scripts/esports/cs2_bot.py --settle           # only run settlement step
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write

BOT_NAME = "bot_cs2_value_v1"   # legacy alias — registry key below is canonical
MIN_EXTRA_EDGE = 0.05   # 5% above the threshold (which already has 3% baked in)
BASE_STAKE = 1.0        # 1 unit reference (Kelly fraction is multiplied by this)
KELLY_FRACTION = 0.5    # half-Kelly — standard variance-reduced stake
KELLY_CAP = 2.0         # never wager more than 2u on a single bet
HLTV_EDGE_FLOOR = 0.03  # extra 3% required for HLTV-fallback picks (less proven)
HLTV_BASE_EDGE = 0.05   # 5% threshold edge for the hltv_v1 model

# Market consensus / outlier protection.
MIN_BOOKS_FOR_PICK = 2          # need at least 2 books to fire — single-book picks lack a sanity cross-ref
MAX_CONSENSUS_DRIFT = 0.30      # best price cannot exceed median market consensus by >30%
MAX_EXTRA_EDGE = 0.50           # cap on edge over threshold; anything bigger is model error or stale data
MAX_MODEL_VS_CONSENSUS_PP = 0.15  # our_prob vs median consensus implied prob must be within 15pp

# Anomaly guard: if our model probability and the implied probability from the
# bookmaker offering the "value" diverge by more than this in absolute terms,
# the gap is more likely a data bug than a real edge — suppress the bet.
# Calibrated on real-money soft-book mistakes: 25pp ≈ 4σ in the calibrated model.
MAX_PROB_DIVERGENCE = 0.25


# ─────────────────────────── BOT REGISTRY ───────────────────────────
#
# Each bot is a config dict layered on BASE_GATES. Bots share the same scan
# pool (cs2_upcoming_matches × model coverage) and the same picker function;
# only the gates differ. This mirrors soccer's BOTS_CONFIG in
# workers/jobs/daily_pipeline_v2.py.
#
# Keys:
#   sources      — model sources eligible (e.g. ("elo+pq_v1",) or ("v8","v7"))
#   markets      — markets to scan (subset of ("match_winner","atleast1map"))
#   min_extra_edge — required edge above threshold for ELO+PQ rows
#   hltv_edge_floor / hltv_base_edge — same, but for HLTV-fallback rows
#   max_extra_edge — kill bets where edge is implausibly large (data bug)
#   min_books_for_pick / max_consensus_drift / max_model_vs_consensus_pp — consensus gates
#   max_prob_divergence — anomaly kill switch (model vs bookie implied)
#   min_odds / max_odds — odds-range filter (dog vs fav variants)
#   kelly_fraction / kelly_cap — stake sizing
#   enabled — quick on/off without deleting the row
BASE_GATES = {
    "min_extra_edge": MIN_EXTRA_EDGE,
    "hltv_edge_floor": HLTV_EDGE_FLOOR,
    "hltv_base_edge": HLTV_BASE_EDGE,
    "max_extra_edge": MAX_EXTRA_EDGE,
    "min_books_for_pick": MIN_BOOKS_FOR_PICK,
    "max_consensus_drift": MAX_CONSENSUS_DRIFT,
    "max_model_vs_consensus_pp": MAX_MODEL_VS_CONSENSUS_PP,
    "max_prob_divergence": MAX_PROB_DIVERGENCE,
    "min_odds": 1.01,
    "max_odds": 100.0,
    "kelly_fraction": KELLY_FRACTION,
    "kelly_cap": KELLY_CAP,
    "markets": ("match_winner", "atleast1map"),
    "enabled": True,
}


def _cfg(name: str, sources: tuple, **overrides) -> dict:
    """Build a bot config: BASE_GATES + sources + per-bot overrides."""
    return {**BASE_GATES, "name": name, "sources": sources, **overrides}


BOTS_CONFIG: dict[str, dict] = {
    # ── Baseline: value strategy split by model source for attribution ──
    # These four mirror the 4 bots that have been firing since 2026-06-08.
    # Same gates, different model. bot_name keeps the model suffix so the
    # bots table / weekly review track each model's live ROI independently.
    "bot_cs2_value_v1": _cfg("bot_cs2_value_v1", ("elo+pq_v1",)),
    "bot_cs2_v8":       _cfg("bot_cs2_v8",       ("v8",)),
    "bot_cs2_v7":       _cfg("bot_cs2_v7",       ("v7",)),
    "bot_cs2_hltv_v1":  _cfg("bot_cs2_hltv_v1",  ("hltv_v1",)),

    # ── Diversification (added 2026-06-25, mirrors soccer's bot variants) ──
    # Goal: lift CS2 paper-bet volume from ~2/day toward soccer's tempo by
    # running multiple strategies on the same fixture pool.

    # Aggressive: lower edge floor + tighter anomaly guard. Catches edges
    # that the conservative bot lets through. Excludes hltv_v1 (the
    # weakest model — at low edges, hltv_v1 noise overwhelms signal).
    "bot_cs2_aggressive_v1": _cfg(
        "bot_cs2_aggressive_v1",
        ("elo+pq_v1", "v8", "v7"),
        min_extra_edge=0.03,
        hltv_edge_floor=0.02,
        max_prob_divergence=0.20,
    ),

    # Dog: only fires on underdog at decent odds. High variance, lower
    # win-rate, longer payouts — soccer's bot_ah_away_dog analog.
    # match_winner only — map-handicap underdog odds in CS2 are too noisy.
    "bot_cs2_dog_v1": _cfg(
        "bot_cs2_dog_v1",
        ("elo+pq_v1", "v8"),
        markets=("match_winner",),
        min_odds=2.20,
        min_extra_edge=0.04,
    ),

    # Favourite: only fires on shortest prices. Low variance, lower payout
    # per bet but higher hit rate. Soccer's bot_ah_home_fav analog.
    "bot_cs2_fav_v1": _cfg(
        "bot_cs2_fav_v1",
        ("elo+pq_v1", "v8"),
        markets=("match_winner",),
        max_odds=1.70,
        min_extra_edge=0.04,
    ),
}


def _load_open_matches() -> list[dict]:
    """Pre-kickoff matches with model coverage.

    Returns rows from BOTH:
      1. ELO+PQ-covered matches (has_elo_history=TRUE) — primary model
      2. HLTV-fallback matches — ELO gated, but BOTH teams in HLTV top-248
         and a cs2_predictions row with model_version='hltv_v1' exists.

    The 'source' column tells the bot which thresholds to apply.
    """
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=72)).isoformat()  # CS2 fixtures locked 2-3d ahead

    elo_rows = execute_query("""
        SELECT id, bo3gg_id, team1, team2, kickoff_time, best_of,
               win_prob1, win_prob2,
               fair_odds1, fair_odds2, threshold_odds1, threshold_odds2,
               fair_odds_map1, fair_odds_map2, threshold_map1, threshold_map2,
               bookie_odds1, bookie_odds2,
               coolbet_odds1, coolbet_odds2,
               coolbet_odds_map1, coolbet_odds_map2,
               pinnacle_odds1, pinnacle_odds2,
               roster_change1, roster_change2,
               'elo+pq_v1' AS source
        FROM cs2_upcoming_matches
        WHERE has_elo_history = TRUE
          AND kickoff_time >= %s
          AND kickoff_time <= %s
          AND threshold_odds1 IS NOT NULL
    """, (now.isoformat(), horizon))

    # HLTV fallback: ELO is gated (threshold_odds1 NULL), but the parallel
    # hltv_v1 model has a prediction for this match. Use the latest hltv_v1
    # row per (bo3gg_id) joined back to current upcoming_matches.
    hltv_rows = execute_query("""
        SELECT u.id, u.bo3gg_id, u.team1, u.team2, u.kickoff_time, u.best_of,
               h.win_prob1, h.win_prob2,
               h.fair_odds1, h.fair_odds2,
               NULL AS threshold_odds1, NULL AS threshold_odds2,
               NULL AS fair_odds_map1, NULL AS fair_odds_map2,
               NULL AS threshold_map1, NULL AS threshold_map2,
               u.bookie_odds1, u.bookie_odds2,
               u.coolbet_odds1, u.coolbet_odds2,
               u.coolbet_odds_map1, u.coolbet_odds_map2,
               u.pinnacle_odds1, u.pinnacle_odds2,
               u.roster_change1, u.roster_change2,
               COALESCE(h.source, 'hltv_v1') AS source
        FROM cs2_upcoming_matches u
        JOIN LATERAL (
            -- Prefer v8 (stacking + kd_diff, AUC 0.703), then v7
            -- (no kd, AUC 0.697), else hltv_v1 (rank-only, AUC 0.673).
            SELECT win_prob1, win_prob2, fair_odds1, fair_odds2, model_version AS source
            FROM cs2_predictions p
            WHERE p.bo3gg_id = u.bo3gg_id
              AND p.model_version IN ('v8', 'v7', 'hltv_v1')
            ORDER BY CASE p.model_version
                       WHEN 'v8' THEN 0
                       WHEN 'v7' THEN 1
                       ELSE 2
                     END,
                     p.scan_time DESC
            LIMIT 1
        ) h ON TRUE
        WHERE u.threshold_odds1 IS NULL           -- ELO gated
          AND u.kickoff_time >= %s
          AND u.kickoff_time <= %s
          AND u.bo3gg_id IS NOT NULL
    """, (now.isoformat(), horizon))

    return list(elo_rows) + list(hltv_rows)


def _is_anomaly(side_prob: float | None, bookie_odds: float, threshold: float = MAX_PROB_DIVERGENCE) -> bool:
    """True if our model's win prob diverges from the bookie's implied prob by
    more than `threshold` in absolute terms — likely model bug or stale odds."""
    if side_prob is None or bookie_odds <= 1.0:
        return False
    implied = 1.0 / bookie_odds   # raw implied, ignores vig (still good enough)
    return abs(side_prob - implied) > threshold


def market_consensus(prices: list[tuple[str, float]]) -> tuple[float, float] | None:
    """Return (consensus_implied_prob, consensus_odds) from non-empty (bookie, odds) list.

    Median of raw 1/odds across the books. Robust to one outlier when ≥3 books,
    and acts as a sanity average for 2 books. None if list is empty.
    """
    if not prices:
        return None
    implied = sorted(1.0 / odds for _, odds in prices if odds > 1.0)
    if not implied:
        return None
    n = len(implied)
    cons = (implied[n // 2 - 1] + implied[n // 2]) / 2 if n % 2 == 0 else implied[n // 2]
    if cons <= 0:
        return None
    return cons, 1.0 / cons


def kelly_stake(side_prob: float | None, bookie_odds: float,
                fraction: float = KELLY_FRACTION, cap: float = KELLY_CAP) -> float:
    """Half-Kelly stake. Returns BASE_STAKE * `fraction`-Kelly, capped at `cap`.

    Kelly formula: f* = (b*p - q) / b, where b = decimal_odds - 1, p = win prob, q = 1 - p.
    Falls back to 1.0 if probability unknown (preserves prior behavior).
    """
    if side_prob is None or bookie_odds <= 1.0:
        return BASE_STAKE
    b = bookie_odds - 1.0
    p = float(side_prob)
    q = 1.0 - p
    full = (b * p - q) / b
    if full <= 0:
        return 0.0                      # caller should skip
    return min(cap, round(BASE_STAKE * fraction * full, 4))


def _eligible_books(row: dict, sidekey: str, market: str = "match_winner") -> list[tuple[str, float]]:
    """Bookies actually quoting odds for one side. (bookie_name, decimal_odds).

    market='match_winner' (default) → returns the head-to-head odds from
    bo3gg/coolbet/pinnacle.

    market='atleast1map' → returns ≥1-map odds. Today only Coolbet
    populates this (cs2_coolbet_scanner mig 250); other bookies' ≥1-map
    columns don't exist yet. Empty list = no bookie priced this market
    → bot can't compute edge → skips the side."""
    if market == "atleast1map":
        candidates = [
            ("coolbet", row.get(f"coolbet_odds_map{sidekey}")),
        ]
    else:
        candidates = [
            ("bo3gg",    row[f"bookie_odds{sidekey}"]),
            ("coolbet",  row[f"coolbet_odds{sidekey}"]),
            ("pinnacle", row[f"pinnacle_odds{sidekey}"]),
        ]
    return [(b, float(o)) for b, o in candidates if o is not None and float(o) > 1.0]


def _consider_side(*, source: str, side: str, team_name: str, prices: list[tuple[str, float]],
                   fair: float | None, thr: float | None, prob: float | None,
                   min_extra: float, market: str = "match_winner",
                   min_books: int = MIN_BOOKS_FOR_PICK,
                   max_drift: float = MAX_CONSENSUS_DRIFT,
                   max_extra: float = MAX_EXTRA_EDGE,
                   max_model_vs_consensus_pp: float = MAX_MODEL_VS_CONSENSUS_PP,
                   max_prob_divergence: float = MAX_PROB_DIVERGENCE,
                   min_odds: float = 1.01,
                   max_odds: float = 100.0,
                   kelly_fraction: float = KELLY_FRACTION,
                   kelly_cap: float = KELLY_CAP) -> dict | None:
    """Apply all gates for one (match, market, side) tuple and return a single
    best-bookie pick, or None.

    Gates (in order, fail-fast):
      1. Need ≥ min_books quoting (sanity cross-ref).
      2. Compute market consensus median.
      3. Best price cannot be > max_drift above consensus (stale outlier).
      4. Best price must be within [min_odds, max_odds] — dog/fav filter.
      5. Best price must be ≥ model threshold.
      6. Edge above threshold must be in [min_extra, max_extra].
      7. |our_prob − consensus_implied| ≤ max_model_vs_consensus_pp.
      8. Anomaly guard: |our_prob − bookie_implied| ≤ max_prob_divergence.
      9. Kelly stake > 0.
    """
    if not prices or thr is None or fair is None:
        return None
    if len(prices) < min_books:
        return None
    cons = market_consensus(prices)
    if cons is None:
        return None
    consensus_prob, consensus_odds = cons

    best_bookie, best_odds = max(prices, key=lambda x: x[1])

    if best_odds > consensus_odds * (1 + max_drift):
        return None     # stale-odds outlier
    if best_odds < min_odds or best_odds > max_odds:
        return None     # odds-range filter (dog/fav variants)
    if best_odds < thr:
        return None     # below threshold
    extra = (best_odds - thr) / thr
    if extra < min_extra:
        return None
    # Reject crazy edges — almost always a sign of model bug or stale data.
    # See aAa vs RUSTEC: HLTV sigmoid gives aAa 39% on 3 pts vs 8 pts, but both
    # bookies agree aAa is ~19% (consensus). Edge above 50% over threshold is
    # never a real-money opportunity worth firing on.
    if extra > max_extra:
        return None
    # Tighter divergence vs market CONSENSUS (not just one bookie's implied).
    # When our probability and the market median disagree by > 15pp, the model
    # is the suspect — market has more bookmakers' worth of consensus.
    if prob is not None and abs(float(prob) - consensus_prob) > max_model_vs_consensus_pp:
        return None
    if _is_anomaly(float(prob) if prob is not None else None, best_odds, max_prob_divergence):
        return None
    stake = kelly_stake(float(prob) if prob is not None else None, best_odds,
                       fraction=kelly_fraction, cap=kelly_cap)
    if stake <= 0:
        return None

    return {
        "side": side, "team": team_name, "market": market,
        "bookie": best_bookie, "odds": best_odds, "fair": float(fair),
        "thr": float(thr), "edge": extra, "stake": stake, "source": source,
        "consensus_prob": consensus_prob, "n_books": len(prices),
    }


def _scan_one(row: dict, cfg: dict) -> list[dict]:
    """One pick at most per (match, market, side) — at the best-priced bookie.

    Returns [] when row's source is not eligible for this bot config.
    """
    source = row.get("source") or "elo+pq_v1"
    if source not in cfg["sources"]:
        return []

    picks: list[dict] = []
    best_of = row["best_of"] or 3

    # ROSTER-CHANGE GATE (added 2026-06-09 after Virtus.pro vs Oxuji incident).
    # When EITHER team has a recent roster change, prior team-level stats
    # (ELO, PQ, HLTV rank) become unreliable: a brand-new player invalidates
    # the team's whole history. Sit out — match is unpriceable for us until
    # ~30d of new-roster data has accumulated. The UI badge alone wasn't
    # enough; the bot must actually skip.
    if row.get("roster_change1") or row.get("roster_change2"):
        return []

    # For HLTV-fallback rows, threshold_odds is NULL — derive from fair_odds.
    thr1 = row["threshold_odds1"]
    thr2 = row["threshold_odds2"]
    # v7 + hltv_v1 are HLTV-fallback variants — derive threshold from fair odds.
    if source in ("v7", "hltv_v1"):
        f1, f2 = row["fair_odds1"], row["fair_odds2"]
        if f1 and f2:
            thr1 = round(float(f1) * (1 - cfg["hltv_base_edge"]), 3)
            thr2 = round(float(f2) * (1 - cfg["hltv_base_edge"]), 3)
    min_extra = cfg["hltv_edge_floor"] if source in ("v7", "hltv_v1") else cfg["min_extra_edge"]

    gate_kwargs = dict(
        min_books=cfg["min_books_for_pick"],
        max_drift=cfg["max_consensus_drift"],
        max_extra=cfg["max_extra_edge"],
        max_model_vs_consensus_pp=cfg["max_model_vs_consensus_pp"],
        max_prob_divergence=cfg["max_prob_divergence"],
        min_odds=cfg["min_odds"],
        max_odds=cfg["max_odds"],
        kelly_fraction=cfg["kelly_fraction"],
        kelly_cap=cfg["kelly_cap"],
    )

    # match_winner
    if "match_winner" in cfg["markets"]:
        for side, team_name, fair, thr, prob, sidekey in [
            ("team1", row["team1"], row["fair_odds1"], thr1, row.get("win_prob1"), "1"),
            ("team2", row["team2"], row["fair_odds2"], thr2, row.get("win_prob2"), "2"),
        ]:
            prices = _eligible_books(row, sidekey)
            pick = _consider_side(source=source, side=side, team_name=team_name,
                                  prices=prices, fair=fair, thr=thr, prob=prob,
                                  min_extra=min_extra, market="match_winner",
                                  **gate_kwargs)
            if pick:
                picks.append(pick)

    # atleast1map (BO3/5 only) — same shape. Use the per-market odds
    # column (coolbet_odds_map*) instead of match-winner odds — otherwise
    # the bot evaluates a Match Result price as if it were a Map Handicap
    # price, which is a category error.
    if "atleast1map" in cfg["markets"] and best_of >= 3:
        for side, team_name, fair, thr, sidekey in [
            ("team1", row["team1"], row["fair_odds_map1"], row["threshold_map1"], "1"),
            ("team2", row["team2"], row["fair_odds_map2"], row["threshold_map2"], "2"),
        ]:
            prices = _eligible_books(row, sidekey, market="atleast1map")
            # No model prob for ≥1map → anomaly guard is a no-op
            pick = _consider_side(source=source, side=side, team_name=team_name,
                                  prices=prices, fair=fair, thr=thr, prob=None,
                                  min_extra=cfg["min_extra_edge"], market="atleast1map",
                                  **gate_kwargs)
            if pick:
                pick["stake"] = BASE_STAKE   # no Kelly without probability
                picks.append(pick)

    return picks


def _get_bot_bankroll(bot_name: str) -> float:
    """Read current_bankroll from the bots table (mirrors soccer convention)."""
    rows = execute_query("SELECT current_bankroll FROM bots WHERE name = %s", (bot_name,))
    if not rows:
        return 1000.0  # fallback if bot row missing
    return float(rows[0]["current_bankroll"])


# Cap a single bet at 2% of bankroll regardless of what Kelly says — match the
# soccer Kelly-cap convention.
MAX_STAKE_PCT_OF_BANKROLL = 0.02


def _stake_eur(stake_units: float, bankroll: float) -> float:
    """Translate the unit-stake (e.g. 0.05u) to euros using 1u = 1% bankroll
    convention, then cap at MAX_STAKE_PCT_OF_BANKROLL of bankroll. Returns
    rounded to cents."""
    # 1u = 1% of bankroll → stake_units * bankroll / 100
    raw_eur = stake_units * bankroll / 100.0
    capped = min(raw_eur, bankroll * MAX_STAKE_PCT_OF_BANKROLL)
    return round(max(capped, 0.0), 2)


def _write_bet(row: dict, pick: dict, cfg: dict) -> bool:
    bot_name = cfg["name"]
    bankroll = _get_bot_bankroll(bot_name)
    stake_units = pick.get("stake", BASE_STAKE)
    stake_eur = _stake_eur(stake_units, bankroll)
    res = execute_write("""
        INSERT INTO cs2_simulated_bets
            (bot_name, bo3gg_id, placed_at, kickoff_time,
             team1, team2, market, pick, bookie,
             odds_at_pick, fair_odds, threshold_odds, edge, stake,
             stake_eur, bankroll_at_pick,
             consensus_implied_prob, n_books_at_pick)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bot_name, bo3gg_id, market, pick) DO NOTHING
    """, (
        bot_name, row["bo3gg_id"], row["kickoff_time"],
        row["team1"], row["team2"],
        pick["market"], pick["team"], pick["bookie"],
        pick["odds"], pick["fair"], pick["thr"], pick["edge"],
        stake_units, stake_eur, bankroll,
        pick.get("consensus_prob"), pick.get("n_books"),
    ))
    return bool(res)


def _settle() -> int:
    """Settle open cs2_simulated_bets against cs2_results. Updates the bot's
    bankroll on each settlement so the next pick sizes off the new bankroll."""
    open_bets = execute_query("""
        SELECT b.id, b.bot_name, b.team1, b.team2, b.market, b.pick,
               b.odds_at_pick, b.stake, b.stake_eur,
               r.winner, r.score1, r.score2
        FROM cs2_simulated_bets b
        JOIN cs2_results r ON b.bo3gg_id = r.bo3gg_id
        WHERE b.result IS NULL
    """, ())

    settled = 0
    bankroll_cache: dict[str, float] = {}
    for row in open_bets:
        won = _bet_won(row)
        if won is None:
            continue
        odds = float(row["odds_at_pick"])
        stake = float(row["stake"])
        stake_eur = float(row["stake_eur"]) if row["stake_eur"] is not None else None
        result = "won" if won else "lost"
        pnl_units = round(stake * (odds - 1), 4) if won else round(-stake, 4)
        pnl_eur = None
        if stake_eur is not None:
            pnl_eur = round(stake_eur * (odds - 1), 2) if won else round(-stake_eur, 2)
        execute_write(
            """UPDATE cs2_simulated_bets
                  SET result = %s, pnl = %s, pnl_eur = %s, settled_at = NOW()
                WHERE id = %s""",
            (result, pnl_units, pnl_eur, row["id"]),
        )
        # Update bot bankroll
        bn = row["bot_name"]
        if pnl_eur is not None:
            if bn not in bankroll_cache:
                bankroll_cache[bn] = _get_bot_bankroll(bn)
            bankroll_cache[bn] += pnl_eur
        settled += 1

    # Flush bankroll updates
    for bn, new_bankroll in bankroll_cache.items():
        execute_write(
            "UPDATE bots SET current_bankroll = %s, updated_at = NOW() WHERE name = %s",
            (round(new_bankroll, 2), bn),
        )
    return settled


def _bet_won(row: dict) -> bool | None:
    winner_team = row["team1"] if row["winner"] == "team1" else row["team2"]
    if row["market"] == "match_winner":
        return row["pick"] == winner_team
    if row["market"] == "atleast1map":
        s1, s2 = row.get("score1"), row.get("score2")
        if s1 is None or s2 is None:
            return None
        team_score = s1 if row["pick"] == row["team1"] else s2
        return team_score >= 1
    return None


def _active_configs(only_name: str | None = None) -> list[dict]:
    cfgs = [c for c in BOTS_CONFIG.values() if c.get("enabled", True)]
    if only_name:
        cfgs = [c for c in cfgs if c["name"] == only_name]
        if not cfgs:
            raise SystemExit(f"unknown or disabled bot: {only_name!r} "
                             f"(known: {sorted(BOTS_CONFIG)})")
    return cfgs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write simulated_bets to DB")
    p.add_argument("--settle", action="store_true", help="Only run settlement, no scan")
    p.add_argument("--bot", default=None, help="Run a single bot by name (default: all enabled)")
    args = p.parse_args()

    if args.settle:
        print(f"\n=== CS2 BOT  settle  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
        n = _settle()
        print(f"  settled: {n} bets\n")
        return

    configs = _active_configs(args.bot)
    matches = _load_open_matches()
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
    print(f"\n=== CS2 BOTS  {len(configs)} configs  {len(matches)} matches  {ts} UTC ===")

    total_picks, total_written = 0, 0
    for cfg in configs:
        cfg_picks, cfg_written = 0, 0
        for row in matches:
            picks = _scan_one(row, cfg)
            if not picks:
                continue
            for p in picks:
                cfg_picks += 1
                tag = "  fired" if args.record else "  dry"
                src = p.get("source", "elo+pq_v1")
                cons_str = f" cons={1.0/p['consensus_prob']:.2f} (n={p.get('n_books')})" if p.get("consensus_prob") else ""
                print(f"    {tag}  [{cfg['name']:24} {src:10}]  {row['team1']:22} vs {row['team2']:22}  "
                      f"{p['market']:12} → {p['team']:18} @ {p['bookie']:8} {p['odds']:>5.2f}  "
                      f"(thr {p['thr']:.2f}, edge +{p['edge']*100:.1f}%, stake {p.get('stake', BASE_STAKE):.2f}u{cons_str})")
                if args.record:
                    if _write_bet(row, p, cfg):
                        cfg_written += 1
        total_picks += cfg_picks
        total_written += cfg_written
        if cfg_picks:
            print(f"    -- {cfg['name']}: {cfg_picks} picks, {cfg_written} written")

    print(f"\n  total picks: {total_picks}  written: {total_written}")

    if args.record:
        n = _settle()
        print(f"  settled: {n} prior open bets")
    print()


if __name__ == "__main__":
    main()
