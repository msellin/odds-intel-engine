"""Epicbet read-only odds explorer (EPICBET-ODDS-INGEST-2026-08-27).

Second Estonian-licensed, operator-reachable book in `odds_snapshots`. Coolbet
was the only venue the operator can actually bet at, which meant every value bot
was priced against a single book; Epicbet (Ducks In A Row OÜ, EMTA toto licence)
gives us a second reachable price to shop against.

Why this file is so much simpler than `coolbet_explorer.py`:

  • **No auth.** Epicbet's prematch feed is served anonymously over plain REST
    JSON — no session, no JWT, no Imperva. Verified 2026-08-27 from a plain
    `requests` call with nothing but a User-Agent. So this runs VPS-side on the
    normal scheduler; there is no Mac daemon, no FlareSolverr, no session table.
  • **Bulk, not per-match.** Coolbet needs search → fo-match → sidebets per
    fixture. Epicbet's own site pulls a whole league in one
    `match.getFoByLeague` call with the main market groups already inlined, so a
    full sweep is ~140 league calls plus a handful of batched odds calls,
    regardless of how many fixtures are in our window.

API shape (tRPC-style — every request is `?input=<url-encoded JSON>`):

    /s/core-proxy/public/sport-base/foCategory.getByCountry
        {"country":"EE","language":"en","isLiveBet":false}
        → flat category list; football = sport.id 1, leagues have `league` set
          and carry the `boCategory.id` used as `leagueCategoryId` below.

    /s/core-proxy/public/sport-base/match.getFoByLeague
        {"leagueCategoryId":7,"language":"en","country":"EE",
         "offset":0,"limit":50,"period":"all"}
        → {name, matches:[{id, startDate, homeTeamName, awayTeamName,
                           marketGroups:[{id,name,viewType,markets:[...]}]}],
           hasMore}

    /s/core-proxy/public/sport-odds/activeOdds.getPreMatchByMarketIds
        [137120331, 137120908, ...]
        → [{outcomeId, status, value}, ...]

**Market coverage is deliberately narrower than Coolbet's.** The league listing
returns only the top market groups: 1X2 (group 45), Match Total Goals (15), Both
to Score (69) and Goals Handicap (19). Double chance is NOT in the listing, so
Epicbet writes no `double_chance` rows — Coolbet still does. Adding DC needs a
per-match markets endpoint we have not located; not worth blocking the ingest
for a market that is arithmetically implied by 1X2 anyway.

Epicbet also prices quarter lines (0.75, 1.25, 2.25 …) that Coolbet does not
carry at all. `_ou_market_for_line` keeps only the .5 lines we have a column
vocabulary for; AH quarter lines ARE kept, since `handicap_line` is a float.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import requests
from rich.console import Console
from rich.table import Table

from workers.api_clients.supabase_client import store_book_odds_snapshots
from workers.automation.coolbet_explorer import (
    _ou_market_for_line,
    _ou_rows_monotone,
    load_matches_in_window,
)
from workers.automation.coolbet_placer import _parse_iso_start, fuzzy_match_event

log = logging.getLogger(__name__)
console = Console()

BOOKMAKER = "Epicbet"
_BASE = "https://epicbet.com"
_CATEGORIES_URL = "/s/core-proxy/public/sport-base/foCategory.getByCountry"
_LEAGUE_MATCHES_URL = "/s/core-proxy/public/sport-base/match.getFoByLeague"
_ODDS_URL = "/s/core-proxy/public/sport-odds/activeOdds.getPreMatchByMarketIds"

_FOOTBALL_SPORT_ID = 1
# Epicbet paginates league listings; 50 covers every league we have seen in one
# call, and `hasMore` drives the follow-up pages regardless.
_LEAGUE_PAGE_SIZE = 50
# The site itself sends ~350 market ids in a single activeOdds call. 250 keeps
# the query string comfortably inside proxy URL limits.
_ODDS_CHUNK = 250
# Kickoff-bucket half-width, in hours. Must be >= coolbet_placer's
# _FUZZY_DATE_TOLERANCE_HOURS or we would pre-filter out events the fuzzy
# matcher would still have accepted.
_FUZZY_HOUR_SPAN = 6

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Market group ids observed on Epicbet. Names are localised per language, group
# ids are not — so ids are the primary key and the name is only a fallback.
_GROUP_1X2 = 45
_GROUP_OU = 15
_GROUP_BTTS = 69
_GROUP_AH = 19


# ── transport ─────────────────────────────────────────────────────────────────


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-GB,en;q=0.9,et;q=0.8",
        "Referer": f"{_BASE}/en/sports/football",
    })
    return s


# ── FlareSolverr fallback (EPICBET-403-FROM-VPS-2026-08-29) ──────────────────
#
# EPICBET-ODDS-INGEST claimed Epicbet "hits no bot-protection". That was only
# ever tested from the operator's residential IP. From the Hetzner VPS every
# call returns 403 behind a Cloudflare "Just a moment..." interstitial, which
# is why the job wrote nothing for six days while reporting success.
#
# Cookie harvesting — the pattern coolbet_session.py uses against Imperva —
# does NOT work here. FlareSolverr does earn a `cf_clearance` cookie, but
# replaying it from plain `requests` still 403s: Cloudflare binds clearance to
# the browser's TLS fingerprint, not just IP + User-Agent. Verified on the box.
#
# So the calls themselves go through FlareSolverr. A *session* makes that
# affordable: the first request pays the challenge (~11s), every subsequent
# one is ~0.3s, because FS keeps the solved browser tab alive.
# Same shape as the Coolbet odds snapshot, which is the proven pattern in this
# repo: API calls through a NAMED FlareSolverr session, env-configurable
# (com.oddsintel.coolbet-odds-snapshot.plist sets COOLBET_FLARE_SESSION=
# coolbet_odds_reader). Only Coolbet *placement* drives a real browser; its
# odds reader does exactly this.
#
# The difference is where it can run. Imperva refuses the datacenter IP even
# through FS, which is why the Coolbet reader had to move to Mac launchd.
# Cloudflare does not: FS from the VPS returns 200 and 552 categories,
# verified on the box, so this stays server-side and needs no Mac.
_FS_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")
_FS_SESSION_ID = os.getenv("EPICBET_FLARE_SESSION", "epicbet_odds_reader")
# Only used when a direct call has already been refused, so the Mac path (which
# reaches Epicbet fine) never pays the FS cost.
_FS_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S)


def _fs_post(cmd: str, **kw):
    r = requests.post(f"{_FS_URL}/v1", json={"cmd": cmd, **kw}, timeout=180)
    r.raise_for_status()
    return r.json()


def _fs_open(sess: requests.Session) -> None:
    """Create the shared FS session once per run; mark it on `sess`."""
    if getattr(sess, "_fs_on", False):
        return
    try:
        _fs_post("sessions.destroy", session=_FS_SESSION_ID)   # clear a stale one
    except Exception:
        pass
    out = _fs_post("sessions.create", session=_FS_SESSION_ID)
    if out.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr session create failed: {out.get('message')}")
    sess._fs_on = True
    log.info("Epicbet: direct calls are 403 from this host — routed via FlareSolverr")


def fs_close(sess: requests.Session) -> None:
    """Destroy the FS session. Safe to call when one was never opened —
    leaking a session pins a Chrome tab until the sweeper reaps it."""
    if not getattr(sess, "_fs_on", False):
        return
    try:
        _fs_post("sessions.destroy", session=_FS_SESSION_ID)
    except Exception as e:
        log.warning("Epicbet: FS session destroy failed: %s", e)
    sess._fs_on = False


def _fs_get_json(url: str, *, _retry: bool = True):
    """Fetch `url` through FlareSolverr and return the decoded JSON body.

    FS returns the response wrapped in a rendered HTML page, so the JSON is
    inside a <pre> block.

    Recreates the session once if it has vanished. `sweep_stale_sessions.py`
    runs hourly at :37 and destroys every session not in its whitelist
    (coolbet_prod, hltv_*); this one is deliberately NOT whitelisted, because
    it is created and destroyed per run and the sweeper is then a free
    safety net for a leak after a crash. The cost is that a run overlapping
    :37 can have its session pulled mid-flight — so recover instead of
    failing the whole sweep.
    """
    out = _fs_post("request.get", url=url, session=_FS_SESSION_ID, maxTimeout=90000)
    if out.get("status") != "ok":
        msg = str(out.get("message") or "")
        if _retry and "session" in msg.lower():
            log.warning("Epicbet: FS session vanished mid-run (%s) — recreating", msg)
            _fs_post("sessions.create", session=_FS_SESSION_ID)
            return _fs_get_json(url, _retry=False)
        raise RuntimeError(f"FlareSolverr: {msg}")
    sol = out.get("solution") or {}
    if sol.get("status") != 200:
        raise requests.HTTPError(f"Epicbet returned {sol.get('status')} via FlareSolverr")
    body = sol.get("response") or ""
    m = _FS_PRE.search(body)
    return json.loads(m.group(1) if m else body)


def _get(sess: requests.Session, path: str, payload=None, *, timeout: int = 25):
    """GET an Epicbet tRPC endpoint and unwrap `{"result":{"data":...}}`.

    Tries a direct request first and falls back to FlareSolverr for the rest
    of the run once one is refused — so the residential path stays fast and
    the VPS still works.
    """
    url = _BASE + path
    if payload is not None:
        url += "?input=" + urllib.parse.quote(
            json.dumps(payload, separators=(",", ":"))
        )

    if getattr(sess, "_fs_on", False):
        body = _fs_get_json(url)
    else:
        try:
            r = sess.get(url, timeout=timeout)
            r.raise_for_status()
            body = r.json()
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status not in (403, 429, 503):
                raise
            _fs_open(sess)
            body = _fs_get_json(url)

    if isinstance(body, dict) and "result" in body:
        return body["result"].get("data")
    return body


# ── discovery ─────────────────────────────────────────────────────────────────


def fetch_football_leagues(sess: requests.Session) -> list[dict]:
    """Every football league with a prematch offering, as
    [{"cat_id": <leagueCategoryId>, "name": str, "slug": str}]."""
    cats = _get(sess, _CATEGORIES_URL,
                {"country": "EE", "language": "en", "isLiveBet": False}) or []
    out: list[dict] = []
    seen: set[int] = set()
    for c in cats:
        if (c.get("sport") or {}).get("id") != _FOOTBALL_SPORT_ID:
            continue
        if not c.get("league"):          # region/sport nodes carry league=None
            continue
        cat_id = (c.get("boCategory") or {}).get("id")
        if cat_id is None or cat_id in seen:
            continue
        seen.add(int(cat_id))
        out.append({
            "cat_id": int(cat_id),
            "name": (c.get("boCategory") or {}).get("name") or c.get("slugEn") or "",
            "slug": c.get("slugEn") or "",
        })
    return out


def fetch_league_events(sess: requests.Session, cat_id: int) -> list[dict]:
    """All prematch fixtures for one league, paginated through `hasMore`.

    Returned dicts are shaped for `fuzzy_match_event` (home/away/start) with the
    raw Epicbet payload kept under "raw" for market parsing.
    """
    events: list[dict] = []
    offset = 0
    while True:
        data = _get(sess, _LEAGUE_MATCHES_URL, {
            "leagueCategoryId": cat_id,
            "language": "en",
            "country": "EE",
            "offset": offset,
            "limit": _LEAGUE_PAGE_SIZE,
            "period": "all",
        })
        if not data:
            break
        for m in data.get("matches") or []:
            if m.get("type") and m["type"] != "regular":
                continue          # outrights et al — no fixture to match against
            events.append({
                "id": m.get("id"),
                "home": m.get("homeTeamName") or "",
                "away": m.get("awayTeamName") or "",
                "start": _normalise_start(m.get("startDate") or m.get("betEndDate")),
                "league": data.get("name") or "",
                "raw": m,
            })
        if not data.get("hasMore"):
            break
        offset += _LEAGUE_PAGE_SIZE
    return events


def _normalise_start(raw: str | None) -> str:
    """Epicbet ships two shapes: "2026-08-27 18:30:00+00" and ISO-Z. Coolbet's
    `_parse_iso_start` wants something `datetime.fromisoformat` accepts, so
    normalise the space-separated form and leave ISO alone."""
    if not raw:
        return ""
    return raw.replace(" ", "T", 1) if " " in raw else raw


# ── odds ──────────────────────────────────────────────────────────────────────


def collect_market_ids(event: dict) -> list[int]:
    """Market ids for the groups we ingest, for one Epicbet event."""
    ids: list[int] = []
    for g in (event.get("raw") or {}).get("marketGroups") or []:
        if g.get("id") not in (_GROUP_1X2, _GROUP_OU, _GROUP_BTTS, _GROUP_AH):
            continue
        for mkt in g.get("markets") or []:
            if mkt.get("id") is not None:
                ids.append(int(mkt["id"]))
    return ids


def fetch_odds(sess: requests.Session, market_ids: list[int],
               *, sleep_s: float = 0.2) -> dict[int, float]:
    """outcome_id → decimal odds, for every open outcome across `market_ids`."""
    out: dict[int, float] = {}
    uniq = sorted(set(int(m) for m in market_ids))
    for i in range(0, len(uniq), _ODDS_CHUNK):
        chunk = uniq[i:i + _ODDS_CHUNK]
        rows = _get(sess, _ODDS_URL, chunk) or []
        for row in rows:
            if row.get("status") != "open":
                continue
            try:
                oid = int(row["outcomeId"])
                val = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if val > 1.0:
                out[oid] = val
        if i + _ODDS_CHUNK < len(uniq):
            time.sleep(sleep_s)
    return out


# ── parsing ───────────────────────────────────────────────────────────────────


def parse_event_markets(
    event: dict, odds_map: dict[int, float],
) -> list[tuple[str, str, float, float | None]]:
    """(market, selection, odds, handicap_line) rows for one Epicbet event.

    Emits the same vocabulary every other bookmaker in `odds_snapshots` uses:
    `1x2` home/draw/away, `over_under_XX` over/under, `btts` yes/no,
    `asian_handicap` home/away with a home-perspective `handicap_line`.
    """
    raw = event.get("raw") or {}
    home_name = (raw.get("homeTeamName") or "").strip()
    away_name = (raw.get("awayTeamName") or "").strip()
    rows: list[tuple[str, str, float, float | None]] = []

    def add(market: str, selection: str, outcome_id, line: float | None = None) -> None:
        try:
            odds = odds_map.get(int(outcome_id))
        except (TypeError, ValueError):
            return
        if odds and odds > 1.0:
            rows.append((market, selection, float(odds), line))

    for g in raw.get("marketGroups") or []:
        gid = g.get("id")
        for mkt in g.get("markets") or []:
            outcomes = mkt.get("outcomes") or []

            if gid == _GROUP_1X2:
                # Outcomes are ordered home / draw / away but carry team names
                # rather than result keys, so match on name and fall back to
                # position only for the draw.
                for oc in outcomes:
                    nm = (oc.get("name") or "").strip()
                    if nm.lower() == "draw":
                        add("1x2", "draw", oc.get("id"))
                    elif nm == home_name:
                        add("1x2", "home", oc.get("id"))
                    elif nm == away_name:
                        add("1x2", "away", oc.get("id"))

            elif gid == _GROUP_OU:
                market = _ou_market_for_line(_as_float(mkt.get("line")))
                if market is None:
                    continue          # quarter line, or a line we don't store
                for oc in outcomes:
                    nm = (oc.get("name") or "").strip().lower()
                    if nm in ("over", "under"):
                        add(market, nm, oc.get("id"))

            elif gid == _GROUP_BTTS:
                for oc in outcomes:
                    nm = (oc.get("name") or "").strip().lower()
                    if nm in ("yes", "no"):
                        add("btts", nm, oc.get("id"))

            elif gid == _GROUP_AH:
                # EPICBET-AH-SIGN: `market.line` agrees with the home side on
                # every sample checked (market 166930520 line=-2.5 prices Celta
                # -2.5 / Osasuna +2.5; market 159759518 line=1 prices Celta +1.0
                # / Osasuna -1.0), so it is already home-perspective — the same
                # convention `handicap_line` uses for Coolbet. We still read the
                # value off each outcome's own line string rather than the
                # market-level one: it is unambiguous per row, and it survives
                # Epicbet ever flipping the market-level sign.
                for oc in outcomes:
                    nm = (oc.get("name") or "").strip()
                    oc_line = _as_float(oc.get("line"))
                    if oc_line is None:
                        continue
                    if nm == home_name:
                        add("asian_handicap", "home", oc.get("id"), oc_line)
                    elif nm == away_name:
                        # Store the away row against the same home-perspective
                        # line so both sides of one market share a key.
                        add("asian_handicap", "away", oc.get("id"), -oc_line)
    return rows


def _as_float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _minutes_to_kickoff(iso: str) -> int | None:
    if not iso:
        return None
    try:
        ko = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=timezone.utc)
    return int((ko - datetime.now(timezone.utc)).total_seconds() // 60)


# ── squad-qualifier guard (EPICBET-SQUAD-GUARD) ───────────────────────────────

# `fuzzy_match_event` scores with `partial_ratio`, which is blind to squad
# suffixes: "Rosario Central Res." vs Epicbet's first-team "Rosario Central"
# scores 100. On the first live run that produced Epicbet home 2.75 against
# Pinnacle 1.47 on Rosario Central Res. v Barracas Central Res. — an apparent
# +87% edge that is really two different fixtures. Reserve, youth and women's
# sides must only match candidates carrying the same qualifier.
_SQUAD_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("w",   re.compile(r"(?:^|[\s(\[])(?:w|women|femenino|femenina|feminino|feminina|ladies)(?:$|[\s)\]])")),
    ("u23", re.compile(r"(?:^|[\s(\[-])u[-\s]?23(?:$|[\s)\]])")),
    ("u21", re.compile(r"(?:^|[\s(\[-])u[-\s]?21(?:$|[\s)\]])")),
    ("u20", re.compile(r"(?:^|[\s(\[-])u[-\s]?20(?:$|[\s)\]])")),
    ("u19", re.compile(r"(?:^|[\s(\[-])u[-\s]?19(?:$|[\s)\]])")),
    ("u18", re.compile(r"(?:^|[\s(\[-])u[-\s]?18(?:$|[\s)\]])")),
    ("u17", re.compile(r"(?:^|[\s(\[-])u[-\s]?17(?:$|[\s)\]])")),
    ("res", re.compile(r"(?:^|\s)(?:res\.?|reserves?|ii|2|b)$")),
)


def _squad_tag(name: str) -> str | None:
    """Squad qualifier carried by a team name, or None for a first team."""
    n = (name or "").strip().lower()
    for tag, pat in _SQUAD_PATTERNS:
        if pat.search(n):
            return tag
    return None


def _squads_compatible(our_home: str, our_away: str, ev: dict) -> bool:
    """True when both sides agree on squad qualifier with the candidate event."""
    return (
        _squad_tag(our_home) == _squad_tag(ev.get("home") or "")
        and _squad_tag(our_away) == _squad_tag(ev.get("away") or "")
    )


# ── orchestration ─────────────────────────────────────────────────────────────


def run_bulk(
    days: int = 2,
    dry_run: bool = False,
    limit: int | None = None,
    *,
    sleep_s: float = 0.15,
) -> dict:
    """Snapshot Epicbet prices for every DB match kicking off within `days`.

    Returns a counters dict (also rendered as a table) so the scheduler log and
    the smoke test can both assert on it.
    """
    matches = load_matches_in_window(days)
    if limit:
        matches = matches[:limit]
    if not matches:
        console.print("[yellow]No upcoming matches in DB window.[/yellow]")
        return {"db_matches": 0, "matched": 0, "stored": 0}

    console.print(
        f"[cyan]Loaded {len(matches)} matches from DB "
        f"(window={days}d){' [DRY-RUN]' if dry_run else ''}[/cyan]"
    )

    sess = _session()
    try:
        return _run_bulk_inner(sess, matches, days, sleep_s, dry_run)
    finally:
        # Always tear the FS session down. A leaked session pins a Chrome tab
        # until the hourly flaresolverr_sweep reaps it, and repeated leaks are
        # how FS ran out of memory before (see project_fs_sticking_pattern).
        fs_close(sess)


def _run_bulk_inner(sess, matches, days, sleep_s, dry_run):
    leagues = fetch_football_leagues(sess)
    console.print(f"[cyan]Epicbet football leagues with prematch: {len(leagues)}[/cyan]")

    events: list[dict] = []
    for i, lg in enumerate(leagues, 1):
        try:
            found = fetch_league_events(sess, lg["cat_id"])
        except Exception as e:
            log.warning("league %s (%s) failed: %s", lg["name"], lg["cat_id"], e)
            continue
        events.extend(found)
        if i < len(leagues):
            time.sleep(sleep_s)
    console.print(f"[cyan]Epicbet prematch fixtures indexed: {len(events)}[/cyan]")

    # Resolve our matches against the index first, so we only pull odds for
    # markets we are actually going to store.
    #
    # `fuzzy_match_event` already rejects candidates more than ±6h from our
    # kickoff, but it does so *after* scoring every event — O(db_matches ×
    # fixtures) partial_ratio calls, which is minutes of CPU at a few hundred
    # matches against a few thousand fixtures. Bucketing by kickoff hour first
    # cuts the candidate set to the handful that could possibly match, without
    # changing the outcome.
    by_hour: dict[int, list[dict]] = {}
    for ev in events:
        start = _parse_iso_start(ev.get("start"))
        if start is None:
            continue
        by_hour.setdefault(int(start.timestamp() // 3600), []).append(ev)

    pairs: list[tuple[dict, dict]] = []
    for m in matches:
        ko = m.get("date")
        candidates = events
        if ko is not None:
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            centre = int(ko.timestamp() // 3600)
            candidates = [
                ev
                for h in range(centre - _FUZZY_HOUR_SPAN, centre + _FUZZY_HOUR_SPAN + 1)
                for ev in by_hour.get(h, ())
            ]
        candidates = [
            ev for ev in candidates if _squads_compatible(m["home"], m["away"], ev)
        ]
        if not candidates:
            continue
        ev = fuzzy_match_event(m["home"], m["away"], candidates, m.get("date"),
                               match_id=m.get("id"))
        if ev is not None:
            pairs.append((m, ev))

    market_ids: list[int] = []
    for _m, ev in pairs:
        market_ids.extend(collect_market_ids(ev))
    odds_map = fetch_odds(sess, market_ids) if market_ids else {}
    console.print(
        f"[cyan]Matched {len(pairs)}/{len(matches)} · "
        f"{len(set(market_ids))} markets · {len(odds_map)} live outcomes[/cyan]"
    )

    stored_total = 0
    parsed_total = 0
    dropped_ou = 0
    by_market: dict[str, int] = {}

    for m, ev in pairs:
        rows = parse_event_markets(ev, odds_map)
        parsed_total += len(rows)

        # Same OU sanity guard the Coolbet writer runs
        # (COOLBET-OU-LINE-MISLABEL-2026-08-22): a non-monotone Under-probability
        # across lines is mathematically impossible, so the labelling is wrong
        # and zero OU data beats lying OU data.
        ou_rows = [r for r in rows if r[0].startswith("over_under_")]
        if ou_rows and not _ou_rows_monotone([(a, b, c) for a, b, c, _ in ou_rows]):
            log.warning("epicbet-ou-monotonicity: dropping %d OU rows for match %s",
                        len(ou_rows), m["id"])
            dropped_ou += len(ou_rows)
            rows = [r for r in rows if not r[0].startswith("over_under_")]

        for market, _sel, _odds, _line in rows:
            by_market[market] = by_market.get(market, 0) + 1
        if dry_run or not rows:
            continue
        try:
            stored_total += store_book_odds_snapshots(
                BOOKMAKER, m["id"], rows,
                _minutes_to_kickoff(ev.get("start") or ""),
            )
        except Exception as e:
            log.warning("store failed for match %s: %s", m["id"], e)

    t = Table(show_header=True, title="Epicbet ingest summary")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    t.add_row("Matches in DB window", str(len(matches)))
    t.add_row("Matched on Epicbet", str(len(pairs)))
    t.add_row("Rows parsed", str(parsed_total))
    t.add_row("OU rows dropped (monotonicity)", str(dropped_ou))
    t.add_row("Rows stored", "0 (dry-run)" if dry_run else str(stored_total))
    for mkt in sorted(by_market):
        t.add_row(f"  {mkt}", str(by_market[mkt]))
    console.print(t)

    return {
        "db_matches": len(matches),
        "matched": len(pairs),
        "parsed": parsed_total,
        "stored": stored_total,
        "by_market": by_market,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Epicbet odds explorer")
    ap.add_argument("--days", type=int, default=2, help="DB kickoff window")
    ap.add_argument("--limit", type=int, default=None, help="cap DB matches")
    ap.add_argument("--dry-run", action="store_true", help="parse but do not store")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    run_bulk(days=args.days, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
