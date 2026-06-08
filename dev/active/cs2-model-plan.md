# CS2 Model — Vision, Data, and Multi-Model Plan

Status: drafting 2026-06-08. Lives here while in flux; promotes to `MODEL_WHITEPAPER.md`
once the core pieces ship.

---

## 1. Vision

Bring CS2 to the same level as soccer: a multi-model ensemble whose predictions
accumulate over time, get calibrated against actual outcomes, and retrain on a
weekly cadence — measured against Pinnacle CLV.

Current state (2026-06-08): single ELO + Player Quality logistic, scanner runs
manually, no prediction history is stored. We are about 1/4 of the way to a
production CS2 product.

## 2. The Edge Thesis

Same as soccer: bookmaker pricing softens away from the top tier.
- Tier 1 majors (IEM, BLAST, ESL Pro League) are tightly priced — minimal edge
- Tier 2-3 regional leagues (CCT, ESEA Advanced, regional Pro Leagues) — soft
- Tier 4 (open quals, secondary divisions) — softest but variance is high

Map markets (≥1 map, total maps) are softer than match winner because most
bookmakers don't run a proper BO-aware model — they extrapolate from match odds.

## 3. Data Sources

### 3.1 Free, in use

| Source | Data | Refresh | Status |
|--------|------|---------|--------|
| `cs2_all_tiers_games.csv` (Kaggle) | 9,203 historical series 2023-01 → 2026-04 | Static — needs manual replacement | Used for ELO base |
| `cs2_newestcombinedmatches.csv` (Kaggle) | 7,032 BO3 matches w/ player ratings & lineups | Static (last update Oct 2025) | Used for PQ + backtest |
| bo3.gg `/matches` | Upcoming + finished series, BO format, bookmaker odds reference | Live | Drives scanner |
| bo3.gg `/player_transfers` | Roster changes 45d lookback | Live | Roster flag — but rate-limited (intermittent 503) |
| GRID Open Access | Series fixtures (CS2 = titleId 28) | Live | Confirmed but redundant with bo3.gg; no rosters in OA |

### 3.2 Free, NOT yet in use — should integrate

| Source | What we get | Cost | Implementation |
|--------|------------|------|----------------|
| **Liquipedia API** | Current rosters per team (5-man active lineup with join dates), full transfer history per year, team metadata | Free, no auth | MediaWiki `action=parse` returns wikitext; needs parser. Rate-limit polite: 1 req/2s. |
| **HLTV.org** (no API) | Authoritative player ratings (HLTV 2.1 rating), team world rankings, demo stats | Free if scraped politely | Risky long-term — they actively block scrapers. Consider monthly snapshot only. |
| **Kaggle CS2 datasets** | Quarterly refreshed match data with player stats | Free | Manual download. Need a `scripts/esports/refresh_cs2_csv.py` reminder/cron. |

### 3.3 Paid — gaps free sources don't fill

| Source | What we'd gain | Cost | When to upgrade |
|--------|----------------|------|-----------------|
| **PandaScore** (Pro tier) | Live odds for CS2 from 10+ bookies, current rosters via API, calendar | ~€99/mo | When we want true CLV (not just bo3.gg's single bookie ref) |
| **GRID Stats Feed** | Round-by-round stats, per-map win prob, player K/D in real time | ~€500+/mo | Only if we go in-play |
| **HLTV API access** | Authoritative ratings + rankings | Custom pricing | Probably never worth it for a side product |

**Recommendation:** stay free until we prove CLV consistently > 0 over 60+ days at ≥30 bets/week. Then PandaScore Pro for proper odds shopping.

## 4. Storage Architecture (the gap)

### 4.1 What exists today

| Table | What it stores | Problem |
|-------|---------------|---------|
| `cs2_upcoming_matches` | Latest snapshot per (team1, team2, kickoff) | UPSERTS — historical predictions overwritten, lost forever once scan rewrites |
| `cs2_bets` | User-logged bets via /admin/cs2 | Fine — no change needed |

### 4.2 What we need (proposed migrations)

| Table | What it stores | Why |
|-------|---------------|-----|
| `cs2_predictions` | Every prediction we've ever made: (scan_time, match_key, win_prob1, win_prob2, fair_odds, ELO at time, PQ at time, model version) | Calibration + retraining input |
| `cs2_results` | Match outcomes (winner, score, finished_at) | Joins to predictions for accuracy |
| `cs2_clv_snapshots` | Bookmaker odds at prediction time + at kickoff time | CLV computation |

**Schema sketch:**

```sql
CREATE TABLE cs2_predictions (
  id BIGSERIAL PRIMARY KEY,
  bo3gg_id INTEGER NOT NULL,
  scan_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  kickoff_time TIMESTAMPTZ NOT NULL,
  team1 TEXT NOT NULL, team2 TEXT NOT NULL,
  best_of INTEGER, league TEXT,
  elo1 FLOAT, elo2 FLOAT,
  pq1 FLOAT, pq2 FLOAT,
  win_prob1 FLOAT, win_prob2 FLOAT,
  fair_odds1 FLOAT, fair_odds2 FLOAT,
  bookie_odds1 FLOAT, bookie_odds2 FLOAT,
  roster_change1 BOOLEAN, roster_change2 BOOLEAN,
  model_version TEXT,                              -- e.g. "elo+pq_v1"
  UNIQUE (bo3gg_id, scan_time)
);

CREATE TABLE cs2_results (
  bo3gg_id INTEGER PRIMARY KEY,
  team1 TEXT, team2 TEXT,
  kickoff_time TIMESTAMPTZ,
  winner TEXT,                                     -- team1/team2/draw
  score1 INTEGER, score2 INTEGER,                  -- maps won
  finished_at TIMESTAMPTZ
);
```

## 5. The Multi-Model Plan

Parallel to soccer's XGBoost + Poisson ensemble, CS2 wants multiple priced models
voting per match. Each model outputs a win probability; ensemble takes a calibrated
blend.

### 5.1 Phase 1 — already shipped (v1)
- **ELO** (K=32, tournament tier weights, BO weight)
- **Player Quality** (avg HLTV rating of last known lineup)
- **Combined logistic** (62.2% test accuracy, +4.1pp over ELO-only)

### 5.2 Phase 2 — next signals to add (v2)
1. **Form momentum**: last-5 vs last-20 win rate, weighted by opponent strength
2. **Roster stability**: days since last roster change (proxy for chemistry)
3. **Map pool overlap**: per-team per-map historical win rate (signal post-veto is real; pre-veto small)
4. **Travel/jetlag**: LAN vs online + region transitions
5. **Stake/tier delta**: known upset bias in T1 events (~5pp)
6. **Bookmaker line movement** (when we get PandaScore): if Pinnacle steamed, follow

### 5.3 Phase 3 — second model class (v3)
- **XGBoost** trained on the prediction history (once we have ≥1,000 predictions w/ outcomes), with same features as ELO+PQ + form/roster/map features
- **Map-level Poisson** for per-map total rounds
- **Bivariate model** for in-play (P/O/M style strategies analog to soccer)

### 5.4 Phase 4 — ensemble + calibration
- Platt or isotonic calibration on (predicted_prob, actual_outcome) pairs
- Weighted ensemble: weight each sub-model by recent CLV
- Per-tournament-tier calibration (T1 vs T3 have different miscalibration patterns)

## 6. Cron Schedule

Add to `workers/scheduler.py`:

| Job | Schedule | What |
|-----|----------|------|
| `cs2_scanner` | Every 4h, 06:00 → 22:00 UTC | Run `cs2_elo_scanner.py --record` → writes `cs2_upcoming_matches` + `cs2_predictions` |
| `cs2_settlement` | Hourly 12:00 → 02:00 UTC | Fetch finished bo3.gg results, populate `cs2_results`, settle `cs2_bets`, compute CLV |
| `cs2_roster_refresh` | Daily 04:00 UTC | Refresh roster cache from Liquipedia (current 5-man lineups) |
| `cs2_weekly_calibration` | Sunday 03:00 UTC | Refit calibrator on past 90d predictions+results; print log_loss + ECE; auto-commit new model version if better |
| `cs2_quarterly_refresh` | Manual reminder | Download fresh Kaggle CS2 CSV when available (no good automation path) |

## 7. Where each piece lives

- **Scanner** — `scripts/esports/cs2_elo_scanner.py` (exists)
- **Settlement** — `scripts/esports/cs2_settlement.py` (new — analog to `workers/settlement.py`)
- **Liquipedia roster fetcher** — `scripts/esports/cs2_liquipedia_rosters.py` (new)
- **Calibration job** — `scripts/esports/cs2_calibrate.py` (new — runs weekly cron)
- **Cron registration** — `workers/scheduler.py` (extend with cs2_* jobs)
- **Frontend** — `/admin/cs2` already shows current sheet; new page `/admin/cs2/performance` could show calibration plots once we have data

## 8. Open questions / risks

- **bo3.gg rate limits**: `/player_transfers` 503s under load. Liquipedia is a more reliable roster source and should become primary.
- **Single bookmaker reference**: bo3.gg gives one bookie's odds. Without PandaScore, our "VALUE" badge is "vs this one book" not "vs market". OK as a sanity flag, not the basis for real money.
- **Player rating staleness**: HLTV CSV is Oct 2025. Liquipedia rosters are current, but mapping new players → ratings requires HLTV scraping (or accepting "rating=league avg for unknown player").
- **Data volume for retraining**: at ~40 matches/day × 4 scans/day = ~160 predictions/day. ~1,000 settled predictions takes ~25 days. So real retraining cadence is monthly, not weekly.

## 9. Execution order

1. ✅ Scanner V2 with live ELO, PQ, roster (done 2026-06-08)
2. 🔄 Migration 199: `cs2_predictions` + 200: `cs2_results` (in progress)
3. 🔄 Scanner writes to `cs2_predictions` on every scan (in progress)
4. ⬜ Settlement job + cron (next)
5. ⬜ Scanner cron in scheduler.py
6. ⬜ Liquipedia roster fetcher + daily roster refresh cron
7. ⬜ Wait for data (~25 days for 1k predictions)
8. ⬜ Calibration + multi-model retrain
9. ⬜ Promote relevant sections from this doc into `MODEL_WHITEPAPER.md` Section 11 (CS2)
