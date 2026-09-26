"""ASIAN HANDICAP SHARP-OUTLIER BOT ([[#187]], 2026-09-26) — a third market for our users (👥 PICKS).

A publishable book's Asian-handicap price beats Pinnacle's fair price on the SAME line. Every line type:
whole (0, ±1, …), half (±0.5, …) and quarter (±0.25, ±0.75, …). No model — Pinnacle's handicap market
is the sharpest price there is (Hegarty & Whelan, IJF 2025: AH odds are efficient where 1X2 odds are not),
so it is the fair price, and the pick is a book that disagrees with it.

EVIDENCE (pre-registered: dev/active/ah-market-bot-prereg.md; backtest scripts/analysis/ah_market/):
  discovery 07-01..08-31: pooled rule +5.4% CLV vs an INDEPENDENT close (the other books' consensus,
                          Pinnacle and the pick's own book excluded), n 223, p < 0.001
  holdout   09-01..09-25: +3.0%, n 185, p = 0.0001 — SUPPORTED (Holm). ROI +1.2% [-8.9, +11.1]: unproven.
  The per-line-type cells did not replicate on their own, so the bot runs the POOLED rule.
  Direct-feed books (Epicbet) were NEGATIVE in the holdout (-8.1%, n 30) and absent from discovery, so the
  bot prices only the API-Football books the evidence covers (AF_BOOKS, frozen here on purpose).

THE RULE (= the backtest's; live uses each book's latest quote, as O/U EARLY does):
  * fair price  Pinnacle's two prices on the line, power de-vig (the shared sharp engine, 2-way)
  * EV          odds x p_fair - 1 in [3%, 15%)  (>= 15% = a likely wrong / stale quote)
  * freshness   Pinnacle's NEWEST fetch <= 60 min old, only the rungs in that fetch, and the book quote
                from the same fetch (<= 5 min apart — backtest parity); >= 45 min before kickoff
  * ladder      the book's price must sit between ITS OWN prices on the neighbouring lines (L -/+ 0.25)
                wherever those are quoted (amendment 2: a price that breaks its own book's ladder is a
                feed error — Betano's quarter ladder is non-monotonic on 9.4% of same-fetch triples)
  * one pick per match (best EV), ever — a later run never adds a second line or side
Paper only: simulated_bets, EXPERIMENTAL (admins only), flat EUR 10, nothing sent.

SELECTION FORMAT. simulated_bets.selection = side + the HOME-perspective line ("away -0.75" = the away side
of home -0.75, i.e. away +0.75) — what settlement._r_asian_handicap and odds_snapshots.handicap_line use.
Quarter lines settle half win / half loss (settle_fraction 0.5, migration 476).
"""
from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console

console = Console()

BOT = "bot_ah_sharp_v1"
MODEL_VERSION = "ah_sharp_v1"
EV_MIN, EV_CAP = 0.03, 0.15
FRESH_MIN = 60.0
LEAD_H = 0.75
HORIZON_H = 72.0
STAKE = 10.0
OVERROUND = (0.98, 1.30)     # a line whose sum(1/odds) sits outside this is a data fault, not a price
# The books the pre-registered evidence covers (API-Football feed). An ALLOW-list on purpose: a new direct
# feed (own fetch cadence, e.g. Optibet) must not enter an untested rule by being added elsewhere.
AF_BOOKS = ("Bet365", "Betano", "1xBet", "Marathonbet", "BetVictor", "SBO", "10Bet", "Superbet",
            "Betfair", "Dafabet")


def _rule():
    from workers.automation.sharp_engine import SharpRule, PICK_BEST_PER_LINE
    return SharpRule(
        # the engine refuses e > ceiling; the pre-registered band is [3%, 15%) — so 15% itself is refused
        BOT, books=AF_BOOKS, edge_unit="ev", edge_floor=EV_MIN, edge_ceiling=EV_CAP - 1e-9,
        odds_floor=1.01, odds_ceiling=None, outlier_mult=None,
        anchor_max_age_h=FRESH_MIN / 60.0, book_max_age_min=FRESH_MIN,
        overround=OVERROUND, sanity_guard=False, min_hours_to_ko=LEAD_H,
        pick=PICK_BEST_PER_LINE)


def ladder_ok(lines: dict, cand: dict) -> bool:
    """The pick book's price on (side, L) lies between ITS OWN fresh prices on L-0.25 and L+0.25 wherever
    those exist. Home odds fall as the home line rises; away odds rise."""
    from workers.utils.anchor import ah_line_market
    line = float(cand["market"].split(":", 1)[1])
    side, book, odds, mid = cand["selection"], cand["bookmaker"], cand["odds"], cand["match_id"]
    for d in (-0.25, 0.25):
        nb = lines.get((mid, ah_line_market(line + d)))
        q = ((nb or {}).get("quotes") or {}).get(book, {}).get(side)
        if not q:
            continue
        n_odds = q[0]
        home_dir = -1 if d > 0 else 1
        sign = home_dir if side == "home" else -home_dir
        if sign * (n_odds - odds) < -1e-9:
            return False
    return True


SAME_FETCH_MIN = 5.0


def same_fetch(lines: dict) -> dict:
    """Backtest parity (the pre-registered rule paired a book price with Pinnacle's price FROM THE SAME
    FETCH): keep a Pinnacle rung only if it is in Pinnacle's NEWEST fetch for that match (a rung Pinnacle
    has since dropped is stale), and a book quote only if it was seen within SAME_FETCH_MIN of that fetch
    (API-Football delivers every book in Pinnacle's own fetch). Without this a fresh book price is paired
    with an older Pinnacle price — the first live dry run found 2x the backtest's pick rate."""
    newest: dict[str, float] = {}
    for (mid, _), ln in lines.items():
        for _side, (_o, ts) in (ln["quotes"].get("Pinnacle") or {}).items():
            newest[mid] = max(newest.get(mid, 0.0), ts)
    out: dict = {}
    for key, ln in lines.items():
        t0 = newest.get(key[0])
        if t0 is None:
            continue                   # no Pinnacle on this match at all: nothing can be priced
        # Pinnacle keeps only rungs from its newest fetch; books only quotes from that fetch. A rung with
        # no (fresh) Pinnacle price is KEPT without it: the engine cannot price it, but ladder_ok reads the
        # book's neighbouring rungs from it.
        quotes = {b: {s: q for s, q in sq.items()
                      if abs(q[1] - t0) <= (60 if b == "Pinnacle" else SAME_FETCH_MIN * 60)}
                  for b, sq in ln["quotes"].items()}
        pin = quotes.get("Pinnacle") or {}
        if len(pin) < 2:
            quotes.pop("Pinnacle", None)
        out[key] = dict(ln, quotes={b: sq for b, sq in quotes.items() if sq})
    return out


def evaluate(lines: dict, now_ts: float) -> list[dict]:
    """Pure: {(match_id, 'asian_handicap:<line>'): {ko, quotes}} -> one best pick per match."""
    from workers.automation.sharp_engine import evaluate as engine
    lines = same_fetch(lines)
    cands = [c for c in engine(_rule(), lines, now_ts) if ladder_ok(lines, c)]
    best: dict[str, dict] = {}
    for c in cands:
        if c["match_id"] not in best or c["edge"] > best[c["match_id"]]["edge"]:
            best[c["match_id"]] = c
    return list(best.values())


def load_lines(now: datetime) -> dict:
    from workers.api_clients.db import execute_query
    from workers.automation.sharp_engine import lines_from_rows
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.handicap_line, o.bookmaker, o.selection)
               o.match_id::text AS match_id,
               'asian_handicap:' || (o.handicap_line::float8)::text AS market,
               o.bookmaker, lower(o.selection) AS selection,
               o.odds::float8 AS odds, extract(epoch FROM o."timestamp")::float8 AS ts,
               extract(epoch FROM m.date)::float8 AS ko
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE m.date > NOW() + (%s * INTERVAL '1 hour') AND m.date < NOW() + (%s * INTERVAL '1 hour')
           AND m.status = 'scheduled' AND m.date_disputed_at IS NULL
           AND o.market = 'asian_handicap' AND o.handicap_line IS NOT NULL
           AND lower(o.selection) IN ('home', 'away')
           AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND o.bookmaker = ANY(%s)
           AND o."timestamp" < m.date AND o."timestamp" > NOW() - (%s * INTERVAL '1 minute')
         ORDER BY o.match_id, o.handicap_line, o.bookmaker, o.selection, o."timestamp" DESC
        """,
        (LEAD_H, HORIZON_H, ["Pinnacle", *AF_BOOKS], FRESH_MIN),
    )
    return lines_from_rows([dict(r) for r in rows])


def selection_of(cand: dict) -> str:
    line = float(cand["market"].split(":", 1)[1]) + 0.0
    return f"{cand['selection']} {line:+g}"


def run(dry_run: bool = False) -> int:
    """Scan upcoming matches and record new picks. Returns the number stored."""
    from workers.api_clients.db import execute_query, execute_write
    from workers.api_clients.supabase_client import store_bet
    from workers.jobs.daily_pipeline_v2 import _get_bot_id_by_name

    now = datetime.now(timezone.utc)
    picks = evaluate(load_lines(now), now.timestamp())
    if not picks:
        console.print("[dim]ah_sharp_outlier: 0 candidates[/dim]")
        return 0
    bid = _get_bot_id_by_name(BOT)
    if not bid and not dry_run:
        console.print(f"[yellow]ah_sharp_outlier: {BOT} not registered (migration 476) — nothing stored[/yellow]")
        return 0
    # one pick per match, ever: a later run never adds a second line or side
    have = {str(r["match_id"]) for r in execute_query(
        "SELECT DISTINCT match_id FROM simulated_bets WHERE bot_id = %s", (bid,))} if bid else set()
    stored = 0
    for p in picks:
        sel = selection_of(p)
        reasoning = (f"[{BOT}] AH {sel} @ {p['odds']:.2f} {p['bookmaker']} | EV vs Pinnacle "
                     f"{p['edge'] * 100:+.1f}% (fair {1 / p['p_fair']:.2f}) | quote {p['quote_age_min']:.0f} min old")
        if dry_run:
            console.print(f"  DRY {p['match_id'][:8]} {reasoning}")
            continue
        if p["match_id"] in have:
            continue
        ip = 1 / p["odds"]
        bet_id = store_bet(bid, p["match_id"], {
            "market": "asian_handicap", "selection": sel, "odds": p["odds"],
            "model_prob": p["p_fair"], "implied_prob": ip, "edge": p["p_fair"] - ip,
            "calibrated_prob": round(p["p_fair"], 4), "stake": STAKE,
            "placed_at": now.isoformat(), "reasoning": reasoning,
            "recommended_bookmaker": p["bookmaker"], "timing_cohort": "all",
            "model_version": MODEL_VERSION,
        })
        if bet_id:
            stored += 1
            have.add(p["match_id"])
            # The PUBLIC price of this pick is the price it was taken at. pick_price's "best available on
            # any publishable book" would also count a quote this rule refused (EV >= 15%, a broken ladder,
            # a direct feed) — on AH that is exactly where wrong quotes live, so it would inflate the record.
            execute_write(
                "UPDATE simulated_bets SET odds_at_pick_available = %s "
                "WHERE id = %s AND (odds_at_pick_available IS NULL OR odds_at_pick_available > %s)",
                (p["odds"], bet_id, p["odds"]))
    console.print(f"[green]ah_sharp_outlier: {stored} new picks ({len(picks)} candidates)[/green]")
    return stored


if __name__ == "__main__":
    import sys
    run(dry_run="--dry-run" in sys.argv)
