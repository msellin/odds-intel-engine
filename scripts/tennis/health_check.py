#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS health check — one-shot diagnostic.

Verifies every layer of the tennis pipeline can be reached + reports the
last-known state. Designed to be runnable both locally and on the VPS when
something looks off. Output is a single human-readable report; exit code is
0 if everything's healthy, 1 if any check fails.

Usage:
    python3 scripts/tennis/health_check.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parents[2] / ".env")

from workers.api_clients.db import execute_query  # noqa: E402


_ok = 0
_fail = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _ok, _fail
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    if ok:
        _ok += 1
    else:
        _fail += 1


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    print("=" * 70)
    print(f"TENNIS HEALTH CHECK   {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 70)

    # ── 1. Env vars ──────────────────────────────────────────────────────
    section("1. Environment")
    oa = os.environ.get("OA_KEY") or os.environ.get("ODDS_API_KEY", "")
    check("OA_KEY / ODDS_API_KEY set", bool(oa),
          f"prefix={oa[:8]}…" if oa else "MISSING — scanner / settlement / closing-odds all bail silently")

    # ── 2. Schema ────────────────────────────────────────────────────────
    section("2. tennis_value_bets schema")
    cols = execute_query("""
        SELECT column_name, is_nullable
          FROM information_schema.columns
         WHERE table_name='tennis_value_bets'
           AND column_name IN ('fair_source','pin_fair_odds','edge_pct','bot_id','closing_odds','clv')
         ORDER BY column_name
    """)
    col_map = {c["column_name"]: c["is_nullable"] for c in cols}
    check("bot_id column exists (Phase 2)", "bot_id" in col_map)
    check("fair_source column exists (Phase 4)", "fair_source" in col_map,
          "" if "fair_source" in col_map else "migration 264 not applied")
    check("pin_fair_odds is NULLable (Phase 4)", col_map.get("pin_fair_odds") == "YES")
    check("edge_pct is NULLable (Phase 4)", col_map.get("edge_pct") == "YES")
    check("closing_odds column exists", "closing_odds" in col_map)
    check("clv column exists", "clv" in col_map)

    # ── 3. The Odds API connectivity ─────────────────────────────────────
    section("3. The Odds API connectivity")
    if not oa:
        check("/sports probe", False, "skipped — no key")
    else:
        try:
            r = requests.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": oa, "all": "true"},
                timeout=15,
            )
            remaining = r.headers.get("x-requests-remaining", "?")
            if r.status_code == 200:
                body = r.json()
                tennis = [s for s in body if isinstance(s, dict)
                          and "tennis" in (s.get("key") or "").lower()
                          and s.get("active")]
                check("/sports returns 200", True, f"credits remaining: {remaining}")
                check("at least one active tennis sport", len(tennis) > 0,
                      f"{len(tennis)} active — {[s.get('key') for s in tennis[:3]]}")
            else:
                check("/sports returns 200", False, f"HTTP {r.status_code} — {r.text[:100]}")
        except requests.RequestException as e:
            check("/sports reachable", False, str(e))

    # ── 4. Scheduler state — pipeline_runs ───────────────────────────────
    section("4. Pipeline runs (last 24h)")
    now = datetime.now(timezone.utc)

    for job in ("tennis_scanner", "tennis_settlement", "tennis_closing_odds",
                "coolbet_tennis_scanner"):
        rows = execute_query(
            """
            SELECT status, started_at, error_message
              FROM pipeline_runs
             WHERE job_name = %s
               AND started_at > now() - interval '24 hours'
             ORDER BY started_at DESC LIMIT 1
            """,
            (job,),
        )
        if not rows:
            check(f"{job}: any run in 24h", False,
                  "NO RUNS — scheduler not firing this cron, or job exits before _run_job")
            continue
        r = rows[0]
        age_min = int((now - r["started_at"]).total_seconds() / 60)
        is_ok = r["status"] == "completed"
        detail = f"{age_min} min ago, status={r['status']}"
        if r["error_message"]:
            detail += f"  err: {r['error_message'][:80]}"
        check(f"{job}: last run", is_ok, detail)

    # ── 5. Data volume — last 24h ────────────────────────────────────────
    section("5. tennis_value_bets — last 24h")
    has_fair_source = "fair_source" in col_map

    if has_fair_source:
        rows = execute_query("""
            SELECT COALESCE(fair_source, 'unknown') AS source,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE result IS NOT NULL) AS settled,
                   COUNT(*) FILTER (WHERE result IS NULL) AS unsettled
              FROM tennis_value_bets
             WHERE logged_at > now() - interval '24 hours'
             GROUP BY 1 ORDER BY total DESC
        """)
    else:
        rows = execute_query("""
            SELECT 'all' AS source,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE result IS NOT NULL) AS settled,
                   COUNT(*) FILTER (WHERE result IS NULL) AS unsettled
              FROM tennis_value_bets
             WHERE logged_at > now() - interval '24 hours'
        """)

    if not rows or all(r["total"] == 0 for r in rows):
        check("any rows logged in last 24h", False,
              "ZERO rows — scanner not writing")
    else:
        for r in rows:
            n = r["total"]
            print(f"      {r['source']:25s}  total={n:5d}  settled={r['settled']:4d}  "
                  f"unsettled={r['unsettled']:4d}")
        any_pin = any(r["source"] == "odds_api_pinnacle" and r["total"] > 0 for r in rows) \
                  if has_fair_source else False
        any_cb = any(r["source"] == "coolbet_only" and r["total"] > 0 for r in rows) \
                 if has_fair_source else False
        if has_fair_source:
            check("odds_api_pinnacle rows landing", any_pin,
                  "no rows from tour-mains scanner")
            check("coolbet_only rows landing (Phase 4 unlock)", any_cb,
                  "Phase 4 scanner refactor not active yet OR Coolbet returned no matches")

    # ── 6. Stale settlement ──────────────────────────────────────────────
    section("6. Stale settlement (past KO+6h, result NULL)")
    rows = execute_query("""
        SELECT COUNT(*) AS cnt
          FROM tennis_value_bets
         WHERE result IS NULL
           AND kickoff_time < now() - interval '6 hours'
    """)
    stale = rows[0]["cnt"] if rows else 0
    check("≤ 5 stale rows", stale <= 5,
          f"{stale} unsettled past KO+6h — settlement source isn't catching up")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {_ok} passed, {_fail} failed")
    print("=" * 70)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
