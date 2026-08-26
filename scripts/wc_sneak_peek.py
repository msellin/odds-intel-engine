"""WC SNEAK-PEEK — what our bots would pick on WC matches today.

The standard betting pipeline reads predictions where `source='af'`, but WC
fixtures only have `national_team_v1` / `national_team_v1_blended` predictions
right now (AF predictions land tomorrow at 05:30 UTC for matches kicking off
on 06-11). This script reproduces the simple edge math the pipeline uses, but
against the national-team predictions + latest odds in `odds_snapshots`.

Output: a markdown report in dev/active/wc-sneak-peek-YYYY-MM-DD.md with one
section per market (1x2, BTTS, OU 2.5), top picks ranked by edge%, plus a
sample-row breakdown per match. Operator-facing only — does NOT write to
simulated_bets, does NOT touch the public bot funnel.

Important caveats:
  - Uses national_team_v1_blended for 1x2 (the most sophisticated WC model),
    falls back to national_team_v1 for BTTS/OU (blended doesn't cover those).
  - Edge = model_prob - implied_prob. NO Platt calibration applied.
  - NO meta-model filter (B-ML3 is 1x2/AH only and trained on AF preds anyway).
  - NO PIN-CROSS-DRIFT veto (would never fire — drift requires ≥2 snapshots
    and the sweep just inserted; tomorrow's refreshes give us drift).
  - NO league exposure cap, NO Pinnacle-disagreement gate.
  - This is the model's view, NOT a calibrated bot signal. Treat as preview.

Run:
    python3 scripts/wc_sneak_peek.py
"""
from __future__ import annotations

import sys
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query


# Edge thresholds mirror the bot configs in workers/jobs/daily_pipeline_v2.py.
# Use bot_v10_all's "calibrated" gates as the reference — that's the bot the
# track record actually leans on (+17.5% true 60d total ROI on n=157).
EDGE_THRESHOLD_1X2_FAV = 0.05    # odds < 2.0
EDGE_THRESHOLD_1X2_LONG = 0.08   # odds >= 2.0
EDGE_THRESHOLD_BTTS = 0.06
EDGE_THRESHOLD_OU = 0.05

# Pinnacle availability is the bot-cohort sanity gate — we only show picks
# where Pinnacle has quoted the market. (Soft books alone are too easy to beat
# on paper; the picks become unreliable in real-money production.)
REQUIRE_PINNACLE = True


def load_wc_fixtures() -> list[dict]:
    return execute_query(
        """SELECT m.id, m.date, ht.name AS home, at.name AS away
           FROM matches m
           JOIN leagues l ON l.id = m.league_id
           LEFT JOIN teams ht ON ht.id = m.home_team_id
           LEFT JOIN teams at ON at.id = m.away_team_id
           WHERE l.name = 'World Cup' AND m.date >= '2026-06-11'
             AND m.status::text = 'scheduled'
           ORDER BY m.date"""
    )


def load_predictions(match_ids: list[str]) -> dict[str, dict[str, float]]:
    """Per match: {market_selection: model_prob}.

    1x2 prefers national_team_v1_blended; BTTS/OU fall back to national_team_v1.
    """
    out: dict[str, dict[str, float]] = defaultdict(dict)
    # Pass 1: national_team_v1 (covers all 7 markets)
    rows = execute_query(
        """SELECT match_id, market, model_probability
           FROM predictions
           WHERE source = 'national_team_v1'
             AND match_id = ANY(%s::uuid[])""",
        [match_ids],
    )
    for r in rows:
        out[str(r["match_id"])][r["market"]] = float(r["model_probability"])
    # Pass 2: national_team_v1_blended overrides 1x2 where present (it's the
    # market-blended version, more reliable on 1x2 specifically).
    rows = execute_query(
        """SELECT match_id, market, model_probability
           FROM predictions
           WHERE source = 'national_team_v1_blended'
             AND match_id = ANY(%s::uuid[])""",
        [match_ids],
    )
    for r in rows:
        out[str(r["match_id"])][r["market"]] = float(r["model_probability"])
    return out


def load_best_odds(match_ids: list[str]) -> dict[str, dict[str, dict]]:
    """Per match: {market_selection: {'best_odds', 'best_book', 'pinnacle_odds'}}.

    Best = max odds across books (highest implied EV) on the latest snapshot.
    Pinnacle odds tracked separately for the availability gate.
    """
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    rows = execute_query(
        """SELECT DISTINCT ON (match_id, market, selection, bookmaker)
                  match_id, market, selection, bookmaker, odds
           FROM odds_snapshots
           WHERE match_id = ANY(%s::uuid[])
             AND is_live = false
             AND odds > 1.0
             AND market IN ('1x2', 'btts', 'over_under_25')
           ORDER BY match_id, market, selection, bookmaker, timestamp DESC""",
        [match_ids],
    )
    # Aggregate to best + pinnacle per (match, market, selection)
    by_key: dict[tuple, dict] = defaultdict(lambda: {"best_odds": 0.0, "best_book": None, "pinnacle_odds": None})
    for r in rows:
        key = (str(r["match_id"]), r["market"], r["selection"])
        odds = float(r["odds"])
        bm = r["bookmaker"]
        entry = by_key[key]
        if odds > entry["best_odds"]:
            entry["best_odds"] = odds
            entry["best_book"] = bm
        if bm == "Pinnacle":
            entry["pinnacle_odds"] = odds
    # Normalize the keys to match prediction market names (the predictions table
    # uses '1x2_home', 'btts_yes', 'over_2_5'; odds_snapshots uses '1x2'+selection,
    # 'btts'+selection, 'over_under_25'+selection).
    market_xlate = {
        ("1x2", "home"): "1x2_home",
        ("1x2", "draw"): "1x2_draw",
        ("1x2", "away"): "1x2_away",
        ("btts", "yes"): "btts_yes",
        ("btts", "no"): "btts_no",
        ("over_under_25", "over"): "over_2_5",
        ("over_under_25", "under"): "under_2_5",
    }
    for (mid, mkt, sel), data in by_key.items():
        pred_key = market_xlate.get((mkt, sel))
        if pred_key is None:
            continue
        out[mid][pred_key] = data
    return out


def edge_threshold_for(market: str, odds: float) -> float:
    if market.startswith("1x2_"):
        return EDGE_THRESHOLD_1X2_FAV if odds < 2.0 else EDGE_THRESHOLD_1X2_LONG
    if market.startswith("btts_"):
        return EDGE_THRESHOLD_BTTS
    if market in ("over_2_5", "under_2_5"):
        return EDGE_THRESHOLD_OU
    return 1.0  # unreachable for our 3 markets


def market_display(market: str) -> tuple[str, str]:
    """Pretty (market, selection) for output."""
    if market == "1x2_home": return ("1X2", "Home")
    if market == "1x2_draw": return ("1X2", "Draw")
    if market == "1x2_away": return ("1X2", "Away")
    if market == "btts_yes": return ("BTTS", "Yes")
    if market == "btts_no":  return ("BTTS", "No")
    if market == "over_2_5": return ("O/U 2.5", "Over")
    if market == "under_2_5": return ("O/U 2.5", "Under")
    return (market, "")


def main() -> None:
    fixtures = load_wc_fixtures()
    if not fixtures:
        print("No WC fixtures found.")
        return
    match_ids = [str(f["id"]) for f in fixtures]
    print(f"Loaded {len(fixtures)} WC fixtures")

    preds = load_predictions(match_ids)
    odds = load_best_odds(match_ids)
    print(f"Loaded {sum(len(v) for v in preds.values())} prediction rows across {len(preds)} matches")
    print(f"Loaded {sum(len(v) for v in odds.values())} (market,selection) odds rows across {len(odds)} matches")

    # Compute picks. For each (match, market, selection): edge = model_prob - implied(best_odds).
    picks: list[dict] = []
    for fix in fixtures:
        mid = str(fix["id"])
        for market, prob in preds.get(mid, {}).items():
            odata = odds.get(mid, {}).get(market)
            if not odata or odata["best_odds"] <= 1.0:
                continue
            if REQUIRE_PINNACLE and odata["pinnacle_odds"] is None:
                continue
            best_odds = odata["best_odds"]
            implied = 1.0 / best_odds
            edge = prob - implied
            thresh = edge_threshold_for(market, best_odds)
            if edge < thresh:
                continue
            picks.append({
                "match_id": mid,
                "kickoff": fix["date"],
                "home": fix["home"],
                "away": fix["away"],
                "market": market,
                "model_prob": prob,
                "best_odds": best_odds,
                "best_book": odata["best_book"],
                "pinnacle_odds": odata["pinnacle_odds"],
                "implied": implied,
                "edge": edge,
                "edge_pct": edge * 100,
                "threshold": thresh * 100,
            })

    picks.sort(key=lambda p: p["edge"], reverse=True)
    print(f"\nFound {len(picks)} picks clearing edge thresholds (Pinnacle-gated)")

    # Group by market for the report sections
    by_market: dict[str, list[dict]] = defaultdict(list)
    for p in picks:
        if p["market"].startswith("1x2_"):
            by_market["1x2"].append(p)
        elif p["market"].startswith("btts_"):
            by_market["btts"].append(p)
        else:
            by_market["ou25"].append(p)

    # Write the report
    today = date.today().isoformat()
    out_path = Path(f"dev/active/wc-sneak-peek-{today}.md")
    lines: list[str] = []
    lines.append(f"# WC Sneak Peek — what our bots would pick today\n")
    lines.append(f"**Generated:** {datetime.utcnow().isoformat()}Z")
    lines.append(f"**Predictions source:** `national_team_v1_blended` (1x2) + `national_team_v1` (BTTS, OU 2.5)")
    lines.append(f"**Odds source:** latest non-live `odds_snapshots` (post 2026-06-10 sweep)")
    lines.append(f"**Edge thresholds:** 1x2 fav ≥{EDGE_THRESHOLD_1X2_FAV*100:.0f}% / 1x2 long ≥{EDGE_THRESHOLD_1X2_LONG*100:.0f}% / BTTS ≥{EDGE_THRESHOLD_BTTS*100:.0f}% / OU ≥{EDGE_THRESHOLD_OU*100:.0f}%")
    lines.append(f"**Pinnacle gate:** {'ON' if REQUIRE_PINNACLE else 'OFF'}  (best book may differ; Pinnacle just has to quote it)\n")
    lines.append(f"**Total picks:** {len(picks)}  •  1x2: {len(by_market['1x2'])}  •  BTTS: {len(by_market['btts'])}  •  OU: {len(by_market['ou25'])}\n")
    lines.append("---\n")
    lines.append("> **Operator note:** these are RAW model-vs-market edges. No Platt calibration on the WC model, no B-ML3 meta-filter, no PIN-CROSS-DRIFT veto (the live helper just shipped; trail will warm up overnight). Day-1 picks during peak-traffic WC opener carry credibility risk if surfaced publicly. Recommend treating this as preview — wait for AF preds + the normal pipeline at 06:00 UTC tomorrow for the real picks.\n")

    for section_key, section_label in (("1x2", "1X2 picks"), ("btts", "BTTS picks"), ("ou25", "Over/Under 2.5 picks")):
        section_picks = by_market[section_key]
        lines.append(f"\n## {section_label}  ({len(section_picks)})\n")
        if not section_picks:
            lines.append(f"_No {section_label.lower()} cleared the edge gates._\n")
            continue
        lines.append("| Kickoff (UTC) | Match | Pick | Model | Odds | Book | Pinn | Edge | Gate |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for p in section_picks:
            mkt_pretty, sel_pretty = market_display(p["market"])
            ko = p["kickoff"].strftime("%m-%d %H:%M")
            lines.append(
                f"| {ko} | {p['home']} vs {p['away']} | **{mkt_pretty} {sel_pretty}** | "
                f"{p['model_prob']*100:.1f}% | {p['best_odds']:.2f} | {p['best_book']} | "
                f"{p['pinnacle_odds']:.2f} | **+{p['edge_pct']:.1f}pp** | ≥{p['threshold']:.0f}pp |"
            )

    lines.append("\n---\n")
    lines.append(f"_Generated by `scripts/wc_sneak_peek.py`. Operator-facing review report, not surfaced publicly. Re-run with the same command to refresh._\n")

    out_path.write_text("\n".join(lines))
    print(f"\nWrote report to: {out_path}")
    print(f"  1x2 picks: {len(by_market['1x2'])}")
    print(f"  BTTS picks: {len(by_market['btts'])}")
    print(f"  OU picks: {len(by_market['ou25'])}")


if __name__ == "__main__":
    main()
