#!/usr/bin/env python3
"""[[#172]] Re-price every bot's settled pre-match legs at the Estonian books we can actually bet.

WHY. Every bot's recorded odds are "best price at pick time" over whatever book set the writer
used — historically including books the operator cannot reach (Marathonbet, Betano, Pinnacle)
and AF-fed feeds later shown to quote phantom prices (AF `Unibet`, Kambi). Before any real money
the question is whether a bot's value survives when priced ONLY at ACCESSIBLE_BOOKMAKERS.

METHOD (fixed before the first run).
  Population  bot_ledger, result in (won, lost), NOT is_inplay. All sources (sim / shadow /
              forward_test), active and retired bots.
  Price       for each leg and each book in ACCESSIBLE_BOOKMAKERS: the latest odds_snapshots row
              for the SAME (match, market, selection) with timestamp <= pick_time, is_live not
              true, odds > 1. It competes only if pick_time - ts <= ODDS_FRESH_MAX_MIN (the
              executable router's rule, best_price_router), passes the router's ANCHOR-PRICE-
              SANITY check against Pinnacle's latest quote <= pick_time (fail open when none), and
              is not a BLACKLISTED_OU_SOURCES quote on an O/U line. est_odds = max over books.
              Also reported: est over PLACEABLE_BOOKS only (Coolbet + Unibet-Site — the books an
              automated executor exists for). Sensitivity: the pick_price rule (<= ODDS_MAX_LAG_HOURS behind the freshest book,
              <= ODDS_MAX_AGE_HOURS old) — reported, never used for a verdict.
  Markets     1x2, 1x2_1h, btts, double_chance, draw_no_bet, over_under_*, team_total_*,
              corners_ou_* map 1:1 (same market key, lowercase selection). asian_handicap is NOT
              re-priced (ledger 'away -0.5' vs snapshot home-line convention; also no sharp close
              in leg_clv_sharp) — counted as unmapped.
  CLV         p_close = anchor_p_close(status, cons_status, p_close, p_close_cons) from
              leg_clv_sharp (§85/§86). clv_rec = odds_recorded * p_close - 1;
              clv_est = est_odds * p_close - 1 (same legs). clv_cons_est = est_odds *
              p_close_cons - 1 where cons_status = 'ok' — the independent judge for sharp-anchored
              bots (Pinnacle and own book excluded from the consensus; §85 #150 bullet). Where a
              sharp bot has < 20 legs with a consensus close (pre-#113 legs) it falls back to
              clv_est, which is partly circular for a Pinnacle-triggered bot — flagged in the doc.
  ROI         flat 1 unit at est_odds.
  Uncertainty bootstrap over MATCHES, 10,000 resamples, seed 172; one-sided p = share of
              resampled means <= 0. Holm across every bot with >= 50 re-priced legs, on its JUDGE
              metric (clv_cons_est for sharp/consensus-anchored bots, clv_est otherwise).
  Recent      the same scoring restricted to picks >= 2026-09-19 (inside the 7-day full-resolution
              retention window, §59), Holm across bots with >= 50 recent legs — sensitivity only.
  Excluded    #065: model-anchored 1X2 legs picked 2026-05-10 .. 2026-09-14 (home/away swap) and
              model-anchored O/U legs picked 2026-09-03 10:49 .. 2026-09-13 21:00 — scored
              separately, never in a verdict.

Read-only. Writes CSV/JSON to data/models/_research/own172/ (gitignored).

    python3 scripts/analysis/own_track_estonian_reprice.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.automation.anchor_sanity import is_anchor_sane  # noqa: E402
from workers.automation.best_price_router import ODDS_FRESH_MAX_MIN, PLACEABLE_BOOKS  # noqa: E402
from workers.jobs.daily_pipeline_v2 import (  # noqa: E402
    ACCESSIBLE_BOOKMAKERS, ODDS_MAX_AGE_HOURS, ODDS_MAX_LAG_HOURS)
from workers.registry import bot_registry as reg  # noqa: E402
from workers.utils.odds_quality import BLACKLISTED_OU_SOURCES  # noqa: E402

OUT = ROOT / "data" / "models" / "_research" / "own172"
BOOKS = sorted(ACCESSIBLE_BOOKMAKERS)
N_BOOT = 10_000
SEED = 172
MIN_JUDGED = 50
RECENT_FROM = pd.Timestamp("2026-09-19", tz="UTC")
LEDGER_TABLE = {"sim": "simulated_bets", "shadow": "shadow_bets", "forward_test": "picks_forward_test"}

# Anchor of retired bots absent from the registry, from their bots.strategy_description.
RETIRED_ANCHOR = {
    "bot_sweep_ou25_v1": "sharp", "bot_sweep_ou35_v1": "sharp", "bot_pin_1x2_home_v1": "sharp",
    "bot_pin_1x2_draw_tier4_v1": "sharp", "bot_coolbet_value_v1": "sharp",
    "bot_team_total_paper_shadow_v1": "sharp", "bot_corners_paper_shadow_v1": "sharp",
    "bot_1h_1x2_paper_shadow_v1": "sharp",
    "bot_sweep_1x2_home_v1": "sharp?", "bot_sweep_1x2_draw_v1": "sharp?", "bot_sweep_btts_yes_v1": "sharp?",
    "control_junk_anchor": "control",
}
FAMILY_EXTRA = {"control_junk_anchor": "forward_test"}

SWAP_1X2 = (datetime(2026, 5, 10, tzinfo=timezone.utc), datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc))
SWAP_OU = (datetime(2026, 9, 3, 10, 49, tzinfo=timezone.utc), datetime(2026, 9, 13, 21, 0, tzinfo=timezone.utc))


def mappable(market: str) -> bool:
    m = market.lower()
    return (m in ("1x2", "1x2_1h", "btts", "double_chance", "draw_no_bet")
            or m.startswith("over_under_") or m.startswith("team_total_") or m.startswith("corners_ou_"))


def _mgroup(m: str) -> str:
    for p in ("corners_ou_", "team_total_", "over_under_"):
        if m.startswith(p):
            return {"corners_ou_": "corners", "team_total_": "team_total"}.get(p, m)
    return m


def bot_meta(name: str, source: str) -> tuple[str, str, bool]:
    spec = reg.by_name(name)
    if spec is not None:
        return spec.family, spec.anchor, True
    anchor = RETIRED_ANCHOR.get(name, "model")   # every other retired bot is a model/pipeline bot
    fam = FAMILY_EXTRA.get(name, "retired-" + source)
    return fam, anchor, False


def load_legs() -> pd.DataFrame:
    rows = execute_query("""
        SELECT l.source, l.pick_id::text AS pick_id, l.bot_name, l.match_id::text AS match_id,
               l.kickoff, l.pick_time, lower(l.market) AS market, lower(l.selection) AS selection,
               l.odds::float AS odds_rec, l.bookmaker AS book_rec, l.result, l.rule_version,
               b.retired_at,
               anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons)::float AS p_anchor,
               anchor_source(c.status, c.cons_status) AS anchor_src,
               CASE WHEN c.cons_status = 'ok' THEN c.p_close_cons::float END AS p_cons
          FROM bot_ledger l
          LEFT JOIN bots b ON b.name = l.bot_name
          LEFT JOIN leg_clv_sharp c
                 ON c.leg_id = l.pick_id
                AND c.ledger = CASE l.source WHEN 'sim' THEN 'simulated_bets'
                                             WHEN 'shadow' THEN 'shadow_bets'
                                             ELSE 'picks_forward_test' END
         WHERE l.result IN ('won', 'lost') AND NOT l.is_inplay
    """)
    df = pd.DataFrame(rows)
    for c in ("kickoff", "pick_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


PRICE_SQL = """
WITH l AS (
  SELECT * FROM unnest(%(k)s::text[], %(m)s::uuid[], %(mk)s::text[], %(s)s::text[], %(t)s::timestamptz[])
         AS l(k, match_id, market, selection, pick_time)
)
SELECT l.k, bk.b AS book, q.odds, EXTRACT(EPOCH FROM (l.pick_time - q.ts)) / 60.0 AS age_min
  FROM l
  CROSS JOIN unnest(%(books)s::text[]) AS bk(b)
  CROSS JOIN LATERAL (
       SELECT o.odds::float AS odds, o.timestamp AS ts
         FROM odds_snapshots o
        WHERE o.match_id = l.match_id AND o.market = l.market AND lower(o.selection) = l.selection
          AND o.bookmaker = bk.b AND o.is_live IS NOT TRUE AND o.odds > 1
          AND o.timestamp <= l.pick_time
        ORDER BY o.timestamp DESC
        LIMIT 1) q
"""


def fetch_quotes(legs: pd.DataFrame) -> pd.DataFrame:
    out = []
    by_match = legs.groupby("match_id")
    mids = list(by_match.groups.keys())
    for i in range(0, len(mids), 150):
        chunk = legs[legs.match_id.isin(mids[i:i + 150])]
        rows = execute_query(PRICE_SQL, {
            "k": chunk.key.tolist(), "m": chunk.match_id.tolist(), "mk": chunk.market.tolist(),
            "s": chunk.selection.tolist(), "t": [t.to_pydatetime() for t in chunk.pick_time],
            "books": BOOKS + ["Pinnacle"],
        })
        out.extend(rows)
        print(f"  quotes: {min(i + 150, len(mids))}/{len(mids)} matches", end="\r", flush=True)
    print()
    return pd.DataFrame(out, columns=["k", "book", "odds", "age_min"])


def reprice(legs: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    q = q.copy()
    q["age_min"] = q.age_min.astype(float)
    pin = q[q.book == "Pinnacle"].set_index("k").odds.to_dict()
    ours = q[q.book != "Pinnacle"].copy()
    mk = legs.set_index("key").market.to_dict()
    ours["market"] = ours.k.map(mk)
    ours = ours[~(ours.market.str.startswith("over_under") & ours.book.isin(BLACKLISTED_OU_SOURCES))]
    ours["sane"] = [is_anchor_sane(o, pin.get(k)) for k, o in zip(ours.k, ours.odds)]
    ours = ours[ours.sane]
    # strict: the executable router's freshness
    strict = ours[ours.age_min <= ODDS_FRESH_MAX_MIN]
    best = strict.sort_values("odds", ascending=False).drop_duplicates("k").set_index("k")
    # sensitivity: pick_price's rule
    ours["fresh_ts"] = ours.groupby("k").age_min.transform("min")
    pp = ours[(ours.age_min - ours.fresh_ts <= ODDS_MAX_LAG_HOURS * 60) & (ours.age_min <= ODDS_MAX_AGE_HOURS * 60)]
    best_pp = pp.groupby("k").odds.max()
    legs = legs.copy()
    legs["est_odds"] = legs.key.map(best.odds)
    legs["est_book"] = legs.key.map(best.book)
    legs["est_age_min"] = legs.key.map(best.age_min)
    legs["est_odds_pp"] = legs.key.map(best_pp)
    # executor subset: only the books an automated placer exists for today (best_price_router.PLACEABLE_BOOKS)
    legs["est_odds_exec"] = legs.key.map(strict[strict.book.isin(PLACEABLE_BOOKS)].groupby("k").odds.max())
    legs["n_books_any"] = legs.key.map(ours.groupby("k").book.nunique()).fillna(0).astype(int)
    return legs


def boot(df: pd.DataFrame, col: str, rng) -> dict:
    d = df[["match_id", col]].dropna()
    n = len(d)
    if n == 0:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "p": None}
    g = d.groupby("match_id")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    means = s[idx].sum(1) / c[idx].sum(1)
    return {"n": int(n), "mean": float(d[col].mean()), "lo": float(np.percentile(means, 2.5)),
            "hi": float(np.percentile(means, 97.5)), "p": float((means <= 0).mean())}


def holm(ps: dict) -> dict:
    items = sorted(ps.items(), key=lambda x: x[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"ACCESSIBLE_BOOKMAKERS = {BOOKS}; ODDS_FRESH_MAX_MIN = {ODDS_FRESH_MAX_MIN}; "
          f"pick_price rule lag<={ODDS_MAX_LAG_HOURS}h age<={ODDS_MAX_AGE_HOURS}h")
    legs = load_legs()
    legs["key"] = legs.source + "|" + legs.pick_id
    meta = {n: bot_meta(n, s) for n, s in legs[["bot_name", "source"]].drop_duplicates().itertuples(index=False)}
    legs["family"] = legs.bot_name.map(lambda n: meta[n][0])
    legs["anchor"] = legs.bot_name.map(lambda n: meta[n][1])
    legs["mappable"] = legs.market.map(mappable)
    is_model = legs.anchor == "model"
    pt = legs.pick_time
    legs["swap065"] = (is_model & (legs.market == "1x2") & (pt >= SWAP_1X2[0]) & (pt <= SWAP_1X2[1])) | \
                      (is_model & legs.market.str.startswith("over_under") & (pt >= SWAP_OU[0]) & (pt <= SWAP_OU[1]))
    print(f"{len(legs)} settled pre-match legs, {legs.bot_name.nunique()} bots, "
          f"{(~legs.mappable).sum()} unmappable (asian_handicap)")

    q = fetch_quotes(legs[legs.mappable])
    legs = reprice(legs, q)
    won = legs.result == "won"
    legs["clv_rec"] = legs.odds_rec * legs.p_anchor - 1
    legs["clv_est"] = legs.est_odds * legs.p_anchor - 1
    legs["clv_cons_est"] = legs.est_odds * legs.p_cons - 1
    legs["clv_cons_rec"] = legs.odds_rec * legs.p_cons - 1
    legs["clv_cons_exec"] = legs.est_odds_exec * legs.p_cons - 1
    legs["clv_exec"] = legs.est_odds_exec * legs.p_anchor - 1
    legs["roi_est"] = np.where(legs.est_odds.notna(), np.where(won, legs.est_odds - 1, -1.0), np.nan)
    legs["roi_rec"] = np.where(won, legs.odds_rec - 1, -1.0)
    legs["price_ratio"] = legs.est_odds / legs.odds_rec
    legs.to_csv(OUT / "legs.csv", index=False)

    rng = np.random.default_rng(SEED)
    rows = []
    for bot, g in legs.groupby("bot_name"):
        main_g = g[~g.swap065]
        pr = main_g[main_g.est_odds.notna()]
        anchor = g.anchor.iloc[0]
        judge = "clv_cons_est" if anchor in ("sharp", "sharp?", "consensus", "control") else "clv_est"
        r = {
            "bot": bot, "family": g.family.iloc[0], "anchor": anchor,
            "active": bool(pd.isna(g.retired_at.iloc[0])),
            "markets": ",".join(sorted(g.market.map(_mgroup).unique())),
            "first_ko": str(g.kickoff.min().date()), "last_ko": str(g.kickoff.max().date()),
            "settled": len(g), "unmapped": int((~g.mappable).sum()), "swap065": int(g.swap065.sum()),
            "priced": len(pr), "coverage": len(pr) / max(1, len(main_g[main_g.mappable])),
            "priced_pp": int(main_g.est_odds_pp.notna().sum()),
            "coverage_since_0919": (lambda s: s.est_odds.notna().mean() if len(s) else None)(
                main_g[(main_g.pick_time >= "2026-09-19") & main_g.mappable]),
            "price_ratio_med": float(pr.price_ratio.median()) if len(pr) else None,
            "share_est_ge_rec": float((pr.est_odds >= pr.odds_rec - 1e-9).mean()) if len(pr) else None,
            "judge": judge,
            "priced_exec": int(main_g.est_odds_exec.notna().sum()),
            "clv_exec_mean": float(main_g.clv_exec.mean()) if main_g.clv_exec.notna().any() else None,
            "clv_cons_exec_mean": float(main_g.clv_cons_exec.mean()) if main_g.clv_cons_exec.notna().any() else None,
        }
        for col in ("clv_rec", "clv_est", "clv_cons_rec", "clv_cons_est", "roi_est", "roi_rec"):
            b = boot(pr, col, rng)
            for k2, v in b.items():
                r[f"{col}_{k2}"] = v
        if judge == "clv_cons_est" and r["clv_cons_est_n"] < 20:
            judge = r["judge"] = "clv_est"   # no consensus close (pre-#113 legs): Pinnacle close, circular for sharp bots
        # RECENT window: picks inside the 7-day full-resolution retention window (§59), where
        # "latest quote <= pick_time" is not a pruning artefact. Sensitivity, same judge.
        rec = pr[pr.pick_time >= RECENT_FROM]
        for col in ("clv_rec", "clv_est", "clv_cons_rec", "clv_cons_est", "roi_est"):
            b = boot(rec, col, rng)
            for k2, v in b.items():
                r[f"recent_{col}_{k2}"] = v
        # the swap-flagged legs, reported separately
        sw = g[g.swap065 & g.est_odds.notna()]
        r["swap_priced"] = len(sw)
        r["swap_clv_est"] = float(sw.clv_est.mean()) if sw.clv_est.notna().any() else None
        rows.append(r)
    res = pd.DataFrame(rows)
    judged = res[res.priced >= MIN_JUDGED].copy()
    pvals = {}
    for _, r in judged.iterrows():
        p = r[f"{r.judge}_p"]
        if p is not None and not pd.isna(p) and r[f"{r.judge}_n"] >= 20:
            pvals[r.bot] = p
    adj = holm(pvals)
    res["holm_p"] = res.bot.map(adj)
    rp = {r.bot: r[f"recent_{r.judge}_p"] for _, r in res.iterrows()
          if r[f"recent_{r.judge}_n"] >= MIN_JUDGED and not pd.isna(r[f"recent_{r.judge}_p"])}
    res["recent_holm_p"] = res.bot.map(holm(rp))

    def verdict(r):
        if r.priced < MIN_JUDGED:
            return "too few"
        if pd.isna(r.holm_p):
            return "no judge close"
        if r.holm_p < 0.05 and r[f"{r.judge}_mean"] > 0:
            return "survives"
        if r[f"{r.judge}_hi"] is not None and r[f"{r.judge}_hi"] < 0:
            return "negative"
        return "undetermined"
    res["verdict"] = res.apply(verdict, axis=1)
    res = res.sort_values(["verdict", "priced"], ascending=[True, False])
    res.to_csv(OUT / "per_bot.csv", index=False)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(), "books": BOOKS,
        "fresh_max_min": ODDS_FRESH_MAX_MIN, "legs": len(legs),
        "legs_priced": int(legs.est_odds.notna().sum()),
        "legs_unmapped": int((~legs.mappable).sum()), "legs_swap065": int(legs.swap065.sum()),
        "unmapped_by_market": legs[~legs.mappable].market.value_counts().to_dict(),
        "est_book_share": legs.est_book.value_counts(normalize=True).round(3).to_dict(),
        "coverage_by_week": legs[legs.mappable].assign(wk=legs.pick_time.dt.strftime("%G-W%V"))
            .groupby("wk").est_odds.apply(lambda s: round(s.notna().mean(), 3)).to_dict(),
        "n_judged": len(pvals),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    cols = ["bot", "anchor", "settled", "unmapped", "swap065", "priced", "coverage", "coverage_since_0919",
            "price_ratio_med", "clv_rec_mean", "clv_est_mean", "clv_est_lo", "clv_est_hi", "clv_cons_est_n",
            "clv_cons_est_mean", "clv_cons_est_lo", "clv_cons_est_hi", "roi_est_mean", "roi_est_lo",
            "roi_est_hi", "holm_p", "verdict", "recent_clv_est_n", "recent_clv_est_mean",
            "recent_clv_cons_est_n", "recent_clv_cons_est_mean", "recent_holm_p"]
    print(res[cols].round(4).to_string(index=False))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
