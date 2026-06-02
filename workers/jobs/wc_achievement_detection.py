"""
OddsIntel — World Cup Achievement Detection (WC-ACHIEVEMENTS)

Scans current state every 15 minutes during the WC window and awards
badges to `wc_user_achievements`. Idempotent via UNIQUE (user_id, slug).

PARALLEL to scoring — never mutates wc_bracket_meta totals. Adds rows only
to wc_user_achievements. AI ghosts are never awarded achievements (they're
on the leaderboard for benchmarking; the badges are a user-engagement
loop).

Achievement catalog (15 slugs)
──────────────────────────────
  first_to_lock        Submitted bracket in the earliest 10% of lock-in times.
  early_bird           Submitted >24h before WC kickoff.
  last_minute          Submitted within 1h of WC kickoff.
  groups_perfect_one   Got at least one entire group's 1-4 standings correct.
  groups_perfect_three Got 3+ groups perfect.
  groups_all_perfect   Got all 12 groups perfect.
  r32_beat_ai          Outscored OddsIntel Elite AI's R32 bracket score.
  final_called         Picked both finalists correctly.
  champion_correct     Picked the actual champion.
  called_the_upset     Picked at least one lower-ELO team that advanced.
  vs_you_streak_5      5 consecutive correct per-match wc_user_picks.
  vs_you_streak_10     10 consecutive correct picks.
  vs_you_perfect_day   All matches on a single day picked correctly (>=2).
  viewed_all_groups    Visited all 12 group views (engagement, FE-emitted).
  golden_boot_correct  Golden Boot pick matched the actual top scorer.

Run cadence: every 15 min on `:00/:15/:30/:45` during the WC window
(2026-06-11 → 2026-07-19), gated in `workers/scheduler.py`. Cheap — a few
table scans per run.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from workers.api_clients.db import execute_query
from workers.jobs.wc_bracket_scoring import (
    ROUND_POINTS,
    build_advancers,
    build_actual_group_standings,
    compute_group_standings_score,
    _load_slot_assignments,
    _load_golden_boot_actual,
    _load_all_picks,
    _load_all_group_picks,
    WC_LEAGUE_AF_ID,
)

console = Console()
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# WC first kickoff (matches frontend `WC_FIRST_KICKOFF_ISO` + scripts/generate_ai_brackets.py).
WC_FIRST_KICKOFF = datetime(2026, 6, 11, 19, 0, 0, tzinfo=timezone.utc)

# Named AI ghost used as the "beat the AI" benchmark.
ELITE_AI_LABEL = "OddsIntel Elite AI"

# Achievement slugs — kept in sync with frontend `wc-achievement-badge.tsx`.
ACHIEVEMENT_SLUGS: tuple[str, ...] = (
    "first_to_lock",
    "early_bird",
    "last_minute",
    "groups_perfect_one",
    "groups_perfect_three",
    "groups_all_perfect",
    "r32_beat_ai",
    "final_called",
    "champion_correct",
    "called_the_upset",
    "vs_you_streak_5",
    "vs_you_streak_10",
    "vs_you_perfect_day",
    "viewed_all_groups",
    "golden_boot_correct",
)


# ── Award helper ───────────────────────────────────────────────────────────

def _award(user_id: str, slug: str, detail: Optional[dict] = None) -> bool:
    """Insert an achievement row idempotently. Returns True if newly awarded,
    False if the user already had it. Errors are logged + swallowed so a
    single bad row never breaks the detection loop."""
    if slug not in ACHIEVEMENT_SLUGS:
        log.warning("award(): unknown slug %s", slug)
        return False
    try:
        rows = execute_query(
            """INSERT INTO wc_user_achievements (user_id, slug, detail)
                 VALUES (%s::uuid, %s, %s::jsonb)
               ON CONFLICT (user_id, slug) DO NOTHING
               RETURNING id""",
            (user_id, slug, json.dumps(detail) if detail else None),
        )
        return bool(rows)
    except Exception as e:
        log.warning("award(%s,%s) failed: %s", user_id, slug, e)
        return False


def _earned_slugs_for_user(user_id: str) -> set[str]:
    """Return the set of slugs the user already has. Cheap — single indexed lookup."""
    try:
        rows = execute_query(
            "SELECT slug FROM wc_user_achievements WHERE user_id = %s::uuid",
            (user_id,),
        )
        return {r["slug"] for r in rows}
    except Exception:
        return set()


# ── Context loaders (shared across all detectors) ─────────────────────────

class _DetectionContext:
    """Pre-computed read-only state shared across users in one detection
    run. Keeps the per-user loop O(1) DB calls."""

    def __init__(self):
        self.advancers = build_advancers()
        self.actual_groups = build_actual_group_standings()
        self.slot_assignments = _load_slot_assignments()
        self.golden_boot_actual = _load_golden_boot_actual()

        # All user picks + group picks (humans only — AI ghosts excluded).
        self.picks_by_user = _load_all_picks()
        self.group_picks_by_user = _load_all_group_picks()

        # Lock-in times for percentile ranking (first_to_lock).
        self.locked_at_by_user = _load_locked_at_map()

        # Golden Boot text per user.
        self.golden_boot_by_user = _load_golden_boot_picks()

        # Per-match user picks (vs_you streaks + perfect day).
        self.user_picks_with_actuals = _load_user_picks_with_actuals()

        # Group views (engagement — opt-in client emits).
        self.group_views = _load_group_views()

        # ELO map for upset detection.
        self.elo_by_team = _load_latest_elo()

        # Elite AI's R32 score, for r32_beat_ai.
        self.elite_ai_r32_score = _compute_ai_r32_score(self.advancers,
                                                       self.slot_assignments)


def _load_locked_at_map() -> dict[str, datetime]:
    """{user_id: locked_at} for users who've locked their bracket."""
    rows = execute_query(
        """SELECT user_id::text AS user_id, locked_at
           FROM wc_bracket_meta
           WHERE user_id IS NOT NULL AND locked_at IS NOT NULL""",
        (),
    )
    out: dict[str, datetime] = {}
    for r in rows:
        v = r.get("locked_at")
        if isinstance(v, datetime):
            out[r["user_id"]] = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return out


def _load_golden_boot_picks() -> dict[str, str]:
    """{user_id: golden_boot_player_text} from wc_bracket_meta."""
    rows = execute_query(
        """SELECT user_id::text AS user_id, golden_boot_player
           FROM wc_bracket_meta
           WHERE user_id IS NOT NULL AND golden_boot_player IS NOT NULL""",
        (),
    )
    return {r["user_id"]: r["golden_boot_player"] for r in rows if r.get("golden_boot_player")}


def _load_user_picks_with_actuals() -> dict[str, list[dict]]:
    """{user_id: [{match_id, pick, actual, match_date}]} sorted by match_date
    ASC. `actual` is the 1/X/2 result derived from matches.result, or None
    if unfinished. Used by streak + perfect-day detection."""
    rows = execute_query(
        """
        SELECT up.user_id::text AS user_id,
               up.match_id::text AS match_id,
               up.pick,
               m.date::date AS match_date,
               m.status,
               m.result
        FROM wc_user_picks up
        JOIN matches m ON m.id = up.match_id
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = %s
        ORDER BY up.user_id, m.date ASC
        """,
        (WC_LEAGUE_AF_ID,),
    )

    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        actual: Optional[str] = None
        if r["status"] == "finished":
            res = r["result"]
            if res == "home":
                actual = "1"
            elif res == "away":
                actual = "2"
            elif res == "draw":
                actual = "X"
        by_user[r["user_id"]].append({
            "match_id": r["match_id"],
            "pick": r["pick"],
            "actual": actual,
            "match_date": r["match_date"],
        })
    return by_user


def _load_group_views() -> dict[str, set[str]]:
    """{user_id: {group_letter}} from wc_group_views — if the table exists.
    Engagement-tracking is opt-in; if the table is missing we just skip
    `viewed_all_groups` quietly."""
    try:
        rows = execute_query(
            """SELECT user_id::text AS user_id, group_letter
               FROM wc_group_views
               WHERE user_id IS NOT NULL""",
            (),
        )
    except Exception:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        out[r["user_id"]].add(r["group_letter"])
    return out


def _load_latest_elo() -> dict[str, float]:
    """{team_id: latest_elo_rating} from team_elo_international."""
    try:
        rows = execute_query(
            """
            SELECT DISTINCT ON (team_id)
                   team_id::text AS team_id,
                   elo_rating
            FROM team_elo_international
            ORDER BY team_id, match_date DESC
            """,
            (),
        )
    except Exception:
        return {}
    return {r["team_id"]: float(r["elo_rating"]) for r in rows if r.get("elo_rating") is not None}


def _compute_ai_r32_score(advancers: dict, slot_assignments: dict) -> Optional[int]:
    """Return Elite AI's R32 score (positional preferred) or None if no
    Elite AI rows / no R32 data yet."""
    rows = execute_query(
        """SELECT round, position, picked_team_id::text AS picked_team_id
           FROM wc_bracket_picks
           WHERE ai_label = %s AND round = 'r32'""",
        (ELITE_AI_LABEL,),
    )
    if not rows:
        return None

    # Positional path when slots are settled; else fall back to set-membership.
    score = 0
    use_positional = any(
        sa.get("match_id") and sa.get("status") == "finished"
        for sa in slot_assignments.values()
    )
    if use_positional:
        for p in rows:
            slot = slot_assignments.get(("r32", p["position"]))
            if not slot or slot.get("status") != "finished":
                continue
            res = slot.get("result")
            winner = (
                slot.get("home_team_id") if res == "home"
                else slot.get("away_team_id") if res == "away"
                else None
            )
            if winner and p["picked_team_id"] == winner:
                score += ROUND_POINTS["r32"]
    else:
        r32_winners = advancers.get("r32")
        if not r32_winners:
            return None
        for p in rows:
            if p["picked_team_id"] in r32_winners:
                score += ROUND_POINTS["r32"]
    return score


# ── Per-user detection ─────────────────────────────────────────────────────

def _user_r32_score(picks: list[dict], advancers: dict, slot_assignments: dict) -> int:
    """Compute a single user's R32 score with the same dual-path logic the
    AI uses, so r32_beat_ai is apples-to-apples."""
    score = 0
    use_positional = any(
        sa.get("match_id") and sa.get("status") == "finished"
        for sa in slot_assignments.values()
    )
    r32_picks = [p for p in picks if p["round"] == "r32"]
    if use_positional:
        for p in r32_picks:
            slot = slot_assignments.get(("r32", p["position"]))
            if not slot or slot.get("status") != "finished":
                continue
            res = slot.get("result")
            winner = (
                slot.get("home_team_id") if res == "home"
                else slot.get("away_team_id") if res == "away"
                else None
            )
            if winner and p["picked_team_id"] == winner:
                score += ROUND_POINTS["r32"]
    else:
        r32_winners = advancers.get("r32", set())
        for p in r32_picks:
            if p["picked_team_id"] in r32_winners:
                score += ROUND_POINTS["r32"]
    return score


def _detect_submission_timing(
    user_id: str,
    ctx: _DetectionContext,
    earned: set[str],
    lockin_threshold_ts: Optional[float],
) -> None:
    """early_bird / last_minute / first_to_lock."""
    locked_at = ctx.locked_at_by_user.get(user_id)
    if locked_at is None:
        return
    kickoff_ts = WC_FIRST_KICKOFF.timestamp()
    locked_ts = locked_at.timestamp()
    seconds_before_ko = kickoff_ts - locked_ts

    if "early_bird" not in earned and seconds_before_ko > 24 * 3600:
        _award(user_id, "early_bird", {"locked_at": locked_at.isoformat()})

    if "last_minute" not in earned and 0 <= seconds_before_ko <= 3600:
        _award(user_id, "last_minute", {"locked_at": locked_at.isoformat()})

    if (
        "first_to_lock" not in earned
        and lockin_threshold_ts is not None
        and locked_ts <= lockin_threshold_ts
    ):
        _award(user_id, "first_to_lock", {"locked_at": locked_at.isoformat()})


def _detect_groups(user_id: str, ctx: _DetectionContext, earned: set[str]) -> None:
    """groups_perfect_one / groups_perfect_three / groups_all_perfect."""
    if not ctx.actual_groups:
        return  # group stage not fully settled
    picks = ctx.group_picks_by_user.get(user_id, [])
    if not picks:
        return
    res = compute_group_standings_score(picks=picks, actuals=ctx.actual_groups)
    perfect = res["perfect_groups"]
    if perfect >= 1 and "groups_perfect_one" not in earned:
        _award(user_id, "groups_perfect_one", {"perfect_groups": perfect})
    if perfect >= 3 and "groups_perfect_three" not in earned:
        _award(user_id, "groups_perfect_three", {"perfect_groups": perfect})
    if perfect == 12 and "groups_all_perfect" not in earned:
        _award(user_id, "groups_all_perfect", {"perfect_groups": 12})


def _detect_bracket(user_id: str, ctx: _DetectionContext, earned: set[str]) -> None:
    """r32_beat_ai / final_called / champion_correct / called_the_upset."""
    picks = ctx.picks_by_user.get(user_id, [])
    if not picks:
        return

    # r32_beat_ai — needs both Elite AI score AND R32 to be settled.
    if (
        "r32_beat_ai" not in earned
        and ctx.elite_ai_r32_score is not None
        and ctx.advancers.get("r32")
    ):
        user_r32 = _user_r32_score(picks, ctx.advancers, ctx.slot_assignments)
        if user_r32 > ctx.elite_ai_r32_score:
            _award(user_id, "r32_beat_ai",
                   {"user_score": user_r32, "ai_score": ctx.elite_ai_r32_score})

    # champion_correct
    champion_winners = ctx.advancers.get("champion")
    if champion_winners and "champion_correct" not in earned:
        # Two storage variants: explicit "champion" row OR derive from (final, 0).
        champ_pick = next(
            (p for p in picks if p["round"] == "champion"),
            None,
        )
        if not champ_pick:
            champ_pick = next(
                (p for p in picks if p["round"] == "final" and p["position"] == 0),
                None,
            )
        if champ_pick and champ_pick["picked_team_id"] in champion_winners:
            _award(user_id, "champion_correct",
                   {"team_id": champ_pick["picked_team_id"]})

    # final_called — both finalists correctly picked (sf winners that
    # advanced to final). Detection requires SF results AND that the user
    # has both 'final' slot picks.
    final_advancers = ctx.advancers.get("final")
    if final_advancers and "final_called" not in earned:
        final_picks = [p["picked_team_id"] for p in picks if p["round"] == "final"]
        if final_picks:
            hits = sum(1 for t in final_picks if t in final_advancers)
            # final has only 1 slot but we accept 2-team variant brackets too.
            # "Both finalists correct" = picked both teams that actually
            # advanced to the final. Most schemas only have 1 final pick,
            # so we also check if (sf, position) picks cover both finalists.
            if hits >= min(2, len(final_advancers)):
                _award(user_id, "final_called",
                       {"finalists": list(final_advancers)})
            elif len(final_picks) < 2:
                # Fallback: examine SF picks. If both SF winners (= the two
                # finalists) appear among the user's SF picks, that counts.
                sf_picks = {p["picked_team_id"] for p in picks if p["round"] == "sf"}
                if sf_picks and final_advancers.issubset(sf_picks):
                    _award(user_id, "final_called",
                           {"finalists": list(final_advancers), "via": "sf"})

    # called_the_upset — picked a lower-ELO team that advanced.
    if "called_the_upset" not in earned and ctx.elo_by_team and ctx.slot_assignments:
        upset = _find_upset_for_user(picks, ctx)
        if upset:
            _award(user_id, "called_the_upset", upset)


def _find_upset_for_user(picks: list[dict], ctx: _DetectionContext) -> Optional[dict]:
    """Return {team_id, advanced_to} for the first upset pick we find,
    or None. An upset = picked team had lower ELO than its opponent AND
    actually won that matchup."""
    for p in picks:
        rk = p["round"]
        if rk == "champion":
            continue
        slot = ctx.slot_assignments.get((rk, p["position"]))
        if not slot or slot.get("status") != "finished":
            continue
        res = slot.get("result")
        winner = (
            slot.get("home_team_id") if res == "home"
            else slot.get("away_team_id") if res == "away"
            else None
        )
        if not winner or p["picked_team_id"] != winner:
            continue
        # Find the loser to compare ELO.
        home = slot.get("home_team_id")
        away = slot.get("away_team_id")
        loser = away if winner == home else home
        if not loser:
            continue
        winner_elo = ctx.elo_by_team.get(winner)
        loser_elo = ctx.elo_by_team.get(loser)
        if winner_elo is None or loser_elo is None:
            continue
        if winner_elo < loser_elo:
            return {
                "team_id": winner,
                "advanced_to": rk,
                "winner_elo": winner_elo,
                "loser_elo": loser_elo,
            }
    return None


def _detect_vs_you(user_id: str, ctx: _DetectionContext, earned: set[str]) -> None:
    """vs_you_streak_5 / vs_you_streak_10 / vs_you_perfect_day."""
    rows = ctx.user_picks_with_actuals.get(user_id, [])
    if not rows:
        return

    # Streaks — consecutive correct picks (settled matches only, in date order).
    best_streak = 0
    current_streak = 0
    streak_matches: list[str] = []
    best_streak_matches: list[str] = []
    for r in rows:
        if r["actual"] is None:
            # Unsettled — break the streak.
            current_streak = 0
            streak_matches = []
            continue
        if r["pick"] == r["actual"]:
            current_streak += 1
            streak_matches.append(r["match_id"])
            if current_streak > best_streak:
                best_streak = current_streak
                best_streak_matches = list(streak_matches)
        else:
            current_streak = 0
            streak_matches = []

    if best_streak >= 5 and "vs_you_streak_5" not in earned:
        _award(user_id, "vs_you_streak_5",
               {"matches": best_streak_matches[:5], "streak": best_streak})
    if best_streak >= 10 and "vs_you_streak_10" not in earned:
        _award(user_id, "vs_you_streak_10",
               {"matches": best_streak_matches[:10], "streak": best_streak})

    # Perfect day — at least 2 matches on the same calendar date, all correct.
    if "vs_you_perfect_day" not in earned:
        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if r["actual"] is None:
                continue
            by_day[str(r["match_date"])].append(r)
        for day, day_rows in by_day.items():
            if len(day_rows) >= 2 and all(r["pick"] == r["actual"] for r in day_rows):
                _award(user_id, "vs_you_perfect_day",
                       {"day": day, "matches": [r["match_id"] for r in day_rows]})
                break


def _detect_engagement(user_id: str, ctx: _DetectionContext, earned: set[str]) -> None:
    """viewed_all_groups — needs all 12 group letters (A..L) in wc_group_views."""
    if "viewed_all_groups" in earned:
        return
    seen = ctx.group_views.get(user_id, set())
    if len(seen) >= 12:
        _award(user_id, "viewed_all_groups", {"groups": sorted(seen)})


def _detect_golden_boot(user_id: str, ctx: _DetectionContext, earned: set[str]) -> None:
    """golden_boot_correct — operator-stamped actual matches user's pick."""
    if "golden_boot_correct" in earned:
        return
    actual = ctx.golden_boot_actual
    user_pick = ctx.golden_boot_by_user.get(user_id)
    if not actual or not user_pick:
        return
    if user_pick.strip().lower() == actual.strip().lower():
        _award(user_id, "golden_boot_correct", {"player": actual})


# ── Public entrypoints ─────────────────────────────────────────────────────

def detect_for_user(user_id: str, ctx: Optional[_DetectionContext] = None) -> dict:
    """Detect + award all achievements for a single user. Returns a dict of
    {"awarded": [slug, ...]} listing only newly-awarded slugs."""
    if ctx is None:
        ctx = _DetectionContext()

    earned_before = _earned_slugs_for_user(user_id)
    lockin_threshold_ts = _compute_lockin_threshold(ctx.locked_at_by_user)

    _detect_submission_timing(user_id, ctx, earned_before, lockin_threshold_ts)
    _detect_groups(user_id, ctx, earned_before)
    _detect_bracket(user_id, ctx, earned_before)
    _detect_vs_you(user_id, ctx, earned_before)
    _detect_engagement(user_id, ctx, earned_before)
    _detect_golden_boot(user_id, ctx, earned_before)

    earned_after = _earned_slugs_for_user(user_id)
    return {"awarded": sorted(earned_after - earned_before)}


def _compute_lockin_threshold(locked_at_by_user: dict[str, datetime]) -> Optional[float]:
    """Return the unix-timestamp cutoff at the 10th-percentile of lock-in
    times. Below this threshold = "first to lock". Returns None when there
    are fewer than 10 lock-ins (the badge is meaningless at that scale)."""
    if len(locked_at_by_user) < 10:
        return None
    ts = sorted(v.timestamp() for v in locked_at_by_user.values())
    idx = max(1, len(ts) // 10) - 1  # top 10% by earliest time
    return ts[idx]


def detect_for_all_users() -> dict:
    """Detect + award achievements for every user with any WC state. The
    scheduled entry point. Returns a stats summary for logging.

    Pre-tournament: ctx loads cleanly (empty sets), most detectors no-op,
    early_bird / first_to_lock may still award. No exceptions raised."""
    ctx = _DetectionContext()

    user_ids: set[str] = set()
    user_ids.update(ctx.picks_by_user.keys())
    user_ids.update(ctx.group_picks_by_user.keys())
    user_ids.update(ctx.locked_at_by_user.keys())
    user_ids.update(ctx.golden_boot_by_user.keys())
    user_ids.update(ctx.user_picks_with_actuals.keys())
    user_ids.update(ctx.group_views.keys())

    if not user_ids:
        console.print("[dim]wc_achievement_detection: no users — exit clean[/dim]")
        return {"users_scanned": 0, "awarded": 0}

    total_awarded = 0
    by_slug: dict[str, int] = defaultdict(int)
    for uid in user_ids:
        res = detect_for_user(uid, ctx=ctx)
        for slug in res["awarded"]:
            by_slug[slug] += 1
            total_awarded += 1

    console.print(
        f"[green]wc_achievement_detection: scanned {len(user_ids)} users, "
        f"awarded {total_awarded} new badges {dict(by_slug) if by_slug else ''}[/green]"
    )
    return {
        "users_scanned": len(user_ids),
        "awarded": total_awarded,
        "by_slug": dict(by_slug),
    }


# ── CLI entry-point ────────────────────────────────────────────────────────

def main():
    """Manual run: `python -m workers.jobs.wc_achievement_detection`."""
    stats = detect_for_all_users()
    console.print(stats)


if __name__ == "__main__":
    main()
