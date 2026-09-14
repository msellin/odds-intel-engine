"""Epicbet IN-PLAY odds collector (INPLAY-VIABILITY-GATE-2026-09-14).

WHY EPICBET FIRST. Measured live 2026-09-14 against Coolbet and Unibet-Site:
  * anonymous REST — no session, no Imperva, no DataDome, nothing to expire;
  * thread-safe — Coolbet's shared FlareSolverr session silently returns ANOTHER
    match's markets when read in parallel, Epicbet has no such shared state;
  * fastest — 0.2-0.8s per fixture vs Coolbet ~4s and Unibet CDP calls that were
    observed hanging for 60s+;
  * deepest live board — 18 distinct O/U lines pooled and the only one of the
    three quoting a 2-way Asian handicap in play;
  * no Mac dependency — Unibet needs the operator's logged-in Chrome tab.

WHAT IT CAPTURES, AND WHY IT IS SHAPED THIS WAY.
One row per (fixture, instant), markets nested. A wide row per selection would
be ~40x the volume for the same information.

`suspended` is recorded as DATA, not as a missing row. Both goals observed on
2026-09-14 had the book pull its market BEFORE the score feed even reported the
goal — so "the market was closed" is one of the more informative states there is,
and a collector that simply writes nothing during a suspension destroys it.

Game state (minute, score) comes from API-Football `/odds/live`. ONLY its
`fixture.status` and `teams.*.goals` are used. Its PRICES are deliberately
ignored: measured the same day, AF's live odds are a median 40s stale (sawtooth
cache, ~34s refresh, tail to 11 minutes) and ~2pp wider than Epicbet. Its scores
and clock, however, are sound and are the cheapest event feed we have.

`af_age_s` is stored on every row so no future reader has to re-derive whether a
price was fresh — the exact question that could not be answered about the
existing 2.2M-row `live_match_snapshots` table.

OUTPUT. `--jsonl PATH` appends newline-delimited JSON (default, no DB needed).
Safe to run anywhere; nothing here writes to the database or places a bet.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

_BASE = "https://www.epicbet.com"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
_CATS = "/s/core-proxy/public/sport-base/foCategory.getByCountry"
_BYLEAGUE = "/s/core-proxy/public/sport-base/match.getFoByLeague"
_SIDEBETS = "/s/core-proxy/public/sport-base/match.getSidebets"
# The LIVE odds endpoint. NOT `activeOdds.getPreMatchByMarketIds` — that one
# answers 200 on an in-play fixture with its stale PRE-MATCH prices, which is a
# silent trap rather than an error.
_ODDS_LIVE = "/s/core-proxy/public/sport-odds/activeOdds.getLiveBetByMarketIds"

# Epicbet market-group ids -> our families. Kept deliberately wide: the owner's
# instruction is "collect all data, we build strategies later", so anything we
# can name is stored even if no current idea uses it.
GROUPS = {
    45: "1x2", 15: "ou", 19: "ah2", 29: "ah3", 69: "btts", 65: "dnb", 96: "dc",
    2055: "early_win", 413: "next_goal", 101: "corners_total", 86: "corners_ah",
    318: "corners_1x2", 79: "cards_total", 6: "ou_1h", 98: "1x2_1h",
    7: "team_total_home", 5: "team_total_away", 22: "correct_score",
    102: "corners_away", 133: "corners_home", 309: "corners_1h",
}
_ODDS_CHUNK = 250


class Epicbet:
    def __init__(self) -> None:
        self.s = requests.Session()
        # One fixture is fetched per thread, so the default pool of 10 thrashes
        # (and logs a warning per discarded connection) once the live board is
        # larger than that. Size it to the fixture cap.
        _ad = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
        self.s.mount("https://", _ad)
        self.s.headers.update({
            "User-Agent": _UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en;q=0.9,et;q=0.8",
            "Referer": f"{_BASE}/en/sports/football",
        })

    def get(self, path: str, payload=None, timeout: int = 25):
        url = _BASE + path
        if payload is not None:
            url += "?input=" + urllib.parse.quote(json.dumps(payload, separators=(",", ":")))
        r = self.s.get(url, timeout=timeout)
        r.raise_for_status()
        b = r.json()
        return b["result"].get("data") if isinstance(b, dict) and "result" in b else b

    def live_board(self) -> list[dict]:
        """Every football fixture Epicbet currently lists as in-play."""
        cats = self.get(_CATS, {"country": "EE", "language": "en", "isLiveBet": True}) or []
        out: list[dict] = []
        for c in cats:
            if (c.get("sport") or {}).get("id") != 1 or not c.get("league"):
                continue
            cid = (c.get("boCategory") or {}).get("id")
            if cid is None:
                continue
            try:
                d = self.get(_BYLEAGUE, {"leagueCategoryId": cid, "language": "en",
                                         "country": "EE", "offset": 0, "limit": 25,
                                         "period": "live"})
            except Exception as e:  # noqa: BLE001
                log.debug("league %s live listing failed: %s", cid, e)
                continue
            for m in (d or {}).get("matches", []):
                out.append({"eb_id": m["id"], "league": (c.get("boCategory") or {}).get("name"),
                            "home": m.get("homeTeamName"), "away": m.get("awayTeamName"),
                            "start": m.get("startDate")})
            time.sleep(0.05)
        return out

    def board_odds(self, eb_id: int) -> dict:
        """Full live board for one fixture: every named group, prices attached.

        A market present but unpriced is emitted with odds=None and
        suspended=True rather than dropped — see the module docstring.
        """
        sb = self.get(_SIDEBETS, {"matchId": eb_id, "language": "en",
                                  "country": "EE", "marketType": "main"})
        idx: dict[int, dict] = {}
        for g in (sb or {}).get("marketGroups", []):
            fam = GROUPS.get(g.get("id"))
            if not fam:
                continue
            for m in g.get("markets", []):
                if m.get("id") is None:
                    continue
                idx[m["id"]] = {
                    "fam": fam, "gid": g.get("id"), "line": m.get("line"),
                    "outs": [{"oid": o.get("id"), "sel": o.get("name")}
                             for o in (m.get("outcomes") or [])],
                }
        ids = list(idx)
        px: dict[int, dict] = {}
        for i in range(0, len(ids), _ODDS_CHUNK):
            try:
                d = self.get(_ODDS_LIVE, ids[i:i + _ODDS_CHUNK])
            except Exception as e:  # noqa: BLE001
                log.debug("live odds chunk failed for %s: %s", eb_id, e)
                continue
            for o in (d or []):
                px[o["outcomeId"]] = {"v": o.get("value"), "st": o.get("status"),
                                      "pr": o.get("product")}
        mkts = []
        for m in idx.values():
            sel = []
            for o in m["outs"]:
                p = px.get(o["oid"]) or {}
                open_ = p.get("st") == "open" and p.get("v")
                sel.append({"sel": o["sel"], "odds": float(p["v"]) if open_ else None,
                            "suspended": not open_, "product": p.get("pr")})
            if sel:
                mkts.append({"fam": m["fam"], "gid": m["gid"], "line": m["line"], "sel": sel})
        return {"home": (sb or {}).get("homeTeamName"),
                "away": (sb or {}).get("awayTeamName"), "markets": mkts}


def af_state() -> dict:
    """{af_fixture_id: {minute, seconds, goals, age_s}} from API-Football.

    Scores and clock ONLY. See the module docstring on why the prices are not read.
    """
    from workers.api_clients.api_football import get_live_odds
    now = time.time()
    out: dict[str, dict] = {}
    for it in (get_live_odds() or []):
        f = it.get("fixture") or {}
        st = f.get("status") or {}
        up = it.get("update")
        age = None
        if up:
            try:
                age = round(now - datetime.fromisoformat(up).timestamp(), 1)
            except Exception:  # noqa: BLE001
                age = None
        out[str(f.get("id"))] = {
            "minute": st.get("elapsed"), "seconds": st.get("seconds"),
            "goals": [(it.get("teams") or {}).get("home", {}).get("goals"),
                      (it.get("teams") or {}).get("away", {}).get("goals")],
            "af_age_s": age,
        }
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def match_af(board: list[dict], af: dict, af_names: dict) -> dict:
    """Epicbet fixture -> AF fixture id, by fuzzy team name."""
    from rapidfuzz import fuzz
    out = {}
    for b in board:
        best, bs = None, 0
        for afid, (h, a) in af_names.items():
            if afid not in af:
                continue
            s = (fuzz.partial_ratio(_norm(b["home"]), _norm(h))
                 + fuzz.partial_ratio(_norm(b["away"]), _norm(a))) / 2
            if s > bs:
                bs, best = s, afid
        if bs >= 80:
            out[b["eb_id"]] = best
    return out


def load_af_names() -> dict:
    """AF fixture id -> (home, away) for anything live, from our own DB."""
    try:
        from workers.api_clients.db import execute_query
        # Columns MUST be aliased: `ht.name` and `at2.name` both come back as
        # "name" in a dict row, so the away team silently overwrites the home
        # team and every fuzzy match then scores against the wrong pair.
        rows = execute_query(
            """SELECT m.api_football_id::text AS afid,
                      ht.name  AS home_name,
                      at2.name AS away_name
                 FROM matches m
                 JOIN teams ht  ON ht.id = m.home_team_id
                 JOIN teams at2 ON at2.id = m.away_team_id
                WHERE m.api_football_id IS NOT NULL
                  AND m.date > now() - interval '8 hours'
                  AND m.date < now() + interval '4 hours'""")
        out = {}
        for r in rows:
            if isinstance(r, dict):
                out[r["afid"]] = (r["home_name"], r["away_name"])
            else:
                out[r[0]] = (r[1], r[2])
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("AF name lookup failed (%s) — running without AF state", e)
        return {}


def run(jsonl: str, cadence: float, rediscover_s: float, max_fixtures: int,
        duration_s: float) -> None:
    eb = Epicbet()
    t_end = time.time() + duration_s
    board: list[dict] = []
    af_names = load_af_names()
    afmap: dict = {}
    last_disc = 0.0
    cycles = written = errors = 0
    while time.time() < t_end:
        t0 = time.time()
        if t0 - last_disc > rediscover_s:
            try:
                board = eb.live_board()[:max_fixtures]
                last_disc = t0
                if not af_names:
                    af_names = load_af_names()
                log.info("board refreshed: %d live fixtures", len(board))
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.warning("board refresh failed: %s", e)
        af = {}
        try:
            af = af_state()
            if board and af_names:
                afmap = match_af(board, af, af_names)
        except Exception as e:  # noqa: BLE001
            errors += 1
            log.debug("AF state failed: %s", e)

        res: dict = {}
        def grab(f):  # noqa: ANN001
            try:
                res[f["eb_id"]] = eb.board_odds(f["eb_id"])
            except Exception as e:  # noqa: BLE001
                res[f["eb_id"]] = {"error": str(e)[:120]}
        ths = [threading.Thread(target=grab, args=(f,)) for f in board]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

        stamp = datetime.now(timezone.utc).isoformat()
        with open(jsonl, "a") as fh:
            for f in board:
                d = res.get(f["eb_id"]) or {}
                if d.get("error"):
                    errors += 1
                    continue
                st = af.get(afmap.get(f["eb_id"])) or {}
                fh.write(json.dumps({
                    "captured_at": stamp, "book": "Epicbet",
                    "eb_id": f["eb_id"], "league": f["league"],
                    "home": f["home"], "away": f["away"], "start": f["start"],
                    "af_fixture_id": afmap.get(f["eb_id"]),
                    "minute": st.get("minute"), "seconds": st.get("seconds"),
                    "score": st.get("goals"), "af_age_s": st.get("af_age_s"),
                    "markets": d.get("markets") or [],
                }) + "\n")
                written += 1
        cycles += 1
        if cycles % 10 == 0:
            log.info("cycle %d | fixtures %d | rows %d | errors %d | %.1fs",
                     cycles, len(board), written, errors, time.time() - t0)
        time.sleep(max(0.0, cadence - (time.time() - t0)))
    log.info("DONE cycles=%d rows=%d errors=%d", cycles, written, errors)


def main() -> int:
    ap = argparse.ArgumentParser(description="Epicbet in-play odds collector")
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--cadence", type=float, default=30.0)
    ap.add_argument("--rediscover", type=float, default=300.0)
    ap.add_argument("--max-fixtures", type=int, default=30)
    ap.add_argument("--hours", type=float, default=1.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run(a.jsonl, a.cadence, a.rediscover, a.max_fixtures, a.hours * 3600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
