#!/usr/bin/env python3
"""BOTS-DESCRIBE — how does every bot actually differ? One table, one command.

    python3 scripts/bots_describe.py
    python3 scripts/bots_describe.py --active-only --show-sql

WHY THIS EXISTS
---------------
Owner, 2026-09-11, on adding two more bots: *"we created more bots now? can we
later understand how the bots differ?"* A fair question — there are 16 active
bots and the count only goes up, and today only the ones on `BotConfig` state
their gates declaratively. Everything else keeps them scattered across a job
file, which is precisely how two near-identical mirrors drifted apart badly
enough to cost real bets.

So this prints the gates side by side and is explicit about which bots can
still answer the question only by reading code. That second column is the
honest measure of how far the pick_generator migration has actually got.

WHAT "DIFFER" MEANS HERE — the axes that actually change behaviour:
    candidate SOURCE   which fixtures it can even consider (the ~100x axis:
                       pipeline picks vs every fixture we model)
    ANCHOR             what the edge is measured against (our model / de-vigged
                       Pinnacle / another book) — NEVER comparable across kinds,
                       which is why a 3% sharp floor and a 13% model floor are
                       both correct
    market + selection what it bets
    edge + odds floors the gates
    books              where it may bet
    money              can the UI placer stake it

Anything not on that list is not a real difference and should not become a new
bot.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _configured() -> dict:
    from workers.automation.bot_configs import ALL_CONFIGS
    return {c.bot_name: c for c in ALL_CONFIGS}


def _db_bots(active_only: bool):
    from workers.api_clients.db import execute_query
    where = "WHERE is_active AND retired_at IS NULL" if active_only else ""
    return execute_query(f"""
        SELECT name, maturity_label AS maturity, strategy,
               strategy_description AS descr,
               (retired_at IS NOT NULL) AS retired
          FROM bots {where} ORDER BY name
    """)


def _anchor(name: str, strategy: str | None) -> str:
    n, s = name.lower(), (strategy or "").lower()
    if "sharp" in n or "devig" in s or "pin_" in n:
        return "sharp/de-vig"
    if "line_shop" in s or "sweep" in n or "lineshop" in s:
        return "line-shop"
    return "model"


def run(active_only: bool, show_sql: bool) -> int:
    cfgs = _configured()
    try:
        from scripts.place_coolbet_ui import PLACEABLE_BOTS
    except Exception:  # noqa: BLE001
        PLACEABLE_BOTS = set()

    rows = _db_bots(active_only)
    print(f"\n{'='*118}")
    print(f"BOT COMPARISON — {len(rows)} bots"
          f"{' (active only)' if active_only else ''}   ·   "
          f"{len(cfgs)} declared via BotConfig")
    print(f"{'='*118}")
    print(f"{'bot':34}{'anchor':14}{'source':12}{'market':16}{'sel':8}"
          f"{'edge':>7}{'odds':>6}{'€':>3}  cfg")
    for r in rows:
        name = r["name"]
        c = cfgs.get(name)
        money = "Y" if name in PLACEABLE_BOTS else "-"
        if c:
            from workers.automation.coolbet_placer import (
                min_edge_for_pick, _min_odds_for,
            )
            mkt = c.markets[0] if len(c.markets) == 1 else f"{len(c.markets)} mkts"
            sel = ",".join(c.selections) if c.selections else "all"
            m0 = c.markets[0]
            of = c.odds_floor if c.odds_floor is not None else float(_min_odds_for(m0))
            if c.edge_floor is not None:
                ef = c.edge_floor
            else:
                s0 = (c.selections or ("home",))[0]
                ef = float(min_edge_for_pick(m0, s0, of))
            print(f"{name[:33]:34}{_anchor(name, r['strategy']):14}"
                  f"{c.prob_source:12}{mkt[:15]:16}{sel[:7]:8}"
                  f"{ef:>7.2f}{of:>6.2f}{money:>3}  ✓")
        else:
            # Not declarative: its gates live in a job file and this table
            # cannot state them. That is the finding, not a formatting gap.
            print(f"{name[:33]:34}{_anchor(name, r['strategy']):14}"
                  f"{'(in code)':12}{'?':16}{'?':8}"
                  f"{'?':>7}{'?':>6}{money:>3}  —")

    missing = [r["name"] for r in rows if r["name"] not in cfgs]
    print(f"\n{'-'*118}")
    print(f"cfg ✓ = gates are DECLARATIVE (pick_generator BotConfig) and this "
          f"table can state them.")
    print(f"cfg — = gates live in a job file; answering \"how does it differ\" "
          f"means reading code.")
    print(f"\n  declarative: {len(rows) - len(missing)} of {len(rows)}")
    if missing:
        print(f"  still in code ({len(missing)}): " + ", ".join(missing))
        print("\n  That list is the pick_generator migration backlog. Every bot on "
              "it is a bot whose\n  gates can drift from the registry unnoticed — "
              "which is exactly what happened to the\n  two mirrors before they "
              "were migrated (one pre-filtered on the pipeline's odds, the\n  "
              "other applied no odds floor at all).")
    if show_sql:
        print(f"\n{'-'*118}\nSTRATEGY DESCRIPTIONS (DB, for the non-declarative ones)\n"
              f"{'-'*118}")
        for r in rows:
            if r["name"] in cfgs:
                continue
            print(f"\n  {r['name']}  [{r['maturity']}]")
            print(f"    {(r['descr'] or '(none)')[:400]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--active-only", action="store_true", default=True)
    ap.add_argument("--all", dest="active_only", action="store_false",
                    help="include retired bots")
    ap.add_argument("--show-sql", action="store_true",
                    help="also dump the DB strategy_description of any bot whose "
                         "gates are not declarative")
    a = ap.parse_args()
    return run(a.active_only, a.show_sql)


if __name__ == "__main__":
    raise SystemExit(main())
