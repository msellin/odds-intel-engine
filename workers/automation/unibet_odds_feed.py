"""UNIBET-SITE ODDS — the true unibet.ee site prices (bookmaker `Unibet-Site`).

⚠️ UPDATED 2026-09-24 (#112): the history below describes the 2026-09-09 build on the
operator's Mac. Current reality:
  * WHERE: VPS scheduler job `unibet_site_odds` (every 30 min, :15/:45) since 2026-09-23
    (UNIBET-ON-VPS), logged OUT, through the Estonian exit; the near-kickoff close is the
    VPS timer `oddsintel-near-kickoff-epicbet`. The Mac plist is parked.
  * SCOPE: a board-wide sweep (country categories + AF "World" fixtures routed to
    Unibet's international / international clubs / international youth / uefa club
    categories), not "a handful of candidates". Markets: 1x2, O/U, BTTS, DC, DNB,
    corners, 1H corners, 1H goals, cards (UNIBET-SITE-MARKET-WIDENING-2026-09-15).
  * The public Kambi feed (`Unibet-Kambi`) is RETIRED (2026-09-15) — it read higher than
    the site on 38% of quotes. This module is the only Unibet price basis.
  * Pairing: `coolbet_placer.fuzzy_match_event` (orientation-strict since #001) +
    `unique_pairs` (one event → one fixture, #120); request budget via footprint (#110).

HISTORY (2026-09-09). Captures the SPA's OWN Kindred `contest-page` responses — the only
transport that yields TRUE site prices (Derby home 3.50 = site, not the 3.20 public Kambi
feed). Tested live then: raw-CDP capture of the SPA's own response → 200 ✓; fresh CDP tab
→ DataDome 500/204 ✗; injected fetch from the page → CORS ✗; FlareSolverr → the Kindred API
→ 400 ✗. Full matrix in dev/active/unibet-ui-placer-plan.md.

SAFETY: read-only. It reads response bodies; it never selects an outcome, sets a stake or
places anything — that is `unibet_placer`.
"""
from __future__ import annotations

import logging
import os

from workers.automation import unibet_browser_sync as ubs
from workers.utils import footprint   # BOOK-FOOTPRINT (#110) — every Kindred request is counted

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


# UNIBET-SITE-MARKET-WIDENING-2026-09-15 — over/under-shaped propositions that
# price a DIFFERENT quantity from match goals. Each is an exact `propositionType`
# mapped to the vocabulary the other books already write, so the same market can
# be line-shopped across Epicbet / Coolbet / Unibet-Site:
#
#   our market          <- Unibet propositionType
#   over_under_1h_NN       1st_half_total
#   corners_ou_NN          total_corners
#   corners_1h_ou_NN       1st_half_total_corners
#   cards_ou_NN            total_bookings
#
# The line comes from `options[].total`, same as match totals. A proposition with
# a missing or quarter line is SKIPPED, never guessed at.
_OU_FAMILIES: dict[str, str] = {
    "1st_half_total": "over_under_1h_{n}",
    "total_corners": "corners_ou_{n}",
    "1st_half_total_corners": "corners_1h_ou_{n}",
    "total_bookings": "cards_ou_{n}",
}

# Categorical markets. Option labels are LOCALISED (we fetch et_EE), unlike
# `propositionType`. That is a real fragility, and it is why every map below
# FAILS CLOSED: an unrecognised label yields no row rather than a guessed one.
# If the operator's tab ever renders in another language these markets go quiet
# — the safe direction — and the smoke test pins the Estonian labels so the
# silence is attributable instead of mysterious.
_BTTS_SEL = {"Jah": "yes", "Ei": "no"}
_DC_SEL = {"1X": "1x", "12": "12", "X2": "x2"}
_DNB_SEL = {"1": "home", "2": "away"}
_OU_SEL = {"Üle": "over", "Alla": "under"}


def _ou_tag(template: str, line: float) -> str | None:
    """`corners_ou_{n}` + 9.5 -> `corners_ou_95`; 10.5 -> `corners_ou_105`.

    Returns None for quarter lines — the shared vocabulary has no spelling for
    them and the other books never write one.
    """
    if abs(line * 2 - round(line * 2)) > 1e-9:
        return None
    return template.format(n=f"{round(line * 10):02d}")


# UNIBET-SITE-AH-LINE ([[#132]], 2026-09-26): the committed fixture was trimmed, so nobody has seen a FULL
# live `2_way_handicap` proposition. The feed reads through the operator's logged-in tab (DataDome), so an
# ad-hoc capture is off the table; instead the first few AH propositions a process parses are logged raw
# (every key, no extra request) — read them from the scheduler journal (`grep UNIBET-AH-SAMPLE`) to see
# whether the line is carried anywhere (a key, the displayName, an option field). Remove once answered.
_AH_SAMPLE_MAX = 3
_ah_samples_logged = 0


def _sample_ah_proposition(contest_name, prop: dict) -> None:
    global _ah_samples_logged
    if _ah_samples_logged >= _AH_SAMPLE_MAX:
        return
    _ah_samples_logged += 1
    try:
        import json as _json
        log.info("UNIBET-AH-SAMPLE %s: %s", contest_name, _json.dumps(prop, ensure_ascii=False, default=str)[:4000])
    except Exception:  # noqa: BLE001 — diagnostics only
        pass


def parse_contest(contest_json: dict) -> list[tuple[str, str, float, float | None]]:
    """(market, selection, odds, handicap_line) rows in the shared vocabulary.

    Keyed on the language-stable `propositionType` (NOT the localised
    `displayName`). This is what makes the parser contamination-proof — the
    Estonian feed carries `1x2`, `1x2_{xup}up` (a 2-up variant), `3_way_handicap`,
    `1st_half_total`, `{competitor1}_total`, `total_corners`, `total_bookings`
    etc. side by side, and only the exact types below are the match markets we bet.

    WHY ASIAN HANDICAP IS ABSENT, AND CANNOT SIMPLY BE ADDED
    --------------------------------------------------------
    UNIBET-SITE-MARKET-WIDENING-2026-09-15 audited a captured `contest-page` and
    found the feed returns **21 propositions where this parser took 3**. Most of
    that gap was simply never written and is taken below. `2_way_handicap`
    ("Aasia händikäp") and `3_way_handicap` are the exception — **the handicap
    LINE is not in the payload at all.** On the committed fixture both arrive as

        {"propositionType": "2_way_handicap",
         "options": [{"optionDisplayName": "1", "price": 2.0,  "total": null},
                     {"optionDisplayName": "2", "price": 1.78, "total": null}]}

    A price of 2.00 on "1" is meaningless without knowing whether it is -0.5 or
    -1.5, and `asian_handicap` rows are keyed on `handicap_line` throughout the
    pipeline. Writing them with a NULL line would drop an unpriceable row into
    the market the router shops hardest — the same shape as KAMBI-CRITERION-
    CONTAMINATION, where a look-alike offer entered a real market's vocabulary
    and became the #1 recommended book at prices that did not exist.

    So AH is deliberately NOT parsed. Recovering it needs the line from somewhere
    else (a fuller `displayName` on other fixtures, or another endpoint) and that
    is an investigation, not a parser change. Tracked as UNIBET-SITE-AH-LINE.
    """
    c = contest_json.get("contest") or contest_json
    props = c.get("propositions") or []
    rows: list[tuple[str, str, float, float | None]] = []
    for p in props:
        ptype = str(p.get("propositionType") or "")
        opts = p.get("options") or []
        if ptype == "2_way_handicap":
            _sample_ah_proposition(c.get("name"), p)

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

        # Team goal totals — CB-UB-1H-TT-COLUMNS-2026-09-11, so the per-bot
        # "Now UB" column fills for bot_team_total_paper_shadow_v1. competitor1 is
        # the home side (Derby fixture: Derby County = competitor1 = home 3.50).
        # Same vocabulary as Epicbet: team_total_{side}_{NN}, numeric line.
        # There is no first-half 1x2 propositionType on the contest page (only
        # `half_time_full_time`), so 1x2_1h cannot come from this feed.
        side = {"{competitor1}_total": "home", "{competitor2}_total": "away"}.get(ptype)
        if side:
            line = _total_line(opts)
            if line is None or abs(line * 2 - round(line * 2)) > 1e-9:
                continue
            tag = f"team_total_{side}_{round(line * 10):02d}"
            m = {"Üle": "over", "Alla": "under"}
            for o in opts:
                sel = m.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append((tag, sel, odds, line))
            continue

        # ---- UNIBET-SITE-MARKET-WIDENING-2026-09-15 ----------------------
        # Non-goal over/unders: 1st-half goals, corners, 1st-half corners,
        # bookings. Exact-type dispatch, so `total` (match goals, handled
        # above) and the per-team totals can never fall in here.
        template = _OU_FAMILIES.get(ptype)
        if template:
            line = _total_line(opts)
            if line is None:
                continue
            tag = _ou_tag(template, line)
            if tag is None:
                continue
            for o in opts:
                sel = _OU_SEL.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append((tag, sel, odds, line))
            continue

        # Both teams to score — no line.
        if ptype == "both_teams_to_score":
            for o in opts:
                sel = _BTTS_SEL.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append(("btts", sel, odds, None))
            continue

        # Double chance. Selections are lower-cased to match the vocabulary the
        # other books write (`1x` / `12` / `x2`), NOT the feed's display casing.
        if ptype == "double_chance":
            for o in opts:
                sel = _DC_SEL.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append(("double_chance", sel, odds, None))
            continue

        # Draw no bet. Shares the "1"/"2" option labels with `2_way_handicap`,
        # which is precisely why this dispatches on the exact type and never on
        # the option shape — see the AH note in the docstring.
        if ptype == "draw_no_bet":
            for o in opts:
                sel = _DNB_SEL.get(str(o.get("optionDisplayName")))
                odds = _price(o.get("price"))
                if sel and odds:
                    rows.append(("draw_no_bet", sel, odds, None))
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
        footprint.check(_BOOKMAKER)
    except footprint.FootprintBudgetExceeded as e:
        out["reason"] = str(e)
        return out
    try:
        contest = asyncio.run(_async_capture(event_url, timeout_s=timeout_s))
    except Exception as e:  # noqa: BLE001
        footprint.record(_BOOKMAKER, "error")
        out["reason"] = f"capture failed: {e}"
        return out
    footprint.record(_BOOKMAKER, "ok" if contest else "error")
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


_WORLD_CLUB_KEYS = ("club", "champions league", "europa league", "conference league",
                    "libertadores", "sudamericana", "confederation cup", "concacaf champions",
                    "leagues cup", "campeones cup", "intercontinental")
# Unibet files UEFA's club competitions under their own top-level category (review
# 2026-09-24: 'uefa club' is in the sweep's category list). Tried first for these;
# 'international clubs' is the fallback when a sweep does not list it.
_UEFA_CLUB_KEYS = ("uefa champions league", "uefa europa league", "uefa europa conference league",
                   "uefa conference league", "uefa super cup", "uefa youth league")


def world_category(league: str) -> str:
    """#112: Unibet's category for one of API-Football's country='World' competitions.
    Youth (U15–U23 / youth / junior) → 'international youth'; club competitions →
    'international clubs'; everything else (national teams: qualifiers, Nations League,
    friendlies, continental cups) → 'international'."""
    import re
    l = (league or "").lower()
    if any(k in l for k in _UEFA_CLUB_KEYS) or (l.startswith("uefa") and "super cup" in l):
        return "uefa club"
    if re.search(r"\bu(1[5-9]|2[0-3])\b", l) or "youth" in l or "junior" in l:
        return "international youth"
    if any(k in l for k in _WORLD_CLUB_KEYS):
        return "international clubs"
    return "international"


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
    if not tab:
        # UNIBET-TAB-NOT-LOADED (2026-09-25): no tab on unibet.ee (e.g. it ended up on about:blank
        # after a Chrome restart) — reuse any page tab; the origin check below loads unibet.ee in it.
        tab = next((t for t in (targets or []) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        c["reason"] = "no page tab open in CDP-Chrome (self-revive will open + log one in)"; return c

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
    # REFRESH-BY-KICKOFF (#112, 2026-09-24): one contest page per matched fixture is the
    # per-fixture cost; far fixtures are re-fetched only when their stored board is old
    # enough for their distance to kickoff (same tiers as Coolbet / Epicbet).
    from workers.automation.coolbet_explorer import _last_stored_by_match, refresh_due
    _last_ub = _last_stored_by_match(_BOOKMAKER, [f["id"] for f in fixtures])
    c["refresh_skipped"] = 0
    c["db_fixtures"] = len(fixtures)
    if not fixtures:
        c["reason"] = "no DB fixtures in window"; return c

    last_fetch = [0.0]
    consec_blocks = [0]
    # NEAR-KICKOFF-CAPTURE-2026-09-11: fixture -> contestKey pairings, persisted
    # after the sweep so near_kickoff_capture can fetch one fixture by key.
    mapped: list[tuple] = []

    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=40_000_000) as ws:
        nid = [0]

        # UNIBET-TAB-NOT-LOADED (2026-09-25): after the 00:18 Chrome restart the tab was LISTED
        # as unibet.ee (/json/list keeps the restored URL) while its document was about:blank.
        # Every injected fetch from about:blank fails with status 0 (no unibet origin / cookies),
        # the sweep read that as "session blocked?", and the feed auto-paused itself for 8 h.
        # Check the real document origin and reload the page once if it is not unibet.ee.
        async def _eval(expr: str, rid: int) -> object:
            await ws.send(json.dumps({"id": rid, "method": "Runtime.evaluate",
                                      "params": {"expression": expr, "returnByValue": True}}))
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < 15:
                try:
                    evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                except asyncio.TimeoutError:
                    continue
                if evt.get("id") == rid:
                    return ((evt.get("result") or {}).get("result") or {}).get("value")
            return None

        origin = await _eval("location.origin", 900001)
        if not (isinstance(origin, str) and origin.endswith("unibet.ee")):
            c["tab_reloaded"] = str(origin)
            await ws.send(json.dumps({"id": 900002, "method": "Page.navigate",
                                      "params": {"url": "https://www.unibet.ee/betting/odds"}}))
            for _ in range(20):  # up to ~20 s for the page to become usable
                await asyncio.sleep(1.0)
                if await _eval("location.origin.endsWith('unibet.ee') && document.readyState", 900003) in ("interactive", "complete"):
                    break
            log.warning("unibet-site: tab was on %r, reloaded unibet.ee before the sweep", origin)

        async def inj(url: str) -> dict | None:
            # rate-limit + cap + abort-on-repeated-block
            if c["fetches"] >= _RATE_MAX_FETCHES:
                return None
            try:
                footprint.check(_BOOKMAKER)
            except footprint.FootprintBudgetExceeded:
                c["budget_refused"] = c.get("budget_refused", 0) + 1
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
                    footprint.record(_BOOKMAKER, "error")
                    return None
                try:
                    d = json.loads(val)
                except Exception:  # noqa: BLE001
                    footprint.record(_BOOKMAKER, "error")
                    return None
                footprint.record(_BOOKMAKER, footprint.classify_status(d.get("s")))
                if d.get("s") != 200:
                    # keep the status: 0 = the fetch never got an answer (tab not on unibet.ee /
                    # network), 403/429 = a real block — "session blocked?" hid the difference.
                    c["last_status"] = d.get("s")
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
            st = c.get("last_status")
            c["reason"] = ("quickbrowse returned no country RNs "
                           + (f"(status {st}: {'no answer — tab not on unibet.ee or network down' if st == 0 else 'blocked'})"
                              if st is not None else "(empty answer)")); return c

        def country_of(rn: str) -> str:
            return rn.split(":", 1)[1].replace("_", " ")
        # #112 (2026-09-24): "esoccer" is simulated console football — never a real fixture.
        rn_list = [(rn, country_of(rn)) for rn in rns if country_of(rn).lower() != "esoccer"]
        log.info("unibet-site: %d top-level categories: %s", len(rn_list),
                 ", ".join(sorted(cn for _, cn in rn_list)))

        # group DB fixtures by COUNTRY and fuzzy-map each to a Unibet country RN.
        # UNIBET-WORLD (#112, 2026-09-24): fixtures whose country has no confident Unibet
        # category — above all AF's "World" (friendlies, Nations League, qualifiers: 28% of
        # the window, all skipped) — are regrouped by their COMPETITION name, which is
        # matched against the same category list below.
        def _country_rn(target: str):
            best, best_rn = 0, None
            for rn, cn in rn_list:
                sc = fuzz.token_set_ratio(target, cn.lower())
                if sc > best:
                    best, best_rn = sc, rn
            return best_rn if best_rn and best >= 80 else None
        groups: dict[str, list] = {}
        for f in fixtures:
            country = (f.get("country") or "").lower().strip()
            if country == "world":
                # #112 (2026-09-24): the competition-name fallback below mapped NONE of AF's
                # "World" fixtures ("uefa nations league" / "friendlies" never clear 80
                # against Unibet's category names; league_mapped stayed 0, 121 unmapped per
                # sweep). Unibet files them under three fixed categories — route explicitly.
                groups.setdefault("world:" + world_category(f.get("league") or ""), []).append(f)
            elif _country_rn(country):
                groups.setdefault(country, []).append(f)
            else:
                groups.setdefault("league:" + (f.get("league") or "").lower().strip(), []).append(f)

        for country, fx in groups.items():
            if consec_blocks[0] >= _RATE_ABORT_AFTER_BLOCKS:
                c["reason"] = "aborted — repeated non-200 (DataDome throttling); stopping to not hammer"; break
            if c["fetches"] >= _RATE_MAX_FETCHES:
                c["reason"] = f"hit fetch cap {_RATE_MAX_FETCHES}"; break
            target = (country or "").lower().strip()
            if target.startswith("world:"):
                want = target[len("world:"):]
                best_rn = next((rn for rn, cn in rn_list if cn.lower() == want), None)
                if not best_rn and want == "uefa club":
                    best_rn = next((rn for rn, cn in rn_list if cn.lower() == "international clubs"), None)
                if not best_rn:
                    c["world_unmapped"] = c.get("world_unmapped", 0) + len(fx)
                    continue
                c["world_mapped"] = c.get("world_mapped", 0) + len(fx)
            elif target.startswith("league:"):
                # competition-name fallback: "uefa nations league" / "friendlies" / "world cup
                # - qualification europe" against Unibet's category names
                best_rn = _country_rn(target[len("league:"):])
                if not best_rn:
                    c["league_unmapped"] = c.get("league_unmapped", 0) + len(fx)
                    continue
                c["league_mapped"] = c.get("league_mapped", 0) + len(fx)
            else:
                best_rn = _country_rn(target)
            if not best_rn:
                continue  # no confident country or competition match → skip
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
            # pass 1: pair every fixture of this country; ONE EVENT → ONE FIXTURE (#120,
            # 2026-09-24 — 41 Unibet events were paired with >1 fixture in 7 days)
            from workers.automation.coolbet_placer import unique_pairs
            cand_pairs = []
            for f in fx:
                ev = fuzzy_match_event(f["home"], f["away"], cands, f.get("date"), str(f["id"]))
                key = (ev or {}).get("raw", {}).get("contest_key") if ev else None
                if ev and key:
                    cand_pairs.append((f, {**ev, "id": key}))
            cand_pairs, _dup = unique_pairs(cand_pairs)
            c["dup_dropped"] = c.get("dup_dropped", 0) + _dup
            # pass 2: fetch
            for f, ev in cand_pairs:
                if consec_blocks[0] >= _RATE_ABORT_AFTER_BLOCKS or c["fetches"] >= _RATE_MAX_FETCHES:
                    break
                key = ev["id"]
                c["matched"] += 1
                mapped.append((str(f["id"]), key, ev.get("start") or None, ev.get("_match_score")))
                if not refresh_due(f["date"], _last_ub.get(str(f["id"])), datetime.now(timezone.utc)):
                    c["refresh_skipped"] += 1
                    continue
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
    if mapped and not dry_run:
        from workers.api_clients.supabase_client import record_book_events
        c["event_map_rows"] = record_book_events(_BOOKMAKER, mapped)
    if c["reason"] is None:
        c["reason"] = "ok"
    return c


def run_bulk(days: int = 2, dry_run: bool = False, limit: int | None = None,
             login: bool = True) -> dict:
    """Broad Unibet SITE odds sweep for DB fixtures within `days` → `Unibet-Site`.
    Rate-limited (env UNIBET_SITE_RATE_S / _MAX_FETCHES), fail-safe. Needs a
    unibet.ee tab in CDP-Chrome. Never raises.

    `login=False` (UNIBET-ON-VPS, 2026-09-23): skip the self-revive login. The
    prices are public — a logged-OUT tab returned the same prices as the Mac's
    logged-in one — and login is needed only to place. The VPS reader runs this
    way because its login is answered with a DataDome captcha."""
    import asyncio
    # UNIBET-SELF-REVIVE (2026-09-10): before sweeping, self-login if the CDP
    # unibet.ee session went logged-out — the same way the Coolbet daemon heals.
    # Root cause of the earlier "stale for hours": unibet_browser_sync didn't load
    # .env (creds invisible → auto-login no-op) + a login-button click race. Both
    # fixed; auto-login through DataDome works (verified logged_in ✓). Rate-limited
    # to once/30min. Never raises.
    heal = "skipped" if login else "not_attempted"
    if login:
        try:
            from workers.automation import unibet_browser_sync as ubs
            heal = ubs.ensure_logged_in(min_gap_min=30)
            if heal == "logged_in":
                log.info("unibet-site: session self-revived (logged back in)")
        except Exception as e:  # noqa: BLE001
            log.debug("unibet-site self-revive skipped (non-fatal): %s", e)
    try:
        res = asyncio.run(_async_run_bulk(days, limit, dry_run))
    except Exception as e:  # noqa: BLE001
        log.warning("unibet-site run_bulk failed: %s", e)
        res = {"reason": f"run_bulk error: {e}", "stored": 0}
    res["self_revive"] = heal
    # UNIBET-SITE-STALE-ALERT (2026-09-10): only page a human when self-revive
    # could NOT recover — i.e. the auto-login genuinely failed or creds are missing
    # (a real SMS/2FA/selector break), not on a routine logged-out tick that healed
    # itself. Deduped 3h so it can't spam; never raises.
    try:
        reason = (res or {}).get("reason") or ""
        stale = (res or {}).get("stored", 0) == 0 and ("unibet.ee tab" in reason or "page tab" in reason)
        if not dry_run and stale and heal in ("failed", "no_creds"):
            from workers.notify.telegram import send_telegram
            detail = ("auto-login FAILED (SMS/2FA or a changed login selector) — open unibet.ee "
                      "in CDP-Chrome (:9222) and log in by hand" if heal == "failed"
                      else "UNIBET_USER / UNIBET_PASS not set in .env — add them so the feed can self-login")
            send_telegram(
                f"🟠 Unibet-Site odds feed STALE and self-revive could not recover: {detail}. "
                "Until then the Unibet-Site sweep writes 0 rows and the best-price router can't route to Unibet.",
                dedup_key="unibet-site-no-tab", dedup_window_s=10800)
    except Exception as e:  # noqa: BLE001
        log.debug("unibet-site stale alert failed (non-fatal): %s", e)

    # ODDS-ARRIVAL HOOK (2026-09-11) — the Unibet half of what Coolbet's
    # `run_board_sweep` already does. Fresh Unibet prices have just landed, so
    # re-derive the candidates NOW rather than at the next :10/:40 generator
    # poll. This matters MORE on this side than on Coolbet's: Unibet sweeps
    # :15/:45 and the generators ran :10/:40, so a qualifying Unibet price
    # waited ~25 minutes every single time — and the mirrors price across BOTH
    # books, so a new Unibet quote can change which book wins a pick the bots
    # already hold.
    #
    # Guarded on `stored` because a swept-but-wrote-nothing tick (logged-out
    # tab, rate limit, dry run) changes no price and so cannot change any
    # decision. `on_odds_written` never raises — collecting odds is this
    # function's job and must survive a pick-generation failure.
    if not dry_run and (res or {}).get("stored", 0):
        from workers.automation.pick_generator import on_odds_written
        on_odds_written("Unibet-Site")

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
    try:
        footprint.check(_BOOKMAKER)
    except footprint.FootprintBudgetExceeded as e:
        log.warning("unibet-odds: %s", e)
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
                    footprint.record(_BOOKMAKER, "error")
                    return None
                d = json.loads(val)
                footprint.record(_BOOKMAKER, footprint.classify_status(d.get("s")))
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
    ap.add_argument("--no-login", action="store_true",
                    help="bulk: read logged-out, never attempt the self-revive login")
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
        print(json.dumps(run_bulk(days=args.days, dry_run=args.dry_run, limit=args.limit,
                                  login=not args.no_login),
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
