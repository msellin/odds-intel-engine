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


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def evaluate(quotes: list[dict], now_ts: float, kickoff_ts: dict[str, float]) -> list[dict]:
    """Pure decision function (unit-tested). `quotes`: latest pre-kickoff O/U quote per
    (match_id, market, bookmaker, selection) with keys odds, ts (epoch s). Returns candidate
    picks: {bot, match_id, market, selection, odds, bookmaker, p_fair, ev, ev_cons}."""
    from collections import defaultdict
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
                if o is None or not (ODDS_LO <= o <= ODDS_HI):
                    continue
                p = p_over if sel == "over" else 1 - p_over
                ev = o * p - 1
                if not (EV_MIN <= ev <= EV_CAP):
                    continue
                others = [v for k, v in logits.items() if k != b]
                ev_cons = None
                if len(others) >= CONS_MIN_BOOKS:
                    pc = 1 / (1 + math.exp(-sum(others) / len(others)))
                    ev_cons = o * (pc if sel == "over" else 1 - pc) - 1
                cand = {"match_id": mid, "market": mk, "selection": sel, "odds": o, "bookmaker": b,
                        "p_fair": p, "ev": ev, "ev_cons": ev_cons}
                if (ko - now_ts) >= EARLY_MIN_H * 3600:
                    if BOT_EARLY not in best or ev > best[BOT_EARLY]["ev"]:
                        best[BOT_EARLY] = cand
                if ev_cons is not None and ev_cons >= CONS_EV_MIN:
                    if BOT_2ANCHOR not in best or ev > best[BOT_2ANCHOR]["ev"]:
                        best[BOT_2ANCHOR] = cand
        for bot, c in best.items():
            out.append({**c, "bot": bot})
    return out


def _send_vip_pick(p: dict) -> None:
    """#148/#149 VIP: O/U EARLY's live pick goes ONLY to Pro/Elite users and the private VIP
    channel (never the public channel — the signaler excludes VIP bots), labelled EV8 / EV5."""
    from workers.api_clients.db import execute_query
    from workers.notify.telegram import send_telegram_to_users, send_telegram_vip
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
        send_telegram_to_users(msg, tier_minimum="pro", dedup_key=f"user-bet-{p['match_id']}-{p['market']}-{p['selection']}")
        send_telegram_vip(msg)
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
    picks = evaluate(quotes, now_ts, kickoff)
    if not picks:
        console.print("[dim]ou_sharp_outlier: 0 candidates[/dim]")
        return 0
    bot_ids = {b: _get_bot_id_by_name(b) for b in BOTS}
    # one pick per (match, line) per bot, across runs too: never add the opposite side later
    have = {(str(r["bot_id"]), str(r["match_id"]), r["market"]) for r in execute_query(
        "SELECT bot_id, match_id, market FROM simulated_bets WHERE bot_id = ANY(%s::uuid[]) AND result = 'pending'",
        ([v for v in bot_ids.values() if v],),
    )} if any(bot_ids.values()) else set()
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
            have.add((str(bid), p["match_id"], p["market"]))
            if p["bot"] in VIP_BOTS:
                _send_vip_pick(p)
    console.print(f"[green]ou_sharp_outlier: {stored} new picks ({len(picks)} candidates)[/green]")
    return stored


if __name__ == "__main__":
    import sys
    run(dry_run="--dry-run" in sys.argv)
