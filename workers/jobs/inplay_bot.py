"""
OddsIntel — In-Play Paper Trading Bot (Phase 1)

Rule-based in-play paper trading across 11 strategies.
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
  F   — Odds Momentum Reversal (DROPPED 2026-05-08)
  I   — Favourite Stall: strong fav, 0-0 at min 42-65, live home ≥ 3.0
  J   — Goal Debt Over 1.5: 0-0 at min 30-52, prematch O25 ≥ 0.62, OU1.5 ≥ 2.85
  L   — Goal Contagion: first goal at min 15-35, O25 ≥ 0.55, OU2.5 available

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
        "description": "xG Divergence Over 2.5 — total goals ≤ 1, min 25-35",
        "strategy": "inplay_a",
    },
    # inplay_a2 was MERGED into inplay_a on 2026-05-08 (4/5 AI consensus). Same
    # thesis (live xG running ahead of prematch), just different score state.
    # _check_strategy_a now accepts total_goals ≤ 1 so it covers 0-0, 1-0,
    # and 0-1 in one strategy. The bot record + any pending a2 bets remain
    # in DB for settlement; no new ones get placed.
    "inplay_b": {
        "description": "BTTS Momentum — trailing team pressure, min 15-40",
        "strategy": "inplay_b",
    },
    "inplay_c": {
        "description": "Favourite Comeback — fav trailing by 1 (home fav: looser, longer window)",
        "strategy": "inplay_c",
    },
    # inplay_c_home was MERGED into inplay_c on 2026-05-08 (3/5 AI consensus —
    # replies 3, 4, 5). Same thesis. The merged strategy keeps both branches
    # but always runs: home-favourite gets the wider minute window (25-70) +
    # 5pp lower possession threshold; away-favourite gets the stricter
    # original (25-60). One strategy instead of two near-identical ones.
    "inplay_d": {
        "description": "Late Goals Compression Over 2.5 — min 55-75",
        "strategy": "inplay_d",
    },
    "inplay_e": {
        "description": "Dead Game Unders — tempo collapse, min 25-50",
        "strategy": "inplay_e",
    },
    "inplay_g": {
        "description": "Corner Cluster Over 2.5 — ≥3 corners in last 10min, min 30-70",
        "strategy": "inplay_g",
    },
    "inplay_h": {
        "description": "HT Restart Surge Over 2.5 — 0-0 at HT with first-half attacking, min 46-55",
        "strategy": "inplay_h",
    },
    "inplay_i": {
        "description": "Favourite Stall — strong fav 0-0 min 42-65, live fav odds drifted ≥ 3.0",
        "strategy": "inplay_i",
    },
    "inplay_j": {
        "description": "Goal Debt Over 1.5 — 0-0 min 30-52, prematch O25 ≥ 0.62, live OU1.5 ≥ 2.85",
        "strategy": "inplay_j",
    },
    "inplay_l": {
        "description": "Goal Contagion — first goal min 15-35 in high-λ match, Over 2.5 at remaining Poisson",
        "strategy": "inplay_l",
    },
    "inplay_m": {
        "description": "Equalizer Magnet — 1-0 or 0-1 min 30-60, BTTS prematch ≥ 0.48, live OU25 ≥ 3.0, bet Over 2.5",
        "strategy": "inplay_m",
    },
    "inplay_n": {
        "description": "Late Favourite Push — 0-0/1-1 min 72-80, home_win_prob ≥ 0.65, live home odds drifted ≥ 2.20, bet Home",
        "strategy": "inplay_n",
    },
    "inplay_q": {
        "description": "Red Card Overreaction — red 15-55, total goals ≤ 1, 11-man possession ≥ 55%, live OU2.5 over ≥ 2.30, bet Over 2.5",
        "strategy": "inplay_q",
    },
    # inplay_f (Odds Momentum Reversal) was DROPPED 2026-05-08 after the
    # 11-day backfill replay placed 78 settled F-bets at -6.4% ROI. 4/5
    # AI tools (replies 1, 2, 4-probation, 5) recommended drop; reply 5's
    # argument was decisive: sharp books already price the pace signal
    # we'd need to exploit on the same data we have. The bot record + any
    # pending bets remain in DB and will settle naturally; no new bets.
    # _check_strategy_f below is kept so historical settlement / replay
    # of older snapshots doesn't crash, but it's no longer dispatched.
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

# Per-stage rejection counters — accumulated across the session, logged on heartbeat.
# Lets us see exactly where the funnel collapses ("of 89 candidates, X had no prematch,
# Y had stale odds, Z hit the league xG gate, ...").
_funnel: dict[str, int] = {
    "no_prematch": 0,
    "league_xg_gate": 0,
    "existing_bet": 0,
    "no_strategy_trigger": 0,
    "odds_stale": 0,
    "score_changed": 0,
    "store_bet_error": 0,
}

# Per-strategy firing counters — tried vs fired per bot, logged on heartbeat.
# INPLAY-LIVE-DEBUG: tells us which strategies fire at what rate vs how often they're checked.
_strategy_stats: dict[str, dict[str, int]] = {}

# Goal Contagion state — tracks first-goal events for strategy L.
# match_id → last seen total goals (updated at end of each run_inplay_strategies cycle)
_prev_total_goals: dict[str, int] = {}
# match_id → cycle count when first goal was detected (window = 6 cycles ≈ 3 min)
_goal_event_window: dict[str, int] = {}


# ── Entrypoint (called from LivePoller) ──────────────────────────────────────

def run_inplay_strategies():
    """
    Main entrypoint — called every 30s from LivePoller after snapshots are stored.
    Reads latest snapshots from DB, checks all strategy conditions, logs paper bets.
    """
    from workers.utils.kill_switches import is_disabled
    if is_disabled("inplay"):
        return
    from psycopg2.pool import PoolError
    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import ensure_bots, store_bet

    global _bot_ids, _cycle_count, _total_bets_session, _total_candidates_session
    global _prev_total_goals, _goal_event_window
    _cycle_count += 1

    # Ensure bots exist (cached after first call)
    if not _bot_ids:
        try:
            _bot_ids = ensure_bots(INPLAY_BOTS)
        except PoolError:
            console.print("[yellow]InplayBot skipped: pool saturated (ensure_bots)[/yellow]")
            return
        if _bot_ids:
            console.print(f"[cyan]InplayBot: {len(_bot_ids)} bots registered[/cyan]")

    # 1. Get latest snapshot per live match (within 90s — allows for 1 missed cycle)
    try:
        candidates = _get_live_candidates(execute_query)
    except PoolError:
        # Pool exhausted — bail this cycle with a one-line log instead of
        # spamming a 100-line traceback every 30s. Will retry next cycle.
        console.print("[yellow]InplayBot skipped: pool saturated, retry next cycle[/yellow]")
        return

    # 1b. Smooth live xG (INPLAY-EMA-LIVE-XG) — overwrite cand xg_home/away
    # with a 5-min half-life EMA so a single big-chance snapshot can't trigger
    # a bet on its own. Real-xG candidates only; proxy candidates pass through.
    try:
        _attach_ema_live_xg(candidates, execute_query)
    except PoolError:
        console.print("[yellow]InplayBot: pool saturated during EMA smoothing — falling back to raw xG this cycle[/yellow]")

    # Heartbeat every 10 cycles — show real/proxy split + pool utilization
    if _cycle_count % 10 == 0:
        from workers.api_clients.db import get_pool_status
        pool = get_pool_status()
        pool_str = f"pool {pool['used']}/{pool['max']} ({pool['pct']}%)"
        pool_warn = " ⚠️ POOL HIGH" if pool["pct"] >= 80 else ""
        if candidates:
            real_xg = sum(1 for c in candidates if c.get("has_live_xg"))
            proxy = len(candidates) - real_xg
            with_ou_odds = sum(1 for c in candidates if c.get("live_ou_25_over"))
            with_1x2_odds = sum(1 for c in candidates if c.get("live_1x2_home"))
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
            funnel_str = ", ".join(f"{k}={v}" for k, v in _funnel.items() if v > 0)
            funnel_part = f" | funnel since-last: [{funnel_str}]" if funnel_str else ""
            console.print(
                f"[dim]InplayBot heartbeat: {len(candidates)} candidates "
                f"({real_xg} real xG, {proxy} proxy) "
                f"| odds: {with_ou_odds} OU / {with_1x2_odds} 1x2 "
                f"[{', '.join(summaries)}{extra}] | "
                f"session: {_total_bets_session} bets / {_total_candidates_session} evaluated | "
                f"{pool_str}{pool_warn}{funnel_part}[/dim]"
            )
            if _strategy_stats:
                stat_parts = []
                for sname, sdata in sorted(_strategy_stats.items()):
                    fired = sdata.get("fired", 0)
                    tried = sdata.get("tried", 0)
                    pct = f"{fired*100//tried}%" if tried > 0 else "0%"
                    stat_parts.append(f"{sname.replace('inplay_', '')}={fired}/{tried}({pct})")
                console.print(f"[dim]InplayBot strategy rates: {', '.join(stat_parts)}[/dim]")
            for k in _funnel:
                _funnel[k] = 0
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
                    f"session: {_total_bets_session} bets | {pool_str}{pool_warn}[/dim]"
                )
            except Exception:
                console.print(
                    f"[dim]InplayBot heartbeat: 0 candidates | "
                    f"session: {_total_bets_session} bets | {pool_str}{pool_warn}[/dim]"
                )

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
        mid = str(cand["match_id"])  # psycopg2 returns UUID objects; prematch keys are strings
        pm = prematch.get(mid)
        if not pm:
            _funnel["no_prematch"] += 1
            continue

        league_id = pm.get("league_id")
        has_live_xg = cand.get("has_live_xg", False)

        # League xG gate — only enforced for real-xG matches.
        # Proxy matches bypass: shot data is universally available and doesn't need calibration.
        if has_live_xg:
            if league_id and _league_xg_cache.get(str(league_id), 0) < MIN_LEAGUE_XG_MATCHES:
                _funnel["league_xg_gate"] += 1
                continue

        has_red_card = mid in red_card_matches

        for bot_name, bot_cfg in INPLAY_BOTS.items():
            bot_id = _bot_ids.get(bot_name)
            if not bot_id:
                continue

            if (mid, bot_name) in existing_bets:
                _funnel["existing_bet"] += 1
                continue

            trigger = _check_strategy(bot_name, cand, pm, has_red_card, execute_query)
            _sstat = _strategy_stats.setdefault(bot_name, {"tried": 0, "fired": 0})
            _sstat["tried"] += 1
            if not trigger:
                _funnel["no_strategy_trigger"] += 1
                continue
            _sstat["fired"] += 1

            # Safety: staleness check — odds must be < 60s old
            odds_age = _odds_age_seconds(cand)
            if odds_age is None or odds_age > 60:
                _funnel["odds_stale"] += 1
                continue

            # Safety: score re-check — verify score unchanged since snapshot
            if not _score_recheck(execute_query, mid, cand["score_home"], cand["score_away"]):
                _funnel["score_changed"] += 1
                continue

            xg_h, xg_a, is_real = _compute_live_xg(cand)
            xg_source = "live" if is_real else "shot_proxy"
            bet_data = {
                "market": trigger["market"],
                "selection": trigger["selection"],
                "odds": trigger["odds"],
                "stake": 1.0,
                "model_prob": trigger["model_prob"],
                "edge": trigger["edge"] / 100,  # strategies store edge as %, DB expects decimal (0.374 = 37.4%)
                "xg_source": xg_source,
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
                _funnel["store_bet_error"] += 1
                console.print(f"[red]InplayBot store_bet error ({bot_name}): {e}[/red]")

    _total_bets_session += bets_placed
    _total_candidates_session += len(candidates)

    if bets_placed > 0:
        console.print(f"[bold green]InplayBot: {bets_placed} paper bet(s) placed this cycle[/bold green]")

    # Update goal contagion state — do this AFTER strategy checks so goal_just_scored
    # is still True for strategy L on the cycle the goal is first detected.
    for cand in candidates:
        mid = str(cand["match_id"])
        total = (cand.get("score_home") or 0) + (cand.get("score_away") or 0)
        prev = _prev_total_goals.get(mid, 0)
        if total > prev and prev == 0 and total == 1:
            _goal_event_window[mid] = _cycle_count
        _prev_total_goals[mid] = total

    # Expire stale goal windows (> 8 cycles old ≈ 4 minutes)
    expired = [mid for mid, cyc in _goal_event_window.items()
               if _cycle_count - cyc > 8]
    for mid in expired:
        _goal_event_window.pop(mid, None)


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
            lms.live_ou_15_over,
            lms.live_ou_15_under,
            lms.live_ou_25_over,
            lms.live_ou_25_under,
            lms.live_1x2_home,
            lms.live_1x2_draw,
            lms.live_1x2_away,
            lms.live_next10_over,
            lms.live_next10_under,
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

    # prematch_xg_home/away come from team_season_stats.goals_for_avg (rolling
    # season-avg goals/game per team). When team stats are missing we fall
    # back to the league's mean goals/game across teams that DO have stats —
    # this avoids the prior bug where a flat 1.3+1.3 inflated Strategy E's
    # "Under" edges in low-scoring leagues (replies 4 + 5 of the 5-AI review).
    # Final fallback is 1.1+1.1 (global median, not mean) when even the league
    # has no calibration data; xg_fallback_used flag is exposed so strategies
    # can apply an edge penalty for those rows.
    rows = execute_query("""
        SELECT
            m.id AS match_id,
            m.league_id,
            m.home_team_id,
            m.away_team_id,
            l.tier AS league_tier,
            COALESCE(tss_h.goals_for_avg::numeric, la.league_avg::numeric, 1.1) AS prematch_xg_home,
            COALESCE(tss_a.goals_for_avg::numeric, la.league_avg::numeric, 1.1) AS prematch_xg_away,
            (tss_h.goals_for_avg IS NULL OR tss_a.goals_for_avg IS NULL) AS xg_fallback_used,
            p_ou.model_probability   AS prematch_o25_prob,
            p_btts.model_probability AS prematch_btts_prob,
            p_home.model_probability AS prematch_home_prob,
            p_away.model_probability AS prematch_away_prob
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        LEFT JOIN LATERAL (
            SELECT AVG(goals_for_avg) AS league_avg
            FROM team_season_stats
            WHERE league_api_id = l.api_football_id
        ) la ON TRUE
        LEFT JOIN LATERAL (
            SELECT goals_for_avg FROM team_season_stats
            WHERE team_api_id = m.home_team_api_id
              AND league_api_id = l.api_football_id
            ORDER BY season DESC LIMIT 1
        ) tss_h ON TRUE
        LEFT JOIN LATERAL (
            SELECT goals_for_avg FROM team_season_stats
            WHERE team_api_id = m.away_team_api_id
              AND league_api_id = l.api_football_id
            ORDER BY season DESC LIMIT 1
        ) tss_a ON TRUE
        LEFT JOIN predictions p_ou   ON p_ou.match_id   = m.id AND p_ou.market   = 'over25'   AND p_ou.source   = 'ensemble'
        LEFT JOIN predictions p_btts ON p_btts.match_id = m.id AND p_btts.market = 'btts_yes' AND p_btts.source = 'ensemble'
        LEFT JOIN predictions p_home ON p_home.match_id = m.id AND p_home.market = '1x2_home' AND p_home.source = 'ensemble'
        LEFT JOIN predictions p_away ON p_away.match_id = m.id AND p_away.market = '1x2_away' AND p_away.source = 'ensemble'
        LEFT JOIN LATERAL (
            SELECT
                MAX(os.odds) FILTER (WHERE os.selection = 'over'
                    AND os.bookmaker NOT IN ('api-football', 'api-football-live'))  AS prematch_ou25_over,
                MAX(os.odds) FILTER (WHERE os.selection = 'under'
                    AND os.bookmaker NOT IN ('api-football', 'api-football-live'))  AS prematch_ou25_under
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = 'over_under_25' AND os.is_closing = false
        ) pm_ou25 ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                MAX(os.odds) FILTER (WHERE os.selection = 'over'
                    AND os.bookmaker NOT IN ('api-football', 'api-football-live'))  AS prematch_ou15_over
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = 'over_under_15' AND os.is_closing = false
        ) pm_ou15 ON TRUE
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

def _attach_ema_live_xg(candidates: list[dict], execute_query,
                        half_life_min: float = 5.0, window_min: int = 10) -> None:
    """
    INPLAY-EMA-LIVE-XG — replace each cand's `xg_home`/`xg_away` with a
    time-weighted EMA over the last `window_min` minutes of snapshots.

    Why: AF live xG often jumps in single-cycle steps when a chance is logged
    (a clear-cut chance can add 0.4 xG in one snapshot). Strategies built on
    cumulative-to-minute readings then trip an entry on a one-off rather than
    a sustained pattern. The exponential filter weights recent samples heavily
    while smoothing isolated spikes — half-life of 5 min, time-aware (alpha
    scales with the inter-snapshot minute delta).

    No-op for proxy-only candidates (xG NULL → shot proxy in `_compute_live_xg`)
    and for matches with fewer than 2 prior snapshots in the window.
    """
    if not candidates:
        return
    real_xg = [c for c in candidates
               if c.get("xg_home") is not None and c.get("xg_away") is not None]
    if not real_xg:
        return

    match_ids = [str(c["match_id"]) for c in real_xg]
    rows = execute_query(
        f"""
        SELECT lms.match_id, lms.minute, lms.captured_at,
               lms.xg_home, lms.xg_away
        FROM live_match_snapshots lms
        WHERE lms.match_id = ANY(%s::uuid[])
          AND lms.xg_home IS NOT NULL
          AND lms.captured_at >= NOW() - INTERVAL '{int(window_min)} minutes'
        ORDER BY lms.match_id, lms.captured_at ASC
        """,
        (match_ids,),
    )

    by_mid: dict[str, list[dict]] = {}
    for r in rows:
        by_mid.setdefault(str(r["match_id"]), []).append(r)

    for cand in real_xg:
        mid = str(cand["match_id"])
        history = by_mid.get(mid, [])
        if len(history) < 2:
            continue
        prev_min = history[0].get("minute") or 0
        ema_h = float(history[0].get("xg_home") or 0)
        ema_a = float(history[0].get("xg_away") or 0)
        for r in history[1:]:
            cur_min = r.get("minute") or prev_min
            delta = max(0.5, cur_min - prev_min)
            alpha = 1.0 - math.exp(-delta / max(half_life_min, 0.01))
            x_h = float(r.get("xg_home") or 0)
            x_a = float(r.get("xg_away") or 0)
            ema_h = alpha * x_h + (1 - alpha) * ema_h
            ema_a = alpha * x_a + (1 - alpha) * ema_a
            prev_min = cur_min
        cand["xg_home"] = ema_h
        cand["xg_away"] = ema_a
        cand["xg_ema_applied"] = True


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


def _time_decay_weight(minute: int) -> float:
    """
    INPLAY-TIME-DECAY-PRIOR — weight given to live evidence at `minute`.

    `w_live = 1 - exp(-minute/30)`. At min 30 → 0.63 (63/37 live/prematch),
    at min 60 → 0.86. Replaces the older flat (1 / (1 + minute/90)) blend
    that gave too much weight to prematch information past minute 45.
    """
    if minute <= 0:
        return 0.0
    return 1.0 - math.exp(-minute / 30.0)


def _period_multiplier(minute: int) -> float:
    """
    INPLAY-PERIOD-RATES — period-specific scoring rate multiplier.

    Empirical per-match goal-rate distribution (Reply 4): minutes 1-15 score
    ~0.85× the average rate (warm-up phase), minutes 76-90+ score ~1.20×
    (late urgency). Mid-match periods at 1.0× neutral. Applied to the
    `remaining_lam` so that bets entered late inherit the period uplift,
    and bets entered very early are not overpriced.
    """
    if minute <= 15:
        return 0.85
    if minute >= 76:
        return 1.20
    return 1.0


def _state_multiplier_total(minute: int, score_home: int | None,
                             score_away: int | None) -> float:
    """
    INPLAY-LAMBDA-STATE — score-state multiplier on TOTAL remaining lambda.

    Football is non-Poisson-stationary: trailing teams push (+15%) and
    leaders defend (−10%) late, level matches (+5%/+5%) tend to open up.
    For the *total* goal lambda the per-team effects partially cancel —
    one team trailing + one leading averages to ~+2.5% net; level → +5%.
    Only fires from minute 60 (urgency window).
    """
    if minute < 60:
        return 1.0
    if score_home is None or score_away is None:
        return 1.0
    diff = abs(int(score_home) - int(score_away))
    if diff == 0:
        return 1.05
    return 1.025


def _state_multiplier_team(minute: int, team_state: str) -> float:
    """
    INPLAY-LAMBDA-STATE — per-team multiplier for 1X2 (Strategy N).

    `team_state ∈ {'leading', 'trailing', 'level'}`. Late (≥60) trailing
    +15%, leading −10%, level +5%. Pre-60 returns 1.0 (no urgency yet).
    """
    if minute < 60:
        return 1.0
    return {"trailing": 1.15, "leading": 0.90, "level": 1.05}.get(team_state, 1.0)


def _bayesian_posterior(prematch_xg_total: float, live_xg_total: float,
                        minute: int) -> float:
    """
    Bayesian posterior rate per 90 minutes — blends prematch + live signal.

    INPLAY-TIME-DECAY-PRIOR (2026-05-10): w_live = 1 - exp(-minute/30) drifts
    weight from prematch toward live as the match progresses. At min 30 ~63%
    live / 37% prematch; at min 60 ~86/14. The live signal is normalized to a
    per-90 rate (live_xg × 90 / minute) so the blend is in rate-space, not
    raw-cumulative-space.

    Replaces the older flat blend (pm + live) / (1 + minute/90) which
    underweighted live signal late in the match.
    """
    if minute <= 0:
        return prematch_xg_total
    pm_rate = prematch_xg_total
    live_rate = live_xg_total * 90.0 / minute
    w_live = _time_decay_weight(minute)
    return (1.0 - w_live) * pm_rate + w_live * live_rate


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


def _remaining_goals_prob(pm_xg_total: float, minute: int,
                          goals_observed: int, threshold: float,
                          score_home: int | None = None,
                          score_away: int | None = None) -> tuple[float, float, float]:
    """
    Bayesian remaining-goals Poisson — shared by strategies J/L/M (and future O).

    Returns (model_prob, posterior_lam, remaining_lam):
      - model_prob:    P(remaining goals ≥ threshold) under Poisson(remaining_lam)
      - posterior_lam: full-match λ blended from prematch xG and observed goals
      - remaining_lam: posterior λ scaled to the remaining minutes, with all
                      calibration multipliers applied

    Calibration stack (applied in order to remaining_lam):
      • h2_uplift     = 1.05× post-min-45  (Dixon & Robinson empirical 2nd-half uplift)
      • period_mult   — INPLAY-PERIOD-RATES (0.85× at 1-15, 1.20× at 76+)
      • state_mult    — INPLAY-LAMBDA-STATE (level late +5%, imbalanced late +2.5%)
                        only when score_home / score_away are supplied

    Posterior λ:
      INPLAY-TIME-DECAY-PRIOR — blend prematch rate + observed-goal rate (per 90)
      with w_live = 1 - exp(-minute/30). Equivalent to the live-xG blend used
      by `_bayesian_posterior`, but evidence here is the goal count rather than
      a live-xG signal.
    """
    if minute <= 0:
        return 0.0, pm_xg_total, pm_xg_total

    pm_rate = pm_xg_total
    observed_rate_per_90 = goals_observed * 90.0 / minute
    w_live = _time_decay_weight(minute)
    posterior_lam = (1.0 - w_live) * pm_rate + w_live * observed_rate_per_90

    remaining_frac = (90.0 - minute) / 90.0
    h2_uplift = 1.05 if minute >= 45 else 1.0
    period_mult = _period_multiplier(minute)
    state_mult = _state_multiplier_total(minute, score_home, score_away)
    remaining_lam = posterior_lam * remaining_frac * h2_uplift * period_mult * state_mult

    model_prob = _poisson_over_prob(remaining_lam, threshold - 0.5)
    return model_prob, posterior_lam, remaining_lam


def _scaled_remaining_lam(posterior_lam: float, minute: int,
                          score_home: int | None = None,
                          score_away: int | None = None) -> float:
    """
    Convert a posterior full-match λ into a remaining-time λ with the full
    calibration stack applied (h2_uplift × period_mult × state_mult).

    Used by every strategy that computes its own remaining lambda outside
    `_remaining_goals_prob` (A/C/D/E/G/H/Q). Keeps the multiplier set in
    one place so future tweaks land everywhere.
    """
    if minute >= 90:
        return 0.0
    remaining_frac = (90.0 - minute) / 90.0
    h2_uplift = 1.05 if minute >= 45 else 1.0
    period_mult = _period_multiplier(minute)
    state_mult = _state_multiplier_total(minute, score_home, score_away)
    return posterior_lam * remaining_frac * h2_uplift * period_mult * state_mult


def _bivariate_poisson_win_prob(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    """
    P(home wins), P(draw), P(away wins) for independent Poisson goals.
    Both lambdas represent expected goals in the REMAINING time.
    Sums up to 8 goals per team (sufficient for < 0.001% error at typical live lambdas).
    """
    max_g = 9
    ph_win = pd_draw = pa_win = 0.0
    for h in range(max_g):
        p_h = (lam_h ** h) * math.exp(-lam_h) / math.factorial(h)
        for a in range(max_g):
            p_a = (lam_a ** a) * math.exp(-lam_a) / math.factorial(a)
            p = p_h * p_a
            if h > a:
                ph_win += p
            elif h == a:
                pd_draw += p
            else:
                pa_win += p
    return ph_win, pd_draw, pa_win


# ── Odds Resolution ──────────────────────────────────────────────────────────

def _resolve_odds(live_val, pm_val, min_val: float = 1.0) -> tuple[float, bool]:
    """Return (odds, is_live). Live odds take priority; prematch as fallback.
    Returns (0.0, False) if neither source meets min_val.

    INPLAY-LIVE-DEBUG: live OU odds are only available for ~12% of snapshots
    (AF live odds endpoint coverage). Prematch best odds from odds_snapshots
    are used as fallback for paper trading — model edge still computed correctly
    using remaining-Poisson, just without the live drift component.
    Not used for drift-detection strategies (I, N, C) where the live odds level
    is part of the thesis, not just an entry price.
    """
    if live_val is not None:
        v = float(live_val)
        if v > min_val:
            return v, True
    if pm_val is not None:
        v = float(pm_val)
        if v > min_val:
            return v, False
    return 0.0, False


# ── Strategy Checks ──────────────────────────────────────────────────────────

def _check_strategy(bot_name: str, cand: dict, pm: dict,
                    has_red_card: bool, execute_query) -> dict | None:
    """
    Check if a strategy triggers for this candidate.
    Returns trigger dict {market, selection, odds, model_prob, edge, ...} or None.
    """
    if bot_name == "inplay_a":
        return _check_strategy_a(cand, pm, has_red_card)
    # inplay_a2 intentionally not dispatched — merged into A on 2026-05-08
    elif bot_name == "inplay_b":
        return _check_strategy_b(cand, pm, has_red_card)
    elif bot_name == "inplay_c":
        return _check_strategy_c(cand, pm, has_red_card)
    # inplay_c_home intentionally not dispatched — merged into C on 2026-05-08
    elif bot_name == "inplay_d":
        return _check_strategy_d(cand, pm, has_red_card)
    elif bot_name == "inplay_e":
        return _check_strategy_e(cand, pm, has_red_card)
    elif bot_name == "inplay_g":
        return _check_strategy_g(cand, pm, has_red_card, execute_query)
    elif bot_name == "inplay_h":
        return _check_strategy_h(cand, pm, has_red_card, execute_query)
    elif bot_name == "inplay_i":
        return _check_strategy_i(cand, pm, has_red_card)
    elif bot_name == "inplay_j":
        return _check_strategy_j(cand, pm, has_red_card)
    elif bot_name == "inplay_l":
        return _check_strategy_l(cand, pm, has_red_card)
    elif bot_name == "inplay_m":
        return _check_strategy_m(cand, pm, has_red_card)
    elif bot_name == "inplay_n":
        return _check_strategy_n(cand, pm, has_red_card)
    elif bot_name == "inplay_q":
        return _check_strategy_q(cand, pm, has_red_card, execute_query)
    # inplay_f intentionally not dispatched — dropped 2026-05-08
    return None


def _check_strategy_a(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy A: xG Divergence Over 2.5.

    Merged 2026-05-08: previously A=score(0-0) and A2=combined goals=1. Now
    both states share a single strategy with `total_goals <= 1`.

    Thresholds loosened 2026-05-08 per 5-AI consensus (combinatorial-rarity
    diagnosis): minute 25-35→20-40, live_xg ≥0.9→0.6, SoT ≥4→3, proxy SoT
    ≥9→6, shot-quality ≥0.09→0.07, posterior multiplier 1.15×→1.08×,
    edge 3%/5%→1.5%/3.5%, prematch_o25 >0.54→0.50.

    Proxy mode keeps a higher edge floor to compensate for shot-derived xG noise.
    """
    minute = cand["minute"] or 0
    if minute < 20 or minute > 40:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh + sa > 1:
        return None  # Need a low-scoring state for the divergence thesis to apply

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a

    pm_xg_h = float(pm.get("prematch_xg_home") or 0)
    pm_xg_a = float(pm.get("prematch_xg_away") or 0)
    pm_xg_total = pm_xg_h + pm_xg_a
    if pm_xg_total <= 0:
        return None

    # Bayesian posterior must be > prematch rate * 1.08 (loosened from 1.15)
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    if posterior <= pm_xg_total * 1.08:
        return None

    sot = (cand["shots_on_target_home"] or 0) + (cand["shots_on_target_away"] or 0)

    if is_real:
        if live_xg < 0.6:
            return None
        if sot < 3:
            return None
        total_shots = (cand["shots_home"] or 0) + (cand["shots_away"] or 0)
        if total_shots > 0 and live_xg / total_shots < 0.07:
            return None  # Low-quality shots only
        min_edge = 1.5
    else:
        # Proxy: live_xg ≥ 0.6 maps to sot ≥ 6
        if sot < 6:
            return None
        min_edge = 3.5  # Higher bar for proxy noise

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.50:
        return None

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"))
    if odds <= 1.0:
        return None

    current_goals = sh + sa
    goals_needed = 3 - current_goals
    if goals_needed <= 0:
        return None

    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)
    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)

    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
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
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }


def _check_strategy_b(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy B: BTTS Momentum (bets Over 2.5).

    Logic fixed 2026-05-08 per 5-AI consensus. Old version computed P(trailing
    team scores) — mislabeled it as BTTS — and compared it directly to OU 2.5
    implied probability. That created phantom edge: P(BTTS) is almost always
    higher than P(Over 2.5), so the comparison inflated edge by construction.

    New flow (Reply 4 Option C):
      • Trailing team pressure (xG/SoT) is kept as a STATE filter — same idea
      • Prematch BTTS prob is kept as a MATCH-TYPE filter
      • Edge is computed against the actual market we bet on (Over 2.5) using
        proper Poisson on full-match posterior xG (both teams) — not BTTS

    Real xG: trailing team xg >= 0.4 AND sot >= 2 (original).
    Proxy: trailing team sot >= 4 (equivalent threshold at 0.10/shot).
    """
    minute = cand["minute"] or 0
    # Loosened 2026-05-08: window 15-40 → 12-50 (5-AI consensus)
    if minute < 12 or minute > 50:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if not ((sh == 1 and sa == 0) or (sh == 0 and sa == 1)):
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    sot_h = cand["shots_on_target_home"] or 0
    sot_a = cand["shots_on_target_away"] or 0

    # Loosened 2026-05-08: trailing xg 0.4 → 0.20, sot 2 → 1 (real); proxy sot 4 → 2
    if sa == 0:
        trailing_xg = xg_a
        trailing_sot = sot_a
        if is_real:
            if xg_a < 0.20 or sot_a < 1:
                return None
        else:
            if sot_a < 2:
                return None
    else:
        trailing_xg = xg_h
        trailing_sot = sot_h
        if is_real:
            if xg_h < 0.20 or sot_h < 1:
                return None
        else:
            if sot_h < 2:
                return None

    # Filter: prematch must signal "both attack" type match (loosened 0.48 → 0.42)
    pm_btts = float(pm.get("prematch_btts_prob") or 0)
    if pm_btts <= 0.42:
        return None

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"))
    if odds <= 1.0:
        return None

    # Edge: proper P(Over 2.5) from posterior xG (matches the market we bet)
    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    live_xg = xg_h + xg_a
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)

    current_goals = sh + sa
    goals_needed = 3 - current_goals
    if goals_needed <= 0:
        return None

    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)

    # Edge floor loosened 2026-05-08: 3.0/4.5 → 2.0/3.0 (B model now uses
    # proper Poisson P(Over 2.5) post-2026-05-08 fix, so edge values are honest)
    min_edge = 2.0 if is_real else 3.0
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "trailing_team": "away" if sa == 0 else "home",
            "trailing_xg": round(trailing_xg, 2),
            "trailing_sot": trailing_sot,
            "prematch_btts": round(pm_btts, 3),
            "xg_source": "live" if is_real else "shot_proxy",
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }


def _check_strategy_c(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy C: Favourite Comeback (DNB-style 1X2 bet on the trailing favourite).

    Merged 2026-05-08: previously split into C (any fav) and C_home (home favs
    only, with wider window + lower possession threshold). Now a single
    strategy. When the favourite is at home we keep the looser variant
    (window 25-70, possession 55%/58%); when away we keep the stricter
    original (25-60, 60%/63%). Same code path for both.

    Real xG: fav_xg > opp_xg for dominance signal.
    Proxy: fav_sot > opp_sot (tightened — must strictly exceed, not just equal).
    Possession threshold raised 3pp in proxy mode to compensate for noise.
    """
    minute = cand["minute"] or 0
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

    # Home favs get the looser bands (longer window, lower possession bar)
    max_minute = 70 if home_is_fav else 60
    if minute < 25 or minute > max_minute:
        return None

    if home_is_fav:
        if not (sa - sh == 1):
            return None
        fav_xg, opp_xg = xg_h, xg_a
        fav_poss = poss
        fav_sot = cand["shots_on_target_home"] or 0
        opp_sot = cand["shots_on_target_away"] or 0
    else:
        if not (sh - sa == 1):
            return None
        fav_xg, opp_xg = xg_a, xg_h
        fav_poss = 100.0 - poss
        fav_sot = cand["shots_on_target_away"] or 0
        opp_sot = cand["shots_on_target_home"] or 0

    # Dominance check (loosened possession 2026-05-08: home 55→52, away 60→55)
    if is_real:
        if fav_xg <= opp_xg:
            return None
        min_poss = 52.0 if home_is_fav else 55.0
    else:
        # Proxy: use SoT differential instead; raise possession threshold
        if fav_sot <= opp_sot:
            return None
        min_poss = 55.0 if home_is_fav else 58.0

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

    # Edge floor loosened 2026-05-08: 3.0/4.5 → 1.5/3.0
    min_edge = 1.5 if is_real else 3.0
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
    Strategy D: Late Goals Compression — Over 2.5 in late game.

    Thresholds loosened 2026-05-08 per 5-AI consensus: window 55-75 → 48-80,
    live_xg ≥1.0 → 0.7, proxy SoT 10 → 6, OU odds floor 2.50 → 2.10,
    prematch_o25 >0.50 → 0.46, edge 3%/4.5% → 1.5%/3.0%.

    Real xG: live_xg >= 0.7. Proxy: sot_total >= 6 (~0.6 xG equivalent).
    """
    minute = cand["minute"] or 0
    if minute < 48 or minute > 80:
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

    # Minimum pressure threshold (loosened)
    if is_real:
        if live_xg < 0.7:
            return None
        min_edge = 1.5
    else:
        if sot < 6:
            return None
        min_edge = 3.0

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"), min_val=2.10)
    if odds <= 2.10:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.46:
        return None

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)
    goals_needed = 3 - total_goals
    if goals_needed <= 0:
        return None

    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
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
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }


def _check_strategy_e(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy E: Dead Game Unders — tempo collapse signals Under 2.5 (min 25-50).

    Real xG only. Proxy mode disabled 2026-05-09: the shot-based formula
    `expected_shots = (pm_xg_total / 0.10) * (minute/90)` used 0.10 xG/shot
    (the SoT constant) as the denominator for all shots, inflating expected
    counts and producing falsely-low pace_ratio values. Confirmed: 182 proxy
    bets placed 2026-05-09 at −8.49 pnl (−4.7% ROI). Voided via migration 079.
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
    if not is_real:
        return None  # proxy disabled — see docstring

    live_xg = xg_h + xg_a

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    expected_at_minute = pm_xg_total * (minute / 90.0)
    if expected_at_minute <= 0:
        return None
    pace_ratio = live_xg / expected_at_minute
    min_edge = 3.0

    if pace_ratio >= 0.70:
        return None  # Not a dead game

    # Corners low: independent confirmation of low pressure.
    # NULL corners = no stats yet; skip the corner confirmation rather than
    # treating it as 0 corners (which would always pass as "low").
    corners_total = None
    if cand["corners_home"] is not None and cand["corners_away"] is not None:
        corners_total = cand["corners_home"] + cand["corners_away"]
        expected_corners = 10 * (minute / 90.0)
        if corners_total > expected_corners * 0.8:
            return None

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_under"), pm.get("prematch_ou25_under"))
    if odds <= 1.0:
        return None

    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)
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
        "market": "O/U",
        "selection": "under 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "pace_ratio": round(pace_ratio, 2),
            "corners_total": corners_total,
            "live_xg_total": round(live_xg, 2),
            "xg_source": "live",
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }


def _check_strategy_g(cand: dict, pm: dict, has_red_card: bool,
                      execute_query) -> dict | None:
    """
    Strategy G: Corner Cluster Over 2.5.

    New strategy added 2026-05-08 (4/5 AI consensus — replies 1, 2, 3, 5).
    Thesis: corner clusters indicate sustained final-third pressure that
    the live xG model under-weights for set-piece-strong teams. Three or
    more corners in a 10-minute window precedes goals at higher than
    baseline rate, especially when total goals are still ≤ 1.

    Entry:
      • minute 30-70
      • current total goals ≤ 1
      • ≥ 3 corners gained in last 10 min (combined home + away)
      • OU 2.5 over odds ≥ 2.10
      • prematch_o25 > 0.45 (filter out genuinely defensive matches)
      • no red card
      • model edge ≥ 3% (real xG) or 4.5% (proxy)

    Bet: Over 2.5
    Edge model: bayesian-posterior xG → P(remaining goals ≥ 3 - current),
    same machinery as strategies A/D so we don't introduce a new bias.
    """
    minute = cand["minute"] or 0
    if minute < 30 or minute > 70:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals > 1:
        return None

    cur_corners = (cand["corners_home"] or 0) + (cand["corners_away"] or 0)

    # Look up corners ~10 min ago for this match — same window pattern as
    # strategy F's prior-snapshot lookup. Tolerance 9-11 min so a missed
    # cycle doesn't kill the trigger.
    match_id = cand["match_id"]
    rows = execute_query("""
        SELECT corners_home, corners_away, score_home, score_away
        FROM live_match_snapshots
        WHERE match_id = %s
          AND captured_at >= NOW() - INTERVAL '11 minutes'
          AND captured_at <= NOW() - INTERVAL '9 minutes'
        ORDER BY captured_at DESC
        LIMIT 1
    """, (match_id,))
    if not rows:
        return None

    old = rows[0]
    # Don't fire if a goal was scored in the window — that already moved odds
    if (old["score_home"] != cand["score_home"] or
            old["score_away"] != cand["score_away"]):
        return None

    old_corners = (old["corners_home"] or 0) + (old["corners_away"] or 0)
    corners_delta = cur_corners - old_corners
    if corners_delta < 3:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.45:
        return None

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"), min_val=2.10)
    if odds < 2.10:
        return None

    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a

    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)
    goals_needed = 3 - total_goals
    if goals_needed <= 0:
        return None

    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)

    min_edge = 3.0 if is_real else 4.5
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "corners_delta_10min": corners_delta,
            "corners_total": cur_corners,
            "score_state": f"{sh}-{sa}",
            "prematch_o25": round(pm_o25, 3),
            "xg_source": "live" if is_real else "shot_proxy",
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }


def _check_strategy_h(cand: dict, pm: dict, has_red_card: bool,
                      execute_query) -> dict | None:
    """
    Strategy H: HT Restart Surge — Over 2.5.

    New strategy added 2026-05-08 (3/5 AI consensus — replies 1, 3, 4).
    Thesis: matches that are 0-0 at HT but had high first-half attacking
    volume see a goal-rate spike in minutes 46-58 (managers make tactical
    changes, urgency increases). The market underprices this — HT 0-0
    drifts the headline odds toward Under.

    Entry:
      • minute 46-55 (early second half)
      • current score is 0-0 (still drifted toward Under)
      • HT-end snapshot (lookup at minute 40-46) shows attacking volume:
          - first-half xG total ≥ 0.7 (real)  OR
          - first-half SoT total ≥ 6 (proxy)
      • prematch_o25 > 0.50 (genuine attacking-match prior)
      • OU 2.5 over odds ≥ 2.10
      • no red card
      • edge ≥ 2% (real) / 3.5% (proxy)

    Bet: Over 2.5
    Edge model: same Bayesian-posterior + Poisson as A/D/G.

    HT-end snapshot lookup is a per-snapshot DB query in live mode (one query
    per match per cycle when minute is 46-55). Backfill mode uses the
    in-memory snapshot index for ~10x speedup.
    """
    minute = cand["minute"] or 0
    if minute < 46 or minute > 55:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh != 0 or sa != 0:
        return None  # Bet thesis is "0-0 at HT drifts toward Under"

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.50:
        return None

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    # Dual-line selection (INPLAY-NEW-HT-RESTART, 2026-05-10):
    #   • O2.5 if its odds > 2.80 (market still drifted toward Under — strongest edge)
    #   • else O1.5 if its odds > 1.60 (more conservative when O2.5 is shorter-priced)
    o25_odds, o25_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"), min_val=2.80)
    o15_odds, o15_is_live = _resolve_odds(cand.get("live_ou_15_over"), pm.get("prematch_ou15_over"), min_val=1.60)

    if o25_odds > 2.80:
        line = 2.5
        odds = o25_odds
        odds_is_live = o25_is_live
    elif o15_odds > 1.60:
        line = 1.5
        odds = o15_odds
        odds_is_live = o15_is_live
    else:
        return None

    # HT-end snapshot lookup. Tolerant 40-46 minute range so a missed cycle
    # at exactly minute 45 doesn't break the trigger.
    match_id = cand["match_id"]
    rows = execute_query("""
        SELECT xg_home, xg_away,
               shots_on_target_home, shots_on_target_away,
               score_home, score_away
        FROM live_match_snapshots
        WHERE match_id = %s
          AND minute BETWEEN 40 AND 46
        ORDER BY minute DESC
        LIMIT 1
    """, (match_id,))
    if not rows:
        return None

    ht = rows[0]
    if (ht["score_home"] or 0) != 0 or (ht["score_away"] or 0) != 0:
        return None  # Wasn't actually 0-0 at HT

    ht_xg_h = ht["xg_home"]
    ht_xg_a = ht["xg_away"]
    ht_sot = (ht["shots_on_target_home"] or 0) + (ht["shots_on_target_away"] or 0)

    if ht_xg_h is not None and ht_xg_a is not None:
        ht_xg_total = float(ht_xg_h) + float(ht_xg_a)
        is_real = True
        if ht_xg_total < 0.7:
            return None
    else:
        is_real = False
        if ht_sot < 6:
            return None

    xg_h, xg_a, _ = _compute_live_xg(cand)
    live_xg = xg_h + xg_a
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)

    # Score is 0-0, so total goals at FT == remaining goals.
    # _poisson_over_prob(lam, line) returns P(X > line) under Poisson(lam),
    # which is exactly P(over line) for an integer-valued goal count.
    model_prob = _poisson_over_prob(lambda_remaining, line)

    min_edge = 2.0 if is_real else 3.5
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": f"over {line}",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "line": line,
            "ht_xg_total": round(float(ht_xg_h or 0) + float(ht_xg_a or 0), 2) if ht_xg_h is not None else None,
            "ht_sot_total": ht_sot,
            "prematch_o25": round(pm_o25, 3),
            "xg_source": "live" if is_real else "shot_proxy",
            "odds_source": "live" if odds_is_live else "prematch",
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
        selection = "over 2.5"
    elif drift_pct < -15 and not xg_running_hot:
        odds = float(cand.get("live_ou_25_under") or 0)
        selection = "under 2.5"
    else:
        return None

    if odds <= 1.0:
        return None

    model_prob = _implied_prob(odds) + abs(drift_pct) / 1000.0
    edge = (model_prob - _implied_prob(odds)) * 100
    if edge < 2.0:
        edge = abs(drift_pct) / 5.0

    return {
        "market": "O/U",
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


def _check_strategy_i(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy I: Favourite Stall.

    Strong home or away favourite (prematch win prob ≥ 0.62) is stuck at 0-0
    at minute 42-65. The live 1x2 odds have drifted above 3.0 as the market
    over-penalises the visible blank score. Bivariate Poisson on remaining
    minutes confirms the favourite's win probability still exceeds the market
    implied by ≥ 4%.

    Edge: Market anchors on 0-0 scoreline and underweights the remaining 45%
    of match time where quality advantage manifests. All 5 AI reviews rated
    this as viable at 8-15 bets/day using 16% live 1x2 coverage.
    """
    minute = cand["minute"] or 0
    if minute < 42 or minute > 65:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh != 0 or sa != 0:
        return None  # Only fires at 0-0

    pm_home_prob = float(pm.get("prematch_home_prob") or 0)
    pm_away_prob = float(pm.get("prematch_away_prob") or 0)

    # Identify which side is the strong favourite
    if pm_home_prob >= 0.62:
        fav_side = "home"
        live_fav_odds = float(cand.get("live_1x2_home") or 0)
        pm_fav_prob = pm_home_prob
    elif pm_away_prob >= 0.62:
        fav_side = "away"
        live_fav_odds = float(cand.get("live_1x2_away") or 0)
        pm_fav_prob = pm_away_prob
    else:
        return None

    if live_fav_odds < 3.0:
        return None  # Market hasn't drifted enough — no edge without meaningful drift

    pm_xg_h = float(pm.get("prematch_xg_home") or 1.1)
    pm_xg_a = float(pm.get("prematch_xg_away") or 1.1)
    remaining_frac = (90.0 - minute) / 90.0
    h2_uplift = 1.05 if minute >= 45 else 1.0
    lam_h = pm_xg_h * remaining_frac * h2_uplift
    lam_a = pm_xg_a * remaining_frac * h2_uplift

    ph_win, _, pa_win = _bivariate_poisson_win_prob(lam_h, lam_a)
    model_fav_win = ph_win if fav_side == "home" else pa_win

    market_fav_prob = _implied_prob(live_fav_odds)
    edge_pct = (model_fav_win - market_fav_prob) * 100
    if edge_pct < 4.0:
        return None

    selection = "home" if fav_side == "home" else "away"
    return {
        "market": "1X2",
        "selection": selection,
        "odds": live_fav_odds,
        "model_prob": round(model_fav_win, 4),
        "edge": round(edge_pct, 2),
        "extra": {
            "fav_side": fav_side,
            "pm_fav_prob": round(pm_fav_prob, 3),
            "lam_h_remaining": round(lam_h, 3),
            "lam_a_remaining": round(lam_a, 3),
        },
    }


def _check_strategy_j(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy J: Goal Debt Over 1.5.

    High-expectation match (prematch O25 ≥ 0.62) is 0-0 at minute 30-52.
    Live Over 1.5 odds have drifted above 2.85 (Bayesian fair at min-40 0-0
    is ~2.70 — market needs to overshoot for edge to exist). Bet Over 1.5.

    Math basis (5-AI consensus): λ_remaining = λ_full × (90-m)/90 × Bayesian_update.
    Bayesian update for 0-0 at min 40 with λ=2.8 → posterior λ ≈ 2.29.
    Remaining λ at min 40 = 2.29 × 50/90 = 1.27. P(≥2 more) = 0.37 → fair 2.70.
    Enter only when market ≥ 2.85 (8-12% edge on soft/medium books).
    """
    minute = cand["minute"] or 0
    if minute < 30 or minute > 52:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh != 0 or sa != 0:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 < 0.62:
        return None

    ou15, ou15_is_live = _resolve_odds(cand.get("live_ou_15_over"), pm.get("prematch_ou15_over"), min_val=2.85)
    if ou15 < 2.85:
        return None  # No edge or no odds available

    # P(≥ 2 more goals) — need 2 more for Over 1.5 total (score is 0-0)
    pm_xg_total = float(pm.get("prematch_xg_home") or 1.1) + float(pm.get("prematch_xg_away") or 1.1)
    model_prob, posterior_lam, remaining_lam = _remaining_goals_prob(
        pm_xg_total, minute, goals_observed=0, threshold=2,
        score_home=sh, score_away=sa,
    )
    market_prob = _implied_prob(ou15)
    edge_pct = (model_prob - market_prob) * 100
    if edge_pct < 3.0:
        return None

    return {
        "market": "O/U",
        "selection": "over 1.5",
        "odds": ou15,
        "model_prob": round(model_prob, 4),
        "edge": round(edge_pct, 2),
        "posterior_rate": round(posterior_lam, 3),
        "prematch_xg_total": round(pm_xg_total, 3),
        "extra": {
            "pm_o25_prob": round(pm_o25, 3),
            "posterior_lam": round(posterior_lam, 3),
            "remaining_lam": round(remaining_lam, 3),
            "odds_source": "live" if ou15_is_live else "prematch",
        },
    }


def _check_strategy_l(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy L: Goal Contagion.

    First goal detected (0-0 → 1-0 or 0-1) at minute 15-35 in a high-expectation
    match (prematch O25 ≥ 0.55). Bet Over 2.5 FT at remaining Poisson fair odds
    when live_ou_25_over is available. Fires within a 4-minute window after goal.

    Edge: After the first goal in an open game, the scoring rate is empirically
    elevated for ~8 minutes (Dixon & Robinson 1998, Heuer 2010). The live OU 2.5
    market partially reprices but frequently overestimates the "settling" effect.
    Only fires when model edge vs market is ≥ 4%.

    Uses _goal_event_window module state populated by run_inplay_strategies.
    """
    minute = cand["minute"] or 0
    if has_red_card:
        return None

    mid = str(cand["match_id"])

    # Check that a first-goal event was recorded within the last 8 cycles
    event_cycle = _goal_event_window.get(mid)
    if event_cycle is None:
        return None
    if _cycle_count - event_cycle > 8:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals != 1:
        return None  # Only fires after the first goal (0→1)
    if minute < 15 or minute > 35:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 < 0.55:
        return None

    ou25, ou25_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"))
    if ou25 <= 1.0:
        return None

    # Need 2 more goals for Over 2.5 (1 already scored). Bayesian update with
    # goals_observed=1 reflects above-expectation pace at min 15-35 → posterior rises.
    pm_xg_total = float(pm.get("prematch_xg_home") or 1.1) + float(pm.get("prematch_xg_away") or 1.1)
    expected_by_now = pm_xg_total * (minute / 90.0)
    model_prob, posterior_lam, remaining_lam = _remaining_goals_prob(
        pm_xg_total, minute, goals_observed=1, threshold=2,
        score_home=sh, score_away=sa,
    )
    market_prob = _implied_prob(ou25)
    edge_pct = (model_prob - market_prob) * 100
    if edge_pct < 4.0:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": ou25,
        "model_prob": round(model_prob, 4),
        "edge": round(edge_pct, 2),
        "posterior_rate": round(posterior_lam, 3),
        "prematch_xg_total": round(pm_xg_total, 3),
        "extra": {
            "pm_o25_prob": round(pm_o25, 3),
            "posterior_lam": round(posterior_lam, 3),
            "remaining_lam": round(remaining_lam, 3),
            "goal_at_minute": minute,
            "expected_by_now": round(expected_by_now, 3),
            "odds_source": "live" if ou25_is_live else "prematch",
        },
    }


def _check_strategy_m(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy M: Equalizer Magnet — Over 2.5 in a 1-0 (either side) at min 30-60.

    Thesis (9-AI round-2 consensus): when a "both-attack" prematch (BTTS prob
    ≥ 0.48) reaches 1-0 in the middle third, soft books drift OU 2.5 toward
    Under because the headline scoreline anchors them. The Bayesian posterior
    over the remaining lambda — given prematch xG and the one observed goal —
    keeps the equalizer thesis priced higher than the market does.

    Entry:
      • minute 30-60
      • score is 1-0 or 0-1 (one goal scored)
      • prematch_btts_prob ≥ 0.48
      • live_ou_25_over ≥ 3.0 (market drifted into Under bias)
      • prematch_o25 ≥ 0.45 (don't fire on grind-it-out match types)
      • no red card
      • model edge ≥ 3% via _remaining_goals_prob

    Bet: Over 2.5 (need 2 more goals total since 1 is already on the board).
    BTTS Yes was the alternative selection but we don't capture live BTTS odds.
    """
    minute = cand["minute"] or 0
    if minute < 30 or minute > 60:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if not ((sh == 1 and sa == 0) or (sh == 0 and sa == 1)):
        return None

    pm_btts = float(pm.get("prematch_btts_prob") or 0)
    if pm_btts < 0.48:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 < 0.45:
        return None

    ou25, ou25_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"), min_val=3.0)
    if ou25 < 3.0:
        return None  # Market hasn't drifted enough (or no odds available at this level)

    pm_xg_total = float(pm.get("prematch_xg_home") or 1.1) + float(pm.get("prematch_xg_away") or 1.1)
    model_prob, posterior_lam, remaining_lam = _remaining_goals_prob(
        pm_xg_total, minute, goals_observed=1, threshold=2,
        score_home=sh, score_away=sa,
    )
    market_prob = _implied_prob(ou25)
    edge_pct = (model_prob - market_prob) * 100
    if edge_pct < 3.0:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": ou25,
        "model_prob": round(model_prob, 4),
        "edge": round(edge_pct, 2),
        "posterior_rate": round(posterior_lam, 3),
        "prematch_xg_total": round(pm_xg_total, 3),
        "extra": {
            "score_state": f"{sh}-{sa}",
            "pm_btts_prob": round(pm_btts, 3),
            "pm_o25_prob": round(pm_o25, 3),
            "posterior_lam": round(posterior_lam, 3),
            "remaining_lam": round(remaining_lam, 3),
            "odds_source": "live" if ou25_is_live else "prematch",
        },
    }


def _check_strategy_n(cand: dict, pm: dict, has_red_card: bool) -> dict | None:
    """
    Strategy N: Late Favourite Push — bet Home Win in 0-0 / 1-1 at min 72-80.

    Thesis (9-AI round-2 consensus): in a match where the home side was a strong
    prematch favourite (win prob ≥ 0.65), the live 1x2 market drifts the home
    odds upward as time passes scoreless or level. By minute 72-80 the drift
    often overshoots — bivariate Poisson on the remaining minutes still gives
    home a higher win probability than the implied market odds.

    Window 72-80 is intentionally tight: before 72 the market is still pricing
    efficiently; after 80 the upside is gone (too few minutes left to score).
    Spec deliberately home-only — away-favourite extension is a separate task.

    Entry:
      • minute 72-80
      • score 0-0 or 1-1 (level)
      • prematch_home_prob ≥ 0.65
      • live_1x2_home ≥ 2.20 (drifted from the prematch ~1.45 implied)
      • no red card
      • bivariate Poisson edge ≥ 3%
    """
    minute = cand["minute"] or 0
    if minute < 72 or minute > 80:
        return None
    if has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if not ((sh == 0 and sa == 0) or (sh == 1 and sa == 1)):
        return None  # Level scoreline only

    pm_home_prob = float(pm.get("prematch_home_prob") or 0)
    if pm_home_prob < 0.65:
        return None

    live_home_odds = float(cand.get("live_1x2_home") or 0)
    if live_home_odds < 2.20:
        return None  # Hasn't drifted enough — no edge

    # Bivariate Poisson on the remaining minutes. h2_uplift always engages
    # at minute 72-80; lambda also gets a period multiplier (INPLAY-PERIOD-RATES,
    # 1.20× ≥ minute 76) and a per-team score-state multiplier
    # (INPLAY-LAMBDA-STATE — late level → +5%/+5%; late imbalance → trailing
    # +15%, leader −10%).
    pm_xg_h = float(pm.get("prematch_xg_home") or 1.1)
    pm_xg_a = float(pm.get("prematch_xg_away") or 1.1)
    remaining_frac = (90.0 - minute) / 90.0
    h2_uplift = 1.05  # always in second half at minute 72-80
    period_mult = _period_multiplier(minute)
    if sh > sa:
        home_state, away_state = "leading", "trailing"
    elif sh < sa:
        home_state, away_state = "trailing", "leading"
    else:
        home_state = away_state = "level"
    state_mult_h = _state_multiplier_team(minute, home_state)
    state_mult_a = _state_multiplier_team(minute, away_state)
    lam_h = pm_xg_h * remaining_frac * h2_uplift * period_mult * state_mult_h
    lam_a = pm_xg_a * remaining_frac * h2_uplift * period_mult * state_mult_a

    ph_win, _, _ = _bivariate_poisson_win_prob(lam_h, lam_a)
    # When level at 1-1, home still needs to outscore in the remaining time;
    # bivariate result is the right model regardless of current scoreline since
    # we're pricing the remaining-minutes match outcome.
    market_prob = _implied_prob(live_home_odds)
    edge_pct = (ph_win - market_prob) * 100
    if edge_pct < 3.0:
        return None

    return {
        "market": "1X2",
        "selection": "home",
        "odds": live_home_odds,
        "model_prob": round(ph_win, 4),
        "edge": round(edge_pct, 2),
        "extra": {
            "score_state": f"{sh}-{sa}",
            "pm_home_prob": round(pm_home_prob, 3),
            "lam_h_remaining": round(lam_h, 3),
            "lam_a_remaining": round(lam_a, 3),
        },
    }


def _check_strategy_q(cand: dict, pm: dict, has_red_card: bool,
                      execute_query) -> dict | None:
    """
    Strategy Q: Red Card Overreaction Over 2.5.

    Thesis (1/5 first-round AI consensus, but unique angle): all other
    strategies *exclude* red-card matches, so this strategy is the only one
    that monetises them. When a side gets a red card in minute 15-55 with
    total goals ≤ 1, the 11-man team often dominates territory and shots,
    yet the live OU 2.5 market drifts toward Under because of the dampening
    effect on the 10-man side. We bet Over 2.5 when the 11-man team is
    showing possession dominance.

    Entry:
      • Match has a red card event in minute 15-55 (looked up per snapshot)
      • Current minute > red_minute and ≤ 75 (don't enter too late)
      • Total goals ≤ 1
      • 11-man team possession ≥ 55%
      • Live OU 2.5 over odds > 2.30 (drifted toward Under)
      • prematch xG total > 0 (sanity)
      • Bayesian-posterior + Poisson edge ≥ 3% (real) / 4.5% (proxy)

    The Bayesian model uses standard remaining-Poisson — we deliberately do
    NOT add a hand-tuned red-card uplift to the lambda. The edge comes from
    the market drift, not from a model prediction tweak. If the market is
    pricing the surge correctly, we won't see edge and won't bet.
    """
    minute = cand["minute"] or 0
    if minute > 75:
        return None  # Too late to expect another goal even with the man-up
    if not has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals > 1:
        return None

    # Find the first red card 15-55 + which team got it
    match_id = cand["match_id"]
    rows = execute_query("""
        SELECT minute, team
        FROM match_events
        WHERE match_id = %s
          AND event_type IN ('red_card', 'yellow_red_card')
          AND minute BETWEEN 15 AND 55
        ORDER BY minute ASC
        LIMIT 1
    """, (match_id,))
    if not rows:
        return None  # Red card outside the 15-55 window — not our setup

    red_minute = int(rows[0]["minute"])
    red_team = rows[0]["team"]  # 'home' or 'away'
    if minute <= red_minute:
        return None  # Snapshot must be AFTER the red card

    eleven_man_team = "away" if red_team == "home" else "home"

    poss_h = float(cand["possession_home"] or 50)
    eleven_man_poss = poss_h if eleven_man_team == "home" else (100.0 - poss_h)
    if eleven_man_poss < 55.0:
        return None

    odds, odds_is_live = _resolve_odds(cand.get("live_ou_25_over"), pm.get("prematch_ou25_over"), min_val=2.30)
    if odds <= 2.30:
        return None

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    # Determine xg-source for edge floor — match the convention used by A/D/H
    xg_h, xg_a, is_real = _compute_live_xg(cand)
    live_xg = xg_h + xg_a
    posterior = _bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining = max(1, 90 - minute)
    lambda_remaining = _scaled_remaining_lam(posterior, minute, sh, sa)
    goals_needed = 3 - total_goals
    if goals_needed <= 0:
        return None

    model_prob = _poisson_over_prob(lambda_remaining, goals_needed - 0.5)
    implied = _implied_prob(odds)
    edge = (model_prob - implied) * 100

    min_edge = 3.0 if is_real else 4.5
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "posterior_rate": round(posterior, 3),
        "prematch_xg_total": round(pm_xg_total, 2),
        "extra": {
            "score_state": f"{sh}-{sa}",
            "red_minute": red_minute,
            "red_team": red_team,
            "eleven_man_team": eleven_man_team,
            "eleven_man_possession": round(eleven_man_poss, 1),
            "xg_source": "live" if is_real else "shot_proxy",
            "odds_source": "live" if odds_is_live else "prematch",
        },
    }
