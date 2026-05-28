"""
OddsIntel — Daily Pipeline v2 (Supabase)
Stores everything in Supabase instead of JSON files.
Frontend can read data directly from the same database.

Usage:
  python daily_pipeline_v2.py            # Morning: fetch + predict + bet
  python daily_pipeline_v2.py settle     # Evening: settle bets with results
  python daily_pipeline_v2.py report     # Anytime: show bot performance
"""

import math
import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, date, timezone
from scipy.stats import poisson
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import (
    get_fixtures_by_date, fixture_to_match_dict,
    parse_fixture_odds, get_odds_by_date,
    get_prediction, parse_prediction,
    get_team_statistics, parse_team_statistics,
    get_injuries_by_date, parse_injuries,
    get_standings, parse_standings,
    get_h2h, parse_h2h,
)
from workers.api_clients.supabase_client import (
    ensure_bots, bulk_store_matches, store_odds,
    store_prediction, store_bet, store_prediction_snapshot, store_team_season_stats, store_match_injuries,
    store_league_standings, store_match_h2h,
    batch_write_morning_signals,
    build_match_feature_vectors_live,
)
from workers.notify.telegram import send_telegram, send_telegram_to_users
from workers.model.improvements import (
    calibrate_prob, compute_odds_movement, compute_alignment,
    compute_kelly, compute_stake,
)
from workers.model.xgboost_ensemble import (
    get_xgboost_prediction, ensemble_prediction, _resolve_version,
)

console = Console()

ENGINE_DIR = Path(__file__).parent.parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"

STAKE = 10.0

# Bot configurations
BOTS_CONFIG = {
    "bot_v10_all": {
        "description": "v10 model, all target leagues, tier-adjusted thresholds",
        "tier_label": "elite",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.08, "1x2_long": 0.12, "ou": 0.08},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08, "ou": 0.06},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06, "ou": 0.05},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05, "ou": 0.04},
        },
        "odds_range": (1.30, 4.50),
        "min_prob": 0.30,
    },
    # BOTS-RETIRE-1X2 (2026-05-17): retired via migration 103. Kept in BOTS_CONFIG
    # so historical bot_id linkages keep working — pipeline skips via is_active=false.
    "bot_lower_1x2": {
        # Currently active. Originally retired 2026-05-17 (alpha_t2_1x2=0.00 after
        # May 17 retrain starved it; +83% on 11 bets was variance). Re-activated
        # 2026-05-22 via migration 122 for weekend signal gathering.
        "description": "Tier 2-4 only, 1X2 only — odds 1.35-3.50, edge 3-7%. Re-enabled 2026-05-22 (migration 122).",
        "tier_label": "elite",
        "markets": ["1x2"],
        "tier_filter": [2, 3, 4],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.07},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05},
        },
        "odds_range": (1.35, 3.50),
        "min_prob": 0.35,
    },
    "bot_conservative": {
        # Currently active. Originally retired 2026-05-17 (criteria too tight,
        # never fired in production since launch). Re-activated 2026-05-22 via
        # migration 122; expected to still fire rarely given the 10%+ edge gate.
        "description": "10%+ edge required, very selective — odds 1.50-3.00, min_prob 0.40. Re-enabled 2026-05-22.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.10, "1x2_long": 0.15},
            2: {"1x2_fav": 0.10, "1x2_long": 0.12},
            3: {"1x2_fav": 0.08, "1x2_long": 0.10},
            4: {"1x2_fav": 0.08, "1x2_long": 0.10},
        },
        "odds_range": (1.50, 3.00),
        "min_prob": 0.40,
    },
    # PERF-HONEST-HEADLINE (2026-05-17): retired via migration 104. Replaced by
    # bot_aggressive_v2. -5.7% ROI / -€141 on 441 settled bets — was the single
    # biggest drag on portfolio headline ROI. v2 keeps 129/441 of the bets at
    # +11.6% ROI by dropping draws + under 2.5 + capping odds 1.50-3.30.
    # Pipeline skips via `is_active=false AND retired_at IS NOT NULL` gate.
    "bot_aggressive": {
        # PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25): 25K-row sweep optimum = 15% edge
        # (baseline +0.4% → +9.0% ROI, n=2802). Tightened from 3-5% across all tiers.
        # SLICE-LIVE-VALIDATE (2026-05-25): retired two leaker slices on live evidence:
        #   selection:draw  live ROI -32.7% (n=89,  €-159) → exclude Draw via selection_filter
        #   odds  2.50-3.00 live ROI  -6.3% (n=150, €-57)  → cap odds_range at 2.50
        #   odds  3.50+     live ROI -13.9% (n=273, €-198) → cap odds_range at 2.50 (subsumed)
        # Tradeoff: capping at 2.50 also drops the 3.00-3.50 bucket which was +15.4%
        # on n=97 (€+71). Net live P&L delta if we'd applied this cap on the historical
        # window: +€343 from killing the leakers minus €71 lost = +€272 net positive.
        "description": "PER-BOT-EDGE-THRESHOLD-APPLY + SLICE-LIVE-VALIDATE 2026-05-25: 15% edge all tiers; odds capped 1.25-2.50; no Draw selection (live ROI -32.7%).",
        "tier_label": "pro",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "selection_filter": ["Home", "Away", "Over 2.5", "Under 2.5"],  # no Draw
        "edge_thresholds": {
            1: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            2: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            3: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            4: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
        },
        "odds_range": (1.25, 2.50),
        "min_prob": 0.25,
    },
    # AGGRESSIVE-V2 (2026-05-17): tightened sibling of bot_aggressive.
    # v1's 441 settled bets at -5.7% ROI broke down into three loss buckets:
    #   draws (61 bets, -€154), home odds >=3.30 high-edge (110 bets, -€95),
    #   OU under 2.5 (88 bets, -€46). Retroactive replay of v1's bets under
    #   v2's filters: 129/441 keep, +11.6% ROI, +€90 P&L (+€231 swing).
    # v1 stays running as the control — do not retire until v2 has its own
    # 100+ settled sample.
    "bot_aggressive_v2": {
        # PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25): 25K-row sweep optimum = 15% edge
        # (baseline -4.1% → +2.1% ROI, n=647). Tightened from 5-8% across all tiers.
        # Volume drops from 3548 → 647 backtest bets at 15%; intermediate buckets
        # (8-11%) stay -EV, so don't compromise.
        "description": "PER-BOT-EDGE-THRESHOLD-APPLY 2026-05-25: 15% edge across all tiers (sweep optimum on 25K backtest rows, baseline -4.1% → +2.1% ROI). Odds 1.50-3.30, no Draw, no Under 2.5.",
        "tier_label": "pro",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "selection_filter": ["Home", "Away", "Over 2.5"],  # no Draw, no Under 2.5
        "edge_thresholds": {
            1: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            2: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            3: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
            4: {"1x2_fav": 0.15, "1x2_long": 0.15, "ou": 0.15},
        },
        "odds_range": (1.50, 3.30),
        "min_prob": 0.30,
    },
    "bot_greek_turkish": {
        # NOTE: +ROI in 2022-25 backtest but -ROI in mega backtest (2005-15).
        # Era discrepancy — treat results here as exploratory until more live data.
        # PER-BOT-SLICE-TIGHTEN 2026-05-17: odds 3.50+ = -30% ROI (33 bets, -€99) → cap at 3.50.
        "description": "Only Greek + Turkish leagues — profitable in 2022-25 backtest (era-sensitive)",
        "tier_label": "elite",
        "markets": ["1x2"],
        "tier_filter": [1],
        "league_filter": ["Turkey", "Greece"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.04, "1x2_long": 0.06},
        },
        "odds_range": (1.40, 3.50),
        "min_prob": 0.30,
    },
    "bot_high_roi_global": {
        # RETIRED 2026-05-28 via migration 142. Config kept for bet_id linkage.
        # Live data: Scotland -78% Pinnacle CLV, Ireland -10% CLV. Austria +9% CLV
        # preserved in bot_proven_leagues_v2. Replaced by data-driven league selection.
        "description": "[RETIRED 2026-05-28] Mega backtest leagues — Scotland/Austria/Ireland/Korea. Scotland/Ireland showed strongly negative live Pinnacle CLV. Replaced by bot_proven_leagues_v2.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "tier_filter": None,
        "league_filter": ["Scotland", "Austria", "Ireland", "South Korea", "Singapore"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.06, "1x2_long": 0.09},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08},
            3: {"1x2_fav": 0.05, "1x2_long": 0.08},
        },
        "odds_range": (1.50, 5.00),
        "min_prob": 0.28,
    },
    # ── Optimizer-found bots (2026-04-27) ──────────────────────────────────
    # Grid-searched 412K parameter combos across 1.6M potential bets
    # (football-data 2007-2025 + beat_the_bookie 2005-2015).
    # Only cross-era validated strategies included.
    "bot_opt_away_british": {
        # Confirmed in both FD (+30.6% ROI) and BTB (+15-26% ROI) datasets.
        # Away wins in English lower divisions at mid-range longshot odds.
        "description": "Optimizer: Away wins, T2+ British Isles — cross-era +16% ROI, 336 bets",
        "tier_label": "elite",
        "markets": ["1x2"],
        "selection_filter": ["Away"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Scotland", "Ireland", "Wales"],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.05},
            3: {"1x2_fav": 0.05, "1x2_long": 0.05},
            4: {"1x2_fav": 0.05, "1x2_long": 0.05},
        },
        "odds_range": (2.20, 3.50),
        "min_prob": 0.25,
    },
    "bot_opt_away_europe": {
        # Confirmed in FD (+18.8% ROI, CI +4.9% to +32.8%) and BTB (+30.5%).
        # Away wins in Europe top 5 second divisions.
        "description": "Optimizer: Away wins, T2+ Europe top 5 — cross-era +19% ROI, 373 bets",
        "tier_label": "elite",
        "markets": ["1x2"],
        "selection_filter": ["Away"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Spain", "Germany", "Italy", "France"],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.05},
            3: {"1x2_fav": 0.05, "1x2_long": 0.05},
            4: {"1x2_fav": 0.05, "1x2_long": 0.05},
        },
        "odds_range": (2.20, 3.50),
        "min_prob": 0.40,
    },
    "bot_opt_home_lower": {
        # Confirmed in FD (+24.2% ROI) and BTB (+12.5% ROI, 448 bets).
        # Home underdogs in lower European divisions.
        # BOTS-RETIRE-1X2 (2026-05-17): retired — starved by alpha_t2_1x2=0.00 after
        # May 17 retrain. Live ROI +73% on 15 bets was variance, not signal.
        "description": "Optimizer: Home underdogs, T2+ Europe. FD backtest +24.2% ROI, BTB +12.5%. Originally retired 2026-05-17 (alpha_t2_1x2 starved it post-retrain); re-enabled 2026-05-22 via migration 122.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "selection_filter": ["Home"],
        "tier_filter": [2, 3, 4],
        "edge_thresholds": {
            2: {"1x2_fav": 0.08, "1x2_long": 0.08},
            3: {"1x2_fav": 0.08, "1x2_long": 0.08},
            4: {"1x2_fav": 0.08, "1x2_long": 0.08},
        },
        "odds_range": (3.00, 5.00),
        "min_prob": 0.30,
    },
    "bot_opt_ou_british": {
        # FD only (BTB has no O/U data) — +29% ROI, 85 bets, +22% on O/U combined.
        # Over 2.5 goals in English lower divisions at value odds.
        "description": "Optimizer: O/U T2+ British Isles — FD +22-29% ROI, 85-146 bets",
        "tier_label": "elite",
        "markets": ["ou"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Scotland", "Ireland", "Wales"],
        "edge_thresholds": {
            2: {"ou": 0.07},
            3: {"ou": 0.07},
            4: {"ou": 0.07},
        },
        "odds_range": (2.50, 4.00),
        "min_prob": 0.40,
    },

    # ─── New bots (2026-04-30): BTTS, O/U 1.5/3.5, draw, O/U 2.5 global ────

    "bot_btts_all": {
        # PER-BOT-SLICE-TIGHTEN reverted 2026-05-18: backtest said 2.00-2.50 loses (-6.5%),
        # but live v14 data (41 bets) shows that bucket at +20.5% ROI. Poisson backfill
        # miscalibrated at those odds; live filter stack (Pinnacle veto) fixes it. Keep (1.50, 2.80).
        # PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25): 25K-row sweep optimum = 12% edge
        # (baseline -0.3% → +5.8% ROI, n=331). Tightened from 3-4% across all tiers.
        # SLICE-LIVE-VALIDATE (2026-05-25): odds 1.50-2.00 bucket live ROI -13.9% on
        # n=69 (€-68) — cap odds_range floor at 2.00. The 2.00-2.50 bucket stays
        # (live +1.4% on n=67) and 2.50-2.80 stays.
        "description": "BTTS all leagues. PER-BOT-EDGE-THRESHOLD-APPLY + SLICE-LIVE-VALIDATE 2026-05-25: 12% edge all tiers; odds 2.00-2.80 (1.50-2.00 retired, live ROI -13.9%).",
        "tier_label": "pro",
        "markets": ["btts"],
        "edge_thresholds": {
            1: {"btts": 0.12},
            2: {"btts": 0.12},
            3: {"btts": 0.12},
            4: {"btts": 0.12},
        },
        "odds_range": (2.00, 2.80),
        "min_prob": 0.30,
    },
    "bot_btts_conservative": {
        # PER-BOT-SLICE-TIGHTEN 2026-05-17: odds 2.00-2.50 = -14.3% ROI (290 bets, -€415) → cap at 2.00.
        # PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25): 25K-row sweep optimum = 8% edge
        # (baseline -2.2% → +3.6% ROI, n=142). Tightened from 7% (small but consistent gain).
        "description": "BTTS top leagues only. PER-BOT-EDGE-THRESHOLD-APPLY 2026-05-25: 8% edge across all tiers (sweep optimum on 25K backtest rows, baseline -2.2% → +3.6% ROI).",
        "tier_label": "elite",
        "markets": ["btts"],
        "tier_filter": [1, 2],
        "edge_thresholds": {
            1: {"btts": 0.08},
            2: {"btts": 0.08},
        },
        "odds_range": (1.60, 2.00),
        "min_prob": 0.35,
    },
    "bot_ou15_defensive": {
        # BOT-OU15-RETIRE (2026-05-20): retired. Silent since 2026-05-08;
        # BOT-FUNNEL-DIAGNOSTIC confirmed 97/98 candidates die at ↓edge.
        # Relaxed thresholds (6%→4%, 5%→3%) recovered 0 of 104 candidates
        # in shadow run — calibration drift killed the bot's edge entirely.
        # Config kept in BOTS_CONFIG so historical bet_id linkage survives;
        # migration 113 flipped is_active=false + retired_at=now().
        # BOT-OU15-DIAGNOSE-CLOSE (2026-05-25): final re-retirement via migration 129.
        # Migration 117 un-retired it 2026-05-22 to gather signal — still silent
        # through 2026-05-25 (17-day total silence). Re-enable trigger unchanged:
        # 30+ shadow_bets at ≥3% real ROI sustained over a week.
        "description": "[RETIRED 2026-05-25] O/U 1.5 — odds 1.10-1.60. Retired via migration 129 after 17-day silent period (2026-05-08 → 2026-05-25). All diagnostics ruled out; calibration drift killed OU 1.5 edge structurally. Re-enable trigger: 30+ shadow_bets at ≥3% real ROI over a week.",
        "tier_label": "pro",
        "markets": ["ou15"],
        "edge_thresholds": {
            1: {"ou": 0.04},
            2: {"ou": 0.04},
            3: {"ou": 0.03},
            4: {"ou": 0.03},
        },
        "odds_range": (1.80, 3.50),
        "min_prob": 0.30,
    },
    "bot_ou35_attacking": {
        # PER-BOT-SLICE-TIGHTEN 2026-05-17: over @ 3.00-3.50 = -38.3% ROI (30 bets, -€115) → cap at 3.00.
        # PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25): 25K-row sweep optimum = 14% edge
        # (baseline +30.6% → +40.0% ROI, n=199). Tightened from 5-6% across all tiers.
        "description": "O/U 3.5. PER-BOT-EDGE-THRESHOLD-APPLY 2026-05-25: 14% edge across all tiers (sweep optimum on 25K backtest rows, baseline +30.6% → +40.0% ROI).",
        "tier_label": "pro",
        "markets": ["ou35"],
        "edge_thresholds": {
            1: {"ou": 0.14},
            2: {"ou": 0.14},
            3: {"ou": 0.14},
            4: {"ou": 0.14},
        },
        "odds_range": (1.80, 3.00),
        "min_prob": 0.30,
    },
    "bot_ou25_global": {
        # PER-BOT-SLICE-TIGHTEN 2026-05-17: odds 2.50-3.00 = -8.0% ROI (802 bets, -€643) → cap at 2.50.
        "description": "O/U 2.5 all leagues — extends bot_opt_ou_british globally",
        "tier_label": "pro",
        "markets": ["ou"],
        "edge_thresholds": {
            1: {"ou": 0.06},
            2: {"ou": 0.05},
            3: {"ou": 0.05},
            4: {"ou": 0.04},
        },
        "odds_range": (1.60, 2.50),
        "min_prob": 0.30,
    },
    "bot_draw_specialist": {
        # DRAW-LEAGUE-WHITELIST 2026-05-28: replaced tier_filter=[2,3,4] with an explicit
        # league_name_filter built from the 2023-2026 backtest (clean data, 30+ bets each).
        # Old tier filter blocked Brazil Serie A / Austria Bundesliga (T1) which both show
        # strong draw signals, while including Hungary NB II (-66.8%), Portugal Segunda
        # Liga (-88.4%), Slovenia 2.SNL (-35%), Bulgaria Second League (-64.9%) etc.
        # Whitelist keeps only confirmed-positive leagues; T1 thresholds added for new leagues.
        "description": "DRAW-LEAGUE-WHITELIST 2026-05-28: draw specialist — 12 leagues confirmed by 2023-2026 clean backtest. Replaced tier_filter=[2,3,4] with explicit league_name_filter. Drops Hungary/Portugal/Slovenia/Bulgaria leakers; adds Austria Bundesliga + Brazil Serie A (T1, previously blocked).",
        "tier_label": "pro",
        "markets": ["1x2"],
        "tier_filter": None,
        "selection_filter": ["Draw"],
        "league_name_filter": [
            ("Israel",         "Liga Leumit"),           # T2 — 128 bets +58.9%
            ("Austria",        "Bundesliga"),             # T1 — 73 bets  +46.6%
            ("Sweden",         "Ettan - Norra"),          # T3 — 82 bets  +43.6%
            ("Czech-Republic", "3. liga - CFL B"),        # T3 — 87 bets  +37.6%
            ("Brazil",         "Serie D"),                # T4 — 321 bets +32.5%
            ("Uruguay",        "Segunda División"),       # T2 — 61 bets  +24.3%
            ("Scotland",       "Championship"),           # T2 — 45 bets  +95.9%
            ("Sweden",         "Superettan"),             # T2 — 75 bets  +20.3%
            ("England",        "League Two"),             # T4 — 198 bets +17.9%
            ("Argentina",      "Primera B Metropolitana"),# T3 — 112 bets +17.0%
            ("England",        "Championship"),           # T2 — 227 bets +15.6%
            ("Brazil",         "Serie A"),                # T1 — 141 bets +12.5%
        ],
        "edge_thresholds": {
            1: {"1x2_long": 0.06},
            2: {"1x2_long": 0.05},
            3: {"1x2_long": 0.05},
            4: {"1x2_long": 0.04},
        },
        "odds_range": (2.80, 4.50),
        "min_prob": 0.22,
    },
    "bot_under25_specialist": {
        # UNDER25-SPECIALIST 2026-05-28: three leagues with confirmed OU2.5 under
        # signals from 2023-2026 clean backtest (women's/youth/cups excluded).
        # bot_ou25_global fires on these too but is -6.2% ROI overall because it
        # covers losing leagues like Spain Segunda División (-50.9%), Portugal (-19.5%),
        # France Ligue 1 under (-5.7%). This specialist keeps only the confirmed pockets.
        "description": "UNDER25-SPECIALIST 2026-05-28: OU2.5 under in 3 confirmed leagues (Eng Championship +19%, Poland Ekstraklasa +25.9%, Sweden Ettan Norra +33.3%). Subset of bot_ou25_global but filtered to profitable leagues only.",
        "tier_label": "pro",
        "markets": ["ou"],
        "selection_filter": ["Under 2.5"],
        "tier_filter": None,
        "league_name_filter": [
            ("England",  "Championship"),  # T2 — 242 bets +19.0%
            ("Poland",   "Ekstraklasa"),   # T1 — 33 bets  +25.9%
            ("Sweden",   "Ettan - Norra"), # T3 — 32 bets  +33.3%
        ],
        "edge_thresholds": {
            1: {"ou": 0.05},
            2: {"ou": 0.05},
            3: {"ou": 0.05},
        },
        "odds_range": (1.60, 2.50),
        "min_prob": 0.40,
    },
    "bot_sweden_over25": {
        # SWEDEN-OVER25 2026-05-28: paper bot targeting over 2.5 goals in Sweden's
        # top two divisions. Both show positive signal in backtest but below the
        # 30-bet validation threshold (Superettan: 23 bets +51.2%, Allsvenskan: 15
        # bets +40.0%). Creating now to accumulate live evidence; graduate to real
        # bets once 30+ settled with ≥+5% ROI.
        "description": "SWEDEN-OVER25 2026-05-28: paper bet on Over 2.5 in Superettan + Allsvenskan. Below 30-bet validation threshold (23+15 bets) but both strongly positive. Accumulating live data; graduate at 30+ settled + ≥+5% ROI.",
        "tier_label": "pro",
        "markets": ["ou"],
        "selection_filter": ["Over 2.5"],
        "tier_filter": None,
        "league_name_filter": [
            ("Sweden", "Superettan"),   # T2 — 23 bets +51.2% (below threshold — paper)
            ("Sweden", "Allsvenskan"),  # T1 — 15 bets +40.0% (below threshold — paper)
        ],
        "edge_thresholds": {
            1: {"ou": 0.05},
            2: {"ou": 0.05},
        },
        "odds_range": (1.50, 2.50),
        "min_prob": 0.35,
    },
    "bot_proven_leagues": {
        # RETIRED 2026-05-28 via migration 142. Config kept for bet_id linkage.
        # Scotland (-78% Pinnacle CLV) and Ireland (-28% CLV) killed performance.
        # Replaced by bot_proven_leagues_v2.
        "description": "[RETIRED 2026-05-28] Proven leagues — Scotland/Austria/Ireland/Korea. Scotland/Ireland showed strongly negative live CLV; replaced by bot_proven_leagues_v2 with data-driven league selection.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "tier_filter": None,
        "league_filter": ["Singapore", "Scotland", "Austria", "Ireland", "South Korea"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.05, "1x2_long": 0.08},
            2: {"1x2_fav": 0.04, "1x2_long": 0.06},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05},
        },
        "odds_range": (1.40, 5.00),
        "min_prob": 0.28,
    },
    "bot_proven_leagues_v2": {
        # PROVEN-V2 2026-05-28: Successor to bot_proven_leagues + bot_high_roi_global.
        # Core leagues validated against BOTH live Pinnacle CLV AND historical backtest
        # (214 bets, all settled matches back to 2023 in target countries):
        #   USA   (77 bets, +31.7% historical ROI, +10% live CLV)   ✓ validated
        #   France (35 bets, +39.2% historical ROI, +5-11% live CLV) ✓ validated
        #   Italy  (68 bets,  +4.6% historical ROI, +9-18% live CLV) ✓ validated
        #   Austria (23 bets, -32.1% historical ROI all-selection; home-only: +6.7% live CLV, 10 bets) ← beta
        #   Belgium (5 bets, +5.7% live CLV, Jupiler Pro League) ← beta, calibrate at 20+ bets
        #
        # Selection: Home ONLY. Historical split: Home +44.1% ROI (61 bets),
        # Away -0.6% ROI (153 bets). Away has no edge at these odds.
        "description": "PROVEN-V2: USA/France/Italy/Austria/Belgium home underdogs at 2.80-5.00. Core leagues validated: live Pinnacle CLV ≥+5% AND 214-bet historical backtest. Austria/Belgium added as beta expansion based on live CLV signal — calibrate at 20+ bets each.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "selection_filter": ["Home"],
        "tier_filter": None,
        "league_filter": ["Italy", "France", "USA", "Austria", "Belgium"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.06, "1x2_long": 0.09},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08},
            3: {"1x2_fav": 0.05, "1x2_long": 0.08},
        },
        "odds_range": (2.80, 5.00),
        "min_prob": 0.28,
    },

    "bot_high_roi_global_v2": {
        # HRG-V2 2026-05-28: Successor to bot_high_roi_global (retired migration 142).
        # Original HRG used 2005-2015 mega-backtest countries — now sharp. V2 rebuilt
        # from live Pinnacle CLV scan + 3-year historical data:
        #   Spain:     44 live bets, +5.7% avg CLV. Away underdogs: La Liga away +7.8%,
        #              Segunda away +5.8% CLV, avg odds 4.61-4.67. Historical Pinnacle
        #              naive away-backing +1.8% ROI on 1145 bets (3yr). Key gap: these
        #              bets are UNCOVERED — aggressive_v2 caps at 3.30, proven_v2 home-only.
        #   Australia: 21 live bets, +13% CLV (+15.1% home). Beta — only 1 month data.
        #   Iceland:   12 live bets, +11.5% CLV. Beta — 1 month data.
        # Home/Away only (Draw CLV inconsistent, small sample).
        "description": "HRG-V2: Spain/Australia/Iceland — globally soft markets rebuild. Spain validated (44 live bets, +5.7% CLV; away underdogs at +9.4% CLV, historical Pinnacle +1.8% on 1145 bets). Australia/Iceland beta (1-month sample, high CLV). Home/away at 1.50-5.50.",
        "tier_label": "elite",
        "markets": ["1x2"],
        "selection_filter": ["Home", "Away"],
        "tier_filter": None,
        "league_filter": ["Spain", "Australia", "Iceland"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.06, "1x2_long": 0.09},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08},
            3: {"1x2_fav": 0.05, "1x2_long": 0.08},
        },
        "odds_range": (1.50, 5.50),
        "min_prob": 0.28,
    },

    # ─── Double Chance bots (DC-BOTS 2026-05-11) ──────────────────────────────
    # DC probs derived at placement time from 1X2 calibrated probs:
    #   1X = home_prob + draw_prob, X2 = draw_prob + away_prob, 12 = home_prob + away_prob.
    # No Platt calibration or Pinnacle veto for DC (no DC Platt data yet; add after
    # first ~200 settled bets). Edge thresholds are lower than 1X2 because DC odds
    # are compressed (typical range 1.20-2.20), so a 3% edge at 1.30 is already
    # meaningful.
    "bot_dc_value": {
        # BOT-RETIRE-DESC-DRIFT (2026-05-24): config tag removed — bot was retired
        # 2026-05-19 (BOTS-RETIRE-DC-DNB) but migration 122_unretire_remaining_bots
        # re-activated it. Currently active, firing again post AH-CAL-BYPASS.
        "description": "Double Chance all leagues — 1X/X2/12 at value odds. Loose: T1-4, edge 3-5%, odds 1.25-2.20, min_prob 0.55.",
        "tier_label": "pro",
        "markets": ["dc"],
        "edge_thresholds": {
            1: {"dc": 0.05},
            2: {"dc": 0.04},
            3: {"dc": 0.04},
            4: {"dc": 0.03},
        },
        "odds_range": (1.25, 2.20),
        "min_prob": 0.55,
    },
    "bot_dc_strong_fav": {
        # BOT-RETIRE-DESC-DRIFT (2026-05-24): same as bot_dc_value above. Note
        # this bot is a TIGHTENED SUBSET of bot_dc_value — every pick it makes,
        # bot_dc_value also makes (selection ⊆ {1X,X2,12} since this excludes 12,
        # tier ⊆ {1,2,3,4} since 1-2, edge 6%+ ≥ value's 5%, etc.). So "2 bots
        # agree" badges on shared picks are misleading. Considered for re-retire
        # under AH-AWAY-MODEL-AUDIT 2026-05-24 follow-up.
        "description": "Double Chance T1-2 — strong-favorite cover (1X/X2 only), 6%+ edge. Tightened subset of bot_dc_value.",
        "tier_label": "elite",
        "markets": ["dc"],
        "selection_filter": ["1X", "X2"],
        "tier_filter": [1, 2],
        "edge_thresholds": {
            1: {"dc": 0.06},
            2: {"dc": 0.06},
        },
        "odds_range": (1.20, 1.80),
        "min_prob": 0.65,
    },
    "bot_ah_home_fav": {
        "description": "AH home — favourite covers T1-2, Poisson-priced, 5%+ edge. "
                       "AH-HOME-LINE-FILTER (2026-05-24): handicap_line_max=-0.5 so "
                       "the bot only fires when home is actually a favourite (giving "
                       "goals). The +0 line specifically was -54% ROI on 8 bets — "
                       "structurally miscalibrated when used by a favourite-specialist "
                       "(see scripts/ah_model_audit_live.py). Symmetric with "
                       "bot_ah_away_dog's handicap_line_min=+0.5.",
        "tier_label": "elite",
        "markets": ["ah"],
        "selection_filter": ["Home"],
        "tier_filter": [1, 2],
        "edge_thresholds": {
            1: {"ah": 0.05},
            2: {"ah": 0.05},
        },
        "odds_range": (1.50, 2.20),
        "min_prob": 0.55,
        # AH-HOME-LINE-FILTER (2026-05-24): hard ceiling on handicap line.
        # AH-AWAY-MODEL-AUDIT live-data follow-up showed both bots have ROI
        # ~-50% on the +0 line (home_fav 8 bets / -54%; away_dog 5 bets / -47%).
        # +0 is a DNB-equivalent line and is structurally over-priced by the
        # joint goal model when applied to a heavy favourite (push-adjusted
        # prob over-amplifies the imperfect favourite-longshot bias correction).
        # Restricting to handicap_line <= -0.5 forces home to be a true
        # favourite (giving goals). Symmetric with bot_ah_away_dog's
        # handicap_line_min=+0.5.
        "handicap_line_max": -0.5,
    },
    "bot_ah_away_dog": {
        "description": "AH away (UNDERDOG ONLY) — handicap_line >= 0 (away gets head start). "
                       "Original config (no line filter) lost -31.8% ROI over 12-day silent-period "
                       "backtest because 1X2_away Platt over-predicts at 40-60%+ predicted (actual "
                       "8-18pp below predicted). Negative handicaps (away-favorite) were catastrophic "
                       "(-46% to -81% ROI). Positive handicaps (+0.5) were +42.4% ROI. AH-AWAY-LINE-FILTER "
                       "restricts to the profitable segment until CAL-PLATT-UPGRADE refits 1X2_away.",
        "tier_label": "elite",
        "markets": ["ah"],
        "selection_filter": ["Away"],
        "tier_filter": [1, 2, 3],
        "edge_thresholds": {
            1: {"ah": 0.05},
            2: {"ah": 0.05},
            3: {"ah": 0.06},
        },
        "odds_range": (1.70, 2.50),
        "min_prob": 0.50,
        # AH-AWAY-LINE-FILTER (2026-05-24): hard floor on handicap line.
        # Slice-1 of AH-AWAY-MODEL-AUDIT (scripts/ah_away_model_audit.py):
        #   +1.0 line: hit 100% / ROI +111% (n=1)
        #   +0.5 line: hit 62.0% / ROI +42.4% (n=108)
        #   +0.0 line: hit 43.6% / ROI -4.2%  (n=61, ~breakeven) ← drop
        #   -0.5 line: hit 26.7% / ROI -45.9% (n=300) ← drop
        #   -1.0 line: hit 10.6% / ROI -74.4% (n=122) ← drop
        #   -1.5 line: hit 11.1% / ROI -81.1% (n=9)   ← drop
        # AH-AWAY-LINE-FILTER-TIGHTEN (2026-05-24, same day): candidate-bot
        # backtest (scripts/backtest_ah_new_bots.py) showed tightening from
        # hl>=0 to hl>=+0.5 moves ROI from +26.1% to +43.0%. The hl=0.0 bucket
        # is breakeven-negative on its own, dropping it is the right trade.
        "handicap_line_min": 0.5,
    },
    "bot_dnb_home_value": {
        # RETIRED 2026-05-29 via migration 148. Merged into bot_dnb_specialist.
        # Config kept for historical bet_id linkage — pipeline skips via is_active=false.
        "description": "[RETIRED 2026-05-29] DNB home — merged into bot_dnb_specialist (DNB Home profile).",
        "tier_label": "pro",
        "markets": ["dnb"],
        "selection_filter": ["Home"],
        "tier_filter": None,
        "league_name_filter": [
            ("Austria",  "Bundesliga"),
            ("Mexico",   "Liga MX"),
            ("Russia",   "Premier League"),
            ("Israel",   "Liga Leumit"),
            ("Uruguay",  "Segunda División"),
        ],
        "edge_thresholds": {1: {"dnb": 0.05}, 2: {"dnb": 0.05}},
        "odds_range": (1.30, 1.90),
        "min_prob": 0.60,
    },
    "bot_dnb_away_value": {
        # RETIRED 2026-05-29 via migration 148. Merged into bot_dnb_specialist.
        # Config kept for historical bet_id linkage — pipeline skips via is_active=false.
        "description": "[RETIRED 2026-05-29] DNB away — merged into bot_dnb_specialist (DNB Away profile).",
        "tier_label": "pro",
        "markets": ["dnb"],
        "selection_filter": ["Away"],
        "tier_filter": None,
        "league_name_filter": [
            ("England",   "League Two"),
            ("Sweden",    "Allsvenskan"),
            ("Brazil",    "Serie B"),
            ("England",   "Championship"),
            ("Argentina", "Primera Nacional"),
        ],
        "edge_thresholds": {1: {"dnb": 0.05}, 2: {"dnb": 0.05}, 3: {"dnb": 0.06}, 4: {"dnb": 0.06}},
        "odds_range": (1.60, 2.60),
        "min_prob": 0.50,
    },
    "bot_dnb_specialist": {
        # MULTI-STRATEGY-BOTS 2026-05-29: merges bot_dnb_home_value + bot_dnb_away_value
        # into one bot with two named profiles. Each profile has its own league whitelist,
        # thresholds, odds range, and selection filter. Per-profile ROI is queryable via
        # strategy_profile column on simulated_bets.
        "description": "MULTI-STRATEGY-BOTS 2026-05-29: DNB specialist with 'DNB Home' and 'DNB Away' profiles. Each profile has its own league whitelist and config. Replaces bot_dnb_home_value + bot_dnb_away_value.",
        "tier_label": "pro",
        "markets": ["dnb"],
        "tier_filter": None,
        "edge_thresholds": {},  # per-strategy only — required key but overridden by each strategy
        "odds_range": (1.0, 99.0),  # placeholder — overridden by each strategy
        "min_prob": 0.0,            # placeholder — overridden by each strategy
        "strategies": [
            {
                "alias": "DNB Home",
                "selection_filter": ["Home"],
                "league_name_filter": [
                    ("Austria",  "Bundesliga"),       # T1 — 92 bets  +19.0%
                    ("Mexico",   "Liga MX"),           # T1 — 30 bets  +43.8%
                    ("Russia",   "Premier League"),    # T1 — 44 bets  +11.5%
                    ("Israel",   "Liga Leumit"),       # T2 — 37 bets  +11.4%
                    ("Uruguay",  "Segunda División"),  # T2 — 32 bets  +11.5%
                ],
                "edge_thresholds": {1: {"dnb": 0.05}, 2: {"dnb": 0.05}},
                "odds_range": (1.30, 1.90),
                "min_prob": 0.60,
            },
            {
                "alias": "DNB Away",
                "selection_filter": ["Away"],
                "league_name_filter": [
                    ("England",   "League Two"),       # T4 — 99 bets  +25.4%
                    ("Sweden",    "Allsvenskan"),       # T1 — 168 bets +20.6%
                    ("Brazil",    "Serie B"),           # T1 — 34 bets  +26.6%
                    ("England",   "Championship"),      # T2 — 71 bets  +10.3%
                    ("Argentina", "Primera Nacional"),  # T2 — 42 bets  +13.2%
                ],
                "edge_thresholds": {1: {"dnb": 0.05}, 2: {"dnb": 0.05}, 3: {"dnb": 0.06}, 4: {"dnb": 0.06}},
                "odds_range": (1.60, 2.60),
                "min_prob": 0.50,
            },
        ],
    },
    "bot_high_alignment": {
        # BOT-HIGH-ALIGNMENT (2026-05-25): paper bot that fires only when
        # alignment_class == HIGH. The hypothesis is that HIGH alignment marks
        # the strongest signal stack (most dimensions agree), so even modest
        # edge bets should be +EV here. Sample is too small in current 30-day
        # data (n=27 HIGH bets, mostly losing — noisy), so this bot is the
        # accumulator. Picks all markets the other bots cover, with a low
        # 3% edge floor; min_alignment_class enforces the gate.
        # Tier B (50% stake) and recorded as paper-only until 100+ settled.
        "description": "BOT-HIGH-ALIGNMENT 2026-05-25: paper bot, alignment_class=HIGH only, 3% edge floor across all markets. Tier B stake (50%). Re-evaluate after 100 settled bets.",
        "tier_label": "pro",
        "markets": ["1x2", "ou", "btts", "ah", "dnb", "dc"],
        "tier_filter": None,
        "min_alignment_class": "HIGH",
        "edge_thresholds": {
            1: {"1x2_fav": 0.03, "1x2_long": 0.03, "ou": 0.03, "btts": 0.03, "ah": 0.03, "dnb": 0.03, "dc": 0.03},
            2: {"1x2_fav": 0.03, "1x2_long": 0.03, "ou": 0.03, "btts": 0.03, "ah": 0.03, "dnb": 0.03, "dc": 0.03},
            3: {"1x2_fav": 0.03, "1x2_long": 0.03, "ou": 0.03, "btts": 0.03, "ah": 0.03, "dnb": 0.03, "dc": 0.03},
            4: {"1x2_fav": 0.03, "1x2_long": 0.03, "ou": 0.03, "btts": 0.03, "ah": 0.03, "dnb": 0.03, "dc": 0.03},
        },
        "odds_range": (1.30, 4.50),
        "min_prob": 0.25,
    },
}


# BOT-TIMING cohort assignment — A/B test to find optimal bet timing.
# Cohorts map to scheduler windows:
#   morning  → 06:00 UTC (full match slate, fresh opening odds)
#   midday   → 11:00 UTC (injury news refreshed, standings updated)
#   pre_ko   → 15:00-19:00 UTC (confirmed lineups, most info available)
#   all      → run at every cohort window; dedup (uq_bet) prevents duplicate bets —
#              first run that clears the edge threshold wins, rest are no-ops.
#              Use for bets where early soft odds outweigh lineup uncertainty.
BOT_TIMING_COHORTS: dict[str, str] = {
    # BOT-COHORTS-ALL (2026-05-20): all bots set to "all" so they fire at every
    # cohort window. Dedup (commit aa799cf — "keep highest-edge bot" on
    # bot/match/market/selection) prevents duplicate bets. Prior cohort
    # assignments were based on Phase A timing analysis with small samples
    # (n=114 morning vs n=31 midday for OU bets). That data was suggestive
    # but not decisive; the May 17 retrain and May 7-8 calibration changes
    # likely shifted it anyway. Now that migration 112 fixed the
    # shadow_cohort constraint, BET-TIMING-MONITOR Phase 3 (~2026-06-15)
    # can accumulate proper factorial data per (bot, cohort) to re-impose
    # gating ONLY where it demonstrably helps.
    #
    # Bots known to benefit from late-window data (confirmed lineups for
    # DNB / "opt_away" / "opt_home_lower") are kept on "all" — the bet
    # still fires from morning onwards but the pre_ko evaluation catches
    # any edge that only appears once lineups land. Dedup prevents double
    # placement; if morning fires first, that's the price we get.
    "bot_v10_all":          "all",
    "bot_lower_1x2":        "all",
    "bot_aggressive":       "all",
    "bot_aggressive_v2":    "all",
    "bot_conservative":     "all",
    "bot_greek_turkish":    "all",
    "bot_proven_leagues_v2": "all",
    "bot_high_roi_global":      "all",
    "bot_high_roi_global_v2":   "all",
    "bot_ou15_defensive":   "all",
    "bot_ou35_attacking":   "all",
    "bot_ou25_global":      "all",
    "bot_opt_ou_british":   "all",
    "bot_draw_specialist":      "all",
    "bot_under25_specialist":   "all",
    "bot_sweden_over25":        "all",
    "bot_opt_away_british": "all",
    "bot_opt_away_europe":  "all",
    "bot_opt_home_lower":   "all",
    "bot_btts_all":         "all",
    "bot_btts_conservative":"all",
    "bot_proven_leagues":   "all",
    "bot_dc_value":         "all",
    "bot_dc_strong_fav":    "all",
    "bot_ah_home_fav":      "all",
    "bot_ah_away_dog":      "all",
    "bot_dnb_home_value":   "all",   # RETIRED 2026-05-29 — kept for shadow tracking
    "bot_dnb_away_value":   "all",   # RETIRED 2026-05-29 — kept for shadow tracking
    "bot_dnb_specialist":   "all",
    "bot_high_alignment":   "all",
}


# Dixon-Coles correlation parameter — corrects independent Poisson's draw underestimation.
# Global fallback value. The pipeline loads per-tier values from model_calibration at startup
# (fit by scripts/fit_league_rho.py, refreshed weekly on Sundays alongside Platt).
DIXON_COLES_RHO = -0.13

# ACCESSIBLE-BM: bookmakers whose odds feed into edge calculation and bet recommendations.
# Only books accessible to EU/Estonian users with a real API-Football feed. Inaccessible
# books (SBO, Dafabet, 1xBet, William Hill, BetVictor, Betfair) are excluded so reported
# edge is not inflated by odds the user can never actually achieve.
# Pinnacle is included because it's available via Pinnacle Sports .com (manual) and also
# serves as the sharpest-book quality reference throughout the pipeline.
ACCESSIBLE_BOOKMAKERS: frozenset = frozenset({
    "Bet365", "Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Pinnacle",
    # COOLBET-AS-ACCESSIBLE (2026-05-20): Coolbet is our actual placement
    # venue and now ingested every 30m by the daemon's wide odds mode. Adding
    # it here so its prices feed edge math — if Coolbet has the best price on
    # a match where soft books are weaker, that becomes a real bet candidate.
    # The feedback loop: Coolbet odds in → new edges discovered → bets
    # generated → daemon places them at Coolbet (best available price).
    "Coolbet",
})

# Cache: {league_tier (1-4): rho}. Loaded once per pipeline run.
_dc_rho_cache: dict | None = None


def _load_dc_rho_cache() -> dict:
    """
    Load per-tier Dixon-Coles rho from model_calibration table.
    Keys in DB: 'dc_rho_tier_1', 'dc_rho_tier_2', 'dc_rho_tier_3', 'dc_rho_tier_4'.
    platt_a stores the rho value (platt_b = 0, unused).
    Falls back to empty dict (→ global DIXON_COLES_RHO) if no rows.
    """
    global _dc_rho_cache
    if _dc_rho_cache is not None:
        return _dc_rho_cache

    _dc_rho_cache = {}
    try:
        from workers.api_clients.db import execute_query as _eq_rho
        rows = _eq_rho(
            """
            SELECT DISTINCT ON (market) market, platt_a
            FROM model_calibration
            WHERE market LIKE 'dc_rho_tier_%'
            ORDER BY market, fitted_at DESC
            """,
            [],
        )
        for row in rows:
            try:
                tier_num = int(row["market"].replace("dc_rho_tier_", ""))
                _dc_rho_cache[tier_num] = float(row["platt_a"])
            except (ValueError, KeyError):
                pass
    except Exception:
        pass  # Table may lack dc_rho rows yet — fall back to global

    return _dc_rho_cache


def _dc_tau(h: int, a: int, exp_h: float, exp_a: float, rho: float) -> float:
    """Dixon-Coles correction factor τ for the four low-scoring outcomes."""
    if h == 0 and a == 0:
        return 1.0 - exp_h * exp_a * rho
    if h == 1 and a == 0:
        return 1.0 + exp_a * rho
    if h == 0 and a == 1:
        return 1.0 + exp_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _parse_af_xg(val) -> float | None:
    """Parse an AF expected-goals field ('1.7', 1.7, etc.) into a float.

    Returns None when the value is missing, non-numeric, or outside the
    plausible per-team xG range [0.1, 6.0]. AF occasionally returns blank
    strings for matches with no team-stats coverage — those collapse to None
    so the Tier C fallback (TIER-C-AF-XG) keeps using its hardcoded prior.
    """
    if val is None:
        return None
    try:
        f = float(str(val).strip().rstrip("%"))
    except (ValueError, TypeError):
        return None
    if not (0.1 <= f <= 6.0):
        return None
    return f


def _poisson_probs(exp_h: float, exp_a: float, rho: float | None = None, league_draw_pct: float | None = None) -> dict:
    """Compute 1X2 + O/U (1.5, 2.5, 3.5) + BTTS probabilities from expected goals.

    Applies Dixon-Coles bivariate correction to the four low-scoring outcomes
    (0-0, 1-0, 0-1, 1-1) to fix the ~8% draw underestimation of independent Poisson.
    1X2 probabilities are renormalised after correction.

    Args:
        rho: Dixon-Coles correlation parameter. If None, uses the per-tier cached
             value from model_calibration (loaded by _load_dc_rho_cache()), or falls
             back to DIXON_COLES_RHO (-0.13) if no DB value available.
    """
    _rho = rho if rho is not None else DIXON_COLES_RHO
    p_h = p_d = p_a = 0.0
    p_over_15 = p_over_25 = p_over_35 = 0.0
    p_btts_yes = 0.0

    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a)
            p *= _dc_tau(h, a, exp_h, exp_a, _rho)
            if h > a:
                p_h += p
            elif h == a:
                p_d += p
            else:
                p_a += p
            if h + a > 1:
                p_over_15 += p
            if h + a > 2:
                p_over_25 += p
            if h + a > 3:
                p_over_35 += p
            if h >= 1 and a >= 1:
                p_btts_yes += p

    # Renormalise 1x2 after DC correction (τ shifts probability mass slightly)
    total_1x2 = p_h + p_d + p_a
    if total_1x2 > 0:
        p_h /= total_1x2
        p_d /= total_1x2
        p_a /= total_1x2

    # CAL-DRAW-INFLATE / DRAW-PER-LEAGUE: Dixon-Coles τ only patches (0,0)-(1,1) corner cells.
    # Higher-scoring draws (2-2, 3-3) remain underestimated vs real data.
    # Game-state effects (protecting leads, parking the bus) also inflate draws.
    # Per-league: leagues with high draw rates (e.g. 32%) get higher multiplier than
    # open attacking leagues (e.g. 22%). Global avg is 26.8%. Clamped [1.03, 1.15].
    if league_draw_pct is not None:
        raw_inflate = 1.0 + max(0.0, (league_draw_pct - 0.268) / 0.268 * 0.08)
        DRAW_INFLATE = max(1.03, min(1.15, raw_inflate))
    else:
        DRAW_INFLATE = 1.08  # validated global fallback
    p_d_inflated = p_d * DRAW_INFLATE
    leftover = 1.0 - p_d_inflated
    home_away_sum = p_h + p_a
    if home_away_sum > 0:
        scale = leftover / home_away_sum
        p_h *= scale
        p_a *= scale
    p_d = p_d_inflated

    return {
        "home_prob": p_h, "draw_prob": p_d, "away_prob": p_a,
        "over_15_prob": p_over_15, "under_15_prob": 1 - p_over_15,
        "over_25_prob": p_over_25, "under_25_prob": 1 - p_over_25,
        "over_35_prob": p_over_35, "under_35_prob": 1 - p_over_35,
        "btts_yes_prob": p_btts_yes, "btts_no_prob": 1 - p_btts_yes,
    }


def _goals_from_hist(df: pd.DataFrame, team: str) -> tuple[list[float], list[float]]:
    """Extract goals-for and goals-against lists for a team from a history DataFrame."""
    gf, ga = [], []
    for _, m in df.iterrows():
        if m["home_team"] == team:
            gf.append(float(m["FTHG"]))
            ga.append(float(m["FTAG"]))
        else:
            gf.append(float(m["FTAG"]))
            ga.append(float(m["FTHG"]))
    return gf, ga


def _ah_model_prob(exp_h: float, exp_a: float, selection: str, handicap_line: float,
                   rho: float | None = None) -> float:
    """
    Fair probability for an Asian Handicap bet using Poisson + Dixon-Coles scoring.

    handicap_line: home team's handicap (negative = home gives goals, e.g. -1.25).
    selection: 'home' or 'away'.

    Line types handled:
      whole (x.0)  — push when margin == spread → conditional prob (excl. push)
      half  (x.5)  — no push; strict win/loss
      x.25 quarter — half-loss when margin == floor(spread); EV-adjusted pricing
      x.75 quarter — half-win when margin == floor(spread)+1; EV-adjusted pricing
    """
    _rho = rho if rho is not None else DIXON_COLES_RHO

    # Build integer margin PMF from Poisson + Dixon-Coles
    margin_pmf: dict[int, float] = {}
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a) * _dc_tau(h, a, exp_h, exp_a, _rho)
            m = h - a
            margin_pmf[m] = margin_pmf.get(m, 0.0) + p

    # spread = goals home must win by for a "home" bet to win
    spread = -handicap_line
    floor_s = math.floor(spread)
    frac = spread - floor_s  # always [0, 1)

    if frac < 0.01:  # whole line — push at margin == spread
        s = round(spread)
        p_win = sum(p for m, p in margin_pmf.items() if m > s)
        p_lose = sum(p for m, p in margin_pmf.items() if m < s)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif abs(frac - 0.5) < 0.01:  # half line — no push
        p_win = sum(p for m, p in margin_pmf.items() if m > spread)
        p_lose = sum(p for m, p in margin_pmf.items() if m < spread)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif frac < 0.5:  # x.25 quarter: half-loss when margin == floor_s
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 1)
        p_half_loss = margin_pmf.get(floor_s, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s - 1)
        denom = p_full_win + 0.5 * p_half_loss + p_full_lose
        home_prob = p_full_win / denom if denom > 0 else 0.5
    else:  # x.75 quarter: half-win when margin == floor_s + 1
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 2)
        p_half_win = margin_pmf.get(floor_s + 1, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s)
        numerator = p_full_win + 0.5 * p_half_win
        denom = numerator + p_full_lose
        home_prob = numerator / denom if denom > 0 else 0.5

    return 1.0 - home_prob if selection == "away" else home_prob


# Per-pipeline-run cache: (round(p_home,3), round(p_draw,3)) → (exp_h, exp_a)
# Populated lazily during prediction loop; cleared between runs automatically
# since it's module-level but only filled within a run's match iteration.
_ah_lambda_cache: dict[tuple[float, float], tuple[float, float]] = {}


def _solve_lambdas_calibrated(p_home: float, p_draw: float) -> tuple[float, float] | None:
    """Invert Platt-calibrated 1x2 probs → Poisson (exp_h, exp_a) with caching.

    Using calibrated probs corrects the systematic home-advantage underestimation
    in raw Poisson lambdas (~7.5% bias measured vs Pinnacle closing, 2026-05-21).
    Returns None if optimisation fails.
    """
    from scipy.optimize import minimize

    key = (round(p_home, 3), round(p_draw, 3))
    if key in _ah_lambda_cache:
        return _ah_lambda_cache[key]

    p_away = max(0.01, 1.0 - p_home - p_draw)
    ratio = p_home / p_away
    exp_total = max(0.8, 2.8 - 3.0 * max(0.0, p_draw - 0.25))
    exp_h0 = exp_total * (ratio ** 0.55) / (1.0 + ratio ** 0.55)
    x0 = [max(0.2, exp_h0), max(0.2, exp_total - exp_h0)]

    def _loss(x: list) -> float:
        eh, ea = max(0.15, x[0]), max(0.15, x[1])
        r = _poisson_probs(eh, ea)
        return (r["home_prob"] - p_home) ** 2 + (r["draw_prob"] - p_draw) ** 2

    try:
        res = minimize(_loss, x0, method="Powell",
                       options={"xtol": 0.002, "ftol": 1e-5, "maxiter": 200})
        result = (max(0.15, res.x[0]), max(0.15, res.x[1]))
        _ah_lambda_cache[key] = result
        return result
    except Exception:
        return None


def compute_prediction(match, hist_targets, hist_targets_global=None,
                       _team_sets=None, league_draw_pct: float | None = None):
    """
    Compute Poisson prediction for a match using the best available history.

    Data tiers:
      A — team found in targets_poisson_history (has bookmaker odds calibration)
      B — team found only in targets_global (global results, no odds calibration)
      D — no historical data (AF prediction fallback only)

    _team_sets: optional pre-computed (v9_teams, global_teams) to avoid rebuilding per call.

    Returns prediction dict with a 'data_tier' field, or None if no data.
    """
    from workers.utils.team_names import normalize_team_name, fuzzy_match_team

    home_raw = match["home_team"]
    away_raw = match["away_team"]

    # Normalise team names
    home = normalize_team_name(home_raw, source="default")
    away = normalize_team_name(away_raw, source="default")

    # --- Tier A: search in targets_poisson_history ---
    if _team_sets:
        v9_teams, global_teams_set = _team_sets
    else:
        v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
        global_teams_set = None
    home_v9 = fuzzy_match_team(home, v9_teams) or fuzzy_match_team(home_raw, v9_teams)
    away_v9 = fuzzy_match_team(away, v9_teams) or fuzzy_match_team(away_raw, v9_teams)

    # --- Tier B: search in targets_global ---
    home_global = away_global = None
    if hist_targets_global is not None:
        if global_teams_set is not None:
            global_teams = global_teams_set
        else:
            global_teams = (
                set(hist_targets_global["home_team"].unique()) |
                set(hist_targets_global["away_team"].unique())
            )
        if not home_v9:
            home_global = fuzzy_match_team(home, global_teams) or fuzzy_match_team(home_raw, global_teams)
        if not away_v9:
            away_global = fuzzy_match_team(away, global_teams) or fuzzy_match_team(away_raw, global_teams)

    # Determine effective matched names and tier
    home_matched = home_v9 or home_global
    away_matched = away_v9 or away_global

    if home_v9 and away_v9:
        data_tier = "A"
    elif home_matched and away_matched:
        data_tier = "B"
    else:
        # No historical data — skip (Tier C handled by AF prediction only)
        return None

    # --- Fetch history for matched teams ---
    # Home team history: prefer v9 if available, else global
    if home_v9:
        home_hist = hist_targets[
            (hist_targets["home_team"] == home_v9) |
            (hist_targets["away_team"] == home_v9)
        ].tail(20)
    else:
        home_hist = hist_targets_global[
            (hist_targets_global["home_team"] == home_global) |
            (hist_targets_global["away_team"] == home_global)
        ].tail(20)

    # Away team history: prefer v9 if available, else global
    if away_v9:
        away_hist = hist_targets[
            (hist_targets["home_team"] == away_v9) |
            (hist_targets["away_team"] == away_v9)
        ].tail(20)
    else:
        away_hist = hist_targets_global[
            (hist_targets_global["home_team"] == away_global) |
            (hist_targets_global["away_team"] == away_global)
        ].tail(20)

    if len(home_hist) < 3 or len(away_hist) < 3:
        return None

    home_gf, home_ga = _goals_from_hist(home_hist, home_matched if home_v9 else home_global)
    away_gf, away_ga = _goals_from_hist(away_hist, away_matched if away_v9 else away_global)

    exp_h = max(0.3, np.mean(home_gf[-10:])) * 1.08  # Slight home advantage
    exp_a = max(0.3, np.mean(away_gf[-10:])) * 0.92
    exp_h = (exp_h + np.mean(away_ga[-10:])) / 2
    exp_a = (exp_a + np.mean(home_ga[-10:])) / 2

    # Use per-tier rho if available (fit by scripts/fit_league_rho.py),
    # otherwise falls back to global DIXON_COLES_RHO (-0.13).
    league_tier = int(match.get("tier") or 1)
    tier_rho = _load_dc_rho_cache().get(league_tier)  # None → _poisson_probs uses global
    result = _poisson_probs(exp_h, exp_a, rho=tier_rho, league_draw_pct=league_draw_pct)
    result.update({"exp_home": exp_h, "exp_away": exp_a, "data_tier": data_tier})
    return result


def _store_parsed_odds(match_id: str, parsed_odds: list[dict]):
    """Store pre-parsed API-Football odds rows directly into odds_snapshots."""
    from psycopg2.extras import execute_values
    from workers.api_clients.db import get_conn
    now = datetime.now().astimezone().isoformat()

    rows = [
        (match_id, row["bookmaker"], row["market"], row["selection"],
         row["odds"], row.get("handicap_line"), now, False, None)
        for row in parsed_odds
    ]

    if rows:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """INSERT INTO odds_snapshots
                           (match_id, bookmaker, market, selection, odds, handicap_line, timestamp, is_closing, minutes_to_kickoff)
                           VALUES %s ON CONFLICT DO NOTHING""",
                        rows,
                        page_size=500,
                    )
                    conn.commit()
        except Exception:
            pass  # Dedup errors are fine


def _fetch_af_predictions(af_id_to_match_id: dict[int, str]) -> dict[str, dict]:
    """
    Fetch API-Football predictions for all today's fixtures.
    Returns {match_id: parsed_prediction_dict}.

    BULK-STORE-PREDICTIONS: collects rows in memory and bulk-writes once at the
    end. Same pattern as the standalone fetch_predictions.py — saves ~4
    round-trips per fixture × 500 fixtures (~5min on EU pooler → ~1s).
    """
    from workers.api_clients.supabase_client import (
        bulk_store_predictions, bulk_update_match_af_predictions,
    )
    import json as _json

    af_preds: dict[str, dict] = {}
    fetched = 0
    failed = 0
    af_jsonb_rows: list[tuple[str, str]] = []
    pred_rows: list[dict] = []

    console.print(f"\n[cyan]Fetching API-Football predictions ({len(af_id_to_match_id)} fixtures)...[/cyan]")

    for af_id, match_id in af_id_to_match_id.items():
        try:
            raw = get_prediction(af_id)
            if not raw:
                failed += 1
                continue

            parsed = parse_prediction(raw)
            if not parsed.get("af_home_prob"):
                failed += 1
                continue

            af_preds[match_id] = parsed
            af_jsonb_rows.append((match_id, _json.dumps(parsed["raw"])))

            for market, prob_key in (
                ("1x2_home", "af_home_prob"),
                ("1x2_draw", "af_draw_prob"),
                ("1x2_away", "af_away_prob"),
            ):
                prob = parsed.get(prob_key)
                if prob is not None:
                    pred_rows.append({
                        "match_id": match_id,
                        "market": market,
                        "source": "af",
                        "model_prob": prob,
                        "reasoning": "af_prediction",
                    })

            fetched += 1

        except Exception:
            failed += 1
            continue

    try:
        n_jsonb = bulk_update_match_af_predictions(af_jsonb_rows)
        if n_jsonb:
            console.print(f"  [dim]bulk UPDATE matches.af_prediction: {n_jsonb} rows[/dim]")
    except Exception as e:
        console.print(f"  [yellow]bulk_update_match_af_predictions failed: {e}[/yellow]")

    try:
        n_pred = bulk_store_predictions(pred_rows)
        if n_pred:
            console.print(f"  [dim]bulk INSERT predictions (source=af): {n_pred} rows[/dim]")
    except Exception as e:
        console.print(f"  [red]bulk_store_predictions failed: {e}[/red]")

    console.print(f"  {fetched} predictions stored, {failed} unavailable (league not supported by AF predictions)")
    return af_preds


def _af_agrees_with_bet(selection: str, parsed_pred: dict | None) -> bool | None:
    """
    Determine if API-Football's prediction agrees with our bet selection.

    For 1X2 bets: AF agrees if their highest probability matches the selection.
    For O/U bets: AF agrees if their under_over sign matches ("+2.5" = over, "-2.5" = under).

    Returns True/False/None (None = no AF prediction available).
    """
    if not parsed_pred:
        return None

    home_p = parsed_pred.get("af_home_prob") or 0
    draw_p = parsed_pred.get("af_draw_prob") or 0
    away_p = parsed_pred.get("af_away_prob") or 0

    sel_l = selection.lower()
    if sel_l == "home":
        return home_p >= draw_p and home_p >= away_p
    elif sel_l == "away":
        return away_p >= home_p and away_p >= draw_p
    elif sel_l == "draw":
        return draw_p >= home_p and draw_p >= away_p
    elif "over" in sel_l:
        uo = parsed_pred.get("af_under_over") or ""
        return str(uo).startswith("+")
    elif "under" in sel_l:
        uo = parsed_pred.get("af_under_over") or ""
        return str(uo).startswith("-")

    return None


_TOP_FLIGHT_COUNTRIES = {
    "England", "Spain", "Germany", "Italy", "France",
    "Netherlands", "Portugal", "Turkey", "Greece", "Scotland",
    "Belgium", "Sweden", "Denmark", "Norway", "Poland",
    "Croatia", "Romania", "Serbia", "Ukraine", "Hungary",
    "Iceland", "Latvia", "Cyprus", "Georgia", "Estonia",
    "Austria", "Switzerland", "Russia", "Czech Republic",
    "Slovakia", "Bulgaria", "Belarus", "Finland",
}

# Known tier-2 league name fragments (overrides country-level tier-1 default)
_TIER2_FRAGMENTS = {
    "Championship", "2. Bundesliga", "Serie B", "Ligue 2", "La Liga 2",
    "Liga 2", "Segunda", "Esiliiga", "OBOS", "I Liga", "NB II", "NB 2",
}


def _league_path_to_tier(league_path: str) -> int:
    """
    Derive tier from league path. Tier 1 = top domestic flight, Tier 2 = second tier, etc.
    Uses country + known tier-2 fragment heuristic. League tier stored in DB is authoritative;
    this is only used during initial fixture ingestion before DB lookup is available.
    """
    country = league_path.split(" / ")[0] if " / " in league_path else ""
    name = league_path.split(" / ")[-1] if " / " in league_path else league_path
    if any(frag in name for frag in _TIER2_FRAGMENTS):
        return 2
    return 1 if country in _TOP_FLIGHT_COUNTRIES else 2


def _merge_odds_sources(af_odds_fixtures: list[dict]) -> list[dict]:
    """
    Build the prediction pool from API-Football odds fixtures.
    Previously also merged Kambi odds; Kambi was removed 2026-05-06 after
    empirical analysis showed it never provided the best odds vs AF's 13 bookmakers.
    """
    merged: dict[str, dict] = {}

    def _key(m: dict) -> str:
        date_part = m.get("start_time", "")[:10] or "nodate"
        return f"{m.get('home_team', '').lower()}_{m.get('away_team', '').lower()}_{date_part}"

    for m in af_odds_fixtures:
        k = _key(m)
        if k and k != "__nodate":
            merged[k] = {**m, "bookmaker": "api-football"}

    return list(merged.values())


def _fetch_morning_enrichment(af_fixtures_raw: list[dict], af_id_to_match_id: dict[int, str]):
    """
    T2, T3, T9, T10: Enrich today's fixtures with team stats, injuries,
    standings, and H2H data. Called once per morning after fixtures are stored.
    """
    if not af_fixtures_raw or not af_id_to_match_id:
        return

    today = date.today()
    season = today.year if today.month >= 7 else today.year - 1

    # Build per-fixture metadata for enrichment calls
    fixture_meta: dict[int, dict] = {}
    for af_fix in af_fixtures_raw:
        fid = af_fix.get("fixture", {}).get("id")
        if not fid:
            continue
        teams = af_fix.get("teams", {})
        league = af_fix.get("league", {})
        fixture_meta[fid] = {
            "match_id": af_id_to_match_id.get(fid),
            "home_team_api_id": teams.get("home", {}).get("id"),
            "away_team_api_id": teams.get("away", {}).get("id"),
            "league_api_id": league.get("id"),
            "season": league.get("season") or season,
        }

    # ── T3: Injuries (single /injuries?date= call) ──────────────────────────
    console.print("\n[cyan]T3: Fetching injuries (by date, single call)...[/cyan]")
    eligible_fids = {fid for fid, m in fixture_meta.items() if m.get("match_id")}
    injuries_by_fixture: dict[int, list[dict]] = {}
    try:
        injuries_by_fixture = get_injuries_by_date(today.isoformat())
    except Exception as e:
        console.print(f"  [yellow]Injuries fetch error: {e}[/yellow]")

    inj_stored = 0
    for fid, injuries in injuries_by_fixture.items():
        if fid not in eligible_fids or not injuries:
            continue
        meta = fixture_meta.get(fid, {})
        match_id = meta.get("match_id")
        if not match_id:
            continue
        parsed = parse_injuries(injuries, home_team_api_id=meta.get("home_team_api_id"))
        inj_stored += store_match_injuries(match_id, fid, parsed)
    console.print(f"  {inj_stored} injury records stored")

    # ── T2: Team Statistics (Tier A only, ~2 calls per Tier A fixture) ────────
    console.print("[cyan]T2: Fetching team statistics (Tier A only)...[/cyan]")

    # Batch-fetch tier for all today's matches (1 query)
    tier_by_match: dict[str, int] = {}
    match_ids_for_tier = [m["match_id"] for m in fixture_meta.values() if m.get("match_id")]
    if match_ids_for_tier:
        try:
            from workers.api_clients.db import execute_query as _eq2
            tier_r = _eq2(
                "SELECT m.id, l.tier FROM matches m LEFT JOIN leagues l ON m.league_id = l.id WHERE m.id = ANY(%s)",
                [match_ids_for_tier]
            )
            for row in tier_r:
                tier_by_match[row["id"]] = row.get("tier") or 3
        except Exception:
            pass

    t2_stored = 0
    seen_t2: set[tuple] = set()
    for fid, meta in fixture_meta.items():
        match_id_t2 = meta.get("match_id")
        if not match_id_t2:
            continue
        if tier_by_match.get(match_id_t2, 3) != 1:
            continue  # Tier A only

        lg_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")

        for api_id in [meta.get("home_team_api_id"), meta.get("away_team_api_id")]:
            if not api_id or not lg_api_id or not fix_season:
                continue
            key = (api_id, lg_api_id, fix_season)
            if key in seen_t2:
                continue
            seen_t2.add(key)
            try:
                raw_t2 = get_team_statistics(api_id, lg_api_id, fix_season)
                if raw_t2:
                    parsed_t2 = parse_team_statistics(raw_t2)
                    store_team_season_stats(api_id, lg_api_id, fix_season, parsed_t2)
                    t2_stored += 1
            except Exception:
                continue

    console.print(f"  {t2_stored} team stat records stored ({len(seen_t2)} unique Tier A teams)")

    # ── T9: League Standings (~1 call per unique league) ───────────────────
    console.print("[cyan]T9: Fetching league standings...[/cyan]")
    seen_leagues: set[tuple] = set()
    standings_stored = 0

    for fid, meta in fixture_meta.items():
        league_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")
        if not league_api_id or not fix_season:
            continue
        key = (league_api_id, fix_season)
        if key in seen_leagues:
            continue
        seen_leagues.add(key)

        try:
            raw = get_standings(league_api_id, fix_season)
            if not raw:
                continue
            rows = parse_standings(raw)
            stored = store_league_standings(league_api_id, fix_season, rows)
            standings_stored += stored
        except Exception:
            continue

    console.print(f"  {standings_stored} standing rows stored across {len(seen_leagues)} leagues")

    # ── T10: H2H (~1 call per fixture) ─────────────────────────────────────
    console.print("[cyan]T10: Fetching H2H history...[/cyan]")
    h2h_stored = 0

    for fid, meta in fixture_meta.items():
        match_id = meta.get("match_id")
        home_id = meta.get("home_team_api_id")
        away_id = meta.get("away_team_api_id")
        if not match_id or not home_id or not away_id:
            continue

        try:
            raw = get_h2h(home_id, away_id, last=10)
            if not raw:
                continue
            parsed = parse_h2h(raw, home_team_api_id=home_id)
            store_match_h2h(match_id, parsed)
            h2h_stored += 1
        except Exception:
            continue

    console.print(f"  {h2h_stored} H2H records stored")


def _fetch_af_bulk_odds(today_str, af_fixtures_raw, af_id_to_match_id):
    """Fetch odds from API-Football bulk endpoint and parse per fixture."""
    af_odds_fixtures = []
    af_odds_fetched = 0

    console.print("\n[cyan]Fetching odds from API-Football (bulk)...[/cyan]")
    try:
        bulk_odds = get_odds_by_date(today_str)
        console.print(f"  {len(bulk_odds)} fixtures with odds from API-Football")

        for af_fix in af_fixtures_raw:
            af_id = af_fix.get("fixture", {}).get("id")
            if not af_id or af_id not in bulk_odds:
                continue

            parsed = parse_fixture_odds(bulk_odds[af_id])
            if not parsed:
                continue

            best: dict[str, float] = {}
            ah_lines_best: dict[tuple, float] = {}  # (selection, handicap_line) -> best_odds
            for row in parsed:
                if row["market"] == "1x2":
                    field = f"odds_{row['selection']}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]
                elif row["market"] == "btts":
                    field = f"odds_btts_{row['selection']}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]
                elif row["market"] == "asian_handicap":
                    hl = row.get("handicap_line")
                    if hl is not None:
                        ah_key = (row["selection"], float(hl))
                        if row["odds"] > ah_lines_best.get(ah_key, 0):
                            ah_lines_best[ah_key] = row["odds"]
                elif row["market"] in ("double_chance",):
                    sel_map = {"1x": "odds_dc_1x", "x2": "odds_dc_x2", "12": "odds_dc_12"}
                    field = sel_map.get(row["selection"])
                    if field and row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]
                else:
                    direction = "over" if row["selection"] == "over" else "under"
                    line_suffix = row["market"].replace("over_under_", "")
                    field = f"odds_{direction}_{line_suffix}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]

            if not best:
                continue

            match_dict = fixture_to_match_dict(af_fix)
            league_path = match_dict["league_path"]
            tier = _league_path_to_tier(league_path)

            match_id = af_id_to_match_id.get(af_id)
            af_odds_fixtures.append({
                **match_dict,
                **best,
                "id": match_id,
                "tier": tier,
                "bookmaker": "api-football",
                "ah_lines": [
                    {"selection": sel, "handicap_line": hl, "odds": odds}
                    for (sel, hl), odds in ah_lines_best.items()
                ],
            })
            af_odds_fetched += 1
            if match_id:
                _store_parsed_odds(match_id, parsed)

    except Exception as e:
        console.print(f"  [yellow]AF bulk odds error: {e}[/yellow]")

    console.print(f"  {af_odds_fetched} AF fixtures with odds (tier assigned)")
    return af_odds_fixtures


def _parallel_fetch(af_id_to_match_id, af_fixtures_raw, today_str, all_fixtures):
    """
    Fetch predictions, enrichment, and bulk odds from API-Football.
    Kambi was removed 2026-05-06 — empirical data showed it never provided
    best odds vs the 13 bookmakers already covered by API-Football Ultra.
    """
    af_preds = {}
    if af_id_to_match_id:
        af_preds = _fetch_af_predictions(af_id_to_match_id)
        console.print(f"  AF predictions: {len(af_preds)} available out of {len(af_id_to_match_id)} fixtures")
    console.print("\n[cyan]Running morning enrichment (T2/T3/T9/T10)...[/cyan]")
    try:
        _fetch_morning_enrichment(af_fixtures_raw, af_id_to_match_id)
    except Exception as e:
        console.print(f"  [yellow]Enrichment error (non-fatal): {e}[/yellow]")
    af_odds_fixtures = _fetch_af_bulk_odds(today_str, af_fixtures_raw, af_id_to_match_id)
    return af_preds, af_odds_fixtures


def _next_day(date_str: str) -> str:
    """Return the next calendar day as YYYY-MM-DD."""
    from datetime import timedelta
    d = date.fromisoformat(date_str)
    return (d + timedelta(days=1)).isoformat()


def _load_today_from_db(today_str: str) -> tuple[list[dict], list[dict], dict[str, dict], dict[str, dict]]:
    """
    PIPE-2: Load today's matches with best pre-match odds + AF predictions from DB.
    No API calls — reads odds_snapshots and predictions tables only.
    Uses direct SQL to avoid PostgREST URL length limits with large IN clauses.
    Returns (odds_matches, af_only_matches, af_preds, best_bookmaker):
      - odds_matches: matches with odds (used for betting + signals)
      - af_only_matches: matches with predictions but no odds (signals only, no betting)
      - af_preds: AF prediction probabilities keyed by match_id
      - best_bookmaker: {match_id → {market_selection_key → bookmaker_name}}
    """
    from collections import defaultdict as _dd
    from workers.api_clients.db import execute_query
    next_day_str = _next_day(today_str)

    # 1. Load today's scheduled + recently-live matches with team + league info
    matches_raw = execute_query(
        """SELECT m.id, m.date, m.referee, m.season,
                  m.home_team_id, m.away_team_id,
                  m.home_team_api_id, m.away_team_api_id,
                  m.h2h_home_wins, m.h2h_draws, m.h2h_away_wins,
                  th.name AS home_team_name, th.country AS home_country,
                  ta.name AS away_team_name, ta.country AS away_country,
                  l.name AS league_name, l.country AS league_country,
                  l.tier AS league_tier, l.api_football_id AS league_api_id,
                  m.league_id
           FROM matches m
           JOIN teams th ON m.home_team_id = th.id
           JOIN teams ta ON m.away_team_id = ta.id
           LEFT JOIN leagues l ON m.league_id = l.id
           WHERE m.date >= %s AND m.date < %s
             AND m.status = 'scheduled'""",
        (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z"),
    )

    if not matches_raw:
        return [], [], {}, {}

    # Only bet on matches that haven't kicked off yet.
    # 'scheduled' status + kickoff in the future = safe to bet.
    # We exclude anything at or past kickoff — bookmakers close pre-match
    # markets at kickoff and our Poisson model is only valid pre-match.
    now_utc = datetime.now(timezone.utc)
    filtered = []
    for m in matches_raw:
        kickoff_str = str(m.get("date", ""))
        try:
            kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff > now_utc:  # Kickoff must still be in the future
                filtered.append(m)
        except (ValueError, AttributeError):
            filtered.append(m)

    if not filtered:
        return [], [], {}, {}

    match_ids = [m["id"] for m in filtered]

    # 2. Load best pre-match odds per match per (market, selection) — single query
    # ODDS-QUALITY-CLEANUP: exclude bookmakers known to ship garbage on OU lines
    # (api-football synthetic, api-football-live in-play, William Hill line-shifted).
    # 1X2 and BTTS rows from the same bookmakers are kept — those markets are clean.
    odds_raw = execute_query(
        """SELECT match_id, market, selection, odds, bookmaker, handicap_line
           FROM odds_snapshots
           WHERE match_id = ANY(%s::uuid[]) AND is_closing = false
             AND NOT (
               market LIKE 'over_under_%%'
               AND bookmaker IN ('api-football', 'api-football-live', 'William Hill')
             )""",
        (match_ids,),
    )

    # OU-PIN-REQUIRED (2026-05-10): for OU markets, only aggregate prices when
    # Pinnacle has a row for the same (match, market, selection). Without that
    # reference the cap can't fire and a single mislabelled book row gets
    # promoted by MAX-across-books. Coverage check at ship time: Pinnacle
    # prices ~58% of OU 1.5 / ~85% of OU 2.5 matches — the OU bots place fewer
    # bets but every one is validated against the sharpest book. When Pinnacle
    # IS present, also drop any non-Pinnacle row priced more than 2× Pinnacle
    # (catches mislabelled / Asian-total rows on books that do have a price).
    # Background: bot_ou15_defensive's pre-guard 38-bet history was 50% void
    # (12 had no Pinnacle ref, 7 exceeded the 2× cap). This rule is what would
    # have blocked all 19 voids at placement time.
    pin_ou: dict[str, dict[str, float]] = _dd(dict)  # mid -> "market_selection" -> pinnacle odds
    for row in odds_raw:
        if row.get("bookmaker") != "Pinnacle":
            continue
        if not str(row.get("market", "")).startswith("over_under_"):
            continue
        mid = str(row["match_id"])
        key = f"{row['market']}_{row['selection']}"
        try:
            pin_ou[mid][key] = float(row["odds"])
        except (TypeError, ValueError):
            pass

    best: dict[str, dict[str, float]] = _dd(lambda: _dd(float))
    best_bookmaker: dict[str, dict[str, str]] = _dd(dict)  # ACCESSIBLE-BM: which book had best accessible odds
    bm_sources: dict[str, set] = _dd(set)
    # AH lines: {match_id -> {(selection, handicap_line) -> best_odds}}
    ah_best: dict[str, dict[tuple, float]] = _dd(dict)
    for row in odds_raw:
        mid = str(row["match_id"])
        market = str(row.get("market", ""))
        key = f"{market}_{row['selection']}"
        odds_val = float(row["odds"])
        bookmaker = row.get("bookmaker") or "unknown"
        bm_sources[mid].add(bookmaker)  # track all sources for display, before any filtering
        if market.startswith("over_under_"):
            pin_price = pin_ou.get(mid, {}).get(key)
            if pin_price is None:
                continue  # OU-PIN-REQUIRED — no Pinnacle reference for this OU selection, skip
            if bookmaker != "Pinnacle" and odds_val > 2.0 * pin_price:
                continue  # OU-PINNACLE-CAP — likely mislabelled / Asian-total row
        # ACCESSIBLE-BM: only aggregate odds from bookmakers users can actually bet at
        if bookmaker not in ACCESSIBLE_BOOKMAKERS:
            continue
        if market == "asian_handicap":
            hl = row.get("handicap_line")
            if hl is not None:
                ah_key = (str(row["selection"]), float(hl))
                if odds_val > ah_best[mid].get(ah_key, 0):
                    ah_best[mid][ah_key] = odds_val
            continue  # don't also add to flat `best` dict
        if odds_val > best[mid][key]:
            best[mid][key] = odds_val
            best_bookmaker[mid][key] = bookmaker

    # ODDS-QUALITY-CLEANUP: implied-sum sanity gate on OU pairs.
    # Drop both sides of any (over, under) where 1/over + 1/under < 1.02
    # (impossible market — every legit feed has overround ≥ 2%).
    # Auto-quarantines any future broken source without code changes.
    OU_PAIRS = [
        ("over_under_05_over", "over_under_05_under"),
        ("over_under_15_over", "over_under_15_under"),
        ("over_under_25_over", "over_under_25_under"),
        ("over_under_35_over", "over_under_35_under"),
        ("over_under_45_over", "over_under_45_under"),
    ]
    for mid in list(best.keys()):
        for over_key, under_key in OU_PAIRS:
            o, u = best[mid].get(over_key, 0), best[mid].get(under_key, 0)
            if o > 0 and u > 0 and (1.0 / o + 1.0 / u) < 1.02:
                best[mid][over_key] = 0
                best[mid][under_key] = 0

    MARKET_TO_FIELD = {
        "1x2_home": "odds_home", "1x2_draw": "odds_draw", "1x2_away": "odds_away",
        "over_under_05_over": "odds_over_05", "over_under_05_under": "odds_under_05",
        "over_under_15_over": "odds_over_15", "over_under_15_under": "odds_under_15",
        "over_under_25_over": "odds_over_25", "over_under_25_under": "odds_under_25",
        "over_under_35_over": "odds_over_35", "over_under_35_under": "odds_under_35",
        "over_under_45_over": "odds_over_45", "over_under_45_under": "odds_under_45",
        "btts_yes": "odds_btts_yes", "btts_no": "odds_btts_no",
        "double_chance_1x": "odds_dc_1x",
        "double_chance_x2": "odds_dc_x2",
        "double_chance_12": "odds_dc_12",
    }

    # 3. Build match dicts
    odds_matches = []
    af_only_matches = []
    for m in filtered:
        mid = str(m["id"])
        match_best = best.get(mid, {})

        country = m.get("league_country") or ""
        league_name = m.get("league_name") or ""

        match_dict: dict = {
            "id": mid,
            "home_team": m.get("home_team_name", ""),
            "away_team": m.get("away_team_name", ""),
            "start_time": str(m.get("date", "")),
            "league_path": f"{country} / {league_name}" if country and league_name else league_name,
            "tier": int(m.get("league_tier") or 1),
            "league_api_id": m.get("league_api_id"),
            "season": m.get("season"),
            "referee": m.get("referee"),
            "home_team_id": str(m["home_team_id"]) if m.get("home_team_id") else None,
            "away_team_id": str(m["away_team_id"]) if m.get("away_team_id") else None,
            "home_team_api_id": m.get("home_team_api_id"),
            "away_team_api_id": m.get("away_team_api_id"),
            "league_id": str(m["league_id"]) if m.get("league_id") else None,
            "h2h_home_wins": m.get("h2h_home_wins"),
            "h2h_draws": m.get("h2h_draws"),
            "h2h_away_wins": m.get("h2h_away_wins"),
            "bookmaker": "+".join(sorted(bm_sources.get(mid, {"unknown"}))),
            "odds_home": 0, "odds_draw": 0, "odds_away": 0,
            "odds_over_05": 0, "odds_under_05": 0,
            "odds_over_15": 0, "odds_under_15": 0,
            "odds_over_25": 0, "odds_under_25": 0,
            "odds_over_35": 0, "odds_under_35": 0,
            "odds_over_45": 0, "odds_under_45": 0,
            "odds_btts_yes": 0, "odds_btts_no": 0,
            "odds_dc_1x": 0, "odds_dc_x2": 0, "odds_dc_12": 0,
            "ah_lines": [
                {"selection": sel, "handicap_line": hl, "odds": odds}
                for (sel, hl), odds in ah_best.get(mid, {}).items()
            ],
        }
        if match_best:
            for mkt_sel, field in MARKET_TO_FIELD.items():
                val = match_best.get(mkt_sel, 0)
                if val > 0:
                    match_dict[field] = val
            odds_matches.append(match_dict)
        else:
            af_only_matches.append(match_dict)

    # 4. Load AF predictions — single query
    preds_raw = execute_query(
        """SELECT match_id, market, model_probability
           FROM predictions
           WHERE match_id = ANY(%s::uuid[]) AND source = 'af'""",
        (match_ids,),
    )

    af_preds: dict[str, dict] = {}
    for p in preds_raw:
        mid = str(p["match_id"])
        if mid not in af_preds:
            af_preds[mid] = {}
        mp = float(p["model_probability"])
        if p["market"] == "1x2_home":
            af_preds[mid]["af_home_prob"] = mp
        elif p["market"] == "1x2_draw":
            af_preds[mid]["af_draw_prob"] = mp
        elif p["market"] == "1x2_away":
            af_preds[mid]["af_away_prob"] = mp

    console.print(f"  {len(odds_matches)} matches with odds loaded from DB")
    console.print(f"  {len(af_only_matches)} AF-only matches (no odds) loaded from DB")
    console.print(f"  {len(af_preds)} AF predictions loaded from DB")
    return odds_matches, af_only_matches, af_preds, dict(best_bookmaker)


def _print_funnel(funnel: dict, only_bot: str | None) -> None:
    """Print a per-bot candidate funnel table — shows how many candidates each
    bot generated and where in the filter chain they were dropped. Used to
    diagnose silent bots like bot_ou15_defensive.

    only_bot: if set, only print that one bot's row (cleaner output for
    targeted investigations)."""
    from rich.table import Table as _Table
    bots = sorted(funnel.keys()) if not only_bot else (
        [only_bot] if only_bot in funnel else []
    )
    if not bots:
        console.print(f"\n[yellow]No funnel data for bot '{only_bot}' "
                      f"(bot not in run universe — check cohort + tier filters).[/yellow]")
        return

    # Columns are ordered as they appear in the filter chain.
    columns = [
        ("candidates",        "Cand"),
        ("drop_nan_raw",      "NaN-raw"),
        ("drop_nan_cal",      "NaN-cal"),
        ("drop_edge",         "↓edge"),
        ("drop_odds_too_low", "odds<min"),
        ("drop_odds_too_high","odds>max"),
        ("drop_min_prob",     "<minP"),
        ("drop_pin_veto",     "PIN-veto"),
        ("drop_sharp_gate",   "sharp"),
        ("drop_odds_mv",      "odds-mv"),
        ("drop_kelly_zero",   "kelly≤0"),
        ("drop_aln1",         "ALN-1"),
        ("drop_stake_low",    "stake<1"),
        ("accepted",          "✓ acc"),
    ]
    t = _Table(show_header=True, title="Candidate funnel per bot")
    t.add_column("Bot")
    for _, label in columns:
        t.add_column(label, justify="right")
    for bot in bots:
        c = funnel[bot]
        # Skip bots that generated zero candidates AND have nothing dropped —
        # that's a bot filtered out at the cohort/tier/league level.
        if sum(c.values()) == 0:
            continue
        t.add_row(bot, *[str(c.get(k, 0)) for k, _ in columns])
    console.print()
    console.print(t)


def run_morning(skip_fetch: bool = False, cohort: str | None = None,
                shadow_mode: bool = False, shadow_cohort: str | None = None,
                verbose_funnel: bool = False, verbose_funnel_bot: str | None = None):
    """
    Fetch data → predict → store matches/odds/bets in Supabase.

    skip_fetch=True (Phase 2): reads pre-fetched data from DB — no API calls.
    skip_fetch=False (Phase 1 / manual): fetches from API-Football + Kambi first.
    cohort: if set, only run bots assigned to that timing cohort (morning/midday/pre_ko).
            None = run all bots (backward-compatible).
    shadow_mode=True (BET-TIMING-MONITOR): evaluate ALL bots regardless of cohort;
            write to shadow_bets instead of simulated_bets; no bankroll mutation;
            no bot active-flag check. Used to break the cohort×strategy confound
            in the cohort A/B. `shadow_cohort` MUST be set when shadow_mode=True
            (the window this shadow batch represents, e.g. 'morning').
    verbose_funnel=True (BOT-FUNNEL-DIAGNOSTIC): at end of run, print a per-bot
            candidate funnel table showing how many candidates were generated
            and where in the filter chain they were dropped. Used to diagnose
            silent bots (eg bot_ou15_defensive since 2026-05-08). Counters
            track every drop point: NaN guards, edge/odds/prob threshold,
            Pinnacle veto, sharp-consensus gate, odds-movement veto, Kelly,
            ALN-1, stake-too-small. The accepted row is what made it to
            placement / shadow_bets.
    verbose_funnel_bot: if set, only print that one bot's funnel (cleaner
            output when only one bot's silence is being investigated).
    """
    from workers.utils.kill_switches import is_disabled
    if is_disabled("paper_betting"):
        return
    if shadow_mode and not shadow_cohort:
        raise ValueError("shadow_mode=True requires shadow_cohort to be set")

    import uuid as _uuid
    from collections import Counter as _Counter, defaultdict as _dd_ct
    _shadow_run_id: str | None = str(_uuid.uuid4()) if shadow_mode else None
    _pending_shadow_rows: list[dict] = []
    _funnel: dict[str, _Counter] = _dd_ct(_Counter)  # bot_name → Counter(step → n)

    today_str = date.today().isoformat()
    mode_tag = f" [SHADOW {shadow_cohort}]" if shadow_mode else ""
    console.print(f"[bold green]═══ OddsIntel Pipeline: {today_str}{mode_tag} ═══[/bold green]\n")

    # 1. Ensure bots exist in DB
    console.print("[cyan]Creating/checking bots in Supabase...[/cyan]")
    bot_ids = ensure_bots(BOTS_CONFIG)
    console.print(f"  {len(bot_ids)} bots ready")

    # Running bankroll: tracks in-run stake spend so later bets size against
    # remaining capital (not the same starting bankroll for every bet).
    # Also load is_active so the pipeline can skip paused/retired bots.
    from workers.api_clients.db import execute_query as _eq_br
    _running_bankroll: dict[str, float] = {}
    _bot_active: dict[str, bool] = {}
    for _bn, _bid in bot_ids.items():
        try:
            _row = _eq_br(
                "SELECT current_bankroll, is_active, retired_at FROM bots WHERE id = %s",
                [_bid],
            )
            if _row:
                _running_bankroll[_bn] = float(_row[0]["current_bankroll"])
                _bot_active[_bn] = bool(_row[0].get("is_active")) and _row[0].get("retired_at") is None
            else:
                _running_bankroll[_bn] = 1000.0
                _bot_active[_bn] = True
        except Exception:
            _running_bankroll[_bn] = 1000.0
            _bot_active[_bn] = True

    af_only_matches: list[dict] = []  # matches with predictions but no odds (signals only)
    best_bookmaker: dict[str, dict[str, str]] = {}  # ACCESSIBLE-BM: set by _load_today_from_db (Phase 2) or empty (Phase 1)
    if skip_fetch:
        # Phase 2: read from DB — upstream jobs already fetched everything
        console.print("\n[cyan]Loading today's data from DB (skip_fetch=True)...[/cyan]")
        odds_matches, af_only_matches, af_preds, best_bookmaker = _load_today_from_db(today_str)
        if not odds_matches and not af_only_matches:
            console.print("[yellow]No matches in DB today — pipeline skipped.[/yellow]")
            return
        console.print(f"  [bold]{len(odds_matches)} matches with odds, {len(af_only_matches)} AF-only[/bold]")
    else:
        # Phase 1: fetch from API-Football + Kambi
        # 2. Fetch ALL fixtures from API-Football
        console.print("\n[cyan]Fetching all fixtures from API-Football...[/cyan]")
        af_fixtures_raw = []
        all_fixtures = []
        try:
            af_fixtures_raw = get_fixtures_by_date(today_str)
            console.print(f"  {len(af_fixtures_raw)} fixtures from API-Football")
        except Exception as e:
            console.print(f"  [red]API-Football error: {e}[/red]")
            return

        if not af_fixtures_raw:
            console.print("[yellow]No fixtures from API-Football today.[/yellow]")
            return

        # 3. Store all fixtures in Supabase via bulk_store_matches
        # (BULK-STORE-MATCHES). Pre-builds match_dicts and ships in one batched
        # call instead of N serial round-trips per fixture.
        console.print("\n[cyan]Storing all fixtures in Supabase...[/cyan]")
        af_id_to_match_id: dict[int, str] = {}

        match_dicts = [fixture_to_match_dict(af_fix) for af_fix in af_fixtures_raw]
        try:
            match_ids = bulk_store_matches(match_dicts)
        except Exception as e:
            console.print(f"  [red]Bulk store failed: {e}[/red]")
            return

        stored_fixture_count = 0
        for af_fix, match_dict, match_id in zip(af_fixtures_raw, match_dicts, match_ids):
            if not match_id:
                continue
            af_id = af_fix.get("fixture", {}).get("id")
            if af_id:
                af_id_to_match_id[af_id] = match_id
            all_fixtures.append({
                "home_team": match_dict["home_team"],
                "away_team": match_dict["away_team"],
                "date": match_dict["start_time"],
                "league_name": af_fix.get("league", {}).get("name", ""),
                "country": af_fix.get("league", {}).get("country", ""),
                "status": af_fix.get("fixture", {}).get("status", {}).get("short", "NS"),
                "api_football_id": af_id,
            })
            stored_fixture_count += 1

        console.print(f"  {stored_fixture_count} fixtures stored")

        # === PARALLEL DATA FETCH ===
        console.print("\n[cyan]Running parallel data fetch (predictions + enrichment + odds)...[/cyan]")
        af_preds, af_odds_fixtures = \
            _parallel_fetch(af_id_to_match_id, af_fixtures_raw, today_str, all_fixtures)

        # 6. Build prediction pool from AF odds
        odds_matches = _merge_odds_sources(af_odds_fixtures)

        console.print(f"\n  [bold]{len(odds_matches)} matches in prediction pool[/bold]")
        source_counts: dict[str, int] = {}
        for m in odds_matches:
            for src in m.get("bookmaker", "unknown").split("+"):
                source_counts[src] = source_counts.get(src, 0) + 1
        for source, count in sorted(source_counts.items()):
            console.print(f"  {source}: {count}")

        if not odds_matches:
            console.print("[yellow]No matches with odds today — predictions skipped.[/yellow]")
            return

    # 7. Load historical data for predictions
    console.print("\n[cyan]Loading historical data...[/cyan]")
    targets_path = PROCESSED_DIR / "targets_poisson_history.csv"
    if not targets_path.exists():
        targets_path = PROCESSED_DIR / "targets_fast.csv"
    hist_targets = pd.read_csv(targets_path)
    console.print(f"  {len(hist_targets):,} Tier A matches (targets_poisson_history)")

    hist_targets_global = None
    global_path = PROCESSED_DIR / "targets_global.csv"
    if global_path.exists():
        hist_targets_global = pd.read_csv(global_path)
        console.print(f"  {len(hist_targets_global):,} Tier B matches (targets_global)")
    else:
        console.print("  [yellow]targets_global.csv not found — Tier B unavailable[/yellow]")

    extended_path = PROCESSED_DIR / "targets_extended.csv"
    if extended_path.exists():
        extended_df = pd.read_csv(extended_path)
        if hist_targets_global is not None:
            hist_targets_global = pd.concat([hist_targets_global, extended_df], ignore_index=True)
        else:
            hist_targets_global = extended_df
        console.print(f"  +{len(extended_df):,} Tier B rows from targets_extended (total Tier B: {len(hist_targets_global):,})")

    # Pre-compute team name sets once (avoid rebuilding per match — saves ~30s)
    v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
    global_teams = None
    if hist_targets_global is not None:
        global_teams = set(hist_targets_global["home_team"].unique()) | set(hist_targets_global["away_team"].unique())
    team_sets = (v9_teams, global_teams)

    # Pre-compute league-average BTTS rates for Tier C fallback.
    # Poisson gives us no goal-model for unknown teams, but a league-average
    # BTTS rate (e.g. Czech Republic 35.8%, Sweden 63.7%) is real signal vs
    # the market's implied probability.
    console.print("\n[cyan]Loading league BTTS rates (Tier C fallback)...[/cyan]")
    _league_btts_rates: dict[str, float] = {}
    _global_btts_rate = 0.538  # fallback if no league history
    try:
        btts_rows = _eq_br("""
            SELECT m.league_id,
                   AVG(CASE WHEN m.score_home >= 1 AND m.score_away >= 1 THEN 1.0 ELSE 0.0 END) as btts_rate,
                   COUNT(*) as n
            FROM matches m
            WHERE m.status = 'finished' AND m.score_home IS NOT NULL
            GROUP BY m.league_id
            HAVING COUNT(*) >= 20
        """, [])
        for r in btts_rows:
            _league_btts_rates[str(r["league_id"])] = float(r["btts_rate"])
        console.print(f"  {len(_league_btts_rates)} leagues with BTTS history (global avg {_global_btts_rate:.1%})")
    except Exception as e:
        console.print(f"  [yellow]BTTS rate load failed (non-critical): {e}[/yellow]")

    # 8a. Write all morning signals in one batch — includes AF-only (Grade D) matches
    # Previously only ran for matches with odds; now runs for ALL today's matches so
    # Grade D fixtures get ELO, form, H2H, injury, standings signals too.
    console.print("\n[cyan]Writing morning signals (batch)...[/cyan]")
    try:
        all_signal_matches = odds_matches + af_only_matches
        n_signals = batch_write_morning_signals(all_signal_matches)
        console.print(f"  {n_signals} signals written for {len(all_signal_matches)} matches ({len(odds_matches)} with odds, {len(af_only_matches)} AF-only)")
    except Exception as e:
        console.print(f"  [yellow]batch_write_morning_signals failed (non-critical): {e}[/yellow]")

    # 8b. MFV-LIVE-BUILD — build match_feature_vectors rows for today's pre-KO
    # matches so v10+ XGBoost inference (`_build_row_from_mfv`) finds a row
    # instead of falling back to Poisson-only. Must run after morning signals
    # are written (signals + ELO + form + opening odds are MFV inputs) and
    # before the match loop runs `get_xgboost_prediction`. Re-runs on every
    # betting_refresh because opening_implied_* / odds_drift_home pick up
    # newer snapshots each pass. v10's FEATURE_COLS contains no
    # prediction-source columns, so it's safe to build the row before the
    # Poisson predictions are written in the loop below.
    console.print("\n[cyan]MFV-LIVE-BUILD: writing pre-KO feature vectors...[/cyan]")
    try:
        n_mfv = build_match_feature_vectors_live(None, today_str)
        console.print(f"  {n_mfv} MFV rows upserted for today's pre-KO matches")
    except Exception as e:
        console.print(f"  [yellow]MFV-LIVE-BUILD failed (non-critical, v10+ inference will fall back to Poisson): {e}[/yellow]")

    # 8. Process each match with odds
    console.print("\n[cyan]Processing matches with odds...[/cyan]")
    total_bets = 0
    _new_bet_lines: list[str] = []  # accumulate for Telegram summary
    # 11.6: Track placed bets per bot per league for exposure management
    from collections import defaultdict
    league_bet_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # Pinnacle disagreement veto: batch-load Pinnacle implied for all match/market combos.
    # Empirical analysis (77 settled bets) shows: when cal_prob - pinnacle_implied > 0.12
    # for 1X2 home, the bet loses 22/28 times. Won bets all have gap ≤ 0.129.
    # Threshold 0.12 catches 22 of 34 losses while filtering only 6 of 40 winners.
    # PIN-3: same veto now extended to draw/away/O/U markets (threshold 0.12, tune later).
    PINNACLE_VETO_GAP = 0.12
    all_match_ids_for_signals = [
        m.get("id") for m in odds_matches if m.get("id")
    ]
    # Keys: (match_id_str, signal_name) → float value
    pinnacle_implied_by_match: dict[str, float] = {}      # home (existing)
    pinnacle_draw_by_match:  dict[str, float] = {}        # draw  (PIN-2/3)
    pinnacle_away_by_match:  dict[str, float] = {}        # away  (PIN-2/3)
    pinnacle_over_by_match:  dict[str, float] = {}        # over 2.5 (PIN-2/3)
    pinnacle_under_by_match: dict[str, float] = {}        # under 2.5 (PIN-2/3)
    sharp_consensus_by_match: dict[str, float] = {}
    if all_match_ids_for_signals:
        try:
            from workers.api_clients.db import execute_query as _eq_pin
            # Load all 5 Pinnacle implied signals in one query
            pin_all_rows = _eq_pin(
                """SELECT DISTINCT ON (match_id, signal_name) match_id, signal_name, signal_value
                   FROM match_signals
                   WHERE match_id = ANY(%s::uuid[])
                     AND signal_name IN (
                       'pinnacle_implied_home', 'pinnacle_implied_draw',
                       'pinnacle_implied_away', 'pinnacle_implied_over25',
                       'pinnacle_implied_under25'
                     )
                   ORDER BY match_id, signal_name, captured_at DESC""",
                (all_match_ids_for_signals,)
            )
            _pin_maps = {
                "pinnacle_implied_home":    pinnacle_implied_by_match,
                "pinnacle_implied_draw":    pinnacle_draw_by_match,
                "pinnacle_implied_away":    pinnacle_away_by_match,
                "pinnacle_implied_over25":  pinnacle_over_by_match,
                "pinnacle_implied_under25": pinnacle_under_by_match,
            }
            for pr in pin_all_rows:
                if pr["signal_value"] is not None:
                    target = _pin_maps.get(pr["signal_name"])
                    if target is not None:
                        target[str(pr["match_id"])] = float(pr["signal_value"])
        except Exception as e:
            console.print(f"  [yellow]Pinnacle signal load failed (non-critical): {e}[/yellow]")

        # CAL-SHARP-GATE: batch-load sharp_consensus_home for all matches.
        # Skip 1X2 home bets where sharp_consensus < -0.02 (sharps say home
        # is less likely than soft books). Diagnostic (2026-05-06) showed avg
        # sharp_consensus = -0.0034 across 31 settled home bets — gate is
        # conservative and will fire only when sharps strongly disagree.
        try:
            sc_rows = _eq_pin(
                """SELECT DISTINCT ON (match_id) match_id, signal_value
                   FROM match_signals
                   WHERE match_id = ANY(%s::uuid[])
                     AND signal_name = 'sharp_consensus_home'
                   ORDER BY match_id, captured_at DESC""",
                (all_match_ids_for_signals,)
            )
            for sr in sc_rows:
                if sr["signal_value"] is not None:
                    sharp_consensus_by_match[str(sr["match_id"])] = float(sr["signal_value"])
        except Exception as e:
            console.print(f"  [yellow]Sharp consensus signal load failed (non-critical): {e}[/yellow]")

        # DRAW-PER-LEAGUE: batch-load league_draw_pct so _poisson_probs can use
        # per-league draw inflation instead of the global 1.08 fallback.
        try:
            ldp_rows = _eq_pin(
                """SELECT DISTINCT ON (match_id) match_id, signal_value
                   FROM match_signals
                   WHERE match_id = ANY(%s::uuid[])
                     AND signal_name = 'league_draw_pct'
                   ORDER BY match_id, captured_at DESC""",
                (all_match_ids_for_signals,)
            )
            league_draw_pct_by_match: dict[str, float] = {}
            for lr in ldp_rows:
                if lr["signal_value"] is not None:
                    league_draw_pct_by_match[str(lr["match_id"])] = float(lr["signal_value"])
        except Exception as e:
            console.print(f"  [yellow]league_draw_pct signal load failed (non-critical): {e}[/yellow]")
            league_draw_pct_by_match = {}
    else:
        league_draw_pct_by_match = {}

    # BULK-STORE-PREDICTIONS: accumulate prediction rows across all matches and
    # flush in a single execute_values batch at the end of the loop. Replaces
    # ~17 serial store_prediction round-trips per match × 500 matches that used
    # to dominate run_morning wall time (~21min on EU pooler → ~1s).
    pending_pred_rows: list[dict] = []

    # BULK-STORE-MATCHES: pre-resolve match_ids for any odds_matches that don't
    # already carry one (i.e. matches not loaded from DB). One bulk call instead
    # of N serial store_match round-trips inside the loop.
    matches_needing_store_idx = [i for i, m in enumerate(odds_matches) if not m.get("id")]
    if matches_needing_store_idx:
        try:
            bulk_ids = bulk_store_matches([odds_matches[i] for i in matches_needing_store_idx])
            for i, mid in zip(matches_needing_store_idx, bulk_ids):
                if mid:
                    odds_matches[i]["id"] = mid
                    odds_matches[i]["_just_stored"] = True
        except Exception as e:
            console.print(f"  [red]Bulk store_matches failed: {e}[/red]")

    # ELITE-LEAGUE-FILTER (2026-05-25): batch-load latest league_clv_efficiency
    # per match from match_signals so each candidate-eval iteration has the
    # signal in O(1). One query per run, not per match. Env-gated filter
    # reads from match["_league_clv_efficiency"] downstream.
    league_clv_by_match: dict = {}
    if odds_matches:
        try:
            from workers.api_clients.supabase_client import execute_query as _eq
            _ids = [m["id"] for m in odds_matches if m.get("id")]
            if _ids:
                _rows = _eq(
                    """SELECT DISTINCT ON (match_id) match_id, signal_value
                       FROM match_signals
                       WHERE match_id = ANY(%s::uuid[])
                         AND signal_name = 'league_clv_efficiency'
                       ORDER BY match_id, captured_at DESC""",
                    (_ids,),
                )
                for r in _rows or []:
                    league_clv_by_match[str(r["match_id"])] = float(r["signal_value"]) if r.get("signal_value") is not None else None
        except Exception as _e:
            console.print(f"  [yellow]league_clv_efficiency batch-load failed (non-blocking): {_e}[/yellow]")

    # MULTI-STRATEGY-BOTS: expand BOTS_CONFIG into (bot_name, effective_config, alias) tuples.
    # Bots with a "strategies" list emit one tuple per strategy; each strategy overrides
    # selected top-level keys (selection_filter, league_name_filter, edge_thresholds,
    # odds_range, min_prob, alias). Single-strategy bots emit one tuple with alias="".
    # Computed once here so the match loop just iterates the pre-built list.
    _bot_strategy_iter: list[tuple[str, dict, str]] = []
    for _bn, _bc in BOTS_CONFIG.items():
        _strategies = _bc.get("strategies") or [{}]
        for _st in _strategies:
            _scfg: dict = {k: v for k, v in _bc.items() if k != "strategies"}
            _scfg.update(_st)
            _bot_strategy_iter.append((_bn, _scfg, _st.get("alias", "")))

    for match in odds_matches:
        match_id = match.get("id")
        if not match_id:
            # Bulk store either failed for this row or input was malformed.
            continue
        # ELITE-LEAGUE-FILTER: attach the per-match league CLV signal so the
        # filter check downstream reads it from the match dict directly.
        match["_league_clv_efficiency"] = league_clv_by_match.get(str(match_id))

        # Store odds — skipped when loading from DB (fetch_odds.py already stored them).
        # Original guard was `if not match.get("id")` — true only for matches not
        # loaded from DB. Bulk pre-resolve now always sets id, so use _just_stored.
        if match.get("_just_stored"):
            try:
                store_odds(match_id, {**match, "bookmaker": match.get("bookmaker", match.get("operator", "unknown"))})
            except Exception as e:
                console.print(f"  [yellow]Error storing odds: {e}[/yellow]")

        # Compute Poisson prediction
        _ldp = league_draw_pct_by_match.get(str(match_id))
        poisson_pred = compute_prediction(
            match, hist_targets,
            hist_targets_global=hist_targets_global,
            _team_sets=team_sets,
            league_draw_pct=_ldp,
        )
        # Tier C fallback: if Poisson has no historical data for this match,
        # use API-Football's own prediction probabilities (already fetched for
        # ~191/280 matches/day via the /predictions endpoint).  This ensures we
        # generate a prediction — and evaluate bets — for every match that has
        # odds, not just the subset our CSVs happen to cover.
        af_pred_for_match = af_preds.get(match_id)
        if not poisson_pred:
            if af_pred_for_match and af_pred_for_match.get("af_home_prob"):
                hp = af_pred_for_match["af_home_prob"]
                dp = af_pred_for_match["af_draw_prob"] or 0
                ap = af_pred_for_match["af_away_prob"] or 0
                total = hp + dp + ap
                if total > 0:
                    hp, dp, ap = hp / total, dp / total, ap / total

                # TIER-C-AF-XG (2026-05-19): when AF supplies its own expected-goals
                # (af_goals_home / af_goals_away — e.g. "1.7" / "1.2"), feed them
                # into _poisson_probs() instead of using the hardcoded 50/50 OU prior
                # and league-average BTTS. Same scoring grid as Tier A; same DC rho;
                # same DRAW-PER-LEAGUE inflation. Unlocks OU 1.5/2.5/3.5/4.5, BTTS,
                # and AH markets (which gate on exp_home/exp_away) for every Tier C
                # match where AF returns a goals model. Falls back to the old prior
                # path when AF gives 1X2 but no xG (rare — small leagues with
                # team-stats gaps). The +8% Tier C edge bump in DATA_TIER_EDGE_BUMP
                # is kept unchanged here — that's a separate calibration decision.
                xg_h = _parse_af_xg(af_pred_for_match.get("af_goals_home"))
                xg_a = _parse_af_xg(af_pred_for_match.get("af_goals_away"))
                league_id_str = str(match.get("league_id", ""))
                btts_rate = _league_btts_rates.get(league_id_str, _global_btts_rate)

                if xg_h is not None and xg_a is not None:
                    league_tier = int(match.get("tier") or 1)
                    tier_rho = _load_dc_rho_cache().get(league_tier)
                    poisson_pred = _poisson_probs(xg_h, xg_a, rho=tier_rho, league_draw_pct=_ldp)
                    # AF 1X2 percentages are usually close to but not identical to the
                    # Poisson grid's renormalised probs. Trust the AF percentages for
                    # the 1X2 markets (the /predictions endpoint blends form + H2H +
                    # standings — more signal than xG alone), use the Poisson grid for
                    # the goals/BTTS markets (which the AF response doesn't price).
                    poisson_pred["home_prob"] = hp
                    poisson_pred["draw_prob"] = dp
                    poisson_pred["away_prob"] = ap
                    poisson_pred["exp_home"] = xg_h
                    poisson_pred["exp_away"] = xg_a
                    poisson_pred["data_tier"] = "C"
                else:
                    # Fallback: AF gave us 1X2 but no usable xG.
                    # - 1x2: AF win probabilities (normalised)
                    # - O/U 2.5: neutral 50/50 prior (no goals model)
                    # - BTTS: league-average historical BTTS rate as prior.
                    #   Czech Republic averages 35.8% BTTS; Sweden 63.7%. This is
                    #   real signal vs the market's implied probability, even without
                    #   match-specific Poisson expected-goals data.
                    poisson_pred = {
                        "home_prob": hp,
                        "draw_prob": dp,
                        "away_prob": ap,
                        "over_25_prob": 0.50, "under_25_prob": 0.50,  # neutral prior
                        "btts_yes_prob": btts_rate,
                        "btts_no_prob": 1.0 - btts_rate,
                        "exp_home": None,
                        "exp_away": None,
                        "data_tier": "C",
                    }
            else:
                continue  # No Poisson data AND no AF prediction — truly skip

        data_tier = poisson_pred.get("data_tier", "A")

        # Try XGBoost ensemble for Tier A teams
        pred = poisson_pred  # default: Poisson-only
        xgb_pred = None
        xgb_pred_shadow = None  # Phase B shadow candidate predictions
        if data_tier == "A":
            from workers.utils.team_names import normalize_team_name
            home_norm = normalize_team_name(match["home_team"], source="default")
            away_norm = normalize_team_name(match["away_team"], source="default")
            # Try to get XGBoost prediction. `match_id` lets v10+ models read
            # directly from match_feature_vectors; team-name args are still
            # used by the v9* legacy path.
            _mid = match.get("id")
            xgb_pred = get_xgboost_prediction(
                home_norm, away_norm,
                tier=match.get("tier", 1),
                match_id=_mid,
            )
            _tier = match.get("tier", 1)
            if xgb_pred:
                pred = ensemble_prediction(poisson_pred, xgb_pred, tier=_tier)
            else:
                # Also try with raw names (legacy schema — name normalisation
                # mismatches between feature cache and DB sometimes happen).
                xgb_pred = get_xgboost_prediction(
                    match["home_team"], match["away_team"],
                    tier=_tier,
                    match_id=_mid,
                )
                if xgb_pred:
                    pred = ensemble_prediction(poisson_pred, xgb_pred, tier=_tier)

            # SHADOW-INFERENCE (Phase B, 2026-05-24): if SHADOW_MODEL_VERSION
            # env is set to a different version than production, also run
            # inference with the candidate and write its predictions with
            # the candidate's model_version. compare_models.py can then diff
            # production vs candidate on overlapping settled matches —
            # finally fixing the "0 overlap" bug. Cost: ~2x XGBoost inference
            # for Tier A matches only (a few hundred per pipeline run).
            _shadow_ver = os.environ.get("SHADOW_MODEL_VERSION", "").strip()
            from workers.model.xgboost_ensemble import MODEL_VERSION as _prod_ver
            if _shadow_ver and _shadow_ver != _prod_ver:
                # Temporarily route a clean call through _load_bundle to
                # produce candidate predictions. We call get_xgboost_prediction
                # again but with the candidate version pinned via a thread-
                # local override (simpler: just call the per-version helper).
                from workers.model.xgboost_ensemble import _load_bundle as _lb
                _shadow_bundle = _lb(_shadow_ver)
                if _shadow_bundle:
                    # Manually replicate the relevant slice of
                    # get_xgboost_prediction using the shadow bundle.
                    try:
                        _fc = _shadow_bundle["feature_cols"]
                        from workers.model.xgboost_ensemble import (
                            _is_mfv_schema as _ism, _build_row_from_mfv as _brm,
                            _build_row_from_legacy_cache as _brl,
                        )
                        if _ism(_fc) and _mid:
                            _row_s = _brm(_mid, _fc, _tier)
                        elif not _ism(_fc):
                            _row_s = _brl(home_norm, away_norm, _tier, _fc)
                        else:
                            _row_s = None
                        if _row_s is not None:
                            _X_s = pd.DataFrame([_row_s])[_fc].fillna(0)
                            _r_s = _shadow_bundle["result_1x2"]
                            _probs_s = _r_s.predict_proba(_X_s)[0]
                            _cls = list(_r_s.classes_)
                            if "H" in _cls:
                                _hp_s = _probs_s[_cls.index("H")]; _dp_s = _probs_s[_cls.index("D")]; _ap_s = _probs_s[_cls.index("A")]
                            else:
                                _hp_s = _probs_s[2] if len(_probs_s) > 2 else _probs_s[0]
                                _dp_s = _probs_s[1] if len(_probs_s) > 1 else 0.3
                                _ap_s = _probs_s[0]
                            _ou_s = _shadow_bundle["over_under"]
                            _po_s = _ou_s.predict_proba(_X_s)[0]
                            _oc = list(_ou_s.classes_)
                            _o25_s = _po_s[_oc.index(True)] if True in _oc else (_po_s[_oc.index(1)] if 1 in _oc else _po_s[-1])
                            xgb_pred_shadow = {
                                "xgb_home_prob": float(_hp_s),
                                "xgb_draw_prob": float(_dp_s),
                                "xgb_away_prob": float(_ap_s),
                                "xgb_over25_prob": float(_o25_s),
                                "_shadow_version": _shadow_ver,
                            }
                    except Exception:
                        xgb_pred_shadow = None

        # Store predictions
        data_tier = pred.get("data_tier", "A")

        # S1: Poisson predictions for all three 1x2 markets — buffered for bulk write.
        for market, prob_key, odds_field in (
            ("1x2_home", "home_prob",  "odds_home"),
            ("1x2_draw", "draw_prob",  "odds_draw"),
            ("1x2_away", "away_prob",  "odds_away"),
        ):
            odds_val = match.get(odds_field, 0)
            if odds_val > 0 and poisson_pred.get(prob_key) is not None:
                p = float(poisson_pred[prob_key])
                pending_pred_rows.append({
                    "match_id": match_id,
                    "market": market,
                    "source": "poisson",
                    "model_prob": p,
                    "implied_prob": 1 / odds_val,
                    "edge": p - (1 / odds_val),
                    "reasoning": f"data_tier={data_tier}",
                })

        # S1-XGB: XGBoost individual predictions — buffered for bulk write.
        # PER-MARKET-VERSION-TAG (2026-05-24): tag each prediction with the
        # actual version that produced it via _resolve_version(market_kind).
        # Previously rows were silently tagged with global MODEL_VERSION even
        # when per-market env overrides (MODEL_VERSION_1X2 etc.) routed
        # inference to a different bundle — masking the per-market promotion
        # in the predictions table audit.
        _v_1x2 = _resolve_version("1x2")
        _v_ou = _resolve_version("ou")
        if xgb_pred:
            for market, xgb_key, odds_field in (
                ("1x2_home", "xgb_home_prob", "odds_home"),
                ("1x2_draw", "xgb_draw_prob", "odds_draw"),
                ("1x2_away", "xgb_away_prob", "odds_away"),
            ):
                odds_val = match.get(odds_field, 0)
                if odds_val > 0 and xgb_pred.get(xgb_key) is not None:
                    p = float(xgb_pred[xgb_key])
                    pending_pred_rows.append({
                        "match_id": match_id,
                        "market": market,
                        "source": "xgboost",
                        "model_prob": p,
                        "implied_prob": 1 / odds_val,
                        "edge": p - (1 / odds_val),
                        "reasoning": f"data_tier={data_tier}",
                        "model_version": _v_1x2,
                    })

        # S1-XGB-SHADOW (Phase B, 2026-05-24): if shadow inference ran, write
        # its predictions with model_version=<shadow> so compare_models.py
        # has data to diff against production rows.
        if xgb_pred_shadow:
            _sv = xgb_pred_shadow.get("_shadow_version")
            for market, xgb_key, odds_field in (
                ("1x2_home", "xgb_home_prob", "odds_home"),
                ("1x2_draw", "xgb_draw_prob", "odds_draw"),
                ("1x2_away", "xgb_away_prob", "odds_away"),
            ):
                odds_val = match.get(odds_field, 0)
                if odds_val > 0 and xgb_pred_shadow.get(xgb_key) is not None:
                    p = float(xgb_pred_shadow[xgb_key])
                    pending_pred_rows.append({
                        "match_id": match_id,
                        "market": market,
                        "source": "xgboost",
                        "model_prob": p,
                        "implied_prob": 1 / odds_val,
                        "edge": p - (1 / odds_val),
                        "reasoning": f"data_tier={data_tier} shadow={_sv}",
                        "model_version": _sv,
                    })

        # Store ensemble predictions for every market where we have both a model
        # probability AND bookmaker odds. The prob_key must exist in the pred dict —
        # if you add a new market here, ensure ensemble_prediction() (xgboost_ensemble.py)
        # also produces the corresponding key, otherwise it will silently skip.
        for market, prob_key in [
            ("1x2_home",  "home_prob"),
            ("1x2_draw",  "draw_prob"),
            ("1x2_away",  "away_prob"),
            ("over15",    "over_15_prob"),    # Poisson-only in ensemble
            ("under15",   "under_15_prob"),   # Poisson-only in ensemble
            ("over25",    "over_25_prob"),     # blended Poisson + XGBoost
            ("under25",   "under_25_prob"),    # blended Poisson + XGBoost
            ("over35",    "over_35_prob"),    # Poisson-only in ensemble
            ("under35",   "under_35_prob"),   # Poisson-only in ensemble
            ("btts_yes",  "btts_yes_prob"),   # Poisson-only in ensemble
            ("btts_no",   "btts_no_prob"),    # Poisson-only in ensemble
        ]:
            odds_key = {
                "1x2_home": "odds_home",
                "1x2_draw": "odds_draw",
                "1x2_away": "odds_away",
                "over15":   "odds_over_15",
                "under15":  "odds_under_15",
                "over25":   "odds_over_25",
                "under25":  "odds_under_25",
                "over35":   "odds_over_35",
                "under35":  "odds_under_35",
                "btts_yes": "odds_btts_yes",
                "btts_no":  "odds_btts_no",
            }[market]

            odds_val = match.get(odds_key, 0)
            if odds_val > 0:
                prob = pred.get(prob_key)
                if prob is None:
                    # Tier C: O/U 1.5 and O/U 3.5 are still omitted (no Poisson
                    # expected-goals). BTTS is now covered via league-average rate.
                    # Only warn for Tier A/B where we'd expect the key to exist.
                    if data_tier not in ("C",):
                        console.print(f"  [yellow]Prediction missing prob key '{prob_key}' for {match_id}/{market} (tier={data_tier}) — skipping[/yellow]")
                    continue
                prob = float(prob)  # ensure plain Python float — numpy floats break psycopg2
                # PER-MARKET-VERSION-TAG: ensemble for 1X2 uses v_1x2's XGBoost
                # head; ensemble for O/U 2.5 uses v_ou's head. Other markets
                # (15/35/BTTS) are Poisson-only in ensemble and fall back to
                # the global MODEL_VERSION default.
                if market in ("1x2_home", "1x2_draw", "1x2_away"):
                    _ens_ver = _v_1x2
                elif market in ("over25", "under25"):
                    _ens_ver = _v_ou
                else:
                    _ens_ver = None  # default to _active_model_version() in writer
                _row = {
                    "match_id": match_id,
                    "market": market,
                    "source": "ensemble",
                    "model_prob": prob,
                    "implied_prob": 1 / odds_val,
                    "edge": prob - (1 / odds_val),
                    "reasoning": f"data_tier={data_tier}",
                }
                if _ens_ver:
                    _row["model_version"] = _ens_ver
                pending_pred_rows.append(_row)

        # AH predictions: store calibrated-lambda AH probabilities for CLV analysis.
        # Uses Platt-corrected 1x2 probs (pred) to invert → lambdas, fixing the
        # ~7.5% home-advantage underestimation in raw Poisson (AH-HOME-BIAS 2026-05-21).
        _cal_ph = pred.get("home_prob")
        _cal_pd = pred.get("draw_prob")
        if _cal_ph and _cal_pd and _cal_ph > 0 and _cal_pd > 0:
            _cal_lambdas = _solve_lambdas_calibrated(float(_cal_ph), float(_cal_pd))
            if _cal_lambdas:
                _exp_h_cal, _exp_a_cal = _cal_lambdas
                _tier_rho = _load_dc_rho_cache().get(match.get("tier", 1))
                for _ah_line in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5):
                    for _ah_sel in ("home", "away"):
                        _ah_prob = _ah_model_prob(_exp_h_cal, _exp_a_cal, _ah_sel, _ah_line, rho=_tier_rho)
                        pending_pred_rows.append({
                            "match_id": match_id,
                            "market": f"ah_{_ah_sel}_{_ah_line:.2f}",
                            "source": "poisson",
                            "model_prob": float(_ah_prob),
                            "implied_prob": None,
                            "edge": None,
                            "reasoning": f"data_tier={data_tier}",
                        })

        # Place bets for each bot
        tier = match.get("tier", 1)
        country = match.get("league_path", "").split(" / ")[0] if " / " in match.get("league_path", "") else ""
        league_name = match.get("league_path", "").split(" / ")[-1] if " / " in match.get("league_path", "") else ""

        # SCOTTISH-PREM-LEAGUE-GATE (2026-05-24): INFO-GAP-LEAGUE-AUDIT flagged
        # Scottish Premiership as the ONLY league with systematically sharp pre-KO
        # CLV at n>=20 (n=41, median CLV -25.7%, ROI -48.6%, hit rate 26.9% vs ~40%
        # portfolio baseline). Markets there move ~25pp against us pre-KO from lineup
        # / fitness info we can't see in time. Hard skip until we ship a confirmed-
        # lineup gate. Aggregate ex-SPL portfolio ROI = +2.6%; including SPL drags
        # to ~0%. Re-evaluate at 2026-07 (need ~6 weeks of post-fix data).
        if country == "Scotland" and league_name == "Premiership":
            continue  # skip match entirely, no bots get to evaluate it

        # ELITE-LEAGUE-FILTER (2026-05-25): generalises SCOTTISH-PREM-LEAGUE-GATE
        # into a data-driven filter using the league_clv_efficiency signal
        # (computed weekly by scripts/compute_league_clv_efficiency.py). Skips
        # matches whose league's rolling mean CLV is below the threshold.
        # Env-gated — default OFF so it doesn't contaminate Phase 3.5 verdict.
        # To activate post-2026-06-07:
        #   ELITE_LEAGUE_FILTER_ENABLED=true on Railway
        #   (optional) ELITE_LEAGUE_FILTER_THRESHOLD=-0.005 (default -0.01)
        if os.getenv("ELITE_LEAGUE_FILTER_ENABLED", "false").lower() in ("true", "1", "yes"):
            league_clv_threshold = float(os.getenv("ELITE_LEAGUE_FILTER_THRESHOLD", "-0.01"))
            league_clv = match.get("_league_clv_efficiency")
            if league_clv is not None and league_clv < league_clv_threshold:
                continue  # skip — league has sharper closing line than we beat on average

        # Data-tier adjustments (conservative stake / extra edge for lower-quality data):
        #   A — our CSV + odds history, full calibration → no bump, full stake
        #   B — global ELO CSV, results only → +2% edge req, 50% stake cap
        #   C — AF prediction only, no goals model → +8% edge req, 20% stake cap
        DATA_TIER_EDGE_BUMP = {"A": 0.00, "B": 0.02, "C": 0.08}
        edge_bump = DATA_TIER_EDGE_BUMP.get(data_tier, 0.00)
        tier_tag = f"[Tier {data_tier}] " if data_tier != "A" else ""

        # P2: Compute odds movement for this match (once per match, cached per market)
        odds_movement_cache = {}

        # T1: AF prediction for this match (already looked up for Tier D above)
        af_pred = af_pred_for_match

        for bot_name, config, _strategy_alias in _bot_strategy_iter:
            # ODDS-QUALITY-CLEANUP: skip bots flagged is_active=false or retired.
            # SHADOW-RETIRED-OK (2026-05-20): retired bots still produce shadow_bets
            # so the retirement-note recovery criterion ("≥30 bets at ≥3% ROI in
            # shadow_bets") is actually measurable. They never produce live
            # simulated_bets — only shadow rows feeding the alpha-recovery check.
            if not shadow_mode and not _bot_active.get(bot_name, True):
                continue

            # BOT-TIMING: skip bots not in the active cohort.
            # "all" = run at every cohort; dedup prevents duplicate bets.
            # SHADOW: run ALL bots — that's the whole point of the factorial design.
            bot_cohort = BOT_TIMING_COHORTS.get(bot_name, "morning")

            # ODDS-TIMING-COHORT-PREP (2026-05-25): env-driven override so the
            # 2026-06-07 cohort reassignment is a deploy-time env change instead
            # of a code edit. Format: "bot_name:cohort,bot_name:cohort" e.g.
            #   BOT_COHORT_OVERRIDES="bot_opt_away_british:morning,bot_opt_away_europe:morning"
            # When set, overrides the in-code BOT_TIMING_COHORTS for matching bots.
            _ovr = os.getenv("BOT_COHORT_OVERRIDES", "")
            if _ovr:
                for pair in _ovr.split(","):
                    pair = pair.strip()
                    if ":" in pair:
                        ovr_bot, ovr_cohort = pair.split(":", 1)
                        if ovr_bot.strip() == bot_name:
                            bot_cohort = ovr_cohort.strip()
                            break

            if not shadow_mode and cohort and bot_cohort != "all" and bot_cohort != cohort:
                continue

            # Check tier filter
            if config.get("tier_filter") and tier not in config["tier_filter"]:
                continue

            # Check league filter
            if config.get("league_filter") and country not in config.get("league_filter", []):
                continue

            # Check league_name_filter — list of (country, league_name) tuples.
            # Takes precedence over tier_filter for fine-grained league selection.
            if config.get("league_name_filter") and (country, league_name) not in config["league_name_filter"]:
                continue

            thresholds = config["edge_thresholds"].get(tier, {})
            odds_min, odds_max = config["odds_range"]
            min_prob = config["min_prob"]

            bet_candidates = []
            sel_filter = config.get("selection_filter")

            # Build candidates: (market, selection, odds, raw_prob, os_market, os_selection, threshold)
            candidate_specs = []

            # 1X2: Home
            if "1x2" in config["markets"] and match["odds_home"] > 0 and (not sel_filter or "Home" in sel_filter):
                odds = match["odds_home"]
                me = (thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08))
                candidate_specs.append(("1X2", "Home", odds, pred["home_prob"], "1x2", "home", me))

            # 1X2: Draw
            if "1x2" in config["markets"] and match["odds_draw"] > 0 and (not sel_filter or "Draw" in sel_filter):
                candidate_specs.append(("1X2", "Draw", match["odds_draw"], pred["draw_prob"], "1x2", "draw", thresholds.get("1x2_long", 0.08)))

            # 1X2: Away
            if "1x2" in config["markets"] and match["odds_away"] > 0 and (not sel_filter or "Away" in sel_filter):
                candidate_specs.append(("1X2", "Away", match["odds_away"], pred["away_prob"], "1x2", "away", thresholds.get("1x2_long", 0.08)))

            # O/U 2.5 — AGGRESSIVE-V2: sel_filter (when set) gates OU side
            if "ou" in config.get("markets", []) and match.get("odds_over_25", 0) > 0 and (not sel_filter or "Over 2.5" in sel_filter):
                candidate_specs.append(("O/U", "Over 2.5", match["odds_over_25"], pred["over_25_prob"], "over_under_25", "over", thresholds.get("ou", 0.05)))
            if "ou" in config.get("markets", []) and match.get("odds_under_25", 0) > 0 and (not sel_filter or "Under 2.5" in sel_filter):
                candidate_specs.append(("O/U", "Under 2.5", match["odds_under_25"], pred["under_25_prob"], "over_under_25", "under", thresholds.get("ou", 0.05)))

            # O/U 1.5
            if "ou15" in config.get("markets", []) and match.get("odds_over_15", 0) > 0 and (not sel_filter or "Over 1.5" in sel_filter):
                candidate_specs.append(("O/U", "Over 1.5", match["odds_over_15"], pred.get("over_15_prob", 0), "over_under_15", "over", thresholds.get("ou", 0.05)))
            if "ou15" in config.get("markets", []) and match.get("odds_under_15", 0) > 0 and (not sel_filter or "Under 1.5" in sel_filter):
                candidate_specs.append(("O/U", "Under 1.5", match["odds_under_15"], pred.get("under_15_prob", 0), "over_under_15", "under", thresholds.get("ou", 0.05)))

            # O/U 3.5
            if "ou35" in config.get("markets", []) and match.get("odds_over_35", 0) > 0 and (not sel_filter or "Over 3.5" in sel_filter):
                candidate_specs.append(("O/U", "Over 3.5", match["odds_over_35"], pred.get("over_35_prob", 0), "over_under_35", "over", thresholds.get("ou", 0.05)))
            if "ou35" in config.get("markets", []) and match.get("odds_under_35", 0) > 0 and (not sel_filter or "Under 3.5" in sel_filter):
                candidate_specs.append(("O/U", "Under 3.5", match["odds_under_35"], pred.get("under_35_prob", 0), "over_under_35", "under", thresholds.get("ou", 0.05)))

            # BTTS
            if "btts" in config.get("markets", []) and match.get("odds_btts_yes", 0) > 0 and (not sel_filter or "Yes" in sel_filter):
                candidate_specs.append(("BTTS", "Yes", match["odds_btts_yes"], pred.get("btts_yes_prob", 0), "btts", "yes", thresholds.get("btts", 0.06)))
            if "btts" in config.get("markets", []) and match.get("odds_btts_no", 0) > 0 and (not sel_filter or "No" in sel_filter):
                candidate_specs.append(("BTTS", "No", match["odds_btts_no"], pred.get("btts_no_prob", 0), "btts", "no", thresholds.get("btts", 0.06)))

            # Double Chance (DC-BOTS): probs derived from 1X2 calibrated probs
            # mkt = "double_chance" so settlement.py settle_bet_result matches correctly
            if "dc" in config.get("markets", []):
                dc_1x_prob = pred["home_prob"] + pred["draw_prob"]
                dc_x2_prob = pred["draw_prob"] + pred["away_prob"]
                dc_12_prob = pred["home_prob"] + pred["away_prob"]
                if match.get("odds_dc_1x", 0) > 0 and (not sel_filter or "1X" in sel_filter):
                    candidate_specs.append(("double_chance", "1X", match["odds_dc_1x"], dc_1x_prob, "double_chance", "1x", thresholds.get("dc", 0.04)))
                if match.get("odds_dc_x2", 0) > 0 and (not sel_filter or "X2" in sel_filter):
                    candidate_specs.append(("double_chance", "X2", match["odds_dc_x2"], dc_x2_prob, "double_chance", "x2", thresholds.get("dc", 0.04)))
                if match.get("odds_dc_12", 0) > 0 and (not sel_filter or "12" in sel_filter):
                    candidate_specs.append(("double_chance", "12", match["odds_dc_12"], dc_12_prob, "double_chance", "12", thresholds.get("dc", 0.04)))

            # AH (AH-BOTS): calibrated-lambda AH pricing (AH-HOME-BIAS fix 2026-05-21).
            # Inverts Platt-corrected pred["home_prob"/"draw_prob"] → lambdas via
            # _solve_lambdas_calibrated, fixing ~7.5% raw-Poisson home-bias.
            # mkt = "asian_handicap" so settlement correctly routes to the AH handler.
            if "ah" in config.get("markets", []):
                _cal_ph = pred.get("home_prob")
                _cal_pd = pred.get("draw_prob")
                _cal_lambdas = (
                    _solve_lambdas_calibrated(float(_cal_ph), float(_cal_pd))
                    if _cal_ph and _cal_pd and _cal_ph > 0 and _cal_pd > 0
                    else None
                )
                if _cal_lambdas:
                    _exp_h_cal, _exp_a_cal = _cal_lambdas
                    _tier_rho = _load_dc_rho_cache().get(tier)
                    _hl_min = config.get("handicap_line_min")
                    _hl_max = config.get("handicap_line_max")
                    for _ah in match.get("ah_lines", []):
                        _sel = _ah["selection"]  # "home" or "away"
                        _hl = float(_ah["handicap_line"])
                        _odds = float(_ah["odds"])
                        _sel_cap = _sel.capitalize()  # "Home" or "Away"
                        if sel_filter and _sel_cap not in sel_filter:
                            continue
                        # AH-NO-QUARTER: skip quarter lines (±.25 / ±.75).
                        # Coolbet (our real-money book) only offers full and half lines.
                        # Quarter-line paper bets can never be placed, so they distort
                        # Kelly stakes (consume running bankroll) and trigger the league
                        # exposure cap for adjacent half-line bets that ARE placeable.
                        # If we ever add a book that supports quarter lines, remove this.
                        if abs(_hl % 0.5) == 0.25:
                            continue
                        # AH-AWAY-LINE-FILTER (2026-05-24): per-bot handicap-line bounds.
                        # See bot_ah_away_dog config note. Defaults to no filter.
                        if _hl_min is not None and _hl < _hl_min:
                            continue
                        if _hl_max is not None and _hl > _hl_max:
                            continue
                        _ah_prob = _ah_model_prob(_exp_h_cal, _exp_a_cal, _sel, _hl, rho=_tier_rho)
                        _sel_label = f"{_sel_cap} {_hl:+.4g}"  # e.g. "Home -1.25"
                        candidate_specs.append((
                            "asian_handicap", _sel_label, _odds, _ah_prob,
                            "asian_handicap", _sel_label, thresholds.get("ah", 0.05)
                        ))

            # DNB (DNB-COMPUTE): derived from 1X2 odds, no separate DB storage.
            # Draw → void (stake returned). Model prob removes draw from Poisson probs.
            if "dnb" in config.get("markets", []):
                h_odds = match.get("odds_home", 0)
                a_odds = match.get("odds_away", 0)
                if h_odds and a_odds and h_odds > 0 and a_odds > 0:
                    _hp = pred.get("home_prob", 0) or 0
                    _ap = pred.get("away_prob", 0) or 0
                    _denom = _hp + _ap
                    if _denom > 0:
                        dnb_h_prob = _hp / _denom
                        dnb_a_prob = _ap / _denom
                        dnb_h_odds = (a_odds + h_odds) / a_odds
                        dnb_a_odds = (a_odds + h_odds) / h_odds
                        if not sel_filter or "Home" in sel_filter:
                            candidate_specs.append(("draw_no_bet", "Home", dnb_h_odds, dnb_h_prob, "draw_no_bet", "home", thresholds.get("dnb", 0.05)))
                        if not sel_filter or "Away" in sel_filter:
                            candidate_specs.append(("draw_no_bet", "Away", dnb_a_odds, dnb_a_prob, "draw_no_bet", "away", thresholds.get("dnb", 0.05)))

            for mkt, selection, odds, raw_mp, os_market, os_selection, base_threshold in candidate_specs:
                _funnel[bot_name]["candidates"] += 1
                ip = 1 / odds

                # Guard: skip if raw model probability is NaN
                if math.isnan(raw_mp):  # NaN guard
                    _funnel[bot_name]["drop_nan_raw"] += 1
                    continue

                # P1: Calibrate probability (tier-specific shrinkage + Platt sigmoid)
                # CAL-PIN-SHRINK: pass Pinnacle-implied as shrinkage anchor for all markets (PIN-2)
                # CAL-ALPHA-ODDS: pass odds so calibrate_prob can reduce model weight for longshots
                platt_market = f"{os_market}_{os_selection}"
                _cal_pin_map = {
                    "Home":      pinnacle_implied_by_match,
                    "Draw":      pinnacle_draw_by_match,
                    "Away":      pinnacle_away_by_match,
                    "Over 2.5":  pinnacle_over_by_match,
                    "Under 2.5": pinnacle_under_by_match,
                }
                _cal_pmap = _cal_pin_map.get(selection)
                pin_anchor = _cal_pmap.get(str(match_id)) if _cal_pmap is not None else None
                cal_prob = calibrate_prob(raw_mp, ip, tier=tier, market=platt_market,
                                          anchor_implied=pin_anchor, odds=odds)

                # Guard: skip if calibration produced NaN
                if math.isnan(cal_prob):
                    _funnel[bot_name]["drop_nan_cal"] += 1
                    continue

                # Use calibrated probability for edge calculation
                edge = cal_prob - ip
                me = base_threshold + edge_bump

                if edge < me or odds < odds_min or odds > odds_max or cal_prob < min_prob:
                    if edge < me:
                        _funnel[bot_name]["drop_edge"] += 1
                    elif odds < odds_min:
                        _funnel[bot_name]["drop_odds_too_low"] += 1
                    elif odds > odds_max:
                        _funnel[bot_name]["drop_odds_too_high"] += 1
                    else:
                        _funnel[bot_name]["drop_min_prob"] += 1
                    continue

                # Pinnacle disagreement veto: skip bets where our model is significantly
                # more optimistic than Pinnacle (the sharpest book).
                # Home: gap > 0.12 → 79% loss rate (22/28) from retrospective data.
                # PIN-3: extended to draw/away/O/U 2.5 with same threshold.
                # PIN-4: extended to all markets — BTTS/DC/AH/O/U non-2.5 lines have no
                # stored Pinnacle signal, so fall back to best-book implied (ip) as anchor.
                # AH-VETO-WIDEN (2026-05-24): AH spread markets have intrinsically wider
                # gaps between model prob and best-book ip than 1X2 (wider book vig + spread
                # mechanics), so the 0.12 1X2-tuned threshold killed every AH candidate.
                # Bot bot_ah_away_dog placed 0 bets in the 12 days since PIN-VETO-EXT
                # (2026-05-12) despite 200+ daily candidates passing the 5% edge gate.
                # Wider 0.22 gap still catches the pathological +40-60% EV outliers that
                # motivated PIN-VETO-EXT in the first place (those were 23pp+ on O/U
                # non-2.5 lines, not AH). Same widening applied to double_chance for
                # symmetry — also no Pinnacle anchor, same wide-book problem.
                _pin_veto_map = {
                    "Home":      pinnacle_implied_by_match,
                    "Draw":      pinnacle_draw_by_match,
                    "Away":      pinnacle_away_by_match,
                    "Over 2.5":  pinnacle_over_by_match,
                    "Under 2.5": pinnacle_under_by_match,
                }
                _pmap = _pin_veto_map.get(selection) if mkt in ("1X2", "O/U") else None
                _pin_implied = _pmap.get(str(match_id)) if _pmap is not None else None
                _veto_anchor = _pin_implied if _pin_implied is not None else ip
                _veto_gap = 0.22 if mkt in ("asian_handicap", "double_chance") else PINNACLE_VETO_GAP
                if (cal_prob - _veto_anchor) > _veto_gap:
                    _funnel[bot_name]["drop_pin_veto"] += 1
                    continue  # Model too far above market anchor — skip

                # CAL-SHARP-GATE: skip 1X2 home bets when sharp books collectively
                # say home is LESS likely than soft books (sharp_consensus_home < -0.02).
                # Diagnostic (2026-05-06): gate is conservative — avg sharp_consensus
                # was -0.0034 across 31 settled home bets, meaning most bets had
                # sharps roughly aligned. When sharps do disagree strongly, this fires.
                if mkt == "1X2" and selection == "Home":
                    sc = sharp_consensus_by_match.get(str(match_id))
                    if sc is not None and sc < -0.02:
                        _funnel[bot_name]["drop_sharp_gate"] += 1
                        continue  # Sharps say home is less likely — skip

                # P2: Odds movement — soft penalty, hard veto only >10%
                mv_key = f"{os_market}_{os_selection}"
                if mv_key not in odds_movement_cache:
                    odds_movement_cache[mv_key] = compute_odds_movement(
                        match_id, os_market, os_selection, odds
                    )
                odds_mv = odds_movement_cache[mv_key]

                if odds_mv["veto"]:
                    _funnel[bot_name]["drop_odds_mv"] += 1
                    continue  # Market moved >10% against pick — hard skip

                # P4: Kelly fraction (using calibrated prob)
                kelly = compute_kelly(cal_prob, odds)
                if kelly <= 0:
                    _funnel[bot_name]["drop_kelly_zero"] += 1
                    continue

                # P3: Alignment — ALN-1 active (2026-05-12)
                alignment = compute_alignment(
                    match_id, selection, odds_mv, match
                )

                # ALN-1: LOW-alignment bets require 1% extra edge.
                # HIGH/MEDIUM unchanged — sample sizes too small to lower threshold.
                # NONE is neutral (no signal ≠ bad signal).
                _ALN_BUMP = {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}
                aln_bump = _ALN_BUMP.get(alignment["alignment_class"], 0.0)

                # ENG-15 (2026-05-25): per-league market inefficiency index.
                # Continuous version of ELITE_LEAGUE_FILTER. Reads
                # match["_league_clv_efficiency"] (mean pseudo_clv over 60d
                # for the league) and bumps the edge requirement up/down:
                #   high efficiency  (≥+2%)  → -1% edge required (more bets)
                #   neutral          (-1%..+2%) → 0
                #   low/sharp        (<-1%)  → +1% edge required (fewer bets)
                # Env-gated OFF during Phase 3.5 validation. Activate via
                # LEAGUE_EFF_EDGE_BUMP_ENABLED=true post-2026-06-07.
                eff_bump = 0.0
                if os.getenv("LEAGUE_EFF_EDGE_BUMP_ENABLED", "false").lower() in ("true", "1", "yes"):
                    eff = match.get("_league_clv_efficiency")
                    if eff is not None:
                        if eff >= 0.02:
                            eff_bump = -0.01
                        elif eff <= -0.01:
                            eff_bump = 0.01

                if edge < me + aln_bump + eff_bump:
                    # Charge the funnel bucket whose bump made the difference.
                    if eff_bump > 0 and edge >= me + aln_bump:
                        _funnel[bot_name]["drop_league_eff_edge"] = _funnel[bot_name].get("drop_league_eff_edge", 0) + 1
                    else:
                        _funnel[bot_name]["drop_aln1"] += 1
                    continue

                # BOT-HIGH-ALIGNMENT (2026-05-25): per-bot minimum alignment-class
                # filter. Bots can set "min_alignment_class" in their config to
                # only fire when N+ alignment dimensions agree. Order:
                # NONE < LOW < MEDIUM < HIGH.
                _ALN_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
                _min_aln = config.get("min_alignment_class")
                if _min_aln is not None:
                    cur_rank = _ALN_RANK.get(alignment["alignment_class"], 0)
                    min_rank = _ALN_RANK.get(_min_aln, 0)
                    if cur_rank < min_rank:
                        _funnel[bot_name]["drop_min_alignment"] = _funnel[bot_name].get("drop_min_alignment", 0) + 1
                        continue

                # P4: Kelly-based stake sizing with soft odds penalty
                # Use running bankroll (reduced by stakes already placed this run)
                bot_bankroll = _running_bankroll.get(bot_name, 1000.0)

                stake = compute_stake(
                    kelly, bot_bankroll, data_tier,
                    odds_penalty=odds_mv.get("penalty", 0.0),
                )
                if stake < 1.0:
                    _funnel[bot_name]["drop_stake_low"] += 1
                    continue

                _funnel[bot_name]["accepted"] += 1
                bet_candidates.append((mkt, selection, odds, raw_mp, cal_prob, ip, edge, kelly, alignment, odds_mv, stake, os_market, os_selection))

            bet_candidates.sort(key=lambda x: x[6], reverse=True)

            for mkt, selection, odds, raw_mp, cal_prob, ip, edge, kelly, alignment, odds_mv, stake, os_market, os_selection in bet_candidates:
                # T1: AF prediction agreement
                af_agrees = _af_agrees_with_bet(selection, af_pred)

                # B-ML3-V2-ACTIVE (2026-05-25): meta-model scoring + optional
                # filtering. Score every candidate for retrospective analysis;
                # gating only happens if META_B_ML3_ENABLED=true env is set.
                # Maps selection labels to home/draw/away — meta-model expects
                # those exact selection values (one-hot encoded at training).
                meta_score: float | None = None
                try:
                    from workers.model import meta_b_ml3 as _meta
                    _meta_sel = selection.lower()
                    if "home" in _meta_sel:
                        _meta_sel = "home"
                    elif "draw" in _meta_sel:
                        _meta_sel = "draw"
                    elif "away" in _meta_sel:
                        _meta_sel = "away"
                    else:
                        _meta_sel = None  # OU/BTTS/AH selections not in v2.1 training
                    if _meta_sel is not None:
                        meta_score = _meta.score_bet(
                            match_id=str(match_id),
                            selection=_meta_sel,
                            ensemble_prob=cal_prob,
                            opening_implied=ip,
                        )
                        if not _meta.should_fire(meta_score):
                            _funnel[bot_name]["drop_meta_b_ml3"] = _funnel[bot_name].get("drop_meta_b_ml3", 0) + 1
                            continue
                except Exception:
                    # Never let meta scoring kill a placement — graceful fallthrough.
                    pass

                # 11.6: Exposure management — halve stake for 3rd+ bet in same league per bot.
                # SHADOW: skip exposure cap — shadows have fixed 10u stake, no bankroll to protect.
                _league_key = match.get("league_path", "unknown")
                _league_count = league_bet_counts[bot_name][_league_key]
                if not shadow_mode and _league_count >= 2:
                    stake = max(round(stake * 0.5, 2), 1.0)
                    console.print(f"  [dim]Exposure cap ({bot_name}): {_league_count} bets already in {_league_key} — stake halved to €{stake:.2f}[/dim]")

                # SHADOW path: accumulate row, never call store_bet, never touch bankroll.
                if shadow_mode:
                    _pending_shadow_rows.append({
                        "bot_id": bot_ids[bot_name],
                        "match_id": match_id,
                        "market": mkt,
                        "selection": selection,
                        "odds": odds,
                        "model_prob": raw_mp,
                        "calibrated_prob": round(cal_prob, 4),
                        "edge": edge,
                        "kelly_fraction": round(kelly, 6),
                        "placed_at": datetime.now().isoformat(),
                        "timing_cohort": bot_cohort,
                        "recommended_bookmaker": best_bookmaker.get(str(match_id), {}).get(f"{os_market}_{os_selection}"),
                        "meta_clv_score": round(meta_score, 4) if meta_score is not None else None,
                        "strategy_profile": _strategy_alias or None,
                    })
                    total_bets += 1
                    continue

                try:
                    bet_id = store_bet(bot_ids[bot_name], match_id, {
                        "market": mkt,
                        "selection": selection,
                        "odds": odds,
                        "model_prob": raw_mp,
                        "implied_prob": ip,
                        "edge": edge,
                        "stake": stake,
                        "placed_at": datetime.now().isoformat(),
                        "reasoning": f"{tier_tag}{f'[{_strategy_alias}] ' if _strategy_alias else ''}{match['home_team']} vs {match['away_team']} | edge={edge:.3f} cal={cal_prob:.3f} kelly={kelly:.4f} align={alignment['alignment_class']}",
                        "strategy_profile": _strategy_alias or None,
                        # P1: Calibration
                        "calibrated_prob": round(cal_prob, 4),
                        # P2: Odds movement
                        "odds_at_open": odds_mv.get("odds_at_open"),
                        "odds_drift": odds_mv.get("odds_drift"),
                        # P3: Alignment
                        "dimension_scores": alignment["dimensions"],
                        "alignment_count": alignment["alignment_count"],
                        "alignment_total": alignment["alignment_total"],
                        "alignment_class": alignment["alignment_class"],
                        # P4: Kelly
                        "kelly_fraction": round(kelly, 6),
                        # Model disagreement (when ensemble is active)
                        "model_disagreement": pred.get("model_disagreement"),
                        # T1: API-Football prediction comparison
                        "af_home_prob": af_pred.get("af_home_prob") if af_pred else None,
                        "af_draw_prob": af_pred.get("af_draw_prob") if af_pred else None,
                        "af_away_prob": af_pred.get("af_away_prob") if af_pred else None,
                        "af_agrees": af_agrees,
                        # BOT-TIMING: which time-window cohort placed this bet
                        "timing_cohort": bot_cohort,
                        # ACCESSIBLE-BM: which accessible bookmaker had the best odds
                        "recommended_bookmaker": best_bookmaker.get(str(match_id), {}).get(f"{os_market}_{os_selection}"),
                        # B-ML3-V2-ACTIVE: meta-model score for this bet (None if scoring unavailable)
                        "meta_clv_score": round(meta_score, 4) if meta_score is not None else None,
                    })
                    if bet_id:
                        total_bets += 1
                        league_bet_counts[bot_name][_league_key] += 1
                        _new_bet_lines.append(
                            f"  {match['home_team']} vs {match['away_team']} | "
                            f"{mkt} {selection} @ {odds:.2f} | "
                            f"edge {edge*100:+.1f}% | {bot_name}"
                        )
                        _running_bankroll[bot_name] = max(0.0, _running_bankroll.get(bot_name, 1000.0) - stake)
                        bm = best_bookmaker.get(str(match_id), {}).get(f"{os_market}_{os_selection}") or "—"
                        send_telegram(
                            f"🎯 <b>PRE-MATCH</b> {bot_name}\n"
                            f"  <b>{match['home_team']} vs {match['away_team']}</b>\n"
                            f"  {mkt} {selection} @ {odds:.2f}\n"
                            f"  edge {edge*100:+.1f}%  ·  align {alignment['alignment_class']}  ·  {bm}"
                        )
                        _league = match.get("league_path") or ""
                        send_telegram_to_users(
                            f"🔔 <b>New value bet</b>\n"
                            f"<b>{match['home_team']} vs {match['away_team']}</b>\n"
                            f"{mkt} {selection} @ {odds:.2f}\n"
                            f"{edge*100:+.1f}% edge"
                            + (f" · {_league}" if _league else ""),
                            tier_minimum="pro",
                            dedup_key=f"user-bet-{bet_id}",
                        )
                        # Save Stage 1 snapshot: stats-only probability
                        try:
                            store_prediction_snapshot(
                                bet_id=bet_id,
                                stage="stats_only",
                                model_probability=raw_mp,
                                implied_probability=ip,
                                edge_percent=edge,
                                odds_at_snapshot=odds,
                                metadata={
                                    "data_tier": data_tier,
                                    "bot": bot_name,
                                    "calibrated_prob": round(cal_prob, 4),
                                    "kelly": round(kelly, 4),
                                    "alignment_class": alignment["alignment_class"],
                                },
                            )
                        except Exception:
                            pass  # non-critical
                    # else: already placed today, skip silently
                except Exception as e:
                    console.print(f"  [red]Error storing bet: {e}[/red]")

        # Brief status
        ensemble_tag = " [ensemble]" if pred.get("ensemble") else ""
        disagree_tag = f" disagree={pred['model_disagreement']:.1%}" if pred.get("model_disagreement") else ""
        af_tag = ""
        if af_pred:
            hp = af_pred.get("af_home_prob", 0) or 0
            dp = af_pred.get("af_draw_prob", 0) or 0
            ap = af_pred.get("af_away_prob", 0) or 0
            af_tag = f" [AF: H{hp:.0%}/D{dp:.0%}/A{ap:.0%}]"
        console.print(f"  {match['home_team']} vs {match['away_team']} — predicted [Tier {data_tier}]{ensemble_tag}{disagree_tag}{af_tag}")

    # BULK-STORE-PREDICTIONS: flush all buffered prediction rows in one bulk
    # upsert. Replaces ~17 round-trips per match × N matches that used to
    # dominate run_morning wall time (~21min on EU pooler → ~1s).
    if pending_pred_rows:
        try:
            from workers.api_clients.supabase_client import bulk_store_predictions
            n = bulk_store_predictions(pending_pred_rows)
            console.print(f"  [dim]bulk INSERT predictions: {n} rows in 1 round-trip[/dim]")
        except Exception as e:
            import traceback
            console.print(f"  [red]bulk_store_predictions failed: {e}[/red]")
            console.print(f"  [red dim]{traceback.format_exc()}[/red dim]")

    # BET-TIMING-MONITOR: flush shadow rows to shadow_bets (no bankroll touched).
    if shadow_mode:
        if _pending_shadow_rows:
            try:
                from workers.api_clients.supabase_client import bulk_store_shadow_bets
                n = bulk_store_shadow_bets(_pending_shadow_rows, _shadow_run_id, shadow_cohort)
                console.print(
                    f"\n[bold green]SHADOW [{shadow_cohort}] — {n} rows stored "
                    f"(run_id={_shadow_run_id})[/bold green]"
                )
            except Exception as e:
                import traceback
                console.print(f"  [red]bulk_store_shadow_bets failed: {e}[/red]")
                console.print(f"  [red dim]{traceback.format_exc()}[/red dim]")
        else:
            console.print(f"\n[yellow]SHADOW [{shadow_cohort}] — no candidate bets[/yellow]")
        if verbose_funnel:
            _print_funnel(_funnel, verbose_funnel_bot)
        # Skip exposure check + ops_snapshot — shadow runs piggyback on the real run's snapshot.
        return

    cohort_label = f" [{cohort} cohort]" if cohort else " [all bots]"
    console.print(f"\n[bold green]Done! {total_bets} bets placed{cohort_label}[/bold green]")
    if verbose_funnel:
        _print_funnel(_funnel, verbose_funnel_bot)
    console.print("[green]All data stored in Supabase — frontend can display it now[/green]")

    if _new_bet_lines:
        bet_block = "\n".join(_new_bet_lines)
        send_telegram(
            f"🎯 <b>{total_bets} value bet(s) found</b>{cohort_label}\n{bet_block}",
            silent=True,  # summary is redundant — per-bet alerts already sent
        )
    else:
        send_telegram(
            f"📭 Pipeline run complete{cohort_label} — 0 new value bets",
            silent=True,
        )

    # COMBO-PHASE-D: after singles are placed, run the cross-match acca bot.
    # It reads today's pending singles, picks top-edge independent legs, and
    # places one combo bet. Only fires when ≥3 qualifying legs exist.
    # Limited to the morning cohort (or no cohort = full run) — refresh cohorts
    # don't generate enough new pending singles to materially change the menu.
    if cohort in (None, "morning"):
        try:
            from workers.jobs.acca_bot import run_acca_pass
            run_acca_pass()
        except Exception as e:
            console.print(f"[yellow]Acca bot failed (non-critical): {e}[/yellow]")

    # 11.6: Cross-match correlation check — warn about concentrated exposure
    _check_exposure_concentration()

    from workers.api_clients.supabase_client import write_ops_snapshot
    write_ops_snapshot(today_str)


def _check_exposure_concentration():
    """
    11.6: Cross-match correlation / exposure management — post-placement audit.
    Stakes are already reduced during placement (3rd+ bet per league per bot → 50% stake).
    This function logs a summary of any concentrated exposure after the fact.

    See MODEL_ANALYSIS.md Section 11.6.
    """
    from collections import defaultdict

    try:
        from workers.api_clients.db import execute_query as _eq
        today_str = date.today().isoformat()

        # Get today's pending bets with league name via JOIN
        bets = _eq(
            """SELECT sb.id, sb.bot_id, sb.stake, sb.market, sb.selection,
                      COALESCE(l.name, 'Unknown') AS league_name
               FROM simulated_bets sb
               JOIN matches m ON sb.match_id = m.id
               LEFT JOIN leagues l ON m.league_id = l.id
               WHERE sb.result = 'pending'
                 AND sb.pick_time >= %s""",
            (f"{today_str}T00:00:00",),
        )

        if not bets:
            return

        # Group by bot × league
        exposure: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

        for b in bets:
            league_name = b.get("league_name") or "Unknown"
            bot_id = b["bot_id"]
            exposure[bot_id][league_name].append(b)

        # Check for concentrated exposure
        warnings_found = False
        for bot_id, leagues in exposure.items():
            for league_name, league_bets in leagues.items():
                if len(league_bets) >= 3:
                    total_stake = sum(float(b["stake"]) for b in league_bets)
                    if not warnings_found:
                        console.print("\n[yellow]═══ Exposure Concentration Warnings ═══[/yellow]")
                        warnings_found = True
                    console.print(
                        f"  [yellow]⚠ {len(league_bets)} bets in {league_name} "
                        f"(total stake €{total_stake:.2f}) — outcomes are correlated[/yellow]"
                    )

        if not warnings_found:
            console.print("\n[dim]Exposure check: OK — no concentrated league exposure[/dim]")

    except Exception as e:
        console.print(f"  [yellow]Exposure check error (non-critical): {e}[/yellow]")


def run_settle():
    """Settle pending bets — delegates to settlement.py"""
    console.print("[yellow]Use settlement.py directly for settlement.[/yellow]")


def run_report():
    """Show cumulative bot performance"""
    from workers.api_clients.db import execute_query as _eq
    console.print("[bold green]═══ OddsIntel Bot Report ═══[/bold green]\n")

    bots = _eq("SELECT id, name, strategy, current_bankroll FROM bots ORDER BY name", [])

    t = Table(title="Bot Performance (All Time)")
    t.add_column("Bot", style="cyan")
    t.add_column("Strategy")
    t.add_column("Bankroll", justify="right")
    t.add_column("Bets", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Lost", justify="right")
    t.add_column("Pending", justify="right")

    for bot in bots:
        bets = _eq(
            "SELECT result, pnl FROM simulated_bets WHERE bot_id = %s",
            (bot["id"],),
        )
        won = sum(1 for b in bets if b["result"] == "won")
        lost = sum(1 for b in bets if b["result"] == "lost")
        pending = sum(1 for b in bets if b["result"] == "pending")

        t.add_row(
            bot["name"],
            bot.get("strategy", "")[:40],
            f"EUR {bot.get('current_bankroll', 1000):.2f}",
            str(len(bets)),
            str(won),
            str(lost),
            str(pending),
        )

    console.print(t)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "settle":
            run_settle()
        elif sys.argv[1] == "report":
            run_report()
        else:
            console.print(f"Unknown command: {sys.argv[1]}")
            console.print("Usage: python daily_pipeline_v2.py [settle|report]")
    else:
        run_morning()
