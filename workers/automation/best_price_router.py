"""BEST-PRICE-EXECUTION-ROUTER (COOLBET-PICK-TABLE-AUDIT Stage 5) — DRY-RUN first.

The unified real-money placement decision the owner asked for: for each Family-1
`/picks` real-money candidate, read the CURRENT odds at every placeable book
(Coolbet + Unibet), gate each book on its OWN price, and place ONCE at the book
with the best clearing price — never two bets on the same (match, market, selection).

Router decision rule (BETTING_ARCHITECTURE §7, owner 2026-09-09):
  1. both books clear the gate  → place once at the BETTER price
  2. only one book clears        → place once there
  3. never two bets on the same (match, market, selection) — a cross-book exposure
     check (real_bets across books, placed_real IS NOT FALSE) blocks the second.
A book with no fresh odds simply doesn't compete (so one book being down never
costs the bet); the other still places if it clears.

Per-book gate = the SAME gate the placer enforces, evaluated at THAT book's price:
  edge = calibrated_prob − 1/odds  ≥  the bot's BOT_THRESHOLDS floor
  AND odds ≥ `_min_odds_for(market)` (1x2 2.80 / o/u 1.80).
This is why a soft book can rescue a pick the reference price misses (a home
underdog at 3.30 on Coolbet vs 3.55 on Unibet → route to Unibet).

SAFETY (three modes, see route()):
  * default DRY-RUN (execute=False, stage=False) — report the decision, touch nothing.
  * DRY-TEST-IN-ACTION (stage=True) — dispatch each routed pick to the winning book's
    executor in its own stage mode: drives the real slip on the live site and STOPS
    before the place click. A complete no-op against the account.
  * REAL MONEY (execute=True) — places at the winning book. DOUBLE-GATED: refused
    unless env `ROUTER_ALLOW_REAL` is truthy, so a stray execute=True cannot move
    money. This is the owner-gated cutover.
The executor arms are `coolbet_ui_placer.stage_bet` (Coolbet) and
`unibet_placer.place_bet` after `unibet_odds_feed.resolve_event_url` (Unibet), each
of which re-reads LIVE odds and gates on min_odds at dispatch time.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

PLACEABLE_BOOKS = ("Coolbet", "Unibet-Site")  # order = tiebreak preference on equal odds
ODDS_FRESH_MAX_MIN = 180  # a book's snapshot older than this doesn't compete
STAKE_EUR = float(os.getenv("ROUTER_STAKE_EUR", "10"))
# Band around the routed price used to pin the RIGHT outcome button on the site
# (button text is "<name><odds>"; a tight band separates e.g. 1x2-home from
# DNB-home). Wide enough to tolerate normal line movement; the executor still
# re-reads LIVE odds and gates on min_odds, so a big move aborts safely.
_ODDS_BAND_PCT = 0.12


def _latest_book_odds(match_id: str, market: str, selection: str):
    """Latest fresh pre-match odds per placeable book for one (match,market,selection).
    Returns {book: {'odds': float, 'age_min': int}}."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.bookmaker) o.bookmaker,
               o.odds::float AS odds,
               EXTRACT(EPOCH FROM (now() - o.timestamp))/60.0 AS age_min
          FROM odds_snapshots o
         WHERE o.match_id = %s AND o.market = %s AND o.selection = %s
           AND o.bookmaker = ANY(%s)
         ORDER BY o.bookmaker, o.timestamp DESC
        """,
        (match_id, market, selection, list(PLACEABLE_BOOKS)),
    )
    out = {}
    for r in rows or []:
        if r["odds"] and r["odds"] > 1 and float(r["age_min"]) <= ODDS_FRESH_MAX_MIN:
            out[r["bookmaker"]] = {"odds": float(r["odds"]), "age_min": round(float(r["age_min"]))}
    return out


def _has_exposure(match_id: str, market: str, selection: str) -> bool:
    """Cross-book dedup: is there already a REAL (non-paper) bet on this selection?"""
    from workers.api_clients.db import execute_query
    r = execute_query(
        """SELECT 1 FROM real_bets
            WHERE match_id = %s AND market = %s AND lower(selection) = lower(%s)
              AND placed_real IS NOT FALSE
            LIMIT 1""",
        (match_id, market, selection),
    )
    return bool(r)


def decide_book(cal_prob: float, threshold: float, odds_floor: float,
                book_odds: dict) -> dict:
    """Pure routing decision. `book_odds` = {book: odds}. A book clears iff its edge
    (cal_prob − 1/odds) ≥ threshold AND odds ≥ odds_floor. Winner = best clearing
    price; ties break by PLACEABLE_BOOKS order. Returns {clearing, winner, ...}."""
    clearing = {}
    # ROUTER-AUDIT (2026-09-11): keep every book we PRICED, not just the ones
    # that cleared. Without the losers the stored rationale cannot answer the
    # question worth asking later — was the other book close, absent, or just
    # below floor? `reason` is the first failing condition, in gate order.
    considered = {}
    for book, o in book_odds.items():
        if not o or o <= 1:
            considered[book] = {"odds": o, "cleared": False, "reason": "no usable price"}
            continue
        edge = cal_prob - 1.0 / o
        if o < odds_floor:
            reason = f"below odds floor ({o} < {odds_floor})"
        elif edge < threshold:
            reason = f"edge {edge:.4f} < threshold {threshold}"
        else:
            reason = None
        considered[book] = {"odds": o, "edge": round(edge, 4),
                            "cleared": reason is None, "reason": reason}
        if reason is None:
            clearing[book] = {"odds": o, "edge": round(edge, 4)}
    if not clearing:
        return {"clearing": {}, "winner": None, "considered": considered}
    winner = max(clearing, key=lambda b: (clearing[b]["odds"],
                 -(PLACEABLE_BOOKS.index(b) if b in PLACEABLE_BOOKS else 99)))
    return {"clearing": clearing, "winner": winner, "considered": considered,
            "winner_odds": clearing[winner]["odds"], "winner_edge": clearing[winner]["edge"]}


def _routing_note(decision: dict) -> str:
    """Compact, queryable record of WHY this book won, stored on the real_bets
    row (owner request 2026-09-11: "turn it on so we can later analyze why
    which book was chosen").

    Written as JSON inside `notes` so it is analyzable in SQL without a
    migration, e.g.:
        SELECT notes::json->'router'->>'winner', count(*)
          FROM real_bets WHERE notes LIKE '{"router"%' GROUP BY 1;

    Captures every book we PRICED and whether it cleared — not just the
    winner. Without the losers the record cannot answer the question that
    matters later: was the other book close, absent, or merely below floor?
    """
    import json as _json
    clearing = decision.get("clearing") or {}
    return _json.dumps({"router": {
        "winner": decision.get("winner"),
        "winner_odds": decision.get("winner_odds"),
        "winner_edge": decision.get("winner_edge"),
        "threshold": decision.get("threshold"),
        "odds_floor": decision.get("odds_floor"),
        # every book considered: its price, its edge, and whether it cleared
        "considered": decision.get("considered") or {},
        "clearing": {b: v for b, v in clearing.items()},
    }}, separators=(",", ":"), default=str)


def _unibet_outcome_name(market: str, selection: str, home: str, away: str):
    """Map a (market, selection) to the Unibet outcome-button text that
    unibet_placer.place_bet matches against. 1x2 buttons carry the team name
    (draw = 'X'); total buttons carry the Estonian 'Üle'/'Alla'. Returns None
    for a market we don't place on Unibet."""
    from workers.canonical_market import market_family
    fam = market_family(market) or ""
    s = (selection or "").lower()
    if fam == "1x2":
        if "home" in s:
            return home
        if "away" in s:
            return away
        if "draw" in s or s == "x":
            return "X"
    if fam.startswith("over_under") or fam in ("o/u", "ou"):
        if "over" in s:
            return "Üle"
        if "under" in s:
            return "Alla"
    return None


def _dispatch_unibet(pick: dict, decision: dict, *, execute: bool) -> dict:
    """Drive the winning Unibet slip: resolve the event URL, then place_bet.
    execute=False STAGES the slip (dry-test-in-action) and stops before the
    place click; execute=True places for real. Never raises."""
    try:
        from workers.automation.unibet_odds_feed import resolve_event_url
        from workers.automation import unibet_placer
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"import failed: {e}"}
    name = _unibet_outcome_name(pick["market"], pick["selection"],
                                pick.get("home_team"), pick.get("away_team"))
    if not name:
        return {"book": "Unibet-Site", "ok": False,
                "reason": f"no Unibet outcome mapping for {pick['market']}/{pick['selection']}"}
    try:
        r = resolve_event_url(pick.get("home_team"), pick.get("away_team"), pick.get("match_date"))
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"resolve_event_url raised: {e}"}
    if not r.get("url"):
        return {"book": "Unibet-Site", "ok": False, "reason": "resolve_event_url: no Unibet event URL"}
    # UNIBET-UNATTENDED-SESSION (2026-09-11): revive the session BEFORE placing.
    # Unibet did not rot only because nothing ran it unattended; routing real
    # money here creates exactly the loop that rotted Coolbet. ensure_logged_in
    # is idempotent, never raises, and is rate-limited (1/30min) with the stamp
    # written BEFORE the attempt, so a hung login still rate-limits the next tick.
    try:
        from workers.automation import unibet_browser_sync as _ubs
        heal = _ubs.ensure_logged_in(min_gap_min=30)
        if heal in ("failed", "no_creds", "error"):
            return {"book": "Unibet-Site", "ok": False,
                    "reason": f"unibet session not live (ensure_logged_in={heal})",
                    "event_url": r["url"], "outcome": name}
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False,
                "reason": f"ensure_logged_in raised: {e}"}

    o = float(decision["winner_odds"])
    lo, hi = round(o * (1 - _ODDS_BAND_PCT), 2), round(o * (1 + _ODDS_BAND_PCT), 2)
    try:
        res = unibet_placer.place_bet(
            r["url"], name, min_odds=float(decision["odds_floor"]),
            odds_lo=lo, odds_hi=hi, execute=execute, stake=STAKE_EUR)
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"place_bet raised: {e}",
                "event_url": r["url"], "outcome": name}
    placed = bool(res.get("placed"))
    staged = (not execute) and bool(res.get("reason") and "PAPER" in str(res.get("reason")))

    # UNIBET-NO-REAL-BETS-ROW (fixed 2026-09-11) — THE DOUBLE-BET BUG.
    # unibet_placer writes nothing anywhere. The router's ONLY cross-book dedup
    # is _has_exposure(), which reads `real_bets`. So a confirmed real Unibet
    # placement was invisible to the guard and the NEXT pass would place the
    # same (match, market, selection) again. Record it the moment the balance
    # delta confirms — same contract as the Coolbet arm (placed_real=True only
    # after evidence, never on absence of an exception). A recording failure is
    # logged but must NOT turn a placed bet into a reported failure.
    real_bet_id = None
    if placed:
        try:
            from workers.api_clients.supabase_client import store_real_bet
            real_bet_id = store_real_bet(
                match_id=str(pick["match_id"]),
                market=pick["market"],
                selection=pick["selection"],
                bookmaker="Unibet-Site",
                actual_odds=float(res.get("odds") or o),
                stake=float(STAKE_EUR),
                captured_odds=(float(pick["odds_at_pick"])
                               if pick.get("odds_at_pick") else None),
                bot_id=pick.get("bot_id"),
                simulated_bet_id=pick.get("shadow_bet_id"),
                notes=_routing_note(decision),
                placed_real=True,
            )
        except Exception as e:  # noqa: BLE001
            log.error("UNIBET PLACED but real_bets write FAILED (%s) — the "
                      "cross-book dedup is blind to this bet until it is "
                      "recorded by hand: match=%s %s/%s", e,
                      pick.get("match_id"), pick.get("market"), pick.get("selection"))

    return {"book": "Unibet-Site", "ok": placed or staged, "event_url": r["url"],
            "outcome": name, "placed": placed, "staged": staged,
            "real_bet_id": real_bet_id, "result": res}


def _dispatch_coolbet(pick: dict, *, execute: bool,
                      edge_threshold: float = 0.03) -> dict:
    """Drive the winning Coolbet slip via coolbet_ui_placer.stage_bet.
    execute=False STAGES the slip (dry-test-in-action, a complete no-op against
    the account); execute=True places for real. Never raises."""
    try:
        from workers.automation import coolbet_ui_placer as up
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        return {"book": "Coolbet", "ok": False, "reason": f"import failed: {e}"}
    try:
        with sync_playwright() as pw:
            # ROUTER-COOLBET-ARM-DEAD (fixed 2026-09-11): this was
            # `page = up.attach(pw)`, but attach() returns (browser, page).
            # stage_bet then received a TUPLE as its page, raised, and the
            # except below swallowed it into a plain {"ok": False} — so the
            # router's Coolbet arm could never stage or place ANYTHING, and
            # said so in a way indistinguishable from a legitimate decline.
            _browser, page = up.attach(pw)
            # ROUTER-EDGE-THRESHOLD (fixed 2026-09-11): edge_threshold was not
            # passed, so stage_bet's independent live-price re-check fell back
            # to its 0.03 default instead of the bot's 0.08/0.10. The router's
            # own decide_book() uses the right threshold, so this silently
            # loosened the SECOND gate — the one whose entire purpose is to
            # re-verify at the live price. The value is already computed and
            # sitting in `decision`; pass it.
            res = up.stage_bet(page, pick, STAKE_EUR, execute=execute,
                               edge_threshold=edge_threshold)
    except Exception as e:  # noqa: BLE001
        return {"book": "Coolbet", "ok": False, "reason": f"stage_bet raised: {e}"}
    placed = bool(getattr(res, "placed", False))
    staged = (not execute) and bool(getattr(res, "ok", False))
    return {"book": "Coolbet", "ok": placed or staged, "placed": placed, "staged": staged,
            "result": {"placed": placed, "ok": getattr(res, "ok", None),
                       "notes": list(getattr(res, "notes", []) or [])}}


def _dispatch(winner: str, pick: dict, decision: dict, *, execute: bool) -> dict:
    """Route one decision to the winning book's executor arm."""
    if winner == "Unibet-Site":
        return _dispatch_unibet(pick, decision, execute=execute)
    if winner == "Coolbet":
        return _dispatch_coolbet(pick, execute=execute,
                                 edge_threshold=float(decision.get("threshold") or 0.03))
    return {"book": winner, "ok": False, "reason": f"no executor arm for book {winner}"}


def route(execute: bool = False, *, stage: bool = False, limit: int | None = None) -> dict:
    """Route the Family-1 real-money candidates across placeable books by best price.

    Three modes:
      * default (execute=False, stage=False) = DRY-RUN: report the routing
        decision, touch nothing.
      * stage=True = DRY-TEST-IN-ACTION: dispatch each routed pick to the winning
        book's executor with the executor's own execute=False, which drives the
        real slip (search → open event → select outcome → set+read-back stake →
        read slip) and STOPS before the place click. A complete no-op against the
        account, but exercises the entire wiring on the live site.
      * execute=True = REAL MONEY: places at the winning book. OWNER-GATED — refused
        unless env `ROUTER_ALLOW_REAL` is truthy, so a stray execute=True can never
        move money on its own.

    Never raises."""
    from workers.automation.coolbet_placer import _min_odds_for
    # ROUTER-GATE-PARITY (2026-09-11). The router previously ran a THINNER gate
    # stack than `place_coolbet_ui.py --execute` already enforced: its only guard
    # was an exact (match, market, selection) exposure match. Missing were
    # already-placed, the kickoff cutoff, the per-match market-family guard and
    # the daily bet/stake caps. Routing real money through it would have been a
    # downgrade, not parity.
    #
    # REUSE these, never reimplement: `canon_bet` (inside match_exposure /
    # exposure_conflict) collapses the TWO market vocabularies that coexist in
    # `real_bets` — 'o/u'+'over 2.5' from the API placer vs 'over_under_25'+'over'
    # from the UI placer. Any guard that reads real_bets without it sees half the
    # book and will happily double-bet the half it cannot see.
    from scripts.place_coolbet_ui import (
        PLACEABLE_BOTS, BOT_THRESHOLDS, load_picks,
        already_placed, match_exposure, exposure_conflict, spent_today,
        KICKOFF_CUTOFF_MIN, MAX_BETS_PER_DAY, MAX_STAKE_PER_DAY,
    )

    # Real-money is double-gated: the caller's execute=True AND an explicit env
    # opt-in. Without the env flag, a True degrades to report-only (never staged
    # silently either) so nothing places by accident.
    real_allowed = os.getenv("ROUTER_ALLOW_REAL", "").lower() in ("1", "true", "yes")
    real = bool(execute and real_allowed)
    mode = "real" if real else ("stage" if stage else "report")

    out = {"candidates": 0, "routed": 0, "no_book_clears": 0, "already_placed": 0,
           "would_place": [], "skipped": [], "execute": execute, "mode": mode,
           "dispatched": 0, "aborted": None}
    if execute and not real_allowed:
        out["real_refused"] = ("execute=True but ROUTER_ALLOW_REAL is not set — "
                               "real-money placement is owner-gated; reporting only")
    # market label → placer floor key (o/u floors are keyed 'o/u')
    def _floor_key(m):
        from workers.canonical_market import market_family
        return market_family(m)

    picks = []
    for bot in sorted(PLACEABLE_BOTS):
        try:
            picks.extend(load_picks(bot))
        except Exception as e:  # noqa: BLE001
            log.warning("router: load_picks(%s) failed: %s", bot, e)
    out["candidates"] = len(picks)

    # Seed per-match exposure from real_bets ONCE for every candidate match (one
    # query, not one per pick). Cross-book by construction: match_exposure reads
    # real_bets by match_id with no bookmaker filter, so a Unibet bet blocks a
    # Coolbet duplicate and vice versa — which only works because the Unibet arm
    # now records (UNIBET-NO-REAL-BETS-ROW, fixed 2026-09-11).
    try:
        held_by_match = match_exposure([str(p["match_id"]) for p in picks])
    except Exception as e:  # noqa: BLE001
        log.error("match_exposure failed (%s) — refusing to route: the per-match "
                  "dedup is the guard that stops double-betting", e)
        out["aborted"] = f"match_exposure failed: {e}"
        return out

    # Daily caps, counted from COMMITTED 'placed' rows plus what this pass places.
    # Only meaningful when money can actually move; in report/stage mode they are
    # reported but never abort, so a dry run still shows the full routing picture.
    try:
        day_n, day_stake = spent_today()
    except Exception as e:  # noqa: BLE001
        if real:
            log.error("spent_today failed (%s) — refusing to place: the daily cap "
                      "is the backstop for a runaway loop", e)
            out["aborted"] = f"spent_today failed: {e}"
            return out
        day_n, day_stake = 0, 0.0
    out["day_start"] = {"bets": day_n, "stake": round(day_stake, 2)}

    for p in picks:
        mid = p["match_id"]; market = p["market"]; sel = p["selection"]
        bot = p["bot_name"]; cal = p.get("calibrated_prob")
        label = f"{p.get('home_team')} v {p.get('away_team')} | {market}/{sel}"
        if cal is None:
            out["skipped"].append({"pick": label, "reason": "no calibrated_prob"}); continue
        cal = float(cal)
        if _has_exposure(mid, market, sel):
            out["already_placed"] += 1
            out["skipped"].append({"pick": label, "reason": "already have a real bet (cross-book dedup)"}); continue

        # GATE 1 — already placed. A confirmed 'placed' attempt for this exact
        # shadow_bet means the work is done; re-placing is a duplicate.
        sbid = p.get("shadow_bet_id")
        try:
            if sbid and already_placed(str(sbid)):
                out["already_placed"] += 1
                out["skipped"].append({"pick": label,
                                       "reason": "already placed (confirmed attempt)"})
                continue
        except Exception as e:  # noqa: BLE001
            log.warning("already_placed check failed for %s: %s — skipping "
                        "(fail closed: an unreadable dedup must not place)", label, e)
            out["skipped"].append({"pick": label,
                                   "reason": f"already_placed check failed: {e}"})
            continue

        # GATE 2 — maturity / kickoff cutoff. Too close to kickoff the price is
        # moving and the slip may be refused mid-placement.
        kd = p.get("match_date")
        if kd is not None:
            try:
                from datetime import datetime, timezone
                ko = kd if hasattr(kd, "tzinfo") else None
                if ko is not None:
                    if ko.tzinfo is None:
                        ko = ko.replace(tzinfo=timezone.utc)
                    mins = (ko - datetime.now(timezone.utc)).total_seconds() / 60.0
                    if mins < KICKOFF_CUTOFF_MIN:
                        out["skipped"].append({
                            "pick": label,
                            "reason": f"kickoff in {mins:.1f} min < "
                                      f"{KICKOFF_CUTOFF_MIN} min cutoff"})
                        continue
            except Exception as e:  # noqa: BLE001
                log.debug("kickoff cutoff check skipped for %s: %s", label, e)

        # GATE 3 — per-match exposure across BOTH books. Stronger than the exact
        # (match,market,selection) check above: it also refuses a second bet in
        # the same market FAMILY ("the same opinion, not a second one") and
        # enforces the per-match bet/stake caps. `held` is appended to as this
        # pass places, because a DB-only check is racy within one run.
        held = held_by_match.setdefault(mid, [])
        conflict = exposure_conflict(p, held, STAKE_EUR)
        if conflict:
            out["skipped"].append({"pick": label,
                                   "reason": f"per-match exposure: {conflict}"})
            continue

        threshold = float(BOT_THRESHOLDS.get(bot, 0.03))
        odds_floor = _min_odds_for(_floor_key(market))
        books = _latest_book_odds(mid, market, sel)
        dec = decide_book(cal, threshold, odds_floor, {b: d["odds"] for b, d in books.items()})
        if not dec["winner"]:
            out["no_book_clears"] += 1
            out["skipped"].append({"pick": label, "reason": "no book clears the gate",
                                   "books": {b: d["odds"] for b, d in books.items()},
                                   "considered": dec.get("considered") or {},
                                   "threshold": threshold, "odds_floor": odds_floor,
                                   "cal_prob": round(cal, 4)})
            continue
        winner = dec["winner"]
        out["routed"] += 1
        decision = {"pick": label, "bot": bot, "cal_prob": round(cal, 4),
                    "winner": winner, "winner_odds": dec["winner_odds"],
                    "winner_edge": dec["winner_edge"],
                    "all_clearing": dec["clearing"], "clearing": dec["clearing"],
                    "considered": dec.get("considered") or {},
                    "threshold": threshold, "odds_floor": odds_floor}
        out["would_place"].append(decision)

        # Dispatch to the winning book's executor when staging (dry-drive) or
        # placing for real. Report mode touches nothing. `limit` caps how many
        # picks we actually drive (keeps a dry-test-in-action controlled).
        # GATE 4 — daily caps. The backstop for a runaway loop: if something
        # goes wrong upstream, this is what stops it at 80 bets / EUR 800 rather
        # than at the account balance. ABORTS the run rather than skipping the
        # pick — once the cap is hit, every later pick would hit it too, and
        # continuing would just hammer the books for nothing.
        if real:
            if day_n + 1 > MAX_BETS_PER_DAY:
                out["aborted"] = (f"daily bet cap reached ({day_n} >= "
                                  f"{MAX_BETS_PER_DAY}) — stopping the run")
                break
            if day_stake + STAKE_EUR > MAX_STAKE_PER_DAY:
                out["aborted"] = (f"daily stake cap reached (EUR {day_stake:.2f} + "
                                  f"{STAKE_EUR:.2f} > {MAX_STAKE_PER_DAY:.2f}) — "
                                  "stopping the run")
                break

        if (stage or real) and (limit is None or out["dispatched"] < limit):
            disp = _dispatch(winner, p, decision, execute=real)
            decision["dispatch"] = disp
            out["dispatched"] += 1
            # Count what we actually placed, in-pass. A DB-only re-read is racy
            # inside one run — the Airbus UK incident put three bets on one match
            # at 13:00, 13:02 and 13:02 because each check re-read a table that
            # had not caught up yet.
            if disp.get("placed"):
                canon = None
                try:
                    from scripts.place_coolbet_ui import canon_bet
                    canon = canon_bet(market, sel)
                except Exception:  # noqa: BLE001
                    pass
                if canon:
                    held.append({"family": canon[0], "canon": canon[1],
                                 "stake": float(STAKE_EUR)})
                day_n += 1
                day_stake += float(STAKE_EUR)
            elif disp.get("staged"):
                # Dry-test realism: a would-place must occupy the guard exactly as
                # a real one would, or a stage run reports a rosier picture than
                # the real run would produce.
                try:
                    from scripts.place_coolbet_ui import canon_bet
                    c = canon_bet(market, sel)
                    if c:
                        held.append({"family": c[0], "canon": c[1],
                                     "stake": float(STAKE_EUR)})
                except Exception:  # noqa: BLE001
                    pass
        elif real or stage:
            decision["dispatch"] = {"skipped": f"limit {limit} reached"}
    out["day_end"] = {"bets": day_n, "stake": round(day_stake, 2)}
    return out


def monitor(alert: bool = True) -> dict:
    """The 'always check both books' visibility layer (REPORT-ONLY, no money). Runs
    route() in report mode, logs the per-pick routing, and Telegram-alerts when the
    Coolbet-only placer would MISS or under-price a pick — i.e. the best clearing book
    is Unibet-Site (Coolbet absent, lower, or doesn't clear). This is the safe monitor
    that makes "check both" real BEFORE the owner-gated real-money cutover."""
    res = route()  # report mode — DB only, touches nothing
    unibet_wins = []
    for d in res["would_place"]:
        if d["winner"] == "Unibet-Site":
            cb = d["all_clearing"].get("Coolbet")
            why = "Coolbet absent/doesn't clear" if not cb else f"Coolbet {cb['odds']} < Unibet {d['winner_odds']}"
            unibet_wins.append(f"{d['pick']} → UNIBET @ {d['winner_odds']} (edge {d['winner_edge']:.1%}; {why})")
    log.info("router-monitor: candidates=%d routed=%d already_placed=%d no_book_clears=%d | UNIBET-wins=%d",
             res["candidates"], res["routed"], res["already_placed"], res["no_book_clears"], len(unibet_wins))
    for u in unibet_wins:
        log.info("  ⇢ %s", u)
    if alert and unibet_wins:
        try:
            from workers.notify.telegram import send_telegram
            send_telegram(
                "🔵 Best-price router — %d pick(s) better/ONLY at UNIBET, which the Coolbet-only "
                "placer MISSES:\n%s\n(These need the router / Unibet placer to be captured.)" % (
                    len(unibet_wins), "\n".join("• " + x for x in unibet_wins[:8])),
                dedup_key="router-unibet-divergence", dedup_window_s=3600)
        except Exception as e:  # noqa: BLE001
            log.debug("router-monitor alert failed (non-fatal): %s", e)
    return {"candidates": res["candidates"], "routed": res["routed"],
            "already_placed": res["already_placed"], "unibet_wins": unibet_wins}


def main() -> int:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Best-price router (Coolbet vs Unibet).")
    ap.add_argument("--stage", action="store_true",
                    help="DRY-TEST-IN-ACTION: drive the winning book's real slip and stop "
                         "before placing (no money moves)")
    ap.add_argument("--execute", action="store_true",
                    help="REAL MONEY at the winning book — owner-gated, also requires "
                         "env ROUTER_ALLOW_REAL=true; otherwise degrades to report-only")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap how many routed picks are actually driven (stage/execute)")
    ap.add_argument("--monitor", action="store_true",
                    help="REPORT-ONLY 'always check both books' monitor + Telegram alert on Unibet-wins (no money)")
    a = ap.parse_args()
    if a.monitor:
        print(json.dumps(monitor(), indent=2, ensure_ascii=False, default=str))
        return 0
    res = route(execute=a.execute, stage=a.stage, limit=a.limit)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
