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


def main() -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Capture Unibet SITE odds for one event (Unibet-Site).")
    ap.add_argument("--event", required=True, help="unibet.ee event page URL")
    ap.add_argument("--match-id", help="DB match id to store under")
    ap.add_argument("--write", action="store_true", help="write rows to odds_snapshots")
    ap.add_argument("--minutes", type=int, default=None, help="minutes to kickoff")
    args = ap.parse_args()
    res = fetch_event_odds(args.event, match_id=args.match_id, write=args.write,
                           minutes_to_kickoff=args.minutes)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
