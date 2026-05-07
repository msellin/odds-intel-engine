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

xG source:
  All strategies now run on both real live xG (from AF stats endpoint, ~top leagues only)
  and a shot-based proxy: xg_proxy = sot * 0.10 + (shots - sot) * 0.03.
  Proxy bets use a higher edge floor (+1.5–2pp) and log xg_source="shot_proxy" in reasoning.
  A/A2 require real xG for the quality filter but fall back gracefully with a tighter threshold.
"""

import json
import math
from datetime import datetime, timezone
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
        "description": "BTTS Momentum — trailing team pressure, min 15-40",
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

# Minimum distinct matches with real xG per league before trusting xG-gated strategies.
# Set low (3) while data accumulates — bot launched 2026-04-27, no league has 20 yet.
# Only applies to real-xG mode; proxy mode bypasses this gate entirely.
MIN_LEAGUE_XG_MATCHES = 3

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

    # Heartbeat every 10 cycles — show real/proxy split
    if _cycle_count % 10 == 0:
        if candidates:
            real_xg = sum(1 for c in candidates if c.get("has_live_xg"))
            proxy = len(candidates) - real_xg
            sample = candidates[:3]
            summaries = []
            for c in sample:
                xg_h, xg_a, is_real = _compute_live_xg(c)
                src = "xG" if is_real else "proxy"
                summaries.append(
                    f"min{c['minute']} {c['score_home']}-{c['score_away']} "
                    f"{src} {xg_h:.1f}-{xg_a:.1f}"
                )
            extra = f" +{len(candidates)-3} more" if len(candidates) > 3 else ""
            console.print(
                f"[dim]InplayBot heartbeat: {len(candidates)} candidates "
                f"({real_xg} real xG, {proxy} proxy) [{', '.join(summaries)}{extra}] | "
                f"session: {_total_bets_session} bets / {_total_candidates_session} evaluated[/dim]"
            )
        else:
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
                    f"{live_count} live snapshots (90s), {xg_count} with real xG | "
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
            continue

        league_id = pm.get("league_id")
        has_live_xg = cand.get("has_live_xg", False)

        # League xG gate — only enforced for real-xG matches.
        # Proxy matches bypass: shot data is universally available and doesn't need calibration.
        if has_live_xg:
            if league_id and _league_xg_cache.get(str(league_id), 0) < MIN_LEAGUE_XG_MATCHES:
                continue

        has_red_card = mid in red_card_matches

        for bot_name, bot_cfg in INPLAY_BOTS.items():
            bot_id = _bot_ids.get(bot_name)
            if not bot_id:
                continue

            if (mid, bot_name) in existing_bets:
                continue

            trigger = _check_strategy(bot_name, cand, pm, has_red_card, execute_query)
            if not trigger:
                continue

            # Safety: staleness check — odds must be < 60s old
            odds_age = _odds_age_seconds(cand)
            if odds_age is None or odds_age > 60:
                continue

            # Safety: score re-check — verify score unchanged since snapshot
            if not _score_recheck(execute_query, mid, cand["score_home"], cand["score_away"]):
                continue

            xg_h, xg_a, is_real = _compute_live_xg(cand)
            bet_data = {
                "market": trigger["market"],
                "selection": trigger["selection"],
                "odds": trigger["odds"],
                "stake": 1.0,
                "model_prob": trigger["model_prob"],
                "edge": trigger["edge"],
                "reasoning": json.dumps({
                    "strategy": bot_name,
                    "minute": cand["minute"],
                    "score": f"{cand['score_home']}-{cand['score_away']}",
                    "xg_home": round(xg_h, 3),
                    "xg_away": round(xg_a, 3),
                    "xg_source": "live" if is_real else "shot_proxy",
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
                    src_tag = "" if is_real else " [proxy]"
                    console.print(
                        f"[bold green]INPLAY BET: {bot_name}{src_tag} | "
                        f"{trigger['market']}/{trigger['selection']} @ {trigger['odds']:.2f} | "
                        f"edge={trigger['edge']:.1f}% | "
                        f"min {cand['minute']} score {cand['score_home']}-{cand['score_away']} | "
                        f"xG {xg_h:.2f}-{xg_a:.2f}"
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
    """
    Get latest snapshot per live match within last 90 seconds.
    Returns ALL live matches (not just those with xG) — strategies handle proxy fallback.
    has_live_xg flag lets each strategy decide confidence level.
    """
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
            lms.captured_at,
            (lms.xg_home IS NOT NULL) AS has_live_xg
        FROM live_match_snapshots lms
        JOIN matches m ON m.id = lms.match_id
        WHERE m.status = 'live'
          AND lms.captured_at >= NOW() - INTERVAL '90 seconds'
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

def _compute_live_xg(cand: dict) -> tuple[float, float, bool]:
    """
    Returns (xg_home, xg_away, is_real_xg).

    Uses live xG from AF when available (top leagues only).
    Falls back to shot proxy: sot * 0.10 + (shots - sot) * 0.03.
      - 0.10 ≈ avg xG per shot on target
      - 0.03 ≈ avg xG per off-target/blocked shot
    Proxy is less accurate but directionally sound for trading signals.
    """
    xg_h = cand.get("xg_home")
    xg_a = cand.get("xg_away")
    if xg_h is not None and xg_a is not None:
        return float(xg_h), float(xg_a), True

    sot_h = cand.get("shots_on_target_home") or 0
    sot_a = cand.get("shots_on_target_away") or 0
    off_h = max(0, (cand.get("shots_home") or 0) - sot_h)
    off_a = max(0, (cand.get("shots_away") or 0) - sot_a)
    return (
        sot_h * 0.10 + off_h * 0.03,
        sot_a * 0.10 + off_a * 0.03,
        False,
    )


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
        return _check_strategy_a(cand, pm, has_red_card, score_filter=None)
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
    A: score 0-0 only. A2: combined goals = 1.

    Proxy mode: runs but drops the shot-quality filter (trivially true with proxy),
    replaces live_xg >= 0.9 with sot_combined >= 9, raises edge floor to 5%.
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
        if sh + sa != 1:
            return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a

    pm_xg_h = float(pm.get("prematch_xg_home") or 0)
    pm_xg_a = float(pm.get("prematch_xg_away") or 0)
    pm_xg_total = pm_xg_h + pm_xg_a
    if pm_xg_total <= 0:
        return None

    # Bayesian posterior must be > prematch rate * 1.15
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    if posterior <= pm_xg_total * 1.15:
        return None

    sot = (cand["shots_on_target_home"] or 0) + (cand["shots_on_target_away"] or 0)

    if is_real:
        # Real xG: original checks
        if live_xg < 0.9:
            return None
        if sot < 4:
            return None
        total_shots = (cand["shots_home"] or 0) + (cand["shots_away"] or 0)
        if total_shots > 0 and live_xg / total_shots < 0.09:
            return None  # Low-quality shots only
        min_edge = 3.0
    else:
        # Proxy: proxy xg >= 0.9 maps to sot >= 9; no quality filter (trivially true)
        if sot < 9:
            return None
        min_edge = 5.0  # Higher bar for proxy noise

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.54:
        return None

    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    current_goals = sh + sa
    goals_needed = 3 - current_goals
    if goals_needed <= 0:
        return None

    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = posterior * remaining_minutes / 90.0
    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)

    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
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
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }


def _check_strategy_b(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy B: BTTS Momentum — trailing team shows pressure.

    Real xG: trailing team xg >= 0.4 AND sot >= 2 (original).
    Proxy: trailing team sot >= 4 (equivalent threshold at 0.10/shot).
    """
    minute = cand["minute"] or 0
    if minute < 15 or minute > 40:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if not ((sh == 1 and sa == 0) or (sh == 0 and sa == 1)):
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    sot_h = cand["shots_on_target_home"] or 0
    sot_a = cand["shots_on_target_away"] or 0

    if sa == 0:
        # Away team trailing
        trailing_xg = xg_a
        if is_real:
            if xg_a < 0.4 or sot_a < 2:
                return None
        else:
            if sot_a < 4:  # 4 * 0.10 = 0.40 proxy equivalent
                return None
    else:
        # Home team trailing
        trailing_xg = xg_h
        if is_real:
            if xg_h < 0.4 or sot_h < 2:
                return None
        else:
            if sot_h < 4:
                return None

    pm_btts = float(pm.get("prematch_btts_prob") or 0)
    if pm_btts <= 0.48:
        return None

    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    remaining_minutes = max(1, 90 - minute)
    trailing_lambda = trailing_xg * (remaining_minutes / max(1, minute))
    pm_xg_trailing = (
        float(pm.get("prematch_xg_away") or 0.8) if sa == 0
        else float(pm.get("prematch_xg_home") or 0.8)
    )
    blended_lambda = (pm_xg_trailing * (remaining_minutes / 90.0) + trailing_lambda) / 2.0
    btts_prob = 1.0 - math.exp(-blended_lambda)

    min_edge = 3.0 if is_real else 4.5
    implied = _implied_prob(odds)
    edge = (btts_prob - implied) * 100
    if edge < min_edge:
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
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }


def _check_strategy_c(cand: dict, pm: dict, has_red_card: bool,
                      home_only: bool) -> dict | None:
    """
    Strategy C/C_home: Favourite Comeback (DNB).

    Real xG: fav_xg > opp_xg for dominance signal.
    Proxy: fav_sot > opp_sot (tightened — must strictly exceed, not just equal).
    Possession threshold raised 3pp in proxy mode.
    """
    minute = cand["minute"] or 0
    max_minute = 70 if home_only else 60
    if minute < 25 or minute > max_minute:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    xg_h, xg_a, is_real = _compute_live_xg(cand)
    poss = float(cand["possession_home"] or 50)

    pm_home = float(pm.get("prematch_home_prob") or 0)
    pm_away = float(pm.get("prematch_away_prob") or 0)
    if pm_home <= 0 or pm_away <= 0:
        return None

    home_is_fav = pm_home > pm_away

    if home_is_fav:
        if not (sa - sh == 1):
            return None
        fav_xg, opp_xg = xg_h, xg_a
        fav_poss = poss
        fav_sot = cand["shots_on_target_home"] or 0
        opp_sot = cand["shots_on_target_away"] or 0
    else:
        if home_only:
            return None
        if not (sh - sa == 1):
            return None
        fav_xg, opp_xg = xg_a, xg_h
        fav_poss = 100.0 - poss
        fav_sot = cand["shots_on_target_away"] or 0
        opp_sot = cand["shots_on_target_home"] or 0

    if home_only and not home_is_fav:
        return None

    # Dominance check
    if is_real:
        if fav_xg <= opp_xg:
            return None
        min_poss = 55.0 if home_only else 60.0
    else:
        # Proxy: use SoT differential instead; raise possession threshold
        if fav_sot <= opp_sot:
            return None
        min_poss = 58.0 if home_only else 63.0

    if fav_poss < min_poss:
        return None

    # SoT guard: favourite must not be losing the shots battle
    if fav_sot < opp_sot:
        return None

    if home_is_fav:
        odds = cand.get("live_1x2_home")
    else:
        odds = cand.get("live_1x2_away")

    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    remaining = max(1, 90 - minute)
    fav_lambda_remaining = fav_xg * (remaining / max(1, minute))
    p_fav_scores = 1.0 - math.exp(-fav_lambda_remaining)

    min_edge = 3.0 if is_real else 4.5
    implied = _implied_prob(odds)
    edge = (p_fav_scores - implied) * 100
    if edge < min_edge:
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
            "fav_sot": fav_sot,
            "opp_sot": opp_sot,
            "fav_possession": round(fav_poss, 1),
            "prematch_home_prob": round(pm_home, 3),
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }


def _check_strategy_d(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy D: Late Goals Compression — Over 2.5 in late game (min 55-75).

    Real xG: live_xg >= 1.0.
    Proxy: sot_total >= 10 (10 * 0.10 = 1.0 equivalent). Lower confidence → edge floor 4.5%.
    """
    minute = cand["minute"] or 0
    if minute < 55 or minute > 75:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals > 1:
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a
    sot = (cand["shots_on_target_home"] or 0) + (cand["shots_on_target_away"] or 0)

    # Minimum pressure threshold
    if is_real:
        if live_xg < 1.0:
            return None
        min_edge = 3.0
    else:
        if sot < 10:  # ~1.0 xG equivalent
            return None
        min_edge = 4.5

    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) <= 2.50:
        return None
    odds = float(odds)

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.50:
        return None

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
    if edge < min_edge:
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
            "sot_total": sot,
            "prematch_o25": round(pm_o25, 3),
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }


def _check_strategy_e(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy E: Dead Game Unders — tempo collapse signals Under 2.5 (min 25-50).

    Real xG: pace_ratio = live_xg / expected_xg < 0.70.
    Proxy: shot_pace_ratio = total_shots / expected_shots < 0.70,
           where expected_shots = (pm_xg_total / 0.10) * (minute / 90).
    Both modes require low corner rate as confirmation.
    """
    minute = cand["minute"] or 0
    if minute < 25 or minute > 50:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh + sa > 1:
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    if is_real:
        expected_at_minute = pm_xg_total * (minute / 90.0)
        if expected_at_minute <= 0:
            return None
        pace_ratio = live_xg / expected_at_minute
        min_edge = 3.0
    else:
        # Shot pace proxy: expected shots derived from prematch xG at avg 0.10 xG/shot
        expected_shots_at_minute = (pm_xg_total / 0.10) * (minute / 90.0)
        if expected_shots_at_minute <= 0:
            return None
        total_shots = (cand["shots_home"] or 0) + (cand["shots_away"] or 0)
        pace_ratio = total_shots / expected_shots_at_minute
        min_edge = 4.5

    if pace_ratio >= 0.70:
        return None  # Not a dead game

    # Corners low: independent confirmation of low pressure
    corners_total = (cand["corners_home"] or 0) + (cand["corners_away"] or 0)
    expected_corners = 10 * (minute / 90.0)
    if corners_total > expected_corners * 0.8:
        return None

    odds = cand.get("live_ou_25_under")
    if not odds or float(odds) <= 1.0:
        return None
    odds = float(odds)

    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = posterior * remaining / 90.0
    goals_needed_for_over = 3 - (sh + sa)
    if goals_needed_for_over <= 0:
        return None

    p_over = _poisson_over_prob(lambda_remaining, goals_needed_for_over - 0.5)
    model_prob = 1.0 - p_over

    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
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
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }


def _check_strategy_f(cand: dict, pm: dict, has_red_card: bool,
                      execute_query) -> dict | None:
    """
    Strategy F: Odds Momentum Reversal — bet against unexplained odds drift.

    Real xG: xg_running_hot = xg_pace_90 > pm_xg_total.
    Proxy: sot_pace_90 > pm_xg_total * 10 (inverse of 0.10 xG/shot).
    """
    minute = cand["minute"] or 0
    if minute < 10:
        return None
    if has_red_card:
        return None

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

    if (old["score_home"] != cand["score_home"] or
            old["score_away"] != cand["score_away"]):
        return None

    drift_pct = (cur_ou - old_ou) / old_ou * 100
    if abs(drift_pct) < 15:
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a
    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    if is_real:
        xg_pace_90 = live_xg / max(1, minute) * 90
        xg_running_hot = xg_pace_90 > pm_xg_total
        pace_label = round(xg_pace_90, 2)
    else:
        sot_total = (cand["shots_on_target_home"] or 0) + (cand["shots_on_target_away"] or 0)
        sot_pace_90 = sot_total / max(1, minute) * 90
        # expected SOT pace = pm_xg_total / 0.10 per 90
        xg_running_hot = sot_pace_90 > pm_xg_total * 10
        pace_label = round(sot_pace_90, 2)

    if drift_pct > 15 and xg_running_hot:
        odds = cur_ou
        selection = "over"
    elif drift_pct < -15 and not xg_running_hot:
        odds = float(cand.get("live_ou_25_under") or 0)
        selection = "under"
    else:
        return None

    if odds <= 1.0:
        return None

    model_prob = _implied_prob(odds) + abs(drift_pct) / 1000.0
    edge = (model_prob - _implied_prob(odds)) * 100
    if edge < 2.0:
        edge = abs(drift_pct) / 5.0

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
            "pace_90": pace_label,
            "xg_source": "live" if is_real else "shot_proxy",
        },
    }
