"""
OddsIntel — Backtest National-Team Predictor (WC-PHASE-3 validation)

Walks every finished international match in chronological order. For
holdout matches (Euro 2024 + WC 2022 + WC 2018), predicts using ELO state
+ recent goal stats BEFORE seeing the result, then updates state. This
keeps the backtest leak-free without needing a static pre-built training
set.

Reports:
  - log-loss vs 33/33/33 baseline (and the Brier score)
  - Calibration: predicted prob bucket → actual frequency
  - O/U 2.5 calibration + accuracy
  - BTTS accuracy
  - Top wins/losses (biggest model misses)

Run:
  python scripts/backtest_national_team_model.py
  python scripts/backtest_national_team_model.py --holdout euro_2024  # subset
"""
import sys, os, argparse, math
from pathlib import Path
from collections import defaultdict, deque
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console
from rich.table import Table
from workers.api_clients.db import execute_query
from workers.model.national_team_predictor import predict_match, TeamGoalStats
# ELO walk constants live in compute_international_elo.py so the script
# and the predictor stay aligned (single source of truth for K-factor +
# competition categorisation).
from scripts.compute_international_elo import (
    COMP_CATEGORY, K_BY_CAT, HOME_ADV_BY_CAT, category_for_league,
)

console = Console()

# Holdouts: (label, set of (league_af_id, season) tuples)
HOLDOUTS = {
    "wc_2018":    {(1, 2018)},
    "wc_2022":    {(1, 2022)},
    "euro_2024":  {(4, 2024)},
    "euro_2020":  {(4, 2020)},
    "afcon_2023": {(6, 2023)},
    "copa_2024":  {(9, 2024)},
}

# Default holdout set — use the most recent finished major tournaments.
DEFAULT_HOLDOUT = {(1, 2022), (4, 2024), (9, 2024), (6, 2023)}


def safe_log(p: float) -> float:
    return math.log(max(p, 1e-12))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", type=str, default=None,
                        help=f"Comma-separated subset of {list(HOLDOUTS.keys())} (default: wc22+euro24+copa24+afcon23)")
    parser.add_argument("--form-window", type=int, default=20,
                        help="N most-recent internationals for goal stats (default 20)")
    args = parser.parse_args()

    if args.holdout:
        names = [s.strip() for s in args.holdout.split(",") if s.strip()]
        holdout_keys = set()
        for n in names:
            if n not in HOLDOUTS:
                console.print(f"[red]unknown holdout '{n}'[/red]")
                sys.exit(1)
            holdout_keys |= HOLDOUTS[n]
    else:
        holdout_keys = DEFAULT_HOLDOUT

    console.print(f"[bold cyan]═══ Backtest national-team v1 ═══[/bold cyan]")
    console.print(f"  holdout: {sorted(holdout_keys)}")
    console.print(f"  goal-stats window: last {args.form_window} matches\n")

    matches = execute_query("""
        SELECT m.id, m.date::date AS match_date,
               m.home_team_id, m.away_team_id,
               m.score_home, m.score_away,
               m.season AS match_season,
               l.api_football_id AS league_af_id, l.name AS league_name
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country = 'World'
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND l.api_football_id = ANY(%s::int[])
        ORDER BY m.date ASC, m.id ASC
    """, [list(COMP_CATEGORY.keys())])

    console.print(f"  loaded {len(matches)} finished international matches")

    # In-memory ELO state
    elo: dict[str, float] = {}
    n_matches: dict[str, int] = {}
    # Recent goals deques per team: list of (goals_for, goals_against, weight)
    form: dict[str, deque] = defaultdict(lambda: deque(maxlen=args.form_window))

    # Collected predictions on holdout
    rows = []  # one dict per holdout match with both prediction + actual outcome

    for m in matches:
        cat = category_for_league(m["league_af_id"])
        K = K_BY_CAT[cat]
        h_adv = HOME_ADV_BY_CAT[cat]
        h_id, a_id = m["home_team_id"], m["away_team_id"]
        sh, sa = m["score_home"], m["score_away"]

        in_holdout = (m["league_af_id"], m["match_season"]) in holdout_keys

        if in_holdout:
            # Predict using CURRENT state (before this match updates ELO/form)
            h_elo = elo.get(h_id, 1500.0)
            a_elo = elo.get(a_id, 1500.0)

            def _stats(team_id):
                d = form.get(team_id) or deque()
                if not d:
                    return TeamGoalStats(0.0, 0.0, 0)
                w_total = sum(w for _, _, w in d)
                if w_total == 0:
                    return TeamGoalStats(0.0, 0.0, 0)
                gf = sum(g * w for g, _, w in d) / w_total
                ga = sum(c * w for _, c, w in d) / w_total
                return TeamGoalStats(gf, ga, len(d))

            pred = predict_match(h_elo, a_elo, _stats(h_id), _stats(a_id), comp_category=cat)

            if sh > sa: actual_1x2 = "home"
            elif sh < sa: actual_1x2 = "away"
            else: actual_1x2 = "draw"
            actual_over25 = 1 if (sh + sa) >= 3 else 0
            actual_btts = 1 if (sh >= 1 and sa >= 1) else 0

            rows.append({
                "league_af_id": m["league_af_id"],
                "league_name": m["league_name"],
                "match_date": m["match_date"],
                "home_id": h_id, "away_id": a_id,
                "score_h": sh, "score_a": sa,
                "p_home": pred["1x2_home"], "p_draw": pred["1x2_draw"], "p_away": pred["1x2_away"],
                "p_over25": pred["over_2_5"], "p_btts": pred["btts_yes"],
                "lam_h": pred["lam_h"], "lam_a": pred["lam_a"],
                "home_elo_pre": h_elo, "away_elo_pre": a_elo,
                "actual_1x2": actual_1x2,
                "actual_over25": actual_over25,
                "actual_btts": actual_btts,
            })

        # Always update ELO + form state (holdouts contribute to subsequent matches too — fine, since by then those matches happened)
        h_elo_eff = elo.get(h_id, 1500.0) + h_adv
        a_elo_eff = elo.get(a_id, 1500.0)
        exp_h = 1.0 / (1 + 10 ** ((a_elo_eff - h_elo_eff) / 400))
        gd = abs(sh - sa)
        gd_mult = max(1.0, (gd + 1) ** 0.5)
        if sh > sa: act_h = 1.0
        elif sh < sa: act_h = 0.0
        else: act_h = 0.5
        new_h = elo.get(h_id, 1500.0) + K * gd_mult * (act_h - exp_h)
        new_a = elo.get(a_id, 1500.0) + K * gd_mult * ((1 - act_h) - (1 - exp_h))
        elo[h_id] = new_h
        elo[a_id] = new_a
        n_matches[h_id] = n_matches.get(h_id, 0) + 1
        n_matches[a_id] = n_matches.get(a_id, 0) + 1

        from workers.model.national_team_predictor import COMP_WEIGHT
        w = COMP_WEIGHT.get(cat, 0.5)
        form[h_id].append((float(sh), float(sa), w))
        form[a_id].append((float(sa), float(sh), w))

    # === Report ===
    console.print(f"\n[bold]Holdout matches predicted:[/bold] {len(rows)}")
    if not rows:
        console.print("[red]No holdout matches — check that holdout league/season pairs match DB[/red]")
        return

    # 1X2 log-loss + brier
    ll_model = 0.0; ll_baseline = 0.0
    brier_model = 0.0
    correct = 0
    for r in rows:
        if r["actual_1x2"] == "home": p = r["p_home"]
        elif r["actual_1x2"] == "draw": p = r["p_draw"]
        else: p = r["p_away"]
        ll_model += -safe_log(p)
        ll_baseline += -safe_log(1.0 / 3.0)
        # Brier — sum-of-squared-errors over 3 outcomes
        targets = {"home": 0, "draw": 0, "away": 0}; targets[r["actual_1x2"]] = 1
        brier_model += sum((r[f"p_{k}"] - targets[k]) ** 2 for k in ("home", "draw", "away"))
        # Argmax accuracy
        best = max(("home", "draw", "away"), key=lambda k: r[f"p_{k}"])
        if best == r["actual_1x2"]: correct += 1

    n = len(rows)
    ll_model_avg = ll_model / n; ll_baseline_avg = ll_baseline / n
    brier_avg = brier_model / n
    acc = correct / n * 100

    # O/U 2.5
    over_acc = 0; over_ll = 0.0
    for r in rows:
        p_over = r["p_over25"]
        pred_over = 1 if p_over >= 0.5 else 0
        if pred_over == r["actual_over25"]: over_acc += 1
        # log-loss for binary
        p_obs = p_over if r["actual_over25"] == 1 else (1 - p_over)
        over_ll += -safe_log(p_obs)
    over_acc_pct = over_acc / n * 100
    over_ll_avg = over_ll / n
    over_baseline = -safe_log(0.5)  # 50/50 baseline log-loss

    # BTTS
    btts_acc = 0; btts_ll = 0.0
    for r in rows:
        p_btts = r["p_btts"]
        pred_btts = 1 if p_btts >= 0.5 else 0
        if pred_btts == r["actual_btts"]: btts_acc += 1
        p_obs = p_btts if r["actual_btts"] == 1 else (1 - p_btts)
        btts_ll += -safe_log(p_obs)
    btts_acc_pct = btts_acc / n * 100
    btts_ll_avg = btts_ll / n

    t = Table(title="Backtest summary")
    t.add_column("Metric"); t.add_column("Value"); t.add_column("Baseline / note")
    t.add_row("Holdout matches", f"{n}", "")
    t.add_row("1X2 log-loss", f"{ll_model_avg:.4f}", f"{ll_baseline_avg:.4f} (33/33/33)")
    t.add_row("1X2 log-loss improvement", f"{(ll_baseline_avg - ll_model_avg) / ll_baseline_avg * 100:.1f}%", "≥5% = useful")
    t.add_row("1X2 Brier score", f"{brier_avg:.4f}", "0=perfect, 0.667=33/33/33")
    t.add_row("1X2 top-pick accuracy", f"{acc:.1f}%", "33.3% baseline")
    t.add_row("O/U 2.5 log-loss", f"{over_ll_avg:.4f}", f"{over_baseline:.4f} (50/50)")
    t.add_row("O/U 2.5 accuracy", f"{over_acc_pct:.1f}%", "50% baseline")
    t.add_row("BTTS log-loss", f"{btts_ll_avg:.4f}", f"{over_baseline:.4f} (50/50)")
    t.add_row("BTTS accuracy", f"{btts_acc_pct:.1f}%", "50% baseline")
    console.print(t)

    # Calibration: bin predicted prob, show actual frequency
    console.print("\n[bold]1X2 calibration — predicted prob bucket → actual hit rate:[/bold]")
    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        bin_rows = []
        for r in rows:
            for k in ("home", "draw", "away"):
                p = r[f"p_{k}"]
                if lo <= p < hi:
                    bin_rows.append((p, 1 if r["actual_1x2"] == k else 0))
        if not bin_rows:
            console.print(f"  [{lo:.1f}, {hi:.1f}): n=0")
            continue
        avg_p = sum(p for p, _ in bin_rows) / len(bin_rows)
        hit_rate = sum(h for _, h in bin_rows) / len(bin_rows) * 100
        console.print(f"  [{lo:.1f}, {hi:.1f}):  n={len(bin_rows):>4}  avg_predicted={avg_p:.3f}  actual={hit_rate:.1f}%")

    # Per-holdout breakdown
    console.print("\n[bold]Per-holdout breakdown:[/bold]")
    by_key: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_key[(r["league_af_id"], r["league_name"])].append(r)
    for key, rs in sorted(by_key.items()):
        ll = 0.0; corr = 0
        for r in rs:
            p_actual = r[f"p_{r['actual_1x2']}"]
            ll += -safe_log(p_actual)
            best = max(("home", "draw", "away"), key=lambda k: r[f"p_{k}"])
            if best == r["actual_1x2"]: corr += 1
        console.print(f"  {key[1]}: n={len(rs)}  log-loss={ll/len(rs):.4f}  accuracy={corr/len(rs)*100:.1f}%")

    # Biggest model misses (most surprising losses) — sanity-check predictions weren't catastrophic
    console.print("\n[bold]Biggest 5 model misses (lowest prob assigned to actual outcome):[/bold]")
    rows_sorted = sorted(rows, key=lambda r: r[f"p_{r['actual_1x2']}"])
    teams = execute_query(
        "SELECT id, name FROM teams WHERE id = ANY(%s::uuid[])",
        [list({r["home_id"] for r in rows_sorted[:5]} | {r["away_id"] for r in rows_sorted[:5]})]
    )
    name_by_id = {t["id"]: t["name"] for t in teams}
    for r in rows_sorted[:5]:
        h = name_by_id.get(r["home_id"], "?")[:14]
        a = name_by_id.get(r["away_id"], "?")[:14]
        console.print(f"  {r['match_date']}  {h:<14} {r['score_h']}-{r['score_a']} {a:<14}  "
                      f"P({r['actual_1x2']})={r[f'p_{r['actual_1x2']}']:.3f}  "
                      f"(model said H={r['p_home']:.2f} D={r['p_draw']:.2f} A={r['p_away']:.2f})")


if __name__ == "__main__":
    main()
