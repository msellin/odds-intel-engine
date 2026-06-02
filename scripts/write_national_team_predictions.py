"""
OddsIntel — Write National-Team Predictions (WC-PHASE-3 prediction job)

For each upcoming international match (next 30 days), compute predictions
via the WC-PHASE-3 model and write them to the `predictions` table with
`source='national_team_v1'`.

Inputs:
  - team_elo_international (populated by compute_international_elo.py)
  - Recent form: last 20 internationals per team (computed inline from
    `matches` table, weighted by competition)

Outputs:
  - predictions rows for markets: 1x2_home, 1x2_draw, 1x2_away
  - (goals markets `over_2_5` + `btts_yes` written with low confidence —
    backtest showed the goals model isn't worth marketing, but the data
    is harmless to write and the frontend can decide what to surface)

Usage:
  python scripts/write_national_team_predictions.py
  python scripts/write_national_team_predictions.py --dry-run
  python scripts/write_national_team_predictions.py --days 7

Idempotent: `store_prediction` upserts on (match_id, market, source).
"""
import sys, os, argparse
from pathlib import Path
from collections import defaultdict, deque
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console
from workers.api_clients.db import execute_query
from workers.api_clients.supabase_client import bulk_store_predictions
from workers.model.national_team_predictor import (
    predict_match, TeamGoalStats, COMP_WEIGHT,
)
from scripts.compute_international_elo import COMP_CATEGORY, category_for_league

console = Console()

# Best params from /tmp/sweep_national_team.py validation against 141-match holdout
BEST_PARAMS = {
    "softening_factor": 1.3,   # softens overconfident favourites
    "draw_base": 0.30,         # slight draw inflation
    "avg_goals_per_team": 1.15,
    "elo_goal_factor": 0.0,    # ELO-aware goals didn't help in sweep
    "goals_smoothing": 0.3,    # shrinks O/U + BTTS toward 0.5 (humble)
}

FORM_WINDOW = 20


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=30,
                        help="Predict matches in the next N days (default 30)")
    args = parser.parse_args()

    console.print(f"[bold cyan]═══ Write national-team predictions ═══[/bold cyan]")
    console.print(f"  window: next {args.days} days; params: {BEST_PARAMS}\n")

    # Load upcoming international matches
    upcoming = execute_query("""
        SELECT m.id, m.date, m.api_football_id,
               m.home_team_id, m.away_team_id,
               l.api_football_id AS league_af_id, l.name AS league_name
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country = 'World'
          AND l.api_football_id = ANY(%s::int[])
          AND m.status = 'scheduled'
          AND m.date::date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL %s
        ORDER BY m.date ASC
    """, [list(COMP_CATEGORY.keys()), f"{args.days} days"])

    console.print(f"  {len(upcoming)} upcoming international matches to predict")

    if not upcoming:
        console.print("[yellow]Nothing to predict — exiting.[/yellow]")
        return

    # Load latest ELO per team (window: only teams in the upcoming match set)
    team_ids = list({m["home_team_id"] for m in upcoming} |
                    {m["away_team_id"] for m in upcoming})
    elo_rows = execute_query("""
        SELECT DISTINCT ON (team_id) team_id, elo_rating
        FROM team_elo_international
        WHERE team_id = ANY(%s::uuid[])
        ORDER BY team_id, match_date DESC
    """, [team_ids])
    elo: dict[str, float] = {r["team_id"]: float(r["elo_rating"]) for r in elo_rows}
    console.print(f"  loaded ELO for {len(elo)}/{len(team_ids)} relevant teams")

    # Load recent form per team (last FORM_WINDOW finished internationals,
    # weighted by competition).
    history = execute_query("""
        SELECT m.home_team_id, m.away_team_id, m.score_home, m.score_away,
               m.date, l.api_football_id AS league_af_id
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country = 'World'
          AND l.api_football_id = ANY(%s::int[])
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND (m.home_team_id = ANY(%s::uuid[]) OR m.away_team_id = ANY(%s::uuid[]))
        ORDER BY m.date DESC
        LIMIT 50000
    """, [list(COMP_CATEGORY.keys()), team_ids, team_ids])

    form: dict[str, deque] = defaultdict(lambda: deque(maxlen=FORM_WINDOW))
    # iterate oldest-first so deque keeps last N (most recent)
    for h in reversed(history):
        cat = category_for_league(h["league_af_id"])
        w = COMP_WEIGHT.get(cat, 0.5)
        sh, sa = h["score_home"], h["score_away"]
        hid, aid = h["home_team_id"], h["away_team_id"]
        if hid in team_ids:
            form[hid].append((float(sh), float(sa), w))
        if aid in team_ids:
            form[aid].append((float(sa), float(sh), w))

    def _stats(tid: str) -> TeamGoalStats:
        d = form.get(tid) or deque()
        if not d:
            return TeamGoalStats(0, 0, 0)
        w_total = sum(w for _, _, w in d)
        if w_total == 0:
            return TeamGoalStats(0, 0, 0)
        gf = sum(g * w for g, _, w in d) / w_total
        ga = sum(c * w for _, c, w in d) / w_total
        return TeamGoalStats(gf, ga, len(d))

    # Compute predictions
    pred_rows: list[dict] = []
    skipped_no_elo = 0
    for m in upcoming:
        h_elo = elo.get(m["home_team_id"])
        a_elo = elo.get(m["away_team_id"])
        if h_elo is None or a_elo is None:
            skipped_no_elo += 1
            continue
        cat = category_for_league(m["league_af_id"])
        pred = predict_match(
            home_elo=h_elo, away_elo=a_elo,
            home_stats=_stats(m["home_team_id"]),
            away_stats=_stats(m["away_team_id"]),
            comp_category=cat,
            **BEST_PARAMS,
        )

        match_id = m["id"]
        reasoning = (
            f"national_team_v1 · ELO {h_elo:.0f} vs {a_elo:.0f} · "
            f"comp={cat} · form_n={_stats(m['home_team_id']).n_matches}/"
            f"{_stats(m['away_team_id']).n_matches}"
        )

        # 1X2 — primary, ship-quality
        for mkt, prob in (
            ("1x2_home", pred["1x2_home"]),
            ("1x2_draw", pred["1x2_draw"]),
            ("1x2_away", pred["1x2_away"]),
        ):
            pred_rows.append({
                "match_id": match_id, "market": mkt, "source": "national_team_v1",
                "model_prob": prob, "confidence": 0.6,
                "reasoning": reasoning,
                "model_version": "national_team_v1",
            })

        # O/U + BTTS — written but with low confidence (backtest weak)
        for mkt, prob in (
            ("over_2_5",  pred["over_2_5"]),
            ("under_2_5", pred["under_2_5"]),
            ("btts_yes",  pred["btts_yes"]),
            ("btts_no",   pred["btts_no"]),
        ):
            pred_rows.append({
                "match_id": match_id, "market": mkt, "source": "national_team_v1",
                "model_prob": prob, "confidence": 0.3,
                "reasoning": reasoning,
                "model_version": "national_team_v1",
            })

    console.print(f"  prepared {len(pred_rows)} prediction rows ({len(upcoming) - skipped_no_elo} fixtures, {skipped_no_elo} skipped for missing ELO)")

    if args.dry_run:
        console.print("[yellow]Dry run — not writing.[/yellow]")
        # Print a sample
        for r in pred_rows[:7]:
            console.print(f"  sample: {r['market']:<10} p={r['model_prob']:.3f}  match={r['match_id'][:8]}")
        return

    n = bulk_store_predictions(pred_rows)
    console.print(f"[green]✓ wrote {n} prediction rows to `predictions`[/green]")
    console.print(f"  (source='national_team_v1', model_version='national_team_v1')")


if __name__ == "__main__":
    main()
