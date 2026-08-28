"""SHADOW-BOT-FIXES-2026-08-26 — point-in-time replay engine for shadow bots.

Answers two questions the live shadow data is far too small to answer:

  1. Does a config change hold up over a long horizon, or is the week of live
     data that motivated it just noise? (LINESHOP-SHIN-DEVIG, SHADOW-PROMOTION-GATE)
  2. What would a RETIRED bot have returned if it had kept betting after its
     retirement date? (SHADOW-RETIRED-COUNTERFACTUAL)

Method — strictly point-in-time, no look-ahead:
  * pick price   = last odds snapshot at or before (kickoff - PICK_LEAD_HOURS)
  * model prob   = last `predictions` row CREATED at or before that same instant
  * closing price= last odds snapshot at or before kickoff
  * settlement   = real final score

The pick lead of 3h matches scripts/per_bot_backtest_sweep.py so the numbers
here are comparable with the backtest figures already shown on /admin/shadow-bots.

Usage:
    python3 scripts/lineshop_replay.py --start 2025-01-01 --end 2026-08-26 \
        --bot bot_pin_1x2_home_v1 --devig shin
    python3 scripts/lineshop_replay.py --start 2026-08-24 --end 2026-08-26 \
        --bot bot_pin_1x2_draw_tier4_v1          # post-retirement counterfactual
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import proportional_devig, shin_devig  # noqa: E402

PICK_LEAD_HOURS = 3

# First date on which more than one bookmaker exists in odds_snapshots, i.e. the
# earliest a line-shopping bot could have had anything to shop against. Pinnacle
# goes back to 2023-01-27; Unibet / Betano / 10Bet / Marathonbet all start
# 2026-04-28, 888Sport 04-30, Coolbet 05-20. Verified 2026-08-28.
LINESHOP_DATA_START = "2026-04-28"

ACCESSIBLE = {"Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Pinnacle", "Coolbet"}

# Bot configs as deployed. Kept here rather than imported because the point is
# to replay a bot's config as it was, including configs that have since been
# deleted from daily_pipeline_v2.py when the bot was retired.
#
#   kind = "lineshop"  -> pure price comparison vs de-vigged Pinnacle, no model
#   kind = "model"     -> model probability vs best accessible price
CONFIGS: dict[str, dict] = {
    "bot_pin_1x2_home_v1": {
        "kind": "lineshop", "market": "1x2", "selections": ["home"],
        "tiers": (1, 2), "edge_min": 0.03, "odds_min": 1.30, "odds_max": 6.00,
        "outlier_mult": 1.35,
    },
    # Retired 2026-08-24. Config as it ran: 5% gate, tier 4 only.
    "bot_pin_1x2_draw_tier4_v1": {
        "kind": "lineshop", "market": "1x2", "selections": ["draw"],
        "tiers": (4,), "edge_min": 0.05, "odds_min": 1.30, "odds_max": 6.00,
        "outlier_mult": 1.35, "vig_inclusive": True,
    },
    "bot_sweep_ou25_v1": {
        "kind": "lineshop", "market": "over_under_25", "selections": ["over", "under"],
        "tiers": (1, 2), "edge_min": 0.03, "odds_min": 1.30, "odds_max": 5.00,
        "outlier_mult": 1.30, "max_vig": 0.10, "one_side_only": True,
    },
    "bot_sweep_ou35_v1": {
        "kind": "lineshop", "market": "over_under_35", "selections": ["over", "under"],
        "tiers": (1, 2), "edge_min": 0.03, "odds_min": 1.30, "odds_max": 5.00,
        "outlier_mult": 1.30, "max_vig": 0.10, "one_side_only": True,
    },
    "bot_sweep_1x2_home_v1": {
        "kind": "model", "market": "1x2", "selections": ["home"], "pred": {"home": "1x2_home"},
        "tiers": (2, 3), "edge_min": 0.10, "odds_min": 2.00, "odds_max": 5.00,
        "min_prob": 0.25, "require_pinnacle": True,
    },
    "bot_sweep_1x2_draw_v1": {
        "kind": "model", "market": "1x2", "selections": ["draw"], "pred": {"draw": "1x2_draw"},
        "tiers": (2, 3), "edge_min": 0.05, "odds_min": 1.30, "odds_max": 3.50,
        "min_prob": 0.25, "require_pinnacle": True,
    },
    "bot_sweep_btts_yes_v1": {
        "kind": "model", "market": "btts", "selections": ["yes"], "pred": {"yes": "btts_yes"},
        "tiers": (2, 3), "edge_min": 0.05, "odds_min": 2.00, "odds_max": 2.50,
        "min_prob": 0.25, "require_pinnacle": False,
    },
    # Retired 2026-08-24 — 1X2 home on matches where Pinnacle has NO price.
    "bot_no_pin_home_v1": {
        "kind": "model", "market": "1x2", "selections": ["home"], "pred": {"home": "1x2_home"},
        "tiers": None, "exclude_tier_0": True, "edge_min": 0.08,
        "odds_min": 1.30, "odds_max": 6.00, "min_prob": 0.25,
        "require_pinnacle": False, "forbid_pinnacle": True, "model_sanity_gap": 0.20,
    },
    # Retired 2026-08-21 — same but any selection and no tier-0 / sanity guards
    # (those were added after it was retired, so replaying it without them is
    # what "if it had kept running unchanged" actually means).
    "bot_no_pin_shadow_v1": {
        "kind": "model", "market": "1x2", "selections": ["home", "draw", "away"],
        "pred": {"home": "1x2_home", "draw": "1x2_draw", "away": "1x2_away"},
        "tiers": None, "edge_min": 0.08, "odds_min": 1.30, "odds_max": 6.00,
        "min_prob": 0.25, "require_pinnacle": False, "forbid_pinnacle": True,
    },
}

_COMPLEMENTS = {"1x2": ["home", "draw", "away"], "btts": ["yes", "no"]}


def complements(market: str) -> list[str]:
    if market in _COMPLEMENTS:
        return list(_COMPLEMENTS[market])
    if market.startswith("over_under"):
        return ["over", "under"]
    return []


def fetch_matches(start: str, end: str, market: str) -> list[dict]:
    return execute_query(
        """
        SELECT m.id, m.date, m.score_home, m.score_away, l.tier
          FROM matches m
          JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
           AND m.date >= %s AND m.date < %s
           AND EXISTS (SELECT 1 FROM odds_snapshots o
                        WHERE o.match_id = m.id AND o.market = %s
                          AND o.timestamp <= m.date)
         ORDER BY m.date
        """,
        [start, end, market],
    )


def fetch_prices(match_ids: list[str], market: str, lead_hours: int) -> tuple[dict, dict]:
    """Returns (pick_prices, close_prices), each keyed (match, selection, book)."""
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
               o.match_id, o.selection, o.bookmaker, o.odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.market = %s
           AND o.timestamp <= m.date - (%s || ' hours')::interval
         ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [match_ids, market, str(lead_hours)],
    )
    pick = {(str(r["match_id"]), r["selection"], r["bookmaker"]): float(r["odds"]) for r in rows}
    rows2 = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
               o.match_id, o.selection, o.bookmaker, o.odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.market = %s
           AND o.timestamp <= m.date
         ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [match_ids, market],
    )
    close = {(str(r["match_id"]), r["selection"], r["bookmaker"]): float(r["odds"]) for r in rows2}
    return pick, close


def fetch_preds(match_ids: list[str], markets: list[str], lead_hours: int) -> dict:
    """Ensemble model probability as of (kickoff - lead). Point-in-time: filters
    on predictions.created_at so a later re-score cannot leak backwards."""
    rows = execute_query(
        """
        SELECT DISTINCT ON (p.match_id, p.market)
               p.match_id, p.market, p.model_probability
          FROM predictions p
          JOIN matches m ON m.id = p.match_id
         WHERE p.match_id = ANY(%s::uuid[]) AND p.market = ANY(%s)
           AND p.source = 'ensemble'
           AND p.created_at <= m.date - (%s || ' hours')::interval
         ORDER BY p.match_id, p.market, p.created_at DESC
        """,
        [match_ids, markets, str(lead_hours)],
    )
    return {(str(r["match_id"]), r["market"]): float(r["model_probability"]) for r in rows}


def won(market: str, selection: str, sh: int, sa: int) -> bool | None:
    if market == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sa > sh}[selection]
    if market == "btts":
        yes = sh > 0 and sa > 0
        return yes if selection == "yes" else not yes
    if market.startswith("over_under"):
        line = float(market.replace("over_under_", "")) / 10.0
        total = sh + sa
        if total == line:
            return None  # push
        return total > line if selection == "over" else total < line
    return None


def run(bot: str, start: str, end: str, devig_mode: str, lead: int) -> dict:
    cfg = CONFIGS[bot]
    market = cfg["market"]
    matches = fetch_matches(start, end, market)
    if not matches:
        return {"n": 0}
    # Apply the tier filter BEFORE pulling prices. The price tables hold ~10M
    # 1x2 rows for a few months, so fetching for matches the config will discard
    # anyway is what made the full-history run intractable.
    if cfg.get("tiers") is not None:
        matches = [m for m in matches if m["tier"] in cfg["tiers"]]
    if cfg.get("exclude_tier_0"):
        matches = [m for m in matches if not (m["tier"] is not None and m["tier"] == 0)]
    if not matches:
        return {"n": 0, "drops": {"tier": "all"}}
    ids = [str(m["id"]) for m in matches]
    pick, close = fetch_prices(ids, market, lead)
    preds = {}
    if cfg["kind"] == "model":
        preds = fetch_preds(ids, list(cfg["pred"].values()), lead)

    sides = complements(market)
    devig_fn = shin_devig if devig_mode == "shin" else proportional_devig

    bets: list[dict] = []
    drops: dict = defaultdict(int)

    for m in matches:
        mid, tier = str(m["id"]), m["tier"]
        sh, sa = int(m["score_home"]), int(m["score_away"])

        if cfg.get("tiers") is not None and tier not in cfg["tiers"]:
            drops["tier"] += 1
            continue
        if cfg.get("exclude_tier_0") and (tier is not None and tier == 0):
            drops["tier0"] += 1
            continue

        pin = {s: pick.get((mid, s, "Pinnacle")) for s in sides}
        has_pin = all(pin.get(s) for s in sides)
        if cfg.get("forbid_pinnacle") and any(pin.get(s) for s in sides):
            drops["has_pinnacle"] += 1
            continue
        if cfg.get("require_pinnacle") and not has_pin:
            drops["no_pinnacle"] += 1
            continue
        if cfg["kind"] == "lineshop" and not has_pin:
            drops["no_pinnacle"] += 1
            continue

        candidates = []
        for sel in cfg["selections"]:
            offers = {
                bk: o
                for (mm, ss, bk), o in pick.items()
                if mm == mid and ss == sel and bk in ACCESSIBLE and bk != "Pinnacle"
            }
            if not offers:
                drops["no_soft"] += 1
                continue
            best_bk, best = max(offers.items(), key=lambda kv: kv[1])
            if not (cfg["odds_min"] <= best <= cfg["odds_max"]):
                drops["odds_range"] += 1
                continue

            if cfg["kind"] == "lineshop":
                if best > pin[sel] * cfg["outlier_mult"]:
                    drops["outlier"] += 1
                    continue
                implied = [1.0 / pin[s] for s in sides]
                total = sum(implied)
                if cfg.get("max_vig") is not None and total > 1.0 + cfg["max_vig"]:
                    drops["vig"] += 1
                    continue
                if cfg.get("vig_inclusive"):
                    # Replays the pre-2026-08-24 formula, which compared against
                    # Pinnacle's VIG-INCLUSIVE implied probability.
                    true_p = 1.0 / pin[sel]
                else:
                    probs = devig_fn([pin[s] for s in sides])
                    if probs is None:
                        drops["devig_fail"] += 1
                        continue
                    true_p = probs[sides.index(sel)]
                edge = best * true_p - 1.0
            else:
                mp = preds.get((mid, cfg["pred"][sel]))
                if mp is None:
                    drops["no_pred"] += 1
                    continue
                if mp < cfg.get("min_prob", 0.0):
                    drops["min_prob"] += 1
                    continue
                if cfg.get("model_sanity_gap") is not None:
                    mkt_median = sorted(offers.values())[len(offers) // 2]
                    if mp - (1.0 / mkt_median) > cfg["model_sanity_gap"]:
                        drops["model_sanity"] += 1
                        continue
                true_p = mp
                edge = best * mp - 1.0

            if edge < cfg["edge_min"]:
                drops["low_edge"] += 1
                continue

            w = won(market, sel, sh, sa)
            if w is None:
                drops["push"] += 1
                continue
            candidates.append(
                {
                    "match_id": mid, "sel": sel, "odds": best, "book": best_bk,
                    "edge": edge, "true_p": true_p, "won": w, "date": m["date"],
                    "tier": tier,
                    "close_pin": close.get((mid, sel, "Pinnacle")),
                }
            )

        if not candidates:
            continue
        if cfg.get("one_side_only") or len(cfg["selections"]) == 1:
            bets.append(max(candidates, key=lambda c: c["edge"]))
        else:
            bets.extend(candidates)

    n = len(bets)
    if n == 0:
        return {"n": 0, "drops": dict(drops)}
    rets = [(b["odds"] - 1.0) if b["won"] else -1.0 for b in bets]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    se = (var / n) ** 0.5 if n > 1 else 0.0
    return {
        "n": n,
        "roi": mean * 100,
        "se": se * 100,
        "t": (mean / se) if se else 0.0,
        "hit": 100.0 * sum(1 for b in bets if b["won"]) / n,
        "avg_odds": sum(b["odds"] for b in bets) / n,
        "avg_edge": 100.0 * sum(b["edge"] for b in bets) / n,
        "bets": bets,
        "drops": dict(drops),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--bot", required=True, choices=sorted(CONFIGS) + ["all"])
    ap.add_argument("--devig", default="shin", choices=["shin", "proportional"])
    ap.add_argument("--lead", type=int, default=PICK_LEAD_HOURS)
    ap.add_argument("--by-month", action="store_true")
    args = ap.parse_args()

    bots = sorted(CONFIGS) if args.bot == "all" else [args.bot]
    print(f"replay {args.start} → {args.end}  devig={args.devig}  lead={args.lead}h")

    # LINESHOP-REPLAY-WINDOW-GUARD (2026-08-28): line-shopping needs at least
    # two books quoting the same match. Before 2026-04-28 the odds archive holds
    # PINNACLE ONLY — every other accessible book starts 2026-04-28 (888Sport
    # 04-30, Coolbet 05-20). A replay starting earlier is not "a longer
    # backtest", it is the same four months with a silent zero in front of it,
    # and the old output rendered that as a bare "(no qualifying picks)" line
    # indistinguishable from a genuinely unprofitable config. Say so instead.
    if args.start < LINESHOP_DATA_START:
        print(f"  ⚠️  requested start {args.start} precedes the multi-book archive. "
              f"Only Pinnacle exists before {LINESHOP_DATA_START}, so no line-shop")
        print(f"      comparison is possible there — results below cover "
              f"{LINESHOP_DATA_START} → {args.end} regardless of the flag.")
    print()
    print(f"{'bot':30s} {'n':>6s} {'ROI':>8s} {'SE':>7s} {'t':>6s} {'hit':>6s} {'odds':>6s} {'edge':>7s}")
    print("-" * 82)
    for b in bots:
        r = run(b, args.start, args.end, args.devig, args.lead)
        if not r.get("n"):
            print(f"{b:30s} {'0':>6s}   (no qualifying picks)  drops={r.get('drops')}")
            continue
        print(
            f"{b:30s} {r['n']:6d} {r['roi']:+7.2f}% {r['se']:6.2f}% {r['t']:+6.2f} "
            f"{r['hit']:5.1f}% {r['avg_odds']:6.2f} {r['avg_edge']:+6.1f}%"
        )
        if args.by_month:
            by = defaultdict(list)
            for bet in r["bets"]:
                by[bet["date"].strftime("%Y-%m")].append(
                    (bet["odds"] - 1.0) if bet["won"] else -1.0
                )
            for mo in sorted(by):
                v = by[mo]
                print(f"      {mo}  n={len(v):5d}  roi={100*sum(v)/len(v):+7.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
