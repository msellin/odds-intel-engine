"""
ODDS-FIDELITY-AUDIT — measure data quality across all books we ingest.

The strategic question this answers: is our existing AF feed reliable enough,
or do we need a complementary aggregator (e.g. The Odds API at +$59/mo)?

Five measurements, each per-book where applicable:

1. **Placement freshness** — when a bot picked a bet at time T, how old was
   the odds_snapshot row that informed that decision? Answers "are we
   reacting to stale lines at decision time?"

2. **Pinnacle close-capture staleness** — `simulated_bets.closing_odds` is
   what we use for CLV. How old was the most-recent Pinnacle row in
   odds_snapshots compared to kickoff when we captured the close?
   Answers "is our CLV benchmark actually fresh?"

3. **Implied-sum sanity per book per market** — a real 1X2 market has
   implied-sum 1.02-1.15 (typical overround). Anything outside that range
   for any book signals broken data or systematic line errors.

4. **Cross-book consistency** — for 1X2 lines where 2+ books quote the same
   match-market-selection within a 30-min window, what's the variance?
   High variance = data quality concern OR market disagreement.

5. **AF Pinnacle vs our latest available Pinnacle** — every match has many
   Pinnacle snapshots in our DB. We capture one specific snapshot as the
   "close." Is that capture the freshest available, or are we stopping
   earlier than we should?

Output: dev/active/odds-fidelity-audit-YYYY-MM-DD.md with per-book scorecard,
top concerns, and a Phase 1 recommendation table.

Run: python3 scripts/audit_odds_fidelity.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Mirrors workers/jobs/daily_pipeline_v2.py:992 — these are the books we use
# for value-bet edge math. Books outside this set are ingested for
# comparison only and not pinned by the audit.
ACCESSIBLE_BOOKMAKERS = {
    "Bet365", "Unibet", "Betano", "Marathonbet", "10Bet",
    "888Sport", "Pinnacle", "Coolbet",
}
# Inspect-only books — present in odds_snapshots, currently excluded from
# ACCESSIBLE_BOOKMAKERS. We audit them too so we have data to decide on
# un-exclusion (e.g. Bwin candidate).
INSPECT_ONLY_BOOKMAKERS = {
    "Bwin", "1xBet", "Betfair", "BetVictor", "Dafabet", "William Hill",
    "SBO", "Superbet",
}
# Synthetic / known-broken sources kept out of fidelity scoring entirely.
SYNTHETIC_SOURCES = {
    "api-football", "api-football-live", "Avg", "Max", "Betfair Exchange",
}

WINDOW_DAYS = 30
OUTPUT_PATH = REPO_ROOT / "dev" / "active" / f"odds-fidelity-audit-{datetime.now(timezone.utc):%Y-%m-%d}.md"


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url)


def section_placement_freshness(cur) -> str:
    """How old was the latest odds row for the recommended bookmaker
    when a bot decided to bet?
    """
    cur.execute(
        """
        SELECT
            COALESCE(b.recommended_bookmaker, 'Unknown') AS book,
            EXTRACT(EPOCH FROM (b.pick_time - MAX(s.timestamp))) AS staleness_sec,
            b.market,
            b.selection,
            b.pick_time
        FROM simulated_bets b
        JOIN odds_snapshots s
          ON s.match_id = b.match_id
         AND s.market = b.market
         AND s.selection = b.selection
         AND s.bookmaker = b.recommended_bookmaker
         AND s.timestamp <= b.pick_time
        WHERE b.pick_time >= NOW() - INTERVAL %s
          AND b.recommended_bookmaker IS NOT NULL
        GROUP BY b.id, b.recommended_bookmaker, b.market, b.selection, b.pick_time
        """,
        (f"{WINDOW_DAYS} days",),
    )
    rows = cur.fetchall()

    by_book: dict[str, list[float]] = {}
    for r in rows:
        if r["staleness_sec"] is None:
            continue
        by_book.setdefault(r["book"], []).append(float(r["staleness_sec"]))

    lines = ["## 1. Placement freshness", ""]
    lines.append("How stale was the latest available odds row for the recommended")
    lines.append("bookmaker when the bot decided to bet? `pick_time` minus latest")
    lines.append("`odds_snapshots.timestamp` for the same match/market/selection/book.")
    lines.append("")
    lines.append(f"Window: last {WINDOW_DAYS} days. Sample: {len(rows)} bet decisions.")
    lines.append("")
    lines.append("| Book | N decisions | Median staleness | P95 staleness | Verdict |")
    lines.append("|---|---:|---:|---:|---|")
    for book in sorted(by_book.keys()):
        vals = sorted(by_book[book])
        n = len(vals)
        median = vals[n // 2]
        p95 = vals[min(int(n * 0.95), n - 1)]
        verdict = "✅ fresh" if median < 1800 else (
            "⚠️ ~stale" if median < 7200 else "🔴 very stale"
        )
        lines.append(
            f"| {book} | {n} | {_format_sec(median)} | {_format_sec(p95)} | {verdict} |"
        )
    lines.append("")
    lines.append("Thresholds — `✅ fresh` = median < 30min, `⚠️ ~stale` < 2h, `🔴 very stale` ≥ 2h.")
    lines.append("")
    return "\n".join(lines)


def section_clv_capture_staleness(cur) -> str:
    """When we captured `closing_odds` for CLV computation, how recent was
    the latest Pinnacle row in odds_snapshots for that match?

    The bet has a kickoff time. We measure: (kickoff - latest Pinnacle
    snapshot before kickoff). If that's large, our "close" capture is
    based on stale Pinnacle data, which makes CLV noisy.
    """
    cur.execute(
        """
        WITH settled AS (
          SELECT b.id, b.match_id, b.market, b.selection,
                 m.date AS kickoff,
                 b.clv_pinnacle
          FROM simulated_bets b
          JOIN matches m ON m.id = b.match_id
          WHERE b.pick_time >= NOW() - INTERVAL %s
            AND b.result IN ('won', 'lost')
            AND b.clv_pinnacle IS NOT NULL
        )
        SELECT
            s.match_id,
            s.kickoff,
            EXTRACT(EPOCH FROM (s.kickoff - MAX(os.timestamp))) AS gap_sec
        FROM settled s
        LEFT JOIN odds_snapshots os
          ON os.match_id = s.match_id
         AND os.bookmaker = 'Pinnacle'
         AND os.market = s.market
         AND os.selection = s.selection
         AND os.timestamp <= s.kickoff
        GROUP BY s.match_id, s.kickoff
        """,
        (f"{WINDOW_DAYS} days",),
    )
    rows = cur.fetchall()
    gaps = [float(r["gap_sec"]) for r in rows if r["gap_sec"] is not None]
    missing = sum(1 for r in rows if r["gap_sec"] is None)

    lines = ["## 2. Pinnacle close-capture staleness", ""]
    lines.append("For every settled bet with a clv_pinnacle value, how recent was the")
    lines.append("latest Pinnacle snapshot we had for the match-market-selection before")
    lines.append("kickoff? Smaller is better — the smaller this gap, the closer our")
    lines.append("captured `closing_odds` is to the actual market close.")
    lines.append("")
    lines.append(f"Window: last {WINDOW_DAYS} days. Settled-bet sample: {len(rows)}.")
    lines.append(f"Bets with NO Pinnacle row before kickoff: **{missing}**")
    lines.append("")
    if not gaps:
        lines.append("_No matching gaps to summarise._")
        lines.append("")
        return "\n".join(lines)
    gaps.sort()
    n = len(gaps)
    median = gaps[n // 2]
    p25 = gaps[int(n * 0.25)]
    p75 = gaps[int(n * 0.75)]
    p95 = gaps[min(int(n * 0.95), n - 1)]
    lines.append("| Percentile | Gap (kickoff − latest Pinnacle row) |")
    lines.append("|---|---:|")
    lines.append(f"| P25 | {_format_sec(p25)} |")
    lines.append(f"| Median | {_format_sec(median)} |")
    lines.append(f"| P75 | {_format_sec(p75)} |")
    lines.append(f"| P95 | {_format_sec(p95)} |")
    lines.append("")
    if median > 3600:
        lines.append(f"🔴 **Median gap is {_format_sec(median)}.** Our Pinnacle close")
        lines.append("capture is materially stale. CLV numbers are noisy because the")
        lines.append("\"close\" we benchmark against isn't actually the close. A")
        lines.append("complementary aggregator with sub-minute Pinnacle refresh would")
        lines.append("tighten this; expected CLV-noise reduction is significant.")
    elif median > 600:
        lines.append(f"⚠️ Median gap is {_format_sec(median)} — workable but loose.")
        lines.append("Lines move in the final hour before kickoff; our close capture")
        lines.append("is missing some of that motion. Marginal case for fresher source.")
    else:
        lines.append(f"✅ Median gap is {_format_sec(median)} — close enough to the")
        lines.append("actual close that CLV calculations are accurate. **No data-")
        lines.append("freshness justification for paying for a complementary aggregator.**")
    lines.append("")
    return "\n".join(lines)


def section_implied_sum_sanity(cur) -> str:
    """For each book, compute the implied-probability sum on 1X2 lines
    from the most recent snapshot per match. A real 1X2 sums to 1.02-1.15.
    """
    cur.execute(
        """
        WITH latest_per_match_book AS (
          SELECT DISTINCT ON (match_id, bookmaker, selection)
            match_id, bookmaker, selection, odds, timestamp
          FROM odds_snapshots
          WHERE market = '1x2'
            AND timestamp >= NOW() - INTERVAL %s
            AND odds > 1.0
          ORDER BY match_id, bookmaker, selection, timestamp DESC
        ),
        per_book AS (
          SELECT
            bookmaker,
            match_id,
            SUM(CASE WHEN selection='home' THEN 1.0/odds ELSE 0 END) AS p_home,
            SUM(CASE WHEN selection='draw' THEN 1.0/odds ELSE 0 END) AS p_draw,
            SUM(CASE WHEN selection='away' THEN 1.0/odds ELSE 0 END) AS p_away,
            COUNT(DISTINCT selection) AS n_sel
          FROM latest_per_match_book
          GROUP BY bookmaker, match_id
          HAVING COUNT(DISTINCT selection) = 3
        )
        SELECT
            bookmaker,
            COUNT(*) AS n_matches,
            AVG(p_home + p_draw + p_away)::numeric(6,4) AS avg_sum,
            MIN(p_home + p_draw + p_away)::numeric(6,4) AS min_sum,
            MAX(p_home + p_draw + p_away)::numeric(6,4) AS max_sum,
            SUM(CASE WHEN (p_home+p_draw+p_away) BETWEEN 1.02 AND 1.15 THEN 1 ELSE 0 END)::float / COUNT(*) AS pct_in_band
        FROM per_book
        GROUP BY bookmaker
        ORDER BY n_matches DESC
        """,
        (f"{WINDOW_DAYS} days",),
    )
    rows = cur.fetchall()

    lines = ["## 3. Implied-sum sanity (1X2 markets)", ""]
    lines.append("For each book's most-recent 1X2 quote per match (last 30 days), the")
    lines.append("implied-probability sum should land in the typical overround band")
    lines.append("of **1.02-1.15**. Sub-1.0 means a book is offering free money (data")
    lines.append("bug). Above 1.20 means the book is structurally non-competitive.")
    lines.append("")
    lines.append("| Book | Matches | Avg sum | Min | Max | % in 1.02-1.15 band | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for r in rows:
        book = r["bookmaker"]
        if book in SYNTHETIC_SOURCES:
            continue
        in_band = float(r["pct_in_band"])
        if in_band >= 0.95:
            verdict = "✅ healthy"
        elif in_band >= 0.80:
            verdict = "⚠️ minor drift"
        else:
            verdict = "🔴 broken / non-competitive"
        lines.append(
            f"| {book} | {r['n_matches']} | {r['avg_sum']} | {r['min_sum']} | "
            f"{r['max_sum']} | {in_band:.1%} | {verdict} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_cross_book_consistency(cur) -> str:
    """For each match where 2+ ACCESSIBLE_BOOKMAKERS quote 1X2 within a
    30-min window, measure variance in implied home-win probability.
    """
    books_csv = ",".join(f"'{b}'" for b in ACCESSIBLE_BOOKMAKERS)
    cur.execute(
        f"""
        WITH latest_30d AS (
          SELECT DISTINCT ON (match_id, bookmaker)
            match_id, bookmaker, odds, timestamp
          FROM odds_snapshots
          WHERE market='1x2' AND selection='home'
            AND bookmaker IN ({books_csv})
            AND timestamp >= NOW() - INTERVAL %s
            AND odds > 1.0
          ORDER BY match_id, bookmaker, timestamp DESC
        ),
        per_match AS (
          SELECT
            match_id,
            AVG(1.0/odds) AS avg_p,
            STDDEV(1.0/odds) AS std_p,
            MAX(1.0/odds) - MIN(1.0/odds) AS range_p,
            COUNT(*) AS n_books
          FROM latest_30d
          GROUP BY match_id
          HAVING COUNT(*) >= 2
        )
        SELECT
            n_books,
            COUNT(*) AS n_matches,
            AVG(range_p)::numeric(6,4) AS avg_range,
            AVG(std_p)::numeric(6,4) AS avg_std
        FROM per_match
        GROUP BY n_books
        ORDER BY n_books
        """,
        (f"{WINDOW_DAYS} days",),
    )
    rows = cur.fetchall()
    lines = ["## 4. Cross-book consistency on home-win probability", ""]
    lines.append("For each match, when 2+ accessible books quote 1X2, what's the")
    lines.append("spread on implied home-win probability? A real market has books")
    lines.append("disagree by 1-4 percentage points on home-win across them. >10pp")
    lines.append("spread = at least one book has stale or broken data.")
    lines.append("")
    lines.append("| # books quoting | N matches | Avg range (max-min p_home) | Avg stddev |")
    lines.append("|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r['n_books']} | {r['n_matches']} | "
            f"{float(r['avg_range']):.3f} | {float(r['avg_std'] or 0):.3f} |"
        )
    lines.append("")
    lines.append("Interpretation — avg range below 0.05 across many books = healthy")
    lines.append("market agreement. Above 0.10 = systematic divergence; investigate.")
    lines.append("")
    return "\n".join(lines)


def section_per_book_refresh_cadence(cur) -> str:
    """Median gap between consecutive snapshots per book per (match, market)
    — how often does each book's data actually update?
    """
    cur.execute(
        """
        WITH gaps AS (
          SELECT
            bookmaker,
            EXTRACT(EPOCH FROM (timestamp - LAG(timestamp) OVER (
              PARTITION BY match_id, market, selection, bookmaker
              ORDER BY timestamp
            ))) AS gap_sec
          FROM odds_snapshots
          WHERE timestamp >= NOW() - INTERVAL %s
            AND market = '1x2'
        )
        SELECT
            bookmaker,
            COUNT(*) AS n_gaps,
            PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY gap_sec) AS median_gap_sec,
            PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY gap_sec) AS p95_gap_sec
        FROM gaps
        WHERE gap_sec IS NOT NULL AND gap_sec > 0
        GROUP BY bookmaker
        ORDER BY n_gaps DESC
        """,
        (f"{WINDOW_DAYS} days",),
    )
    rows = cur.fetchall()
    lines = ["## 5. Per-book refresh cadence", ""]
    lines.append("How often does each book's data actually update in odds_snapshots?")
    lines.append("Measures the gap between consecutive snapshots per `(match, market,")
    lines.append("selection, bookmaker)`. Tighter median = more responsive feed.")
    lines.append("")
    lines.append("| Book | Sample gaps | Median refresh interval | P95 |")
    lines.append("|---|---:|---:|---:|")
    for r in rows:
        book = r["bookmaker"]
        if book in SYNTHETIC_SOURCES:
            continue
        lines.append(
            f"| {book} | {r['n_gaps']} | "
            f"{_format_sec(float(r['median_gap_sec']))} | "
            f"{_format_sec(float(r['p95_gap_sec']))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _format_sec(s: float) -> str:
    if s is None:
        return "—"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    if s < 86_400:
        return f"{s/3600:.1f}h"
    return f"{s/86_400:.1f}d"


def write_report() -> None:
    started = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            placement = section_placement_freshness(cur)
            clv_close = section_clv_capture_staleness(cur)
            implied = section_implied_sum_sanity(cur)
            cross = section_cross_book_consistency(cur)
            cadence = section_per_book_refresh_cadence(cur)

    header = [
        "# Odds Fidelity Audit",
        "",
        f"_Generated {started:%Y-%m-%d %H:%M} UTC. Window: last {WINDOW_DAYS} days._",
        "",
        "The strategic question: are our existing odds feeds (API-Football + ",
        "Coolbet scraper) reliable enough, or do we need a complementary",
        "aggregator (e.g. The Odds API at +$59/mo) to tighten our CLV story",
        "and broaden detected value bets?",
        "",
        "This audit measures five things across our ingested books, and the",
        "final section translates the numbers into a Phase 1 recommendation.",
        "",
        "**Books in scope:**",
        "- *Accessible* (used for value-bet edge math): "
        + ", ".join(sorted(ACCESSIBLE_BOOKMAKERS)),
        "- *Inspect-only* (ingested for comparison, currently excluded): "
        + ", ".join(sorted(INSPECT_ONLY_BOOKMAKERS)),
        "- *Synthetic / excluded from scoring*: "
        + ", ".join(sorted(SYNTHETIC_SOURCES)),
        "",
        "---",
        "",
    ]

    footer = [
        "---",
        "",
        "## Recommendation framework",
        "",
        "Read the verdicts above against these thresholds:",
        "",
        "**If Section 2 (Pinnacle close-capture staleness) median > 1h**: the",
        "case for a complementary aggregator is strong. Our CLV benchmark is",
        "materially stale. Action: subscribe to The Odds API 100K plan ($59/mo)",
        "for fresher Pinnacle scraping; route only the Pinnacle stream of TOA",
        "into `odds_snapshots` (don't double-ingest other books we already get",
        "from AF). Re-run this audit after 2 weeks of TOA Pinnacle to confirm",
        "median gap dropped < 10min.",
        "",
        "**If Section 2 median 5-60min**: marginal case. Continue with AF for",
        "now; pursue **official Pinnacle API access** via `api@pinnacle.com`",
        "as zero-cost optionality.",
        "",
        "**If Section 2 median < 5min**: AF is fine. **Do not pay for a",
        "complementary aggregator** based on freshness alone. Look elsewhere",
        "for bookmaker-expansion ROI (e.g. un-excluding Bwin in Section 3).",
        "",
        "**If any book in Section 3 shows < 80% in 1.02-1.15 band**: that book",
        "is broken or non-competitive on 1X2. Recommend dropping from edge",
        "math (similar to William Hill OU blacklist) until fixed upstream.",
        "",
        "**If Section 4 avg range > 0.10 with 3+ books**: book divergence is",
        "wide enough to suggest one or more sources is glitchy. Cross-check the",
        "worst outliers per match (`scripts/diag_book_outlier.py` would be a",
        "natural follow-up if this audit shows the problem).",
        "",
        "**Re-run cadence:** monthly until N (settled bets in window) ≥ 500,",
        "then quarterly. Save outputs to `dev/active/odds-fidelity-audit-",
        "YYYY-MM-DD.md` so we can track per-book fidelity over time.",
        "",
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "\n".join(header + [placement, clv_close, implied, cross, cadence] + footer)
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"Wrote {OUTPUT_PATH}  ({elapsed:.1f}s)")


if __name__ == "__main__":
    try:
        write_report()
    except Exception as e:
        print(f"Audit failed: {e}", file=sys.stderr)
        raise
