#!/usr/bin/env python3
"""TRIGGER-WIDEN-AUDIT — the book-agnostic trigger sweep with a WIDE, DATE-based
test window, run per BOOKMAKER, with month + fold detail.

Why this exists (2026-09-10): the count-based 0.7 split trapped the model-anchor
TEST inside 8 days, because Coolbet-priced settled fixtures are ~37% concentrated
in the last 10 days (the board sweep filled the board ~Sep 8). A DATE-based split
(train before CUTOFF, test after) gives a months-long TEST window on REAL odds.

Honesty caveats this audit must carry:
  * Coolbet history is real prices but a BIASED fixture subset before ~Sep 8
    (old search-based ingest hit only mapped leagues; the board sweep is new).
  * Unibet-Site (the only PLACEABLE Unibet feed) has ~50 settled matches — NOT
    testable yet. The deep `Unibet` (AF) feed is NOT a price we can take.
  * Betano is placeable AND deep (back to Apr) — the one honest wide-window book.

Re-run when more data has accrued (esp. Coolbet full-board + Unibet-Site):
    python3 scripts/trigger_widen_audit.py [--cutoff 2026-08-01]
Read-only.
"""
from __future__ import annotations
import argparse, sys, datetime as dt
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query           # noqa: E402
from workers.automation.coolbet_placer import _min_odds_for  # noqa: E402
from workers.model.devig import devig                       # noqa: E402


def _load_model(bookmaker: str):
    return execute_query("""
      WITH bk AS (
        SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
         WHERE o.market='1x2' AND o.bookmaker=%s AND o.timestamp<=m.date
           AND m.status='finished' AND m.result IS NOT NULL
         ORDER BY o.match_id,o.selection,o.timestamp DESC)
      SELECT m.date, bk.selection, bk.odds, p.model_probability::float praw,
             (bk.selection=m.result::text)::int won
        FROM bk JOIN matches m ON m.id::text=bk.mid
        JOIN LATERAL (SELECT model_probability FROM predictions
                       WHERE match_id=m.id AND market='1x2_'||bk.selection
                       ORDER BY model_version DESC LIMIT 1) p ON true
    """, (bookmaker,))


def _load_sharp(bookmaker: str):
    return execute_query("""
      WITH bk AS (
        SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
         WHERE o.market='1x2' AND o.bookmaker=%s AND o.timestamp<=m.date
           AND m.status='finished' AND m.result IS NOT NULL
         ORDER BY o.match_id,o.selection,o.timestamp DESC),
      pinraw AS (
        SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
         WHERE o.market='1x2' AND o.bookmaker='Pinnacle' AND o.timestamp<=m.date AND m.status='finished'
         ORDER BY o.match_id,o.selection,o.timestamp DESC),
      pin AS (SELECT mid, max(odds) FILTER (WHERE selection='home') ph,
                          max(odds) FILTER (WHERE selection='draw') pd,
                          max(odds) FILTER (WHERE selection='away') pa
                FROM pinraw GROUP BY mid)
      SELECT m.date, bk.selection, bk.odds, pin.ph,pin.pd,pin.pa, m.result::text result
        FROM bk JOIN matches m ON m.id::text=bk.mid JOIN pin ON pin.mid=bk.mid
       WHERE pin.ph IS NOT NULL AND pin.pd IS NOT NULL AND pin.pa IS NOT NULL
    """, (bookmaker,))


def _report(title, picks):
    print(f"\n  {title}")
    if len(picks) < 20:
        print(f"    UNDERPOWERED — {len(picks)} bets (need 20+)"); return
    picks.sort(key=lambda x: x[0])
    rets = np.array([p[2] for p in picks]); od = np.array([p[1] for p in picks])
    print(f"    OVERALL n={len(picks)}  ROI {rets.mean()*100:+.1f}%  win {(rets>0).mean()*100:.0f}%"
          f"  avg_odds {od.mean():.2f}  span {picks[0][0].date()}..{picks[-1][0].date()}")
    by = defaultdict(list)
    for d,o,r in picks: by[d.strftime('%Y-%m')].append(r)
    months = " | ".join(f"{m}:{np.mean(v)*100:+.0f}%(n{len(v)})" for m,v in sorted(by.items()))
    print(f"    MONTHLY  {months}")
    fsz = max(1, len(picks)//3)
    fs = []
    for i in range(0, len(picks), fsz):
        seg = picks[i:i+fsz]
        if len(seg) >= 15:
            a = np.array([s[2] for s in seg])
            fs.append(f"{seg[0][0].date()}..{seg[-1][0].date()}:{a.mean()*100:+.0f}%(n{len(a)})")
    print(f"    FOLDS    " + "  ".join(fs) + ("  ROBUST+" if fs and all('+' in f.split(':')[1] for f in fs) else "  not-robust"))


def model_cells(bookmaker, cutoff):
    rows = _load_model(bookmaker)
    recs = [(r["date"], float(r["praw"]), float(r["odds"]), int(r["won"]), r["selection"])
            for r in rows if r["praw"] is not None]
    recs.sort(key=lambda x: x[0])
    if len(recs) < 200:
        print(f"\n### MODEL · {bookmaker}: only {len(recs)} rows — skip"); return
    train = [r for r in recs if r[0].date() < cutoff]
    test  = [r for r in recs if r[0].date() >= cutoff]
    print(f"\n### MODEL-ANCHOR · {bookmaker}  (TRAIN {len(train)} rows <{cutoff}; TEST {len(test)} rows >={cutoff})")
    if len(train) < 300:
        print(f"    ⛔ CANNOT WIDEN — only {len(train)} pre-{cutoff} rows to fit the calibrator on. "
              f"{bookmaker} history is too short/recent for a wide-window model test; forward accrual is the only path.")
        return
    iso = IsotonicRegression(out_of_bounds="clip").fit([r[1] for r in train], [r[3] for r in train])
    of = float(_min_odds_for("1x2"))
    for label, sel, e, lo, hi in [
        ("DRAW · 2.8-3.3 · 5% [candidate]","draw",0.05,2.80,3.30),
        ("ALL  · 2.8-12  · 13% [baseline]","all",0.13,2.80,12.0),
    ]:
        picks=[]
        for d,praw,o,won,s in test:
            if sel!="all" and s!=sel: continue
            if not (lo<=o<=hi): continue
            cal=float(iso.predict([praw])[0])
            if cal<=e or cal>=1: continue
            if o>=max(1/(cal-e),of): picks.append((d,o,(o-1) if won else -1.0))
        _report(label, picks)


def sharp_cells(bookmaker):
    rows=_load_sharp(bookmaker); recs=[]
    for r in rows:
        pr=devig([float(r["ph"]),float(r["pd"]),float(r["pa"])])
        if not pr: continue
        idx={"home":0,"draw":1,"away":2}[r["selection"]]; o=float(r["odds"])
        recs.append((r["date"],r["selection"],o,int(r["selection"]==r["result"]),pr[idx]-1/o))
    recs.sort(key=lambda x:x[0])
    print(f"\n### SHARP-ANCHOR · {bookmaker}  (edge=P_sharp[devig Pinnacle]-1/{bookmaker}_odds; {len(recs)} rows, full span)")
    for label,sel,e,lo,hi in [
        ("HOME · 1.0-3.3 · 2% [trustworthy]","home",0.02,1.01,3.30),
        ("DRAW · 1.0-12  · 2%","draw",0.02,1.01,12.0),
        ("ALL  · 5.5-12  · 5% [mirage check]","all",0.05,5.50,12.0),
    ]:
        picks=[(d,o,(o-1) if w else -1.0) for (d,s,o,w,se) in recs
               if (sel=="all" or s==sel) and lo<=o<=hi and se>=e]
        _report(label, picks)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cutoff", default="2026-08-01")
    a=ap.parse_args(); cutoff=dt.date.fromisoformat(a.cutoff)
    print("="*78); print(f"TRIGGER-WIDEN-AUDIT — date-split TEST >= {cutoff}, real odds per book"); print("="*78)
    for bk in ("Coolbet","Betano","Unibet"):   # Coolbet+Betano placeable; Unibet=AF (NOT placeable — caveat)
        model_cells(bk, cutoff)
    for bk in ("Coolbet","Betano"):
        sharp_cells(bk)
    print("\nNOTE: `Unibet` above is the AF feed (deep but NOT the placeable price). "
          "`Unibet-Site` (real) has ~50 settled matches — re-run this when it has 300+.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
