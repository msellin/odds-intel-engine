"""
ODDS-FRESHNESS-FREE-WINS Move 2 — alt-CLV backtest.

The question: does our settle logic capture the freshest pre-kickoff
Pinnacle row, or do we leave a fresher one on the table?

How current clv_pinnacle is computed (`workers/jobs/settlement.py:_get_pinnacle_close`):
  1. Primary path — latest Pinnacle row with `is_closing = TRUE`
  2. Fallback — latest Pinnacle row by timestamp **with NO `timestamp <= kickoff`
     filter**. This is the potential bug: if AF leaked a post-kickoff snapshot
     for any reason, the fallback could pick it up and tag it as "close."

The alt definition for this backtest:
  alt_close = latest Pinnacle row with `timestamp <= match.date` (strict pre-kickoff)
  alt_clv  = (odds_at_pick / alt_close) - 1

Three possible outcomes:
  (a) alt_clv ≈ current clv_pinnacle on every bet → settle logic is correct;
      the 60min audit gap is purely AF's 3h refresh cycle. **No internal fix.
      The bookmaker question is settled — pay $300+/mo enterprise feed or live
      with current data.**
  (b) alt_clv materially > current clv_pinnacle on a subset → settle pulled
      an older snapshot than the freshest pre-kickoff. **Fixable internally
      for free** by tightening the SQL in `_get_pinnacle_close`.
  (c) alt_clv materially < current clv_pinnacle on a subset → settle pulled a
      post-kickoff snapshot (from a leak in api-football-live or similar).
      **Bug — settle logic is silently inflating CLV.**

Output: dev/active/fresh-pinnacle-clv-backtest-YYYY-MM-DD.md with summary
table + decision recommendation.

Run: python3 scripts/backtest_fresh_pinnacle_clv.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, mean, stdev

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

WINDOW_DAYS = 30
# Materiality threshold for outcome (b) / (c) — if avg |alt - current| crosses
# this, treat as a real issue worth acting on. 1pp on CLV is the natural unit
# (CLV is reported in percent on /accuracy etc.)
MATERIAL_PP = 0.01
OUTPUT_PATH = REPO_ROOT / "dev" / "active" / f"fresh-pinnacle-clv-backtest-{datetime.now(timezone.utc):%Y-%m-%d}.md"


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url)


def fetch_bets_and_alt_close(cur) -> list[dict]:
    """For every settled bet in window with clv_pinnacle set, compute the
    alt close = latest Pinnacle row strictly before kickoff.

    Joins through matches to get the kickoff timestamp. Uses LATERAL so the
    per-bet alt-close lookup runs as one query rather than 1000s of round
    trips.
    """
    cur.execute(
        """
        SELECT
          b.id                    AS bet_id,
          b.match_id              AS match_id,
          b.market                AS market,
          b.selection             AS selection,
          b.odds_at_pick::float   AS odds_at_pick,
          b.clv_pinnacle::float   AS current_clv,
          b.closing_odds::float   AS current_close,
          b.pick_time             AS pick_time,
          b.result                AS result,
          m.date                  AS kickoff,
          alt.odds::float         AS alt_close,
          alt.timestamp           AS alt_ts,
          alt.is_closing          AS alt_is_closing
        FROM simulated_bets b
        JOIN matches m ON m.id = b.match_id
        LEFT JOIN LATERAL (
          SELECT odds, timestamp, is_closing
          FROM odds_snapshots
          WHERE match_id = b.match_id
            AND market = b.market
            AND selection = b.selection
            AND bookmaker = 'Pinnacle'
            AND timestamp <= m.date
            AND odds > 1.0
          ORDER BY timestamp DESC
          LIMIT 1
        ) alt ON TRUE
        WHERE b.pick_time >= NOW() - INTERVAL %s
          AND b.result IN ('won', 'lost')
          AND b.clv_pinnacle IS NOT NULL
        """,
        (f"{WINDOW_DAYS} days",),
    )
    return cur.fetchall()


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "stdev": None}
    n = len(values)
    return {
        "n": n,
        "mean": mean(values),
        "median": median(values),
        "stdev": stdev(values) if n > 1 else 0.0,
    }


def _pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%"


def _abs_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{abs(x)*100:.2f}pp"


def build_report() -> None:
    started = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            bets = fetch_bets_and_alt_close(cur)

    # Partition: matched (alt found) vs unmatched (no pre-kickoff Pinnacle row)
    matched = [b for b in bets if b["alt_close"] and b["alt_close"] > 1.0]
    unmatched = [b for b in bets if not b["alt_close"] or b["alt_close"] <= 1.0]

    # alt_clv on matched rows
    for b in matched:
        b["alt_clv"] = (b["odds_at_pick"] / b["alt_close"]) - 1.0
        # delta = alt - current; positive = alt is higher (we under-counted CLV)
        b["delta"] = b["alt_clv"] - b["current_clv"]
        # Whether settle picked a different row than the freshest pre-kickoff
        b["close_diff"] = (
            None if b["current_close"] is None
            else abs(b["odds_at_pick"] / (b["current_clv"] + 1.0) - b["alt_close"]) > 0.01
        )

    deltas = [b["delta"] for b in matched]
    alt_clvs = [b["alt_clv"] for b in matched]
    cur_clvs = [b["current_clv"] for b in matched]
    close_diffs = sum(1 for b in matched if b.get("close_diff"))

    d_stats = stats(deltas)
    alt_stats = stats(alt_clvs)
    cur_stats = stats(cur_clvs)

    # Time gap stats: how stale is the alt_close vs kickoff?
    gap_secs = []
    for b in matched:
        if b["alt_ts"] and b["kickoff"]:
            gap_secs.append(
                (b["kickoff"] - b["alt_ts"]).total_seconds()
            )
    gap_stats = stats(gap_secs)

    # Outcome verdict
    if d_stats["mean"] is None:
        verdict = "❓ insufficient data"
        outcome = "?"
        body = "_No matched rows._"
    else:
        mean_delta = d_stats["mean"]
        if abs(mean_delta) < MATERIAL_PP:
            outcome = "a"
            verdict = "✅ Outcome (a) — settle logic is correct"
            body = (
                f"Mean delta is **{mean_delta*100:+.3f}pp**, well within the "
                f"{MATERIAL_PP*100:.0f}pp materiality threshold. The settle "
                f"logic is grabbing essentially the same row the strict "
                f"`timestamp <= kickoff` query returns. The 60min audit gap is "
                f"a property of AF's 3h refresh cycle, not our pipeline."
                f"\n\n**Action:** no internal fix possible. The bookmaker "
                f"freshness question is settled — either live with current "
                f"AF cadence or pay enterprise prices ($300+/mo) for a "
                f"sub-minute feed."
            )
        elif mean_delta > MATERIAL_PP:
            outcome = "b"
            verdict = "🟡 Outcome (b) — settle picks older snapshot than available"
            body = (
                f"Mean delta is **{mean_delta*100:+.3f}pp** — alt-CLV is "
                f"systematically higher. Our settle logic is grabbing a "
                f"Pinnacle row OLDER than the freshest pre-kickoff row we "
                f"already had in the DB."
                f"\n\n**Action:** tighten the SQL in "
                f"`workers/jobs/settlement.py:_get_pinnacle_close` — drop the "
                f"`is_closing = TRUE` primary filter or add an explicit "
                f"`timestamp <= matches.date` constraint so the fallback "
                f"path always picks the freshest pre-kickoff row. Free fix; "
                f"~30min of work. Re-run this backtest to confirm convergence."
            )
        else:  # mean_delta < -MATERIAL_PP
            outcome = "c"
            verdict = "🔴 Outcome (c) — settle picks POST-kickoff snapshot (bug)"
            body = (
                f"Mean delta is **{mean_delta*100:+.3f}pp** — alt-CLV is "
                f"systematically lower. The current settle is grabbing a "
                f"Pinnacle row that is NEWER than kickoff, meaning we're "
                f"capturing live or post-match odds and labelling them as "
                f"the close. This silently inflates CLV — our published "
                f"CLV numbers are wrong."
                f"\n\n**Action:** P0 fix — add explicit `timestamp <= "
                f"matches.date` constraint to `_get_pinnacle_close`. Audit "
                f"any downstream computations that rely on `clv_pinnacle` "
                f"(performance metrics, /accuracy, meta_b_ml3 features). "
                f"Consider a one-off backfill to recompute clv_pinnacle on "
                f"affected rows."
            )

    # Build per-market breakdown for visibility
    market_breakdown = {}
    for b in matched:
        mkt = b["market"]
        market_breakdown.setdefault(mkt, []).append(b["delta"])
    market_lines = []
    for mkt in sorted(market_breakdown.keys()):
        vals = market_breakdown[mkt]
        n = len(vals)
        if n < 5:
            continue  # too few to summarise
        s = stats(vals)
        market_lines.append(
            f"| {mkt} | {n} | {_pct(s['mean'])} | {_pct(s['median'])} | "
            f"{_abs_pct(s['stdev'])} |"
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    report = [
        "# Fresh-Pinnacle CLV Backtest",
        "",
        f"_Generated {started:%Y-%m-%d %H:%M} UTC. Window: last {WINDOW_DAYS} days._",
        "",
        "## Question",
        "",
        "Does our settle logic capture the freshest pre-kickoff Pinnacle row",
        "available, or do we leave fresher rows on the table?",
        "",
        "Tested by recomputing `clv_pinnacle` for every settled bet using a",
        "strict `WHERE timestamp <= match.date AND bookmaker = 'Pinnacle'`",
        "filter (the freshest pre-kickoff Pinnacle row in `odds_snapshots`),",
        "then comparing the resulting alt-CLV against the current",
        "`simulated_bets.clv_pinnacle`.",
        "",
        "## Sample",
        "",
        f"| Slice | Count |",
        f"|---|---:|",
        f"| Settled bets in window with `clv_pinnacle` set | {len(bets)} |",
        f"| Matched (alt Pinnacle row found pre-kickoff) | {len(matched)} |",
        f"| Unmatched (no Pinnacle row in DB before kickoff) | {len(unmatched)} |",
        "",
        f"_Unmatched bets are the ones where AF never delivered Pinnacle data_",
        f"_for the match before kickoff. This is AF's 3h refresh cycle leaving_",
        f"_gaps on some leagues — already documented in the fidelity audit._",
        "",
        "## Headline CLV comparison (matched rows only)",
        "",
        "| Metric | Current `clv_pinnacle` | Alt CLV (strict pre-kickoff) | Delta (alt − current) |",
        "|---|---:|---:|---:|",
        f"| Mean | {_pct(cur_stats['mean'])} | {_pct(alt_stats['mean'])} | {_pct(d_stats['mean'])} |",
        f"| Median | {_pct(cur_stats['median'])} | {_pct(alt_stats['median'])} | {_pct(d_stats['median'])} |",
        f"| Stdev | {_abs_pct(cur_stats['stdev'])} | {_abs_pct(alt_stats['stdev'])} | {_abs_pct(d_stats['stdev'])} |",
        "",
        f"**Bets where settle picked a different row than alt:** {close_diffs} of {len(matched)} "
        f"({(close_diffs/len(matched)*100 if matched else 0):.1f}%)",
        "",
        f"**Alt Pinnacle row age at kickoff:**",
        f"- Median: {(gap_stats['median']/60 if gap_stats['median'] else 0):.0f} min before kickoff",
        f"- Mean:   {(gap_stats['mean']/60 if gap_stats['mean'] else 0):.0f} min before kickoff",
        "",
        "## Verdict",
        "",
        verdict,
        "",
        body,
        "",
        "## Per-market breakdown",
        "",
        "| Market | N | Mean delta | Median delta | Stdev |",
        "|---|---:|---:|---:|---:|",
        *market_lines,
        "",
        "## Method",
        "",
        "Single SQL query joining `simulated_bets` → `matches` → LATERAL",
        "`odds_snapshots` (strict pre-kickoff Pinnacle). Per-bet alt-CLV is",
        "computed in Python; distributions are reported above.",
        "",
        "Materiality threshold: **±1pp on mean delta**. Below that, settle is",
        "considered correct (outcome a). Above, settle has a bug (b or c).",
        "",
        "## Re-run cadence",
        "",
        "Re-run after any change to settle logic (`_get_pinnacle_close`) or",
        "to the `is_closing` flag wiring. Otherwise, monthly as part of the",
        "fidelity-audit cycle.",
        "",
    ]
    OUTPUT_PATH.write_text("\n".join(report))
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"Wrote {OUTPUT_PATH}  ({elapsed:.1f}s)  outcome={outcome}")


if __name__ == "__main__":
    build_report()
