#!/usr/bin/env python3
"""
Refresh WC2026 picks for the next round.

Run before each FIFA Match Predictor round opens:
  - Group Stage Round 2  -> run between Jun 17-18
  - Group Stage Round 3  -> run between Jun 22-23
  - Round of 32          -> after group stage ends
  - Round of 16/QF/SF/F  -> after each prior round

What it does:
  1. Verifies MD1/prev-round results are settled in the DB
  2. Triggers ELO recompute from the latest results
  3. Re-runs national_team_v1 predictions for upcoming fixtures
  4. Prints a picks summary with:
     - Pick + scoreline per match (ordered by date)
     - EV ranking
     - Booster recommendation (highest EV with Risky-bonus potential)
     - Risky-bonus candidates (away wins likely <20% crowd)

Usage:
  python3 scripts/refresh_wc_round.py --round 2
  python3 scripts/refresh_wc_round.py --round 3
  python3 scripts/refresh_wc_round.py --round R16  --first-scorer
"""

import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor

WC_LEAGUE_ID = "108e7471-93af-42bb-81b6-841b9acfa985"

# FIFA Match Predictor scoring (group stage)
PTS_OUTCOME = 10
PTS_HOME_GOALS = 5
PTS_AWAY_GOALS = 5
PTS_GOAL_DIFF = 5
PTS_EXACT_BONUS = 5  # on top of getting both goal tallies right
PTS_RISKY = 10       # H/A win when <20% of users picked it
PTS_FIRST_TEAM = 5   # knockouts only
PTS_FIRST_SCORER = 10  # knockouts only

# Rough round windows (UTC dates). Used only to slice DB queries.
ROUND_WINDOWS = {
    "1":  ("2026-06-11", "2026-06-17"),
    "2":  ("2026-06-18", "2026-06-22"),
    "3":  ("2026-06-23", "2026-06-27"),
    "R32": ("2026-06-28", "2026-07-03"),
    "R16": ("2026-07-04", "2026-07-07"),
    "QF": ("2026-07-09", "2026-07-11"),
    "SF": ("2026-07-14", "2026-07-15"),
    "F":  ("2026-07-18", "2026-07-19"),
}

# Teams that crowd will heavily back (>20% likely on H/A win) -> NO Risky bonus.
# Calibrate as the tournament progresses. Big names + recent winners + hosts +
# any team with a globally-famous star (Haaland, Mbappe, etc.).
CROWD_FAVOURITES = {
    # European elite + recent finalists
    "Spain", "France", "Brazil", "Argentina", "Germany", "England", "Portugal",
    "Netherlands", "Italy", "Belgium", "Croatia", "Switzerland",
    # Star-driven
    "Norway",  # Haaland
    "Uruguay",  # known WC pedigree
    # Hosts
    "Mexico", "USA", "Canada",
    # Asian giants
    "Japan", "South Korea",
}


def db():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


def check_prev_round_settled(round_id: str) -> bool:
    prev = {"2": "1", "3": "2", "R32": "3", "R16": "R32", "QF": "R16", "SF": "QF", "F": "SF"}.get(round_id)
    if not prev:
        return True
    start, end = ROUND_WINDOWS[prev]
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status = 'FT') AS done
            FROM matches
            WHERE league_id = %s::uuid AND season = 2026
              AND date::date BETWEEN %s AND %s
        """, (WC_LEAGUE_ID, start, end))
        row = cur.fetchone()
        print(f"  Round {prev}: {row['done']}/{row['total']} matches settled")
        return row["total"] > 0 and row["done"] == row["total"]


def recompute_elo():
    print("Recomputing international ELO from latest results...")
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compute_international_elo.py")],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("ELO recompute failed:")
        print(res.stderr)
        sys.exit(1)
    print("  done")


def rewrite_predictions():
    print("Re-running national_team_v1 predictions for upcoming WC fixtures...")
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "write_national_team_predictions.py")],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("Prediction writer failed:")
        print(res.stderr)
        sys.exit(1)
    print(res.stdout.strip().split("\n")[-1] if res.stdout else "  done")


def fetch_round(round_id: str):
    start, end = ROUND_WINDOWS[round_id]
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH lp AS (
              SELECT DISTINCT ON (match_id, market)
                match_id, market, model_probability
              FROM predictions
              WHERE source IN ('national_team_v1', 'national_team_v1_blended')
                AND match_id IN (
                  SELECT id FROM matches
                  WHERE league_id = %s::uuid AND season = 2026
                    AND date::date BETWEEN %s AND %s
                )
              ORDER BY match_id, market, created_at DESC
            )
            SELECT
              m.id, m.date, ht.name AS home, at2.name AS away,
              MAX(CASE WHEN lp.market='1x2_home' THEN lp.model_probability END) hp,
              MAX(CASE WHEN lp.market='1x2_draw' THEN lp.model_probability END) dp,
              MAX(CASE WHEN lp.market='1x2_away' THEN lp.model_probability END) ap,
              MAX(CASE WHEN lp.market='over_2_5'  THEN lp.model_probability END) ov,
              MAX(CASE WHEN lp.market='btts_yes'  THEN lp.model_probability END) bt
            FROM matches m
            JOIN teams ht  ON ht.id  = m.home_team_id
            JOIN teams at2 ON at2.id = m.away_team_id
            JOIN lp ON lp.match_id = m.id
            WHERE m.league_id = %s::uuid AND m.season = 2026
              AND m.date::date BETWEEN %s AND %s
            GROUP BY m.id, m.date, ht.name, at2.name
            ORDER BY m.date
        """, (WC_LEAGUE_ID, start, end, WC_LEAGUE_ID, start, end))
        return cur.fetchall()


def predict_score(hp, dp, ap, ov, bt):
    """Same Poisson-ish scoreline picker as generate_wc_unified_report.py."""
    total = 3.2 if ov > 0.65 else (2.7 if ov > 0.52 else (2.3 if ov > 0.42 else 1.8))
    both = bt > 0.55
    if hp > 0.55:
        fs, fav_home = 0.62, True
    elif ap > 0.55:
        fs, fav_home = 0.62, False
    else:
        if dp > 0.28:
            g = round(total / 2)
            return f"{g}-{g}" if both else ("1-0" if hp >= ap else "0-1")
        fav_home = hp >= ap
        fs = 0.55
    fg = round(total * fs)
    ug = round(total * (1 - fs))
    if not both and ug > 0:
        ug = 0
    if fg == ug:
        fg += 1
    return f"{fg}-{ug}" if fav_home else f"{ug}-{fg}"


def pick(hp, dp, ap):
    if hp >= dp and hp >= ap:
        return "H", hp
    if ap >= dp and ap >= hp:
        return "A", ap
    return "D", dp


def is_risky_candidate(side: str, team_name: str) -> bool:
    """Crowd is unlikely to pick this side -> Risky bonus eligible."""
    if side == "D":
        return False  # draws don't qualify
    return team_name not in CROWD_FAVOURITES


def expected_points(side, prob, exact_score_prob, risky_eligible):
    """Rough EV ignoring partial-credit (goals only / GD only). Conservative."""
    base = PTS_OUTCOME * prob
    exact = (PTS_HOME_GOALS + PTS_AWAY_GOALS + PTS_GOAL_DIFF + PTS_EXACT_BONUS) * exact_score_prob
    risky = PTS_RISKY * prob if risky_eligible else 0
    return base + exact + risky


def exact_score_prob(prob_of_pick: float, hp: float, dp: float, ap: float) -> float:
    """Heuristic: more mismatched games have more predictable scorelines."""
    spread = max(hp, dp, ap) - min(hp, dp, ap)
    # 6-12% for the modal score in dominant matches, 4-7% in close ones
    return 0.04 + 0.10 * spread


def summarize(rows, round_id: str):
    enriched = []
    for r in rows:
        if any(v is None for v in [r["hp"], r["dp"], r["ap"], r["ov"], r["bt"]]):
            continue
        hp, dp, ap = float(r["hp"]), float(r["dp"]), float(r["ap"])
        ov, bt = float(r["ov"]), float(r["bt"])
        side, prob = pick(hp, dp, ap)
        team = r["home"] if side == "H" else (r["away"] if side == "A" else "Draw")
        risky = is_risky_candidate(side, team)
        score = predict_score(hp, dp, ap, ov, bt)
        esp = exact_score_prob(prob, hp, dp, ap)
        ev = expected_points(side, prob, esp, risky)
        enriched.append({
            "date": r["date"].strftime("%a %d %b"),
            "fixture": f"{r['home']} vs {r['away']}",
            "pick": team,
            "score": score,
            "win_pct": f"{prob * 100:.0f}%",
            "risky": "RISKY" if risky else "",
            "ev": ev,
        })

    print(f"\n=== ROUND {round_id} — {len(enriched)} matches — ordered by date ===\n")
    for e in enriched:
        print(f"  {e['date']:>10}  {e['fixture']:<40} -> {e['pick']:<22} {e['score']:>4}  ({e['win_pct']}) {e['risky']}")

    print(f"\n=== ROUND {round_id} — TOP 10 BY EXPECTED POINTS ===\n")
    for e in sorted(enriched, key=lambda x: -x["ev"])[:10]:
        print(f"  EV {e['ev']:5.1f}  {e['fixture']:<40} -> {e['pick']:<20} {e['score']}  {e['risky']}")

    booster = max(enriched, key=lambda x: x["ev"])
    print(f"\n=== BOOSTER 2x RECOMMENDATION ===")
    print(f"  {booster['fixture']} -> {booster['pick']} {booster['score']}")
    print(f"  EV {booster['ev']:.1f} (doubles to {booster['ev'] * 2:.1f})")
    if booster["risky"]:
        print(f"  Stacks with Risky bonus: max ceiling 80 pts")

    risky_picks = [e for e in enriched if e["risky"]]
    if risky_picks:
        print(f"\n=== RISKY BONUS CANDIDATES (no booster, just contrarian picks) ===")
        for e in sorted(risky_picks, key=lambda x: -x["ev"]):
            print(f"  {e['fixture']:<40} -> {e['pick']} {e['score']}  ({e['win_pct']})")

    safest_exact = sorted(enriched, key=lambda x: -float(x["win_pct"].rstrip("%")))[0]
    print(f"\n=== HIGHEST CONFIDENCE EXACT SCORE (for outside bets) ===")
    print(f"  {safest_exact['fixture']} -> {safest_exact['score']}  ({safest_exact['win_pct']} win)")


def main():
    ap_arg = argparse.ArgumentParser()
    ap_arg.add_argument("--round", required=True, choices=list(ROUND_WINDOWS.keys()),
                        help="Round to refresh picks for")
    ap_arg.add_argument("--skip-recompute", action="store_true",
                        help="Skip ELO recompute and prediction rewrite, just read DB")
    args = ap_arg.parse_args()

    print(f"[refresh_wc_round] Round {args.round} — {datetime.now():%Y-%m-%d %H:%M}")

    if not args.skip_recompute:
        if not check_prev_round_settled(args.round):
            print("Previous round not fully settled — refresh anyway? (Ctrl-C to abort, Enter to continue)")
            input()
        recompute_elo()
        rewrite_predictions()

    rows = fetch_round(args.round)
    if not rows:
        print(f"No predictions found for round {args.round} window.")
        sys.exit(1)
    summarize(rows, args.round)


if __name__ == "__main__":
    main()
