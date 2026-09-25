"""O/U SHARP-OUTLIER BOTS ([[#149]], 2026-09-25) — soft books beating Pinnacle's fair O/U price.

Rules come from three pre-registered backtest rounds (dev/active/market2-model-plan.md):
  O1  a combined O/U model (ratings + consensus + Pinnacle) — on Pinnacle-priced rows it was
      WORSE than Pinnacle alone, and picks that existed only because the model disagreed with
      Pinnacle lost CLV. So the fair price here is PINNACLE, not a model.
  O2  EV_pin = odds x p_pinnacle - 1 >= 5% (capped at 15%: larger gaps are misposted lines a
      book may void): CLV +1.8% (Aug) / +2.6% (Sep) where real closes exist.
  O3  filters on top — adopted:
        bot_ou_sharp_early_v1  (T3) the book's quote is >= 12 h before kickoff:
                               CLV +7.5% / +6.9%, ROI +10.4% / +10.8% (Aug / Sep)
        bot_ou_sharp_2anchor_v1 (T2) the book ALSO beats the leave-one-out consensus of the
                               other books by >= 2% EV: CLV +6.6% / +4.2%
Lines 1.5 / 2.5 / 3.5, odds 1.30-6.00, one pick per (match, line) per bot (best EV), every
publishable book (Telegram audience). Paper only: simulated_bets, experimental, no placement.

Live differences from the backtest (stated so the forward record is read correctly): the
backtest priced each book's OPENING quote against Pinnacle's opening price; live uses each
book's LATEST quote and Pinnacle's latest, both at most QUOTE_MAX_AGE_H old.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from rich.console import Console

from workers.registry.bot_registry import VIP_BOTS, vip_ev_label

console = Console()

LINES = ("over_under_15", "over_under_25", "over_under_35")
EV_MIN, EV_CAP = 0.05, 0.15
ODDS_LO, ODDS_HI = 1.30, 6.00
QUOTE_MAX_AGE_H = 3.0
EARLY_MIN_H = 12.0
CONS_EV_MIN, CONS_MIN_BOOKS = 0.02, 3
HORIZON_H = 72.0
STAKE = 10.0
BOT_EARLY = "bot_ou_sharp_early_v1"
BOT_2ANCHOR = "bot_ou_sharp_2anchor_v1"
BOTS = (BOT_EARLY, BOT_2ANCHOR)
MODEL_VERSION = "ou_sharp_v1"
FUNNEL_SOURCE = "ou_sharp"
# CANDIDATE-FUNNEL (#162 W7.5): how far a decision got, for keeping ONE row per
# (bot, match, line, side) — the table's key has no bookmaker, and the helper keeps the
# LAST row per key, so the book that came closest to being picked must be the one written.
_STAGE = {"accepted": 5, "drop_one_per_match": 4, "drop_too_late": 3, "drop_no_consensus": 3,
          "drop_consensus_edge": 3, "drop_ev_cap": 2, "drop_edge": 1,
          "drop_odds_too_low": 0, "drop_odds_too_high": 0}


def power_devig(o_over: float, o_under: float) -> float | None:
    """2-way power de-vig (ANALYSIS_GOTCHAS #78: never proportional). Returns p(over)."""
    a, b = 1 / o_over, 1 / o_under
    if not (0.98 < a + b < 1.30):
        return None
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = (lo + hi) / 2
        if a ** k + b ** k - 1 < 0:
            hi = k
        else:
            lo = k
    return a ** ((lo + hi) / 2)


def in_ev_band(odds: float, p_fair: float) -> bool:
    """The O/U sharp-outlier price rule shared by both bots: odds in ODDS_LO..ODDS_HI and
    EV = odds x p_fair - 1 in EV_MIN..EV_CAP (larger gaps are misposted lines)."""
    return ODDS_LO <= odds <= ODDS_HI and EV_MIN <= odds * p_fair - 1 <= EV_CAP


def early_rule(odds: float, p_fair: float, hours_to_kickoff: float) -> bool:
    """O/U EARLY (VIP #2, bot_ou_sharp_early_v1): the EV band above AND >= EARLY_MIN_H to
    kickoff. THE one definition — evaluate() uses it and so does the VIP guard
    (workers/utils/vip_guard.py, #164), so "in VIP's range" can never drift from VIP's rule."""
    return in_ev_band(odds, p_fair) and hours_to_kickoff >= EARLY_MIN_H


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _band_step(odds: float, ev: float) -> str | None:
    """Why in_ev_band() said no (None = it said yes). Order matches the rule: odds first."""
    if odds < ODDS_LO:
        return "drop_odds_too_low"
    if odds > ODDS_HI:
        return "drop_odds_too_high"
    if ev < EV_MIN:
        return "drop_edge"
    if ev > EV_CAP:
        return "drop_ev_cap"
    return None


def evaluate(quotes: list[dict], now_ts: float, kickoff_ts: dict[str, float],
             funnel: dict | None = None) -> list[dict]:
    """Pure decision function (unit-tested). `quotes`: latest pre-kickoff O/U quote per
    (match_id, market, bookmaker, selection) with keys odds, ts (epoch s). Returns candidate
    picks: {bot, match_id, market, selection, odds, bookmaker, p_fair, ev, ev_cons}.

    `funnel` (optional, #162 W7.5): filled with the near-floor decisions — EV vs Pinnacle
    >= EV_MIN - NEAR_FLOOR_PP — keyed (bot, match, market, selection), one per key (the book
    that got furthest; see _STAGE), with `step`. Does not change what is returned."""
    from collections import defaultdict
    from workers.utils.candidate_funnel import NEAR_FLOOR_PP

    def note(bot: str, cand: dict, step: str) -> None:
        # Diagnostics on the VIP #2 pick path: it must never be able to change or stop a pick
        # ([[#162]] W7.5 review), so any bookkeeping error is swallowed.
        if funnel is None:
            return
        try:
            k = (bot, cand["match_id"], cand["market"], cand["selection"])
            old = funnel.get(k)
            if old is None or (_STAGE.get(step, -1), cand["ev"]) > (_STAGE.get(old["step"], -1), old["ev"]):
                funnel[k] = {**cand, "bot": bot, "step": step}
        except Exception:  # noqa: BLE001
            pass

    by = defaultdict(dict)                     # (match, market) -> book -> {over, under, ts}
    for q in quotes:
        if now_ts - q["ts"] > QUOTE_MAX_AGE_H * 3600:
            continue
        d = by[(q["match_id"], q["market"])].setdefault(q["bookmaker"], {})
        d[q["selection"]] = q["odds"]; d["ts"] = min(d.get("ts", q["ts"]), q["ts"])
    out = []
    for (mid, mk), books in by.items():
        ko = kickoff_ts.get(mid)
        if ko is None or ko <= now_ts:
            continue
        pin = books.get("Pinnacle")
        if not pin or "over" not in pin or "under" not in pin:
            continue
        p_over = power_devig(pin["over"], pin["under"])
        if p_over is None:
            continue
        # per-book de-vigged logit for the leave-one-out consensus
        logits = {}
        for b, d in books.items():
            if b == "Pinnacle" or "over" not in d or "under" not in d:
                continue
            p = power_devig(d["over"], d["under"])
            if p is not None:
                logits[b] = _logit(p)
        best: dict[str, dict] = {}
        for b, d in books.items():
            if b == "Pinnacle":
                continue
            for sel in ("over", "under"):
                o = d.get(sel)
                if o is None:
                    continue
                p = p_over if sel == "over" else 1 - p_over
                ev = o * p - 1
                if not in_ev_band(o, p):
                    if ev >= EV_MIN - NEAR_FLOOR_PP:     # far below the floor answers nothing
                        c0 = {"match_id": mid, "market": mk, "selection": sel, "odds": o,
                              "bookmaker": b, "p_fair": p, "ev": ev, "ev_cons": None, "ts": d["ts"]}
                        for bot in BOTS:
                            note(bot, c0, _band_step(o, ev))
                    continue
                others = [v for k, v in logits.items() if k != b]
                ev_cons = None
                if len(others) >= CONS_MIN_BOOKS:
                    pc = 1 / (1 + math.exp(-sum(others) / len(others)))
                    ev_cons = o * (pc if sel == "over" else 1 - pc) - 1
                cand = {"match_id": mid, "market": mk, "selection": sel, "odds": o, "bookmaker": b,
                        "p_fair": p, "ev": ev, "ev_cons": ev_cons}
                fc = {**cand, "ts": d["ts"]}
                if early_rule(o, p, (ko - now_ts) / 3600):
                    note(BOT_EARLY, fc, "drop_one_per_match")   # the winner is relabelled below
                    if BOT_EARLY not in best or ev > best[BOT_EARLY]["ev"]:
                        best[BOT_EARLY] = cand
                else:
                    note(BOT_EARLY, fc, "drop_too_late")
                if ev_cons is not None and ev_cons >= CONS_EV_MIN:
                    note(BOT_2ANCHOR, fc, "drop_one_per_match")
                    if BOT_2ANCHOR not in best or ev > best[BOT_2ANCHOR]["ev"]:
                        best[BOT_2ANCHOR] = cand
                else:
                    note(BOT_2ANCHOR, fc, "drop_no_consensus" if ev_cons is None else "drop_consensus_edge")
        for bot, c in best.items():
            out.append({**c, "bot": bot})
            if funnel is not None:
                try:
                    funnel[(bot, c["match_id"], c["market"], c["selection"])] = {
                        **c, "bot": bot, "step": "accepted", "ts": books[c["bookmaker"]]["ts"]}
                except Exception:  # noqa: BLE001 — diagnostics never touch the pick
                    pass
    return out


def funnel_rows(funnel: dict, now_ts: float) -> list[dict]:
    """evaluate()'s funnel as candidate_funnel rows. fair_prob is Pinnacle's power-de-vigged
    probability; threshold is EV_MIN (a multiplicative EV floor like the publisher's — never
    comparable to the pipeline's model edge, ANALYSIS_GOTCHAS #72)."""
    return [{"source": FUNNEL_SOURCE, "bot": f["bot"], "match_id": f["match_id"],
             "market": f["market"], "selection": f["selection"], "bookmaker": f["bookmaker"],
             "odds": f["odds"], "fair_prob": f["p_fair"], "fair_source": "pinnacle_power",
             "raw_prob": None, "threshold": EV_MIN, "step": f["step"],
             "quote_age_min": round((now_ts - f["ts"]) / 60, 1) if f.get("ts") else None}
            for f in funnel.values()]


def _send_vip_pick(p: dict, bet_id: str) -> None:
    """#148/#149 VIP: O/U EARLY's live pick goes ONLY to Pro/Elite users and the private VIP
    channel (never the public channel — the signaler excludes VIP bots), labelled EV8 / EV5.
    #162 W5.3: through the ONE audited sender (pause, bot_distribution.vip_channel, pick_sends
    row, DB dedupe on this pick)."""
    from workers.api_clients.db import execute_query
    from workers.notify.pick_sender import send_vip_pick
    try:
        m = execute_query("""SELECT ht.name h, at.name a, l.name lg FROM matches m JOIN teams ht ON ht.id = m.home_team_id
                               JOIN teams at ON at.id = m.away_team_id LEFT JOIN leagues l ON l.id = m.league_id
                              WHERE m.id = %s""", (p["match_id"],))
        h, a, lg = (m[0]["h"], m[0]["a"], m[0]["lg"]) if m else ("?", "?", None)
        line = {"over_under_15": "1.5", "over_under_25": "2.5", "over_under_35": "3.5"}.get(p["market"], p["market"])
        msg = (f"⭐ <b>VIP pick · {vip_ev_label(p['p_fair'], p['odds'])}</b>\n"
               f"<b>{h} vs {a}</b>\n"
               f"{p['selection'].capitalize()} {line} goals @ {p['odds']:.2f} ({p['bookmaker']})\n"
               f"EV {p['ev']*100:+.1f}% · fair odds {1 / p['p_fair']:.2f} — take it down to {1.05 / p['p_fair']:.2f}"
               + (f"\n{lg}" if lg else ""))
        send_vip_pick(p["bot"], bet_id, msg, match_id=p["match_id"], market=p["market"],
                      selection=p["selection"])
    except Exception as e:                      # a notification must never lose a recorded pick
        console.print(f"[yellow]VIP O/U notify failed: {e}[/yellow]")


def run(dry_run: bool = False) -> int:
    """Scan upcoming matches and record new picks. Returns the number stored."""
    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import store_bet
    from workers.jobs.daily_pipeline_v2 import is_publishable_book, _get_bot_id_by_name

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
               o.match_id::text AS match_id, o.market, o.bookmaker, o.selection,
               o.odds::float8 AS odds, extract(epoch FROM o."timestamp")::float8 AS ts,
               extract(epoch FROM m.date)::float8 AS ko
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE m.date > NOW() AND m.date < NOW() + (%s * INTERVAL '1 hour')
           AND m.status = 'scheduled' AND m.date_disputed_at IS NULL
           AND o.market = ANY(%s) AND o.selection IN ('over', 'under')
           AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND o."timestamp" < m.date AND o."timestamp" > NOW() - (%s * INTERVAL '1 hour')
         ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o."timestamp" DESC
        """,
        (HORIZON_H, list(LINES), QUOTE_MAX_AGE_H),
    )
    quotes = [dict(r) for r in rows if r["bookmaker"] == "Pinnacle" or is_publishable_book(r["bookmaker"])]
    kickoff = {r["match_id"]: float(r["ko"]) for r in rows}
    funnel: dict = {}
    picks = evaluate(quotes, now_ts, kickoff, funnel)
    if not picks:
        console.print("[dim]ou_sharp_outlier: 0 candidates[/dim]")
        _record_funnel(funnel, now_ts, dry_run)
        return 0
    bot_ids = {b: _get_bot_id_by_name(b) for b in BOTS}
    # one pick per (match, line) per bot, across runs too: never add the opposite side later
    have = {(str(r["bot_id"]), str(r["match_id"]), r["market"]): r["selection"] for r in execute_query(
        "SELECT bot_id, match_id, market, selection FROM simulated_bets WHERE bot_id = ANY(%s::uuid[]) AND result = 'pending'",
        ([v for v in bot_ids.values() if v],),
    )} if any(bot_ids.values()) else {}
    stored = 0
    for p in picks:
        bid = bot_ids.get(p["bot"])
        ip = 1 / p["odds"]
        reasoning = (f"[{p['bot']}] {p['market']} {p['selection']} @ {p['odds']:.2f} {p['bookmaker']} | "
                     f"EV vs Pinnacle {p['ev']*100:+.1f}%" +
                     (f" | EV vs consensus {p['ev_cons']*100:+.1f}%" if p["ev_cons"] is not None else ""))
        if dry_run:
            console.print(f"  DRY {p['match_id'][:8]} {reasoning}")
            continue
        if not bid or (str(bid), p["match_id"], p["market"]) in have:
            # picked on an earlier run: the SAME side stays `accepted`; the opposite side is
            # the one-pick-per-line rule applied across runs
            _fk = funnel.get((p["bot"], p["match_id"], p["market"], p["selection"]))
            if bid and _fk is not None and have[(str(bid), p["match_id"], p["market"])] != p["selection"]:
                _fk["step"] = "drop_one_per_match"
            continue
        bet_id = store_bet(bid, p["match_id"], {
            "market": p["market"], "selection": p["selection"], "odds": p["odds"],
            "model_prob": p["p_fair"], "implied_prob": ip, "edge": p["p_fair"] - ip,
            "calibrated_prob": round(p["p_fair"], 4), "stake": STAKE,
            "placed_at": now.isoformat(), "reasoning": reasoning,
            "recommended_bookmaker": p["bookmaker"], "timing_cohort": "all",
            "model_version": MODEL_VERSION,
        })
        if bet_id:
            stored += 1
            have[(str(bid), p["match_id"], p["market"])] = p["selection"]
            if p["bot"] in VIP_BOTS:
                _send_vip_pick(p, bet_id)
    _record_funnel(funnel, now_ts, dry_run)
    console.print(f"[green]ou_sharp_outlier: {stored} new picks ({len(picks)} candidates)[/green]")
    return stored


def _record_funnel(funnel: dict, now_ts: float, dry_run: bool) -> None:
    """CANDIDATE-FUNNEL (#162 W7.5): write this run's near-floor decisions through the shared
    helper (upsert, latest decision per candidate per day, 90-day retention). Diagnostics only
    — never raises into the job (record() swallows DB errors; this guards the row building)."""
    if dry_run or not funnel:
        return
    try:
        from workers.utils.candidate_funnel import record
        record(funnel_rows(funnel, now_ts))
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]ou_sharp_outlier: candidate funnel failed (non-fatal): {e}[/yellow]")


if __name__ == "__main__":
    import sys
    run(dry_run="--dry-run" in sys.argv)
