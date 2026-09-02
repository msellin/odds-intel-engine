"""
Betaminic vs OddsIntel — apples-to-apples ROI audit (deferred).

Reads dev/active/betaminic_raw.json. If that file is the auth-required stub
(see scripts/scrape_betaminic.py docstring), this audit writes a similarly
auth-gated ledger entry and exits. When the operator later runs the real
scrape with BETAMINIC_COOKIE set, this script will compute ROI in the same
way as audit_vs_signalodds.py / audit_vs_deepbetting.py.

The stub still emits comparison_betaminic.json so the landing page's
comparison block can show "Betaminic — pending" with a date stamp instead
of silently omitting the row.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

INPUT_PATH = ROOT / "dev" / "active" / "betaminic_raw.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_betaminic.json"

STAKE = 10.0
MIN_SAMPLE = 50
DEFAULT_START = "2026-05-04"


# STALE-ODDS-HISTORY-RESTATE-2026-09-02: our side of the comparison now comes
# from scripts/_our_stats.py, which prices at odds that were LIVE at pick time
# rather than summing `pnl` (settled from the inflated `odds_at_pick`). Six
# copies of this query existed; five would have kept publishing the old number.
from scripts._our_stats import our_stats  # noqa: E402,F401


def _print_section(title: str, st: dict) -> None:
    """Same one-line summary the other audits print, so the five outputs can
    be eyeballed side by side."""
    if not st or not st.get("n"):
        print(f"\n[{title}]\n  (no data)")
        return
    print(f"\n[{title}]")
    print(f"  n={st['n']:>5}  stake={st['stake_total']:>9.2f}  "
          f"pnl={st['pnl_total']:>+9.2f}  ROI={st['roi_pct']:>+6.2f}%  "
          f"hit={st['hit_rate_pct']:.2f}%  avg_odds={st['avg_odds']:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_betaminic.py first.",
              file=sys.stderr)
        return 2

    raw = json.loads(INPUT_PATH.read_text())

    # BETAMINIC-PUBLIC-TABLE-2026-09-02. The scraper used to emit a dict
    # carrying {"status": "auth_required"}; it now emits a plain LIST of
    # per-bet rows from the public ShootingBets results table. Accept both so
    # an old raw file on disk still produces the honest stub rather than a
    # crash.
    if isinstance(raw, dict):
        status = raw.get("status", "ok")
        reason = raw.get("reason")
        strategies = raw.get("strategies") or []
        rows = []
    else:
        status, reason, strategies = "ok", None, []
        rows = raw

    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    if status == "auth_required" or (not rows and not strategies):

        print(f"\nBetaminic data is auth-gated (reason: {reason!r}); "
              "writing auth_required ledger entry.")
        out = {
            "source": "Betaminic",
            "source_url": "https://www.betaminic.com/betamin-builder/public-strategies/",
            "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window": {"start": start, "end": end},
            "status": "auth_required",
            "min_sample_each_side": MIN_SAMPLE,
            "scope_notes": (
                "Betaminic gates its strategy ROI behind a free-signup auth "
                "wall. Auto-signup is out of policy (no paywall bypass / no "
                "fabricated numbers). Comparison-block row should render as "
                "\"Betaminic — pending audit (signup required)\" until the "
                "operator runs scripts/scrape_betaminic.py with a logged-in "
                "BETAMINIC_COOKIE."
            ),
            "reproducible_via": "scripts/scrape_betaminic.py + scripts/audit_vs_betaminic.py",
            "their_stats": {"n": 0, "note": reason},
            "our_stats_same_window": ours,
        }
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                          sort_keys=True).encode()
        print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
        print(f"Wrote: {OUT_PATH}")
        return 0

    # Future: real audit. Strategies expected to look like
    #   {"strategy_id": int, "name": str, "settled_bets": [{...}], ...}
    # The settled_bets array follows the same shape as our other audits — we
    # filter to 1X2 + OU 2.5, then compute ROI at STAKE=10.0.

    # ── real-data path ───────────────────────────────────────────────────
    # Scope matches every other audit: 1X2 + OU 2.5 only, settled, priced.
    # Betaminic labels these "Moneyline" and "Totals"; Totals carries the line
    # in its own column, so anything other than 2.5 is dropped rather than
    # silently compared against our 2.5 book (ANALYSIS_GOTCHAS #25).
    drops: Counter = Counter()
    kept: list[dict] = []
    for r in rows:
        d = r.get("kickoff_date")
        if not d:
            drops["no_date"] += 1
            continue
        if not (start <= d < end):
            drops["out_of_window"] += 1
            continue
        mkt = (r.get("market") or "").strip().lower()
        if mkt == "moneyline":
            norm = "1x2"
        elif mkt == "totals":
            if (r.get("line") or "").strip() != "2.5":
                drops[f"totals_line_{r.get('line')}"] += 1
                continue
            norm = "over_under_25"
        else:
            drops[f"market_{mkt or 'blank'}"] += 1
            continue
        res = (r.get("result") or "").strip().upper()
        if res not in ("W", "L"):
            drops[f"result_{res or 'blank'}"] += 1
            continue
        o = r.get("odds")
        try:
            o = float(o) if o is not None else None
        except (TypeError, ValueError):
            o = None
        if o is None or o < 1.01:
            drops["no_odds"] += 1
            continue
        kept.append({**r, "_market": norm, "_odds": o, "_won": res == "W"})

    print(f"After scope filter: {len(kept)} kept, drops={dict(drops)}")

    def _stats(rs: list[dict]) -> dict:
        if not rs:
            return {"n": 0}
        pnl = sum((x["_odds"] - 1.0) * STAKE if x["_won"] else -STAKE for x in rs)
        stake = STAKE * len(rs)
        return {
            "n": len(rs),
            "stake_total": round(stake, 2),
            "pnl_total": round(pnl, 2),
            "roi_pct": round(100.0 * pnl / stake, 2),
            "hit_rate_pct": round(100.0 * sum(1 for x in rs if x["_won"]) / len(rs), 2),
            "avg_odds": round(sum(x["_odds"] for x in rs) / len(rs), 3),
        }

    theirs = _stats(kept)
    by_market = {}
    for m in sorted({x["_market"] for x in kept}):
        by_market[m] = _stats([x for x in kept if x["_market"] == m])

    print("\n" + "=" * 78)
    print(f"Betaminic vs OddsIntel · {start} → {end} · stake {STAKE:.0f} EUR flat · 1X2 + OU 2.5")
    print("=" * 78)
    _print_section("Betaminic (public ShootingBets results)", theirs)
    _print_section("OddsIntel (calibrated+beta+active)", ours)
    for m, st in by_market.items():
        _print_section(f"  market: {m}", st)

    enough = theirs.get("n", 0) >= MIN_SAMPLE and ours.get("n", 0) >= MIN_SAMPLE
    if not enough:
        print(f"\nNOTE: below MIN_SAMPLE={MIN_SAMPLE} on one side — "
              "publishing as insufficient-data-pending.")

    out = {
        "source": "Betaminic",
        "source_url": "https://www.betaminic.com/shootingbets/results/",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start, "end": end},
        "status": "ok" if enough else "insufficient-data-pending",
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "Betaminic ShootingBets public results table (wpDataTables AJAX, "
            "no auth). Moneyline -> 1x2, Totals -> over_under_25 (other lines "
            "dropped, never compared against our 2.5 book). Settled W/L only, "
            "priced at the Bet365 value odds Betaminic publishes, 10 EUR flat."
        ),
        "reproducible_via": "scripts/scrape_betaminic.py + scripts/audit_vs_betaminic.py",
        "their_stats": theirs,
        "their_by_market": by_market,
        "our_stats_same_window": ours,
    }
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                      sort_keys=True).encode()
    print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"Wrote: {OUT_PATH}")

    from scripts._picks_csv import compute_pnl, write_picks_csv  # noqa: E402
    csv_rows = [{
        "source": "betaminic",
        "kickoff_date": r["kickoff_date"],
        "league": r.get("league") or "",
        "home_team": r.get("home_team") or "",
        "away_team": r.get("away_team") or "",
        "market": r["_market"],
        "pick": r.get("selection") or "",
        "odds": f"{r['_odds']:.3f}",
        "result": "won" if r["_won"] else "lost",
        "pnl_per_unit": compute_pnl(r["_odds"], "won" if r["_won"] else "lost"),
        "ref_url": "https://www.betaminic.com/shootingbets/results/",
    } for r in kept]
    n_csv = write_picks_csv(LEDGER_DIR / "picks_betaminic.csv", csv_rows)
    print(f"Wrote {n_csv} rows to {LEDGER_DIR / 'picks_betaminic.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
