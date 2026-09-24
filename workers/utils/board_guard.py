"""BOARD-GUARD ([[#120]] WRONG-FIXTURE-BOARDS, 2026-09-24) — refuse a book's WHOLE board
when it is another match's prices filed under our fixture.

WHY THIS EXISTS. The observatory data-quality audit (5 days) found 14 (fixture, book)
pairs — 10 of them Epicbet — where a book's entire board belonged to a DIFFERENT match:
Trans Narva v Levadia stored Epicbet home 1.95 against a 10.06 consensus AND every O/U
line reversed; Worcester v Banbury stored 1.03 / 23 / 81 against ~2.3 / 2.9 / 2.9; a
Kyrgyz U23 side was paired with two different opponents two days apart. ~8 shadow bets
were struck on those boards, and the owner copies shadow picks with real money.

`mirror_guard.drop_mirrored_1x2` did not stop it, by design: it strips only the three
1X2 legs ("O/U and BTTS have no home/away to swap"). That is right for a home/away
TRANSPOSITION, where the rest of the board is the correct match — and wrong for a
wrong-fixture pairing, where every market is someone else's. The two faults need
different remedies, so this is a separate test.

THE TEST. For each market we can compare (1X2, O/U 1.5 / 2.5 / 3.5, BTTS), take the
median price of each selection over >= MIN_PEERS other books (latest pre-match quote,
same line — a mislabelled line is excluded, see `_line_ok`). The book is OFF in a market
when any leg is more than GUARD_RATIO away from that median. A board OFF in >=
MIN_OFF_MARKETS markets is a wrong-fixture board: every row is refused (quarantined,
never silently dropped). One market off is a single-market fault and is left to the
existing guards. Two independent markets agreeing that the book is somewhere else is
strong evidence; a genuine late price move shifts one market, not the whole board.

TWO PLACES, SAME TEST:
  * WRITE time (`screen_board`) in the direct-book writers — the board never lands.
  * READ-BACK (`workers/jobs/board_audit.py`, every 30 min) over boards already stored,
    because a board written before enough peers had priced the fixture passed with no
    evidence (fail-open), and becomes judgeable later. That was the measured "leak".

Never raises: a guard that can fail a write is worse than the fault it prevents.
"""
from __future__ import annotations

import logging
from statistics import median

log = logging.getLogger(__name__)

GUARD_RATIO = 1.5625          # 1.25² — the repo's one definition of "impossible price" (§9)
# ...AND the leg must be this far off in implied probability. A ratio alone is noisy at
# long odds: William Hill 81.0 vs a 45.0 median is x1.80 but 1 prob-point — ordinary
# longshot disagreement, not another match (first dry run, 2026-09-24).
MIN_PROB_GAP = 0.04
MIN_PEERS = 4                 # the mirror-guard quorum
MIN_OFF_MARKETS = 2
CHECK_MARKETS = {"1x2": ("home", "draw", "away"), "over_under_15": ("over", "under"),
                 "over_under_25": ("over", "under"), "over_under_35": ("over", "under"),
                 "btts": ("yes", "no")}
# SINGLE-MARKET CHECK (#123, 2026-09-24). The whole-board rule above needs >= 2 markets off,
# so a board that is wrong in ONE market passes it: Coolbet BTTS-yes stored at 11.0 / 4.5
# against a ~50% consensus (29 Coolbet + 40 Epicbet fixtures >15 pp off), and Asian-handicap
# rows far off the same line elsewhere. BTTS and each AH LINE are judged on their own against
# a >= MIN_PEERS median: a leg more than SINGLE_MARKET_PROB_GAP off in implied probability is
# refused (that market only). Probability, not ratio — at AH's ~1.9 prices a real 15 pp miss
# is only x1.3. Within NEAR_KO_MIN of kickoff an AH line must agree to NEAR_KO_AH_PROB_GAP
# (in-play / wrong-kickoff rows cluster there — handover §3). An independently scraped direct
# book showing the same prices still corroborates (a real move), as in the whole-board rule.
# SCOPE: judged only for the books WE scrape and bet (DIRECT_BOOKS). A dry run over 72 h
# (2026-09-24) flagged 33 boards / 653 rows, nearly all API-Football books' AH lines where
# two book families disagree with each other on quarter lines (Bet365+Betano one way,
# 1xBet+Marathonbet the other) — a 4–6-book median is no referee there. The direct-book hits
# were few and plainly wrong (Coolbet BTTS-yes 2.6 vs 1.85, Epicbet 3.4 vs 1.82). AF books
# still count as PEERS.
SINGLE_MARKET_PROB_GAP = 0.15
NEAR_KO_AH_PROB_GAP = 0.10
NEAR_KO_MIN = 30
AH_SIDES = ("home", "away")


def ah_key(line) -> str | None:
    """Board key for one Asian-handicap line (handicap_line = the HOME line on both
    selections at every book we store — verified in the #119 (E) handover §2.3)."""
    try:
        return f"ah:{float(line):+g}"
    except (TypeError, ValueError):
        return None


_NOT_PEERS = ("Max", "Avg", "Betfair Exchange", "BetWin", "Betfred", "Unibet",
              "Unibet-Kambi", "Coolbet-OddsAPI")
# REVIEW FIXES (2026-09-24, independent review of #120):
# * Books we scrape OURSELVES, each with its own matcher. When one of them independently
#   agrees with the judged board, the board is corroborated, not wrong: on de810545
#   Epicbet and Unibet-Site moved together on real news while the AF books sat frozen.
DIRECT_BOOKS = frozenset({"Coolbet", "Epicbet", "Unibet-Site", "Tonybet"})
# * Pinnacle is the reference line; it is never itself judged by a soft-book median.
NEVER_JUDGED = frozenset({"Pinnacle"})
PEER_MAX_AGE_H = 6        # a peer quote older than this is not evidence about now


def _line_ok(market: str, line) -> bool:
    """An over_under_* row must carry its label's line (or none) — see
    odds_quality.ou_line_matches (1xBet over_under_25 rows carrying 0.25)."""
    from workers.utils.odds_quality import ou_line_matches
    return ou_line_matches(market, line)


def board_offenses(board: dict[str, dict[str, float]],
                   peers: dict[str, dict[str, dict[str, float]]]) -> list[tuple[str, str]]:
    """Pure. board = {market: {sel: odds}} for ONE book on ONE fixture;
    peers = {market: {other_book: {sel: odds}}}. → [(market, detail)] of OFF markets."""
    out = []
    for market, sides in CHECK_MARKETS.items():
        b = board.get(market) or {}
        if not all(s in b and b[s] and b[s] > 1.0 for s in sides):
            continue
        full = [q for q in (peers.get(market) or {}).values()
                if all(s in q and q[s] and q[s] > 1.0 for s in sides)]
        # identical feeds (1xBet and Marathonbet are often byte-identical) are ONE opinion
        full = list({tuple(q[s] for s in sides): q for q in full}.values())
        if len(full) < MIN_PEERS:
            continue
        med = {s: median(q[s] for q in full) for s in sides}
        off = [(s, max(b[s] / med[s], med[s] / b[s])) for s in sides
               if max(b[s] / med[s], med[s] / b[s]) > GUARD_RATIO
               and abs(1.0 / b[s] - 1.0 / med[s]) > MIN_PROB_GAP]
        if off and _corroborated(b, sides, (peers.get(market) or {})):
            off = []   # another book we scrape ourselves independently shows these prices
        if off:
            worst_side, worst = max(off, key=lambda x: x[1])
            out.append((market, f"{worst_side} {b[worst_side]} vs {len(full)}-book median "
                                f"{med[worst_side]:.2f} (x{worst:.2f})"))
    return out


def single_market_offenses(board: dict, peers: dict,
                           minutes_to_kickoff: int | None = None) -> list[tuple[str, str]]:
    """Pure. → [(market_key, detail)] for BTTS / AH lines whose price is far from a
    >= MIN_PEERS median on its own (see SINGLE_MARKET_PROB_GAP)."""
    out = []
    near_ko = minutes_to_kickoff is not None and 0 <= minutes_to_kickoff <= NEAR_KO_MIN
    for key, b in board.items():
        if key == "btts":
            sides, gap = ("yes", "no"), SINGLE_MARKET_PROB_GAP
        elif key.startswith("ah:"):
            sides = AH_SIDES
            gap = NEAR_KO_AH_PROB_GAP if near_ko else SINGLE_MARKET_PROB_GAP
        else:
            continue
        if not all(b.get(s, 0) > 1.0 for s in sides):
            continue
        full = [q for q in (peers.get(key) or {}).values() if all(q.get(s, 0) > 1.0 for s in sides)]
        full = list({tuple(q[s] for s in sides): q for q in full}.values())
        if len(full) < MIN_PEERS:
            continue
        med = {s: median(q[s] for q in full) for s in sides}
        off = [(s, abs(1.0 / b[s] - 1.0 / med[s])) for s in sides if abs(1.0 / b[s] - 1.0 / med[s]) > gap]
        if off and _corroborated(b, sides, peers.get(key) or {}):
            off = []
        if off:
            s_, g_ = max(off, key=lambda x: x[1])
            out.append((key, f"{s_} {b[s_]} vs {len(full)}-book median {med[s_]:.2f} "
                             f"({g_ * 100:.0f} pp > {gap * 100:.0f})"))
    return out


CORROBORATE_RATIO = 1.15   # within 15% on EVERY leg — books' margins differ (Al-Rayyan v Qatar SC: Epicbet draw 4.51 vs Unibet 4.0 on the same real move); the mirror-closeness check below is what stops a book vouching for a mirror


def _corroborated(b: dict, sides, market_peers: dict) -> bool:
    """True when another book WE scrape independently shows the same prices as `b`.

    Tightened after review #2 (2026-09-24). The first version reused the "off" band
    (×1.5625 / 4 prob-pts), so a correctly oriented book could vouch for a genuine mirror,
    and two WRONGLY paired books could vouch for each other (4f6992cc: Epicbet paired to
    Koper v Nafta and Unibet-Site to Persebaya "agreed", while Coolbet — the one correctly
    paired direct book — contradicted both). Now:
      * agreement = within CORROBORATE_RATIO on every leg;
      * for 1X2 the corroborator must be closer to `b` than to `b` with home/away swapped;
      * any OTHER direct book that contradicts `b` (off by the guard's own rule) voids it.
    """
    def off(x, q):
        return any(max(x[s] / q[s], q[s] / x[s]) > GUARD_RATIO and abs(1 / x[s] - 1 / q[s]) > MIN_PROB_GAP
                   for s in sides)
    direct = {bk: q for bk, q in market_peers.items()
              if bk in DIRECT_BOOKS and all(q.get(s, 0) > 1.0 for s in sides)}
    if any(off(b, q) for q in direct.values()):
        agrees = [q for q in direct.values() if not off(b, q)]
        contradicts = [q for q in direct.values() if off(b, q)]
        if contradicts and agrees:
            return False            # direct books disagree among themselves: no corroboration
    for q in direct.values():
        if not all(max(b[s] / q[s], q[s] / b[s]) <= CORROBORATE_RATIO for s in sides):
            continue
        if set(sides) == {"home", "draw", "away"}:
            mirror = {"home": b["away"], "draw": b["draw"], "away": b["home"]}
            d_b = sum(abs(1 / q[s] - 1 / b[s]) for s in sides)
            d_m = sum(abs(1 / q[s] - 1 / mirror[s]) for s in sides)
            if d_b >= d_m:
                continue
        return True
    return False


def is_wrong_board(offenses: list) -> bool:
    return len(offenses) >= MIN_OFF_MARKETS


# ── TWO-WAY SWAP (#121 phase 1, 2026-09-24) ────────────────────────────────────
# The O/U / BTTS counterpart of the 1X2 mirror: over and under (yes and no) stored the
# wrong way round while the rest of the board is right — 22 in 5 days, Epicbet 15. The
# test is STRUCTURAL like mirror_guard's: swapping the two sides must reconcile the pair
# with the consensus, the straight reading must not, and the consensus must not be a
# coin-flip (a 50/50 market has no power).
SWAP_TOL = 0.03          # |p_book − (1 − p_consensus)| after the swap
SWAP_MIN_STRAIGHT = 0.12 # |p_book − p_consensus| as stored
SWAP_MIN_SEPARATION = 0.08  # |p_consensus − 0.5|
TWO_WAY = {m: s for m, s in CHECK_MARKETS.items() if len(s) == 2}


def swapped_two_way(board: dict, peers: dict) -> list[str]:
    """Pure → the two-way markets whose sides are transposed vs a >= MIN_PEERS consensus."""
    out = []
    for market, (a, b) in TWO_WAY.items():
        q = board.get(market) or {}
        full = [v for v in (peers.get(market) or {}).values() if v.get(a, 0) > 1 and v.get(b, 0) > 1]
        if not (q.get(a, 0) > 1 and q.get(b, 0) > 1) or len(full) < MIN_PEERS:
            continue
        def p_first(x):
            return (1 / x[a]) / (1 / x[a] + 1 / x[b])
        pc = median(p_first(v) for v in full)
        pb = p_first(q)
        if (abs(pc - 0.5) >= SWAP_MIN_SEPARATION and abs(pb - pc) >= SWAP_MIN_STRAIGHT
                and abs(pb - (1 - pc)) <= SWAP_TOL):
            out.append(market)
    return out


def peer_boards(match_id: str, bookmaker: str) -> dict:
    """Latest pre-match quote per OTHER book for the checked markets (3 days). Never raises."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT DISTINCT ON (bookmaker, market, selection, handicap_line)
                      bookmaker, market, lower(selection) AS sel, odds::float AS odds, handicap_line
                 FROM odds_snapshots
                WHERE match_id = %s AND market = ANY(%s) AND bookmaker <> %s
                  AND NOT (bookmaker = ANY(%s)) AND COALESCE(is_live, false) = false
                  AND timestamp > now() - make_interval(hours => %s)
                ORDER BY bookmaker, market, selection, handicap_line, timestamp DESC""",
            (match_id, list(CHECK_MARKETS) + ["asian_handicap"], bookmaker, list(_NOT_PEERS),
             PEER_MAX_AGE_H)) or []
    except Exception as e:  # noqa: BLE001 — fail open
        log.debug("board-guard: peer lookup failed for %s: %s", match_id, e)
        return {}
    out: dict = {}
    for r in rows:
        if r["market"] == "asian_handicap":
            k = ah_key(r["handicap_line"])
            if k:
                out.setdefault(k, {}).setdefault(r["bookmaker"], {})[r["sel"]] = r["odds"]
        elif _line_ok(r["market"], r["handicap_line"]):
            out.setdefault(r["market"], {}).setdefault(r["bookmaker"], {})[r["sel"]] = r["odds"]
    return out


def board_from_rows(rows, market_of, selection_of, odds_of, line_of=lambda r: None) -> dict:
    board: dict = {}
    for r in rows:
        m = market_of(r)
        key = ah_key(line_of(r)) if m == "asian_handicap" else (
            m if m in CHECK_MARKETS and _line_ok(m, line_of(r)) else None)
        if key:
            try:
                board.setdefault(key, {})[str(selection_of(r)).lower()] = float(odds_of(r))
            except (TypeError, ValueError):
                continue
    return board


def quarantine_rows(match_id: str, bookmaker: str, rows, market_of, selection_of, odds_of,
                    line_of, reason: str, minutes_to_kickoff: int | None = None) -> None:
    """Park every refused row in odds_snapshots_quarantined. Never raises."""
    try:
        from workers.api_clients.db import get_conn
        vals = [(match_id, bookmaker, market_of(r), selection_of(r), odds_of(r), line_of(r),
                 minutes_to_kickoff, reason) for r in rows]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO odds_snapshots_quarantined
                       (match_id, bookmaker, market, selection, odds, handicap_line, timestamp,
                        is_live, minutes_to_kickoff, quarantine_reason, quarantined_at)
                       VALUES (%s, %s, %s, %s, %s, %s, now(), false, %s, %s, now())""", vals)
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("board-guard: quarantine write failed for %s/%s: %s", match_id, bookmaker, e)


def record_finding(check: str, match_id: str, bookmaker: str, detail: dict,
                   rows_moved: int = 0) -> None:
    """One row per detection in data_quality_findings (migration 398). Never raises."""
    try:
        import json
        from workers.api_clients.db import execute_write
        execute_write(
            """INSERT INTO data_quality_findings (check_name, match_id, bookmaker, detail, rows_moved)
               VALUES (%s, %s, %s, %s::jsonb, %s)""",
            (check, match_id, bookmaker, json.dumps(detail, default=str), rows_moved))
    except Exception as e:  # noqa: BLE001
        log.debug("board-guard: finding write failed: %s", e)


def screen_board(match_id: str, bookmaker: str, rows, market_of, selection_of, odds_of,
                 line_of=lambda r: None, minutes_to_kickoff: int | None = None,
                 peers: dict | None = None):
    """Write-time screen for one (fixture, book) batch. Returns the rows to store —
    all of them, or NONE when the board is another match's."""
    try:
        if bookmaker in NEVER_JUDGED:
            return rows
        board = board_from_rows(rows, market_of, selection_of, odds_of, line_of)
        if not board:
            return rows
        peers = peers if peers is not None else peer_boards(match_id, bookmaker)
        offenses = board_offenses(board, peers)
        if not is_wrong_board(offenses):
            swapped = swapped_two_way(board, peers)
            if swapped:
                bad = [r for r in rows if market_of(r) in swapped]
                reason = f"board-guard 2026-09-24: two-way sides transposed vs consensus in {', '.join(swapped)}"
                log.warning("board-guard: refusing %s %s on %s — sides swapped", bookmaker, swapped, match_id)
                quarantine_rows(match_id, bookmaker, bad, market_of, selection_of, odds_of, line_of,
                                reason, minutes_to_kickoff)
                record_finding("swapped_two_way", match_id, bookmaker,
                               {"markets": swapped, "where": "write"}, len(bad))
                rows = [r for r in rows if market_of(r) not in swapped]
            # #123: BTTS / AH-line faults the whole-board rule cannot see on its own
            single = (single_market_offenses(board, peers, minutes_to_kickoff)
                      if bookmaker in DIRECT_BOOKS else [])
            if single:
                keys = {k for k, _ in single}
                def _key(r):
                    m = market_of(r)
                    return ah_key(line_of(r)) if m == "asian_handicap" else m
                bad = [r for r in rows if _key(r) in keys]
                if bad:
                    reason = ("board-guard 2026-09-24: single market off consensus — "
                              + "; ".join(f"{k}: {d}" for k, d in single))
                    log.warning("board-guard: refusing %s %s on %s — %s", bookmaker, sorted(keys), match_id, reason)
                    quarantine_rows(match_id, bookmaker, bad, market_of, selection_of, odds_of, line_of,
                                    reason, minutes_to_kickoff)
                    record_finding("single_market_off", match_id, bookmaker,
                                   {"offenses": single, "where": "write"}, len(bad))
                    rows = [r for r in rows if _key(r) not in keys]
            return rows
    except Exception as e:  # noqa: BLE001 — fail open
        log.debug("board-guard: screen failed for %s/%s: %s", match_id, bookmaker, e)
        return rows
    reason = ("board-guard 2026-09-24: whole board refused — wrong fixture? off in "
              + "; ".join(f"{m}: {d}" for m, d in offenses))
    log.warning("board-guard: refusing %s board on %s — %s", bookmaker, match_id, reason)
    quarantine_rows(match_id, bookmaker, rows, market_of, selection_of, odds_of, line_of,
                    reason, minutes_to_kickoff)
    record_finding("wrong_fixture_board", match_id, bookmaker,
                   {"offenses": offenses, "where": "write", "rows": len(rows)}, len(rows))
    return []
