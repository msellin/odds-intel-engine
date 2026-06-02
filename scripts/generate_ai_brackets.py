"""
OddsIntel — WC AI Ghost Bracket Generator (WC-AI-GHOSTS)

Generates a complete bracket + group-standings prediction for each of 5 named
AI strategies. The output is written to the same tables real users use:

    wc_group_predictions  — 48 rows per AI (12 groups × 4 positions)
    wc_bracket_picks      — 32 R32 + 16 R16 + 8 QF + 4 SF + 2 F + 1 champion = 63 rows
    wc_bracket_meta       — 1 row per AI (carries ai_label, no user_id)

…with `ai_label` set and `user_id` NULL. The bracket-scoring job loops over
ALL meta rows, so the AI ghosts participate in the combined leaderboard
automatically — no second code path.

Strategies:

    OddsIntel Elite AI   — full stack: AF predictions if present, else our
                            national_team_v1, else international ELO.
    OddsIntel Pro AI     — calibrated subset: national_team_v1 only
                            (no AF predictions; no ELO fallback).
    OddsIntel Free AI    — basic ELO ranking only.
    Market Implied       — implied probabilities from latest 1X2
                            odds_snapshots. Falls back to ELO when WC
                            bookmakers haven't priced anything yet
                            (pre-launch this is expected — there are
                            no WC odds in `odds_snapshots` today).
    Chalk                — naive baseline: higher-ELO team always wins
                            (no probability shaping at all).

For group standings, each strategy needs a per-team "tournament strength"
score. We use the team's win-probability against an average-of-group
opponent, then rank 1st..4th by that score. This is deterministic given
a frozen model + ELO snapshot.

For the bracket, each strategy needs to pick winners between two teams. We
use the same per-team strength score:
  - R32: top 32 strongest teams across all groups (top 2 + best 8 thirds is
         what the real format produces; we pre-compute the same teams the
         strategy would predict as advancing).
  - R16..F: pair adjacent slots in the strategy's R32 list and pick the
         higher-strength team.
  - Champion: highest-strength team that reached the final.

Idempotency:
    Re-running before lock OVERWRITES picks (DELETE existing AI rows for
    that ai_label, then re-insert). After 2026-06-11 19:00 UTC the script
    refuses to overwrite — same lock the human bracket uses.

Run:
    python scripts/generate_ai_brackets.py            # all 5 strategies
    python scripts/generate_ai_brackets.py --strategy chalk
    python scripts/generate_ai_brackets.py --force    # ignore lock (dev only)
    python scripts/generate_ai_brackets.py --dry-run  # show counts, no write
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

# Local imports — kept inside main() to avoid load-on-import side-effects in
# the smoke-test source-inspection path.

console = Console()
log = logging.getLogger(__name__)

# ── AI strategy registry ──────────────────────────────────────────────────

# These labels are the leaderboard-facing names. The leading robot glyph is
# added by the FE renderer (`is_ai` flag), not stored in the DB — so the
# label remains greppable.
AI_STRATEGIES: list[str] = [
    "OddsIntel Elite AI",
    "OddsIntel Pro AI",
    "OddsIntel Free AI",
    "Market Implied",
    "Chalk",
]

# WC-GHOSTS-LAYER-2 (2026-06-02): anonymous AI variants that fill out the
# leaderboard so it doesn't feel empty pre-WC. Each "Player 001..040" is a
# real bracket — same scoring path as the 5 named strategies — but its
# strength score is the Elite/Pro/Free/Chalk baseline (rotated by seed)
# PLUS a deterministic per-team gaussian perturbation. Different seed →
# different bracket. Frontend muted-greys these so they look like ordinary
# players ranking around the user, not blatantly labelled "AI variants".
#
# Why mixing baselines instead of just perturbing Elite: real users have
# varied prediction quality. If all 40 variants are Elite-perturbed they
# cluster at the top and the leaderboard feels like "AI on top, human at
# bottom". Rotating across 4 baselines spreads variants across the score
# range naturally.
ANONYMOUS_VARIANT_COUNT = 40
ANONYMOUS_BASELINES: list[str] = [
    "OddsIntel Elite AI",
    "OddsIntel Pro AI",
    "OddsIntel Free AI",
    "Chalk",
]


def _anonymous_variant_labels() -> list[str]:
    """Stable label set: 'Player 001' .. 'Player 040'."""
    return [f"Player {i:03d}" for i in range(1, ANONYMOUS_VARIANT_COUNT + 1)]


def is_anonymous_variant(ai_label: str | None) -> bool:
    """Pattern detection for the frontend / scoring code. Anonymous = the
    'Player NNN' pattern. Named strategies use proper names like
    'OddsIntel Elite AI'."""
    return bool(ai_label) and ai_label.startswith("Player ") and ai_label[7:].isdigit()

# Lock anchor — same as wc-bracket lock (frontend `WC_FIRST_KICKOFF_ISO`).
WC_FIRST_KICKOFF = datetime(2026, 6, 11, 19, 0, 0, tzinfo=timezone.utc)

WC_LEAGUE_AF_ID = 1


# ── Data loaders ──────────────────────────────────────────────────────────

def _load_wc_group_fixtures() -> list[dict]:
    """All WC group-stage fixtures (date < 2026-06-28). Returns:
        [{id, date, home_team_id, away_team_id, league_id}]
    """
    from workers.api_clients.db import execute_query
    return execute_query(
        """
        SELECT m.id::text AS id, m.date,
               m.home_team_id::text AS home_team_id,
               m.away_team_id::text AS away_team_id,
               m.league_id::text AS league_id
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = %s
          AND m.date::date < DATE '2026-06-28'
        ORDER BY m.date ASC
        """,
        (WC_LEAGUE_AF_ID,),
    )


def _build_groups(group_fixtures: list[dict]) -> list[tuple[str, list[str]]]:
    """Union-find groups → [(label, [team_id,...4]),...]. Labels A..L in
    earliest-kickoff order (matches the frontend's `deriveGroups`)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for fx in group_fixtures:
        h, a = fx["home_team_id"], fx["away_team_id"]
        parent.setdefault(h, h)
        parent.setdefault(a, a)
        union(h, a)

    teams_by_root: dict[str, set[str]] = defaultdict(set)
    earliest_by_root: dict[str, str] = {}
    for fx in group_fixtures:
        root_h = find(fx["home_team_id"])
        root_a = find(fx["away_team_id"])
        # they will be equal after union, but defensive:
        root = root_h
        teams_by_root[root].add(fx["home_team_id"])
        teams_by_root[root].add(fx["away_team_id"])
        # record the earliest kickoff per group → label order
        cur = earliest_by_root.get(root)
        d = str(fx["date"])
        if cur is None or d < cur:
            earliest_by_root[root] = d

    # Order groups by earliest fixture kickoff
    roots = sorted(teams_by_root.keys(),
                   key=lambda r: earliest_by_root.get(r, "9999"))
    alpha = "ABCDEFGHIJKL"
    out: list[tuple[str, list[str]]] = []
    for i, root in enumerate(roots):
        label = alpha[i] if i < len(alpha) else f"G{i+1}"
        out.append((label, sorted(teams_by_root[root])))
    return out


def _load_team_names(team_ids: set[str]) -> dict[str, str]:
    from workers.api_clients.db import execute_query
    if not team_ids:
        return {}
    rows = execute_query(
        "SELECT id::text AS id, name FROM teams WHERE id = ANY(%s::uuid[])",
        (list(team_ids),),
    )
    return {r["id"]: r["name"] for r in rows}


def _load_international_elo(team_ids: set[str]) -> dict[str, float]:
    """Latest international ELO per team. Falls back silently to {} when
    the table is empty (pre-Phase 3 dev DB)."""
    from workers.api_clients.db import execute_query
    if not team_ids:
        return {}
    try:
        rows = execute_query(
            """
            SELECT DISTINCT ON (team_id) team_id::text AS team_id, elo_rating
            FROM team_elo_international
            WHERE team_id = ANY(%s::uuid[])
            ORDER BY team_id, match_date DESC
            """,
            (list(team_ids),),
        )
    except Exception:
        return {}
    return {r["team_id"]: float(r["elo_rating"]) for r in rows}


def _load_national_team_predictions(match_ids: set[str]) -> dict[str, dict]:
    """{match_id: {home, draw, away}} pulled from `predictions` with
    source='national_team_v1'. Missing markets → entry is omitted."""
    from workers.api_clients.db import execute_query
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT match_id::text AS match_id, market, model_probability
        FROM predictions
        WHERE source = 'national_team_v1'
          AND match_id = ANY(%s::uuid[])
        """,
        (list(match_ids),),
    )
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        if r["market"] == "1x2_home":
            out[r["match_id"]]["home"] = float(r["model_probability"])
        elif r["market"] == "1x2_draw":
            out[r["match_id"]]["draw"] = float(r["model_probability"])
        elif r["market"] == "1x2_away":
            out[r["match_id"]]["away"] = float(r["model_probability"])
    # Only keep matches with all 3 probs.
    return {k: v for k, v in out.items() if {"home", "draw", "away"} <= set(v)}


def _load_af_predictions(match_ids: set[str]) -> dict[str, dict]:
    """{match_id: {home, draw, away}} from AF (source='api_football'). Same
    shape as the national-team loader so the Elite-AI strategy can fall back
    cleanly when AF doesn't carry WC predictions."""
    from workers.api_clients.db import execute_query
    if not match_ids:
        return {}
    try:
        rows = execute_query(
            """
            SELECT match_id::text AS match_id, market, model_prob
            FROM predictions
            WHERE source = 'api_football'
              AND match_id = ANY(%s::uuid[])
            """,
            (list(match_ids),),
        )
    except Exception:
        return {}
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        if r["market"] == "1x2_home":
            out[r["match_id"]]["home"] = float(r["model_prob"])
        elif r["market"] == "1x2_draw":
            out[r["match_id"]]["draw"] = float(r["model_prob"])
        elif r["market"] == "1x2_away":
            out[r["match_id"]]["away"] = float(r["model_prob"])
    return {k: v for k, v in out.items() if {"home", "draw", "away"} <= set(v)}


def _load_market_implied(match_ids: set[str]) -> dict[str, dict]:
    """{match_id: {home, draw, away}} normalised from latest 1X2 odds_snapshots
    (any bookmaker). Pre-launch this is usually empty — strategy falls back
    to ELO. We pick the most recent snapshot per (match, selection)."""
    from workers.api_clients.db import execute_query
    if not match_ids:
        return {}
    try:
        rows = execute_query(
            """
            SELECT DISTINCT ON (match_id, selection)
                   match_id::text AS match_id, selection, odds
            FROM odds_snapshots
            WHERE market = '1x2'
              AND match_id = ANY(%s::uuid[])
            ORDER BY match_id, selection, captured_at DESC
            """,
            (list(match_ids),),
        )
    except Exception:
        return {}
    raw: dict[str, dict] = defaultdict(dict)
    for r in rows:
        try:
            odds = float(r["odds"])
            if odds <= 1.0:
                continue
            raw[r["match_id"]][r["selection"]] = 1.0 / odds
        except (TypeError, ValueError):
            continue
    out: dict[str, dict] = {}
    for mid, sels in raw.items():
        if {"home", "draw", "away"} <= set(sels):
            s = sels["home"] + sels["draw"] + sels["away"]
            if s > 0:
                out[mid] = {
                    "home": sels["home"] / s,
                    "draw": sels["draw"] / s,
                    "away": sels["away"] / s,
                }
    return out


# ── Strategy core: per-team strength score ─────────────────────────────────

def _strength_from_elo(elo: dict[str, float], team_ids: list[str]) -> dict[str, float]:
    """Pure-ELO strength: each team's value is its ELO rating. Missing teams
    default to the mean of the present set so they don't sink to 0."""
    if not elo:
        return {tid: 0.5 for tid in team_ids}
    present = [elo[t] for t in team_ids if t in elo]
    mean = sum(present) / len(present) if present else 1500.0
    return {tid: elo.get(tid, mean) for tid in team_ids}


def _strength_from_match_probs(
    probs: dict[str, dict],
    group_fixtures: list[dict],
    team_ids: list[str],
) -> dict[str, float]:
    """Strength = sum of win-prob across that team's group fixtures. A team
    plays 3 group fixtures in a 4-team round-robin → strength ∈ [0, 3]."""
    strength: dict[str, float] = {tid: 0.0 for tid in team_ids}
    for fx in group_fixtures:
        p = probs.get(fx["id"])
        if not p:
            continue
        h, a = fx["home_team_id"], fx["away_team_id"]
        if h in strength:
            strength[h] += p.get("home", 0.0)
        if a in strength:
            strength[a] += p.get("away", 0.0)
    return strength


def _strategy_strength(
    strategy: str,
    team_ids: list[str],
    group_fixtures: list[dict],
    elo: dict[str, float],
    af_probs: dict[str, dict],
    nt_probs: dict[str, dict],
    market_probs: dict[str, dict],
) -> dict[str, float]:
    """Per-team scalar 'strength' for the given strategy. All 5 strategies
    produce a strength score over the same team set; bracket + group-standings
    selection then sorts by strength desc and breaks ties by team_id (stable,
    deterministic, reproducible)."""
    if strategy == "OddsIntel Elite AI":
        # AF preds if available, fall back to national_team_v1, then ELO.
        combined = dict(nt_probs)
        for mid, p in af_probs.items():
            combined[mid] = p  # AF overrides — Elite stack prefers AF first
        s = _strength_from_match_probs(combined, group_fixtures, team_ids)
        if not any(v > 0 for v in s.values()):
            return _strength_from_elo(elo, team_ids)
        # Blend in ELO at 10% weight so teams with no probs aren't tied at 0.
        e = _strength_from_elo(elo, team_ids)
        e_mean = sum(e.values()) / max(1, len(e))
        return {tid: s[tid] + 0.001 * (e.get(tid, e_mean) - e_mean) for tid in team_ids}

    if strategy == "OddsIntel Pro AI":
        # Calibrated subset = national_team_v1 only. No fallback to ELO inside
        # the strategy itself — but if NT preds are missing for some fixtures
        # the strength is just lower there; ties broken by team_id below.
        s = _strength_from_match_probs(nt_probs, group_fixtures, team_ids)
        return s

    if strategy == "OddsIntel Free AI":
        # Basic ELO — exactly what a free user would see in the basic model.
        return _strength_from_elo(elo, team_ids)

    if strategy == "Market Implied":
        s = _strength_from_match_probs(market_probs, group_fixtures, team_ids)
        if any(v > 0 for v in s.values()):
            return s
        # Pre-launch fallback when bookies haven't priced WC yet.
        return _strength_from_elo(elo, team_ids)

    if strategy == "Chalk":
        # Naive baseline. Pure ELO ranking.
        return _strength_from_elo(elo, team_ids)

    # Anonymous variant — pattern: "Player NNN". Strength = rotated
    # baseline strategy + deterministic per-team gaussian noise. The seed
    # is the variant index (1..40); both baseline rotation and the noise
    # RNG seed from it, so re-running produces the SAME variant brackets.
    if strategy.startswith("Player ") and strategy[7:].isdigit():
        import random as _r
        seed = int(strategy[7:])
        baseline_name = ANONYMOUS_BASELINES[(seed - 1) % len(ANONYMOUS_BASELINES)]
        base = _strategy_strength(
            baseline_name, team_ids, group_fixtures, elo, af_probs, nt_probs, market_probs,
        )
        rng = _r.Random(seed * 9973 + 1)
        values = list(base.values())
        if values:
            spread = max(values) - min(values) or 1.0
        else:
            spread = 1.0
        # 25% of spread as noise standard deviation — large enough that
        # variants don't collapse to the same picks, small enough that
        # the picks still look plausible (no "Player 023 picks San Marino
        # to win Group A").
        sigma = 0.25 * spread
        return {
            tid: base.get(tid, sum(base.values()) / max(1, len(base)))
                 + rng.gauss(0, sigma)
            for tid in team_ids
        }

    raise ValueError(f"unknown strategy: {strategy}")


# ── Per-strategy bracket + group-standings derivation ──────────────────────

def _group_standings_for_strategy(
    strength: dict[str, float],
    groups: list[tuple[str, list[str]]],
) -> list[dict]:
    """Return [{group_letter, position, picked_team_id}] — 48 rows total.
    Position 1 = predicted 1st place, …, Position 4 = predicted 4th place."""
    rows: list[dict] = []
    for label, team_ids in groups:
        # Sort by strength DESC; tie-break by team_id ASC (deterministic).
        ranked = sorted(team_ids,
                        key=lambda t: (-strength.get(t, 0.0), t))
        for pos, tid in enumerate(ranked[:4], start=1):
            rows.append({
                "group_letter": label,
                "position": pos,
                "picked_team_id": tid,
            })
    return rows


def _bracket_for_strategy(
    strength: dict[str, float],
    groups: list[tuple[str, list[str]]],
) -> list[dict]:
    """Return [{round, position, picked_team_id}] covering R32 (16 slots),
    R16 (8), QF (4), SF (2), Final (1), Champion (1). Slots are positional
    — the scorer treats R32..SF as set membership so slot ordering doesn't
    affect score, but we still write a stable order for diffability.

    Construction (32 → 16 → 8 → 4 → 2 → 1):
      • R32 field: each group's top-two predicted finishers + the 8 strongest
        predicted thirds (FIFA format). All 32 placed; ordered by strength.
      • R16: top 16 of R32 by strength.
      • QF, SF, F: same shrinking-by-half rule.
      • Champion: top 1.
    """
    # ── R32 field ──────────────────────────────────────────────────────────
    top_two: list[str] = []
    thirds: list[str] = []
    for _, team_ids in groups:
        ranked = sorted(team_ids,
                        key=lambda t: (-strength.get(t, 0.0), t))
        if len(ranked) >= 2:
            top_two.extend(ranked[:2])
        if len(ranked) >= 3:
            thirds.append(ranked[2])

    thirds_sorted = sorted(thirds,
                           key=lambda t: (-strength.get(t, 0.0), t))
    best_thirds = thirds_sorted[:8]

    r32_field = sorted(top_two + best_thirds,
                       key=lambda t: (-strength.get(t, 0.0), t))[:32]

    rows: list[dict] = []

    # R32: 16 slots — the scoring rule is set membership over a 32-team field,
    # so we emit the strongest 16 of those 32 (the "advanced past R32") as the
    # R32 picks. (Slot index is purely for UI ordering.)
    r32_pred = r32_field[:16]
    for i, tid in enumerate(r32_pred):
        rows.append({"round": "r32", "position": i, "picked_team_id": tid})

    # R16: 8 slots — top 8 of R32-pred
    r16_pred = r32_pred[:8]
    for i, tid in enumerate(r16_pred):
        rows.append({"round": "r16", "position": i, "picked_team_id": tid})

    # QF: 4 slots
    qf_pred = r16_pred[:4]
    for i, tid in enumerate(qf_pred):
        rows.append({"round": "qf", "position": i, "picked_team_id": tid})

    # SF: 2 slots
    sf_pred = qf_pred[:2]
    for i, tid in enumerate(sf_pred):
        rows.append({"round": "sf", "position": i, "picked_team_id": tid})

    # Final: 1 slot — top 1 of SF
    final_pred = sf_pred[:1]
    for i, tid in enumerate(final_pred):
        rows.append({"round": "final", "position": i, "picked_team_id": tid})

    # Champion: 1 slot — same team as final winner
    if final_pred:
        rows.append({"round": "champion", "position": 0, "picked_team_id": final_pred[0]})

    return rows


# ── Writer ────────────────────────────────────────────────────────────────

def _write_ai_strategy(ai_label: str, group_rows: list[dict], bracket_rows: list[dict],
                       dry_run: bool) -> dict:
    """Idempotent write for one AI strategy: delete existing rows, insert
    fresh ones, upsert meta row. Returns counts."""
    from workers.api_clients.db import execute_write, bulk_upsert

    if dry_run:
        return {
            "ai_label": ai_label,
            "group_picks": len(group_rows),
            "bracket_picks": len(bracket_rows),
            "written": False,
        }

    # Wipe-and-replace for AI rows. Safe because the partial-unique indexes
    # make AI rows distinct from human rows.
    execute_write(
        "DELETE FROM wc_group_predictions WHERE ai_label = %s",
        (ai_label,),
    )
    execute_write(
        "DELETE FROM wc_bracket_picks WHERE ai_label = %s",
        (ai_label,),
    )

    # wc_group_predictions
    grp_tuples = [
        (ai_label, r["group_letter"], r["position"], r["picked_team_id"])
        for r in group_rows
    ]
    if grp_tuples:
        from workers.api_clients.db import get_conn
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO wc_group_predictions
                       (ai_label, group_letter, position, picked_team_id)
                       VALUES %s""",
                    grp_tuples,
                )
                conn.commit()

    # wc_bracket_picks
    pk_tuples = [
        (ai_label, r["round"], r["position"], r["picked_team_id"])
        for r in bracket_rows
    ]
    if pk_tuples:
        from workers.api_clients.db import get_conn
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO wc_bracket_picks
                       (ai_label, round, position, picked_team_id)
                       VALUES %s""",
                    pk_tuples,
                )
                conn.commit()

    # Meta — upsert by ai_label. Score columns are recomputed by the
    # scoring job; we write 0 here just to seed the row so the leaderboard
    # picks it up immediately.
    bulk_upsert(
        table="wc_bracket_meta",
        columns=["ai_label", "current_score", "current_rank",
                 "group_standings_score", "total_score"],
        rows=[(ai_label, 0, None, 0, 0)],
        conflict_columns=["ai_label"],
        update_columns=["current_score", "group_standings_score", "total_score"],
    )

    return {
        "ai_label": ai_label,
        "group_picks": len(group_rows),
        "bracket_picks": len(bracket_rows),
        "written": True,
    }


# ── Stage-gated per-round generation (WC-BRACKET-STAGE-GATED) ──────────────

def _load_slot_assignments_for_round(round_key: str) -> list[dict]:
    """Return [{position, match_id, home_team_id, away_team_id, locked_at, status}]
    for one bracket round. Only slots with seeded match_id are returned —
    unseeded slots have no matchup to predict against."""
    from workers.api_clients.db import execute_query
    try:
        return execute_query(
            """
            SELECT s.position,
                   s.match_id::text AS match_id,
                   s.locked_at,
                   m.home_team_id::text AS home_team_id,
                   m.away_team_id::text AS away_team_id,
                   m.status
            FROM wc_bracket_slot_assignments s
            JOIN matches m ON m.id = s.match_id
            WHERE s.round = %s
              AND s.match_id IS NOT NULL
            ORDER BY s.position ASC
            """,
            (round_key,),
        )
    except Exception:
        # Migration 171 not yet applied → empty list.
        return []


def _generate_for_round(
    round_key: str,
    strategies: list[str],
    dry_run: bool,
    force: bool,
) -> dict:
    """Per-round AI pick generation. Each strategy picks the winner of every
    seeded matchup in `round_key` by looking up its per-team strength
    (same scoring as the pre-tournament full-bracket path).

    Lock: refuses to overwrite if the round's `locked_at` is already in
    the past (unless --force). Idempotent before lock — DELETE + re-INSERT.
    """
    if round_key not in {"r32", "r16", "qf", "sf", "final"}:
        return {"ok": False, "error": f"unknown round_key: {round_key}"}

    slots = _load_slot_assignments_for_round(round_key)
    if not slots:
        return {"ok": True, "results": [], "round": round_key,
                "note": "no seeded slots yet"}

    # Per-round lock gate — `locked_at` is the same across all slots in a
    # round (first kickoff). Read it from any slot.
    now = datetime.now(timezone.utc)
    locked_at = slots[0].get("locked_at")
    if locked_at and hasattr(locked_at, "timestamp") and locked_at <= now and not force:
        return {
            "ok": False,
            "error": f"round {round_key} is locked (locked_at={locked_at.isoformat()})",
            "round": round_key,
        }

    # Pre-tournament data drives the strength score (same model as full
    # bracket mode). Once the WC kicks off the strength is frozen at the
    # pre-tournament value — which is fine: AI ghosts are deterministic
    # ghosts of the pre-tournament model, not adaptive opponents.
    fixtures = _load_wc_group_fixtures()
    if not fixtures:
        return {"ok": False, "error": "no WC group fixtures in DB"}

    groups = _build_groups(fixtures)
    team_ids = sorted({t for _, ts in groups for t in ts})

    # Include the teams in the round's matchups (R16+ knockout teams may
    # not be in the group-fixture set if AF labels are weird).
    round_team_ids: set[str] = set()
    for s in slots:
        round_team_ids.add(s["home_team_id"])
        round_team_ids.add(s["away_team_id"])
    team_ids_combined = sorted(set(team_ids) | round_team_ids)

    elo = _load_international_elo(set(team_ids_combined))
    match_ids = {fx["id"] for fx in fixtures}
    af_probs = _load_af_predictions(match_ids)
    nt_probs = _load_national_team_predictions(match_ids)
    market_probs = _load_market_implied(match_ids)

    results: list[dict] = []
    for strat in strategies:
        # Accept named strategies + anonymous variant labels.
        if strat not in AI_STRATEGIES and not is_anonymous_variant(strat):
            console.print(f"[yellow]skip unknown strategy: {strat}[/yellow]")
            continue
        strength = _strategy_strength(
            strat, team_ids_combined, fixtures, elo, af_probs, nt_probs, market_probs,
        )

        rows: list[dict] = []
        for s in slots:
            h, a = s["home_team_id"], s["away_team_id"]
            # Higher-strength team wins; tie-break by team_id (deterministic).
            picked = h if (strength.get(h, 0.0), -ord(h[0])) >= (
                strength.get(a, 0.0), -ord(a[0])
            ) else a
            rows.append({
                "round": round_key,
                "position": s["position"],
                "picked_team_id": picked,
            })

        # Also derive Champion when the round is the Final.
        if round_key == "final":
            final_pick = rows[0] if rows else None
            if final_pick:
                rows.append({
                    "round": "champion",
                    "position": 0,
                    "picked_team_id": final_pick["picked_team_id"],
                })

        if dry_run:
            results.append({"ai_label": strat, "round": round_key,
                            "n_picks": len(rows), "written": False})
            continue

        # Idempotent write: delete this strategy's existing picks for this
        # round (+ champion if round=final), then insert fresh.
        from workers.api_clients.db import execute_write, get_conn
        import psycopg2.extras
        execute_write(
            "DELETE FROM wc_bracket_picks WHERE ai_label = %s AND round = %s",
            (strat, round_key),
        )
        if round_key == "final":
            execute_write(
                "DELETE FROM wc_bracket_picks WHERE ai_label = %s AND round = 'champion'",
                (strat,),
            )
        if rows:
            tuples = [(strat, r["round"], r["position"], r["picked_team_id"]) for r in rows]
            with get_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """INSERT INTO wc_bracket_picks
                           (ai_label, round, position, picked_team_id)
                           VALUES %s""",
                        tuples,
                    )
                    conn.commit()

        # Make sure a meta row exists for the leaderboard.
        execute_write(
            """INSERT INTO wc_bracket_meta (ai_label, current_score, current_rank,
                                            group_standings_score, total_score)
               VALUES (%s, 0, NULL, 0, 0)
               ON CONFLICT (ai_label) DO NOTHING""",
            (strat,),
        )

        results.append({"ai_label": strat, "round": round_key,
                        "n_picks": len(rows), "written": True})
        console.print(f"  [green]{strat}[/green] · {round_key}: {len(rows)} picks")

    return {"ok": True, "results": results, "round": round_key, "dry_run": dry_run}


# ── Top-level orchestration ───────────────────────────────────────────────

def generate_all(
    strategies: Optional[list[str]] = None,
    dry_run: bool = False,
    force: bool = False,
    round_key: Optional[str] = None,
) -> dict:
    """Build + write picks for the given strategies (default: all 5).

    When `round_key` is set, generates POSITIONAL picks for ONLY that
    round (WC-BRACKET-STAGE-GATED). Picks are looked up against
    wc_bracket_slot_assignments — for each (round, position) with a seeded
    `match_id`, the strategy picks the higher-strength team between that
    match's home/away. Existing AI rows for `(round_key, *)` are
    DELETE'd + re-inserted (idempotent before that round's lock).

    When `round_key` is None, falls back to the pre-tournament full-bracket
    generation (group standings + greedy R32→Champion ranking from the
    pre-group-stage strength score)."""
    # Default: the 5 named strategies + 40 anonymous variants ("Player 001"
    # ... "Player 040"). Explicit --strategy flag(s) override this default.
    strategies = strategies or (list(AI_STRATEGIES) + _anonymous_variant_labels())

    # Lock gate — refuse to overwrite once the WC has started, EXCEPT when
    # we're targeting a specific (still-unlocked) round. Per-round mode is
    # the whole point of the stage-gated rewrite, so we must allow it
    # post-kickoff for rounds whose `locked_at` is still in the future.
    now = datetime.now(timezone.utc)
    if round_key is None and now >= WC_FIRST_KICKOFF and not force:
        return {
            "ok": False,
            "error": "WC has started — refusing to overwrite AI brackets. Use --force to override.",
            "now": now.isoformat(),
            "lock": WC_FIRST_KICKOFF.isoformat(),
        }

    # ── Per-round mode (stage-gated) ─────────────────────────────────────
    if round_key is not None:
        return _generate_for_round(round_key, strategies, dry_run, force)

    # Load shared data once.
    fixtures = _load_wc_group_fixtures()
    if not fixtures:
        return {"ok": False, "error": "no WC group fixtures in DB"}

    groups = _build_groups(fixtures)
    if len(groups) < 1:
        return {"ok": False, "error": f"only {len(groups)} groups derivable"}

    team_ids = sorted({t for _, ts in groups for t in ts})
    elo = _load_international_elo(set(team_ids))
    match_ids = {fx["id"] for fx in fixtures}
    af_probs = _load_af_predictions(match_ids)
    nt_probs = _load_national_team_predictions(match_ids)
    market_probs = _load_market_implied(match_ids)

    console.print(
        f"[cyan]Loaded {len(fixtures)} fixtures, {len(groups)} groups, "
        f"{len(team_ids)} teams; ELO={len(elo)}, AF={len(af_probs)}, "
        f"NT={len(nt_probs)}, market={len(market_probs)}[/cyan]"
    )

    out: list[dict] = []
    for strat in strategies:
        # Accept named strategies + anonymous variant labels ("Player NNN").
        if strat not in AI_STRATEGIES and not is_anonymous_variant(strat):
            console.print(f"[yellow]skip unknown strategy: {strat}[/yellow]")
            continue
        strength = _strategy_strength(
            strat, team_ids, fixtures, elo, af_probs, nt_probs, market_probs,
        )
        group_rows = _group_standings_for_strategy(strength, groups)
        bracket_rows = _bracket_for_strategy(strength, groups)
        res = _write_ai_strategy(strat, group_rows, bracket_rows, dry_run)
        console.print(
            f"  [green]{strat}[/green]: "
            f"groups={res['group_picks']} bracket={res['bracket_picks']} "
            f"written={res['written']}"
        )
        out.append(res)

    return {"ok": True, "results": out, "dry_run": dry_run}


def main():
    parser = argparse.ArgumentParser(description="Generate WC AI ghost brackets.")
    parser.add_argument("--strategy", action="append",
                        help=("Specific strategy to (re)generate. Repeatable. "
                              "Default: all 5."))
    parser.add_argument("--round",
                        choices=["r32", "r16", "qf", "sf", "final"],
                        help=("Stage-gated mode: generate picks ONLY for the "
                              "named round, using seeded wc_bracket_slot_assignments. "
                              "Re-runs are idempotent until the round locks."))
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to DB.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite even after WC lock has fired.")
    args = parser.parse_args()

    result = generate_all(
        strategies=args.strategy,
        dry_run=args.dry_run,
        force=args.force,
        round_key=args.round,
    )
    console.print(result)
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
