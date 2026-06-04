"""
OddsIntel — Write Blended National-Team Predictions (WC-A4)

Reads existing `national_team_v1` 1X2 predictions + the per-fixture market
consensus row from `wc_market_consensus`, blends them via
`workers.model.wc_blender.blend_with_confidence`, and writes the result
back to `predictions` tagged `source='national_team_v1_blended'`.

Why a separate source string?
  Keeps the raw model output auditable. The FE can switch to the blended
  source by changing one constant; the unblended row stays in the DB so
  we can show users "our model alone said X, the market said Y, we shipped Z".

Failure modes handled:
  - No `national_team_v1` row for a fixture (ELO missing, etc.):
        Skip — nothing to blend against. Logged but non-fatal.
  - `wc_market_consensus` row absent (A3 scraper hasn't run, or fixture
    isn't in its window):
        Fall back to own-only output. Row is still written with
        `source='national_team_v1_blended'` so the FE always has a row to
        pick up, with `reasoning` flagging `blended=False`.
  - Both present:
        Bayesian-blend by n_sources confidence; write 3 rows per fixture.

Usage:
    python3 scripts/write_blended_predictions.py
    python3 scripts/write_blended_predictions.py --dry-run --max-fixtures 5
    python3 scripts/write_blended_predictions.py --days 14

Idempotent — `bulk_store_predictions` upserts on
(match_id, market, source, model_version).
"""
from __future__ import annotations

import sys
import os
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console

from workers.api_clients.db import execute_query
from workers.api_clients.supabase_client import bulk_store_predictions
from workers.model.wc_blender import BLEND_LAMBDA, blend_with_confidence


console = Console()

SOURCE_OWN = "national_team_v1"
SOURCE_BLENDED = "national_team_v1_blended"
MODEL_VERSION = "national_team_v1_blended"
# WC league id — A3/national-team predictor scope this to api_football_id=1.
WC_LEAGUE_AF_ID = 1


def _load_upcoming_wc_fixtures(days: int, max_fixtures: int | None) -> list[dict]:
    """Upcoming WC fixtures in the blender's window."""
    limit_clause = f"LIMIT {int(max_fixtures)}" if max_fixtures else ""
    return execute_query(
        f"""
        SELECT m.id, m.date, m.api_football_id,
               m.home_team_id, m.away_team_id,
               th.name AS home_name, ta.name AS away_name
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams   th ON th.id = m.home_team_id
        JOIN teams   ta ON ta.id = m.away_team_id
        WHERE l.api_football_id = %s
          AND m.status = 'scheduled'
          AND m.date::date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL %s
        ORDER BY m.date ASC
        {limit_clause}
        """,
        [WC_LEAGUE_AF_ID, f"{int(days)} days"],
    )


def _load_own_probs(match_ids: list[str]) -> dict[str, dict[str, float]]:
    """
    Read latest `national_team_v1` 1X2 rows for the given matches.

    Returns: {match_id: {'home': p, 'draw': p, 'away': p}}.
    Matches without a complete (home+draw+away) triple are omitted —
    the blender can't operate on partial rows.
    """
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT match_id, market, model_probability
        FROM predictions
        WHERE source = %s
          AND match_id = ANY(%s::uuid[])
          AND market IN ('1x2_home', '1x2_draw', '1x2_away')
        """,
        [SOURCE_OWN, match_ids],
    )

    by_match: dict[str, dict[str, float]] = {}
    market_to_key = {"1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away"}
    for r in rows:
        mid = str(r["match_id"])
        key = market_to_key.get(r["market"])
        if key is None:
            continue
        by_match.setdefault(mid, {})[key] = float(r["model_probability"])

    return {mid: t for mid, t in by_match.items()
            if {"home", "draw", "away"} <= t.keys()}


def _load_market_consensus(match_ids: list[str]) -> dict[str, dict]:
    """
    Read wc_market_consensus rows for the given matches.

    Returns: {match_id: {'home', 'draw', 'away', 'n_sources'}} keyed by str.
    Absent fixtures (no consensus row yet) are simply not in the dict.
    Gracefully degrades when the table is empty (A3-FIX in flight).
    """
    if not match_ids:
        return {}
    try:
        rows = execute_query(
            """
            SELECT match_id, home_prob, draw_prob, away_prob, n_sources
            FROM wc_market_consensus
            WHERE match_id = ANY(%s::uuid[])
            """,
            [match_ids],
        )
    except Exception as e:
        # Table missing entirely (migration not applied yet) is a valid
        # state during the rollout — fall back to own-only.
        console.print(
            f"[yellow]wc_market_consensus not readable ({type(e).__name__}: {e}); "
            f"falling back to own-only blend output.[/yellow]"
        )
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        out[str(r["match_id"])] = {
            "home": float(r["home_prob"]),
            "draw": float(r["draw_prob"]),
            "away": float(r["away_prob"]),
            "n_sources": int(r["n_sources"]),
        }
    return out


def run_blended_predictions(
    days: int = 30,
    max_fixtures: int | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Callable entry point — scheduler imports this directly.

    Returns: {"fixtures": N, "with_market": N, "own_only": N, "rows": N,
              "missing_own": N}.
    """
    console.print("[bold cyan]═══ WC-A4 Write blended predictions ═══[/bold cyan]")
    console.print(f"  window: next {days} days; base λ={BLEND_LAMBDA}")
    if max_fixtures:
        console.print(f"  cap: max {max_fixtures} fixtures (dev mode)")

    fixtures = _load_upcoming_wc_fixtures(days=days, max_fixtures=max_fixtures)
    if not fixtures:
        console.print("[yellow]No upcoming WC fixtures in window — exiting.[/yellow]")
        return {"fixtures": 0, "with_market": 0, "own_only": 0, "rows": 0,
                "missing_own": 0}

    match_ids = [str(f["id"]) for f in fixtures]
    own_by_match = _load_own_probs(match_ids)
    mkt_by_match = _load_market_consensus(match_ids)

    console.print(
        f"  {len(fixtures)} fixtures · own preds for "
        f"{len(own_by_match)} · market consensus for {len(mkt_by_match)}"
    )

    pred_rows: list[dict] = []
    n_with_market = 0
    n_own_only = 0
    n_missing_own = 0
    sample_lines: list[str] = []

    for f in fixtures:
        mid = str(f["id"])
        own = own_by_match.get(mid)
        if own is None:
            n_missing_own += 1
            continue

        mkt = mkt_by_match.get(mid)
        if mkt is None:
            blended = blend_with_confidence(own, None, n_sources=0)
            n_own_only += 1
            sample_lines.append(
                f"  · {f['home_name']:<20} v {f['away_name']:<20} "
                f"NO MARKET — own only: H={own['home']:.3f} D={own['draw']:.3f} "
                f"A={own['away']:.3f}"
            )
        else:
            blended = blend_with_confidence(
                own,
                {"home": mkt["home"], "draw": mkt["draw"], "away": mkt["away"]},
                n_sources=mkt["n_sources"],
            )
            n_with_market += 1
            sample_lines.append(
                f"  · {f['home_name']:<20} v {f['away_name']:<20} "
                f"own=(H{own['home']:.2f}/D{own['draw']:.2f}/A{own['away']:.2f}) "
                f"mkt=(H{mkt['home']:.2f}/D{mkt['draw']:.2f}/A{mkt['away']:.2f}) "
                f"n={mkt['n_sources']} λ_used={blended['lambda_used']:.3f} → "
                f"H{blended['home']:.3f} D{blended['draw']:.3f} A{blended['away']:.3f}"
            )

        reasoning_payload = {
            "blended": bool(blended["blended"]),
            "lambda_used": float(blended["lambda_used"]),
            "lambda_base": float(BLEND_LAMBDA),
            "n_market_sources": int(mkt["n_sources"]) if mkt else 0,
            "own_prob": {
                "home": round(own["home"], 6),
                "draw": round(own["draw"], 6),
                "away": round(own["away"], 6),
            },
            "market_prob": (
                {
                    "home": round(mkt["home"], 6),
                    "draw": round(mkt["draw"], 6),
                    "away": round(mkt["away"], 6),
                }
                if mkt
                else None
            ),
        }
        reasoning_str = "wc_a4_blend " + json.dumps(reasoning_payload, separators=(",", ":"))

        # Confidence inherits own model's 0.6 when we actually blended;
        # 0.5 when we fell back to own-only (slightly lower to signal "no
        # market lift available").
        conf = 0.6 if mkt is not None else 0.5

        for mkt_key, prob in (
            ("1x2_home", blended["home"]),
            ("1x2_draw", blended["draw"]),
            ("1x2_away", blended["away"]),
        ):
            pred_rows.append({
                "match_id": mid,
                "market": mkt_key,
                "source": SOURCE_BLENDED,
                "model_prob": float(prob),
                "confidence": conf,
                "reasoning": reasoning_str,
                "model_version": MODEL_VERSION,
            })

    stats = {
        "fixtures": len(fixtures),
        "with_market": n_with_market,
        "own_only": n_own_only,
        "missing_own": n_missing_own,
        "rows": len(pred_rows),
    }

    console.print(
        f"  prepared {len(pred_rows)} prediction rows "
        f"({n_with_market} blended, {n_own_only} own-only, "
        f"{n_missing_own} skipped no-own-preds)"
    )

    # Sample 5 lines so dry-run is useful, but cap noise in cron logs.
    for line in sample_lines[: 5 if not dry_run else len(sample_lines)]:
        console.print(line)

    if dry_run:
        console.print("[yellow]Dry run — not writing.[/yellow]")
        return stats

    if not pred_rows:
        console.print("[yellow]Nothing to write.[/yellow]")
        return stats

    n_written = bulk_store_predictions(pred_rows)
    console.print(
        f"[green]✓ wrote {n_written} prediction rows to `predictions` "
        f"(source='{SOURCE_BLENDED}')[/green]"
    )
    stats["written"] = n_written
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=30,
                        help="Blend WC fixtures in the next N days (default 30)")
    parser.add_argument("--max-fixtures", type=int, default=None,
                        help="Cap fixtures processed (dev/debug)")
    args = parser.parse_args()
    run_blended_predictions(
        days=args.days,
        max_fixtures=args.max_fixtures,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
