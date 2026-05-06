"""
OddsIntel — In-Play Paper Trading Bot (Phase 1)

Rule-based in-play paper trading across 8 strategies (Week 1: A-F + A2 + C_home).
Reads from live_match_snapshots (populated by LivePoller every 30s), joins prematch
data from predictions + matches.af_prediction, and logs paper bets to simulated_bets.

Called from LivePoller._run_cycle() after snapshots are stored. No extra API calls.

Strategies:
  A   — xG Divergence Over 2.5 (score 0-0)
  A2  — xG Divergence Over 2.5 (score 1-0)
  B   — BTTS Momentum
  C   — Favourite Comeback (DNB)
  C_home — Home Favourite Comeback (DNB)
  D   — Late Goals Compression (Over 2.5)
  E   — Dead Game Unders
  F   — Odds Momentum Reversal
"""

import json
import math
from datetime import datetime, timezone, timedelta
from rich.console import Console

console = Console()

# ── Bot Configs ──────────────────────────────────────────────────────────────

INPLAY_BOTS = {
    "inplay_a": {
        "description": "xG Divergence Over 2.5 — score 0-0, min 25-35",
        "strategy": "inplay_a",
    },
    "inplay_a2": {
        "description": "xG Divergence Over 2.5 — score 1-0, min 25-35",
        "strategy": "inplay_a2",
    },
    "inplay_b": {
        "description": "BTTS Momentum — trailing team xG, min 15-40",
        "strategy": "inplay_b",
    },
    "inplay_c": {
        "description": "Favourite Comeback DNB — favourite trailing, min 25-60",
        "strategy": "inplay_c",
    },
    "inplay_c_home": {
        "description": "Home Favourite Comeback DNB — home fav trailing, min 25-70",
        "strategy": "inplay_c_home",
    },
    "inplay_d": {
        "description": "Late Goals Compression Over 2.5 — min 55-75",
        "strategy": "inplay_d",
    },
    "inplay_e": {
        "description": "Dead Game Unders — tempo collapse, min 25-50",
        "strategy": "inplay_e",
    },
    "inplay_f": {
        "description": "Odds Momentum Reversal — odds move without goal",
        "strategy": "inplay_f",
    },
}

# Minimum matches with xG data per league before we trust signals
MIN_LEAGUE_XG_MATCHES = 20

# ── Global State ─────────────────────────────────────────────────────────────

_bot_ids: dict[str, str] = {}  # bot_name -> bot_uuid, populated on first run
_league_xg_cache: dict[str, int] = {}  # league_id -> count, refreshed every 10 min
_league_cache_time: float = 0.0
_cycle_count: int = 0  # Track cycles for periodic status logs
_total_bets_session: int = 0  # Total bets placed since startup
_total_candidates_session: int = 0  # Total candidates evaluated


# ── Entrypoint (called from LivePoller) ──────────────────────────────────────

def run_inplay_strategies():
    """
    Main entrypoint — called every 30s from LivePoller after snapshots are stored.
    Reads latest snapshots from DB, checks all strategy conditions, logs paper bets.
    """
    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import ensure_bots, store_bet

    global _bot_ids, _cycle_count, _total_bets_session, _total_candidates_session
    _cycle_count += 1

    # Ensure bots exist (cached after first call)
    if not _bot_ids:
        _bot_ids = ensure_bots(INPLAY_BOTS)
        if _bot_ids:
            console.print(f"[cyan]InplayBot: {len(_bot_ids)} bots registered[/cyan]")

    # 1. Get latest snapshot per live match (within 90s — allows for 1 missed cycle)
    candidates = _get_live_candidates(execute_query)

    # Heartbeat even when no candidates — so we can tell the bot is running
    if _cycle_count % 10 == 0:
        if candidates:
            sample = candidates[:3]
            summaries = [
                f"min{c['minute']} {c['score_home']}-{c['score_away']} "
                f"xG {float(c['xg_home'] or 0):.1f}-{float(c['xg_away'] or 0):.1f}"
                for c in sample
            ]
            extra = f" +{len(candidates)-3} more" if len(candidates) > 3 else ""
            console.print(
                f"[dim]InplayBot heartbeat: {len(candidates)} candidates [{', '.join(summaries)}{extra}] | "
                f"session: {_total_bets_session} bets / {_total_candidates_session} evaluated[/dim]"
            )
        else:
            # Diagnose why no candidates: count live matches vs those with xG
            try:
                live_count = execute_query(
                    "SELECT COUNT(DISTINCT lms.match_id) AS n FROM live_match_snapshots lms "
                    "JOIN matches m ON m.id = lms.match_id "
                    "WHERE m.status = 'live' AND lms.captured_at >= NOW() - INTERVAL '90 seconds'",
                )[0]["n"]
                xg_count = execute_query(
                    "SELECT COUNT(DISTINCT lms.match_id) AS n FROM live_match_snapshots lms "
                    "JOIN matches m ON m.id = lms.match_id "
                    "WHERE m.status = 'live' AND lms.captured_at >= NOW() - INTERVAL '90 seconds' "
                    "AND lms.xg_home IS NOT NULL",
                )[0]["n"]
                console.print(
                    f"[dim]InplayBot heartbeat: 0 candidates | "
                    f"{live_count} live snapshots (90s), {xg_count} with xG | "
                    f"session: {_total_bets_session} bets[/dim]"
                )
            except Exception:
                console.print(f"[dim]InplayBot heartbeat: 0 candidates | session: {_total_bets_session} bets[/dim]")

    if not candidates:
        return

    # 2. Get prematch data for these matches
    match_ids = [c["match_id"] for c in candidates]
    prematch = _get_prematch_data(execute_query, match_ids)

    # 3. Refresh league xG cache if stale (every 10 min)
    _refresh_league_cache(execute_query)

    # 4. Get existing in-play bets for these matches (no-double-trigger)
    existing_bets = _get_existing_inplay_bets(execute_query, match_ids)

    # 5. Get red card matches
    red_card_matches = _get_red_card_matches(execute_query, match_ids)

    # 6. Check each strategy for each candidate
    bets_placed = 0
    for cand in candidates:
        mid = cand["match_id"]
        pm = prematch.get(mid)
        if not pm:
            continue  # No prematch data — skip

        # Safety: league has enough xG data?
        league_id = pm.get("league_id")
        if league_id and _league_xg_cache.get(str(league_id), 0) < MIN_LEAGUE_XG_MATCHES:
            continue

        # Safety: red card active?
        has_red_card = mid in red_card_matches

        for bot_name, bot_cfg in INPLAY_BOTS.items():
            bot_id = _bot_ids.get(bot_name)
            if not bot_id:
                continue

            # No double-trigger
            if (mid, bot_name) in existing_bets:
                continue

            # Check strategy conditions
            trigger = _check_strategy(bot_name, cand, pm, has_red_card, execute_query)
            if not trigger:
                continue

            # Safety: staleness check — odds must be < 60s old
            odds_age = _odds_age_seconds(cand)
            if odds_age is None or odds_age > 60:
                continue

            # Safety: score re-check — re-read latest snapshot, verify score unchanged
            if not _score_recheck(execute_query, mid, cand["score_home"], cand["score_away"]):
                continue

            # Log the paper bet
            bet_data = {
                "market": trigger["market"],
                "selection": trigger["selection"],
                "odds": trigger["odds"],
                "stake": 1.0,  # Fixed unit stake for Phase 1
                "model_prob": trigger["model_prob"],
                "edge": trigger["edge"],
                "reasoning": json.dumps({
                    "strategy": bot_name,
                    "minute": cand["minute"],
                    "score": f"{cand['score_home']}-{cand['score_away']}",
                    "xg_home": float(cand["xg_home"] or 0),
                    "xg_away": float(cand["xg_away"] or 0),
                    "posterior_rate": trigger.get("posterior_rate"),
                    "prematch_xg_total": trigger.get("prematch_xg_total"),
                    "odds_age_ms": int(odds_age * 1000) if odds_age else None,
                    **{k: v for k, v in trigger.get("extra", {}).items()},
                }),
            }

            try:
                bet_id = store_bet(bot_id, mid, bet_data)
                if bet_id:
                    bets_placed += 1
                    console.print(
                        f"[bold green]INPLAY BET: {bot_name} | "
                        f"{trigger['market']}/{trigger['selection']} @ {trigger['odds']:.2f} | "
                        f"edge={trigger['edge']:.1f}% | "
                        f"min {cand['minute']} score {cand['score_home']}-{cand['score_away']} | "
                        f"xG {float(cand['xg_home'] or 0):.2f}-{float(cand['xg_away'] or 0):.2f}"
                        f"[/bold green]"
                    )
            except Exception as e:
                console.print(f"[red]InplayBot store_bet error ({bot_name}): {e}[/red]")

    _total_bets_session += bets_placed
    _total_candidates_session += len(candidates)

    if bets_placed > 0:
        console.print(f"[bold green]InplayBot: {bets_placed} paper bet(s) placed this cycle[/bold green]")


# ── Data Queries ─────────────────────────────────────────────────────────────

def _get_live_candidates(execute_query) -> list[dict]:
    """Get latest snapshot per live match with xG data, within last 90 seconds."""
    rows = execute_query("""
        SELECT DISTINCT ON (lms.match_id)
            lms.match_id,
            lms.minute,
            lms.score_home,
            lms.score_away,
            lms.xg_home,
            lms.xg_away,
            lms.shots_home,
            lms.shots_away,
            lms.shots_on_target_home,
            lms.shots_on_target_away,
            lms.possession_home,
            lms.corners_home,
            lms.corners_away,
            lms.live_ou_25_over,
            lms.live_ou_25_under,
            lms.live_1x2_home,
            lms.live_1x2_draw,
            lms.live_1x2_away,
            lms.captured_at
        FROM live_match_snapshots lms
        JOIN matches m ON m.id = lms.match_id
        WHERE m.status = 'live'
          AND lms.captured_at >= NOW() - INTERVAL '90 seconds'
          AND lms.xg_home IS NOT NULL
        ORDER BY lms.match_id, lms.captured_at DESC
    """)
    return rows


def _get_prematch_data(execute_query, match_ids: list[str]) -> dict[str, dict]:
    """Get prematch xG, O2.5 prob, 1x2 probs, league info for a batch of matches."""
    if not match_ids:
        return {}

    rows = execute_query("""
        SELECT
            m.id AS match_id,
            m.league_id,
            m.home_team_id,
            m.away_team_id,
            l.tier AS league_tier,
            -- AF predicted goals (prematch xG proxy)
            (m.af_prediction->'predictions'->'goals'->>'home')::numeric AS prematch_xg_home,
            (m.af_prediction->'predictions'->'goals'->>'away')::numeric AS prematch_xg_away,
            -- Our model's prematch O2.5 probability
            p_ou.model_probability AS prematch_o25_prob,
            -- Our model's prematch BTTS probability
            p_btts.model_probability AS prematch_btts_prob,
            -- Our model's prematch 1X2 probabilities (for favourite detection)
            p_home.model_probability AS prematch_home_prob,
            p_away.model_probability AS prematch_away_prob
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        LEFT JOIN predictions p_ou ON p_ou.match_id = m.id
            AND p_ou.market = 'ou_25_over' AND p_ou.source = 'ensemble'
        LEFT JOIN predictions p_btts ON p_btts.match_id = m.id
            AND p_btts.market = 'btts_yes' AND p_btts.source = 'ensemble'
        LEFT JOIN predictions p_home ON p_home.match_id = m.id
            AND p_home.market = '1x2_home' AND p_home.source = 'ensemble'
        LEFT JOIN predictions p_away ON p_away.match_id = m.id
            AND p_away.market = '1x2_away' AND p_away.source = 'ensemble'
        WHERE m.id = ANY(%s::uuid[])
    """, (match_ids,))

    return {str(r["match_id"]): r for r in rows}


def _refresh_league_cache(execute_query):
    """Cache league xG match counts, refresh every 10 minutes."""
    import time
    global _league_xg_cache, _league_cache_time

    if time.time() - _league_cache_time < 600:
        return

    rows = execute_query("""
        SELECT m.league_id, COUNT(DISTINCT lms.match_id) AS xg_count
        FROM live_match_snapshots lms
        JOIN matches m ON m.id = lms.match_id
        WHERE lms.xg_home IS NOT NULL
        GROUP BY m.league_id
    """)
    _league_xg_cache = {str(r["league_id"]): int(r["xg_count"]) for r in rows}
    _league_cache_time = time.time()


def _get_existing_inplay_bets(execute_query, match_ids: list[str]) -> set[tuple]:
    """Return set of (match_id, bot_name) pairs that already have in-play bets."""
    if not match_ids:
        return set()

    rows = execute_query("""
        SELECT sb.match_id, b.name AS bot_name
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.match_id = ANY(%s::uuid[])
          AND b.name LIKE 'inplay_%%'
    """, (match_ids,))

    return {(str(r["match_id"]), r["bot_name"]) for r in rows}


def _get_red_card_matches(execute_query, match_ids: list[str]) -> set[str]:
    """Return set of match_ids that have a red card event."""
    if not match_ids:
        return set()

    rows = execute_query("""
        SELECT DISTINCT match_id
        FROM match_events
        WHERE match_id = ANY(%s::uuid[])
          AND event_type IN ('red_card', 'yellow_red_card')
    """, (match_ids,))

    return {str(r["match_id"]) for r in rows}


# ── Safety Checks ────────────────────────────────────────────────────────────

def _odds_age_seconds(cand: dict) -> float | None:
    """How old are the odds in this snapshot? Returns seconds or None."""
    captured = cand.get("captured_at")
    if not captured:
        return None
    if isinstance(captured, str):
        captured = datetime.fromisoformat(captured)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - captured).total_seconds()


def _score_recheck(execute_query, match_id: str,
                   expected_home: int, expected_away: int) -> bool:
    """Re-read the very latest snapshot and verify score hasn't changed."""
    rows = execute_query("""
        SELECT score_home, score_away
        FROM live_match_snapshots
        WHERE match_id = %s
        ORDER BY captured_at DESC
        LIMIT 1
    """, (match_id,))

    if not rows:
        return False

    return (rows[0]["score_home"] == expected_home and
            rows[0]["score_away"] == expected_away)


# ── Math Helpers ─────────────────────────────────────────────────────────────

def _bayesian_posterior(prematch_xg_total: float, live_xg_total: float,
                        minute: int) -> float:
    """
    Bayesian posterior rate: treats prematch xG as '1 game of prior evidence'.
    Shrinks early noise toward prior; converges with raw pace by minute 35.

    Formula: (prematch_xg + live_xg) / (1.0 + minute / 90)
    Returns rate per 90 minutes (comparable to prematch xG rate).
    """
    if minute <= 0:
        return prematch_xg_total
    return (prematch_xg_total + live_xg_total) / (1.0 + minute / 90.0)


def _poisson_over_prob(lam: float, threshold: float) -> float:
    """P(X >= threshold) for Poisson(lam). Used for Over 2.5 = P(goals >= 3)."""
    k = int(math.floor(threshold))
    # P(X <= k) via CDF
    cdf = 0.0
    for i in range(k + 1):
        cdf += (lam ** i) * math.exp(-lam) / math.factorial(i)
    return 1.0 - cdf


def _implied_prob(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds <= 1.0:
        return 1.0
    return 1.0 / odds


# ── Strategy Checks ──────────────────────────────────────────────────────────

def _check_strategy(bot_name: str, cand: dict, pm: dict,
                    has_red_card: bool, execute_query) -> dict | None:
    """
    Check if a strategy triggers for this candidate.
    Returns trigger dict {market, selection, odds, model_prob, edge, ...} or None.
    """
    if bot_name == "inplay_a":
        return _check_strategy_a(cand, pm, has_red_card, score_filter=(0, 0))
    elif bot_name == "inplay_a2":
        return _check_strategy_a(cand, pm, has_red_card, score_filter=None)  # 1-0 either way
    elif bot_name == "inplay_b":
        return _check_strategy_b(cand, pm, has_red_card)
    elif bot_name == "inplay_c":
        return _check_strategy_c(cand, pm, has_red_card, home_only=False)
    elif bot_name == "inplay_c_home":
        return _check_strategy_c(cand, pm, has_red_card, home_only=True)
    elif bot_name == "inplay_d":
        return _check_strategy_d(cand, pm, has_red_card)
    elif bot_name == "inplay_e":
        return _check_strategy_e(cand, pm, has_red_card)
    elif bot_name == "inplay_f":
        return _check_strategy_f(cand, pm, has_red_card, execute_query)
    return None


def _check_strategy_a(cand: dict, pm: dict, has_red_card: bool,
                      score_filter: tuple | None) -> dict | None:
    """
    Strategy A/A2: xG Divergence Over 2.5.
    A: score 0-0 only. A2: score 1-0 either way (combined goals = 1).
    """
    minute = cand["minute"] or 0
    if minute < 25 or minute > 35:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if score_filter == (0, 0):
        if sh != 0 or sa != 0:
            return None
    else:
        # A2: combined goals = 1
        if sh + sa != 1:
            return None

    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    live_xg = xg_h + xg_a

    # Need prematch xG
    pm_xg_h = float(pm.get("prematch_xg_home") or 0)
    pm_xg_a = float(pm.get("prematch_xg_away") or 0)
    pm_xg_total = pm_xg_h + pm_xg_a
    if pm_xg_total <= 0:
        return None

    # Bayesian posterior must be > prematch rate * 1.15
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    prematch_rate = pm_xg_total  # per 90 min
    if posterior <= prematch_rate * 1.15:
        return None

    # Combined xG >= 0.9
    if live_xg < 0.9:
        return None

    # Shots on target >= 4 combined
    sot = (cand["shots_on_target_home"] or 0) + (cand["shots_on_target_away"] or 0)
    if sot < 4:
        return None

    # Prematch O2.5 > 54%
    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.54:
        return None

    # xG per shot quality filter
    total_shots = (cand["shots_home"] or 0) + (cand["shots_away"] or 0)
    if total_shots > 0 and live_xg / total_shots < 0.09:
        return None

    # Live O2.5 odds must exist
    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    # Model probability: derive from Bayesian posterior remaining goals
    current_goals = sh + sa
    goals_needed = 3 - current_goals  # Over 2.5 = 3+ goals total
    if goals_needed <= 0:
        return None  # Already over — market would be settled

    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = posterior * remaining_minutes / 90.0
    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)

    # Edge = model_prob - implied_prob >= 3%
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < 3.0:
        return None

    return {
        "market": "ou_25",
        "selection": "over",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "score_state": f"{sh}-{sa}",
            "sot_combined": sot,
            "prematch_o25": round(pm_o25, 3),
        },
    }


def _check_strategy_b(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """Strategy B: BTTS Momentum — trailing team shows xG + shots."""
    minute = cand["minute"] or 0
    if minute < 15 or minute > 40:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    # Score must be 1-0 or 0-1
    if not ((sh == 1 and sa == 0) or (sh == 0 and sa == 1)):
        return None

    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    sot_h = cand["shots_on_target_home"] or 0
    sot_a = cand["shots_on_target_away"] or 0

    # Trailing team's xG >= 0.4 AND shots on target >= 2
    if sa == 0:
        # Away team trailing
        if xg_a < 0.4 or sot_a < 2:
            return None
    else:
        # Home team trailing
        if xg_h < 0.4 or sot_h < 2:
            return None

    # Prematch BTTS > 48%
    pm_btts = float(pm.get("prematch_btts_prob") or 0)
    if pm_btts <= 0.48:
        return None

    # We don't have live BTTS odds from AF — use O2.5 as proxy
    # BTTS and Over 2.5 are correlated: if trailing team scores, likely 2+ goals total
    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    # Model probability for BTTS based on trailing team xG
    trailing_xg = xg_a if sa == 0 else xg_h
    remaining_minutes = max(1, 90 - minute)
    # Simple: trailing team scores at least once in remaining time
    trailing_lambda = trailing_xg * (remaining_minutes / max(1, minute))
    # Bayesian blend with prematch
    pm_xg_trailing = float(pm.get("prematch_xg_away") or 0.8) if sa == 0 else float(pm.get("prematch_xg_home") or 0.8)
    blended_lambda = (pm_xg_trailing * (remaining_minutes / 90.0) + trailing_lambda) / 2.0
    btts_prob = 1.0 - math.exp(-blended_lambda)  # P(at least 1 goal from trailing team)

    implied = _implied_prob(odds)
    edge = (btts_prob - implied) * 100
    if edge < 3.0:
        return None

    return {
        "market": "ou_25",
        "selection": "over",
        "odds": odds,
        "model_prob": round(btts_prob, 4),
        "edge": round(edge, 2),
        "extra": {
            "trailing_team": "away" if sa == 0 else "home",
            "trailing_xg": round(trailing_xg, 2),
            "trailing_sot": sot_a if sa == 0 else sot_h,
            "prematch_btts": round(pm_btts, 3),
        },
    }


def _check_strategy_c(cand: dict, pm: dict, has_red_card: bool,
                      home_only: bool) -> dict | None:
    """Strategy C/C_home: Favourite Comeback (DNB)."""
    minute = cand["minute"] or 0
    max_minute = 70 if home_only else 60
    if minute < 25 or minute > max_minute:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    poss = float(cand["possession_home"] or 50)

    # Determine favourite from prematch probabilities
    pm_home = float(pm.get("prematch_home_prob") or 0)
    pm_away = float(pm.get("prematch_away_prob") or 0)

    if pm_home <= 0 or pm_away <= 0:
        return None

    home_is_fav = pm_home > pm_away
    # Favourite must be trailing by exactly 1
    if home_is_fav:
        if not (sa - sh == 1):
            return None
        fav_xg, opp_xg = xg_h, xg_a
        fav_poss = poss
        fav_sot = cand["shots_on_target_home"] or 0
        opp_sot = cand["shots_on_target_away"] or 0
    else:
        if home_only:
            return None  # C_home requires home team to be favourite
        if not (sh - sa == 1):
            return None
        fav_xg, opp_xg = xg_a, xg_h
        fav_poss = 100.0 - poss
        fav_sot = cand["shots_on_target_away"] or 0
        opp_sot = cand["shots_on_target_home"] or 0

    if home_only and not home_is_fav:
        return None

    # Favourite xG > underdog xG
    if fav_xg <= opp_xg:
        return None

    # Possession threshold
    min_poss = 55.0 if home_only else 60.0
    if fav_poss < min_poss:
        return None

    # Shots on target: favourite >= opponent
    if fav_sot < opp_sot:
        return None

    # We bet on favourite draw or win — use Draw odds as proxy for DNB
    # (DNB = refund on draw, profit on fav win. Draw odds represent the "draw" scenario.)
    # Actually for DNB we want the favourite's 1X2 odds
    if home_is_fav:
        odds = cand.get("live_1x2_home")
    else:
        odds = cand.get("live_1x2_away")

    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    # Simple model: favourite comeback probability based on xG dominance
    remaining = max(1, 90 - minute)
    fav_lambda_remaining = fav_xg * (remaining / max(1, minute))
    opp_lambda_remaining = opp_xg * (remaining / max(1, minute))
    # P(favourite scores at least 1 more than opponent in remaining time)
    # Simplified: P(fav scores >=1) * correction
    p_fav_scores = 1.0 - math.exp(-fav_lambda_remaining)
    # Rough edge check
    implied = _implied_prob(odds)
    edge = (p_fav_scores - implied) * 100
    if edge < 3.0:
        return None

    return {
        "market": "1x2",
        "selection": "home" if home_is_fav else "away",
        "odds": odds,
        "model_prob": round(p_fav_scores, 4),
        "edge": round(edge, 2),
        "extra": {
            "fav_team": "home" if home_is_fav else "away",
            "fav_xg": round(fav_xg, 2),
            "opp_xg": round(opp_xg, 2),
            "fav_possession": round(fav_poss, 1),
            "prematch_home_prob": round(pm_home, 3),
        },
    }


def _check_strategy_d(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """Strategy D: Late Goals Compression — Over 2.5 in late game."""
    minute = cand["minute"] or 0
    if minute < 55 or minute > 75:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    # Score must be 0-0 or 1-0 (either way)
    if total_goals > 1:
        return None

    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    live_xg = xg_h + xg_a

    # Combined xG >= 1.0
    if live_xg < 1.0:
        return None

    # Live O2.5 odds > 2.50
    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 2.50:
        return None
    odds = float(odds)

    # Prematch expected goals > 2.3 (use prematch O2.5 prob as proxy)
    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.50:
        return None

    # Model: goals remaining based on Bayesian posterior
    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = posterior * remaining / 90.0
    goals_needed = 3 - total_goals
    if goals_needed <= 0:
        return None

    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < 3.0:
        return None

    return {
        "market": "ou_25",
        "selection": "over",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "score_state": f"{sh}-{sa}",
            "live_xg_total": round(live_xg, 2),
            "prematch_o25": round(pm_o25, 3),
        },
    }


def _check_strategy_e(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """Strategy E: Dead Game Unders — tempo collapse signals Under 2.5."""
    minute = cand["minute"] or 0
    if minute < 25 or minute > 50:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals > 1:
        return None

    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    live_xg = xg_h + xg_a

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    # xG pace < 70% of expected
    expected_xg_at_minute = pm_xg_total * (minute / 90.0)
    if expected_xg_at_minute <= 0:
        return None
    pace_ratio = live_xg / expected_xg_at_minute
    if pace_ratio >= 0.70:
        return None  # Not a dead game — xG is tracking expectation

    # Corners low: proxy for low pressure
    corners_total = (cand["corners_home"] or 0) + (cand["corners_away"] or 0)
    expected_corners = 10 * (minute / 90.0)  # ~10 corners per 90 min average
    if corners_total > expected_corners * 0.8:
        return None  # Corner rate not low enough

    # Under 2.5 odds
    odds = cand.get("live_ou_25_under")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    # Model: Under probability from Bayesian posterior
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = posterior * remaining / 90.0
    goals_needed_for_over = 3 - total_goals
    if goals_needed_for_over <= 0:
        return None

    p_over = _poisson_over_prob(lambda_remaining, goals_needed_for_over - 0.5)
    model_prob = 1.0 - p_over  # P(Under 2.5)

    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < 3.0:
        return None

    return {
        "market": "ou_25",
        "selection": "under",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "pace_ratio": round(pace_ratio, 2),
            "corners_total": corners_total,
            "live_xg_total": round(live_xg, 2),
        },
    }


def _check_strategy_f(cand: dict, pm: dict, has_red_card: bool,
                      execute_query) -> dict | None:
    """Strategy F: Odds Momentum Reversal — bet against unexplained odds drift."""
    minute = cand["minute"] or 0
    if minute < 10:
        return None
    if has_red_card:
        return None

    # Need to compare current odds to 10 minutes ago
    match_id = cand["match_id"]
    rows = execute_query("""
        SELECT live_ou_25_over, live_1x2_home, live_1x2_away,
               score_home, score_away, minute
        FROM live_match_snapshots
        WHERE match_id = %s
          AND captured_at >= NOW() - INTERVAL '12 minutes'
          AND captured_at <= NOW() - INTERVAL '8 minutes'
        ORDER BY captured_at DESC
        LIMIT 1
    """, (match_id,))

    if not rows:
        return None

    old = rows[0]
    old_ou = float(old["live_ou_25_over"] or 0)
    cur_ou = float(cand.get("live_ou_25_over") or 0)

    if old_ou <= 0 or cur_ou <= 0:
        return None

    # Score must NOT have changed (no goal in the window)
    if (old["score_home"] != cand["score_home"] or
            old["score_away"] != cand["score_away"]):
        return None

    # Check O/U 2.5 drift
    drift_pct = (cur_ou - old_ou) / old_ou * 100

    if abs(drift_pct) < 15:
        return None  # Not enough drift

    # Check if drift is contrary to xG trend
    xg_h = float(cand["xg_home"] or 0)
    xg_a = float(cand["xg_away"] or 0)
    live_xg = xg_h + xg_a
    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)

    if pm_xg_total <= 0:
        return None

    xg_pace = live_xg / max(1, minute) * 90
    xg_running_hot = xg_pace > pm_xg_total

    # Drift up = Over odds increased = market moving toward Under
    # If xG is running hot but Over odds went UP — market wrong, bet Over
    if drift_pct > 15 and xg_running_hot:
        odds = cur_ou
        selection = "over"
    # Drift down = Over odds decreased = market moving toward Over
    # If xG is running cold but Over odds went DOWN — market wrong, bet Under
    elif drift_pct < -15 and not xg_running_hot:
        odds = float(cand.get("live_ou_25_under") or 0)
        selection = "under"
    else:
        return None  # Drift aligned with xG — no reversal signal

    if odds <= 1.0:
        return None

    # Simple edge estimate based on reversal magnitude
    model_prob = _implied_prob(odds) + abs(drift_pct) / 1000.0  # Small bump
    edge = (model_prob - _implied_prob(odds)) * 100
    if edge < 2.0:
        # For F, use a fixed minimum edge since the signal is the drift itself
        edge = abs(drift_pct) / 5.0  # 15% drift → 3% estimated edge

    return {
        "market": "ou_25",
        "selection": selection,
        "odds": odds,
        "model_prob": round(min(model_prob, 0.99), 4),
        "edge": round(edge, 2),
        "extra": {
            "drift_pct": round(drift_pct, 1),
            "old_ou_odds": round(old_ou, 3),
            "cur_ou_odds": round(cur_ou, 3),
            "xg_running_hot": xg_running_hot,
            "xg_pace_90": round(xg_pace, 2),
        },
    }
