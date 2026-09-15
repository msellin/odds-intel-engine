"""INPLAY-COLLECTOR (2026-09-15, OWN Phase 1b) — the in-play board collector
that WRITES TO THE DATABASE, plus the paper bot that reads it.

Grew out of `inplay_epicbet_collector.py` (2026-09-14), which wrote JSONL to a
scratch file for the discovery work. This module is the production shape of the
same loop: one row per (fixture, instant) into `inplay_book_quotes`, a heartbeat
so a dead collector alerts, and — after every cycle — the two LOCKED slow-state
triggers evaluated against the fresh board, writing paper picks to `shadow_bets`
at the ON-SCREEN price.

WHY EPICBET, AND WHY ONLY EPICBET TODAY. The 2026-09-15 review found Coolbet's
in-play margin tighter (4.96%/5.16% vs Epicbet 6.41%/6.44% on 43 fixtures) and
Coolbet has the only placer. But Coolbet's board sits behind Imperva and its
request volume is what escalates the wall (RELIABILITY_LEDGER §6, §10); a 30-60 s
in-play poll on top of the pre-match sweep is exactly the footprint that has
taken the pre-match feed down twice. Epicbet's live board is anonymous REST,
thread-safe and fast (module docstring of the discovery collector). So the RIG
collects Epicbet, the paper bot measures the triggers at Epicbet's on-screen
price, and Coolbet in-play collection is filed as a follow-up with its own
Imperva budget. What we learn about the TRIGGERS transfers; what we learn about
Epicbet's PRICE does not, and the eval script says so.

THE TWO TRIGGERS (LOCKED — dev/active/own-implementation-plan.md Phase 1b).
  T1  score 0-0, minute 35–54, market over_under line 2.5, back UNDER, price ≤ 2.20
  T2  goal difference exactly 2, minute 70–89, back the LEADER on 1x2, price ≤ 2.20
Both are SLOW-STATE triggers on purpose: a 5–10 s acceptance delay and event
suspension kill any trigger keyed to a state change, and at 45 s cadence we are
the slow party, not the book. Any new trigger needs a wide-window + split-half
check before it gets a line anywhere (INPLAY_STRATEGY_CANDIDATES, B1 withdrawn).

CONTROL ARM. The same triggers priced off API-Football's live aggregate at the
same instant, written under a SECOND bot (`bot_inplay_slowstate_afctl_v1`) so
the unique view does not collapse the two arms. The gap between the arms is the
value of the fresh board.

PRIMARY METRIC (scripts/inplay_slowstate_eval.py): realised hit-rate minus the
book's own de-vigged implied probability on the selected set, cluster-robust on
fixture. CLV is inadmissible in play (ANALYSIS_GOTCHAS §14). n≈3,000 for +2.5pp
at 80% power; STOP at n=1,000 if the lift is negative.

Nothing here places a bet or touches an account. It writes `inplay_book_quotes`,
`shadow_bets` (two experimental paper bots) and one heartbeat row.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

BOOK = "Epicbet"
BOT_LIVE = "bot_inplay_slowstate_v1"
BOT_CONTROL = "bot_inplay_slowstate_afctl_v1"
STAKE_EUR = 10.0
PRICE_CAP = 2.20
HEARTBEAT_NAME = "inplay_collector"

TRIGGERS = {
    # name: (minute_lo, minute_hi, description)
    "T1_00_under25": (35, 54, "0-0 at 35'-54' -> back UNDER 2.5 at <= 2.20"),
    "T2_lead2_leader": (70, 89, "two-goal lead at 70'-89' -> back the LEADER (1x2) at <= 2.20"),
}


# ── triggers (pure) ──────────────────────────────────────────────────────────
def evaluate_triggers(minute: int | None, score: list | None, markets: list[dict],
                      home_team: str | None = None, away_team: str | None = None) -> list[dict]:
    """Return the picks the LOCKED triggers fire on this board state.

    `markets` is the collector's nested shape: [{fam, line, sel:[{sel, odds, suspended}]}].
    Each returned pick: {trigger, market, selection, odds, book_prob} where
    book_prob is the book's OWN de-vigged probability of the selection (2-way or
    3-way Shin de-vig over the same market). Never raises.
    """
    from workers.model.devig import devig
    out: list[dict] = []
    if minute is None or not score or len(score) != 2 or score[0] is None or score[1] is None:
        return out
    h, a = int(score[0]), int(score[1])

    def _market(fam: str, line: float | None = None):
        for m in markets:
            if m.get("fam") != fam:
                continue
            if line is not None:
                try:
                    if abs(float(m.get("line") if m.get("line") is not None else -99) - line) > 1e-6:
                        continue
                except (TypeError, ValueError):
                    continue
            return m
        return None

    def _price(m: dict, want: str) -> tuple[float | None, float | None]:
        sels = [s for s in (m.get("sel") or []) if s.get("odds") and not s.get("suspended")]
        if not sels:
            return None, None
        odds = [float(s["odds"]) for s in sels]
        probs = devig(odds) or []
        for s, p in zip(sels, probs):
            if _norm_sel(s.get("sel"), home_team, away_team) == want:
                return float(s["odds"]), p
        return None, None

    lo, hi, _ = TRIGGERS["T1_00_under25"]
    if h == 0 and a == 0 and lo <= minute <= hi:
        m = _market("ou", 2.5)
        if m and len([s for s in m.get("sel") or [] if s.get("odds")]) == 2:
            odds, p = _price(m, "under")
            if odds and odds <= PRICE_CAP:
                out.append({"trigger": "T1_00_under25", "market": "over_under_25",
                            "selection": "under", "odds": odds, "book_prob": p})

    lo, hi, _ = TRIGGERS["T2_lead2_leader"]
    if abs(h - a) == 2 and lo <= minute <= hi:
        m = _market("1x2")
        if m and len([s for s in m.get("sel") or [] if s.get("odds")]) == 3:
            want = "home" if h > a else "away"
            odds, p = _price(m, want)
            if odds and odds <= PRICE_CAP:
                out.append({"trigger": "T2_lead2_leader", "market": "1x2",
                            "selection": want, "odds": odds, "book_prob": p})
    return out


def _seconds(v) -> int | None:
    """AF's `status.seconds` is a CLOCK STRING like "90:48" (minute:second), not
    an int — found on the first live cycle (2026-09-15). Return elapsed seconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    t = str(v).strip()
    if ":" in t:
        try:
            m, sec = t.split(":", 1)
            return int(m) * 60 + int(sec)
        except ValueError:
            return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _norm_sel(s, home_team: str | None = None, away_team: str | None = None) -> str:
    """Epicbet labels 1x2 selections with the TEAM NAMES ("Ajax U19" / "Draw" /
    "AZ Alkmaar U19"), not 1/X/2 — found by the Phase 1b verifier on the first
    live board, after a smoke test with synthetic "1"/"X"/"2" labels had passed.
    Match team names first (exact, then alphanumeric-normalised), then the
    generic vocab."""
    t = (s or "").strip().lower()
    def _n(x):
        return "".join(ch for ch in (x or "").lower() if ch.isalnum())
    if home_team and (t == home_team.strip().lower() or (_n(t) and _n(t) == _n(home_team))):
        return "home"
    if away_team and (t == away_team.strip().lower() or (_n(t) and _n(t) == _n(away_team))):
        return "away"
    if t in ("1", "home", "h"):
        return "home"
    if t in ("2", "away", "a"):
        return "away"
    if t in ("x", "draw", "d", "viik"):
        return "draw"
    if t.startswith("over") or t.startswith("üle"):
        return "over"
    if t.startswith("under") or t.startswith("alla"):
        return "under"
    return t


# ── DB writers ───────────────────────────────────────────────────────────────
def write_quotes(rows: list[dict]) -> int:
    """Insert board rows into inplay_book_quotes. Never raises."""
    if not rows:
        return 0
    from workers.api_clients.db import get_conn
    import psycopg2.extras
    payload = [(r["captured_at"], BOOK, r.get("book_event_id"), r.get("af_fixture_id"),
                r.get("match_id"), r.get("league"), r.get("home"), r.get("away"),
                r.get("minute"), r.get("seconds"),
                (r.get("score") or [None, None])[0], (r.get("score") or [None, None])[1],
                r.get("af_age_s"), json.dumps(r.get("markets") or []))
               for r in rows]
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO inplay_book_quotes
                       (captured_at, book, book_event_id, af_fixture_id, match_id, league,
                        home_team, away_team, minute, seconds, score_home, score_away,
                        af_age_s, markets) VALUES %s""",
                    payload, page_size=200)
                conn.commit()
        return len(payload)
    except Exception as e:  # noqa: BLE001
        log.warning("inplay_book_quotes write failed: %s", e)
        return 0


def heartbeat(status: str) -> None:
    """One row in pipeline_health_state so a dead collector is visible; the
    freshness watchdog reads updated_at."""
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """INSERT INTO pipeline_health_state (pipeline_name, last_alert_reason, updated_at)
               VALUES (%s, %s, NOW())
               ON CONFLICT (pipeline_name) DO UPDATE
                 SET last_alert_reason = EXCLUDED.last_alert_reason, updated_at = NOW()""",
            (HEARTBEAT_NAME, status[:200]))
    except Exception as e:  # noqa: BLE001
        log.debug("heartbeat failed: %s", e)


_BOT_IDS: dict[str, str | None] = {}


def _bot_id(name: str) -> str | None:
    if name not in _BOT_IDS:
        try:
            from workers.api_clients.db import execute_query
            r = execute_query("SELECT id FROM bots WHERE name = %s AND retired_at IS NULL", (name,))
            _BOT_IDS[name] = str(r[0]["id"]) if r else None
        except Exception:  # noqa: BLE001
            return None
    return _BOT_IDS[name]


def write_pick(bot_name: str, match_id: str, pick: dict, minute: int, score: list,
               book: str, run_id: str) -> bool:
    """Paper pick into shadow_bets at the ON-SCREEN price. ON CONFLICT DO NOTHING:
    the first fire per (bot, match, market, selection) is the pick — a trigger
    that stays true for 20 minutes is one bet, not twenty."""
    bid = _bot_id(bot_name)
    if not bid:
        return False
    from workers.api_clients.db import execute_write
    try:
        n = execute_write(
            """INSERT INTO shadow_bets
                   (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                    odds_at_pick, odds_at_pick_live, pick_time, stake,
                    model_probability, calibrated_prob, edge_percent,
                    recommended_bookmaker, strategy_profile,
                    inplay_minute, inplay_score_home, inplay_score_away)
               VALUES (%s, 'inplay_slowstate', %s, %s, %s, %s, %s, %s, now(), %s,
                       %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
            (run_id, bid, match_id, pick["market"], pick["selection"],
             pick["odds"], pick["odds"], STAKE_EUR,
             pick.get("book_prob"), pick.get("book_prob"),
             (round(pick["book_prob"] - 1.0 / pick["odds"], 5) if pick.get("book_prob") else None),
             book, pick["trigger"], int(minute), int(score[0]), int(score[1])))
        return bool(n)
    except Exception as e:  # noqa: BLE001
        log.warning("shadow_bets write failed (%s): %s", bot_name, e)
        return False


def _af_control_pick(pick: dict, af_prices: dict | None) -> dict | None:
    """The same trigger priced off AF's live aggregate at this instant, if AF
    quotes the market. Returns a pick dict or None."""
    if not af_prices:
        return None
    q = af_prices.get(pick["market"])
    if not q or pick["selection"] not in q:
        return None
    odds = float(q[pick["selection"]])
    if odds <= 1.0:
        return None
    from workers.model.devig import devig
    sides = ["home", "draw", "away"] if pick["market"] == "1x2" else ["over", "under"]
    if not all(s in q for s in sides):
        return None
    probs = devig([float(q[s]) for s in sides]) or []
    p = probs[sides.index(pick["selection"])] if probs else None
    return {**pick, "odds": odds, "book_prob": p}


# ── the loop ─────────────────────────────────────────────────────────────────
def run(cadence: float, rediscover_s: float, max_fixtures: int, duration_s: float | None,
        write_picks: bool = True) -> None:
    from workers.jobs.inplay_epicbet_collector import (Epicbet, af_state, match_af, load_af_names)
    from workers.jobs.inplay_af_prices import af_live_prices, af_fixture_to_match_id
    eb = Epicbet()
    t_end = (time.time() + duration_s) if duration_s else None
    board: list[dict] = []
    af_names: dict = {}
    afmap: dict = {}
    last_disc = 0.0
    cycles = written = picks = errors = 0
    run_id = str(uuid.uuid4())
    heartbeat("starting")
    while t_end is None or time.time() < t_end:
        t0 = time.time()
        if t0 - last_disc > rediscover_s:
            try:
                board = eb.live_board()[:max_fixtures]
                last_disc = t0
                af_names = load_af_names() or af_names
                log.info("board refreshed: %d live fixtures", len(board))
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.warning("board refresh failed: %s", e)
        af: dict = {}
        af_px: dict = {}
        try:
            af = af_state()
            if board and af_names:
                afmap = match_af(board, af, af_names)
            af_px = af_live_prices() if write_picks else {}
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

        stamp = datetime.now(timezone.utc)
        rows: list[dict] = []
        for f in board:
            d = res.get(f["eb_id"]) or {}
            if d.get("error"):
                errors += 1
                continue
            afid = afmap.get(f["eb_id"])
            st = af.get(afid) or {}
            match_id = af_fixture_to_match_id(afid) if afid else None
            row = {"captured_at": stamp, "book_event_id": str(f["eb_id"]), "af_fixture_id": afid,
                   "match_id": match_id, "league": f["league"], "home": f["home"], "away": f["away"],
                   "minute": st.get("minute"), "seconds": _seconds(st.get("seconds")),
                   "score": st.get("goals"), "af_age_s": st.get("af_age_s"),
                   "markets": d.get("markets") or []}
            rows.append(row)
            if write_picks and match_id and row["minute"] is not None and row["score"]:
                for pick in evaluate_triggers(row["minute"], row["score"], row["markets"],
                                              d.get("home") or f["home"], d.get("away") or f["away"]):
                    if write_pick(BOT_LIVE, match_id, pick, row["minute"], row["score"], BOOK, run_id):
                        picks += 1
                        log.info("PICK %s %s %s/%s @ %.2f (%s %s-%s %d')", pick["trigger"], f["home"],
                                 pick["market"], pick["selection"], pick["odds"], f["away"],
                                 row["score"][0], row["score"][1], row["minute"])
                    ctl = _af_control_pick(pick, af_px.get(str(afid)))
                    if ctl:
                        write_pick(BOT_CONTROL, match_id, ctl, row["minute"], row["score"],
                                   "api-football-live", run_id)
        written += write_quotes(rows)
        cycles += 1
        if cycles % 10 == 0:
            log.info("cycle %d | fixtures %d | rows %d | picks %d | errors %d | %.1fs",
                     cycles, len(board), written, picks, errors, time.time() - t0)
        heartbeat(f"cycle {cycles} fixtures {len(board)} rows {written} picks {picks} errors {errors}")
        time.sleep(max(0.0, cadence - (time.time() - t0)))
    heartbeat(f"stopped after {cycles} cycles")
    log.info("DONE cycles=%d rows=%d picks=%d errors=%d", cycles, written, picks, errors)


def main() -> int:
    ap = argparse.ArgumentParser(description="In-play board collector + slow-state paper bot (Epicbet)")
    ap.add_argument("--cadence", type=float, default=45.0)
    ap.add_argument("--rediscover", type=float, default=300.0)
    ap.add_argument("--max-fixtures", type=int, default=15)
    ap.add_argument("--hours", type=float, default=None, help="run for N hours; default = forever (launchd KeepAlive)")
    ap.add_argument("--no-picks", action="store_true", help="collect only; do not evaluate triggers")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(a.cadence, a.rediscover, a.max_fixtures, (a.hours * 3600 if a.hours else None),
        write_picks=not a.no_picks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
