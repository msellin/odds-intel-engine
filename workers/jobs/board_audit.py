"""BOARD AUDIT ([[#120]], 2026-09-24) — re-screen boards ALREADY STORED for wrong-fixture
boards and home/away mirrors, and move the offending snapshots to quarantine.

WHY A READ-BACK AND NOT ONLY THE WRITE-TIME GUARDS. Both guards judge a book against
the OTHER books on the fixture at the moment of writing, and fail open when fewer than
four have priced it. A direct book often prices a fixture before the API-Football books
do (Epicbet ~32 h before kickoff, Pinnacle ~19 h), so its first boards land with no
evidence either way — and stay wrong once the evidence arrives. Measured 2026-09-24:
42 mirrored 1X2 sets in 5 days, including a Unibet-Site one written AFTER the mirror
guard shipped, and 14 whole wrong-fixture boards the mirror guard never looks at.

WHAT IT DOES, every 30 min, for fixtures kicking off between now−3 h and now+48 h:
  1. For each (fixture, book), judge the LATEST board (grouped by minute — Coolbet stamps
     every leg with its own microsecond timestamp) against the other books' latest
     quotes: `board_guard.board_offenses` (>= 2 markets off → wrong fixture) and
     `mirror_guard.is_mirrored` (1X2 transposed).
  2. Only for an offender, load that book's full history on the fixture and judge EVERY
     snapshot the same way, so only the snapshots that are wrong move.
  3. Move them: wrong-fixture → every row of that snapshot (all markets — team totals,
     corners and AH of the wrong match are wrong too); mirror → that snapshot's 1X2 rows.
     Copy to odds_snapshots_quarantined, then delete, in one transaction — reversible.
  4. One data_quality_findings row per (check, fixture, book) with the rows moved;
     Telegram when a book racks up >= 3 wrong-fixture boards in a day.

    python3 -m workers.jobs.board_audit --dry-run
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict

from datetime import timedelta

from workers.utils.board_guard import (CHECK_MARKETS, NEVER_JUDGED, PEER_MAX_AGE_H, _NOT_PEERS,
                                       _corroborated, _line_ok, board_offenses, is_wrong_board,
                                       record_finding, swapped_two_way)
from workers.utils.mirror_guard import consensus, is_mirrored

log = logging.getLogger(__name__)
ALERT_PER_BOOK_PER_DAY = 3


SNAP_GAP_S = 120   # rows of one book written within 2 min of each other are ONE snapshot


def _snapshots(rows) -> list[tuple]:
    """rows of ONE (fixture, book) → [(snapshot_ts, [rows])], grouped by time GAP, not by
    calendar minute: Coolbet writes one row at a time, each with its own now(), so a board
    that straddles a minute boundary used to split in two (review 2026-09-24)."""
    out, cur, last = [], [], None
    for r in sorted(rows, key=lambda r: r["timestamp"]):
        if last is not None and (r["timestamp"] - last).total_seconds() > SNAP_GAP_S:
            out.append((cur[-1]["timestamp"], cur))
            cur = []
        cur.append(r)
        last = r["timestamp"]
    if cur:
        out.append((cur[-1]["timestamp"], cur))
    return out


def _board_of(rows) -> dict:
    board: dict = {}
    for r in rows:
        if r["market"] in CHECK_MARKETS and _line_ok(r["market"], r["handicap_line"]):
            board.setdefault(r["market"], {})[r["sel"]] = float(r["odds"])
    return board


def _peers_asof(history: dict, book: str, t, *, allow_after: bool = False) -> dict:
    """Other books' latest quote per (market, sel) at or before `t`, no older than
    PEER_MAX_AGE_H — judging an August snapshot against September prices moved correct
    rows (7eb36ebb, review 2026-09-24)."""
    lo = t - timedelta(hours=PEER_MAX_AGE_H)
    hi = t + timedelta(hours=PEER_MAX_AGE_H) if allow_after else t
    out: dict = {}
    for (b, market, sel), series in history.items():
        if b == book:
            continue
        val = None
        for ts, odds in series:          # series sorted ascending
            if ts > hi:
                break
            if ts >= lo:
                val = odds
        if val is not None:
            out.setdefault(market, {}).setdefault(b, {})[sel] = val
    return out


OWN_BOARD_WINDOW_MIN = 60


def _own_asof(history: dict, book: str, t) -> dict:
    """This book's own latest quote per (market, sel) within OWN_BOARD_WINDOW_MIN before t.
    A snapshot often carries only part of the board (Coolbet/Epicbet write markets in
    separate passes); judging only its rows let a wrong 1X2 written 9 min after the rest
    of the wrong board through as a single-market fault (Chippenham, review #2)."""
    lo = t - timedelta(minutes=OWN_BOARD_WINDOW_MIN)
    out: dict = {}
    for (b, market, sel), series in history.items():
        if b != book:
            continue
        val = None
        for ts, odds in series:
            if ts > t:
                break
            if ts >= lo:
                val = odds
        if val is not None:
            out.setdefault(market, {})[sel] = val
    return out


def _judge(board: dict, peers: dict) -> dict:
    """→ {"wrong": [offenses] | None, "mirror": bool, "swapped": [markets]}"""
    offenses = board_offenses(board, peers)
    verdict = {"wrong": offenses if is_wrong_board(offenses) else None, "mirror": False,
               "swapped": [] if is_wrong_board(offenses) else swapped_two_way(board, peers)}
    t = board.get("1x2") or {}
    if not verdict["wrong"] and {"home", "draw", "away"} <= t.keys():
        refs = list({(q["home"], q["draw"], q["away"]) for q in (peers.get("1x2") or {}).values()
                     if {"home", "draw", "away"} <= q.keys()})
        verdict["mirror"] = (is_mirrored(t["home"], t["draw"], t["away"], consensus(refs))
                             # a book we scrape ourselves showing the same triple is independent
                             # evidence of a real move, not a transposition (de810545, review)
                             and not _corroborated(t, ("home", "draw", "away"), peers.get("1x2") or {}))
    return verdict


def run(*, dry_run: bool = False, back_h: float = 3, ahead_h: float = 48) -> dict:
    from workers.api_clients.db import execute_query, get_conn
    c = defaultdict(int)
    latest = execute_query(
        """SELECT DISTINCT ON (o.match_id, o.bookmaker, o.market, o.selection)
                  o.match_id::text AS mid, o.bookmaker AS book, o.market, lower(o.selection) AS sel,
                  o.odds::float AS odds, o.handicap_line, o.timestamp
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE m.date BETWEEN now() - make_interval(hours => %s) AND now() + make_interval(hours => %s)
              AND o.market = ANY(%s) AND NOT (o.bookmaker = ANY(%s))
              AND COALESCE(o.is_live, false) = false AND o.timestamp <= m.date
              AND o.timestamp > m.date - interval '3 days'
            ORDER BY o.match_id, o.bookmaker, o.market, o.selection, o.timestamp DESC""",
        (int(back_h), int(ahead_h), list(CHECK_MARKETS), list(_NOT_PEERS))) or []
    by_fix: dict = defaultdict(lambda: defaultdict(list))
    for r in latest:
        by_fix[r["mid"]][r["book"]].append(r)
    candidates = []
    for mid, books in by_fix.items():
        c["fixtures"] += 1
        hist_latest = defaultdict(list)
        for book, rows in books.items():
            for r in rows:
                hist_latest[(book, r["market"], r["sel"])].append((r["timestamp"], float(r["odds"])))
        for book, rows in books.items():
            if book in NEVER_JUDGED:
                continue
            c["boards"] += 1
            t = max(r["timestamp"] for r in rows)
            # PRE-FILTER only: peers' LATEST quotes are usually written after this book's
            # last snapshot, so accept ±PEER_MAX_AGE_H here. The verdict that MOVES rows
            # below is strictly as-of each snapshot.
            v = _judge(_board_of(rows), _peers_asof(hist_latest, book, t, allow_after=True))
            if v["wrong"] or v["mirror"] or v["swapped"]:
                candidates.append((mid, book))
    for mid, book in candidates:
        # full pre-match history of the fixture, every book, bounded to 3 days before kickoff
        hist = execute_query(
            """SELECT o.id, o.bookmaker AS book, o.market, lower(o.selection) AS sel,
                      o.odds::float AS odds, o.handicap_line, o.timestamp
                 FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                WHERE o.match_id = %s AND COALESCE(o.is_live, false) = false
                  AND o.timestamp <= m.date AND o.timestamp > m.date - interval '3 days'
                  AND NOT (o.bookmaker = ANY(%s))""", (mid, list(_NOT_PEERS))) or []
        series = defaultdict(list)
        for r in hist:
            if r["market"] in CHECK_MARKETS and _line_ok(r["market"], r["handicap_line"]):
                series[(r["book"], r["market"], r["sel"])].append((r["timestamp"], float(r["odds"])))
        for k in series:
            series[k].sort()
        mine = [r for r in hist if r["book"] == book]
        move_ids, wrong_snaps, mirror_snaps, swap_snaps = [], 0, 0, 0
        wrong_ex = swap_ex = None
        for t, snap in _snapshots(mine):
            # judge the book's board AS IT STOOD at t (its own last hour), not only the rows
            # this snapshot happens to carry
            v = _judge(_own_asof(series, book, t), _peers_asof(series, book, t))
            snap_markets = {r["market"] for r in snap}
            if v["wrong"]:
                wrong_snaps += 1
                wrong_ex = wrong_ex or v["wrong"]
                move_ids += [r["id"] for r in snap]            # every market of the wrong match
                continue
            if v["mirror"] and "1x2" in snap_markets:
                mirror_snaps += 1
                move_ids += [r["id"] for r in snap if r["market"] == "1x2"]
            if v["swapped"] and snap_markets & set(v["swapped"]):
                swap_snaps += 1
                swap_ex = swap_ex or v["swapped"]
                move_ids += [r["id"] for r in snap if r["market"] in v["swapped"]]
        example = wrong_ex if wrong_snaps else swap_ex
        if not move_ids:
            continue
        check = ("wrong_fixture_board" if wrong_snaps else
                 "mirrored_1x2" if mirror_snaps else "swapped_two_way")
        c[check] += 1
        move_ids = [str(i) for i in move_ids]
        c["rows_to_move"] += len(move_ids)
        if dry_run:
            log.info("DRY %s %s/%s: %d snapshots, %d rows %s", check, book, mid,
                     wrong_snaps or mirror_snaps or swap_snaps, len(move_ids), example or "")
            continue
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO odds_snapshots_quarantined
                         (match_id, bookmaker, market, selection, odds, timestamp, is_closing,
                          minutes_to_kickoff, is_live, handicap_line, is_opening, original_id,
                          quarantine_reason, quarantined_at)
                       SELECT match_id, bookmaker, market, selection, odds, timestamp, is_closing,
                              minutes_to_kickoff, is_live, handicap_line, is_opening, id, %s, now()
                         FROM odds_snapshots WHERE id = ANY(%s::uuid[])""",
                    (f"board-audit 2026-09-24: {check}" + (f" — {example}" if example else ""), move_ids))
                cur.execute("DELETE FROM odds_snapshots WHERE id = ANY(%s::uuid[])", (move_ids,))
            conn.commit()
        c["rows_moved"] += len(move_ids)
        record_finding(check, mid, book, {"where": "read-back", "wrong_snapshots": wrong_snaps,
                                          "mirror_snapshots": mirror_snaps, "swap_snapshots": swap_snaps,
                                          "offenses": example}, len(move_ids))
    if not dry_run:
        _alert()
    log.info("board-audit: %s", dict(c))
    return dict(c)


def _alert() -> None:
    try:
        from workers.api_clients.db import execute_query
        from workers.notify.telegram import send_telegram
        rows = execute_query(
            """SELECT f.bookmaker, count(*) n FROM data_quality_findings f
                 JOIN matches m ON m.id = f.match_id
                WHERE f.check_name = 'wrong_fixture_board' AND f.found_at > now() - interval '24 hours'
                  -- CURRENT fixtures only: the one-off 5-day clean-up of 2026-09-24 recorded 12
                  -- findings on matches of 19-22 Sept and paged "12 wrong-fixture boards in
                  -- 24 h" although nothing current was wrong. The alert means "the matcher is
                  -- failing today", so it counts only matches kicking off from 24 h ago onward.
                  AND m.date > now() - interval '24 hours'
                GROUP BY 1 HAVING count(*) >= %s""", (ALERT_PER_BOOK_PER_DAY,)) or []
        for r in rows:
            send_telegram(f"🟠 <b>{r['bookmaker']}</b>: {r['n']} wrong-fixture boards in 24 h "
                          f"(another match's prices under our fixture) — quarantined. "
                          f"The book's matcher is pairing the wrong events (#120).",
                          dedup_key=f"wrong-board-{r['bookmaker']}", dedup_window_s=86400)
    except Exception as e:  # noqa: BLE001
        log.debug("board-audit alert failed: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--back-hours", type=float, default=3)
    ap.add_argument("--ahead-hours", type=float, default=48)
    a = ap.parse_args()
    print(run(dry_run=a.dry_run, back_h=a.back_hours, ahead_h=a.ahead_hours))
