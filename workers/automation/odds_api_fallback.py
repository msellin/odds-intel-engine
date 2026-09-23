"""LICENSED COOLBET FALLBACK via The Odds API (#110 step 4, 2026-09-23).

WHY. When our own Coolbet sweep is paused (#108: the exit IP was flagged and the
book was dark for hours), picks and placement lose their main executable price.
The Odds API (the-odds-api.com) carries Coolbet (`coolbet`, region `eu`) under a
commercial licence — a paid, sanctioned source rather than a way round a block.

WHAT IT IS NOT.
  * Not a replacement for the own sweep: it runs ONLY while `coolbet_prematch` is
    paused (or with --force), because every call spends paid credits and the own
    sweep has far more markets.
  * Not stored as "Coolbet". Rows are written as bookmaker `Coolbet-OddsAPI`, which
    is NOT in ACCESSIBLE_BOOKMAKERS — so it cannot silently price a pick or a stake.
    Promoting it (e.g. after comparing its prices with our own Coolbet rows for the
    same fixtures) is the owner's decision.

COST. One call per soccer competition per sweep, costing (markets × regions) =
2 credits (h2h + totals, eu). The sport list (`/v4/sports`) is free. With ~40
active soccer competitions a sweep is ~80 credits.

Needs env ODDS_API_KEY. Not registered in the scheduler until the owner provides a
key and decides the cadence.

    python3 -m workers.automation.odds_api_fallback --dry-run --max-sports 3
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

API = "https://api.the-odds-api.com/v4"
BOOK_KEY = "coolbet"
LABEL = "Coolbet-OddsAPI"
MARKETS = "h2h,totals"


def _key() -> str:
    k = os.getenv("ODDS_API_KEY")
    if not k:
        raise SystemExit("ODDS_API_KEY is not set — the licensed fallback needs the owner's key")
    return k


def own_sweep_paused() -> bool:
    """The fallback's trigger: our own Coolbet sweep is paused (by us or auto)."""
    from workers.api_clients.db import execute_query
    rows = execute_query("SELECT paused FROM feed_controls WHERE feed_id = 'coolbet_prematch'") or []
    return bool(rows and rows[0]["paused"])


def soccer_sports(key: str) -> list[str]:
    r = requests.get(f"{API}/sports", params={"apiKey": key}, timeout=20)
    r.raise_for_status()
    return [s["key"] for s in r.json() if s.get("group") == "Soccer" and s.get("active")
            and not s.get("has_outrights")]


def parse_event(ev: dict) -> list[tuple[str, str, float, float | None]]:
    """The Odds API event → (market, selection, odds, line) rows, Coolbet only."""
    rows: list[tuple[str, str, float, float | None]] = []
    home, away = ev.get("home_team"), ev.get("away_team")
    for bm in ev.get("bookmakers") or []:
        if bm.get("key") != BOOK_KEY:
            continue
        for m in bm.get("markets") or []:
            if m.get("key") == "h2h":
                sel_of = {home: "home", away: "away", "Draw": "draw"}
                for o in m.get("outcomes") or []:
                    sel = sel_of.get(o.get("name"))
                    if sel and o.get("price"):
                        rows.append(("1x2", sel, float(o["price"]), None))
            elif m.get("key") == "totals":
                for o in m.get("outcomes") or []:
                    line, name = o.get("point"), str(o.get("name") or "").lower()
                    if line is None or name not in ("over", "under") or not o.get("price"):
                        continue
                    if abs(line * 2 - round(line * 2)) > 1e-9:   # no quarter lines
                        continue
                    tag = "over_under_" + f"{line:.1f}".replace(".", "").zfill(2)
                    rows.append((tag, name, float(o["price"]), float(line)))
    return rows


def run(*, dry_run: bool = False, force: bool = False, horizon_hours: float = 48,
        max_sports: int | None = None) -> dict:
    c = {"sports": 0, "events": 0, "matched": 0, "stored": 0, "credits_remaining": None}
    if not force and not own_sweep_paused():
        c["skipped"] = "own Coolbet sweep is running — fallback not needed"
        return c
    key = _key()
    from workers.automation.coolbet_explorer import _load_af_candidates
    from workers.automation.coolbet_matching import match_event_to_af
    af = _load_af_candidates(horizon_hours)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)
    sports = soccer_sports(key)[:max_sports] if max_sports else soccer_sports(key)
    for sport in sports:
        r = requests.get(f"{API}/sports/{sport}/odds", timeout=30, params={
            "apiKey": key, "regions": "eu", "bookmakers": BOOK_KEY,
            "markets": MARKETS, "oddsFormat": "decimal"})
        c["credits_remaining"] = r.headers.get("x-requests-remaining")
        if r.status_code != 200:
            log.warning("odds-api %s: HTTP %s %s", sport, r.status_code, r.text[:200])
            continue
        c["sports"] += 1
        for ev in r.json():
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if not (now < start < horizon):
                continue
            rows = parse_event(ev)
            if not rows:
                continue
            c["events"] += 1
            row, _score, _ = match_event_to_af(ev["home_team"], ev["away_team"], None, start, af)
            if row is None:
                continue
            c["matched"] += 1
            if not dry_run:
                from workers.api_clients.supabase_client import store_book_odds_snapshots
                c["stored"] += store_book_odds_snapshots(
                    LABEL, row["id"], rows,
                    minutes_to_kickoff=int((start - now).total_seconds() // 60))
    log.info("odds-api fallback: %s", c)
    return c


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even while the own sweep is running")
    ap.add_argument("--horizon-hours", type=float, default=48)
    ap.add_argument("--max-sports", type=int)
    a = ap.parse_args()
    print(run(dry_run=a.dry_run, force=a.force, horizon_hours=a.horizon_hours, max_sports=a.max_sports))
