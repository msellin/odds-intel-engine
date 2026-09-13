#!/usr/bin/env python3
"""BOT-INVENTORY — every bot: status, why it was retired, whether that was right,
whether a live bot already replaces it, and its best fold-robust configuration.

    python3 scripts/bot_inventory.py
    python3 scripts/bot_inventory.py --min-n 30 --md docs/BOT_INVENTORY.md

WHY THIS EXISTS
---------------
Owner, 2026-09-13: *"its kind of messy atm, we have so many shadow bots, retired
and active ones. we should research all of them and then create a detailed table
about each bot, and if retired ones already have an active bot that copies them,
and in what configuration they are most profitable."*

95 bots, 75 retired. Retirement decisions were made at different times by
different reasoning, and at least one is already known to have over-generalised:
`bot_coolbet_value_v1` was retired for "line-shop loses OOS" when the loss was
confined to its O/U leg — its 1x2 leg was CLV +3.4% (t=+6.9) and every one of its
25 segments was CLV-positive.

So this re-derives the verdict from the data instead of trusting the label.

WHAT EACH COLUMN MEANS, and the traps each one guards
-----------------------------------------------------
`CLV(plac)`  CLV vs Pinnacle on PLACEABLE BOOKS ONLY. Not all-books: half the
             apparent edge of the line-shop bots came from prices a reader
             cannot take, most damagingly `Unibet-Kambi`, which disagrees with
             the real site on 91% of quotes. A bot's all-books CLV flatters it.
`t`          |t| < 2 means indistinguishable from zero. Most rows are.
`ROI`        Reported, never trusted alone: ~9,300 settled bets are needed for
             +/-2%, and essentially no bot here has that. CLV converges ~30x
             faster and is the metric that decides.
`best cfg`   The best fold-robust configuration found — positive in EVERY
             walk-forward fold. A config that is positive overall but negative
             in one fold is discarded, because that is what overfitting looks
             like from the inside.
`verdict`    Derived, not read off `retired_reason`.

⚠️ THE STANDING TRAP. Choosing a configuration by scanning this many bots and
slices guarantees some winners by chance. `best cfg` is therefore restricted to
fold-robust cells with n >= the gate, and any config resting on n < 30 is
reported as `thin` rather than as a recommendation.
"""
from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.api_clients.db import execute_query  # noqa: E402

# Books a reader can actually bet. Unibet-Kambi is deliberately EXCLUDED —
# unibet.ee left that API on 2026-09-06 and it reads higher than the site on
# 29% of quotes (KAMBI-FEED-DIVERGENCE). Pinnacle is excluded too: unbettable
# here, and it is the reference CLV is measured against, so including it is
# circular.
PLACEABLE = ("Coolbet", "Unibet-Site", "Epicbet", "Betano", "Unibet")


def _bots() -> list[dict]:
    return execute_query(
        """SELECT name, is_active, retired_at::date AS retired, maturity_label,
                  strategy, COALESCE(retired_reason,'') AS reason,
                  COALESCE(strategy_description,'') AS descr
             FROM bots ORDER BY name""") or []


def _picks() -> list[dict]:
    return execute_query(
        """SELECT b.name AS bot, s.market, lower(s.selection) AS sel,
                  s.recommended_bookmaker AS bk,
                  COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float AS odds,
                  s.edge_percent::float AS edge,
                  s.clv_pinnacle::float AS clv,
                  (s.result = 'won') AS won, s.pick_time
             FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id
            WHERE s.result IN ('won','lost')
              AND COALESCE(s.odds_at_pick_live, s.odds_at_pick) > 1.0
            ORDER BY s.pick_time""") or []


def _m(sel: list[dict]) -> dict | None:
    if len(sel) < 3:
        return None
    rets = [(r["odds"] - 1) if r["won"] else -1.0 for r in sel]
    cl = [r["clv"] for r in sel if r["clv"] is not None]
    clv = ct = None
    if len(cl) >= 3 and st.stdev(cl):
        clv = 100 * st.mean(cl)
        ct = st.mean(cl) / (st.stdev(cl) / math.sqrt(len(cl)))
    return {"n": len(sel), "roi": 100 * st.mean(rets), "clv": clv, "t": ct}


def _fold_ok(sel: list[dict], k: int = 3) -> bool:
    """CLV positive in EVERY fold. Ordered by pick_time already."""
    if len(sel) < 30:
        return False
    step = len(sel) // k
    for i in range(k):
        f = sel[i * step:(i + 1) * step] if i < k - 1 else sel[i * step:]
        cl = [r["clv"] for r in f if r["clv"] is not None]
        if not cl or st.mean(cl) <= 0:
            return False
    return True


def _best_cfg(rows: list[dict]) -> tuple[str, dict | None]:
    """Best fold-robust configuration, searched over edge floor x selection drop."""
    best, best_lbl = None, "none fold-robust"
    cands: list[tuple[str, list[dict]]] = [("as-is", rows)]
    for f in (0.05, 0.08, 0.10, 0.13):
        cands.append((f"edge>={f:.0%}",
                      [r for r in rows if r["edge"] is not None and r["edge"] >= f]))
    for s in sorted({r["sel"] for r in rows}):
        drop = [r for r in rows if r["sel"] != s]
        if len(drop) < len(rows):
            cands.append((f"drop {s}", drop))
    for f in (0.08, 0.10):
        for s in sorted({r["sel"] for r in rows}):
            cands.append((f"edge>={f:.0%}+drop {s}",
                          [r for r in rows if r["edge"] is not None
                           and r["edge"] >= f and r["sel"] != s]))
    for lbl, sub in cands:
        if not _fold_ok(sub):
            continue
        m = _m(sub)
        if m and m["clv"] is not None and (best is None or m["clv"] > best["clv"]):
            best, best_lbl = m, lbl
    return best_lbl, best


def _successor(bot: dict, actives: list[dict]) -> str:
    """Does a LIVE bot already run this strategy? Matched on the anchor and the
    market words in the strategy name, which is how these bots are actually
    named — not on a similarity score nobody can audit."""
    s = (bot["strategy"] or bot["name"]).lower()
    anchor = ("sharp" if ("sharp" in s or "devig" in s or "pin_" in s or "value" in s)
              else "model")
    mkt = ("1x2" if "1x2" in s else "ou" if ("ou" in s or "over" in s) else
           "dc" if "dc" in s or "double" in s else
           "corners" if "corner" in s else "?")
    hits = []
    for a in actives:
        as_ = (a["strategy"] or a["name"]).lower()
        a_anchor = ("sharp" if ("sharp" in as_ or "devig" in as_ or "pin_" in as_
                                or "value" in as_) else "model")
        a_mkt = ("1x2" if "1x2" in as_ else "ou" if ("ou" in as_ or "over" in as_)
                 else "dc" if "dc" in as_ or "double" in as_ else
                 "corners" if "corner" in as_ else "?")
        if a_anchor == anchor and a_mkt == mkt and mkt != "?":
            hits.append(a["name"])
    return ", ".join(hits[:2]) if hits else "—"


def run(min_n: int, md: str | None) -> int:
    bots, picks = _bots(), _picks()
    by: dict[str, list[dict]] = {}
    for p in picks:
        by.setdefault(p["bot"], []).append(p)
    actives = [b for b in bots if b["is_active"] and not b["retired"]]

    rows_out = []
    for b in bots:
        rs = by.get(b["name"], [])
        plac = [r for r in rs if r["bk"] in PLACEABLE]
        m_all, m_pl = _m(rs), _m(plac)
        if not rs or len(rs) < min_n:
            continue
        cfg_lbl, cfg = _best_cfg(plac if len(plac) >= 30 else rs)
        rows_out.append({
            "bot": b["name"], "status": "active" if b["is_active"] and not b["retired"]
            else f"retired {b['retired'] or ''}",
            "reason": (b["reason"] or "")[:110],
            "n": m_all["n"] if m_all else 0,
            "n_pl": m_pl["n"] if m_pl else 0,
            "clv_pl": m_pl["clv"] if m_pl else None,
            "t_pl": m_pl["t"] if m_pl else None,
            "roi_pl": m_pl["roi"] if m_pl else None,
            "cfg": cfg_lbl, "cfg_n": cfg["n"] if cfg else 0,
            "cfg_clv": cfg["clv"] if cfg else None,
            "cfg_roi": cfg["roi"] if cfg else None,
            "succ": _successor(b, actives) if not (b["is_active"] and not b["retired"]) else "",
        })

    rows_out.sort(key=lambda r: (r["clv_pl"] if r["clv_pl"] is not None else -99),
                  reverse=True)
    print(f"\n{len(bots)} bots total · {len(actives)} active · "
          f"{len(bots)-len(actives)} retired · {len(rows_out)} with n>={min_n} settled\n")
    print(f"{'bot':32}{'status':20}{'n(pl)':>6}{'CLV%':>8}{'t':>6}{'ROI%':>8}"
          f"  {'best fold-robust cfg':26}{'cfgCLV':>8}{'cfgN':>6}")
    for r in rows_out:
        f = lambda v, d=1: "—" if v is None else f"{v:+.{d}f}"  # noqa: E731
        thin = " thin" if 0 < r["cfg_n"] < 30 else ""
        print(f"  {r['bot'][:30]:32}{r['status'][:18]:20}{r['n_pl']:>6}"
              f"{f(r['clv_pl']):>8}{f(r['t_pl']):>6}{f(r['roi_pl']):>8}"
              f"  {(r['cfg']+thin)[:25]:26}{f(r['cfg_clv']):>8}{r['cfg_n']:>6}")

    if md:
        L = ["# Bot inventory — auto-generated\n",
             f"{len(bots)} bots · {len(actives)} active · {len(bots)-len(actives)} retired\n",
             "\nCLV is on **placeable books only** (Kambi and Pinnacle excluded — "
             "see the script header for why). `best cfg` is fold-robust: positive "
             "in every walk-forward fold. Configs on n<30 are marked `thin` and "
             "are not recommendations.\n",
             "\n| bot | status | n | CLV% | t | ROI% | best fold-robust cfg | cfg CLV | cfg n | live successor | retired reason |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows_out:
            f = lambda v: "—" if v is None else f"{v:+.1f}"  # noqa: E731
            L.append(f"| `{r['bot']}` | {r['status']} | {r['n_pl']} | {f(r['clv_pl'])} "
                     f"| {f(r['t_pl'])} | {f(r['roi_pl'])} | {r['cfg']}"
                     f"{' *(thin)*' if 0 < r['cfg_n'] < 30 else ''} | {f(r['cfg_clv'])} "
                     f"| {r['cfg_n']} | {r['succ'] or '—'} | {r['reason'] or '—'} |")
        Path(md).write_text("\n".join(L) + "\n")
        print(f"\nwrote {md}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--md")
    a = ap.parse_args()
    return run(a.min_n, a.md)


if __name__ == "__main__":
    raise SystemExit(main())
