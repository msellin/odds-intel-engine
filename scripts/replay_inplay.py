"""
Replay InplayBot strategies against historical live_match_snapshots.

Two modes:

  Single-day / range (fast, terminal-only summary):
    python3 scripts/replay_inplay.py --date today
    python3 scripts/replay_inplay.py --date 2026-05-08

  Full backfill (all history, in-memory Strategy F, per-bet CSV):
    python3 scripts/replay_inplay.py --backfill
    python3 scripts/replay_inplay.py --backfill --from 2026-05-01 --to 2026-05-08

Backfill writes:
  dev/active/inplay-backfill-bets.csv      — every would-be bet, fully detailed
  dev/active/inplay-backfill-summary.txt   — per-bot / per-league / per-date roll-up

DRY-RUN ONLY. Nothing is written to the database. To persist these as
backdated paper bets later, a separate --apply flag and migration would
be needed (deliberately not implemented here — review the CSV first).

Reuses the actual `_check_strategy_*` functions from inplay_bot.py so
behavior matches live, minus:
  - the league xG gate (set to 0; we have full history)
  - the live `_score_recheck` (LivePoller may have written newer rows;
    in replay we trust the snapshot at captured_at)
  - the kill switch (irrelevant historically)

Strategy F note: it normally runs a per-snapshot DB query looking back
8-12 minutes. In backfill that's slow (~17 min/day). We replace it with
an in-memory lookup against the same snapshots we already loaded.
"""

import sys
import os
import csv
import argparse
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.db import execute_query
from workers.jobs import inplay_bot

# Disable league xG gate for replay (we have full history)
inplay_bot.MIN_LEAGUE_XG_MATCHES = 0

INPLAY_BOTS = inplay_bot.INPLAY_BOTS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "dev" / "active"
DEFAULT_CSV = DEFAULT_OUT_DIR / "inplay-backfill-bets.csv"
DEFAULT_SUMMARY = DEFAULT_OUT_DIR / "inplay-backfill-summary.txt"


# ── Data fetch ───────────────────────────────────────────────────────────────

SNAPSHOT_COLUMNS = """
    lms.match_id, lms.minute, lms.score_home, lms.score_away,
    lms.xg_home, lms.xg_away, lms.shots_home, lms.shots_away,
    lms.shots_on_target_home, lms.shots_on_target_away,
    lms.possession_home, lms.corners_home, lms.corners_away,
    lms.live_ou_15_over, lms.live_ou_15_under,
    lms.live_ou_25_over, lms.live_ou_25_under,
    lms.live_1x2_home, lms.live_1x2_draw, lms.live_1x2_away,
    lms.captured_at,
    (lms.xg_home IS NOT NULL) AS has_live_xg
"""


def fetch_snapshots(date_filter: str | None = None,
                    date_from: str | None = None,
                    date_to: str | None = None):
    """Pull snapshots that have at least one live odds field, in chronological order.

    date_filter: ISO date for a single day. Mutually exclusive with date_from/to.
    date_from/date_to: inclusive ISO date range. None on either side means open-ended.
    """
    where = ["(lms.live_ou_25_over IS NOT NULL OR lms.live_ou_15_over IS NOT NULL OR lms.live_1x2_home IS NOT NULL)",
             "lms.minute IS NOT NULL", "lms.minute BETWEEN 1 AND 90"]
    params: list = []

    if date_filter:
        where.append("lms.captured_at::date = %s")
        params.append(date_filter)
    else:
        if date_from:
            where.append("lms.captured_at::date >= %s")
            params.append(date_from)
        if date_to:
            where.append("lms.captured_at::date <= %s")
            params.append(date_to)

    sql = f"""
        SELECT {SNAPSHOT_COLUMNS}
        FROM live_match_snapshots lms
        JOIN matches m ON m.id = lms.match_id
        WHERE {' AND '.join(where)}
        ORDER BY lms.captured_at ASC
    """
    print(f"Fetching snapshots (filter={date_filter or f'{date_from or ...}..{date_to or ...}'})...")
    rows = execute_query(sql, tuple(params) if params else None)
    print(f"  {len(rows):,} snapshots with live odds")
    return rows


def fetch_prematch(match_ids: list[str]) -> dict[str, dict]:
    """Pull prematch model + team-season stats for all matches, keyed on str(match_id)."""
    print(f"Fetching prematch data for {len(match_ids):,} matches...")
    rows = execute_query("""
        SELECT
            m.id AS match_id, m.league_id,
            l.tier AS league_tier,
            COALESCE(tss_h.goals_for_avg::numeric, 1.3) AS prematch_xg_home,
            COALESCE(tss_a.goals_for_avg::numeric, 1.3) AS prematch_xg_away,
            p_ou.model_probability   AS prematch_o25_prob,
            p_btts.model_probability AS prematch_btts_prob,
            p_home.model_probability AS prematch_home_prob,
            p_away.model_probability AS prematch_away_prob
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        LEFT JOIN LATERAL (
            SELECT goals_for_avg FROM team_season_stats
            WHERE team_api_id = m.home_team_api_id AND league_api_id = l.api_football_id
            ORDER BY season DESC LIMIT 1
        ) tss_h ON TRUE
        LEFT JOIN LATERAL (
            SELECT goals_for_avg FROM team_season_stats
            WHERE team_api_id = m.away_team_api_id AND league_api_id = l.api_football_id
            ORDER BY season DESC LIMIT 1
        ) tss_a ON TRUE
        LEFT JOIN predictions p_ou   ON p_ou.match_id   = m.id AND p_ou.market   = 'over25'   AND p_ou.source   = 'ensemble'
        LEFT JOIN predictions p_btts ON p_btts.match_id = m.id AND p_btts.market = 'btts_yes' AND p_btts.source = 'ensemble'
        LEFT JOIN predictions p_home ON p_home.match_id = m.id AND p_home.market = '1x2_home' AND p_home.source = 'ensemble'
        LEFT JOIN predictions p_away ON p_away.match_id = m.id AND p_away.market = '1x2_away' AND p_away.source = 'ensemble'
        WHERE m.id = ANY(%s::uuid[])
    """, (match_ids,))
    return {str(r["match_id"]): r for r in rows}


def fetch_red_cards(match_ids: list[str]) -> set[str]:
    rows = execute_query("""
        SELECT DISTINCT match_id FROM match_events
        WHERE match_id = ANY(%s::uuid[])
          AND event_type IN ('red_card', 'yellow_red_card')
    """, (match_ids,))
    return {str(r["match_id"]) for r in rows}


def fetch_existing_inplay_bets() -> set[tuple[str, str]]:
    """Return set of (match_id, bot_name) for inplay bets already in simulated_bets.

    Used by backfill to skip duplicates — the bot has placed 2 bets historically
    (one Apr 30, one today). Don't re-emit those in the backfill CSV.
    """
    rows = execute_query("""
        SELECT sb.match_id, b.name AS bot_name
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE b.name LIKE 'inplay_%%'
    """)
    return {(str(r["match_id"]), r["bot_name"]) for r in rows}


def fetch_match_meta(match_ids: list[str]) -> dict[str, dict]:
    """Final scores + team/league names for readable CSV output."""
    rows = execute_query("""
        SELECT m.id AS match_id, m.score_home, m.score_away, m.status, m.date,
               l.name AS league, l.country AS league_country, l.tier AS league_tier,
               th.name AS home_team, ta.name AS away_team
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.id = ANY(%s::uuid[])
    """, (match_ids,))
    return {str(r["match_id"]): r for r in rows}


# ── Settlement (mirrors workers/jobs/settlement.py:settle_bet_result) ────────

def settle_bet(market: str, selection: str, odds: float,
               score_home: int, score_away: int) -> tuple[str, float]:
    m = (market or "").lower()
    s = (selection or "").lower()
    total = score_home + score_away

    won = False
    if m == "1x2":
        if s == "home" and score_home > score_away:
            won = True
        elif s in ("draw", "x") and score_home == score_away:
            won = True
        elif s == "away" and score_away > score_home:
            won = True
    elif "over_under" in m or "o/u" in m or m == "ou_25":
        line = 2.5
        for token in m.split("_") + s.split():
            try:
                v = float(token) if "." in token else (int(token) / 10 if len(token) == 2 else float(token))
                if 0 < v < 10:
                    line = v
                    break
            except ValueError:
                continue
        if "over" in s and total > line:
            won = True
        elif "under" in s and total < line:
            won = True
    else:
        return ("void", 0.0)

    return ("won", odds - 1.0) if won else ("lost", -1.0)


# ── In-memory Strategy F ─────────────────────────────────────────────────────

# Strategy F in inplay_bot.py runs a per-snapshot DB query to find a snapshot
# 8-12 minutes BEFORE NOW(). For replay that's both wrong (we want 8-12 min
# before the candidate's captured_at, not before "now") and slow (~13K queries).
# Instead we pre-build {match_id: sorted list of (captured_at, ou_over,
# 1x2_home, 1x2_away, score_h, score_a, minute)} and look up in memory.

def build_snapshot_index(snapshots: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = defaultdict(list)
    for s in snapshots:
        idx[str(s["match_id"])].append(s)
    # Already chronological (we ordered by captured_at ASC), but defensive sort:
    for arr in idx.values():
        arr.sort(key=lambda r: r["captured_at"])
    return idx


def _parse_dt(v):
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def replay_strategy_h(cand: dict, pm: dict, has_red_card: bool,
                      snapshot_idx: dict[str, list[dict]]) -> dict | None:
    """In-memory port of inplay_bot._check_strategy_h for backfill replay."""
    minute = cand["minute"] or 0
    if minute < 46 or minute > 55 or has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    if sh != 0 or sa != 0:
        return None

    pm_o25 = float(pm.get("prematch_o25_prob") or 0)
    if pm_o25 <= 0.50:
        return None

    pm_xg_total = float(pm.get("prematch_xg_home") or 0) + float(pm.get("prematch_xg_away") or 0)
    if pm_xg_total <= 0:
        return None

    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) < 2.10:
        return None
    odds = float(odds)

    # HT-end snapshot lookup — minute 40-46, latest first
    mid = str(cand["match_id"])
    history = snapshot_idx.get(mid, [])
    ht = None
    for s in reversed(history):
        m = s.get("minute") or 0
        if 40 <= m <= 46:
            ht = s
            break
    if not ht:
        return None
    if (ht["score_home"] or 0) != 0 or (ht["score_away"] or 0) != 0:
        return None

    ht_xg_h = ht.get("xg_home")
    ht_xg_a = ht.get("xg_away")
    ht_sot = (ht.get("shots_on_target_home") or 0) + (ht.get("shots_on_target_away") or 0)

    if ht_xg_h is not None and ht_xg_a is not None:
        is_real = True
        if float(ht_xg_h) + float(ht_xg_a) < 0.7:
            return None
    else:
        is_real = False
        if ht_sot < 6:
            return None

    xg_h, xg_a, _ = inplay_bot._compute_live_xg(cand)
    live_xg = xg_h + xg_a
    posterior = inplay_bot._bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = posterior * remaining_minutes / 90.0
    model_prob = inplay_bot._poisson_over_prob(lambda_remaining, 2.5)

    min_edge = 2.0 if is_real else 3.5
    implied = inplay_bot._implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "extra": {
            "ht_sot_total": ht_sot,
            "prematch_o25": round(pm_o25, 3),
        },
    }


def replay_strategy_g(cand: dict, pm: dict, has_red_card: bool,
                      snapshot_idx: dict[str, list[dict]]) -> dict | None:
    """In-memory port of inplay_bot._check_strategy_g for backfill replay.

    Mirrors live logic but the "corners 9-11 min ago" lookup is done against
    the in-memory snapshot index keyed on the candidate's captured_at — not
    a per-snapshot DB query.
    """
    minute = cand["minute"] or 0
    if minute < 30 or minute > 70 or has_red_card:
        return None

    sh, sa = cand["score_home"] or 0, cand["score_away"] or 0
    total_goals = sh + sa
    if total_goals > 1:
        return None

    cand_t = _parse_dt(cand["captured_at"])
    window_lo = cand_t - timedelta(minutes=11)
    window_hi = cand_t - timedelta(minutes=9)

    mid = str(cand["match_id"])
    history = snapshot_idx.get(mid, [])
    old = None
    for s in reversed(history):
        st = _parse_dt(s["captured_at"])
        if st > window_hi:
            continue
        if st < window_lo:
            break
        old = s
        break
    if not old:
        return None

    if (old["score_home"] != cand["score_home"] or
            old["score_away"] != cand["score_away"]):
        return None

    cur_corners = (cand["corners_home"] or 0) + (cand["corners_away"] or 0)
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

    odds = cand.get("live_ou_25_over")
    if not odds or float(odds) < 2.10:
        return None
    odds = float(odds)

    xg_h, xg_a, is_real = inplay_bot._compute_live_xg(cand)
    live_xg = xg_h + xg_a

    posterior = inplay_bot._bayesian_posterior(pm_xg_total, live_xg, minute)
    remaining_minutes = max(1, 90 - minute)
    lambda_remaining = posterior * remaining_minutes / 90.0
    goals_needed = 3 - total_goals
    if goals_needed <= 0:
        return None

    model_prob = inplay_bot._poisson_over_prob(lambda_remaining, goals_needed - 0.5)
    min_edge = 3.0 if is_real else 4.5
    implied = inplay_bot._implied_prob(odds)
    edge = (model_prob - implied) * 100
    if edge < min_edge:
        return None

    return {
        "market": "O/U",
        "selection": "over 2.5",
        "odds": odds,
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 2),
        "extra": {
            "corners_delta_10min": corners_delta,
            "corners_total": cur_corners,
        },
    }


def replay_strategy_f(cand: dict, pm: dict, has_red_card: bool,
                      snapshot_idx: dict[str, list[dict]]) -> dict | None:
    """In-memory port of inplay_bot._check_strategy_f for backfill replay.

    Logic identical to live, except the 'snapshot 8-12 minutes before this
    candidate' lookup is done against the in-memory index keyed on the
    candidate's captured_at (not NOW()).
    """
    import math

    minute = cand["minute"] or 0
    if minute < 10 or has_red_card:
        return None

    cand_t = _parse_dt(cand["captured_at"])
    window_lo = cand_t - timedelta(minutes=12)
    window_hi = cand_t - timedelta(minutes=8)

    mid = str(cand["match_id"])
    history = snapshot_idx.get(mid, [])
    old = None
    for s in reversed(history):
        st = _parse_dt(s["captured_at"])
        if st > window_hi:
            continue
        if st < window_lo:
            break
        old = s
        break
    if not old:
        return None

    old_ou = float(old.get("live_ou_25_over") or 0)
    cur_ou = float(cand.get("live_ou_25_over") or 0)
    if old_ou <= 0 or cur_ou <= 0:
        return None
    if (old["score_home"] != cand["score_home"] or
            old["score_away"] != cand["score_away"]):
        return None

    drift_pct = (cur_ou - old_ou) / old_ou * 100
    if abs(drift_pct) < 15:
        return None

    xg_h, xg_a, is_real = inplay_bot._compute_live_xg(cand)
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

    model_prob = inplay_bot._implied_prob(odds) + abs(drift_pct) / 1000.0
    edge = (model_prob - inplay_bot._implied_prob(odds)) * 100
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
        },
    }


# ── Replay engine ────────────────────────────────────────────────────────────

def run_replay(snapshots: list[dict],
               prematch: dict[str, dict],
               red_cards: set[str],
               match_meta: dict[str, dict],
               existing_bets: set[tuple[str, str]] | None = None,
               skip_f: bool = False,
               use_in_memory_f: bool = True) -> tuple[list[dict], dict]:
    """
    Returns (placed_bets, settled_aggregates).
      placed_bets: list of bet dicts with all fields needed for CSV output.
      settled_aggregates: by-bot rollup.

    existing_bets: set of (match_id, bot_name) already in simulated_bets — skipped
    so the backfill CSV never contains a duplicate of a bet that was actually placed.
    """
    snapshot_idx = build_snapshot_index(snapshots) if (use_in_memory_f and not skip_f) else {}
    existing_bets = existing_bets or set()

    bots_to_run = [b for b in INPLAY_BOTS if not (skip_f and b == "inplay_f")]
    placed: dict[tuple[str, str], dict] = {}
    eval_count = 0
    skipped_dupe_keys: set[tuple[str, str]] = set()

    for cand in snapshots:
        eval_count += 1
        mid = str(cand["match_id"])
        pm = prematch.get(mid)
        if not pm:
            continue

        has_red_card = mid in red_cards

        for bot_name in bots_to_run:
            key = (mid, bot_name)
            if key in placed:
                continue
            if key in existing_bets:
                # This (match, bot) pair already has a real bet in DB — don't re-emit.
                skipped_dupe_keys.add(key)
                continue
            try:
                if bot_name == "inplay_f" and use_in_memory_f:
                    trigger = replay_strategy_f(cand, pm, has_red_card, snapshot_idx)
                elif bot_name == "inplay_g" and use_in_memory_f:
                    trigger = replay_strategy_g(cand, pm, has_red_card, snapshot_idx)
                elif bot_name == "inplay_h" and use_in_memory_f:
                    trigger = replay_strategy_h(cand, pm, has_red_card, snapshot_idx)
                else:
                    trigger = inplay_bot._check_strategy(
                        bot_name, cand, pm, has_red_card, execute_query
                    )
            except Exception:
                continue
            if not trigger:
                continue

            placed[key] = {
                "match_id": mid,
                "bot_name": bot_name,
                "market": trigger["market"],
                "selection": trigger["selection"],
                "odds": float(trigger["odds"]),
                "edge_pct": float(trigger["edge"]),
                "model_prob": float(trigger.get("model_prob", 0)),
                "minute": cand["minute"],
                "score_at_bet": f"{cand['score_home']}-{cand['score_away']}",
                "captured_at": cand["captured_at"],
            }

    print(f"  evaluated: {eval_count:,} snapshots")
    print(f"  placed: {len(placed):,} unique (match × bot) bets")
    if skipped_dupe_keys:
        print(f"  skipped (already in simulated_bets): {len(skipped_dupe_keys)}")

    # Settle + enrich
    bets = []
    for (mid, bot_name), bet in placed.items():
        meta = match_meta.get(mid, {})
        final = meta if meta.get("status") == "finished" else None
        if final:
            result, pnl = settle_bet(
                bet["market"], bet["selection"], bet["odds"],
                int(final["score_home"]), int(final["score_away"])
            )
            final_score = f"{final['score_home']}-{final['score_away']}"
        else:
            result, pnl = ("pending", 0.0)
            final_score = ""

        bets.append({
            **bet,
            "result": result,
            "pnl": pnl,
            "final_score": final_score,
            "league": meta.get("league") or "?",
            "league_country": meta.get("league_country") or "",
            "league_tier": meta.get("league_tier"),
            "home_team": meta.get("home_team") or "?",
            "away_team": meta.get("away_team") or "?",
            "match_date": meta.get("date"),
        })

    return bets, _aggregate_by_bot(bets)


def _aggregate_by_bot(bets: list[dict]) -> dict:
    by_bot: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "won": 0, "lost": 0, "pending": 0,
        "stake": 0.0, "pnl": 0.0,
        "edge_sum": 0.0, "odds_sum": 0.0,
    })
    for b in bets:
        bb = by_bot[b["bot_name"]]
        bb["n"] += 1
        bb["edge_sum"] += b["edge_pct"]
        bb["odds_sum"] += b["odds"]
        if b["result"] == "pending":
            bb["pending"] += 1
            continue
        bb["stake"] += 1.0
        bb["pnl"] += b["pnl"]
        if b["result"] == "won":
            bb["won"] += 1
        else:
            bb["lost"] += 1
    return by_bot


# ── Output ───────────────────────────────────────────────────────────────────

def write_csv(bets: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "captured_at", "match_date", "league_country", "league", "league_tier",
        "home_team", "away_team", "minute", "score_at_bet",
        "bot_name", "market", "selection", "odds", "edge_pct", "model_prob",
        "final_score", "result", "pnl",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        # Sort: chronological, so review feels like a bet log
        for row in sorted(bets, key=lambda r: (r["captured_at"], r["bot_name"])):
            w.writerow(row)
    print(f"\nCSV: {path}  ({len(bets):,} rows)")


def write_summary(bets: list[dict], by_bot: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("InplayBot Backfill — DRY RUN (no DB writes)")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    lines.append(f"Total bets: {len(bets):,}  "
                 f"(won {sum(1 for b in bets if b['result']=='won')}, "
                 f"lost {sum(1 for b in bets if b['result']=='lost')}, "
                 f"pending {sum(1 for b in bets if b['result']=='pending')})")
    if bets:
        lines.append(f"Date range: {min(b['captured_at'] for b in bets)}  →  "
                     f"{max(b['captured_at'] for b in bets)}")
    lines.append("")

    # Per-bot
    lines.append("Per-strategy roll-up (settled only):")
    lines.append(f"{'Bot':<18} {'Bets':>5} {'Won':>5} {'Lost':>5} {'Pend':>5} "
                 f"{'Win%':>6} {'AvgOdds':>8} {'AvgEdge%':>9} {'Profit':>9} {'ROI%':>7}")
    lines.append("-" * 95)
    total = {"settled": 0, "won": 0, "lost": 0, "pnl": 0.0, "stake": 0.0, "n": 0}
    for bot in INPLAY_BOTS:
        bb = by_bot.get(bot)
        if not bb or bb["n"] == 0:
            lines.append(f"{bot:<18} {0:>5}")
            continue
        settled_n = bb["won"] + bb["lost"]
        winp = bb["won"] / settled_n * 100 if settled_n else 0
        roi = bb["pnl"] / bb["stake"] * 100 if bb["stake"] else 0
        avg_odds = bb["odds_sum"] / bb["n"]
        avg_edge = bb["edge_sum"] / bb["n"]
        lines.append(
            f"{bot:<18} {bb['n']:>5} {bb['won']:>5} {bb['lost']:>5} {bb['pending']:>5} "
            f"{winp:>5.1f}% {avg_odds:>8.2f} {avg_edge:>8.2f}% {bb['pnl']:>+9.2f} {roi:>+6.1f}%"
        )
        total["n"] += bb["n"]
        total["settled"] += settled_n
        total["won"] += bb["won"]
        total["lost"] += bb["lost"]
        total["pnl"] += bb["pnl"]
        total["stake"] += bb["stake"]
    lines.append("-" * 95)
    if total["stake"]:
        winp = total["won"] / total["settled"] * 100 if total["settled"] else 0
        roi = total["pnl"] / total["stake"] * 100
        lines.append(f"{'TOTAL':<18} {total['n']:>5} {total['won']:>5} {total['lost']:>5} "
                     f"{'':>5} {winp:>5.1f}% {'':>8} {'':>9} {total['pnl']:>+9.2f} {roi:>+6.1f}%")
    lines.append("")

    # Per-league (top 20 by bet count)
    lines.append("Per-league roll-up (top 20 by bet count):")
    lines.append(f"{'League':<35} {'Bets':>5} {'Won':>5} {'Lost':>5} {'ROI%':>7}")
    lines.append("-" * 65)
    by_league: dict[str, dict] = defaultdict(lambda: {"n": 0, "won": 0, "lost": 0, "pnl": 0.0, "stake": 0.0})
    for b in bets:
        key = f"{b['league_country']} — {b['league']}".strip(" —")
        bb = by_league[key]
        bb["n"] += 1
        if b["result"] == "won":
            bb["won"] += 1
            bb["stake"] += 1
            bb["pnl"] += b["pnl"]
        elif b["result"] == "lost":
            bb["lost"] += 1
            bb["stake"] += 1
            bb["pnl"] += b["pnl"]
    for league, bb in sorted(by_league.items(), key=lambda x: -x[1]["n"])[:20]:
        roi = bb["pnl"] / bb["stake"] * 100 if bb["stake"] else 0
        lines.append(f"{league[:35]:<35} {bb['n']:>5} {bb['won']:>5} {bb['lost']:>5} {roi:>+6.1f}%")
    lines.append("")

    # Per-date
    lines.append("Per-date roll-up:")
    lines.append(f"{'Date':<12} {'Bets':>5} {'Won':>5} {'Lost':>5} {'ROI%':>7}")
    lines.append("-" * 40)
    by_date: dict[str, dict] = defaultdict(lambda: {"n": 0, "won": 0, "lost": 0, "pnl": 0.0, "stake": 0.0})
    for b in bets:
        d = str(b["captured_at"])[:10]
        bb = by_date[d]
        bb["n"] += 1
        if b["result"] in ("won", "lost"):
            bb["stake"] += 1
            bb["pnl"] += b["pnl"]
            if b["result"] == "won":
                bb["won"] += 1
            else:
                bb["lost"] += 1
    for d in sorted(by_date):
        bb = by_date[d]
        roi = bb["pnl"] / bb["stake"] * 100 if bb["stake"] else 0
        lines.append(f"{d:<12} {bb['n']:>5} {bb['won']:>5} {bb['lost']:>5} {roi:>+6.1f}%")
    lines.append("")
    lines.append("Caveat: prematch xG falls back to 1.3 + 1.3 (league average) when")
    lines.append("team_season_stats is missing. For low-scoring leagues this overstates")
    lines.append("Under 2.5 edges; treat strategy E ROI as an upper bound.")

    out = "\n".join(lines)
    path.write_text(out)
    print(f"Summary: {path}")
    print()
    # Echo summary to stdout too
    print(out)


# ── Entrypoints ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--date", default=None,
                   help="ISO date or 'today'. Single-day mode.")
    g.add_argument("--backfill", action="store_true",
                   help="Backfill mode: writes CSV + summary, supports --from/--to.")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="Backfill start date (ISO). Default: 2026-04-27 (bot launch).")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="Backfill end date (ISO). Default: today.")
    parser.add_argument("--skip-f", action="store_true",
                        help="Skip strategy F entirely (only relevant for --date mode).")
    parser.add_argument("--csv", default=str(DEFAULT_CSV),
                        help=f"Backfill CSV output path (default: {DEFAULT_CSV})")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY),
                        help=f"Backfill summary path (default: {DEFAULT_SUMMARY})")
    args = parser.parse_args()

    print("=" * 70)
    print("  InplayBot replay — DRY RUN (no DB writes)")
    print("=" * 70)
    print()

    if args.backfill:
        date_from = args.date_from or "2026-04-27"
        date_to = args.date_to or date.today().isoformat()
        snapshots = fetch_snapshots(date_from=date_from, date_to=date_to)
    else:
        date_filter = None
        if args.date == "today":
            date_filter = date.today().isoformat()
        elif args.date:
            date_filter = args.date
        snapshots = fetch_snapshots(date_filter=date_filter)

    if not snapshots:
        print("No snapshots in selected window — aborting.")
        return

    distinct_match_ids = list({str(s["match_id"]) for s in snapshots})
    prematch = fetch_prematch(distinct_match_ids)
    print(f"  prematch hits: "
          f"{sum(1 for v in prematch.values() if v.get('prematch_o25_prob') is not None):,}")
    red_cards = fetch_red_cards(distinct_match_ids)
    print(f"  matches with red cards: {len(red_cards)}")
    match_meta = fetch_match_meta(distinct_match_ids)
    finished = sum(1 for v in match_meta.values() if v.get("status") == "finished")
    print(f"  finished matches: {finished:,}")
    existing_bets = fetch_existing_inplay_bets()
    print(f"  existing inplay bets in simulated_bets: {len(existing_bets)} "
          f"(will be skipped to avoid duplicates)")
    print()

    if args.backfill:
        # In-memory strategy F always on for backfill (fast + correct against historical timestamps)
        bets, by_bot = run_replay(snapshots, prematch, red_cards, match_meta,
                                  existing_bets=existing_bets,
                                  skip_f=False, use_in_memory_f=True)
        write_csv(bets, Path(args.csv))
        write_summary(bets, by_bot, Path(args.summary))
    else:
        bets, by_bot = run_replay(snapshots, prematch, red_cards, match_meta,
                                  existing_bets=existing_bets,
                                  skip_f=args.skip_f, use_in_memory_f=not args.skip_f)
        # Single-day: terminal-only summary
        write_summary(bets, by_bot, Path(args.summary))


if __name__ == "__main__":
    main()
