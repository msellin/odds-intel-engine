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
    18 → over_under_XX  over/under            (12/13; EVERY line since #130 —
                                               .5, whole and quarter, spelled
                                               over_under_25 / _20 / _225)
    16 → asian_handicap home/away, line = `hcp` (1714/1715; `hcp` is HOME-
                                               perspective — our convention)
    29 → btts           yes/no                (74/76)
    10 → double_chance  1x/12/x2              (9/10/11)
    11 → draw_no_bet    home/away             (4/5)
- `book_fair_probs` (migration 383): the supplier's margin-free probability that
  Tonybet ships on every outcome. Latest value per key, so the last pre-kickoff
  value is the fair close.

PLACEABLE since 2026-09-23: in ACCESSIBLE_BOOKMAKERS after the site-price check
(14/15 prices identical to tonybet.com/ee). Part of the pre-registered forward
test's "all books" universe since 2026-09-23 (owner's go-ahead; dated note in
dev/active/picks-forward-test-preregistration.md).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from rapidfuzz import fuzz

from workers.utils import footprint

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

# PHASE 1b (2026-09-23) — every family on the full board that has a cross-book
# counterpart or a consumer. Keyed on Sportradar UOF market ids, confirmed against
# Tonybet's own market catalogue (`/api/market-descriptions/get-all-markets`).
# Totals families: vendorMarketId → market-name template; lines are half or whole
# goals/corners (quarters dropped here; the GOALS ladders 18/68/90 keep every line since #130).
_ALT_TOTALS = {
    "19": "team_total_home_{n}", "20": "team_total_away_{n}",
    "69": "team_total_1h_home_{n}", "70": "team_total_1h_away_{n}",
    "166": "corners_ou_{n}", "167": "corners_home_ou_{n}", "168": "corners_away_ou_{n}",
    "177": "corners_1h_ou_{n}", "178": "corners_1h_home_ou_{n}", "179": "corners_1h_away_ou_{n}",
    # Sportradar "bookings" — named honestly, NOT cards_ou: its card-counting rule
    # (reds, second yellows) is not proven identical to the other books' cards markets.
    "139": "bookings_ou_{n}", "140": "bookings_home_ou_{n}", "141": "bookings_away_ou_{n}",
    "152": "bookings_1h_ou_{n}",
}
# Goals totals by half. Since #130 (2026-09-24) every line is kept, like the FT ladder.
_HALF_GOALS = {"68": "over_under_1h_", "90": "over_under_2h_"}


def _goals_label(line: float | None, prefix: str = "over_under_") -> str | None:
    """Goals O/U label for ANY line Tonybet quotes — .5, whole and quarter (#130,
    owner 2026-09-24: "collect all the data we can … all OU lines").

    Spelling is the one `workers/utils/odds_quality.ou_line_from_label` already reads:
    str(line) with the dot removed — 2.5 → over_under_25, 2.0 → over_under_20,
    2.25 → over_under_225, 0.75 → over_under_075. The .5 lines 0.5–4.5 therefore come
    out byte-identical to the shared vocabulary, and a quarter line can never land
    under a .5 label. Lines >= 10 are refused: their spelling would read back as a
    different line ("105" → 1.05). handicap_line always carries the number too.
    """
    if line is None or not 0 < line < 10 or abs(line * 4 - round(line * 4)) > 1e-9:
        return None
    s = f"{line:g}"
    if "." not in s:
        s += ".0"
    return prefix + s.replace(".", "")


_EXTRA_LADDERS = ("over_under_", "over_under_1h_", "over_under_2h_")


def _is_half_line(line: float | None) -> bool:
    return line is not None and abs(line * 2 - round(line * 2)) < 1e-9 and line != int(line)


def drop_non_monotone_extra_lines(rows: list[tuple]) -> tuple[list[tuple], int]:
    """Guard for the whole/quarter/high lines #130 adds. The shared FT guard
    (`epicbet_explorer.drop_non_monotone_ft_ou`) only knows the .5 lines 0.5–4.5, so
    the new lines would otherwise reach the table unchecked — and neither does the
    #120 wrong-fixture guard (board_guard.CHECK_MARKETS is 1x2 + O/U 1.5/2.5/3.5).
    Per ladder (FT, 1H, 2H) the OVER price must not fall as the line rises; if it
    does, every NON-.5-in-0.5..4.5 row of that ladder is dropped (the standard lines
    are left to the shared guard). rows = (market, selection, odds, line[, ...])."""
    dropped = 0
    out = list(rows)
    for pre in _EXTRA_LADDERS:
        fam = [r for r in out if r[0].startswith(pre) and r[3] is not None
               and (pre != "over_under_" or not r[0].startswith(("over_under_1h_", "over_under_2h_")))]
        overs = sorted((r[3], r[2]) for r in fam if r[1] == "over")
        unders = sorted((r[3], r[2]) for r in fam if r[1] == "under")
        ok = all(a[1] <= b[1] + 1e-9 for a, b in zip(overs, overs[1:])) and \
            all(a[1] + 1e-9 >= b[1] for a, b in zip(unders, unders[1:]))
        if not ok:
            extra = {id(r) for r in fam if not (_is_half_line(r[3]) and r[3] < 5)}
            dropped += len(extra)
            out = [r for r in out if id(r) not in extra]
    return out, dropped
_RESULT_1X2 = {"60": "1x2_1h", "83": "1x2_2h"}
_YESNO = {"75": "btts_1h", "95": "btts_2h"}
_YESNO_SEL = {"74": "yes", "76": "no"}


def _session() -> requests.Session:
    from workers.utils.footprint import metered_session   # BOOK-FOOTPRINT (#110)
    s = metered_session("Tonybet")
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
                tag = _goals_label(line)          # every line since #130
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
            # ── phase 1b families ──────────────────────────────────────────
            elif vm in _ALT_TOTALS and oid in _OU:
                line = _line(spec, "total")
                if line is not None and abs(line * 2 - round(line * 2)) < 1e-9:
                    add(_ALT_TOTALS[vm].format(n=f"{round(line * 10):02d}"), _OU[oid], o, line)
            elif vm in _HALF_GOALS and oid in _OU:
                line = _line(spec, "total")
                tag = _goals_label(line, _HALF_GOALS[vm])   # every line since #130
                if tag:
                    add(tag, _OU[oid], o, line)
            elif vm in _RESULT_1X2 and oid in _1X2:
                add(_RESULT_1X2[vm], _1X2[oid], o)
            elif vm in _YESNO and oid in _YESNO_SEL:
                add(_YESNO[vm], _YESNO_SEL[oid], o)
            elif vm == "63" and oid in _DC:
                add("double_chance_1h", _DC[oid], o)
            elif vm == "165" and oid in _AH:
                line = _line(spec, "hcp")
                if line is not None:
                    add("corners_handicap", _AH[oid], o, line)
    return rows


# Full board (~120-140 market types) at these minutes-to-kickoff windows, for our
# matched fixtures only. With 30-minute sweeps each window catches every fixture
# once: ~3 deep fetches per fixture per day. The closing price (T-5..15) comes from
# near_kickoff_capture, which calls fetch_deep_markets by event id.
_DEEP_WINDOWS = ((15, 45), (165, 195), (1425, 1455))


def fetch_deep_markets(sess: requests.Session, event_id) -> list[dict]:
    """Every market on one event (`main=0`). ~0.2–0.8 MB per call; archived raw."""
    r = sess.get(_API, params=[("lang", "en"), ("eventId_eq", str(event_id)),
                               ("main", "0"), ("relations[]", "odds")], timeout=45)
    r.raise_for_status()
    _archive("deep", int(event_id) % 100, r.content)
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"Tonybet deep board {event_id}: {str(body)[:200]}")
    return ((body["data"].get("relations") or {}).get("odds") or {}).get(str(event_id)) or []


def _in_deep_window(minutes: int | None) -> bool:
    return minutes is not None and any(lo <= minutes <= hi for lo, hi in _DEEP_WINDOWS)


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
            # [[#154]] idea 3 (owner 2026-09-26): the FIRST value in each checkpoint bucket is kept in
            # book_fair_probs_history (migration 474) so an anchor test can align Tonybet with Pinnacle /
            # the exchange before kick-off; the latest value above stays the close.
            hist = [(m, b, mk, sel, line, cp, p, odds, mins)
                    for (m, b, mk, sel, line, p, odds, mins) in payload for cp in fair_checkpoints(mins)]
            if hist:
                cur.executemany(
                    """INSERT INTO book_fair_probs_history
                         (match_id, bookmaker, market, selection, handicap_line, checkpoint, fair_prob,
                          odds, minutes_to_kickoff)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (match_id, bookmaker, market, selection,
                                    COALESCE(handicap_line, -9999), checkpoint) DO NOTHING""",
                    hist)
        conn.commit()
    return len(payload)


FAIR_CHECKPOINTS = (("t24h", 24 * 60), ("t3h", 3 * 60), ("t1h", 60))


def fair_checkpoints(minutes: int | None) -> list[str]:
    """Pure: the history buckets a fair value taken `minutes` before kick-off belongs to. 'open' always
    (the first write wins); each tN once inside it. None (unknown) -> 'open' only."""
    out = ["open"]
    if minutes is not None and minutes >= 0:
        out += [name for name, lim in FAIR_CHECKPOINTS if minutes <= lim]
    return out


def store_event_rows(match_id: str, markets: list[dict], minutes: int | None) -> int:
    """Parse + OU-guard + store odds and fair probs for one fixture. Returns rows stored."""
    from workers.api_clients.supabase_client import store_book_odds_snapshots
    from workers.automation.epicbet_explorer import drop_non_monotone_ft_ou
    rows5, _x = drop_non_monotone_extra_lines(parse_markets(markets))
    rows4, _dropped = drop_non_monotone_ft_ou(
        [(mk, sel, odds, line) for mk, sel, odds, line, _p in rows5], match_id)
    kept = set((mk, sel, line) for mk, sel, _o, line in rows4)
    stored = store_book_odds_snapshots(BOOKMAKER, match_id, rows4, minutes) if rows4 else 0
    store_fair_probs(match_id, [r for r in rows5 if (r[0], r[1], r[3]) in kept], minutes)
    return stored


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
         "rows": 0, "stored": 0, "fair_probs": 0, "ou_dropped": 0, "deep": 0,
         "deep_deferred": 0}
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
        record_book_events(BOOKMAKER, [(m["id"], str(ev["id"]), ev["start"], ev.get("_match_score"))
                                       for m, ev in pairs])

    for m, ev in pairs:
        markets = ev["markets"]
        deep_ok = not dry_run and _in_deep_window(_minutes_to_kickoff(ev["start"]))
        # PRIORITY RESERVE (#151, 2026-09-25): a deep board is optional (the main board below
        # is the fallback). On the 09-25 weekend slate these grew to ~120/h and spent the whole
        # 150 before live stats, the near-kickoff close and results could run.
        if deep_ok and not footprint.has_headroom(BOOKMAKER):
            c["deep_deferred"] += 1
            deep_ok = False
        if deep_ok:
            try:
                time.sleep(_SLEEP_S)
                markets = fetch_deep_markets(sess, ev["id"]) or markets
                c["deep"] += 1
            except Exception as e:  # noqa: BLE001 — fall back to the main board
                log.warning("tonybet deep board %s failed: %s", ev["id"], e)
        rows5 = parse_markets(markets)
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


# ── PHASE 2: live stats + results ─────────────────────────────────────────────

def _pair(d: dict | None, key: str) -> tuple:
    v = (d or {}).get(key) or {}
    return v.get("home"), v.get("away")


def _event_map() -> dict[str, str]:
    from workers.api_clients.db import execute_query
    return {r["eid"]: r["mid"] for r in execute_query(
        "SELECT book_event_id AS eid, match_id::text AS mid FROM book_event_map "
        "WHERE bookmaker = %s", (BOOKMAKER,)) or []}


# IN-PLAY BOARDS (#130, 2026-09-24). Owner: "we should collect all the data we can,
# lets start collecting inplay OU, all OU lines that we can from tonybet". The live list
# run_live already fetches every 120 s returns the full board when `relations[]=odds`
# is added, so this costs ZERO extra requests (only a bigger payload; the raw response
# is archived as before). Stored in `inplay_book_quotes` (book='Tonybet') in the same
# nested shape as the Epicbet collector: [{fam, gid, line, sel:[{sel, odds, suspended, p}]}].
# Families: every goals total (FT / 1H / 2H / team totals, ALL lines) plus 1x2, Asian
# handicap and BTTS for context. Sides are the BOOK's orientation — home_team/away_team
# carry Tonybet's names so a flipped pairing is detectable (join on match_id + names).
_LIVE_FAMS = {"18": "ou", "68": "ou_1h", "90": "ou_2h", "19": "tt_home", "20": "tt_away",
              "69": "tt_1h_home", "70": "tt_1h_away", "1": "1x2", "16": "ah", "29": "btts"}
_LIVE_SEL = {**{k: v.capitalize() for k, v in _OU.items()}, **_1X2, **_AH, **_BTTS}
_LAST_BOARD: dict[str, int] = {}    # event id → hash of the last stored board


def _clock(s: str | None) -> tuple[int | None, int | None]:
    """'67:12' → (67, 12). Tonybet leaves it blank for many events (breaks, some
    feeds) — then minute is NULL and analyses join AF's minute on match_id + time."""
    try:
        m, _, sec = (s or "").partition(":")
        return int(m), int(sec) if sec else None
    except ValueError:
        return None, None


def live_board(markets: list[dict]) -> list[dict]:
    """Normalise one event's live markets. Open markets only (status == 1); an
    outcome that is not active is kept but flagged suspended, like Epicbet."""
    out = []
    for m in markets or []:
        vm = str(m.get("vendorMarketId"))
        fam = _LIVE_FAMS.get(vm)
        if fam is None or m.get("status") != 1:
            continue
        if vm == "16" and not _ah_useful(m):   # same AH band as pre-match; ~13 lines/match otherwise
            continue
        line = _line(m.get("specifiers"), "hcp" if vm == "16" else "total")
        sels = [{"sel": _LIVE_SEL.get(str(o.get("vendorOutcomeId")), str(o.get("vendorOutcomeId"))),
                 "odds": float(o["odds"]), "suspended": o.get("active") != 1,
                 "p": o.get("probabilities")}
                for o in m.get("outcomes") or [] if o.get("odds")]
        if sels:
            out.append({"fam": fam, "gid": int(vm), "line": line, "sel": sels})
    out.sort(key=lambda x: (x["gid"], x["line"] if x["line"] is not None else -99))
    return out


def run_live() -> dict:
    """Snapshot score / clock / status / corners / cards for EVERY live football
    event (matched to our fixtures or not — unmatched is still data we own), and
    since #130 the in-play odds board of each into inplay_book_quotes.
    One request per poll; the scheduler polls every 120 s."""
    from workers.api_clients.db import get_conn
    sess = _session()
    r = sess.get(_API, params=[("lang", "en"), ("period", "0"), ("sportId_eq", "1"),
                               ("status_in[]", "2"), ("status_in[]", "1"),
                               ("limit", str(_PAGE)), ("relations[]", "result"),
                               ("relations[]", "statistics"),
                               ("relations[]", "additionalInfo"),
                               ("relations[]", "odds"),            # #130 in-play boards
                               ("relations[]", "competitors")], timeout=45)
    r.raise_for_status()
    _archive("live", 1, r.content)
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"Tonybet live list: {str(body)[:200]}")
    d = body["data"]
    rel = d.get("relations") or {}
    res, st, info = rel.get("result") or {}, rel.get("statistics") or {}, rel.get("additionalInfo") or {}
    emap = _event_map()
    rows = []
    for it in d.get("items") or []:
        e = str(it["id"])
        rr, ss = res.get(e) or {}, st.get(e) or {}
        ch, ca = _pair(ss, "corners")
        yh, ya = _pair(ss, "yellowCards")
        rh, ra = _pair(ss, "redCards")
        yrh, yra = _pair(ss, "yellowRedCards")
        rows.append((BOOKMAKER, e, it.get("vendorEventId"), emap.get(e),
                     rr.get("matchStatusId"), (rr.get("clock") or {}).get("matchTime"),
                     rr.get("team1Score"), rr.get("team2Score"), ch, ca, yh, ya, rh, ra,
                     yrh, yra, (info.get(e) or {}).get("coverage_source")))
    if rows:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO book_live_stats
                         (bookmaker, book_event_id, sr_match_id, match_id, match_status_id,
                          clock, score_home, score_away, corners_home, corners_away,
                          yellows_home, yellows_away, reds_home, reds_away,
                          yellow_reds_home, yellow_reds_away, coverage_source)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
            conn.commit()
    out = {"live": len(rows), "matched": sum(1 for x in rows if x[3]),
           "with_stats": sum(1 for x in rows if x[8] is not None)}

    # ── #130 in-play boards — AFTER the stats commit, so a board failure can never
    # cost the corners/cards record. It still fails the job loudly (RuntimeError after
    # the counts are known) rather than going silently empty.
    comps = {c["id"]: c for c in (rel.get("competitors") or []) if isinstance(c, dict)}
    odds = rel.get("odds") or {}
    now = datetime.now(timezone.utc)
    boards, unchanged = [], 0
    for it in d.get("items") or []:
        e = str(it["id"])
        board = live_board(odds.get(e) or [])
        if not board:
            continue
        rr = res.get(e) or {}
        key = hash((rr.get("team1Score"), rr.get("team2Score"), json.dumps(board, sort_keys=True)))
        if _LAST_BOARD.get(e) == key:
            unchanged += 1
            continue
        _LAST_BOARD[e] = key
        minute, sec = _clock((rr.get("clock") or {}).get("matchTime"))
        h, a = comps.get(it.get("competitor1Id")) or {}, comps.get(it.get("competitor2Id")) or {}
        boards.append((now, BOOKMAKER, e, emap.get(e), h.get("name"), a.get("name"),
                       minute, sec, rr.get("team1Score"), rr.get("team2Score"), json.dumps(board)))
    live_ids = {str(it["id"]) for it in d.get("items") or []}
    for gone in [k for k in _LAST_BOARD if k not in live_ids]:
        del _LAST_BOARD[gone]
    out.update(boards=len(boards), boards_unchanged=unchanged, events_with_odds=len(odds))
    if boards:
        import psycopg2.extras
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """INSERT INTO inplay_book_quotes
                             (captured_at, book, book_event_id, match_id, home_team, away_team,
                              minute, seconds, score_home, score_away, markets) VALUES %s""",
                        boards, page_size=200)
                conn.commit()
        except Exception as ex:
            for b in boards:            # let the next poll retry these events
                _LAST_BOARD.pop(b[2], None)
            raise RuntimeError(f"Tonybet in-play boards not stored ({out}): {ex}") from ex
    return out


def _period(periods: list, n: int) -> tuple:
    for p in periods or []:
        if p.get("number") == n:
            return p.get("team1Score"), p.get("team2Score")
    return None, None


def run_results(hours_back: int = 36) -> dict:
    """Upsert FT / HT / 2H results for football that ended in the last `hours_back`
    hours. Tonybet purges results ~1–2 days after kickoff, so this must run well
    inside that. Final corners/cards: the results feed usually has cleared them,
    so fall back to the LAST live snapshot we stored for the event."""
    from workers.api_clients.db import execute_query, get_conn
    sess = _session()
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%d %H:%M:%S"
    items, rel_res, rel_st = [], {}, {}
    for page in range(1, _MAX_PAGES + 1):
        # NB: no `period` param — with it, ended events return nothing.
        r = sess.get(_API, params=[("lang", "en"), ("sportId_eq", "1"),
                                   ("status_in[]", "4"), ("status_in[]", "5"),
                                   ("limit", str(_PAGE)), ("page", str(page)),
                                   ("time_gte", (now - timedelta(hours=hours_back)).strftime(fmt)),
                                   ("time_lte", now.strftime(fmt)),
                                   ("relations[]", "result"), ("relations[]", "statistics")],
                     timeout=60)
        r.raise_for_status()
        _archive("results", page, r.content)
        body = r.json()
        if body.get("status") != "ok":
            raise RuntimeError(f"Tonybet results: {str(body)[:200]}")
        d = body["data"]
        items += d.get("items") or []
        rel_res.update((d.get("relations") or {}).get("result") or {})
        rel_st.update((d.get("relations") or {}).get("statistics") or {})
        if page >= int(d.get("lastPage") or 1):
            break
        time.sleep(_SLEEP_S)
    emap = _event_map()
    ids = [str(i["id"]) for i in items]
    last_live = {r["eid"]: r for r in execute_query(
        """SELECT DISTINCT ON (book_event_id) book_event_id AS eid, corners_home, corners_away,
                  yellows_home, yellows_away, reds_home, reds_away
             FROM book_live_stats WHERE bookmaker = %s AND book_event_id = ANY(%s)
              -- the very last snapshot is often a post-match RESET (0-0, status 0,
              -- no stats); take the last one that still carried stats
              AND corners_home IS NOT NULL
            ORDER BY book_event_id, captured_at DESC""", (BOOKMAKER, ids)) or []} if ids else {}
    rows, from_live, from_feed = [], 0, 0
    for it in items:
        e = str(it["id"])
        rr, ss = rel_res.get(e) or {}, rel_st.get(e) or {}
        if rr.get("team1Score") is None:
            continue
        # matchStatusId 0 = not started: the feed still carries team1Score 0 / team2Score 0,
        # which stored 0-0 "results" (Ajax W 8-0 recorded as 0-0 — DQ audit 2026-09-24)
        if not rr.get("matchStatusId"):
            continue
        ch, ca = _pair(ss, "corners")
        yh, ya = _pair(ss, "yellowCards")
        rh, ra = _pair(ss, "redCards")
        src = "results_feed" if ch is not None else None
        if ch is None and e in last_live:
            lv = last_live[e]
            ch, ca, yh, ya, rh, ra = (lv["corners_home"], lv["corners_away"], lv["yellows_home"],
                                      lv["yellows_away"], lv["reds_home"], lv["reds_away"])
            src = "last_live_snapshot" if ch is not None else None
        from_feed += src == "results_feed"
        from_live += src == "last_live_snapshot"
        hth, hta = _period(rr.get("periods"), 1)
        h2h, h2a = _period(rr.get("periods"), 2)
        # FULL TIME = REGULAR TIME. team1Score/team2Score is the running total and
        # includes extra time AND the penalty shootout (Boreham Wood stored 3-5 for
        # a 1-1). Build FT from the two halves; with no halves, trust the total only
        # when there were no extra periods.
        periods = rr.get("periods") or []
        if hth is not None and h2h is not None:
            ft_h, ft_a = hth + h2h, hta + h2a
        elif len(periods) <= 2:
            ft_h, ft_a = rr.get("team1Score"), rr.get("team2Score")
        else:
            ft_h = ft_a = None
        ko = datetime.strptime(it["time"], fmt).replace(tzinfo=timezone.utc)
        import json as _json
        rows.append((BOOKMAKER, e, it.get("vendorEventId"), emap.get(e), ko,
                     rr.get("matchStatusId"), ft_h, ft_a,
                     hth, hta, h2h, h2a, ch, ca, yh, ya, rh, ra, src,
                     _json.dumps(rr.get("periods") or [])))
    if rows:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO book_match_results
                         (bookmaker, book_event_id, sr_match_id, match_id, kickoff, match_status_id,
                          ft_home, ft_away, ht_home, ht_away, h2_home, h2_away,
                          corners_home, corners_away, yellows_home, yellows_away,
                          reds_home, reds_away, stats_source, periods, captured_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb, now())
                       ON CONFLICT (bookmaker, book_event_id) DO UPDATE SET
                         match_id = COALESCE(EXCLUDED.match_id, book_match_results.match_id),
                         match_status_id = EXCLUDED.match_status_id,
                         ft_home = EXCLUDED.ft_home, ft_away = EXCLUDED.ft_away,
                         ht_home = EXCLUDED.ht_home, ht_away = EXCLUDED.ht_away,
                         h2_home = EXCLUDED.h2_home, h2_away = EXCLUDED.h2_away,
                         corners_home = COALESCE(EXCLUDED.corners_home, book_match_results.corners_home),
                         corners_away = COALESCE(EXCLUDED.corners_away, book_match_results.corners_away),
                         yellows_home = COALESCE(EXCLUDED.yellows_home, book_match_results.yellows_home),
                         yellows_away = COALESCE(EXCLUDED.yellows_away, book_match_results.yellows_away),
                         reds_home = COALESCE(EXCLUDED.reds_home, book_match_results.reds_home),
                         reds_away = COALESCE(EXCLUDED.reds_away, book_match_results.reds_away),
                         stats_source = COALESCE(EXCLUDED.stats_source, book_match_results.stats_source),
                         periods = EXCLUDED.periods, captured_at = now()""", rows)
            conn.commit()
    return {"ended": len(items), "results": len(rows), "matched": sum(1 for x in rows if x[3]),
            "stats_from_feed": from_feed, "stats_from_last_live": from_live}


def main() -> None:
    ap = argparse.ArgumentParser(description="Tonybet pre-match odds sweep")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--live", action="store_true", help="one live-stats poll")
    ap.add_argument("--results", action="store_true", help="one results sweep")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.live:
        print(run_live())
    elif a.results:
        print(run_results())
    else:
        print(run_bulk(a.hours, a.dry_run))


if __name__ == "__main__":
    main()
