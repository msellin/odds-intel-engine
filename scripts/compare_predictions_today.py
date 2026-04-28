#!/usr/bin/env python3
"""
Compare our model predictions vs API-Football predictions for any date.

For settled matches: shows whether AF predicted correctly and if we agreed.
For upcoming matches: shows the probability divergence between models.

The /predictions endpoint works for any fixture_id (past or future).

Usage:
  python scripts/compare_predictions_today.py              # today
  python scripts/compare_predictions_today.py --date 2026-04-27   # yesterday
  python scripts/compare_predictions_today.py --settled    # only show settled matches
"""

import sys
import argparse
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import get_prediction, parse_prediction
from workers.api_clients.supabase_client import get_client
from rich.console import Console
from rich.table import Table

console = Console()


def _pct(val) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):.0%}"
    except Exception:
        return "—"


def _fetch_af_pred_for_match(client, match_id: str, af_id: int, existing_raw) -> dict | None:
    """Get AF prediction — from DB cache first, else fetch live and cache."""
    if existing_raw:
        parsed = parse_prediction(existing_raw)
        if parsed.get("af_home_prob"):
            return parsed

    try:
        raw = get_prediction(af_id)
        if not raw:
            return None
        parsed = parse_prediction(raw)
        if not parsed.get("af_home_prob"):
            return None
        # Cache in DB
        try:
            client.table("matches").update({"af_prediction": parsed["raw"]}).eq("id", match_id).execute()
        except Exception:
            pass
        return parsed
    except Exception:
        return None


def run(target_date: str, settled_only: bool = False):
    client = get_client()
    console.print(f"\n[bold cyan]═══ Prediction Comparison: {target_date} ═══[/bold cyan]\n")

    # 1. Fetch matches for the date
    matches_raw = client.table("matches").select(
        "id, api_football_id, af_prediction, status, "
        "home_team:home_team_id(name), away_team:away_team_id(name), "
        "score_home, score_away, leagues(name, country)"
    ).gte("date", f"{target_date}T00:00:00").lte(
        "date", f"{target_date}T23:59:59"
    ).not_.is_("api_football_id", "null").execute().data

    if settled_only:
        matches_raw = [m for m in matches_raw if m.get("status") == "finished"]

    console.print(f"Found {len(matches_raw)} matches with api_football_id for {target_date}")
    if not matches_raw:
        console.print("[yellow]No matches found. Try running: python scripts/enrich_matches.py --date {target_date}[/yellow]")
        return

    # 2. Fetch our bets for these matches
    match_ids = [m["id"] for m in matches_raw]
    bets_raw = client.table("simulated_bets").select(
        "id, match_id, market, selection, odds_at_pick, model_probability, "
        "calibrated_prob, edge_percent, result, pnl, af_agrees, "
        "af_home_prob, af_draw_prob, af_away_prob, bots(name)"
    ).in_("match_id", match_ids).execute().data

    bets_by_match: dict[str, list] = {}
    for b in bets_raw:
        bets_by_match.setdefault(b["match_id"], []).append(b)

    matches_with_bets = [m for m in matches_raw if m["id"] in bets_by_match]
    console.print(f"We have bets on {len(matches_with_bets)} of these matches ({len(bets_raw)} total bets)\n")

    if not matches_with_bets:
        console.print("[yellow]No bets found for this date.[/yellow]")
        console.print(f"[dim]Tip: bets from this date may predate migration 008 — they won't have af_agrees populated.[/dim]")
        console.print(f"[dim]You can still see AF predictions alongside results below.[/dim]\n")
        # Fall back to showing all matches (not just bet ones) with predictions
        show_all = matches_raw
    else:
        show_all = matches_with_bets

    # 3. Fetch/cache AF predictions for relevant matches
    console.print(f"[cyan]Fetching AF predictions...[/cyan]")
    af_pred_map: dict[str, dict] = {}
    fetched = cached = failed = 0

    for m in matches_raw:
        match_id = m["id"]
        af_id = m["api_football_id"]
        result = _fetch_af_pred_for_match(client, match_id, af_id, m.get("af_prediction"))
        if result:
            af_pred_map[match_id] = result
            if m.get("af_prediction"):
                cached += 1
            else:
                fetched += 1
        else:
            failed += 1

    console.print(f"  {cached} from cache, {fetched} newly fetched, {failed} unavailable\n")

    # 4. SETTLED MATCH SUMMARY (if we have settled bets)
    settled_bets = [b for b in bets_raw if b["result"] in ("won", "lost")]
    if settled_bets:
        console.print("[bold]═══ Settled Bets with AF Comparison ═══[/bold]\n")

        t = Table(show_lines=True)
        t.add_column("Match", no_wrap=True)
        t.add_column("Score", justify="center")
        t.add_column("Selection", justify="center")
        t.add_column("Our\nProb", justify="right")
        t.add_column("AF\nProb", justify="right")
        t.add_column("Prob\nDelta", justify="right")
        t.add_column("AF\nPick", justify="center")
        t.add_column("Result", justify="center")
        t.add_column("P&L", justify="right")
        t.add_column("AF\nRight?", justify="center")

        af_correct = 0
        af_wrong = 0
        af_same_as_us = 0
        af_diff_from_us = 0

        for m in show_all:
            match_id = m["id"]
            match_bets = bets_by_match.get(match_id, [])
            settled = [b for b in match_bets if b["result"] in ("won", "lost")]
            if not settled:
                continue

            home = m["home_team"][0]["name"] if isinstance(m["home_team"], list) else m["home_team"].get("name", "?")
            away = m["away_team"][0]["name"] if isinstance(m["away_team"], list) else m["away_team"].get("name", "?")
            sh = m.get("score_home")
            sa = m.get("score_away")
            score_str = f"{sh}–{sa}" if sh is not None else "—"

            af = af_pred_map.get(match_id)

            # Actual result direction
            actual_winner = None
            if sh is not None and sa is not None:
                if int(sh) > int(sa):
                    actual_winner = "Home"
                elif int(sh) < int(sa):
                    actual_winner = "Away"
                else:
                    actual_winner = "Draw"

            # AF's top pick
            af_top_pick = None
            if af:
                hp = af.get("af_home_prob") or 0
                dp = af.get("af_draw_prob") or 0
                ap = af.get("af_away_prob") or 0
                af_top_pick = "Home" if hp >= dp and hp >= ap else ("Away" if ap >= hp and ap >= dp else "Draw")

            for b in settled:
                sel = b.get("selection", "?")
                our_p = b.get("model_probability")
                result = b.get("result")
                pnl = b.get("pnl") or 0
                odds = b.get("odds_at_pick", 0)

                # Our prob for this selection
                our_prob_str = _pct(our_p)

                # AF prob for same selection (case-insensitive)
                af_p = None
                sel_l = sel.lower()
                if af:
                    if "home" in sel_l:
                        af_p = af.get("af_home_prob")
                    elif "away" in sel_l:
                        af_p = af.get("af_away_prob")
                    elif "draw" in sel_l:
                        af_p = af.get("af_draw_prob")
                    elif "over" in sel_l:
                        af_p = af.get("af_poisson_home")  # proxy
                af_prob_str = _pct(af_p)

                # Delta
                if our_p and af_p:
                    delta = float(our_p) - float(af_p)
                    delta_color = "green" if abs(delta) < 0.05 else "yellow" if abs(delta) < 0.12 else "red"
                    delta_str = f"[{delta_color}]{delta:+.0%}[/{delta_color}]"
                else:
                    delta_str = "—"

                # Was AF right?
                af_pick_correct = None
                if af_top_pick and actual_winner and "1x2" in (b.get("market") or "").lower():
                    af_pick_correct = (af_top_pick == actual_winner)
                    if af_pick_correct:
                        af_correct += 1
                    else:
                        af_wrong += 1

                af_right_str = "[green]✓[/green]" if af_pick_correct is True else ("[red]✗[/red]" if af_pick_correct is False else "—")

                # Did AF agree with us? (case-insensitive)
                sel_l = sel.lower()
                if af_top_pick:
                    if ("home" in sel_l and af_top_pick == "Home") or \
                       ("away" in sel_l and af_top_pick == "Away") or \
                       ("draw" in sel_l and af_top_pick == "Draw"):
                        af_same_as_us += 1
                        af_agrees_icon = "[green]✓[/green]"
                    else:
                        af_diff_from_us += 1
                        af_agrees_icon = "[red]✗[/red]"
                else:
                    af_agrees_icon = "—"

                result_str = "[green]WON[/green]" if result == "won" else "[red]LOST[/red]"
                pnl_str = f"[green]+{pnl:.2f}[/green]" if pnl > 0 else f"[red]{pnl:.2f}[/red]"

                t.add_row(
                    f"{home[:15]}\nvs {away[:15]}",
                    score_str,
                    f"{sel}\n@{float(odds):.2f}",
                    our_prob_str,
                    af_prob_str,
                    delta_str,
                    af_top_pick or "—",
                    result_str,
                    pnl_str,
                    af_right_str,
                )

        console.print(t)

        # Agreement analysis
        total_af_rated = af_correct + af_wrong
        console.print(f"\n[bold]AF Prediction Accuracy on Our Bets:[/bold]")
        if total_af_rated > 0:
            console.print(f"  AF picked correct outcome:  {af_correct}/{total_af_rated} ({af_correct/total_af_rated:.0%})")
        console.print(f"  AF agreed with our pick:    {af_same_as_us} bets")
        console.print(f"  AF disagreed with our pick: {af_diff_from_us} bets")

        # ROI split
        af_agree_bets = [b for b in settled_bets if b.get("af_agrees") is True]
        af_disagree_bets = [b for b in settled_bets if b.get("af_agrees") is False]

        if af_agree_bets or af_disagree_bets:
            console.print(f"\n[bold]ROI split (from stored af_agrees column):[/bold]")

            def _roi(bets):
                if not bets:
                    return None, 0
                total_stake = sum(float(b["stake"]) for b in bets if b.get("stake"))
                total_pnl = sum(float(b["pnl"] or 0) for b in bets)
                return (total_pnl / total_stake if total_stake > 0 else 0), len(bets)

            ag_roi, ag_n = _roi(af_agree_bets)
            di_roi, di_n = _roi(af_disagree_bets)
            all_roi, all_n = _roi(settled_bets)

            console.print(f"  ALL:            {all_n} bets, ROI = {_pct(all_roi)}")
            if ag_n > 0:
                console.print(f"  AF agrees:      {ag_n} bets, ROI = {_pct(ag_roi)}")
            if di_n > 0:
                console.print(f"  AF disagrees:   {di_n} bets, ROI = {_pct(di_roi)}")
        else:
            console.print(f"\n[dim]af_agrees not populated on these bets (placed before migration 008).[/dim]")
            console.print(f"[dim]Future bets will have this data automatically.[/dim]")

    # 5. DIVERGENCE ANALYSIS (all matches with AF predictions)
    console.print(f"\n[bold]═══ Big Divergences (AF vs Our Model, >8%) ═══[/bold]\n")

    divergences = []
    for m in matches_raw:
        match_id = m["id"]
        af = af_pred_map.get(match_id)
        if not af:
            continue

        home = m["home_team"][0]["name"] if isinstance(m["home_team"], list) else m["home_team"].get("name", "?")
        away = m["away_team"][0]["name"] if isinstance(m["away_team"], list) else m["away_team"].get("name", "?")
        sh = m.get("score_home")
        sa = m.get("score_away")
        score_str = f"{sh}–{sa}" if sh is not None else "upcoming"

        # Determine actual result
        if sh is not None and sa is not None:
            actual_winner = "Home" if int(sh) > int(sa) else ("Away" if int(sh) < int(sa) else "Draw")
        else:
            actual_winner = None

        match_bets = bets_by_match.get(match_id, [])

        for b in match_bets:
            sel = b.get("selection", "")
            our_p = b.get("model_probability")
            if not our_p:
                continue

            # AF prob for same selection (case-insensitive)
            sel_l = sel.lower()
            if "home" in sel_l:
                af_p = af.get("af_home_prob")
            elif "away" in sel_l:
                af_p = af.get("af_away_prob")
            elif "draw" in sel_l:
                af_p = af.get("af_draw_prob")
            else:
                continue

            if not af_p:
                continue

            delta = float(our_p) - float(af_p)
            if abs(delta) > 0.08:
                divergences.append({
                    "match": f"{home} vs {away}",
                    "score": score_str,
                    "selection": sel,
                    "our_p": float(our_p),
                    "af_p": float(af_p),
                    "delta": delta,
                    "result": b.get("result"),
                    "actual_winner": actual_winner,
                    "af_advice": (af.get("af_advice") or "")[:50],
                    "pnl": float(b.get("pnl") or 0),
                })

    if divergences:
        divergences.sort(key=lambda x: abs(x["delta"]), reverse=True)
        td = Table()
        td.add_column("Match", no_wrap=True)
        td.add_column("Score", justify="center")
        td.add_column("Sel")
        td.add_column("Ours", justify="right")
        td.add_column("AF", justify="right")
        td.add_column("Δ", justify="right")
        td.add_column("Who\nWas Right?", justify="center")
        td.add_column("AF Advice", style="dim")

        for row in divergences[:15]:
            # Who was right?
            right = None
            if row.get("result") and row.get("actual_winner"):
                we_right = row["result"] == "won"
                if row["our_p"] > row["af_p"]:
                    right = "[green]US[/green]" if we_right else "[red]AF[/red]"
                else:
                    right = "[red]US[/red]" if not we_right else "[green]AF[/green]"
            elif row.get("result") == "pending":
                right = "[dim]pending[/dim]"
            else:
                right = "—"

            d = row["delta"]
            d_color = "green" if abs(d) < 0.05 else "yellow" if abs(d) < 0.12 else "red"
            td.add_row(
                row["match"][:30],
                row["score"],
                row["selection"],
                f"{row['our_p']:.0%}",
                f"{row['af_p']:.0%}",
                f"[{d_color}]{d:+.0%}[/{d_color}]",
                right,
                row["af_advice"],
            )
        console.print(td)
    else:
        if matches_with_bets:
            console.print("[green]No major divergences — models broadly agreed on our bets today.[/green]")
        else:
            console.print("[dim]No bets to compare — run for a date with active bets.[/dim]")

    console.print(f"\n[dim]For ongoing evaluation as bets settle: python scripts/evaluate_af_predictions.py[/dim]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--today", action="store_true", help="Use today instead of yesterday")
    parser.add_argument("--settled", action="store_true", help="Only show settled matches")
    args = parser.parse_args()

    if args.today:
        target = date.today().isoformat()
    elif args.date:
        target = args.date
    else:
        target = (date.today() - timedelta(days=1)).isoformat()  # Default: yesterday (settled)

    run(target, settled_only=args.settled)
