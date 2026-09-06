#!/usr/bin/env python3
"""Propose additions to workers/automation/coolbet_league_mapping.json.

COOLBET-FUZZY-MATCH-FALSE-POSITIVES (2026-09-06)
------------------------------------------------
Coolbet ingest has two paths and only one of them is safe:

  run_league_sweep   resolves our AF league -> a Coolbet league via the mapping
                     and matches team names WITHIN that league. Precise.
  run_sweep          falls back to search_coolbet_event, which searches ALL of
                     Coolbet football, cross-league. This is the path that
                     matched Liga MX's "Atlas vs Queretaro" onto our Liga
                     Premier Serie A "Acatlan vs Guerreros" and stored those
                     prices against the wrong fixture on four sweeps.

The mapping covers 114 of the 583 leagues that had fixtures in the last 30
days, so 79.1% of fixtures take the cross-league path. The fix is to GROW the
mapping -- not to delete the fallback, because the fallback earns 63.8% of all
Coolbet coverage (Coolbet really does carry J1 League, Super Lig, Segunda
Division, Serie C; they are unmapped, not absent).

This script exists because the mapping was hand-built and therefore went stale.
It proposes, it does not write: every row lands in a CSV for review, because a
WRONG mapping is worse than a missing one -- a missing league falls back to
search as it does today, while a wrong league silently prices the wrong
competition.

Ranking is by matches that ACTUALLY RECEIVED Coolbet odds, not by fixture
count, so the leagues that would convert the most real traffic come first.

Usage:
    python3 scripts/coolbet_league_mapping.py --propose [--days 30] [--top 60]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from rapidfuzz import fuzz  # noqa: E402

from workers.api_clients.db import get_conn  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MAPPING_PATH = REPO / "workers" / "automation" / "coolbet_league_mapping.json"
CACHE_PATH = REPO / "workers" / "automation" / "coolbet_leagues_cache.json"
OUT_PATH = REPO / "dev" / "active" / "coolbet_league_mapping_proposals.csv"


def _norm(s: str) -> str:
    """Fold accents and case so 'Segunda Division' == 'Segunda División'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def unmapped_leagues_by_coolbet_yield(days: int) -> list[dict]:
    """AF leagues with no mapping, ranked by matches that already got Coolbet
    odds via the cross-league search path -- i.e. what mapping them would
    convert from guessing to within-league matching."""
    mapped = {e["db_league_id"] for e in json.loads(MAPPING_PATH.read_text())}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id::text, l.name, l.country,
                   COUNT(DISTINCT m.id) FILTER (WHERE o.bookmaker = 'Coolbet') AS cb_matches,
                   COUNT(DISTINCT m.id)                                        AS fixtures
            FROM matches m
            JOIN leagues l ON l.id = m.league_id
            LEFT JOIN odds_snapshots o ON o.match_id = m.id
            WHERE m.date >= now() - make_interval(days => %s)
            GROUP BY 1, 2, 3
            ORDER BY cb_matches DESC
            """,
            (days,),
        )
        rows = cur.fetchall()
    return [
        {"db_league_id": r[0], "db_league_name": r[1], "db_country": r[2],
         "cb_matches": r[3], "fixtures": r[4]}
        for r in rows
        if r[0] not in mapped
    ]


def load_coolbet_leagues() -> tuple[list[dict], bool]:
    """(leagues, is_live). Distinguishes a live fetch from the stale cache by
    comparing against the cache itself -- fetch_coolbet_leagues falls back
    silently, and a proposal built on a 2026-05-20 snapshot would be worthless."""
    cached = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else []
    cached_ids = {int(e["id"]) for e in cached if e.get("id")}
    try:
        from workers.automation.coolbet_placer import fetch_coolbet_leagues
        from workers.automation.coolbet_session import CoolbetSession

        leagues = fetch_coolbet_leagues(CoolbetSession(require_auth=False))
    except Exception as exc:  # network, Imperva, schema
        print(f"[warn] live league fetch raised {type(exc).__name__}: {exc}")
        return cached, False
    live = {int(e["id"]) for e in leagues if e.get("id")} != cached_ids
    return leagues, live


def propose(days: int, top: int) -> int:
    unmapped = unmapped_leagues_by_coolbet_yield(days)
    leagues, is_live = load_coolbet_leagues()

    if not is_live:
        import datetime as _dt
        dated = _dt.date.fromtimestamp(CACHE_PATH.stat().st_mtime).isoformat()
        print(
            f"[STALE] Coolbet returned the CACHED league list ({len(leagues)} "
            f"leagues, dated {dated}). Proposals built on this are not "
            "trustworthy -- the cache holds none of the high-volume leagues "
            "(J1 League, Super Lig, Segunda Division). Re-run when the live "
            "endpoint answers."
        )

    cb = [{"id": int(e["id"]), "name": e.get("name") or "",
           "fullSlug": e.get("fullSlug") or ""} for e in leagues if e.get("id")]

    rows = []
    for af in unmapped[:top]:
        target = _norm(af["db_league_name"])
        country = _norm(af["db_country"] or "")
        scored = []
        for c in cb:
            n = _norm(c["name"])
            slug = _norm(c["fullSlug"].replace("-", " ").replace("/", " "))
            name_score = fuzz.token_set_ratio(target, n)
            # Country must show up in the Coolbet name or slug; Coolbet
            # namespaces most leagues as "<Country> <Competition>".
            country_ok = bool(country) and (country in n or country in slug)
            scored.append((name_score + (15 if country_ok else 0), name_score,
                           country_ok, c))
        scored.sort(key=lambda t: -t[0])
        best = scored[0] if scored else None
        rows.append({
            "db_league_id":   af["db_league_id"],
            "db_league_name": af["db_league_name"],
            "db_country":     af["db_country"],
            "cb_matches_30d": af["cb_matches"],
            "fixtures_30d":   af["fixtures"],
            "cb_league_id":   best[3]["id"] if best else "",
            "cb_league_name": best[3]["name"] if best else "",
            "cb_full_slug":   best[3]["fullSlug"] if best else "",
            "name_score":     best[1] if best else 0,
            "country_ok":     best[2] if best else False,
            "confidence":     ("high" if best and best[1] >= 88 and best[2]
                               else "medium" if best and best[1] >= 75
                               else "low"),
            "decision":       "",  # human writes accept/reject here
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["db_league_id"])
        w.writeheader()
        w.writerows(rows)

    high = sum(1 for r in rows if r["confidence"] == "high")
    conv = sum(r["cb_matches_30d"] for r in rows if r["confidence"] == "high")
    print(f"{len(unmapped)} unmapped leagues; wrote {len(rows)} proposals -> {OUT_PATH}")
    print(f"  high-confidence: {high}  (would convert {conv} matches/30d "
          f"from cross-league search to within-league matching)")
    print("  Review the CSV and fill `decision` before anything is merged.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=60)
    a = ap.parse_args()
    if not a.propose:
        ap.error("nothing to do: pass --propose")
    return propose(a.days, a.top)


if __name__ == "__main__":
    raise SystemExit(main())
