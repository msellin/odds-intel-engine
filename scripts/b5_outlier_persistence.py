#!/usr/bin/env python3
"""B5 — are outlier quotes takeable? ([[#141]])

Pre-registration: dev/active/1x2-model-rebuild-plan.md, "Pre-registration — B5: are
outlier quotes takeable?" (2026-09-24 ~21:30 UTC, written BEFORE looking at the data).
Measurements and decision rule are fixed there; this script implements them.

Question: when a book's 1X2 quote sits >= 5% EV above NEW+'s fair probability, is it
still there when a subscriber could act on it?

  * fair probability = NEW+ (r1x2_comb_v1, OPEN-price variant, walk-forward) — the same
    per-match probability backtests B/B2/B3 use (loaded via backtest_1x2_new_bots);
  * data = odds_snapshots rows with FULL history (older rows keep only opening + latest):
    the retention boundary is measured from the data first and reported;
  * 1X2 only, pre-kickoff (timestamp < matches.date), non-live;
  * episode = the FIRST snapshot where book b quotes a selection at EV = p*odds - 1 >= 5%
    (one per match x book x selection);
  * at +5/+15/+30/+60 min: the book's latest quote at or before that time; if the book
    wrote NO snapshot in (t0, t0 + h] the horizon is "no data" (not "available"), and a
    horizon at/after kickoff is "past kickoff"; shares are over episodes with data;
  * persistence = time from t0 to the first later snapshot with EV < 5% (censored at the
    last pre-kickoff snapshot when it never drops — reported separately);
  * single-snapshot episode = the very next snapshot of that book is already EV < 5%;
  * how it ended: p is fixed per match, so EV can only fall because the book's price fell.
    What differs is whether the MARKET moved with it: consensus = mean proportional-
    de-vigged probability of the selection over the OTHER books' latest triples. Drift
    >= +1pp between t0 and the drop = "market moved"; otherwise "book corrected alone"
    (the outlier was the book's own). The 1pp cut is set here, before the first run.

DECISION RULE (fixed): NAMEABLE in paid picks iff
    share still EV>=5% at +15 min >= 60%  AND  median persistence >= 30 min
    AND single-snapshot share <= 25%;  n episodes < 30 -> INSUFFICIENT.
A book whose snapshot cadence is coarser than 15 min cannot be judged at +5 / +15
(those horizons are mostly "no data") — flagged, and its verdict says so.

Read-only. Output: data/models/_research/1x2/backtest/*_b5.* (gitignored).

    python3 scripts/b5_outlier_persistence.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import backtest_1x2_new_bots as BT  # noqa: E402  (applies prod env, read-only helpers)

EV_MIN = 0.05
HORIZONS_MIN = (5, 15, 30, 60)
RULE_SHARE_AT_15 = 0.60
RULE_MEDIAN_PERSIST_MIN = 30.0
RULE_SINGLE_SNAPSHOT_MAX = 0.25
RULE_MIN_EPISODES = 30
CADENCE_JUDGEABLE_MAX_MIN = 15.0
CONSENSUS_DRIFT_PP = 0.01
CONSENSUS_MAX_AGE_S = 6 * 3600          # another book's triple counts in the consensus if <= 6 h old
FULL_HISTORY_MIN_MEDIAN_ROWS = 6        # retention boundary: median rows per (match, book, sel) above this
OUT_DIR = BT.OUT_DIR
SEL = BT.SEL


def retention_boundary() -> dict:
    rows = BT._q("""
        WITH k AS (
          SELECT o.match_id, o.bookmaker, o.selection, count(*) n,
                 date_trunc('day', m.date) h
            FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
           WHERE m.date >= now() - interval '14 days' AND m.date < now()
             AND o.market = '1x2' AND o.is_live IS NOT TRUE AND o."timestamp" < m.date
           GROUP BY 1, 2, 3, 5)
        SELECT extract(epoch FROM h)::float8 h, percentile_cont(0.5) WITHIN GROUP (ORDER BY n)::float8 med
          FROM k GROUP BY h ORDER BY h""")
    hours = [(r["h"], r["med"]) for r in rows]
    # boundary = first kickoff DATE from which every later date has full history (hour
    # granularity is too noisy: a quiet hour can carry few books)
    boundary = None
    for i in range(len(hours)):
        if all(m > FULL_HISTORY_MIN_MEDIAN_ROWS for _, m in hours[i:]):
            boundary = hours[i][0]
            break
    before = [m for h, m in hours if boundary is not None and h < boundary]
    after = [m for h, m in hours if boundary is not None and h >= boundary]
    return {"boundary_kickoff_utc": BT._iso(boundary), "boundary_epoch": boundary,
            "median_rows_per_key_before": float(np.median(before)) if before else None,
            "median_rows_per_key_after": float(np.median(after)) if after else None}


def load_rows(ids: list[str]) -> pd.DataFrame:
    parts = []
    for i in range(0, len(ids), 500):
        parts += BT._q("""
            SELECT o.match_id::text match_id, o.bookmaker, o.selection, o.odds::float8 odds,
                   extract(epoch FROM o."timestamp")::float8 ts, extract(epoch FROM m.date)::float8 ko
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = '1x2'
               AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date""", (ids[i:i + 500],))
    return pd.DataFrame(parts)


def consensus_at(tri: dict, book: str, sel: str, t: float) -> float | None:
    """Mean de-vigged prob of `sel` over the OTHER books' latest complete triple at or before t."""
    vals = []
    for b, series in tri.items():
        if b == book:
            continue
        ts, P = series
        j = np.searchsorted(ts, t, side="right") - 1
        if j < 0 or t - ts[j] > CONSENSUS_MAX_AGE_S:
            continue
        vals.append(P[j][SEL.index(sel)])
    return float(np.mean(vals)) if len(vals) >= 2 else None


def triples_for_match(g: pd.DataFrame) -> dict:
    """book -> (sorted ts array, list of de-vigged (h,d,a)) using the latest leg of each
    selection at each of that book's snapshot times."""
    out = {}
    for b, gb in g.groupby("bookmaker"):
        if not BT.is_publishable_book(b):
            continue
        last = {}
        ts_list, P = [], []
        for t, gt in gb.sort_values("ts").groupby("ts"):
            for s, o in zip(gt.selection, gt.odds):
                last[s] = o
            if all(s in last for s in SEL):
                inv = np.array([1 / last[s] for s in SEL])
                ts_list.append(t); P.append(inv / inv.sum())
        if ts_list:
            out[b] = (np.array(ts_list), P)
    return out


def analyse(df: pd.DataFrame, fair: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    eps, cad = [], []
    for mid, g in df.groupby("match_id"):
        p = fair[mid]
        tri = triples_for_match(g)
        ko = float(g.ko.iloc[0])
        for b, gb in g.groupby("bookmaker"):
            if not BT.is_publishable_book(b):
                continue
            # cadence per SELECTION series: some sweepers write the three legs a few ms
            # apart, so a per-book timestamp diff would read ~0 min
            for _s, gsel in gb.groupby("selection"):
                uts = np.unique(gsel.ts.to_numpy())
                if len(uts) > 1:
                    od_sorted = gsel.sort_values("ts").odds
                    cad.append({"bookmaker": b, "gap_min": float(np.median(np.diff(uts))) / 60,
                                "repeat_share": float((od_sorted.diff() == 0).iloc[1:].mean())})
            for s, gs in gb.groupby("selection"):
                gs = gs.sort_values("ts")
                ts, od = gs.ts.to_numpy(), gs.odds.to_numpy()
                ev = p[SEL.index(s)] * od - 1
                hit = np.nonzero(ev >= EV_MIN)[0]
                if not len(hit):
                    continue
                i0 = hit[0]
                t0 = ts[i0]
                r = {"match_id": mid, "bookmaker": b, "selection": s, "t0_utc": BT._iso(t0),
                     "minutes_to_ko": (ko - t0) / 60, "odds0": od[i0], "ev0": ev[i0], "p_fair": p[SEL.index(s)],
                     "direct": b in BT.DIRECT_SWEEPER_BOOKS}
                for h in HORIZONS_MIN:
                    th = t0 + 60 * h
                    if th >= ko:
                        r[f"h{h}"] = "past_kickoff"
                        continue
                    j = np.searchsorted(ts, th, side="right") - 1
                    if j <= i0:
                        r[f"h{h}"] = "no_data"
                        continue
                    r[f"h{h}"] = "ev5" if ev[j] >= EV_MIN else ("ev0" if ev[j] >= 0 else "gone")
                drop = np.nonzero(ev[i0 + 1:] < EV_MIN)[0]
                if len(drop):
                    k = i0 + 1 + drop[0]
                    r["persist_min"], r["censored"] = (ts[k] - t0) / 60, False
                    r["single_snapshot"] = bool(k == i0 + 1)
                    c0, c1 = consensus_at(tri, b, s, t0), consensus_at(tri, b, s, ts[k])
                    r["consensus_drift"] = None if c0 is None or c1 is None else c1 - c0
                    r["ended"] = ("unknown_consensus" if r["consensus_drift"] is None else
                                  "market_moved" if r["consensus_drift"] >= CONSENSUS_DRIFT_PP else
                                  "book_corrected_alone")
                else:
                    r["persist_min"], r["censored"] = (ts[-1] - t0) / 60, True
                    r["single_snapshot"] = None if len(ts) == i0 + 1 else False
                    r["consensus_drift"], r["ended"] = None, ("no_followup" if len(ts) == i0 + 1 else "never_dropped")
                eps.append(r)
    return pd.DataFrame(eps), pd.DataFrame(cad)


def book_table(eps: pd.DataFrame, cad: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cmed = cad.groupby("bookmaker").gap_min.median() if len(cad) else pd.Series(dtype=float)
    crep = cad.groupby("bookmaker").repeat_share.mean() if len(cad) else pd.Series(dtype=float)
    for b in sorted(set(eps.bookmaker) | set(cmed.index)):
        e = eps[eps.bookmaker == b]
        r = {"bookmaker": b, "source": "direct_sweeper" if b in BT.DIRECT_SWEEPER_BOOKS else "api_football",
             "cadence_median_gap_min": None if b not in cmed else round(float(cmed[b]), 1),
             "repeat_unchanged_share": None if b not in crep else round(float(crep[b]), 3),
             "n_episodes": int(len(e))}
        for h in HORIZONS_MIN:
            c = e[f"h{h}"].value_counts() if len(e) else pd.Series(dtype=int)
            have = int(c.get("ev5", 0) + c.get("ev0", 0) + c.get("gone", 0))
            r[f"h{h}_n_data"] = have
            r[f"h{h}_no_data"] = int(c.get("no_data", 0))
            r[f"h{h}_past_ko"] = int(c.get("past_kickoff", 0))
            r[f"h{h}_ev5_share"] = None if have == 0 else round(c.get("ev5", 0) / have, 3)
            r[f"h{h}_ev0_share"] = None if have == 0 else round((c.get("ev5", 0) + c.get("ev0", 0)) / have, 3)
        r["median_persist_min"] = None if not len(e) else round(float(e.persist_min.median()), 1)
        r["censored_share"] = None if not len(e) else round(float(e.censored.mean()), 3)
        ss = e.single_snapshot.dropna()
        r["single_snapshot_share"] = None if not len(ss) else round(float(ss.astype(bool).mean()), 3)
        for k in ("book_corrected_alone", "market_moved", "unknown_consensus", "never_dropped", "no_followup"):
            r[f"ended_{k}"] = int((e.ended == k).sum()) if len(e) else 0
        coarse = r["cadence_median_gap_min"] is not None and r["cadence_median_gap_min"] > CADENCE_JUDGEABLE_MAX_MIN
        r["cadence_coarser_than_15min"] = bool(coarse)
        if r["n_episodes"] < RULE_MIN_EPISODES:
            v = "INSUFFICIENT"
        elif coarse:
            v = "CANNOT_JUDGE_+15 (cadence > 15 min)"
        elif r["h15_ev5_share"] is None:
            v = "CANNOT_JUDGE_+15 (no data at +15)"
        else:
            ok = (r["h15_ev5_share"] >= RULE_SHARE_AT_15 and r["median_persist_min"] >= RULE_MEDIAN_PERSIST_MIN
                  and (r["single_snapshot_share"] or 0) <= RULE_SINGLE_SNAPSHOT_MAX)
            v = "NAMEABLE" if ok else "EXCLUDE"
        r["verdict"] = v
        # the two criteria that do NOT need sub-15-minute data, reported so a coarse-cadence
        # book is not read as a fail (descriptive; the rule itself is unchanged)
        r["passes_persistence_and_single_snapshot"] = (
            r["n_episodes"] >= RULE_MIN_EPISODES and r["median_persist_min"] is not None
            and r["median_persist_min"] >= RULE_MEDIAN_PERSIST_MIN
            and (r["single_snapshot_share"] or 0) <= RULE_SINGLE_SNAPSHOT_MAX)
        rows.append(r)
    return pd.DataFrame(rows)


def main() -> int:
    BT.CONN = BT._conn()
    import workers.api_clients.db as DB
    DB.get_conn = BT._ro_get_conn
    DB.execute_query = BT._q
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ret = retention_boundary()
    print(f"retention: {ret}")
    _, comb, diag = BT.walk_forward_probs()
    cache_end = datetime.fromisoformat(diag["cache_last_kickoff"]).timestamp()
    ms = BT._q("""SELECT id::text match_id, extract(epoch FROM date)::float8 ko FROM matches
                   WHERE date >= to_timestamp(%s) AND date <= to_timestamp(%s)""", (ret["boundary_epoch"], cache_end))
    ids = [m["match_id"] for m in ms if m["match_id"] in comb]
    print(f"  {len(ms):,} matches kicked off {ret['boundary_kickoff_utc']} .. {diag['cache_last_kickoff']}; "
          f"{len(ids):,} with a NEW+ OPEN fair probability")
    df = load_rows(ids)
    print(f"  {len(df):,} pre-kickoff 1X2 rows")
    eps, cad = analyse(df, {mid: comb[mid] for mid in ids})
    tab = book_table(eps, cad)
    eps.to_csv(OUT_DIR / "b5_outlier_episodes_b5.csv", index=False)
    tab.to_csv(OUT_DIR / "b5_outlier_books_b5.csv", index=False)

    def group(e):
        if not len(e):
            return {"n": 0}
        out = {"n": int(len(e))}
        for h in HORIZONS_MIN:
            have = e[e[f"h{h}"].isin(["ev5", "ev0", "gone"])]
            out[f"h{h}_n_data"] = int(len(have))
            out[f"h{h}_ev5_share"] = None if not len(have) else float((have[f"h{h}"] == "ev5").mean())
            out[f"h{h}_ev0_share"] = None if not len(have) else float(have[f"h{h}"].isin(["ev5", "ev0"]).mean())
        out["median_persist_min"] = float(e.persist_min.median())
        ss = e.single_snapshot.dropna()
        out["single_snapshot_share"] = None if not len(ss) else float(ss.astype(bool).mean())
        out["ended"] = e.ended.value_counts().to_dict()
        return out
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preregistration": "dev/active/1x2-model-rebuild-plan.md — 'Pre-registration — B5: are outlier quotes takeable?'",
        "retention": ret,
        "window": {"kickoff_from": ret["boundary_kickoff_utc"], "kickoff_to": diag["cache_last_kickoff"],
                   "note": "upper bound = last kickoff in the NEW+ research cache (fair probability needed)"},
        "rule": {"ev_min": EV_MIN, "share_at_15": RULE_SHARE_AT_15, "median_persist_min": RULE_MEDIAN_PERSIST_MIN,
                 "single_snapshot_max": RULE_SINGLE_SNAPSHOT_MAX, "min_episodes": RULE_MIN_EPISODES,
                 "cadence_judgeable_max_min": CADENCE_JUDGEABLE_MAX_MIN, "consensus_drift_pp": CONSENSUS_DRIFT_PP},
        "matches": len(ids), "rows": int(len(df)), "episodes": int(len(eps)),
        "overall": {"api_football": group(eps[~eps.direct]), "direct_sweeper": group(eps[eps.direct])},
        "books": json.loads(tab.to_json(orient="records")),
    }
    (OUT_DIR / "b5_outlier_persistence_summary_b5.json").write_text(json.dumps(summary, indent=1, default=str))
    cols = ["bookmaker", "source", "cadence_median_gap_min", "repeat_unchanged_share", "n_episodes",
            "h5_ev5_share", "h15_ev5_share", "h30_ev5_share", "h60_ev5_share", "h15_ev0_share",
            "h5_no_data", "h15_no_data", "median_persist_min", "censored_share", "single_snapshot_share",
            "ended_book_corrected_alone", "ended_market_moved", "passes_persistence_and_single_snapshot", "verdict"]
    with pd.option_context("display.width", 300, "display.max_columns", 40):
        print(tab.sort_values("n_episodes", ascending=False)[cols].to_string(index=False))
    print(json.dumps(summary["overall"], indent=1, default=str))
    BT.CONN.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
