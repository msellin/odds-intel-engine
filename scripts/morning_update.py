"""
OddsIntel — Morning Update Dashboard

Prints a concise daily snapshot of all metrics that matter:
model progress, bot performance, data accumulation thresholds,
pipeline health, and API usage.

Usage:
    python3 scripts/morning_update.py
    python3 scripts/morning_update.py --verbose   # include per-bot breakdown + calibration detail
"""

import sys
import argparse
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.db import execute_query

TODAY = date.today().isoformat()

parser = argparse.ArgumentParser()
parser.add_argument("--verbose", "-v", action="store_true")
args = parser.parse_args()


def q(sql, params=None):
    return execute_query(sql, params or [])

def val(sql, params=None):
    rows = q(sql, params)
    if rows:
        v = list(rows[0].values())[0]
        return int(v) if v is not None else 0
    return 0

def fval(sql, params=None):
    rows = q(sql, params)
    if rows:
        v = list(rows[0].values())[0]
        return float(v) if v is not None else None
    return None

def bar(current, target, width=20):
    pct = min(current / target, 1.0) if target else 0
    filled = int(pct * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {current}/{target} ({pct*100:.0f}%)"

def section(title):
    print(f"\n{'─' * 58}")
    print(f"  {title}")
    print(f"{'─' * 58}")

def row(label, value, note=""):
    note_str = f"  ← {note}" if note else ""
    print(f"  {label:<40} {value}{note_str}")


print(f"\n{'═' * 58}")
print(f"  OddsIntel Morning Update — {TODAY}")
print(f"{'═' * 58}")


# ── 1. DATA ACCUMULATION THRESHOLDS ──────────────────────────
section("1. DATA ACCUMULATION THRESHOLDS")

platt_ready = val("""
    SELECT COUNT(*) FROM predictions p
    INNER JOIN matches m ON m.id = p.match_id
    WHERE p.source = 'ensemble' AND m.status = 'finished' AND m.result IS NOT NULL
""")
row("Predictions with outcomes (Platt)", bar(platt_ready, 500), "✅ DONE" if platt_ready >= 500 else "needs 500+")

pseudo_clv = val("""
    SELECT COUNT(*) FROM matches
    WHERE status = 'finished' AND pseudo_clv_home IS NOT NULL
""")
row("Pseudo-CLV rows (meta-model Ph1)", bar(pseudo_clv, 3000), "✅ READY" if pseudo_clv >= 3000 else "needs 3,000")

feature_vectors = val("SELECT COUNT(*) FROM match_feature_vectors")
row("Match feature vectors (training)", bar(feature_vectors, 3000), "✅ READY" if feature_vectors >= 3000 else "for XGBoost retrain")

all_settled = val("SELECT COUNT(*) FROM simulated_bets WHERE result != 'pending'")
row("Settled bot bets — all", bar(all_settled, 500), "statistical significance")

alignment_settled = val("""
    SELECT COUNT(*) FROM simulated_bets
    WHERE result != 'pending' AND alignment_class IS NOT NULL
""")
row("Settled bot bets — with alignment", bar(alignment_settled, 300), "validates HIGH/MED/LOW signal")

meta_ready = val("""
    SELECT COUNT(*) FROM simulated_bets
    WHERE result != 'pending'
      AND alignment_class IS NOT NULL
      AND clv IS NOT NULL
""")
row("Settled bets w/ alignment + CLV", bar(meta_ready, 1000), "meta-model Phase 2")

live_matches = val("SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots")
row("Matches with live snapshots", bar(live_matches, 500), "in-play model (~July)")

post_mortems = val("SELECT COUNT(*) FROM model_evaluations WHERE market = 'post_mortem'")
row("Post-mortem rows", bar(post_mortems, 50), "loss pattern insight")


# ── 2. TODAY'S PIPELINE ACTIVITY ─────────────────────────────
section("2. TODAY'S PIPELINE ACTIVITY")

today_matches = val("SELECT COUNT(*) FROM matches WHERE date = %s", [TODAY])
row("Matches scheduled today", today_matches)

today_bets = val("SELECT COUNT(*) FROM simulated_bets WHERE created_at::date = %s", [TODAY])
today_pending = val("SELECT COUNT(*) FROM simulated_bets WHERE created_at::date = %s AND result = 'pending'", [TODAY])
today_settled = today_bets - today_pending
row("Bets placed today", f"{today_bets}  (pending: {today_pending}, settled: {today_settled})", "target ~30-40")

today_predictions = val("""
    SELECT COUNT(*) FROM predictions
    WHERE created_at::date = %s AND source = 'ensemble'
""", [TODAY])
row("Ensemble predictions today", today_predictions)

today_snapshots = val("""
    SELECT COUNT(*) FROM live_match_snapshots
    WHERE captured_at::date = %s
""", [TODAY])
row("Live snapshots today", today_snapshots)

today_odds = val("""
    SELECT COUNT(DISTINCT match_id) FROM odds_snapshots
    WHERE timestamp::date = %s
""", [TODAY])
row("Matches with odds refreshed today", today_odds)

# Recent pipeline runs
last_runs = q("""
    SELECT job_name, status, completed_at
    FROM pipeline_runs
    WHERE run_date >= CURRENT_DATE - INTERVAL '2 days'
    ORDER BY completed_at DESC NULLS LAST
    LIMIT 8
""")
if last_runs:
    print("\n  Recent pipeline runs:")
    for r in last_runs:
        ts = str(r.get('completed_at', ''))[:16]
        status_icon = "✅" if r.get('status') == 'success' else "❌" if r.get('status') == 'error' else "🔄"
        print(f"    {status_icon} {r.get('job_name','?'):<32} {ts}")
else:
    print("\n  No recent pipeline runs found")


# ── 3. BOT PERFORMANCE ────────────────────────────────────────
section("3. BOT PERFORMANCE")

# All-time summary
bot_summary = q("""
    SELECT
        COUNT(*) FILTER (WHERE result != 'pending') as settled,
        COUNT(*) FILTER (WHERE result = 'won') as won,
        COUNT(*) FILTER (WHERE result = 'lost') as lost,
        ROUND(AVG(clv) FILTER (WHERE result != 'pending' AND clv IS NOT NULL)::numeric, 4) as avg_clv,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE result = 'won') /
            NULLIF(COUNT(*) FILTER (WHERE result != 'pending'), 0)
        , 1) as hit_rate_pct,
        ROUND(SUM(pnl) FILTER (WHERE result != 'pending')::numeric, 2) as total_pnl
    FROM simulated_bets
""")
if bot_summary:
    s = bot_summary[0]
    settled = s.get('settled', 0)
    won = s.get('won', 0)
    lost = s.get('lost', 0)
    hit = s.get('hit_rate_pct')
    avg_clv = s.get('avg_clv')
    total_pnl = s.get('total_pnl')

    row("Settled / Won / Lost", f"{settled} / {won} / {lost}")
    row("Hit rate", f"{hit}%" if hit else "—")
    clv_note = "✅ positive edge" if avg_clv and float(avg_clv) > 0 else ("⚠️  negative — watch" if avg_clv else "—")
    row("Avg CLV (closing line value)", str(avg_clv) if avg_clv else "—", clv_note)
    row("Total P&L (units)", str(total_pnl) if total_pnl else "—")

# CLV by alignment class — validates the core hypothesis
alignment_perf = q("""
    SELECT
        alignment_class,
        COUNT(*) as bets,
        ROUND(AVG(clv)::numeric, 4) as avg_clv,
        ROUND(100.0 * SUM(CASE WHEN result='won' THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1) as hit_rate
    FROM simulated_bets
    WHERE result != 'pending' AND alignment_class IS NOT NULL
    GROUP BY alignment_class
    ORDER BY alignment_class DESC
""")
if alignment_perf and any(r.get('bets', 0) > 0 for r in alignment_perf):
    print("\n  CLV by alignment class (hypothesis: HIGH > MED > LOW):")
    for r in alignment_perf:
        clv = r.get('avg_clv', '—')
        flag = "✅" if clv and float(clv) > 0 else "⚠️ "
        print(f"    {flag} {r.get('alignment_class','?'):<8}  bets={r.get('bets',0):<5}  hit={r.get('hit_rate','—')}%  avg_clv={clv}")
    if alignment_settled < 30:
        print(f"    (only {alignment_settled} bets — too early for signal)")
else:
    print("\n  Alignment class breakdown: no settled alignment bets yet")

# Market breakdown
market_perf = q("""
    SELECT
        market,
        COUNT(*) as bets,
        COUNT(*) FILTER (WHERE result = 'won') as won,
        ROUND(AVG(clv) FILTER (WHERE clv IS NOT NULL)::numeric, 4) as avg_clv,
        ROUND(100.0 * COUNT(*) FILTER (WHERE result='won') / NULLIF(COUNT(*),0), 1) as hit_rate
    FROM simulated_bets
    WHERE result != 'pending'
    GROUP BY market
    ORDER BY bets DESC
""")
if market_perf:
    print("\n  Performance by market:")
    for r in market_perf:
        clv = r.get('avg_clv')
        flag = "✅" if clv and float(clv) > 0 else "  "
        print(f"    {flag} {r.get('market','?'):<22}  bets={r.get('bets',0):<4}  hit={r.get('hit_rate','—')}%  avg_clv={clv}")

# Per-bot breakdown (always shown for active bots)
per_bot = q("""
    SELECT
        b.name as bot_name,
        b.starting_bankroll,
        b.current_bankroll,
        COUNT(sb.id) FILTER (WHERE sb.result != 'pending') as settled,
        COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
        ROUND(SUM(sb.pnl) FILTER (WHERE sb.result != 'pending')::numeric, 2) as total_pnl,
        ROUND(AVG(sb.clv) FILTER (WHERE sb.result != 'pending' AND sb.clv IS NOT NULL)::numeric, 4) as avg_clv,
        sb.timing_cohort
    FROM bots b
    LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
    WHERE b.is_active = true AND b.retired_at IS NULL
    GROUP BY b.id, b.name, b.starting_bankroll, b.current_bankroll, sb.timing_cohort
    ORDER BY total_pnl DESC NULLS LAST
""")
active_with_bets = [r for r in per_bot if (r.get('settled') or 0) > 0]
if active_with_bets:
    print("\n  Active bots with settled bets:")
    for r in active_with_bets:
        pnl = float(r.get('total_pnl') or 0)
        start = float(r.get('starting_bankroll') or 1000)
        roi = pnl / start * 100
        settled = r.get('settled', 0)
        clv = r.get('avg_clv')
        flag = "✅" if pnl > 0 else "🔴"
        cohort = r.get('timing_cohort') or '?'
        print(f"    {flag} {r.get('bot_name','?'):<28} [{cohort:<8}]  {settled} bets  P&L=€{pnl:+.2f} ({roi:+.1f}%)  avg_clv={clv}")


# ── 4. MODEL CALIBRATION ──────────────────────────────────────
section("4. MODEL CALIBRATION")

platt_params = q("""
    SELECT market, platt_a, platt_b, ece_before, ece_after, sample_count, fitted_at
    FROM model_calibration
    ORDER BY market
""")
if platt_params:
    print("  Platt scaling (ECE = Expected Calibration Error, lower is better):")
    for r in platt_params:
        ts = str(r.get('fitted_at', '?'))[:10]
        ece_b = r.get('ece_before', 0) or 0
        ece_a = r.get('ece_after', 0) or 0
        improvement = f"{(1 - ece_a/ece_b)*100:.0f}% better" if ece_b > 0 else "—"
        print(f"    {r.get('market','?'):<22}  ECE: {float(ece_b):.3f} → {float(ece_a):.3f} ({improvement})  n={r.get('sample_count')}  fitted={ts}")
else:
    print("  No Platt params in model_calibration table")

# Rough calibration check: predicted vs actual in 10% buckets
if args.verbose:
    calib_check = q("""
        SELECT
            (FLOOR(p.model_probability * 10) / 10)::numeric as prob_bucket,
            COUNT(*) as n,
            ROUND(AVG(CASE
                WHEN p.market = '1x2_home' AND m.result = 'home' THEN 1.0
                WHEN p.market = '1x2_draw' AND m.result = 'draw' THEN 1.0
                WHEN p.market = '1x2_away' AND m.result = 'away' THEN 1.0
                ELSE 0.0
            END)::numeric, 3) as actual_rate
        FROM predictions p
        INNER JOIN matches m ON m.id = p.match_id
        WHERE p.source = 'ensemble'
          AND m.status = 'finished'
          AND m.result IS NOT NULL
          AND p.model_probability IS NOT NULL
          AND p.market IN ('1x2_home', '1x2_draw', '1x2_away')
        GROUP BY FLOOR(p.model_probability * 10) / 10
        HAVING COUNT(*) >= 15
        ORDER BY prob_bucket
    """)
    if calib_check:
        print("\n  Calibration (predicted vs actual, buckets ≥15 samples):")
        for r in calib_check:
            pred = float(r.get('prob_bucket', 0))
            actual = float(r.get('actual_rate', 0))
            diff = actual - pred
            flag = "⚠️ " if abs(diff) > 0.05 else "   "
            print(f"  {flag}  pred={pred:.1f}  actual={actual:.3f}  n={r.get('n',0):<5}  diff={diff:+.3f}")


# ── 5. DATA COLLECTION HEALTH ─────────────────────────────────
section("5. DATA COLLECTION HEALTH")

total_matches = val("SELECT COUNT(*) FROM matches")
finished_matches = val("SELECT COUNT(*) FROM matches WHERE status = 'finished'")
row("Total matches in DB", total_matches)
row("Finished matches", finished_matches)

# Signals coverage
signals_today = val("""
    SELECT COUNT(DISTINCT match_id) FROM match_signals
    WHERE captured_at::date = %s
""", [TODAY])
row("Matches with signals today", signals_today)

# ELO coverage
elo_teams = val("SELECT COUNT(DISTINCT team_id) FROM team_elo_daily")
row("Teams with ELO ratings", elo_teams)

# Injuries
injuries_today = val("""
    SELECT COUNT(*) FROM match_injuries WHERE created_at::date = %s
""", [TODAY])
row("Injury records created today", injuries_today)

# News
news_today = val("""
    SELECT COUNT(*) FROM news_events WHERE detected_at::date = %s
""", [TODAY])
row("News events detected today", news_today)

# Backfill queue status
backfill = q("""
    SELECT
        COUNT(*) FILTER (WHERE status = 'complete') as complete,
        COUNT(*) FILTER (WHERE status = 'in_progress' AND fixtures_total > 0) as started,
        COUNT(*) FILTER (WHERE status = 'in_progress' AND fixtures_total = 0) as not_started,
        MAX(last_run_at) as last_ran
    FROM backfill_progress WHERE phase = 1
""")
if backfill:
    b = backfill[0]
    total_leagues = (b.get('complete') or 0) + (b.get('started') or 0) + (b.get('not_started') or 0)
    last = str(b.get('last_ran', ''))[:16]
    print(f"\n  Backfill queue (phase 1):  {b.get('complete')}/{total_leagues} leagues complete  "
          f"|  {b.get('started')} started  |  {b.get('not_started')} not yet started  |  last ran {last}")

# Actual DB coverage — what % of finished matches have stats/events (source of truth)
coverage = q("""
    SELECT
        l.api_football_id as league_api_id,
        l.name as league_name,
        m.season,
        COUNT(*) as finished,
        COUNT(DISTINCT ms.match_id) as has_stats,
        COUNT(DISTINCT me.match_id) as has_events
    FROM matches m
    JOIN leagues l ON l.id = m.league_id
    LEFT JOIN match_stats ms ON ms.match_id = m.id
    LEFT JOIN (SELECT DISTINCT match_id FROM match_events) me ON me.match_id = m.id
    WHERE m.status = 'finished'
      AND m.season IN (2023, 2024, 2025)
      AND l.api_football_id IN (
          SELECT DISTINCT league_api_id FROM backfill_progress WHERE status = 'complete'
      )
    GROUP BY l.api_football_id, l.name, m.season
    ORDER BY l.name, m.season
""")

if coverage:
    print("\n  Actual DB coverage (completed leagues — stats/events %):")
    print(f"  {'League':<20} {'Season':<7} {'Matches':<9} {'Stats':<8} {'Events'}")
    for r in coverage:
        fin = r.get('finished', 0)
        stats_pct = f"{100*r.get('has_stats',0)//fin}%" if fin else '—'
        events_pct = f"{100*r.get('has_events',0)//fin}%" if fin else '—'
        print(f"  {(r.get('league_name') or '')[:20]:<20} {r.get('season'):<7} {fin:<9} {stats_pct:<8} {events_pct}")
    print("  (Odds not backfilled — AF only serves odds for upcoming/recent matches)")


# ── 6. UPCOMING THRESHOLDS — WHAT UNLOCKS NEXT ───────────────
section("6. UPCOMING THRESHOLDS — WHAT UNLOCKS NEXT")

thresholds = [
    ("Alignment filter validated", alignment_settled, 300,
     "Dynamic HIGH/MED/LOW thresholds → smarter bet filtering"),
    ("Statistical significance", all_settled, 500,
     "Track record stops being noise — results become signal"),
    ("In-play model ready", live_matches, 500,
     "LightGBM in-play model (Phase 2, ~July)"),
    ("Meta-model Phase 1", pseudo_clv, 3000,
     "Logistic regression EV score replaces hardcoded thresholds"),
    ("Meta-model Phase 2 (XGBoost)", meta_ready, 1000,
     "Full signal set meta-model — biggest accuracy jump"),
]

for label, current, target, description in thresholds:
    if current >= target:
        status = "✅ DONE"
    else:
        remaining = target - current
        status = f"{remaining:,} to go"
    print(f"\n  {label}  ({status})")
    print(f"  {bar(current, target)}")
    print(f"  → {description}")

print(f"\n{'═' * 58}\n")
