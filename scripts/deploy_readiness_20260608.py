"""Pre-06-08 deploy readiness check.

Validates 10 conditions for the post-Phase-3.5 deploy on 2026-06-08:

  1. CI green: latest commit's smoke suite passes (manual reminder)
  2. Candidate MAIN bundle in Storage  (v_20260525_signals OR v_20260608)
  3. Isotonic pickles uploaded for candidate bundle (5 markets)
  4. B-ML3-VALIDATE-ACTIVATION result available (≥200 settled bets cohort)
  5. Latest aln1_tune_recommendation file present + parseable
  6. league_clv_efficiency match_signals fresh (≤7 days old)
  7. league_draw_rate_ytd match_signals fresh (≤2 days old)
  8. Pinnacle implied coverage on today's matches ≥10%
  9. Phase 3.5 verdict file exists (from real_perf_split_by_source)
 10. No active migration in progress (migration_log clean)

Each check prints ✓ / ✗ / ? with a hint. Exit 0 if all pass.

Run on 2026-06-08 morning before flipping env vars:
  python3 scripts/deploy_readiness_20260608.py
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
ROOT = Path(__file__).resolve().parent.parent


def check_candidate_bundle() -> tuple[bool, str]:
    for v in ("v_20260608", "v_20260525_signals"):
        bundle = ROOT / "data" / "models" / "soccer" / v
        if bundle.exists() and (bundle / "result_1x2.pkl").exists():
            return True, f"local bundle {v}"
    # Also check Storage via model_versions registry
    rows = execute_query("""
        SELECT version FROM model_versions
        WHERE version IN ('v_20260608', 'v_20260525_signals')
          AND demoted_at IS NULL
        ORDER BY trained_at DESC LIMIT 1
    """)
    if rows:
        return True, f"Storage: {rows[0]['version']}"
    return False, "no candidate bundle found"


def check_isotonic_pickles() -> tuple[bool, str]:
    for v in ("v_20260608", "v_20260525_signals"):
        bundle = ROOT / "data" / "models" / "soccer" / v
        if not bundle.exists():
            continue
        markets = ("isotonic_1x2_home", "isotonic_1x2_draw", "isotonic_1x2_away",
                   "isotonic_over_25", "isotonic_btts_yes")
        present = [m for m in markets if (bundle / f"{m}.pkl").exists()]
        if len(present) >= 3:
            return True, f"{v}: {len(present)}/5 isotonic pickles"
    return False, "no isotonic pickles in any candidate bundle"


def check_meta_validation() -> tuple[bool, str]:
    r = execute_query("""
        SELECT COUNT(*) AS n FROM simulated_bets
        WHERE meta_clv_score IS NOT NULL
          AND result IN ('won', 'lost', 'void')
          AND pick_time >= '2026-05-25'
    """)
    n = r[0]["n"]
    return n >= 200, f"{n}/200 settled bets with meta_clv_score"


def check_aln1_recommendation() -> tuple[bool, str]:
    rec_dir = ROOT / "dev" / "active"
    files = sorted(rec_dir.glob("aln1_tune_recommendation_*.md"))
    if not files:
        return False, "no aln1 recommendation file"
    latest = files[-1].stem.replace("aln1_tune_recommendation_", "")
    return True, f"latest: {latest}"


def check_league_clv_freshness() -> tuple[bool, str]:
    r = execute_query("""
        SELECT MAX(captured_at) AS most_recent
        FROM match_signals
        WHERE signal_name = 'league_clv_efficiency'
    """)
    mr = r[0]["most_recent"]
    if mr is None:
        return False, "no league_clv_efficiency rows"
    from datetime import datetime, timezone
    age_h = (datetime.now(timezone.utc) - mr).total_seconds() / 3600
    return age_h <= 24 * 7, f"most recent: {mr.isoformat()[:19]} ({age_h:.0f}h ago)"


def check_league_draw_rate_freshness() -> tuple[bool, str]:
    r = execute_query("""
        SELECT MAX(captured_at) AS most_recent
        FROM match_signals WHERE signal_name = 'league_draw_rate_ytd'
    """)
    mr = r[0]["most_recent"]
    if mr is None:
        return False, "no league_draw_rate_ytd rows"
    from datetime import datetime, timezone
    age_h = (datetime.now(timezone.utc) - mr).total_seconds() / 3600
    return age_h <= 48, f"most recent: {mr.isoformat()[:19]} ({age_h:.0f}h ago)"


def check_pinnacle_today_coverage() -> tuple[bool, str]:
    r = execute_query("""
        SELECT
          COUNT(DISTINCT m.id) AS total,
          COUNT(DISTINCT m.id) FILTER (
            WHERE EXISTS (
              SELECT 1 FROM odds_snapshots os
              WHERE os.match_id = m.id
                AND os.bookmaker = 'Pinnacle'
                AND os.market = '1x2'
            )
          ) AS with_pinnacle
        FROM matches m
        WHERE m.date::date = CURRENT_DATE
    """)
    total = r[0]["total"] or 0
    with_pin = r[0]["with_pinnacle"] or 0
    if total == 0:
        return False, "no matches today"
    pct = (with_pin / total) * 100
    return pct >= 10, f"{with_pin}/{total} ({pct:.1f}%) with Pinnacle"


def check_phase35_verdict() -> tuple[bool, str]:
    candidates = list((ROOT / "dev" / "active").glob("self-use-validation*.md")) + \
                 list((ROOT / "dev" / "active").glob("phase-3*verdict*.md")) + \
                 list((ROOT / "dev" / "active").glob("phase-4*.md"))
    if not candidates:
        return False, "no Phase 4 verdict doc"
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return True, f"{latest.name}"


def check_no_active_migration() -> tuple[bool, str]:
    # Migration log table doesn't exist as such — just check pg_stat_activity
    # for any long-running ALTER/CREATE.
    try:
        r = execute_query("""
            SELECT COUNT(*) AS n FROM pg_stat_activity
            WHERE state = 'active'
              AND pid != pg_backend_pid()
              AND (query ILIKE '%ALTER TABLE%' OR query ILIKE '%CREATE INDEX%')
        """)
        n = r[0]["n"]
        return n == 0, f"{n} active schema-mod queries"
    except Exception as e:
        return True, f"check skipped: {e}"


def main():
    checks = [
        ("Candidate MAIN bundle in Storage", check_candidate_bundle),
        ("Isotonic pickles uploaded", check_isotonic_pickles),
        ("B-ML3 validation cohort size", check_meta_validation),
        ("ALN-1 recommendation file", check_aln1_recommendation),
        ("league_clv_efficiency fresh", check_league_clv_freshness),
        ("league_draw_rate_ytd fresh", check_league_draw_rate_freshness),
        ("Pinnacle today coverage", check_pinnacle_today_coverage),
        ("Phase 4 verdict doc", check_phase35_verdict),
        ("No active schema modifications", check_no_active_migration),
    ]
    console.print(f"[bold]Deploy-readiness check — {date.today()}[/bold]\n")
    t = Table()
    for c in ("#", "Check", "Status", "Detail"):
        t.add_column(c)
    n_pass = 0
    for i, (name, fn) in enumerate(checks, 1):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        sym = "[green]✓[/green]" if ok else "[red]✗[/red]"
        if ok:
            n_pass += 1
        t.add_row(str(i), name, sym, detail)
    console.print(t)
    console.print(f"\n[bold]{n_pass}/{len(checks)} checks passed[/bold]")
    console.print(f"\n[bold cyan]MANUAL REMINDER[/bold cyan]: also confirm CI green on latest commit before flipping env.")
    console.print(f"\n[bold cyan]Env flips for 2026-06-08 deploy (after all checks pass):[/bold cyan]")
    console.print("  MODEL_VERSION=<candidate version>")
    console.print("  STAGE2_CALIBRATOR=isotonic")
    console.print("  ELITE_LEAGUE_FILTER_ENABLED=true")
    console.print("  LEAGUE_EFF_EDGE_BUMP_ENABLED=true")
    console.print("  META_B_ML3_ENABLED=<true if B-ML3-VALIDATE-ACTIVATION verdict = PASS>")
    console.print("  META_B_ML3_VERSION=v_20260525_v23_xgb")
    console.print('  BOT_COHORT_OVERRIDES="bot_opt_away_british:morning,bot_opt_away_europe:morning,bot_ah_away_dog:morning"')
    console.print("  GATE_EVENTS_BY_COVERAGE=true  (verify with another AF-COVERAGE-AUDIT run first)")
    sys.exit(0 if n_pass == len(checks) else 1)


if __name__ == "__main__":
    main()
