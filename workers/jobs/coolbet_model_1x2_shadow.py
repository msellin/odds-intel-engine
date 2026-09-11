"""
COOLBET-MODEL-1X2-SHADOW-BOT-2026-09-08 — mirror the calibrated model's 1x2
picks into shadow_bets under bot_coolbet_1x2_model_v1 so they place through the
PROVEN Coolbet UI placer with the validated 2D gate (edge>=13% on
calibrated_prob, odds>=2.80).

Why this bot exists: it REPLACES the paused line-shop 1x2 real-money path. The
line-shop 1x2 raw signal loses out-of-sample; the model-edge 1x2 instrument at
edge>=13% AND odds>=2.80 HOLDS out-of-sample at +48% on the test fold. This job
does NOT invent model logic: it COPIES qualifying picks the calibrated model
already produced.

Source: simulated_bets rows on the calibrated cohort (bots.maturity_label =
'calibrated'), market='1x2', result='pending', future kickoff, edge_percent >=
0.13 (edge is stored as a FRACTION — cal_prob - implied — NOT a percentage; the
placer's _MIN_EDGE_BY_MARKET['1x2']=0.13 compares to it directly),
calibrated_prob NOT NULL, singles only, and the operator has not already
placed/skipped it. DISTINCT ON (match, selection) taking the highest
edge_percent.

NO vocabulary conversion (unlike the O/U mirror): 1x2 is already the placer's
market and home/draw/away are already the placer's selections. Write
market='1x2', selection=sb.selection straight through.

Settlement: '1x2' is a STANDARD match-result market. The generic shadow settler
(settlement.py, matched by the resolver registry's 1x2 predicate) grades it from
the goal score exactly as it grades the line-shop bot's identical 1x2 market —
the corners_ou_% skip in _PENDING_SHADOW_BETS_SQL does NOT touch '1x2'. No
settler branch here.

Real money is OFF BY DEFAULT: the UI placer only stakes this bot when its
coolbet_placer_bots row is toggled ui_place_enabled=true (superadmin, and only
for a bot in the code-level PLACEABLE_BOTS whitelist — COOLBET-PLACER-CONTROL).
This job only writes shadow_bets; it never places or touches a bankroll.

Idempotent: ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
updates the carried fields on re-run rather than duplicating. Run as:
    python3 -m workers.jobs.coolbet_model_1x2_shadow
"""
from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)

BOT_NAME = "bot_coolbet_1x2_model_v1"
SHADOW_COHORT = "coolbet_1x2_model"
STAKE_EUR = 10.0
# FLOORS-ONE-SOURCE (2026-09-11): DERIVE the default from the engine registry
# instead of re-typing it. This read `os.getenv(..., "0.10")` — a literal that
# only *happened* to equal _MODEL_1X2_HOME_FLOOR, with nothing keeping the two
# in step. It was one of six independent copies of the 1x2 edge floor; an engine
# change would have left this mirror quietly selecting on the old number.
#
# The env var stays, so an operator can still override for an experiment — but
# the DEFAULT is now the engine's own value, not a guess that matches today.
from workers.automation.coolbet_placer import (  # noqa: E402
    _MODEL_1X2_HOME_FLOOR, _min_odds_for,
)

# FAVLONG-CUTS-2026-09-09: home-underdogs are the one fold-robust 1x2 engine.
EDGE_FLOOR = float(os.getenv("COOLBET_MODEL_1X2_EDGE_FLOOR",
                             str(_MODEL_1X2_HOME_FLOOR)))
# The odds floor was hardcoded as `>= 2.80` INSIDE the SQL, where no constant
# could reach it. Same source as the placer's gate now.
MIN_ODDS = float(os.getenv("COOLBET_MODEL_1X2_MIN_ODDS",
                           str(_min_odds_for("1x2"))))


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def generate_picks() -> dict:
    """Mirror qualifying calibrated model 1x2 picks into shadow_bets. One row per
    (match, selection) via ON CONFLICT upsert. Never raises."""
    counters = {"scanned": 0, "written": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write

        bot_id = _bot_id()
        if not bot_id:
            log.warning("model-1x2 shadow: bot %s not registered (migration 312 unapplied?)", BOT_NAME)
            return counters

        # Highest-edge calibrated 1x2 pick per (match, selection). selection is
        # already home/draw/away — no conversion. market stays '1x2'.
        rows = execute_query(
            """
            SELECT DISTINCT ON (sb.match_id, sb.selection)
                   sb.match_id::text AS match_id,
                   sb.selection,
                   COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) AS odds_at_pick,
                   sb.calibrated_prob,
                   sb.model_probability,
                   sb.edge_percent
              FROM simulated_bets sb
              JOIN bots    b ON b.id = sb.bot_id
              JOIN matches m ON m.id = sb.match_id
             WHERE b.maturity_label = 'calibrated'
               AND sb.market = '1x2'
               -- FAVLONG-CUTS-2026-09-09: real-money 1x2 = HOME-UNDERDOGS ONLY.
               -- The by-selection backtest found home-underdogs are the one fold-robust
               -- 1x2 engine (robust to ~0.10 edge on odds>=2.80); home-favs lose, aways aren't
               -- robust, draws are a sharp edge the model can't see (ANALYSIS_GOTCHAS §57,
               -- BETTING_GATE_DECISIONS "1x2 by type"). odds>=2.80 also excludes home-favs
               -- (odds<2.0) belt-and-braces with the placer's _min_odds_for('1x2')=2.80.
               AND lower(sb.selection) = 'home'
               -- MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11): the odds/edge
               -- pre-filters that used to sit here are GONE. They filtered on
               -- the PIPELINE's price — whichever book `recommended_bookmaker`
               -- happened to be — which is not a book this bot can bet. Nancy
               -- v Reims was rejected on Betano's 3.15 (edge 0.092) while
               -- Coolbet was live at 3.25 (edge 0.102, CLEARS). The bet was
               -- lost before the placer ever saw it.
               -- (NB: keep literal per-cent signs OUT of this comment.
               --  psycopg2 scans the ENTIRE query string for parameter
               --  placeholders, SQL comments included, so a stray one raises
               --  IndexError before the database ever sees the statement.)
               --
               -- The only sound pre-filter is the one that is mathematically
               -- necessary: edge = cal_prob - 1/odds < cal_prob, so no price
               -- whatsoever can clear unless cal_prob > the floor. That bounds
               -- the candidate set without pre-judging a price.
               AND sb.calibrated_prob > %s
               AND sb.result = 'pending'
               AND sb.combo_legs IS NULL
               AND sb.calibrated_prob IS NOT NULL
               AND sb.user_placed_at IS NULL
               AND sb.user_skipped_at IS NULL
               AND m.date > NOW()
             ORDER BY sb.match_id, sb.selection, sb.edge_percent DESC
            """,
            [EDGE_FLOOR],
        )

        run_id = str(uuid.uuid4())
        for r in rows:
            selection = (r["selection"] or "").strip().lower()
            if selection not in ("home", "draw", "away"):
                # A 1x2 pick whose selection isn't one of the three outcomes is
                # malformed — skip rather than write a row the placer can't act on.
                continue
            counters["scanned"] += 1

            # ── MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11) ──────────────
            # Re-price the model's probability against the books we can ACTUALLY
            # BET, and gate on THAT edge. Previously this copied
            # `odds_at_pick` + `edge_percent` straight from simulated_bets,
            # which meant two defects at once:
            #
            #   1. WRONG BOOK. The carried price is whichever book the pipeline
            #      recommended. Nancy v Reims: rejected on Betano's 3.15
            #      (edge 9.2%) while Coolbet was live at 3.25 (edge 10.2%,
            #      clears). The placer re-checks at the live Coolbet price, so
            #      it can never STAKE a bad price — but it can only ever NARROW
            #      this set, so a pick that clears at our book and not at the
            #      pipeline's is lost before the placer sees it.
            #   2. STALE EDGE. `edge_percent` is stored independently of price
            #      and probability and had drifted on 9 of 11 pending picks
            #      (EDGE-IS-DERIVED-NOT-STORED). Clermont's O/U row carried
            #      edge 0.0800 against a price of 2.49 whose true edge is
            #      0.0766 — the gate passed on a number belonging to a
            #      different price.
            #
            # Both die here: the ONLY thing inherited from the pipeline is
            # `calibrated_prob` (the sole model output). Price, edge and gate
            # all come from the books this bot bets at. Reuses the router's own
            # `_latest_book_odds` (180-min freshness cap, so a stale quote never
            # competes) and `decide_book` (edge per book, best clearing price
            # wins) rather than re-implementing either — the same one-predicate
            # discipline as clears_edge_floor.
            from workers.automation.best_price_router import (
                _latest_book_odds, decide_book,
            )
            cal_prob = float(r["calibrated_prob"] or 0)
            book_odds = _latest_book_odds(r["match_id"], "1x2", selection)
            if not book_odds:
                counters["no_book_price"] = counters.get("no_book_price", 0) + 1
                continue
            decision = decide_book(
                cal_prob, EDGE_FLOOR, MIN_ODDS,
                {b: v["odds"] for b, v in book_odds.items()},
                market="1x2", selection=selection,
            )
            # WHICH book won. This bot is shared across BOTH placeable books
            # (owner 2026-09-11: "we share the bot for the books we place, this
            # 1x2 bot is for both unibet and coolbet"), so the row records where
            # its price came from and the PLACER routes from there — otherwise
            # it would assume Coolbet from the bot's name alone. NB the bot and
            # this job keep their `coolbet_*` names for history: the NAME is
            # Coolbet, the SCOPE is both books.
            won_book = decision.get("winner")          # book NAME, e.g. 'Coolbet'
            if not won_book:
                counters["no_book_clears"] = counters.get("no_book_clears", 0) + 1
                continue
            price = float(decision["winner_odds"])
            # WHICH book won. This bot is shared across BOTH placeable books
            # (owner, 2026-09-11: "we share the bot for the books we place, this
            # 1x2 bot is for both unibet and coolbet"), so the row must say where
            # its price came from — otherwise the placer/router cannot route it
            # and would assume Coolbet from the bot's name alone. NB the bot and
            # this job are still named `coolbet_*` for history; the NAME is
            # Coolbet, the SCOPE is both books.
            # Derived from the winning price, never copied — so `edge_percent`
            # cannot disagree with `odds_at_pick` the way it did on 9 of 11
            # pending picks (EDGE-IS-DERIVED-NOT-STORED).
            edge_at_price = cal_prob - 1.0 / price

            # odds_at_pick == odds_at_pick_live: we already source the live
            # (executable) quote via the COALESCE above, so there is no
            # high-water inflation to distinguish. calibrated_prob is the value
            # the placer's live-edge gate reads.
            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick      = EXCLUDED.odds_at_pick,
                        odds_at_pick_live = EXCLUDED.odds_at_pick_live,
                        model_probability = EXCLUDED.model_probability,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent,
                        recommended_bookmaker = EXCLUDED.recommended_bookmaker""",
                [run_id, SHADOW_COHORT, bot_id, r["match_id"], "1x2", selection,
                 price, price, STAKE_EUR,
                 r["model_probability"], r["calibrated_prob"], edge_at_price,
                 won_book],
            )
            counters["written"] += 1

        log.info("model-1x2 shadow: scanned %d picks, %d written/updated",
                 counters["scanned"], counters["written"])
    except Exception as e:
        log.warning("model-1x2 shadow generate_picks raised (non-fatal): %s", e)
    return counters


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(generate_picks(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
