"""
Production audit + WinnerOdds same-window comparison.

Window: 2026-05-01 → 2026-06-07 (regular European club football, pre-WC,
pre-summer-break). Cleaner than the 06-08 → 06-21 window because:
- Top-5 leagues still active
- No WC distortion
- Pre-isotonic activation (so we're auditing the era we have most data for)

5 variants computed:

  v1 PROD_AS_IS          actual placed odds + production prob/filter
  v2 PROD_AT_MAX         same picks, MAX odds at T-30min instead of placed
  v3 RAW_PROB_MAX        ALL picks the model raw_prob would fire, MAX odds
  v4 RAW_MAX_7PCT        v3 with edge >= 7% (the threshold that works in
                         the Buchdahl backtest)
  v5 WO_SAME_WINDOW      WinnerOdds public picks in same window
                         (their reported ROI)

The diff v1 → v2 tells us "would price-shopping fix it"
The diff v2 → v3 tells us "would skipping shrinkage fix it"
The diff v3 → v4 tells us "would tightening edge filter fix it"
v5 anchors the comparison to a real product running in the same window
"""
import os, sys, json, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(str(Path(__file__).parent.parent))
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.db import execute_query


import argparse

WINDOW_START_DEFAULT = "2026-05-01"
WINDOW_END_DEFAULT   = "2026-06-08"   # exclusive

# Country universe variants
TOP5_COUNTRIES = {"England", "Germany", "Spain", "Italy", "France"}

# WinnerOdds-covered countries — from analyzing 12mo of their data
# (top 15 by volume in scripts/wo_analysis output). Adjust as we learn more.
WO_COVERED_COUNTRIES = {
    "England", "Germany", "Spain", "Italy", "France",      # top-5
    "Norway", "Sweden", "Iceland", "Denmark", "Finland",   # Nordic
    "Argentina", "Brazil", "Mexico", "USA", "Chile",       # Americas
    "Poland", "Czech Republic", "Austria", "Turkey",       # Eastern Europe
    "Belgium", "Netherlands", "Portugal", "Greece",        # extra UEFA
    "Australia", "Japan", "South Korea", "China",           # APAC
}

STAKE_UNIT = 10.0


# ---------------------------------------------------------------------------
# WinnerOdds — pull same window
# ---------------------------------------------------------------------------

def wo_pull_window(start_iso: str, end_iso: str) -> list[dict]:
    """Period=12 (all), then filter to window client-side. Public endpoint.
    Retries 3× per page on timeout; bails gracefully if a page repeatedly
    fails."""
    import time
    URL = "https://app.winnerodds.com:4000/graphql"
    HEADERS = {"Content-Type": "application/json",
                "Origin": "https://winnerodds.com",
                "Referer": "https://winnerodds.com/",
                "User-Agent": "Mozilla/5.0 Chrome/149.0"}
    Q = """query getStatsMatchesGeneral($sport: String, $statsFilter: StatsFilter, $statsPagination: StatsPagination) {
      getStatsMatchesGeneral(sport: $sport, statsFilter: $statsFilter, statsPagination: $statsPagination) {
        id benefit status apuesta cuota cuota_promedio cantidad unidades fecha_apuesta country
      }
    }"""

    def fetch_page(page):
        body = {"operationName": "getStatsMatchesGeneral",
                "variables": {"statsFilter": {"period": 12, "tournament": None,
                                              "color": None, "live": None,
                                              "search": "", "bank": 3000},
                              "statsPagination": {"my_page": page, "per_page": 100},
                              "sport": "FOOTBALL"},
                "query": Q}
        for attempt in range(3):
            try:
                req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                              headers=HEADERS, method="POST")
                data = json.loads(urllib.request.urlopen(req, timeout=40).read())
                return data.get("data", {}).get("getStatsMatchesGeneral") or []
            except Exception as e:
                if attempt == 2:
                    print(f"  WO page {page}: gave up after 3 attempts ({e})")
                    return None
                wait = 2 ** attempt
                print(f"  WO page {page}: timeout, retrying in {wait}s ({e})")
                time.sleep(wait)
        return None

    rows = []
    for p in range(1, 200):
        chunk = fetch_page(p)
        if chunk is None:
            print(f"  WO: bailing at page {p}; partial result with {len(rows)} bets so far")
            break
        if not chunk: break
        rows.extend(chunk)
        if (p % 5 == 0):
            print(f"  WO: pulled {len(rows)} bets through page {p}")
        if len(chunk) < 100: break

    # Filter to our window
    win = [r for r in rows
           if r.get("fecha_apuesta")
           and start_iso <= r["fecha_apuesta"][:10] < end_iso]
    return win


def wo_summary(rows: list[dict], countries: set | None = None) -> dict:
    def f(r, k): return float(r.get(k) or 0)
    if countries is not None:
        rows = [r for r in rows if r.get("country") in countries]
    settled = [r for r in rows if r.get("status") in ("WIN","LOSE","LOOSE","HALF_WIN","HALF_LOSE","VOID")]
    stake = sum(f(r, "cantidad") for r in settled)
    pnl = sum(f(r, "benefit") for r in settled)
    hits = sum(1 for r in settled if r.get("status") == "WIN")
    hard = sum(1 for r in settled if r.get("status") in ("WIN","LOSE","LOOSE"))
    clvs = [(f(r, "cuota")/f(r, "cuota_promedio") - 1) * 100
            for r in rows if f(r, "cuota") > 0 and f(r, "cuota_promedio") > 0]
    return {
        "n": len(settled),
        "stake": stake,
        "pnl": pnl,
        "roi": 100*pnl/stake if stake else 0,
        "hit_rate": 100*hits/hard if hard else 0,
        "avg_clv": sum(clvs)/len(clvs) if clvs else 0,
        "clv_n": len(clvs),
        "clv_beat_pct": 100*sum(1 for c in clvs if c > 0)/len(clvs) if clvs else 0,
    }


def wo_country_breakdown(rows: list[dict], min_n: int = 10) -> list[tuple]:
    """Per-country ROI breakdown of WO data — what they actually cover."""
    from collections import defaultdict
    def f(r, k): return float(r.get(k) or 0)
    by_c: dict = defaultdict(lambda: {"n":0, "stake":0.0, "pnl":0.0, "wins":0, "hard":0})
    for r in rows:
        c = r.get("country") or "Unknown"
        if r.get("status") not in ("WIN","LOSE","LOOSE","VOID","HALF_WIN","HALF_LOSE"):
            continue
        d = by_c[c]
        d["n"] += 1
        d["stake"] += f(r, "cantidad")
        d["pnl"] += f(r, "benefit")
        if r.get("status") == "WIN":
            d["wins"] += 1
        if r.get("status") in ("WIN","LOSE","LOOSE"):
            d["hard"] += 1
    out = []
    for c, d in by_c.items():
        if d["n"] < min_n:
            continue
        roi = 100*d["pnl"]/d["stake"] if d["stake"] else 0
        hit = 100*d["wins"]/d["hard"] if d["hard"] else 0
        out.append((c, d["n"], roi, hit))
    return sorted(out, key=lambda t: -t[1])


# ---------------------------------------------------------------------------
# Our production picks
# ---------------------------------------------------------------------------

def pull_our_picks(start: str, end: str) -> list[dict]:
    rows = execute_query(
        """
        SELECT
          sb.id, sb.match_id::text AS match_id, sb.market, sb.selection,
          sb.model_probability::float AS model_prob,
          sb.calibrated_prob::float    AS cal_prob,
          sb.odds_at_pick::float       AS captured_odds,
          sb.edge_percent::float       AS edge_pct,
          sb.stake::float              AS stake,
          sb.pnl::float                AS pnl,
          sb.result::text              AS result,
          sb.created_at,
          b.name                       AS bot_name,
          b.maturity_label             AS maturity,
          m.score_home, m.score_away,
          m.date AS kickoff,
          l.country, l.name AS league
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.created_at >= %s::date
          AND sb.created_at <  %s::date
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2', 'o/u', 'over_under_25')
        ORDER BY sb.created_at ASC
        """,
        (start, end),
    )
    return rows


def pull_max_odds(match_ids: list[str], kickoffs: dict) -> dict:
    """Chunked bulk load — for each (match, market, selection), find:
      - max:       MAX odds at T-60 → T-5 (used for v2/v3/v4 reprice)
      - pinnacle:  MAX Pinnacle in same window
      - pin_close: LATEST Pinnacle snapshot before kickoff (closing line)
      - any_close: LATEST any-book snapshot before kickoff (close fallback)
    """
    out: dict = {}
    if not match_ids:
        return out
    CHUNK = 50
    for i in range(0, len(match_ids), CHUNK):
        ids = match_ids[i:i+CHUNK]
        rows = execute_query(
            """
            SELECT match_id::text AS mid, market, selection, bookmaker,
                   odds::float AS odds, timestamp
              FROM odds_snapshots
             WHERE match_id = ANY(%s::uuid[])
               AND market IN ('1x2', 'over_under_25', 'o/u')
               AND odds IS NOT NULL AND odds > 1.0
            """,
            (ids,),
        )
        for r in rows:
            ko = kickoffs.get(r["mid"])
            if not ko:
                continue
            ts = r["timestamp"]
            mkt = "over_under_25" if r["market"] in ("o/u", "over_under_25") else r["market"]
            key = (r["mid"], mkt, r["selection"])
            o = float(r["odds"])
            slot = out.setdefault(key, {
                "max": 0.0, "max_bk": "", "pinnacle": 0.0,
                "pin_close": 0.0, "pin_close_ts": None,
                "any_close": 0.0, "any_close_ts": None, "any_close_bk": "",
            })
            # Pre-kickoff reprice window (T-60 → T-5): MAX and Pinnacle MAX
            if ko - timedelta(minutes=60) <= ts <= ko + timedelta(minutes=5):
                if o > slot["max"]:
                    slot["max"] = o
                    slot["max_bk"] = r["bookmaker"] or ""
                if r["bookmaker"] == "Pinnacle" and o > slot["pinnacle"]:
                    slot["pinnacle"] = o
            # Closing line: LATEST snapshot at-or-before kickoff (allow T+5 grace)
            if ts <= ko + timedelta(minutes=5):
                if r["bookmaker"] == "Pinnacle" and (
                    slot["pin_close_ts"] is None or ts > slot["pin_close_ts"]
                ):
                    slot["pin_close"] = o
                    slot["pin_close_ts"] = ts
                if (slot["any_close_ts"] is None or ts > slot["any_close_ts"]):
                    slot["any_close"] = o
                    slot["any_close_ts"] = ts
                    slot["any_close_bk"] = r["bookmaker"] or ""
    return out


def _clv_pct(placed: float, close: float) -> float | None:
    """CLV% = (placed / close - 1) * 100. Positive = we beat the close."""
    if not placed or not close or placed <= 1.0 or close <= 1.0:
        return None
    return (placed / close - 1.0) * 100


def _agg_clv(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg": 0.0, "beat_pct": 0.0}
    return {
        "n": len(values),
        "avg": sum(values) / len(values),
        "beat_pct": 100 * sum(1 for v in values if v > 0) / len(values),
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def roi_summary(label: str, rows: list[dict]) -> dict:
    if not rows:
        return {"label": label, "n": 0}
    n = len(rows)
    stake = sum(r["stake"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    hits = sum(1 for r in rows if r["won"])
    return {
        "label": label,
        "n": n,
        "stake": stake,
        "pnl": pnl,
        "roi": 100*pnl/stake if stake else 0,
        "hit_rate": 100*hits/n,
    }


def run_audit(picks_in, odds_idx, label: str):
    """Run all variants for a given picks list. Each variant row carries
    'clv_pin' and 'clv_any' so we can aggregate CLV by variant. CLV is the
    placed odds vs LATEST pre-kickoff snapshot at Pinnacle (preferred) or
    any-book (fallback)."""
    cal = [p for p in picks_in if p["maturity"] == "calibrated"]

    def _clv_for(p, placed):
        mkt = "over_under_25" if p["market"] in ("o/u", "over_under_25") else p["market"]
        slot = odds_idx.get((p["match_id"], mkt, p["selection"])) or {}
        return (
            _clv_pct(placed, slot.get("pin_close") or 0.0),
            _clv_pct(placed, slot.get("any_close") or 0.0),
        )

    v1 = []
    for p in cal:
        placed = p["captured_odds"] or 0.0
        clv_pin, clv_any = _clv_for(p, placed)
        v1.append({"stake": p["stake"] or STAKE_UNIT, "pnl": p["pnl"] or 0,
                    "won": p["result"] == "won",
                    "clv_pin": clv_pin, "clv_any": clv_any})

    v2 = []
    for p in cal:
        mkt = "over_under_25" if p["market"] in ("o/u", "over_under_25") else p["market"]
        slot = odds_idx.get((p["match_id"], mkt, p["selection"]))
        if not slot or slot["max"] < 1.05:
            continue
        mx = slot["max"]
        won = p["result"] == "won"
        clv_pin, clv_any = _clv_for(p, mx)
        v2.append({"stake": STAKE_UNIT,
                    "pnl": (mx - 1) * STAKE_UNIT if won else -STAKE_UNIT,
                    "won": won, "clv_pin": clv_pin, "clv_any": clv_any})

    v3, v4 = [], []
    for p in picks_in:
        mkt = "over_under_25" if p["market"] in ("o/u", "over_under_25") else p["market"]
        slot = odds_idx.get((p["match_id"], mkt, p["selection"]))
        if not slot or slot["max"] < 1.05:
            continue
        raw_p = p["model_prob"]
        if raw_p is None or raw_p <= 0:
            continue
        mx = slot["max"]
        edge = raw_p * mx - 1
        won = p["result"] == "won"
        pnl = (mx - 1) * STAKE_UNIT if won else -STAKE_UNIT
        clv_pin, clv_any = _clv_for(p, mx)
        row = {"stake": STAKE_UNIT, "pnl": pnl, "won": won,
                "clv_pin": clv_pin, "clv_any": clv_any}
        if edge >= 0.05:
            v3.append(row)
        if edge >= 0.07:
            v4.append(row)

    return {"label": label, "v1": v1, "v2": v2, "v3": v3, "v4": v4}


def variant_clv(rows: list[dict]) -> dict:
    """Aggregate CLV for a variant. Prefers Pinnacle close, falls back to any."""
    pin = [r["clv_pin"] for r in rows if r.get("clv_pin") is not None]
    any_ = [r["clv_any"] for r in rows if r.get("clv_any") is not None]
    return {"pin": _agg_clv(pin), "any": _agg_clv(any_)}


def print_audit(audit: dict, wo: dict | None):
    print(f"\n[{audit['label']}]")
    print(f"  {'variant':30s} {'n':>6s} {'stake':>10s} {'pnl':>10s} {'ROI':>8s} "
          f"{'hit':>6s} {'CLV(pin)':>10s} {'CLV(any)':>10s} {'beat%':>7s}")
    print(f"  {'-'*100}")
    summaries = {}
    for k, rows in (
        ("v1 PROD_AS_IS (calibrated)",   audit["v1"]),
        ("v2 PROD_AT_MAX (same picks)",   audit["v2"]),
        ("v3 RAW_PROB+MAX 5%edge (all)",  audit["v3"]),
        ("v4 RAW_PROB+MAX 7%edge (all)",  audit["v4"]),
    ):
        s = roi_summary(k, rows)
        clv = variant_clv(rows)
        s["clv"] = clv
        summaries[k] = s
        if s["n"] == 0:
            print(f"  {k:30s} (no data)")
            continue
        pin = clv["pin"]; anyc = clv["any"]
        pin_str = f"{pin['avg']:+.2f}%(n{pin['n']})" if pin["n"] else "  —      "
        any_str = f"{anyc['avg']:+.2f}%(n{anyc['n']})" if anyc["n"] else "  —      "
        beat_str = f"{anyc['beat_pct']:.0f}%" if anyc["n"] else "—"
        print(f"  {s['label']:30s} {s['n']:>6d} {s['stake']:>10.0f} "
              f"{s['pnl']:>+10.0f} {s['roi']:>+7.2f}% {s['hit_rate']:>5.1f}% "
              f"{pin_str:>10s} {any_str:>10s} {beat_str:>7s}")
    if wo is not None:
        wo_clv = f"{wo.get('avg_clv', 0):+.2f}%(n{wo.get('clv_n', 0)})"
        wo_beat = f"{wo.get('clv_beat_pct', 0):.0f}%" if wo.get("clv_n") else "—"
        print(f"  {'v5 WO_SAME_WINDOW':30s} {wo['n']:>6d} {wo['stake']:>10.0f} "
              f"{wo['pnl']:>+10.0f} {wo['roi']:>+7.2f}% {wo['hit_rate']:>5.1f}% "
              f"{'(WO closing)':>10s} {wo_clv:>10s} {wo_beat:>7s}")
    return summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START_DEFAULT)
    ap.add_argument("--end",   default=WINDOW_END_DEFAULT)
    args = ap.parse_args()
    WINDOW_START = args.start
    WINDOW_END = args.end
    print(f"Window: {WINDOW_START} → {WINDOW_END}")
    print()

    # ---- pull our picks ----
    picks = pull_our_picks(WINDOW_START, WINDOW_END)
    print(f"Production picks (simulated_bets) in window: {len(picks):,}")

    # Filter to calibrated bots only — that's what production places real money on
    calibrated = [p for p in picks if p["maturity"] == "calibrated"]
    print(f"  calibrated-bot picks: {len(calibrated):,}")
    print(f"  beta/active/experimental: {len(picks) - len(calibrated):,}")
    print()

    # Build picks set
    match_ids = sorted({p["match_id"] for p in picks})
    kickoffs = {p["match_id"]: p["kickoff"] for p in picks}

    print(f"Bulk-loading MAX odds for {len(match_ids)} matches...")
    odds_idx = pull_max_odds(match_ids, kickoffs)
    print(f"  loaded {len(odds_idx)} (match, market, sel) odds slots")
    print()

    # WinnerOdds (once, used for all passes)
    print("Pulling WinnerOdds picks in same window...")
    wo_rows = wo_pull_window(WINDOW_START, WINDOW_END)
    print(f"  WO bets in window: {len(wo_rows)}")
    wo_all   = wo_summary(wo_rows)
    wo_top5  = wo_summary(wo_rows, TOP5_COUNTRIES)
    wo_cov   = wo_summary(wo_rows, WO_COVERED_COUNTRIES)

    # WO per-country breakdown — what they actually cover in this window
    print()
    print("=" * 80)
    print(f"WinnerOdds per-country breakdown · {WINDOW_START} → {WINDOW_END}")
    print("=" * 80)
    breakdown = wo_country_breakdown(wo_rows, min_n=5)
    print(f"  {'country':24s} {'n':>5s} {'ROI':>7s} {'hit':>6s}")
    for c, n, roi, hit in breakdown[:20]:
        in_top5 = "★" if c in TOP5_COUNTRIES else " "
        print(f"  {in_top5} {c:22s} {n:>5d} {roi:>+6.2f}% {hit:>5.1f}%")
    print(f"  (★ = top-5 European)")

    # ---------------- THREE PASSES ----------------
    print()
    print("=" * 80)
    print(f"PASS A: ALL LEAGUES · {WINDOW_START} → {WINDOW_END}")
    print("=" * 80)
    a = run_audit(picks, odds_idx, "all-leagues")
    s_all = print_audit(a, wo_all)

    print()
    print("=" * 80)
    print(f"PASS B: TOP-5 EUROPEAN ONLY")
    print("=" * 80)
    top5 = [p for p in picks if p.get("country") in TOP5_COUNTRIES]
    print(f"  picks in top-5: {len(top5)} / {len(picks)}")
    b = run_audit(top5, odds_idx, "top-5")
    s_top5 = print_audit(b, wo_top5)

    print()
    print("=" * 80)
    print(f"PASS C: WO-COVERED COUNTRIES (top-5 + Nordic + Americas + Eastern Europe + ...)")
    print("=" * 80)
    wo_cov_picks = [p for p in picks if p.get("country") in WO_COVERED_COUNTRIES]
    print(f"  picks in WO-covered: {len(wo_cov_picks)} / {len(picks)}")
    c = run_audit(wo_cov_picks, odds_idx, "wo-covered")
    s_wocov = print_audit(c, wo_cov)

    # ---------------- HEADLINE ----------------
    print()
    print("=" * 80)
    print("HEADLINE — side-by-side")
    print("=" * 80)
    for label, summaries, wo in (
        ("all-leagues",  s_all,   wo_all),
        ("top-5",        s_top5,  wo_top5),
        ("wo-covered",   s_wocov, wo_cov),
    ):
        print(f"\n  [{label}]")
        for k in ("v1 PROD_AS_IS (calibrated)",
                   "v2 PROD_AT_MAX (same picks)",
                   "v3 RAW_PROB+MAX 5%edge (all)",
                   "v4 RAW_PROB+MAX 7%edge (all)"):
            s = summaries.get(k)
            if not s or s["n"] == 0: continue
            clv = s.get("clv", {})
            pin = clv.get("pin", {"n": 0, "avg": 0, "beat_pct": 0})
            anyc = clv.get("any", {"n": 0, "avg": 0, "beat_pct": 0})
            ref = pin if pin["n"] >= max(1, s["n"] // 4) else anyc
            ref_lbl = "pin" if ref is pin and pin["n"] else "any"
            clv_str = (f"CLV({ref_lbl}) {ref['avg']:+.2f}% beat {ref['beat_pct']:.0f}%/n{ref['n']}"
                       if ref["n"] else "CLV n/a")
            print(f"    {k:32s} ROI {s['roi']:>+6.2f}% on n={s['n']:>4}   {clv_str}")
        print(f"    WO same set                      ROI {wo['roi']:>+6.2f}% on n={wo['n']:>4}, "
              f"CLV {wo['avg_clv']:+.2f}% beat {wo.get('clv_beat_pct',0):.0f}%/n{wo.get('clv_n',0)}")


if __name__ == "__main__":
    main()
