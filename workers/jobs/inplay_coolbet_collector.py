"""Coolbet IN-PLAY collector — the SECOND clock for the suspension-lead test.

WHY THIS EXISTS, precisely. Epicbet pulling its 1x2 market predicts a goal in the
next snapshot 19.06% of the time (n=934) against a 1.18% baseline — a 16x lift,
measured on boards we already held. That is a detector, not an edge: you cannot
bet a market that is suspended. It becomes an edge ONLY if a second book is still
quoting when the first pulls. This collector is that second book.

So the job is NOT "cover the Coolbet live board". It is "read the SAME fixtures
Epicbet is reading, as close in time as possible, and record who suspends first".
Breadth is Epicbet's job (0.13 s/fixture). Coolbet's job is to be a second clock.

THREE MEASURED FACTS THAT SHAPE EVERY DECISION HERE (2026-09-18):

  1. Coolbet costs ~6 s/fixture (3.1 s markets + 2.8 s odds) — 10-20x Epicbet.
     Hence: a small intersection set, not the whole board.
  2. Coolbet's shared FlareSolverr session SILENTLY returns another match's
     markets under parallel reads (INPLAY-VIABILITY-GATE defect (b)) — plausible
     numbers in the right shape, which downstream matching accepts without
     complaint. Hence: ONE serial reader, a DEDICATED session name, and an
     explicit assertion that the payload is for the match we asked for.
  3. The live sidebets call was pinned at limit=13, returning 8 groups / 12
     markets where limit=1000 returns 39 / 48. Fixed in coolbet_explorer on the
     same day; this collector depends on that fix.

REAL-MONEY SAFETY. `coolbet_prod` is the FlareSolverr session the real-money
placer uses; destroying or wedging it breaks placement (docs/COOLBET_RUNBOOK.md).
This collector hardcodes its own session name and must NEVER be pointed at that
one — pinned by the smoke test COOLBET-INPLAY-NEVER-PROD-SESSION. Nothing here
places a bet, reads a bankroll, or touches a placement gate.

OUTPUT. `inplay_book_quotes` with book='Coolbet', in the SAME nested shape the
Epicbet arm writes, so the two join on (match_id, captured_at). One difference is
deliberate and documented: Coolbet rows carry CANONICAL selection labels
(home/draw/away/over/under/yes/no) where Epicbet rows carry team names, because
Coolbet gives us stable locale-independent `result_key`s and Epicbet does not.
Readers that compare 3-way selections should go positionally, as
`scripts/inplay_trigger_sweep.py` already does.

    python3 -m workers.jobs.inplay_coolbet_collector --cadence 90
    python3 -m workers.jobs.inplay_coolbet_collector --once --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, execute_write, get_conn
from workers.automation.coolbet_explorer import (
    _MTID_1X2, _MTID_1X2_1H, _MTID_AH, _MTID_BTTS, _MTID_DC, _MTID_OU,
    fetch_match_markets, fetch_odds_for_markets,
)
from workers.automation.coolbet_session import CoolbetSession
from workers.jobs.inplay_collector import _seconds

log = logging.getLogger("inplay_coolbet")

BOOK = "Coolbet"

# NEVER "coolbet_prod" — that session belongs to the real-money placer.
FS_SESSION_NAME = "coolbet_inplay"

# Families we capture, keyed by market_type_id so the vocabulary is
# locale-independent (this file's own rule). Values are the `fam` labels the
# Epicbet arm already uses, so rows from the two books are directly comparable.
MTID_TO_FAM: dict[int, str] = (
    {m: "1x2" for m in _MTID_1X2}
    | {m: "ou" for m in _MTID_OU}
    | {m: "btts" for m in _MTID_BTTS}
    | {m: "dc" for m in _MTID_DC}
    | {m: "ah2" for m in _MTID_AH}
    | {m: "1x2_1h" for m in _MTID_1X2_1H}
)

# Coolbet `result_key` -> canonical selection label.
RESULT_KEY_TO_SEL = {
    "[Home]": "home", "Draw": "draw", "[Away]": "away",
    "Over": "over", "Under": "under", "Yes": "yes", "No": "no",
    "1X": "1x", "12": "12", "X2": "x2",
}


def _fam_for(mkt: dict) -> str | None:
    try:
        return MTID_TO_FAM.get(int(mkt.get("market_type_id")))
    except (TypeError, ValueError):
        return None


# Families that have no line at all. Coolbet reports line=0 for these; Epicbet
# reports None. Normalise to None so a reader filtering on `line` sees one shape
# across both books rather than a 0-vs-null trap.
_LINELESS_FAMS = {"1x2", "btts", "dc", "1x2_1h"}


def _line_for(mkt: dict, fam: str | None = None) -> float | None:
    if fam in _LINELESS_FAMS:
        return None
    for key in ("line", "raw_line"):
        v = mkt.get(key)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def board_for_match(session: CoolbetSession, cb_match_id: int) -> list[dict]:
    """Coolbet's live board for one match, in the Epicbet nested shape.

    SUSPENSION IS THE POINT of this collector, so it is recorded as DATA on every
    selection rather than by dropping the row — identical to the Epicbet arm's
    rule, and for the same reason: "the book pulled this market" is the single
    most informative state on the board."""
    markets = fetch_match_markets(session, cb_match_id, live=True)
    if not markets:
        return []
    odds_map = fetch_odds_for_markets(session, markets)

    by_key: dict[tuple, dict] = {}
    for mkt in markets:
        fam = _fam_for(mkt)
        if fam is None:
            continue
        sels: list[dict] = []
        for oc in (mkt.get("outcomes") or []):
            sel = RESULT_KEY_TO_SEL.get(oc.get("result_key"))
            if sel is None:
                continue
            try:
                entry = odds_map.get(int(oc.get("id")))
            except (TypeError, ValueError):
                entry = None
            entry = entry if isinstance(entry, dict) else {}
            raw = entry.get("value")
            try:
                price = float(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            if price is not None and price <= 1.0:
                price = None
            # A SELECTION WITH NO PRICE IS A PULLED SELECTION, AND THAT IS THE
            # MEASUREMENT. Dropping it would record "the book pulled this market"
            # as "this market does not exist", which is exactly the quantity this
            # collector was built to observe — and it would bias it in the
            # direction that makes the signal look weaker than it is. The Epicbet
            # arm writes `odds: null, suspended: true` for these (15,218 of 54,761
            # selections in a 20-minute window), so this must too or the two books
            # are not comparable. `status` is authoritative when present; a
            # missing price is itself evidence of suspension.
            suspended = (entry.get("status") or "").upper() == "SUSPENDED" or price is None
            sels.append({"sel": sel, "odds": price, "suspended": suspended})
        if not sels:
            continue
        # DEDUP. `fetch_match_markets` concatenates fo-match and sidebets, and the
        # headline markets (1x2, the league's main O/U, AH) are returned by BOTH —
        # 433 duplicated (row, fam, line) groups before this, against zero on the
        # Epicbet side. Any consumer counting families or suspensions would double
        # count. Keep the entry with the most selections, then the most priced
        # ones, so a fuller copy always wins over a truncated one.
        key = (fam, _line_for(mkt, fam))
        cand = {"fam": fam, "line": key[1], "sel": sels}
        prev = by_key.get(key)
        if prev is None or (
            len(sels), sum(1 for x in sels if x["odds"] is not None)
        ) > (
            len(prev["sel"]), sum(1 for x in prev["sel"] if x["odds"] is not None)
        ):
            by_key[key] = cand
    return list(by_key.values())


def live_targets(limit: int) -> list[dict]:
    """Fixtures that are live on AF *and* have a Coolbet event mapping.

    The intersection is the whole design: a Coolbet row nobody can compare to an
    Epicbet row at the same instant does nothing for the suspension-lead test."""
    from workers.jobs.inplay_af_prices import af_fixture_to_match_id
    from workers.jobs.inplay_epicbet_collector import af_state

    state = af_state() or {}
    live = [(afid, s) for afid, s in state.items() if s.get("minute") is not None]
    rows: list[dict] = []
    for afid, s in live:
        mid = af_fixture_to_match_id(afid)
        if not mid:
            continue
        rows.append({"af_fixture_id": afid, "match_id": mid, "state": s})
    if not rows:
        return []

    # League and team names come from the DB, NOT from af_state — that helper
    # returns only {minute, seconds, goals, af_age_s}, so reading s["league"] /
    # s["home"] silently yielded None and left those columns 0/77 populated while
    # Epicbet's were 54,453/54,453. Fetched here with the mapping, one query.
    mapped = execute_query(
        """SELECT b.match_id::text AS match_id, b.book_event_id,
                  l.name AS league, ht.name AS home, at.name AS away
             FROM book_event_map b
             JOIN matches m  ON m.id = b.match_id
             JOIN teams   ht ON ht.id = m.home_team_id
             JOIN teams   at ON at.id = m.away_team_id
        LEFT JOIN leagues l  ON l.id = m.league_id
            WHERE b.bookmaker = %s AND b.match_id = ANY(%s::uuid[])""",
        (BOOK, [r["match_id"] for r in rows]),
    )
    by_mid = {m["match_id"]: m for m in mapped}
    out = [r | {"cb_id": by_mid[r["match_id"]]["book_event_id"],
                "league": by_mid[r["match_id"]]["league"],
                "home": by_mid[r["match_id"]]["home"],
                "away": by_mid[r["match_id"]]["away"]}
           for r in rows if r["match_id"] in by_mid]
    # Prefer fixtures already deep enough to be interesting, then stable order.
    out.sort(key=lambda r: (-(r["state"].get("minute") or 0), r["match_id"]))
    return out[:limit]


def write_row(row: dict) -> int:
    return execute_write(
        """INSERT INTO inplay_book_quotes
               (captured_at, book, book_event_id, af_fixture_id, match_id, league,
                home_team, away_team, minute, seconds, score_home, score_away,
                af_age_s, markets)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (row["captured_at"], BOOK, str(row["book_event_id"]), row["af_fixture_id"],
         row["match_id"], row.get("league"), row.get("home"), row.get("away"),
         row.get("minute"), row.get("seconds"), row.get("score_home"),
         row.get("score_away"), row.get("af_age_s"), json.dumps(row["markets"])),
    )


def run(cadence: float, max_fixtures: int, once: bool, dry_run: bool) -> None:
    # One session, one reader, for the life of the process (see hazard 2).
    session = CoolbetSession(require_auth=False, fs_session_name=FS_SESSION_NAME)
    cycles = 0
    while True:
        t0 = time.time()
        # PER-CYCLE counters. They used to accumulate across cycles, so the log
        # read "rows 7, 14, 21, 28" — which looks like growing throughput and is
        # actually 7 rows every cycle, flat.
        written = errors = empty = 0

        # FOOTPRINT PAUSE. `daemons_paused` is the operator's global "stop
        # touching Coolbet" switch — the remedy when Imperva flags this Mac's IP,
        # which is the same condition that takes real-money placement down
        # (docs/COOLBET_RUNBOOK.md). This collector is KeepAlive and makes roughly
        # 32 requests a cycle from the SAME IP and the SAME FlareSolverr instance
        # as `coolbet_prod`, so if it keeps hammering through a pause it sustains
        # the exact escalation the pause exists to let decay. Using a separate FS
        # SESSION does not isolate the FOOTPRINT — that was the gap in this
        # collector's first version, which reasoned only about session names and
        # money primitives. Fails CLOSED, like every other reader of this switch.
        try:
            from workers.automation.coolbet_state import is_daemons_paused
            paused, why = is_daemons_paused()
        except Exception as e:                          # noqa: BLE001
            paused, why = True, f"cannot read daemons_paused ({e})"
        if paused:
            log.warning("daemons_paused — skipping cycle (%s)", why or "no reason given")
            if once:
                return
            time.sleep(max(0.0, cadence - (time.time() - t0)))
            continue

        targets = live_targets(max_fixtures)
        stamp = datetime.now(timezone.utc)
        for t in targets:
            # SERIAL on purpose. Do not parallelise this loop without giving each
            # reader its OWN named FS session — see hazard 2 in the module header.
            try:
                markets = board_for_match(session, int(t["cb_id"]))
            except Exception as e:                      # noqa: BLE001
                errors += 1
                log.warning("coolbet board %s failed: %s", t["cb_id"], str(e)[:160])
                continue
            if not markets:
                # NOT silent. "Coolbet does not carry this fixture live", "the
                # body was an Imperva interstitial" and "every family filtered
                # out" all arrive here and used to be indistinguishable from
                # success — the ledger's "0 looking normal" pattern. One live
                # target (event 6147933) produced zero rows in every cycle and
                # nothing said so.
                empty += 1
                log.info("empty board: cb=%s match=%s min=%s",
                         t["cb_id"], t["match_id"][:8], s.get("minute"))
                continue
            s = t["state"]
            goals = s.get("goals") or [None, None]
            row = {"captured_at": stamp, "book_event_id": t["cb_id"],
                   "af_fixture_id": t["af_fixture_id"], "match_id": t["match_id"],
                   "league": t.get("league"), "home": t.get("home"), "away": t.get("away"),
                   "minute": s.get("minute"),
                   # `seconds` was hardcoded None on a rig whose entire purpose is
                   # sub-minute lead timing. af_state carries it; store it.
                   "seconds": _seconds(s.get("seconds")),
                   "score_home": goals[0], "score_away": goals[1],
                   "af_age_s": s.get("af_age_s"), "markets": markets}
            if dry_run:
                fams = [(m["fam"], m["line"],
                         sum(1 for x in m["sel"] if x["suspended"]), len(m["sel"]))
                        for m in markets]
                print(f"  {t['match_id'][:8]} cb={t['cb_id']} min={s.get('minute')} "
                      f"fams={fams[:6]}{'...' if len(fams) > 6 else ''}")
            else:
                written += write_row(row)
        cycles += 1
        log.info("cycle %d | targets %d | rows %d | empty %d | errors %d | %.1fs",
                 cycles, len(targets), written, empty, errors, time.time() - t0)
        if once:
            return
        time.sleep(max(0.0, cadence - (time.time() - t0)))


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    # ~6 s/fixture serial: 8 fixtures is ~48 s, so 90 s leaves real headroom and
    # keeps us well clear of the Imperva budget. Raising either is a measured
    # decision, not a default.
    ap.add_argument("--cadence", type=float, default=90.0)
    ap.add_argument("--max-fixtures", type=int, default=8)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print boards, write nothing")
    a = ap.parse_args()
    run(a.cadence, a.max_fixtures, a.once, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
