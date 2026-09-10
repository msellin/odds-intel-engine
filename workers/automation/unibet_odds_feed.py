"""UNIBET-UI-PLACER build-step 1 — Unibet real SITE odds (bookmaker `Unibet-Site`).

Captures the unibet.ee SPA's OWN Kindred `contest-page` responses via a RAW CDP
Network session on the operator's established, logged-in tab — the ONLY transport
that yields TRUE site prices. Proven live 2026-09-09 (Derby home 3.50 = site, not
the 3.20 public Kambi feed). Parses 1x2 + Over/Under total goals into this repo's
shared `(market, selection, odds, line)` vocabulary and writes them via
`store_book_odds_snapshots` as bookmaker `Unibet-Site`.

WHY RAW CDP AND NOT AN API SWEEP (all four tested live 2026-09-09):
  * Raw-CDP capture of the SPA's own response (established tab) → 200, true prices ✓
  * Fresh / background CDP tab                                   → 500/204 DataDome challenge ✗
  * Injected fetch() from the established page                  → CORS "Failed to fetch" ✗
  * FlareSolverr → the Kindred API (Coolbet's odds pattern)     → HTTP 400 "Bad request" ✗
So the Coolbet odds path (FlareSolverr → a plain JSON API) does NOT transfer: the
Kindred API rejects everything but the SPA's own fully-formed XHR. Only reading the
SPA's own responses works. Full matrix in dev/active/unibet-ui-placer-plan.md.

LOW-VOLUME BY DESIGN (parity with the placer). Because true site odds cost one tab
navigation per event (no API, no derivable slug, no navigable contestKey), this
fetches odds for the handful of CANDIDATE fixtures we route/place on — NOT a
book-wide sweep. Broad soft-book screening for the Unibet trigger bots stays on the
cheap public Kambi feed (`unibet_kambi.py`, bookmaker `Unibet-Kambi`); this module
confirms the true PLACEABLE price per candidate before the router writes/places.

SAFETY: read-only. It navigates the operator's tab and reads response bodies. It
never selects an outcome, sets a stake, or places anything — that is `unibet_placer`.
"""
from __future__ import annotations

import logging
import os

from workers.automation import unibet_browser_sync as ubs

log = logging.getLogger(__name__)

_BOOKMAKER = "Unibet-Site"

# The SPA's per-event prices call. We match on the path; the SPA appends
# contestKey + client params we do not need to reconstruct (we only READ it).
_CONTEST_MARKER = "views/contest-page"


# ---------------------------------------------------------------------------
# Parser — pure, deterministic, unit-tested offline against a committed fixture.
# ---------------------------------------------------------------------------
def _price(v) -> float | None:
    """Kindred prices are already decimal (3.5), unlike Kambi milli-odds."""
    try:
        f = float(v)
        return f if f > 1 else None
    except (TypeError, ValueError):
        return None


def _total_line(options: list[dict]) -> float | None:
    for o in options:
        v = o.get("total")
        if v is None:
            v = o.get("line")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def parse_contest(contest_json: dict) -> list[tuple[str, str, float, float | None]]:
    """(market, selection, odds, handicap_line) rows in the shared vocabulary.

    Keyed on the language-stable `propositionType` (NOT the localised
    `displayName`). This is what makes the parser contamination-proof — the
    Estonian feed carries `1x2`, `1x2_{xup}up` (a 2-up variant), `3_way_handicap`,
    `1st_half_total`, `{competitor1}_total`, `total_corners`, `total_bookings`
    etc. side by side, and only the exact types below are the match markets we bet.
    """
    c = contest_json.get("contest") or contest_json
    props = c.get("propositions") or []
    rows: list[tuple[str, str, float, float | None]] = []
    for p in props:
        ptype = str(p.get("propositionType") or "")
        opts = p.get("options") or []

        # 1X2 — normal-time result ONLY. Excludes `1x2_{xup}up` (2-up promo
        # variant, team-name options) and `3_way_handicap` (also "1"/"X"/"2").
        if ptype == "1x2":
            m = {"1": "home", "X": "draw", "2": "away"}
            for o in opts:
                sel = m.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append(("1x2", sel, odds, None))
            continue

        # Over/Under MATCH total goals ONLY. `propositionType == "total"` excludes
        # `1st_half_total`, `{competitor1}_total`/`{competitor2}_total`,
        # `total_corners`, `1st_half_total_corners`, `total_bookings` — all of
        # which also ship Üle/Alla options but price on a different quantity.
        if ptype == "total":
            line = _total_line(opts)
            if line is None:
                continue
            # whole/half lines only (Kindred main total is already .5/.0)
            if abs(line * 2 - round(line * 2)) > 1e-9:
                continue
            tag = "over_under_" + f"{line:.1f}".replace(".", "").zfill(2)  # 2.5 -> over_under_25
            m = {"Üle": "over", "Alla": "under"}
            for o in opts:
                sel = m.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append((tag, sel, odds, line))
            continue
    return rows


# ---------------------------------------------------------------------------
# Raw-CDP capture — reads the SPA's own contest-page response bodies.
# ---------------------------------------------------------------------------
async def _http_get_json(url: str) -> object:
    import asyncio
    import json
    import urllib.request

    def _fetch():
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())

    return await asyncio.to_thread(_fetch)


async def _async_capture(event_url: str, *, timeout_s: float) -> dict | None:
    """Navigate the operator's established unibet.ee tab to `event_url` and return
    the parsed `contest-page` JSON the SPA fetches. Raw CDP over a websocket, like
    coolbet_browser_sync — raw CDP retains response bodies where connect_over_cdp
    evicts them. Returns None on any failure."""
    import asyncio
    import base64
    import json

    import websockets

    cdp = ubs.CDP_URL
    try:
        targets = await asyncio.wait_for(_http_get_json(f"{cdp}/json/list"), timeout=timeout_s)
    except Exception as e:  # noqa: BLE001
        log.warning("unibet-odds: CDP /json/list failed: %s", e)
        return None

    tab = None
    for t in targets or []:
        if t.get("type") == "page" and "unibet.ee" in (t.get("url") or "").lower():
            tab = t
            break
    if tab is None:
        log.warning("unibet-odds: no unibet.ee tab open in CDP-Chrome — cannot capture "
                    "(a fresh tab is DataDome-degraded; the operator must keep a "
                    "logged-in unibet.ee tab open).")
        return None
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        return None

    try:
        async with websockets.connect(ws_url, open_timeout=timeout_s, max_size=30_000_000) as ws:
            nid = [0]

            async def cmd(method: str, params: dict | None = None) -> int:
                nid[0] += 1
                await ws.send(json.dumps({"id": nid[0], "method": method, "params": params or {}}))
                return nid[0]

            await cmd("Network.enable")
            await cmd("Page.enable")
            await cmd("Page.navigate", {"url": event_url})

            # Collect contest-page responses (status 200) until their bodies are
            # fully buffered (loadingFinished), then read them promptly.
            contest_ids: dict[str, str] = {}
            finished: set[str] = set()
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout_s
            settle_after_finish_s = 1.5
            first_finish_at: float | None = None
            while loop.time() < deadline:
                if first_finish_at is not None and (loop.time() - first_finish_at) > settle_after_finish_s:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception:  # noqa: BLE001
                    break
                evt = json.loads(msg)
                method = evt.get("method")
                if method == "Network.responseReceived":
                    p = evt.get("params") or {}
                    resp = p.get("response") or {}
                    url = resp.get("url") or ""
                    if _CONTEST_MARKER in url and resp.get("status") == 200:
                        contest_ids[p.get("requestId")] = url
                elif method == "Network.loadingFinished":
                    rid = (evt.get("params") or {}).get("requestId")
                    if rid in contest_ids and rid not in finished:
                        finished.add(rid)
                        if first_finish_at is None:
                            first_finish_at = loop.time()

            if not finished:
                log.warning("unibet-odds: no contest-page 200 seen for %s within %.0fs",
                            event_url[:70], timeout_s)
                return None

            # Read each finished body; keep the envelope that actually has
            # contest.propositions (the SPA fires several contest-page-ish calls).
            best: dict | None = None
            for rid in finished:
                bid = await cmd("Network.getResponseBody", {"requestId": rid})
                t0 = loop.time()
                while loop.time() - t0 < 6:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    evt = json.loads(msg)
                    if evt.get("id") != bid:
                        continue
                    if "error" in evt:
                        break
                    res = evt.get("result") or {}
                    body = res.get("body") or ""
                    if res.get("base64Encoded"):
                        try:
                            body = base64.b64decode(body).decode("utf-8", "replace")
                        except Exception:  # noqa: BLE001
                            break
                    try:
                        parsed = json.loads(body)
                    except Exception:  # noqa: BLE001
                        break
                    if isinstance(parsed, dict) and (parsed.get("contest") or {}).get("propositions"):
                        best = parsed
                    break
            return best
    except Exception as e:  # noqa: BLE001
        log.warning("unibet-odds: capture WS failed: %s", e)
        return None


def fetch_event_odds(event_url: str, *, match_id: str | None = None,
                     write: bool = False, minutes_to_kickoff: int | None = None,
                     timeout_s: float = 25.0) -> dict:
    """Capture Unibet SITE odds for one event and (optionally) store them.

    `event_url` is the fixture's unibet.ee page (the placer's resolved URL). Reads
    the SPA's contest-page via raw CDP on the operator's established tab. When
    `write` and `match_id` are given, writes the parsed rows as bookmaker
    `Unibet-Site`. Never raises.
    """
    import asyncio

    out = {"event_url": event_url, "contest_name": None, "rows": [],
           "stored": 0, "reason": None}
    try:
        contest = asyncio.run(_async_capture(event_url, timeout_s=timeout_s))
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"capture failed: {e}"
        return out
    if not contest:
        out["reason"] = "no contest-page captured (is a logged-in unibet.ee tab open?)"
        return out

    out["contest_name"] = (contest.get("contest") or {}).get("name")
    rows = parse_contest(contest)
    out["rows"] = rows
    if not rows:
        out["reason"] = "captured contest but parsed no 1x2/total rows"
        return out
    if write and match_id:
        from workers.api_clients.supabase_client import store_book_odds_snapshots
        out["stored"] = store_book_odds_snapshots(
            _BOOKMAKER, str(match_id), rows, minutes_to_kickoff=minutes_to_kickoff)
        out["reason"] = f"stored {out['stored']} rows as {_BOOKMAKER}"
    else:
        out["reason"] = "parsed (not written — pass write=True + match_id)"
    return out


# ---------------------------------------------------------------------------
# 3a — BROAD sweep: write Unibet-Site odds for DB fixtures via injected fetch.
# ---------------------------------------------------------------------------
# The Kindred API rejects a bare request (HTTP 400) and DataDome blocks headless
# tabs, so the ONLY broad transport is the operator's established tab making the
# SPA's OWN fetch — an injected fetch WITH the SPA's static headers returns 200
# with true prices (proven 2026-09-09 across events). We enumerate site events
# per league via the lobby view, match them to DB fixtures, then injected-fetch
# each matched event's contest-page. Everything is rate-limited so we never
# hammer DataDome (aggressive navigation was observed to trip a behavioural block).
_SPORTSBFF = "https://sportsbff-ams.kindredext.net/sports-api/api/v2"
_INJ_HEADERS = {"accept": "application/json", "content-type": "application/json",
                "ksp_jurisdiction": "mga", "jurisdiction": "EE",
                "locale": "et_EE", "brand": "unibet"}
_RATE_MIN_INTERVAL_S = float(os.getenv("UNIBET_SITE_RATE_S", "1.2"))  # between injected fetches
_RATE_MAX_FETCHES = int(os.getenv("UNIBET_SITE_MAX_FETCHES", "180"))  # hard cap per run
_RATE_ABORT_AFTER_BLOCKS = 4  # consecutive non-200 → stop (do not hammer a block)


def _inject_expr(url: str) -> str:
    import json
    return (f"(async()=>{{try{{const r=await fetch({json.dumps(url)},"
            f"{{credentials:'include',headers:{json.dumps(_INJ_HEADERS)}}});"
            "const t=await r.text();return JSON.stringify({s:r.status,b:t});}"
            "catch(e){return JSON.stringify({s:0,e:String(e)});}})()")


def _lobby_events(lobby_json: dict) -> list[dict]:
    """Extract [{name, contest_key, start}] from a views/lobby response."""
    out: list[dict] = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("contestKey") and o.get("name") and str(o.get("_typ", "")).endswith("FixtureContest"):
                out.append({"name": o["name"], "contest_key": o["contestKey"],
                            "start": (o.get("startDateTimeUtc") or {}).get("value")})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(lobby_json)
    # dedup by key
    seen = {}
    for e in out:
        seen[e["contest_key"]] = e
    return list(seen.values())


async def _async_run_bulk(days: int, limit: int | None, dry_run: bool) -> dict:
    """Sweep Unibet SITE odds for DB fixtures within `days`, writing `Unibet-Site`.
    Rate-limited + fail-safe. Returns counters. Never raises out."""
    import asyncio
    import json
    import time
    from datetime import datetime, timezone

    import websockets
    from rapidfuzz import fuzz

    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import store_book_odds_snapshots
    from workers.automation.coolbet_placer import fuzzy_match_event

    c = {"db_fixtures": 0, "leagues_swept": 0, "site_events": 0, "matched": 0,
         "stored": 0, "fetches": 0, "blocks": 0, "reason": None}

    # 1) find the operator's established unibet tab
    try:
        targets = await asyncio.wait_for(_http_get_json(f"{ubs.CDP_URL}/json/list"), timeout=8)
    except Exception as e:  # noqa: BLE001
        c["reason"] = f"CDP unreachable: {e}"; return c
    tab = next((t for t in (targets or []) if t.get("type") == "page"
                and "unibet.ee" in (t.get("url") or "").lower()), None)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        c["reason"] = "no logged-in unibet.ee tab open (a fresh tab is DataDome-degraded)"; return c

    # 2) DB fixtures within `days`
    fixtures = execute_query(
        """SELECT m.id::text id, m.date, ht.name home, at2.name away,
                  l.name league, l.country country
             FROM matches m
             LEFT JOIN teams ht ON ht.id=m.home_team_id
             LEFT JOIN teams at2 ON at2.id=m.away_team_id
             LEFT JOIN leagues l ON l.id=m.league_id
            WHERE m.date > now() AND m.date < now() + (%s || ' days')::interval
              AND m.date_disputed_at IS NULL AND ht.name IS NOT NULL AND at2.name IS NOT NULL
            ORDER BY m.date""",
        (str(days),))
    if limit:
        fixtures = fixtures[:limit]
    c["db_fixtures"] = len(fixtures)
    if not fixtures:
        c["reason"] = "no DB fixtures in window"; return c

    last_fetch = [0.0]
    consec_blocks = [0]

    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=40_000_000) as ws:
        nid = [0]
        async def inj(url: str) -> dict | None:
            # rate-limit + cap + abort-on-repeated-block
            if c["fetches"] >= _RATE_MAX_FETCHES:
                return None
            wait = _RATE_MIN_INTERVAL_S - (time.monotonic() - last_fetch[0])
            if wait > 0:
                await asyncio.sleep(wait + 0.15 * (nid[0] % 3))  # small jitter
            nid[0] += 1; rid = nid[0]
            await ws.send(json.dumps({"id": rid, "method": "Runtime.evaluate",
                                      "params": {"expression": _inject_expr(url),
                                                 "awaitPromise": True, "returnByValue": True}}))
            last_fetch[0] = time.monotonic(); c["fetches"] += 1
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < 20:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                evt = json.loads(msg)
                if evt.get("id") != rid:
                    continue
                val = ((evt.get("result") or {}).get("result") or {}).get("value")
                if not val:
                    return None
                try:
                    d = json.loads(val)
                except Exception:  # noqa: BLE001
                    return None
                if d.get("s") != 200:
                    c["blocks"] += 1; consec_blocks[0] += 1
                    return None
                consec_blocks[0] = 0
                try:
                    return json.loads(d.get("b") or "")
                except Exception:  # noqa: BLE001
                    return None
            return None

        # 3) COUNTRY RNs from quickbrowse (football:<country>, depth 1). The root
        # quickbrowse returns country level, and views/lobby at the country level
        # returns ALL that country's events across its leagues — so we enumerate by
        # country (fewer fetches, and country names fuzzy-map more reliably than
        # league names), then fuzzy-match home/away within the country.
        qb = await inj(f"{_SPORTSBFF}/quickbrowse?_typ=GetQuickBrowse&categoryRn=football&clientOffset=-180")
        rns: set[str] = set()
        def walk_rn(o):
            if isinstance(o, dict):
                rn = o.get("categoryRn")
                if isinstance(rn, str) and rn.startswith("football:") and rn.count(":") == 1:
                    rns.add(rn)
                for v in o.values():
                    walk_rn(v)
            elif isinstance(o, list):
                for v in o:
                    walk_rn(v)
        walk_rn(qb or {})
        if not rns:
            c["reason"] = "quickbrowse returned no country RNs (session blocked?)"; return c

        def country_of(rn: str) -> str:
            return rn.split(":", 1)[1].replace("_", " ")
        rn_list = [(rn, country_of(rn)) for rn in rns]

        # group DB fixtures by COUNTRY and fuzzy-map each to a Unibet country RN
        groups: dict[str, list] = {}
        for f in fixtures:
            groups.setdefault(f.get("country") or "", []).append(f)

        for country, fx in groups.items():
            if consec_blocks[0] >= _RATE_ABORT_AFTER_BLOCKS:
                c["reason"] = "aborted — repeated non-200 (DataDome throttling); stopping to not hammer"; break
            if c["fetches"] >= _RATE_MAX_FETCHES:
                c["reason"] = f"hit fetch cap {_RATE_MAX_FETCHES}"; break
            target = (country or "").lower().strip()
            best, best_rn = 0, None
            for rn, cn in rn_list:
                sc = fuzz.token_set_ratio(target, cn.lower())
                if sc > best:
                    best, best_rn = sc, rn
            if not best_rn or best < 80:
                continue  # no confident country match → skip (Kambi still covers it)
            lobby = await inj(f"{_SPORTSBFF}/views/lobby?_typ=GetLobbyPageView&category={best_rn}&clientOffset=-180")
            if not lobby:
                continue
            c["leagues_swept"] += 1
            events = _lobby_events(lobby)
            c["site_events"] += len(events)
            # shape candidates for fuzzy_match_event (expects {home, away, date, raw})
            cands = []
            for e in events:
                nm = e["name"]
                if " vs " not in nm:
                    continue
                h, a = nm.split(" vs ", 1)
                cands.append({"home": h.strip(), "away": a.strip(),
                              "start": e.get("start"), "raw": {"contest_key": e["contest_key"]}})
            for f in fx:
                if consec_blocks[0] >= _RATE_ABORT_AFTER_BLOCKS or c["fetches"] >= _RATE_MAX_FETCHES:
                    break
                ev = fuzzy_match_event(f["home"], f["away"], cands, f.get("date"), str(f["id"]))
                if not ev:
                    continue
                key = (ev.get("raw") or {}).get("contest_key")
                if not key:
                    continue
                c["matched"] += 1
                contest = await inj(f"{_SPORTSBFF}/views/contest-page?_typ=GetContestWithPricesReq&contestKey={key}")
                if not contest or not (contest.get("contest") or {}).get("propositions"):
                    continue
                rows = parse_contest(contest)
                if rows and not dry_run:
                    mins = int((f["date"] - datetime.now(timezone.utc)).total_seconds() // 60)
                    c["stored"] += store_book_odds_snapshots(_BOOKMAKER, str(f["id"]), rows,
                                                             minutes_to_kickoff=mins)
                elif rows:
                    c["stored"] += len(rows)
    if c["reason"] is None:
        c["reason"] = "ok"
    return c


def run_bulk(days: int = 2, dry_run: bool = False, limit: int | None = None) -> dict:
    """Broad Unibet SITE odds sweep for DB fixtures within `days` → `Unibet-Site`.
    Rate-limited (env UNIBET_SITE_RATE_S / _MAX_FETCHES), fail-safe. Requires the
    operator's logged-in unibet.ee tab in CDP-Chrome. Never raises."""
    import asyncio
    try:
        res = asyncio.run(_async_run_bulk(days, limit, dry_run))
    except Exception as e:  # noqa: BLE001
        log.warning("unibet-site run_bulk failed: %s", e)
        res = {"reason": f"run_bulk error: {e}", "stored": 0}
    # UNIBET-SITE-STALE-ALERT (2026-09-10): the sweep silently returned 0 rows for
    # hours because there was no logged-in unibet.ee tab in CDP-Chrome (a fresh tab is
    # DataDome-degraded, and Unibet auto-login fails on DataDome — needs a MANUAL login,
    # unlike Coolbet). Alert once per 3h so the feed can't rot the Unibet-Site odds (and
    # the best-price router's Unibet arm) unnoticed. Deduped; never raises.
    try:
        reason = (res or {}).get("reason") or ""
        if not dry_run and (res or {}).get("stored", 0) == 0 and "unibet.ee tab" in reason:
            from workers.notify.telegram import send_telegram
            send_telegram(
                "🟠 Unibet-Site odds feed STALE — no logged-in unibet.ee tab in CDP-Chrome. "
                "Open unibet.ee in the CDP-Chrome (:9222) and log in (DataDome blocks auto-login). "
                "Until then the Unibet-Site sweep writes 0 rows and the best-price router can't route to Unibet.",
                dedup_key="unibet-site-no-tab", dedup_window_s=10800)
    except Exception as e:  # noqa: BLE001
        log.debug("unibet-site stale alert failed (non-fatal): %s", e)
    return res


# ---------------------------------------------------------------------------
# 3c — fixture → event-URL resolver (for the placer's executor arm).
# ---------------------------------------------------------------------------
# The unibet.ee event URL is /betting/odds/<category-path>/<slug>/<contestKey>.
# PROVEN 2026-09-09: the SPA routes on the trailing contestKey — a garbage slug
# (xxx-vs-yyy) still loads the right event (contest-page 200) — so the URL is fully
# CONSTRUCTIBLE from `category` + `contestKey`, both of which the search/lobby APIs
# give us. `find_event` uses the search API (injected fetch, same transport as the
# odds feed): search a team → its nested contests → fuzzy-match the fixture.
import re as _re
import unicodedata as _ud


def slugify(name: str) -> str:
    """Cosmetic event slug from the contest name (the SPA ignores it — it routes on
    the trailing contestKey — but a real slug keeps URLs readable in logs)."""
    n = _ud.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return _re.sub(r"-+", "-", _re.sub(r"[^a-z0-9]+", "-", n.lower())).strip("-") or "x"


def build_event_url(category: str, name: str, contest_key: str) -> str:
    """/betting/odds/<category path>/<slug>/<contestKey> — routes on the contestKey."""
    return (f"https://www.unibet.ee/betting/odds/{(category or 'football').replace(':', '/')}"
            f"/{slugify(name)}/{contest_key}")


def parse_search_contests(search_json: dict) -> list[dict]:
    """Extract [{contest_key, name, category, start}] from a SearchResponse (contests
    are nested categories→searchResultsGroup→result→contests). Walks defensively."""
    out: dict[str, dict] = {}
    def walk(o):
        if isinstance(o, dict):
            if o.get("_typ") == "SearchContest" or (o.get("contestKey") and o.get("name")):
                out[o["contestKey"]] = {
                    "contest_key": o["contestKey"], "name": o.get("name"),
                    "category": o.get("category"),
                    "start": (o.get("startDateTimeUtc") or {}).get("value")}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(search_json)
    return list(out.values())


async def _async_inject_get(url: str, *, timeout_s: float = 20.0) -> dict | None:
    """One injected fetch of a Kindred API `url` (WITH the SPA headers) from the
    operator's established unibet.ee tab. Returns parsed JSON or None. This is the
    single-shot sibling of the sweep's rate-limited injected fetch."""
    import asyncio
    import json
    import websockets
    try:
        targets = await asyncio.wait_for(_http_get_json(f"{ubs.CDP_URL}/json/list"), timeout=timeout_s)
    except Exception:  # noqa: BLE001
        return None
    tab = next((t for t in (targets or []) if t.get("type") == "page"
                and "unibet.ee" in (t.get("url") or "").lower()), None)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        return None
    expr = (f"(async()=>{{try{{const r=await fetch({json.dumps(url)},"
            f"{{credentials:'include',headers:{json.dumps(_INJ_HEADERS)}}});"
            "const t=await r.text();return JSON.stringify({s:r.status,b:t});}"
            "catch(e){return JSON.stringify({s:0});}})()")
    try:
        async with websockets.connect(tab["webSocketDebuggerUrl"], open_timeout=timeout_s, max_size=30_000_000) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            loop = asyncio.get_event_loop(); t0 = loop.time()
            while loop.time() - t0 < timeout_s:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                evt = json.loads(msg)
                if evt.get("id") != 1:
                    continue
                val = ((evt.get("result") or {}).get("result") or {}).get("value")
                if not val:
                    return None
                d = json.loads(val)
                if d.get("s") != 200:
                    return None
                try:
                    return json.loads(d.get("b") or "")
                except Exception:  # noqa: BLE001
                    return None
    except Exception:  # noqa: BLE001
        return None
    return None


def search_events(query: str) -> list[dict]:
    """Injected-fetch the Unibet search API for `query`; return its contests."""
    import asyncio
    import urllib.parse
    url = (f"{_SPORTSBFF}/search?_typ=GetSearchResults&query="
           f"{urllib.parse.quote(query)}")
    try:
        d = asyncio.run(_async_inject_get(url))
    except Exception:  # noqa: BLE001
        return []
    return parse_search_contests(d or {})


def resolve_event_url(home: str, away: str, match_date=None) -> dict:
    """Resolve a DB fixture → its unibet.ee event URL via the search API + fuzzy match.
    Returns {'url','contest_key','name','category','matched'}; url None if no match.
    Never raises. The URL routes on the contestKey (slug is cosmetic)."""
    from workers.automation.coolbet_placer import fuzzy_match_event
    out = {"url": None, "contest_key": None, "name": None, "category": None, "matched": False}
    contests = search_events(home) or search_events(away)
    cands = []
    for c in contests:
        nm = c.get("name") or ""
        if " vs " not in nm:
            continue
        h, a = nm.split(" vs ", 1)
        cands.append({"home": h.strip(), "away": a.strip(), "start": c.get("start"),
                      "raw": {"contest_key": c["contest_key"], "category": c.get("category"), "name": nm}})
    ev = fuzzy_match_event(home, away, cands, match_date, None)
    if not ev:
        return out
    raw = ev.get("raw") or {}
    key = raw.get("contest_key")
    if not key:
        return out
    out.update(matched=True, contest_key=key, name=raw.get("name"), category=raw.get("category"),
               url=build_event_url(raw.get("category"), raw.get("name") or "", key))
    return out


def main() -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Capture Unibet SITE odds (Unibet-Site).")
    ap.add_argument("--event", help="unibet.ee event page URL (single-event targeted capture)")
    ap.add_argument("--bulk", action="store_true", help="broad sweep of DB fixtures within --days")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--match-id", help="single-event: DB match id to store under")
    ap.add_argument("--write", action="store_true", help="single-event: write rows to odds_snapshots")
    ap.add_argument("--minutes", type=int, default=None, help="single-event: minutes to kickoff")
    ap.add_argument("--resolve", help='resolve a fixture to its event URL: "Home vs Away"')
    args = ap.parse_args()
    if args.resolve:
        h, _, a = args.resolve.partition(" vs ")
        print(json.dumps(resolve_event_url(h.strip(), a.strip()), indent=2, ensure_ascii=False))
        return 0
    if args.bulk:
        print(json.dumps(run_bulk(days=args.days, dry_run=args.dry_run, limit=args.limit),
                         indent=2, ensure_ascii=False))
        return 0
    if not args.event:
        ap.error("pass --event <url> (capture), --bulk (sweep), or --resolve 'Home vs Away'")
    res = fetch_event_odds(args.event, match_id=args.match_id, write=args.write,
                           minutes_to_kickoff=args.minutes)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
