"""OPTIBET-SWEEPER (#101, 2026-09-26) — pre-match odds from Optibet (Estonian-licensed,
Optiwin OÜ; Enlabs in-house trading, "betex"). COLLECTION ONLY: Optibet is NOT in
ACCESSIBLE_BOOKMAKERS and is on no placement or bot path. Whether it becomes placeable is
the owner's call (a site-price check like Tonybet's comes first).

WHY OPTIBET. docs/EE_BOOK_SURVEY_2026_09_23.md ranked it next after Tonybet: own trading
(not a copy of a feed we already hold), ~460 pre-match football events in 48 h.

TRANSPORT. Plain anonymous REST on the sportsbook's own backend,
`ensb-trading.optibet.ee` — the JSON the bet.optibet.ee iframe reads. No login, no token,
no challenge on this host (it sits behind Cloudflare, which answered 200 throughout). We
never touch `www.optibet.ee`: on 2026-09-24 ~10 curl guesses against the SHELL site got a
Cloudflare 403 on the shared Estonian exit. Routed like Tonybet: `OPTIBET_PROXY`, else
`EPICBET_RESIDENTIAL_PROXY` (the zone.ee Estonian exit on the VPS), else direct (the Mac is
already Estonian).

THE THREE CALLS (found 2026-09-26 in the sportsbook bundle and the browser network tab):
  1. GET /en/groups?domainId=1                     every league group + eventCount (1 req)
  2. GET /en/events/group/{id,id,…}?gameTypes=1    the events of MANY groups in one call —
                                                   the path takes a comma list (the bundle
                                                   names it `:groupIds`). ~200 football
                                                   groups in chunks of 40 → ~5 requests.
  3. GET /en/events/{id,id,…}?gameTypes=…          the chosen markets of MANY events in one
                                                   call; only for events matched to our
                                                   fixtures, chunks of 25 → ~8 requests.
So a sweep is ~14 requests (~28/h at the 30-min cadence) — the survey's "139 requests, one
per group" estimate predates finding the comma-list form. `/en/` gives English titles, which
the parser keys on for the period (the type id is shared by 1st half / 2nd half / regular
time); the event's `player1` is home, `player2` away (`invertedTeams` was false on all 759
listed events — an inverted one is skipped, never guessed).

WHAT IS STORED — `odds_snapshots` (bookmaker 'Optibet'), shared vocabulary:
  gameType 1   match                → 1x2            home/draw/away
  gameType 2   doubleChance         → double_chance  1x/12/x2
  gameType 3   overOrUnder          → over_under_XX  over/under, every line (Tonybet spelling)
  gameType 4   handicap (2-way)     → asian_handicap home/away, line = HOME handicap.
               Verified Asian: line 0 prices like draw-no-bet (1.19 vs 1X2 1.58) and -0.5
               like the home win (1.55 vs 1.58). Kept only when both sides price 1.25–4.0.
  gameType 739 both teams to score  → btts / btts_1h / btts_2h        (by title)
  gameType 540 draw no bet          → draw_no_bet (regular time only)
  gameType 12 / 46 half results     → 1x2_1h / 1x2_2h
  gameType 672 half double chance   → double_chance_1h
  gameType 537 half goal totals     → over_under_1h_XX / over_under_2h_XX (15-min windows dropped)
  gameType 538/539 team goal totals → team_total_{home,away}_XX, team_total_1h_{home,away}_XX
  gameType 733 total corners        → corners_ou_XX / corners_1h_ou_XX
Every row goes through the shared writer (`store_book_odds_snapshots`), i.e. the O/U label
check, board_guard.screen_board (wrong-fixture boards) and mirror_guard (inverted 1X2), plus
the per-book monotone-ladder guards Tonybet uses. Pairings go to `book_event_map`.
Not collected (not offered pre-match on the boards sampled 2026-09-26): cards/bookings.
Also on the board and not parsed yet: 3-way handicap, correct score, HT/FT, combos,
player props — all kept in the raw archive.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from workers.utils import footprint

log = logging.getLogger(__name__)

BOOKMAKER = "Optibet"
_BASE = "https://ensb-trading.optibet.ee/en"
_DOMAIN = ("domainId", "1")
_GROUP_CHUNK = 40      # groups per listing call (~350 KB each at gameTypes=1)
_EVENT_CHUNK = 25      # events per board call (~1.1 MB uncompressed, ~45 KB/event)
_SLEEP_S = 1.0
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
_PROXY = os.getenv("OPTIBET_PROXY") or os.getenv("EPICBET_RESIDENTIAL_PROXY") or None

# The game types asked for on the board call (see the table in the module docstring).
BOARD_GAME_TYPES = (1, 2, 3, 4, 12, 46, 537, 538, 539, 540, 672, 733, 739)

# Raw archive, same policy as Tonybet (owner 2026-09-23: "parse everything we can … start
# creating our own data sources"): every response kept gzipped so later parsers can be
# rebuilt back to the first day. OPTIBET_RAW_DIR="" disables; a failure never fails a sweep.
_RAW_DIR = os.getenv("OPTIBET_RAW_DIR", "/opt/oddsintel/raw/optibet")


def _archive(kind: str, n: int, content: bytes) -> None:
    if not _RAW_DIR:
        return
    try:
        import gzip
        from pathlib import Path
        now = datetime.now(timezone.utc)
        d = Path(_RAW_DIR) / now.strftime("%Y/%m/%d")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{now.strftime('%H%M%S')}_{kind}_{n:02d}.json.gz").write_bytes(
            gzip.compress(content, compresslevel=6))
    except Exception as e:  # noqa: BLE001 — archiving must never cost the sweep
        log.debug("optibet raw archive failed: %s", e)


def _session():
    s = footprint.metered_session(BOOKMAKER)      # BOOK-FOOTPRINT (#110)
    if _PROXY:
        s.proxies = {"http": _PROXY, "https": _PROXY}
    # Accept: application/json is REQUIRED — without it the host answers with an HTML page.
    s.headers.update({"User-Agent": _UA, "Accept": "application/json",
                      "Origin": "https://bet.optibet.ee", "Referer": "https://bet.optibet.ee/"})
    return s


def _get(sess, path: str, params: list[tuple], kind: str, n: int = 0):
    r = sess.get(f"{_BASE}{path}", params=params + [_DOMAIN], timeout=60)
    r.raise_for_status()
    if "json" not in (r.headers.get("content-type") or ""):
        raise RuntimeError(f"Optibet {path} answered {r.headers.get('content-type')}, not JSON "
                           f"(a challenge page?) — stopping rather than retrying")
    _archive(kind, n, r.content)
    return r.json()


# ── names ─────────────────────────────────────────────────────────────────────

# Optibet writes squad qualifiers in brackets — "Chelsea (Women)", "Romania(U19)",
# "Amazulu (Reserves)", "Italy (U20) (Women)". The shared squad guard
# (epicbet_explorer._squad_tag) reads trailing words, so rewrite the brackets into the
# form it and AF use: "Chelsea W", "Romania U19", "Amazulu Reserves", "Italy U20 W".
_BRACKET = re.compile(r"\s*\(\s*(women|u\s?\d{2}|reserves?|youth)\s*\)", re.I)


def _team_name(p: dict | None) -> str | None:
    name = ((p or {}).get("name") or "").strip()
    if not name:
        return None

    def sub(m: re.Match) -> str:
        tag = m.group(1).lower().replace(" ", "")
        if tag == "women":
            return " W"
        if tag == "youth":
            return " \x00"      # refused below — the squad guard has no youth tag (Tonybet does the same)
        if tag.startswith("reserve"):
            return " Reserves"
        return " " + tag.upper()

    out = _BRACKET.sub(sub, name)
    if "\x00" in out:
        return None
    return re.sub(r"\s+", " ", out).strip()


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_football_group_ids(sess) -> list[int]:
    """Every football league group that currently lists at least one event."""
    groups = _get(sess, "/groups", [], "groups")
    return [g["id"] for g in groups
            if g.get("sport") == "football" and (g.get("eventCount") or 0) > 0
            and not g.get("isSpecialBets")]


def fetch_listing(sess, group_ids: list[int], hours: int = 48) -> list[dict]:
    """Pre-match football events kicking off within `hours`, with the 1X2 game inline."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    out, seen = [], set()
    for i in range(0, len(group_ids), _GROUP_CHUNK):
        if i:
            time.sleep(_SLEEP_S)
        chunk = ",".join(str(g) for g in group_ids[i:i + _GROUP_CHUNK])
        evs = _get(sess, f"/events/group/{chunk}",
                   [("gameTypes", "1"), ("gameType", "-1")], "listing", i // _GROUP_CHUNK)
        for e in evs or []:
            if e.get("id") in seen or e.get("live") or e.get("started") or e.get("outrightEvent"):
                continue
            seen.add(e.get("id"))
            start = datetime.fromtimestamp(int(e["time"]), tz=timezone.utc)
            if not now < start <= horizon:
                continue
            out.append({
                "id": e["id"], "sr_id": e.get("betRadarMatchId"),
                "home": _team_name(e.get("player1")), "away": _team_name(e.get("player2")),
                "inverted": bool(e.get("invertedTeams")),
                "start": start.isoformat(),
                "league": ((e.get("parentGroups") or [{}])[-1]).get("name"),
                "games": e.get("games") or [],
            })
    return out


def fetch_boards(sess, event_ids: list) -> dict[str, list[dict]]:
    """{event id: games} for BOARD_GAME_TYPES — many events per request."""
    gt = [("gameTypes", str(t)) for t in BOARD_GAME_TYPES]
    out: dict[str, list[dict]] = {}
    for i in range(0, len(event_ids), _EVENT_CHUNK):
        if i:
            time.sleep(_SLEEP_S)
        chunk = ",".join(str(e) for e in event_ids[i:i + _EVENT_CHUNK])
        evs = _get(sess, f"/events/{chunk}", gt, "board", i // _EVENT_CHUNK)
        if isinstance(evs, dict):     # a single id answers with the object, not a list
            evs = [evs]
        for e in evs or []:
            if e.get("live") or e.get("started"):
                continue
            out[str(e["id"])] = e.get("games") or []
    return out


# ── parse ─────────────────────────────────────────────────────────────────────

_1X2 = {1: "home", 2: "draw", 3: "away"}
_DC = {4: "1x", 5: "12", 6: "x2"}
_OU = {7: "over", 8: "under"}
_AH = {9: "home", 10: "away"}
_BTTS = {7430: "yes", 7431: "no"}
_DNB = {1: "home", 3: "away"}
_HT = {26: "home", 27: "draw", 28: "away"}
_2H = {400: "home", 401: "draw", 402: "away"}
_DC_HALF = {7022: "1x", 7023: "12", 7024: "x2"}
_TT_HOME = {22: "over", 23: "under"}
_TT_AWAY = {24: "over", 25: "under"}
_CORNERS = {7415: "over", 7416: "under"}
_PERIOD = (("Regular Time", ""), ("1st Half", "_1h"), ("2nd Half", "_2h"))


def _period(title: str) -> str | None:
    """'' / '_1h' / '_2h' from the English title; None for time windows
    ('1st Half (00:00 - 15:00) - …') and anything unrecognised."""
    for prefix, tag in _PERIOD:
        if title.startswith(prefix) and not title[len(prefix):].lstrip().startswith("("):
            return tag
    return None


def _num(h) -> float | None:
    try:
        v = float(h)
    except (TypeError, ValueError):
        return None
    return v if abs(v * 4 - round(v * 4)) < 1e-9 else None


def _is_half(line: float | None) -> bool:
    return line is not None and abs(line * 2 - round(line * 2)) < 1e-9 and line != int(line)


def parse_games(games: list[dict]) -> list[tuple]:
    """(market, selection, odds, handicap_line) for one event. Inactive games and odds skipped."""
    from workers.automation.tonybet_feed import _goals_label
    rows: list[tuple] = []

    def add(market, sel, o, line=None):
        v = o.get("value")
        if not o.get("isActive") or not v or float(v) <= 1.0:
            return
        rows.append((market, sel, float(v), line))

    for g in games or []:
        if not g.get("active"):
            continue
        t = g.get("typeId")
        title = (g.get("title") or "").strip()
        line = _num(g.get("handicap"))
        odds = g.get("odds") or []
        if t == 4:   # AH band filter, like Tonybet: both sides 1.25–4.0
            prices = [float(o.get("value") or 0) for o in odds]
            if line is None or len(prices) != 2 or not all(1.25 <= p <= 4.0 for p in prices):
                continue
        for o in odds:
            ot = o.get("typeId")
            if t == 1 and ot in _1X2:
                add("1x2", _1X2[ot], o)
            elif t == 2 and ot in _DC:
                add("double_chance", _DC[ot], o)
            elif t == 3 and ot in _OU:
                tag = _goals_label(line)
                if tag:
                    add(tag, _OU[ot], o, line)
            elif t == 4 and ot in _AH:
                add("asian_handicap", _AH[ot], o, line)
            elif t == 739 and ot in _BTTS:
                p = _period(title)
                if p is not None:
                    add("btts" + p, _BTTS[ot], o)
            elif t == 540 and ot in _DNB and title.startswith("Regular Time"):
                add("draw_no_bet", _DNB[ot], o)
            elif t == 12 and ot in _HT:
                add("1x2_1h", _HT[ot], o)
            elif t == 46 and ot in _2H:
                add("1x2_2h", _2H[ot], o)
            elif t == 672 and ot in _DC_HALF and title.startswith("1st Half"):
                add("double_chance_1h", _DC_HALF[ot], o)
            elif t == 537 and ot in _OU and _period(title) in ("_1h", "_2h"):
                tag = _goals_label(line, "over_under" + _period(title) + "_")
                if tag:
                    add(tag, _OU[ot], o, line)
            elif t in (538, 539) and _is_half(line) and _period(title) in ("", "_1h"):
                sel = (_TT_HOME if t == 538 else _TT_AWAY).get(ot)
                if sel:
                    side = "home" if t == 538 else "away"
                    per = _period(title)
                    add(f"team_total{per}_{side}_{round(line * 10):02d}", sel, o, line)
            elif t == 733 and ot in _CORNERS and _is_half(line) and _period(title) in ("", "_1h"):
                add(f"corners{_period(title)}_ou_{round(line * 10):02d}", _CORNERS[ot], o, line)
    return rows


def guard_rows(rows: list[tuple], match_id) -> tuple[list[tuple], int]:
    """Tonybet's two monotone-ladder guards (extra lines, then the FT .5 ladder)."""
    from workers.automation.epicbet_explorer import drop_non_monotone_ft_ou
    from workers.automation.tonybet_feed import drop_non_monotone_extra_lines
    rows, x = drop_non_monotone_extra_lines(rows)
    rows, y = drop_non_monotone_ft_ou(rows, match_id)
    return rows, x + y


# ── sweep ─────────────────────────────────────────────────────────────────────

def run_bulk(hours: int = 48, dry_run: bool = False) -> dict:
    """One sweep: groups → listing → match to DB fixtures → boards → store + pairings.
    Raises when Optibet itself could not be read, so the scheduler records a failure."""
    from workers.api_clients.supabase_client import record_book_events, store_book_odds_snapshots
    from workers.automation.coolbet_explorer import load_matches_in_window
    from workers.automation.coolbet_placer import fuzzy_match_event
    from workers.automation.epicbet_explorer import _minutes_to_kickoff, _squads_compatible
    from workers.automation.tonybet_feed import _flipped

    c = {"groups": 0, "events": 0, "inverted_skipped": 0, "db_matches": 0, "matched": 0,
         "flipped_skipped": 0, "boards": 0, "rows": 0, "stored": 0, "ou_dropped": 0,
         "requests": 0}
    before = footprint.process_requests(BOOKMAKER)
    sess = _session()
    gids = fetch_football_group_ids(sess)
    c["groups"] = len(gids)
    time.sleep(_SLEEP_S)
    events = [e for e in fetch_listing(sess, gids, hours) if e["home"] and e["away"]]
    c["events"] = len(events)
    c["inverted_skipped"] = sum(e["inverted"] for e in events)
    events = [e for e in events if not e["inverted"]]

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
        ev = fuzzy_match_event(m["home"], m["away"], cands, m.get("date"), match_id=m.get("id"))
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

    boards: dict[str, list[dict]] = {}
    if pairs:
        time.sleep(_SLEEP_S)
        try:
            boards = fetch_boards(sess, [ev["id"] for _, ev in pairs])
        except Exception as e:  # noqa: BLE001 — the listing's 1X2 is the fallback
            log.warning("optibet boards failed, storing the listing's 1X2 only: %s", e)
    c["boards"] = len(boards)

    for m, ev in pairs:
        games = boards.get(str(ev["id"])) or ev["games"]
        rows, dropped = guard_rows(parse_games(games), m["id"])
        c["ou_dropped"] += dropped
        c["rows"] += len(rows)
        if dry_run or not rows:
            continue
        try:
            c["stored"] += store_book_odds_snapshots(BOOKMAKER, m["id"], rows,
                                                     _minutes_to_kickoff(ev["start"]))
        except Exception as e:  # noqa: BLE001 — one fixture must not kill the sweep
            log.warning("optibet store failed for %s: %s", m["id"], e)
    c["requests"] = footprint.process_requests(BOOKMAKER) - before
    log.info("optibet sweep: %s", c)
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description="Optibet pre-match odds sweep")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(run_bulk(a.hours, a.dry_run))


if __name__ == "__main__":
    main()
