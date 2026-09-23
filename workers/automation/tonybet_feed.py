"""TONYBET-SWEEPER (#101, 2026-09-23) — pre-match odds + fair probabilities from
Tonybet (Estonian-licensed, Osaühing Tonybet). 20bet runs on the same platform
with identical prices (203/203 compared), so this one sweeper covers both.

WHY TONYBET FIRST. A measured 48 h comparison (docs/EE_BOOK_SURVEY_2026_09_23.md
§"Coverage vs Epicbet") had Tonybet pricing 156 of our fixtures against Epicbet's
139, with 46 that no book we sweep prices at all — for the cheapest sweep of any
book (~6 requests). Design input: dev/active/tonybet-af-capability-map.md.

TRANSPORT. Plain anonymous REST (`platform.tonybet.com/api/event/list`). It answers
the Hetzner IP directly, but we still route through the zone.ee Estonian exit:
Tonybet serves several jurisdictions, and the Estonian line is the one the owner
can bet. `TONYBET_PROXY`, else `EPICBET_RESIDENTIAL_PROXY` (same exit on the VPS),
else direct (the Mac is already Estonian).

WHAT IS STORED
- `odds_snapshots` (bookmaker 'Tonybet'), in the shared vocabulary, keyed on the
  Sportradar UOF `vendorMarketId`, never Tonybet's internal market id:
    1  → 1x2            home/draw/away        (outcomes 1/2/3)
    18 → over_under_XX  over/under            (12/13; .5 lines 0.5–4.5 only, as
                                               every other book writes)
    16 → asian_handicap home/away, line = `hcp` (1714/1715; `hcp` is HOME-
                                               perspective — our convention)
    29 → btts           yes/no                (74/76)
    10 → double_chance  1x/12/x2              (9/10/11)
    11 → draw_no_bet    home/away             (4/5)
- `book_fair_probs` (migration 383): the supplier's margin-free probability that
  Tonybet ships on every outcome. Latest value per key, so the last pre-kickoff
  value is the fair close.

PLACEABLE since 2026-09-23: in ACCESSIBLE_BOOKMAKERS after the site-price check
(14/15 prices identical to tonybet.com/ee). Still EXCLUDED from the pre-registered
forward test's book set (scripts/publish_picks_forward_test.py) — widening that is
the owner's call, versioned as a rule change.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

BOOKMAKER = "Tonybet"
_API = "https://platform.tonybet.com/api/event/list"
_PAGE = 100            # the API's ceiling; limit=250 silently breaks paging
_MAX_PAGES = 30        # ~600 events per 48 h today — a hard stop, not a target
_SLEEP_S = 0.7
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_PROXY = os.getenv("TONYBET_PROXY") or os.getenv("EPICBET_RESIDENTIAL_PROXY") or None

# RAW ARCHIVE (owner, 2026-09-23: "parse everything we can and even more than AF —
# start creating our own data sources"). Every response is kept, gzipped, exactly
# as received: we normalise six market families today, but the payload also holds
# ~180 markets per match, Sportradar fair probabilities, Sportradar match ids,
# squads and coverage metadata. Anything we learn to parse later can be rebuilt
# back to the first day of collection instead of starting from the day we wrote the
# parser. ~6 pages per sweep, ~60 MB/day compressed. Set TONYBET_RAW_DIR="" to
# disable; a write failure never fails the sweep.
_RAW_DIR = os.getenv("TONYBET_RAW_DIR", "/opt/oddsintel/raw/tonybet")


def _archive(kind: str, page: int, content: bytes) -> None:
    if not _RAW_DIR:
        return
    try:
        import gzip
        from pathlib import Path
        now = datetime.now(timezone.utc)
        d = Path(_RAW_DIR) / now.strftime("%Y/%m/%d")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{now.strftime('%H%M%S')}_{kind}_p{page:02d}.json.gz").write_bytes(
            gzip.compress(content, compresslevel=6))
    except Exception as e:  # noqa: BLE001 — archiving must never cost the sweep
        log.debug("tonybet raw archive failed: %s", e)

_1X2 = {"1": "home", "2": "draw", "3": "away"}
_OU = {"12": "over", "13": "under"}
_AH = {"1714": "home", "1715": "away"}
_BTTS = {"74": "yes", "76": "no"}
_DC = {"9": "1x", "10": "12", "11": "x2"}
_DNB = {"4": "home", "5": "away"}


def _session() -> requests.Session:
    s = requests.Session()
    if _PROXY:
        s.proxies = {"http": _PROXY, "https": _PROXY}
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return s


def _team_name(c: dict) -> str | None:
    """Our matcher's squad guard reads the NAME, and Tonybet names women's teams
    without any marker ("Olympique Lyon", gender=2). Append the qualifier AF uses
    so a women's fixture can never pair with the men's. "YOUTH" has no tag the
    guard knows, so those teams are refused (None) rather than risk matching a
    first team."""
    name = (c.get("name") or "").strip()
    if not name:
        return None
    age = (c.get("ageGroup") or "").strip().upper()
    if age == "YOUTH":
        return None
    if age.startswith("U") and age[1:].isdigit() and age.lower() not in name.lower():
        name = f"{name} {age}"
    if c.get("gender") == 2 and not name.endswith(" W"):
        name = f"{name} W"
    return name


def fetch_events(sess: requests.Session, hours: int = 48) -> list[dict]:
    """Every pre-match football event kicking off within `hours`, main board."""
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%d %H:%M:%S"
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        params = [("lang", "en"), ("period", "0"), ("sportId_eq", "1"),
                  ("status_in[]", "0"), ("limit", str(_PAGE)), ("page", str(page)),
                  ("time_gte", now.strftime(fmt)),
                  ("time_lte", (now + timedelta(hours=hours)).strftime(fmt)),
                  ("relations[]", "odds"), ("relations[]", "competitors")]
        r = sess.get(_API, params=params, timeout=45)
        r.raise_for_status()
        _archive("prematch", page, r.content)
        body = r.json()
        if body.get("status") != "ok":
            raise RuntimeError(f"Tonybet event/list returned {body.get('status')}: "
                               f"{str(body)[:200]}")
        d = body["data"]
        rel = d.get("relations") or {}
        comps = {c["id"]: c for c in (rel.get("competitors") or []) if isinstance(c, dict)}
        odds = rel.get("odds") or {}
        for it in d.get("items") or []:
            h, a = comps.get(it.get("competitor1Id")), comps.get(it.get("competitor2Id"))
            if not h or not a:
                continue
            start = datetime.strptime(it["time"], fmt).replace(tzinfo=timezone.utc)
            out.append({
                "id": it["id"], "sr_id": it.get("vendorEventId"),
                "home": _team_name(h), "away": _team_name(a),
                "start": start.isoformat(),
                "markets": odds.get(str(it["id"])) or [],
            })
        if page >= int(d.get("lastPage") or 1):
            break
        time.sleep(_SLEEP_S)
    return out


def _line(spec: str | None, key: str) -> float | None:
    """`total=2.5` / `hcp=-0.75` → float. Anything else (e.g. European `hcp=0:1`)
    → None, so it can never masquerade as an Asian line."""
    for part in (spec or "").split("|"):
        k, _, v = part.partition("=")
        if k == key:
            try:
                return float(v)
            except ValueError:
                return None
    return None


def _ah_useful(m: dict) -> bool:
    """Tonybet quotes ~20 Asian lines per match, down to -2.0 @ 9.00 / 1.04. Keep a
    line only when BOTH sides price 1.25–4.0 — the band anyone bets or analyses.
    The rest would roughly double this book's odds_snapshots volume for nothing."""
    prices = [float(o.get("odds") or 0) for o in m.get("outcomes") or []]
    return len(prices) == 2 and all(1.25 <= p <= 4.0 for p in prices)


def parse_markets(markets: list[dict]) -> list[tuple]:
    """(market, selection, odds, handicap_line, fair_prob) for one event.
    Suspended markets (status != 1) and inactive outcomes are skipped."""
    from workers.automation.coolbet_explorer import _ou_market_for_line
    rows: list[tuple] = []

    def add(market, sel, o, line=None):
        odds = o.get("odds")
        if o.get("active") != 1 or not odds or float(odds) <= 1.0:
            return
        p = o.get("probabilities")
        rows.append((market, sel, float(odds), line,
                     float(p) if p is not None and 0 < float(p) < 1 else None))

    for m in markets:
        if m.get("status") != 1:
            continue
        vm = str(m.get("vendorMarketId"))
        spec = m.get("specifiers")
        for o in m.get("outcomes") or []:
            oid = str(o.get("vendorOutcomeId"))
            if vm == "1" and oid in _1X2:
                add("1x2", _1X2[oid], o)
            elif vm == "18" and oid in _OU:
                line = _line(spec, "total")
                tag = _ou_market_for_line(line) if line is not None else None
                if tag:
                    add(tag, _OU[oid], o, line)
            elif vm == "16" and oid in _AH:
                line = _line(spec, "hcp")
                if line is not None and _ah_useful(m):
                    # hcp is the HOME handicap; both sides share that key.
                    add("asian_handicap", _AH[oid], o, line)
            elif vm == "29" and oid in _BTTS:
                add("btts", _BTTS[oid], o)
            elif vm == "10" and oid in _DC:
                add("double_chance", _DC[oid], o)
            elif vm == "11" and oid in _DNB:
                add("draw_no_bet", _DNB[oid], o)
    return rows


def _flipped(our_home: str, our_away: str, ev: dict) -> bool:
    """`fuzzy_match_event` accepts either orientation. A flipped pairing would
    store Tonybet's away price under our home selection, so detect it and skip."""
    straight = fuzz.partial_ratio(our_home.lower(), ev["home"].lower()) + \
        fuzz.partial_ratio(our_away.lower(), ev["away"].lower())
    swapped = fuzz.partial_ratio(our_home.lower(), ev["away"].lower()) + \
        fuzz.partial_ratio(our_away.lower(), ev["home"].lower())
    return swapped > straight


def store_fair_probs(match_id: str, rows: list[tuple], minutes: int | None) -> int:
    """Upsert the latest fair probability per key (migration 383)."""
    from workers.api_clients.db import get_conn
    payload = [(match_id, BOOKMAKER, mk, sel, line, p, odds, minutes)
               for mk, sel, odds, line, p in rows if p is not None]
    if not payload:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO book_fair_probs
                     (match_id, bookmaker, market, selection, handicap_line, fair_prob,
                      odds, minutes_to_kickoff, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (match_id, bookmaker, market, selection,
                                COALESCE(handicap_line, -9999))
                   DO UPDATE SET fair_prob = EXCLUDED.fair_prob, odds = EXCLUDED.odds,
                                 minutes_to_kickoff = EXCLUDED.minutes_to_kickoff,
                                 updated_at = now()""",
                payload)
        conn.commit()
    return len(payload)


def run_bulk(hours: int = 48, dry_run: bool = False) -> dict:
    """One sweep: fetch → match to DB fixtures → store odds + fair probs + pairings.
    Returns counters. Raises if Tonybet itself could not be read, so the scheduler
    records a failed run instead of a green no-op."""
    from workers.api_clients.supabase_client import (
        record_book_events, store_book_odds_snapshots)
    from workers.automation.coolbet_explorer import load_matches_in_window
    from workers.automation.coolbet_placer import fuzzy_match_event
    from workers.automation.epicbet_explorer import (
        _minutes_to_kickoff, _squads_compatible, drop_non_monotone_ft_ou)

    c = {"events": 0, "db_matches": 0, "matched": 0, "flipped_skipped": 0,
         "rows": 0, "stored": 0, "fair_probs": 0, "ou_dropped": 0}
    sess = _session()
    events = [e for e in fetch_events(sess, hours) if e["home"] and e["away"]]
    c["events"] = len(events)
    matches = load_matches_in_window(max(1, (hours + 23) // 24) + 1)
    horizon = datetime.now(timezone.utc) + timedelta(hours=hours)
    matches = [m for m in matches if m.get("date") and
               (m["date"] if m["date"].tzinfo else m["date"].replace(tzinfo=timezone.utc)) <= horizon]
    c["db_matches"] = len(matches)

    pairs = []
    for m in matches:
        cands = [e for e in events if _squads_compatible(m["home"], m["away"], e)]
        if not cands:
            continue
        ev = fuzzy_match_event(m["home"], m["away"], cands, m.get("date"),
                               match_id=m.get("id"))
        if ev is None:
            continue
        if _flipped(m["home"], m["away"], ev):
            c["flipped_skipped"] += 1
            continue
        pairs.append((m, ev))
    c["matched"] = len(pairs)

    if pairs and not dry_run:
        record_book_events(BOOKMAKER, [(m["id"], str(ev["id"]), ev["start"], None)
                                       for m, ev in pairs])

    for m, ev in pairs:
        rows5 = parse_markets(ev["markets"])
        rows4 = [(mk, sel, odds, line) for mk, sel, odds, line, _p in rows5]
        rows4, dropped = drop_non_monotone_ft_ou(rows4, m["id"])
        c["ou_dropped"] += dropped
        kept = set((mk, sel, line) for mk, sel, _o, line in rows4)
        rows5 = [r for r in rows5 if (r[0], r[1], r[3]) in kept]
        c["rows"] += len(rows4)
        if dry_run or not rows4:
            continue
        mins = _minutes_to_kickoff(ev["start"])
        try:
            c["stored"] += store_book_odds_snapshots(BOOKMAKER, m["id"], rows4, mins)
            c["fair_probs"] += store_fair_probs(m["id"], rows5, mins)
        except Exception as e:  # noqa: BLE001 — one fixture must not kill the sweep
            log.warning("tonybet store failed for %s: %s", m["id"], e)
    log.info("tonybet sweep: %s", c)
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description="Tonybet pre-match odds sweep")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(run_bulk(a.hours, a.dry_run))


if __name__ == "__main__":
    main()
