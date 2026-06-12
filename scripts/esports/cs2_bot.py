#!/usr/bin/env python3
"""
Single CS2 value bot — bot_cs2_value_v1.

Scans cs2_upcoming_matches for value opportunities and writes one
cs2_simulated_bets row per (match, market, bookie). One bet per bookie per
side per match (UNIQUE constraint prevents re-fires on the same opportunity).

Value rule:
  - We have model coverage (has_elo_history = TRUE).
  - Bookie offers >= our threshold odds for the side.
  - Implied edge = (bookie_odds - threshold_odds) / threshold_odds >= 0.05 (5%)
    (the threshold itself already bakes in a 3% target edge, so we want at
    least an extra 5% above it before firing).

Markets supported: match_winner (1x2), atleast1map (BO3/BO5 only).

Settlement happens in cs2_settlement.py — that job populates cs2_results,
which we join to here to mark won/lost/pnl.

Usage:
    python3 scripts/esports/cs2_bot.py              # dry run, print only
    python3 scripts/esports/cs2_bot.py --record     # write simulated_bets
    python3 scripts/esports/cs2_bot.py --settle     # only run settlement step
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write

BOT_NAME = "bot_cs2_value_v1"
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


def _is_anomaly(side_prob: float | None, bookie_odds: float) -> bool:
    """True if our model's win prob diverges from the bookie's implied prob by
    more than MAX_PROB_DIVERGENCE — likely model bug or stale odds."""
    if side_prob is None or bookie_odds <= 1.0:
        return False
    implied = 1.0 / bookie_odds   # raw implied, ignores vig (still good enough)
    return abs(side_prob - implied) > MAX_PROB_DIVERGENCE


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


def kelly_stake(side_prob: float | None, bookie_odds: float) -> float:
    """Half-Kelly stake. Returns BASE_STAKE * half-Kelly fraction, capped at KELLY_CAP.

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
    return min(KELLY_CAP, round(BASE_STAKE * KELLY_FRACTION * full, 4))


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
                   min_extra: float, market: str = "match_winner") -> dict | None:
    """Apply all gates for one (match, market, side) tuple and return a single
    best-bookie pick, or None.

    Gates (in order, fail-fast):
      1. Need ≥ MIN_BOOKS_FOR_PICK quoting (sanity cross-ref).
      2. Compute market consensus median.
      3. Best price cannot be > MAX_CONSENSUS_DRIFT above consensus (stale outlier).
      4. Best price must be ≥ model threshold.
      5. Edge above threshold must be ≥ min_extra (model conviction).
      6. Existing anomaly guard: |our_prob − implied| ≤ MAX_PROB_DIVERGENCE.
      7. Kelly stake > 0.
    """
    if not prices or thr is None or fair is None:
        return None
    if len(prices) < MIN_BOOKS_FOR_PICK:
        return None
    cons = market_consensus(prices)
    if cons is None:
        return None
    consensus_prob, consensus_odds = cons

    best_bookie, best_odds = max(prices, key=lambda x: x[1])

    if best_odds > consensus_odds * (1 + MAX_CONSENSUS_DRIFT):
        return None     # stale-odds outlier
    if best_odds < thr:
        return None     # below threshold
    extra = (best_odds - thr) / thr
    if extra < min_extra:
        return None
    # Reject crazy edges — almost always a sign of model bug or stale data.
    # See aAa vs RUSTEC: HLTV sigmoid gives aAa 39% on 3 pts vs 8 pts, but both
    # bookies agree aAa is ~19% (consensus). Edge above 50% over threshold is
    # never a real-money opportunity worth firing on.
    if extra > MAX_EXTRA_EDGE:
        return None
    # Tighter divergence vs market CONSENSUS (not just one bookie's implied).
    # When our probability and the market median disagree by > 15pp, the model
    # is the suspect — market has more bookmakers' worth of consensus.
    if prob is not None and abs(float(prob) - consensus_prob) > MAX_MODEL_VS_CONSENSUS_PP:
        return None
    if _is_anomaly(float(prob) if prob is not None else None, best_odds):
        return None
    stake = kelly_stake(float(prob) if prob is not None else None, best_odds)
    if stake <= 0:
        return None

    return {
        "side": side, "team": team_name, "market": market,
        "bookie": best_bookie, "odds": best_odds, "fair": float(fair),
        "thr": float(thr), "edge": extra, "stake": stake, "source": source,
        "consensus_prob": consensus_prob, "n_books": len(prices),
    }


def _scan_one(row: dict) -> list[dict]:
    """One pick at most per (match, market, side) — at the best-priced bookie.

    Soccer pattern: pick best price across all books, fire ONE row. Multi-bookie
    info is preserved on cs2_upcoming_matches snapshots, so we don't need it
    duplicated in cs2_simulated_bets. Consensus + outlier guard prevents
    firing on a single book's stale or wrong price.
    """
    picks: list[dict] = []
    best_of = row["best_of"] or 3
    source = row.get("source") or "elo+pq_v1"

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
    # v7 is the production stacking model (AUC 0.694); hltv_v1 is the legacy
    # rank-only baseline (AUC 0.673). Both use the same edge floor since v7
    # only narrowly beats hltv_v1 on aggregate AUC.
    if source in ("v7", "hltv_v1"):
        f1, f2 = row["fair_odds1"], row["fair_odds2"]
        if f1 and f2:
            thr1 = round(float(f1) * (1 - HLTV_BASE_EDGE), 3)
            thr2 = round(float(f2) * (1 - HLTV_BASE_EDGE), 3)
    min_extra = HLTV_EDGE_FLOOR if source in ("v7", "hltv_v1") else MIN_EXTRA_EDGE

    # match_winner
    for side, team_name, fair, thr, prob, sidekey in [
        ("team1", row["team1"], row["fair_odds1"], thr1, row.get("win_prob1"), "1"),
        ("team2", row["team2"], row["fair_odds2"], thr2, row.get("win_prob2"), "2"),
    ]:
        prices = _eligible_books(row, sidekey)
        pick = _consider_side(source=source, side=side, team_name=team_name,
                              prices=prices, fair=fair, thr=thr, prob=prob,
                              min_extra=min_extra, market="match_winner")
        if pick:
            picks.append(pick)

    # atleast1map (BO3/5 only) — same shape. Use the per-market odds
    # column (coolbet_odds_map*) instead of match-winner odds — otherwise
    # the bot evaluates a Match Result price as if it were a Map Handicap
    # price, which is a category error.
    if best_of >= 3:
        for side, team_name, fair, thr, sidekey in [
            ("team1", row["team1"], row["fair_odds_map1"], row["threshold_map1"], "1"),
            ("team2", row["team2"], row["fair_odds_map2"], row["threshold_map2"], "2"),
        ]:
            prices = _eligible_books(row, sidekey, market="atleast1map")
            # No model prob for ≥1map → anomaly guard is a no-op
            pick = _consider_side(source=source, side=side, team_name=team_name,
                                  prices=prices, fair=fair, thr=thr, prob=None,
                                  min_extra=MIN_EXTRA_EDGE, market="atleast1map")
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


def _write_bet(row: dict, pick: dict) -> bool:
    # Different bot_name per source so HLTV-fallback picks track separately.
    src = pick.get("source")
    if src == "v8":
        bot_name = "bot_cs2_v8"
    elif src == "v7":
        bot_name = "bot_cs2_v7"
    elif src == "hltv_v1":
        bot_name = "bot_cs2_hltv_v1"
    else:
        bot_name = BOT_NAME
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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write simulated_bets to DB")
    p.add_argument("--settle", action="store_true", help="Only run settlement, no scan")
    args = p.parse_args()

    print(f"\n=== CS2 BOT  {BOT_NAME}  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    if args.settle:
        n = _settle()
        print(f"  settled: {n} bets\n")
        return

    matches = _load_open_matches()
    print(f"  {len(matches)} open model-covered matches")

    total_picks, total_written = 0, 0
    for row in matches:
        picks = _scan_one(row)
        if not picks:
            continue
        for p in picks:
            total_picks += 1
            tag = "  fired" if args.record else "  dry"
            src = p.get("source", "elo+pq_v1")
            cons_str = f" cons={1.0/p['consensus_prob']:.2f} (n={p.get('n_books')})" if p.get("consensus_prob") else ""
            print(f"    {tag}  [{src:10}]  {row['team1']:22} vs {row['team2']:22}  "
                  f"{p['market']:12} → {p['team']:18} @ {p['bookie']:8} {p['odds']:>5.2f}  "
                  f"(thr {p['thr']:.2f}, edge +{p['edge']*100:.1f}%, stake {p.get('stake', BASE_STAKE):.2f}u{cons_str})")
            if args.record:
                if _write_bet(row, p):
                    total_written += 1

    print(f"\n  picks: {total_picks}  written: {total_written}")

    if args.record:
        n = _settle()
        print(f"  settled: {n} prior open bets")
    print()


if __name__ == "__main__":
    main()
