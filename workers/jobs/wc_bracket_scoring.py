"""
OddsIntel — World Cup Bracket Scoring (WC-BRACKET-SCORING)

Recomputes `wc_bracket_meta.current_score` + `current_rank` for every user who
has placed picks in `wc_bracket_picks`. Idempotent — designed to be called on
a 30-min cron during the WC window.

Scoring rules (also reflected in odds-intel-web/src/lib/wc-bracket-types.ts):

    R32 (Round of 32)   1 pt × 16 slots  = 16
    R16 (Round of 16)   2 pt ×  8 slots  = 16
    QF                  4 pt ×  4 slots  = 16
    SF                  8 pt ×  2 slots  = 16
    Final              16 pt ×  1 slot   = 16
    Champion           32 pt ×  1 slot   = 32
    Golden Boot                          = 10
    ─────────────────────────────────────────
    Max possible                          122

The frontend's scoring legend states "Max possible: 83 pts" — that string is
inconsistent with the per-round arithmetic also rendered on the same page.
This module uses 122 (the arithmetic). The legend copy is a UI-only display
string and does not affect scoring; fix-the-legend is filed as a frontend
follow-up so the auto-recompute job is not blocked by a typography decision.

Scoring semantics:

    For R32..SF, user picks are sets, not positions. The `position` column
    is a slot index in the UI (0..15 for R32, 0..7 for R16, ...), but the
    UI does not anchor any slot to a particular bracket path — users pick
    "16 teams I think advance to R32", "8 teams I think advance to R16",
    etc. So a pick is correct iff the picked team is in the set of teams
    that actually advanced to that round. Slot order does not matter.

    For Champion (1 slot), the pick is correct iff that team wins the final.

    For Golden Boot, points are awarded only if the operator has stamped
    `wc_bracket_meta.golden_boot_player` with the ACTUAL winner. Until then
    no user collects bonus points. We compare strings case-insensitively
    after trimming whitespace because user-typed input drifts.
    (Future: AF has a top-scorer endpoint we could automate — filed as
    follow-up `WC-GOLDEN-BOOT-AUTO`.)

Round derivation:

    The `matches` table has no `round` column. We bucket WC fixtures into
    rounds by kickoff date using the FIFA 2026 published schedule. Pre-
    tournament: zero finished matches → zero scoring rows → exit clean.

    Date windows (FIFA-published, all UTC):
      group   2026-06-11 .. 2026-06-27
      r32     2026-06-28 .. 2026-07-03
      r16     2026-07-04 .. 2026-07-07
      qf      2026-07-09 .. 2026-07-11
      sf      2026-07-14 .. 2026-07-15
      final   2026-07-19 .. 2026-07-19

    (Third-place play-off on 2026-07-18 is intentionally not mapped — it
    does not influence the bracket.)

Advancement derivation:

    R32:  for each of the 12 groups, top 2 by points + the 8 best 3rd-place
          teams (FIFA tie-break: points → goal diff → goals for → head-to-
          head). This is a 4-team-per-group group stage running 2026-06-11
          to 2026-06-27. Groups are not stored in our DB — we union-find
          them from the group-stage pairings.
    R16:  winners of R32 matches.
    QF:   winners of R16 matches.
    SF:   winners of QF matches.
    Final: winners of SF matches.
    Champion: winner of the final.

    "Winner" handles 90-min, extra time, and penalty shoot-outs by reading
    `matches.result` ('home'/'away'/'draw') and `matches.score_home/away`.
    Penalty winners are not separately stored — for knockouts ending in
    PEN, AF flips `result` to the eventual winner. If `result='draw'` on
    a knockout match (impossible in regulation), we skip and log.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Optional

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write, bulk_upsert

console = Console()
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

WC_LEAGUE_AF_ID = 1  # api_football_id of FIFA World Cup in our `leagues` table

ROUND_POINTS: dict[str, int] = {
    "r32": 1,
    "r16": 2,
    "qf": 4,
    "sf": 8,
    "final": 16,
    "champion": 32,
}

GOLDEN_BOOT_POINTS = 10

MAX_POSSIBLE_SCORE = (
    sum(pts * slots for pts, slots in [
        (ROUND_POINTS["r32"], 16),
        (ROUND_POINTS["r16"], 8),
        (ROUND_POINTS["qf"], 4),
        (ROUND_POINTS["sf"], 2),
        (ROUND_POINTS["final"], 1),
        (ROUND_POINTS["champion"], 1),
    ]) + GOLDEN_BOOT_POINTS
)  # = 122

# FIFA 2026 published schedule. Date windows are inclusive on both ends and
# refer to the LOCAL UTC date of kickoff. Group stage starts 2026-06-11; the
# final is 2026-07-19.
ROUND_DATE_WINDOWS: list[tuple[str, date, date]] = [
    ("group",  date(2026, 6, 11), date(2026, 6, 27)),
    ("r32",    date(2026, 6, 28), date(2026, 7,  3)),
    ("r16",    date(2026, 7,  4), date(2026, 7,  7)),
    ("qf",     date(2026, 7,  9), date(2026, 7, 11)),
    ("sf",     date(2026, 7, 14), date(2026, 7, 15)),
    ("final",  date(2026, 7, 19), date(2026, 7, 19)),
]


def _round_for_date(d: date) -> Optional[str]:
    """Return our round key for a WC match kickoff date, or None (third-
    place play-off / unknown)."""
    for key, start, end in ROUND_DATE_WINDOWS:
        if start <= d <= end:
            return key
    return None


# ── Group-stage derivation ─────────────────────────────────────────────────

def _load_wc_matches() -> list[dict]:
    """All WC matches (any status) joined to league.api_football_id."""
    return execute_query(
        """
        SELECT m.id, m.date::date AS match_date, m.status,
               m.home_team_id, m.away_team_id,
               m.score_home, m.score_away, m.result
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = %s
        ORDER BY m.date ASC
        """,
        (WC_LEAGUE_AF_ID,),
    )


def _build_groups(group_matches: list[dict]) -> list[set[str]]:
    """Union-find groups from group-stage pairings.

    Two teams are in the same group iff they have a group-stage fixture
    against each other. With a 4-team round-robin, every group's six
    fixtures form a complete K4 — connected component finds the group
    cleanly. Pre-tournament (zero group matches) this returns []."""
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

    for m in group_matches:
        h, a = m["home_team_id"], m["away_team_id"]
        parent.setdefault(h, h)
        parent.setdefault(a, a)
        union(h, a)

    groups: dict[str, set[str]] = defaultdict(set)
    for team in parent:
        groups[find(team)].add(team)
    return [g for g in groups.values() if len(g) >= 2]


def _standings_for_group(group_teams: set[str], group_matches: list[dict]) -> list[dict]:
    """Compute FIFA group-stage standings (points/GD/GF) over finished matches.

    Returns list of {team_id, played, points, gd, gf} sorted by FIFA
    tie-break: points DESC, GD DESC, GF DESC. Head-to-head is the next
    tie-breaker but is not implemented — close ties are extremely rare
    at the 3rd-place ranking decision and the operator can override via
    a future `wc_advancers_override` table if needed."""
    stats: dict[str, dict] = {
        t: {"team_id": t, "played": 0, "points": 0, "gd": 0, "gf": 0}
        for t in group_teams
    }
    for m in group_matches:
        if m["status"] != "finished":
            continue
        h, a = m["home_team_id"], m["away_team_id"]
        if h not in stats or a not in stats:
            continue
        sh = m["score_home"] or 0
        sa = m["score_away"] or 0
        stats[h]["played"] += 1
        stats[a]["played"] += 1
        stats[h]["gf"] += sh
        stats[a]["gf"] += sa
        stats[h]["gd"] += sh - sa
        stats[a]["gd"] += sa - sh
        if sh > sa:
            stats[h]["points"] += 3
        elif sa > sh:
            stats[a]["points"] += 3
        else:
            stats[h]["points"] += 1
            stats[a]["points"] += 1

    return sorted(
        stats.values(),
        key=lambda s: (s["points"], s["gd"], s["gf"]),
        reverse=True,
    )


# ── Advancer derivation (top level) ────────────────────────────────────────

def _knockout_winner(match: dict) -> Optional[str]:
    """Return the winning team_id for a finished knockout match, or None."""
    if match["status"] != "finished":
        return None
    if match["result"] == "home":
        return match["home_team_id"]
    if match["result"] == "away":
        return match["away_team_id"]
    # 'draw' on a knockout = data error (penalty winner should be set).
    log.warning("Knockout match %s settled as draw — skipping advancer", match["id"])
    return None


def build_advancers() -> dict[str, set[str]]:
    """Return {round_key: set(team_ids_that_advanced)} for each round whose
    matches have been settled. Set semantics — see module docstring.

    Pre-tournament returns {} (no rounds derivable).
    """
    matches = _load_wc_matches()
    if not matches:
        return {}

    by_round: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        rk = _round_for_date(m["match_date"])
        if rk is None:
            continue
        by_round[rk].append(m)

    advancers: dict[str, set[str]] = {}

    # ── Group stage → R32 ────────────────────────────────────────────────
    group_matches = by_round.get("group", [])
    if group_matches:
        groups = _build_groups(group_matches)
        # Only score R32 once ALL group matches are finished — otherwise
        # the 3rd-place rankings are unstable and users would see
        # bouncing scores as late group results land.
        if groups and all(m["status"] == "finished" for m in group_matches):
            top_two: set[str] = set()
            thirds: list[dict] = []
            for grp in groups:
                standings = _standings_for_group(grp, group_matches)
                if len(standings) >= 2:
                    top_two.add(standings[0]["team_id"])
                    top_two.add(standings[1]["team_id"])
                if len(standings) >= 3:
                    thirds.append(standings[2])
            # Best 8 third-place teams complete the 32-team R32 field.
            thirds.sort(
                key=lambda s: (s["points"], s["gd"], s["gf"]),
                reverse=True,
            )
            best_thirds = {s["team_id"] for s in thirds[:8]}
            advancers["r32"] = top_two | best_thirds

    # ── Knockout rounds ──────────────────────────────────────────────────
    # Order matters: r16 advancers are winners of r32 matches, etc.
    knockout_chain = [
        ("r32", "r16"),    # winners of R32 → "advanced to R16"
        ("r16", "qf"),
        ("qf",  "sf"),
        ("sf",  "final"),
    ]
    for played_round, next_round_key in knockout_chain:
        round_matches = by_round.get(played_round, [])
        winners = {w for w in (_knockout_winner(m) for m in round_matches) if w}
        if winners:
            advancers[next_round_key] = winners

    # ── Champion = winner of the Final ───────────────────────────────────
    final_matches = by_round.get("final", [])
    final_winners = {w for w in (_knockout_winner(m) for m in final_matches) if w}
    if final_winners:
        advancers["champion"] = final_winners

    return advancers


# ── Per-user scoring ───────────────────────────────────────────────────────

def _load_all_picks() -> dict[str, list[dict]]:
    """Return {user_id: [{round, position, picked_team_id}]} for every user."""
    rows = execute_query(
        """SELECT user_id::text AS user_id, round, position, picked_team_id::text AS picked_team_id
           FROM wc_bracket_picks""",
        (),
    )
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["user_id"]].append(r)
    return out


def _load_golden_boot_actual() -> Optional[str]:
    """Operator stamps the actual Golden Boot winner in a single
    `app_settings` row (key='wc_golden_boot_actual'). Until that row exists,
    no user collects the bonus. Falls back to None on any error so a missing
    table never blocks scoring of the bracket itself."""
    try:
        rows = execute_query(
            """SELECT value FROM app_settings WHERE key = 'wc_golden_boot_actual'""",
            (),
        )
        if not rows:
            return None
        v = rows[0].get("value")
        return v.strip() if isinstance(v, str) and v.strip() else None
    except Exception:
        # app_settings table may not exist — graceful no-op.
        return None


def compute_user_score(
    user_picks: list[dict],
    advancers: dict[str, set[str]],
    golden_boot_user: Optional[str] = None,
    golden_boot_actual: Optional[str] = None,
) -> dict:
    """
    Compute one user's bracket score.

    Args:
        user_picks: list of {round, position, picked_team_id} rows.
        advancers: {round_key: set(team_ids_actually_advanced)} from
                   `build_advancers()`.
        golden_boot_user:  user's typed Golden Boot pick (free text).
        golden_boot_actual: operator-stamped real Golden Boot winner.

    Returns:
        {
          "score":         total integer points,
          "by_round":      {round_key: pts_from_that_round},
          "golden_boot_hit": bool,
        }
    """
    by_round: dict[str, int] = {k: 0 for k in ROUND_POINTS}
    for p in user_picks:
        rk = p["round"]
        if rk not in ROUND_POINTS:
            continue
        winners = advancers.get(rk)
        if not winners:
            continue
        if p["picked_team_id"] in winners:
            by_round[rk] += ROUND_POINTS[rk]

    golden_hit = False
    if golden_boot_user and golden_boot_actual:
        if golden_boot_user.strip().lower() == golden_boot_actual.strip().lower():
            golden_hit = True

    total = sum(by_round.values()) + (GOLDEN_BOOT_POINTS if golden_hit else 0)
    return {"score": total, "by_round": by_round, "golden_boot_hit": golden_hit}


# ── Bulk recompute ─────────────────────────────────────────────────────────

def recompute_all_brackets() -> dict:
    """Recompute scores + ranks for every user with bracket picks or meta rows.

    Steps:
        1. Build the advancers map from finished WC matches.
        2. For each user with picks, compute score.
        3. Bulk upsert wc_bracket_meta (current_score, current_rank,
           updated_at) — golden_boot_player and locked_at are user-owned
           and NOT overwritten.
        4. Return stats.

    Pre-tournament safety: when zero WC matches are finished, advancers={}
    so every user scores 0 — we still upsert (rank=1 for the tying group)
    so the UI shows "Score: 0 · Rank #1" rather than a stale value.
    """
    advancers = build_advancers()
    picks_by_user = _load_all_picks()

    # Also include users who locked their bracket without saving picks (rare
    # but possible) — they should still appear on the leaderboard with 0.
    meta_rows = execute_query(
        """SELECT user_id::text AS user_id, golden_boot_player
           FROM wc_bracket_meta""",
        (),
    )
    golden_by_user = {r["user_id"]: r.get("golden_boot_player") for r in meta_rows}
    all_user_ids = set(picks_by_user) | set(golden_by_user)

    if not all_user_ids:
        console.print("[dim]recompute_all_brackets: no users with picks/meta — exit clean[/dim]")
        return {"users_scored": 0, "leader_score": 0, "leader_user": None,
                "rounds_settled": list(advancers)}

    golden_actual = _load_golden_boot_actual()

    # Compute scores
    scored: list[tuple[str, int]] = []
    for user_id in all_user_ids:
        result = compute_user_score(
            user_picks=picks_by_user.get(user_id, []),
            advancers=advancers,
            golden_boot_user=golden_by_user.get(user_id),
            golden_boot_actual=golden_actual,
        )
        scored.append((user_id, result["score"]))

    # Dense ranks (ties share a rank, next rank = previous + 1)
    scored.sort(key=lambda t: t[1], reverse=True)
    ranks: dict[str, int] = {}
    last_score: Optional[int] = None
    current_rank = 0
    for user_id, score in scored:
        if score != last_score:
            current_rank += 1
            last_score = score
        ranks[user_id] = current_rank

    # Bulk upsert. We carry updated_at in the row so the UI can show "last
    # scored at ...". golden_boot_player and locked_at are user-controlled
    # — DO NOT touch them.
    rows = [
        (user_id, score, ranks[user_id])
        for user_id, score in scored
    ]
    bulk_upsert(
        table="wc_bracket_meta",
        columns=["user_id", "current_score", "current_rank"],
        rows=rows,
        conflict_columns=["user_id"],
        update_columns=["current_score", "current_rank"],
    )
    # Bump updated_at on touched rows in one pass — keep separate from the
    # upsert so we don't have to thread NOW() through the values list.
    execute_write(
        """UPDATE wc_bracket_meta
           SET updated_at = NOW()
           WHERE user_id = ANY(%s::uuid[])""",
        ([uid for uid, _ in rows],),
    )

    leader_user, leader_score = (scored[0] if scored else (None, 0))
    stats = {
        "users_scored": len(scored),
        "leader_score": leader_score,
        "leader_user": leader_user,
        "rounds_settled": sorted(advancers),
        "golden_boot_actual": golden_actual,
    }
    console.print(
        f"[green]recompute_all_brackets: scored {stats['users_scored']} users, "
        f"leader={leader_score} pts, rounds settled={stats['rounds_settled']}[/green]"
    )
    return stats


# ── CLI entry-point ────────────────────────────────────────────────────────

def main():
    """Manual run: `python -m workers.jobs.wc_bracket_scoring`."""
    stats = recompute_all_brackets()
    console.print(stats)


if __name__ == "__main__":
    main()
