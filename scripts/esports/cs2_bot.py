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
# Stake convention — flat €10 per bet (2026-06-25 CS2-FLAT-STAKE).
# Matches soccer's daily_pipeline_v2.STAKE constant. Earlier the bot used
# Kelly sizing converted via "1u = 1% bankroll" → tiny sub-€1 stakes for
# match_winner but BASE_STAKE-flat €10 for atleast1map (no model prob).
# Two staking paths in one bot made ROI uninterpretable. Soccer uses one
# convention across 16 bots; CS2 now does too.
BASE_STAKE = 1.0        # unit-stake field on cs2_simulated_bets.stake (always 1.0)
STAKE_EUR = 10.0        # flat €10 per bet (cs2_simulated_bets.stake_eur)
HLTV_EDGE_FLOOR = 0.03  # extra 3% required for HLTV-fallback picks (less proven)
HLTV_BASE_EDGE = 0.05   # 5% threshold edge for the hltv_v1 model

# Market consensus / outlier protection.
#
# MIN_BOOKS_FOR_PICK default relaxed 2026-06-25 (CS2-MIN-BOOKS-RELAX): only
# ~9% of CS2 matches in the last 30d had ≥2 books quoting match_winner, so
# the previous default starved every bot of picks (22 paper bets in 90d).
# The 15pp model-vs-consensus gate (becomes 15pp model-vs-bookie-implied
# when there's 1 book — both refer to the same value) and the 25pp anomaly
# guard remain as quality controls. Canonical baseline bots
# (value_v1/v8/v7/hltv_v1) keep min_books=2 via explicit cfg override —
# only the new diversification bots use the relaxed default.
MIN_BOOKS_FOR_PICK = 1          # default: any quoted book passes
MAX_CONSENSUS_DRIFT = 0.30      # best price cannot exceed median market consensus by >30%
MAX_EXTRA_EDGE = 0.50           # cap on edge over threshold; anything bigger is model error or stale data
MAX_MODEL_VS_CONSENSUS_PP = 0.15  # our_prob vs median consensus implied prob must be within 15pp

# Anomaly guard: if our model probability and the implied probability from the
# bookmaker offering the "value" diverge by more than this in absolute terms,
# the gap is more likely a data bug than a real edge — suppress the bet.
# Calibrated on real-money soft-book mistakes: 25pp ≈ 4σ in the calibrated model.
MAX_PROB_DIVERGENCE = 0.25

# ─────────────── MARKET-CONSENSUS SHRINKAGE (2026-06-25) ───────────────
# Mirror soccer's CAL-PIN-SHRINK pattern (improvements.py:142-144) for CS2.
# Soccer shrinks model_prob toward Pinnacle's de-vigged implied. CS2 doesn't
# have stable Pinnacle coverage (geo-blocked from EU dev; ~0% in last 30d
# upcoming pool), so we shrink toward the per-side median of all available
# books — bo3.gg/HLTV-median, Coolbet, Pinnacle when present. This is the
# same `market_consensus()` already used for the consensus-drift veto, so
# the shrinkage anchor is whatever consensus the bot can already see.
#
# shrunk_prob = α · model_prob + (1 − α) · consensus_implied
#
# Per-source α (analog of soccer's per-tier α): weaker models get pulled
# harder toward the market. v8 is the best model (AUC 0.703) so we keep
# most of its signal; hltv_v1 is the legacy rank-only baseline (AUC 0.673)
# so we trust the market more.
ALPHA_BY_SOURCE: dict[str, float] = {
    "elo+pq_v1": 0.75,
    "v8":        0.75,
    "v7":        0.65,
    "hltv_v1":   0.40,
}
DEFAULT_ALPHA = 0.75   # fallback for unknown sources


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
#   enabled — quick on/off without deleting the row
# (Stake sizing is no longer configurable per bot — flat €10 across the
#  registry, matching soccer's daily_pipeline_v2.STAKE.)
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
    # Default markets — the canonical bots stay on match_winner only after
    # CS2-MARKET-SPECIALISTS (2026-06-25); atleast1map / clean_sweep /
    # total_maps_o25 are owned by dedicated specialist bots below so each
    # market gets a clear edge-floor thesis instead of duplicating across
    # all bots.
    "markets": ("match_winner",),
    "shrink_to_market": True,   # mirror soccer's CAL-PIN-SHRINK (per-source α)
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
    # All seven bots use the relaxed min_books_for_pick=1 default — the
    # earlier opt-up to 2 (CS2-MIN-BOOKS-RELAX 2026-06-25 morning) starved
    # v8/v7 of fires (zero in 180d) because HLTV-fallback rows rarely have
    # ≥2 books. The 15pp consensus gate becomes 15pp model-vs-implied at
    # 1 book — stricter than the 25pp anomaly guard — so quality control
    # is preserved. CS2-MIN-BOOKS-RELAX-ALL 2026-06-25 afternoon.
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

    # ── Market specialists (CS2-MARKET-SPECIALISTS 2026-06-25 evening) ──
    # Each specialist owns one market with a thesis-appropriate edge floor.
    # Removed those markets from the canonical bots above so attribution
    # is clean: when "atleast1map" fires today, it's always a1m_specialist,
    # never aliased to value_v1's "atleast1map=2 picks in 90d" footnote.

    # A1M specialist: +1.5 map handicap (team wins ≥1 map in BO3). Lower
    # edge floor (3%) than canonical's 5% — handicap markets are softer
    # than 1X2 so we can extract value at thinner margins.
    "bot_cs2_a1m_specialist_v1": _cfg(
        "bot_cs2_a1m_specialist_v1",
        ("elo+pq_v1", "v8"),
        markets=("atleast1map",),
        min_extra_edge=0.03,
    ),

    # Clean-sweep specialist: -1.5 map handicap (2-0 BO3, 3-0 BO5). High
    # variance — model needs strong conviction on the favorite. Higher
    # floor (4%) than canonical. Orthogonal to MW because the same MW
    # prob can imply very different clean-sweep edges (mapped via p² /
    # p³ in _scan_one).
    "bot_cs2_clean_sweep_v1": _cfg(
        "bot_cs2_clean_sweep_v1",
        ("elo+pq_v1", "v8"),
        markets=("clean_sweep",),
        min_extra_edge=0.04,
    ),

    # Total Maps O/U 2.5 specialist (BO3 only): NEW market. P(over 2.5) =
    # 2 * p * (1 - p) where p = win_prob1 → peaks at p=0.5 (close matches
    # → decider likely), troughs at p=0/1 (lopsided → sweep). Genuinely
    # orthogonal to match_winner — high MW prob can coexist with high or
    # low decider prob depending on opponent strength.
    "bot_cs2_total_maps_v1": _cfg(
        "bot_cs2_total_maps_v1",
        ("elo+pq_v1", "v8"),
        markets=("total_maps_o25",),
        min_extra_edge=0.05,
    ),

    # Map 1 Winner specialist (BO3+ only): CS2-MAP1-WINNER 2026-07-02.
    # Fair odds from enrich_map1_winner() — 65% map win-rate + 35% ELO.
    # Edge source: veto-revealed map selection is public but most books
    # price Map 1 off overall team strength, not map-specific win rates.
    # 3% floor (same as a1m) — handicap-class market, softer than 1X2.
    # Requires veto_map1 to be set; rows without it have fair_odds_m1w* NULL
    # so _scan_one skips them automatically.
    "bot_cs2_map1_winner_v1": _cfg(
        "bot_cs2_map1_winner_v1",
        ("elo+pq_v1", "v8"),
        markets=("map1_winner",),
        min_extra_edge=0.03,
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
               coolbet_odds_cs1, coolbet_odds_cs2,
               coolbet_odds_total_o25, coolbet_odds_total_u25,
               coolbet_odds_m1w1, coolbet_odds_m1w2,
               fair_odds_m1w1, fair_odds_m1w2, veto_map1,
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
               u.coolbet_odds_cs1, u.coolbet_odds_cs2,
               u.coolbet_odds_total_o25, u.coolbet_odds_total_u25,
               u.coolbet_odds_m1w1, u.coolbet_odds_m1w2,
               u.fair_odds_m1w1, u.fair_odds_m1w2, u.veto_map1,
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


def shrink_prob(model_prob: float | None, consensus_prob: float, alpha: float) -> float | None:
    """Linear blend of model probability toward market consensus.

    Returns α·model_prob + (1−α)·consensus_prob clipped to (0, 1).
    None if model_prob is None or the blend lands outside (0, 1).
    Soccer analog: improvements.py:CAL-PIN-SHRINK.
    """
    if model_prob is None:
        return None
    if not (0.0 < alpha <= 1.0):
        return float(model_prob)
    blended = alpha * float(model_prob) + (1.0 - alpha) * float(consensus_prob)
    if not (0.0 < blended < 1.0):
        return None
    return blended


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


def _eligible_books(row: dict, sidekey: str, market: str = "match_winner") -> list[tuple[str, float]]:
    """Bookies actually quoting odds for one side. (bookie_name, decimal_odds).

    market='match_winner' (default) → head-to-head odds from
    bo3gg/coolbet/pinnacle.

    market='atleast1map' → +1.5 map handicap (team wins ≥1 map). Coolbet only
    (cs2_coolbet_scanner mig 250); other bookies' ≥1-map columns don't exist.

    market='clean_sweep' → -1.5 map handicap (team wins 2-0 BO3 / 3-0 BO5).
    Same Coolbet Match Handicap market as atleast1map, mirror outcome
    (mig 263, CS2-CLEAN-SWEEP 2026-06-25).

    Empty list = no bookie priced this market → bot skips the side."""
    if market == "atleast1map":
        candidates = [
            ("coolbet", row.get(f"coolbet_odds_map{sidekey}")),
        ]
    elif market == "clean_sweep":
        candidates = [
            ("coolbet", row.get(f"coolbet_odds_cs{sidekey}")),
        ]
    elif market == "map1_winner":
        candidates = [
            ("coolbet", row.get(f"coolbet_odds_m1w{sidekey}")),
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
                   max_odds: float = 100.0) -> dict | None:
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

    Stake is always BASE_STAKE (1.0u → €10 flat) — see _write_bet.
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

    return {
        "side": side, "team": team_name, "market": market,
        "bookie": best_bookie, "odds": best_odds, "fair": float(fair),
        "thr": float(thr), "edge": extra, "stake": BASE_STAKE, "source": source,
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
    )

    # match_winner — apply per-side market-consensus shrinkage to model prob
    # (mirrors soccer's CAL-PIN-SHRINK). When ≥2 books are present, blend
    # the model's win prob toward the consensus implied with per-source α
    # from ALPHA_BY_SOURCE. Re-derive fair + threshold from the shrunk prob
    # so the edge calc downstream is consistent. Falls through (no
    # shrinkage) when prob is None, books are thin, or cfg opts out.
    if "match_winner" in cfg["markets"]:
        shrink_enabled = cfg.get("shrink_to_market", True)
        alpha = ALPHA_BY_SOURCE.get(source, DEFAULT_ALPHA)
        for side, team_name, fair_orig, thr_orig, prob_orig, sidekey in [
            ("team1", row["team1"], row["fair_odds1"], thr1, row.get("win_prob1"), "1"),
            ("team2", row["team2"], row["fair_odds2"], thr2, row.get("win_prob2"), "2"),
        ]:
            prices = _eligible_books(row, sidekey)
            fair, thr, prob = fair_orig, thr_orig, prob_orig

            if (shrink_enabled and prob_orig is not None
                    and fair_orig is not None and thr_orig is not None
                    and len(prices) >= cfg["min_books_for_pick"]):
                cons = market_consensus(prices)
                if cons is not None:
                    consensus_prob, _ = cons
                    shrunk = shrink_prob(prob_orig, consensus_prob, alpha)
                    if shrunk is not None:
                        prob = shrunk
                        fair = round(1.0 / shrunk, 3)
                        # Preserve the original target_edge implicitly by
                        # keeping the thr/fair ratio: target_edge =
                        # 1 - thr_orig/fair_orig is unchanged post-shrinkage.
                        fair_orig_f = float(fair_orig)
                        if fair_orig_f > 0:
                            thr = round(fair * (float(thr_orig) / fair_orig_f), 3)

            pick = _consider_side(source=source, side=side, team_name=team_name,
                                  prices=prices, fair=fair, thr=thr, prob=prob,
                                  min_extra=min_extra, market="match_winner",
                                  **gate_kwargs)
            if pick:
                if prob != prob_orig and prob_orig is not None:
                    pick["prob_orig"] = float(prob_orig)
                    pick["shrink_alpha"] = alpha
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
                picks.append(pick)

    # clean_sweep (BO3/5 only) — the -1.5 mirror of atleast1map. Team wins
    # 2-0 in BO3 or 3-0 in BO5. Model probability is derived on the fly:
    #   P(2-0 BO3) ≈ win_prob_i²   (maps treated as i.i.d. — first-cut proxy)
    #   P(3-0 BO5) ≈ win_prob_i³
    # Fair odds = 1 / model_prob; threshold uses the bot's standard target
    # edge (cfg["min_extra_edge"], matches the atleast1map convention).
    # CS2-CLEAN-SWEEP 2026-06-25, mig 263.
    if "clean_sweep" in cfg["markets"] and best_of >= 3:
        maps_to_clean = best_of // 2 + 1   # 2 for BO3, 3 for BO5
        for side, team_name, prob_mw, sidekey in [
            ("team1", row["team1"], row.get("win_prob1"), "1"),
            ("team2", row["team2"], row.get("win_prob2"), "2"),
        ]:
            if prob_mw is None:
                continue   # no model prob → can't derive clean_sweep odds
            cs_prob = float(prob_mw) ** maps_to_clean
            if cs_prob <= 0 or cs_prob >= 1:
                continue
            cs_fair = round(1.0 / cs_prob, 3)
            cs_thr = round(cs_fair * (1 - cfg["min_extra_edge"]), 3)
            prices = _eligible_books(row, sidekey, market="clean_sweep")
            pick = _consider_side(source=source, side=side, team_name=team_name,
                                  prices=prices, fair=cs_fair, thr=cs_thr, prob=cs_prob,
                                  min_extra=cfg["min_extra_edge"], market="clean_sweep",
                                  **gate_kwargs)
            if pick:
                picks.append(pick)

    # total_maps_o25 (BO3 ONLY) — Coolbet's Total Maps Over/Under 2.5
    # market. The two outcomes are "over" (decider played, 2-1 or 1-2)
    # and "under" (clean sweep, 2-0 or 0-2). NOT team-oriented — both
    # outcomes are properties of the series, not of a particular team.
    # Model prob: P(over 2.5 | BO3) = 2 * p1 * (1 - p1) where p1 = win_prob1.
    # CS2-TOTAL-MAPS 2026-06-25, mig 265.
    if "total_maps_o25" in cfg["markets"] and best_of == 3:
        prob_mw = row.get("win_prob1")
        if prob_mw is not None:
            p1 = float(prob_mw)
            prob_over = 2.0 * p1 * (1.0 - p1)
            if 0.0 < prob_over < 1.0:
                prob_under = 1.0 - prob_over
                # Two outcomes, each its own column on cs2_upcoming_matches.
                # pick value is the outcome label so settlement can resolve
                # against (score1 + score2) without a team mapping.
                for outcome_label, prob, odds_col in [
                    ("over",  prob_over,  "coolbet_odds_total_o25"),
                    ("under", prob_under, "coolbet_odds_total_u25"),
                ]:
                    odds = row.get(odds_col)
                    if odds is None or float(odds) <= 1.0:
                        continue
                    tm_fair = round(1.0 / prob, 3)
                    tm_thr = round(tm_fair * (1 - cfg["min_extra_edge"]), 3)
                    prices = [("coolbet", float(odds))]
                    pick = _consider_side(
                        source=source, side=outcome_label, team_name=outcome_label,
                        prices=prices, fair=tm_fair, thr=tm_thr, prob=prob,
                        min_extra=cfg["min_extra_edge"], market="total_maps_o25",
                        **gate_kwargs,
                    )
                    if pick:
                        picks.append(pick)

    # map1_winner (BO3+ only) — which team wins the first map.
    # Fair odds come from enrich_map1_winner() (veto + map win-rate blend).
    # Coolbet-only market; no Pinnacle or bo3.gg column exists for this.
    # CS2-MAP1-WINNER 2026-07-02, mig 268.
    if "map1_winner" in cfg["markets"] and best_of >= 3:
        for side, team_name, fair, sidekey in [
            ("team1", row["team1"], row.get("fair_odds_m1w1"), "1"),
            ("team2", row["team2"], row.get("fair_odds_m1w2"), "2"),
        ]:
            if fair is None:
                continue
            prob = 1.0 / float(fair)
            thr = round(float(fair) * (1 - cfg["min_extra_edge"]), 3)
            prices = _eligible_books(row, sidekey, market="map1_winner")
            pick = _consider_side(
                source=source, side=side, team_name=team_name,
                prices=prices, fair=fair, thr=thr, prob=prob,
                min_extra=cfg["min_extra_edge"], market="map1_winner",
                **gate_kwargs,
            )
            if pick:
                picks.append(pick)

    return picks


def _get_bot_bankroll(bot_name: str) -> float:
    """Read current_bankroll from the bots table (mirrors soccer convention).
    Used only to stamp bankroll_at_pick on the bet row — does NOT affect stake
    sizing (flat €10 since CS2-FLAT-STAKE 2026-06-25)."""
    rows = execute_query("SELECT current_bankroll FROM bots WHERE name = %s", (bot_name,))
    if not rows:
        return 1000.0  # fallback if bot row missing
    return float(rows[0]["current_bankroll"])


def _write_bet(row: dict, pick: dict, cfg: dict) -> bool:
    bot_name = cfg["name"]
    bankroll = _get_bot_bankroll(bot_name)
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
        BASE_STAKE, STAKE_EUR, bankroll,
        pick.get("consensus_prob"), pick.get("n_books"),
    ))
    return bool(res)


def _settle() -> int:
    """Settle open cs2_simulated_bets against cs2_results. Updates the bot's
    bankroll on each settlement so the next pick sizes off the new bankroll."""
    open_bets = execute_query("""
        SELECT b.id, b.bot_name, b.team1, b.team2, b.market, b.pick,
               b.odds_at_pick, b.stake, b.stake_eur,
               r.winner, r.score1, r.score2,
               mm.winner_name AS map1_winner_name
        FROM cs2_simulated_bets b
        JOIN cs2_results r ON b.bo3gg_id = r.bo3gg_id
        LEFT JOIN cs2_hltv_matches hm ON hm.bo3gg_id = b.bo3gg_id
        LEFT JOIN cs2_hltv_match_maps mm
               ON mm.hltv_match_id = hm.hltv_match_id AND mm.map_order = 1
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
    if row["market"] == "clean_sweep":
        # Picked team must win AND opponent must score 0 maps.
        # BO3: 2-0; BO5: 3-0. Equivalent test: opponent score == 0 and team wins.
        s1, s2 = row.get("score1"), row.get("score2")
        if s1 is None or s2 is None:
            return None
        if row["pick"] == row["team1"]:
            return row["winner"] == "team1" and s2 == 0
        else:
            return row["winner"] == "team2" and s1 == 0
    if row["market"] == "total_maps_o25":
        # BO3 only — total maps played = score1 + score2 ∈ {2, 3}.
        # "over"  wins on 2-1 or 1-2 (decider played, total = 3).
        # "under" wins on 2-0 or 0-2 (clean sweep, total = 2).
        s1, s2 = row.get("score1"), row.get("score2")
        if s1 is None or s2 is None:
            return None
        total_maps = int(s1) + int(s2)
        if row["pick"] == "over":
            return total_maps > 2     # i.e., >= 3
        if row["pick"] == "under":
            return total_maps < 3     # i.e., <= 2
        return None
    if row["market"] == "map1_winner":
        # Settlement via cs2_hltv_match_maps (map_order=1). If HLTV match
        # details haven't been scraped yet, map1_winner_name is NULL — defer.
        map1_winner_name = row.get("map1_winner_name")
        if not map1_winner_name:
            return None
        return row["pick"] == map1_winner_name
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
    p.add_argument("--no-shrink", action="store_true",
                   help="Disable market-consensus shrinkage (for A/B comparison)")
    args = p.parse_args()

    if args.no_shrink:
        for cfg in BOTS_CONFIG.values():
            cfg["shrink_to_market"] = False

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
                shrink_str = (f" shrink α={p['shrink_alpha']:.2f} (orig prob {p['prob_orig']:.3f})"
                              if "shrink_alpha" in p else "")
                print(f"    {tag}  [{cfg['name']:24} {src:10}]  {row['team1']:22} vs {row['team2']:22}  "
                      f"{p['market']:12} → {p['team']:18} @ {p['bookie']:8} {p['odds']:>5.2f}  "
                      f"(thr {p['thr']:.2f}, edge +{p['edge']*100:.1f}%, stake {p.get('stake', BASE_STAKE):.2f}u{cons_str}{shrink_str})")
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
