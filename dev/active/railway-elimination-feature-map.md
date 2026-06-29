# RAILWAY-ELIMINATION — Feature Map
# What moves from Railway → Hetzner, what stays on Mac

_Status as of 2026-06-29. Phase 3 complete (artifacts ready). Phase 5 (cutover) pending._

---

## Current state

| Service | Where | Cost |
|---------|-------|------|
| Pipeline scheduler (workers/scheduler.py, 100+ jobs) | Railway `pipeline` | $5/mo |
| FlareSolverr for HLTV/CS2 scraping | Railway `flaresolverr-cf` | included |
| Coolbet placement daemon | Mac (launchd) | $0 |
| FlareSolverr for Coolbet (device trust) | Mac (Docker) | $0 |
| DB migrations + manual workflow_dispatch | GitHub Actions | $0 (free tier) |

## Target state after cutover

| Service | Where | Cost |
|---------|-------|------|
| Pipeline scheduler | Hetzner VPS (systemd) | €5.49/mo |
| FlareSolverr for HLTV/CS2 scraping | Hetzner VPS (Docker) | included |
| Coolbet placement daemon | Mac (launchd) — unchanged | $0 |
| FlareSolverr for Coolbet | Mac (Docker) — unchanged | $0 |
| DB migrations + manual workflow_dispatch | GitHub Actions — unchanged | $0 |

Net change: **-$5/mo Railway** + **+€5.49/mo Hetzner** ≈ wash, but Railway dependency eliminated.

---

## What moves to Hetzner (all in workers/scheduler.py)

Nothing has moved yet — the cutover (Phase 5) is still pending. All items below
are **ready to move** (artifacts created, env vars audited).

### ⚽ Soccer pipeline (API-Football)

| Feature | Schedule |
|---------|----------|
| Fixture refresh (fixtures, leagues) | 04:00, 10:00, 16:00, 22:00 UTC |
| Odds collection (13 bookmakers) | Every 30 min 07-22 UTC |
| Odds pre-kickoff snap (13:30, 17:30, 20:00) | 3× daily |
| Closing odds snap (every 5 min 12-23) | High-frequency |
| Odds tomorrow | 04:00, 10:00, 16:00, 22:00 UTC |
| Injuries morning refresh | 08:00 UTC |
| Full enrichment (standings, H2H, team stats) | 13:00 UTC |
| Standings nightly | 23:30 UTC |
| Fixture backfill (coaches, transfers) | Every 25 min |

### 🎯 Betting / prediction

| Feature | Schedule |
|---------|----------|
| Morning betting cohort (Poisson+XGBoost) | 04:00 UTC |
| Betting refresh (9 intraday windows) | Every 30 min 05-21 UTC |
| Shadow model run (paper bots validation) | Every 30 min 07-22 UTC |
| Coolbet odds snapshot (for placement) | Every 30 min 07-22 UTC |
| Settlement (final results + ELO update) | 21:00, 23:30, 01:00 UTC |
| Settle-ready (quick settle for early finishers) | Every 15 min |
| Settle reconcile | 21:30 UTC |
| Real bets placement queue drain | Every 10 s |

### 🤖 ML model maintenance (weekly, run as subprocess)

| Feature | Schedule |
|---------|----------|
| Full model retrain (Poisson+XGBoost) | Sun 03:00 UTC |
| Meta-model B ML3 retrain | Sun 04:00 UTC |
| Meta-model validation | Sun 05:00 UTC |
| Weekly threshold/edge recalibration | Sun 05:30 UTC |
| Bot performance weekly review | Sun 06:00 UTC |
| Retrain healthcheck (verify new model exists) | Sun 07:00 UTC |

### 📊 Signal computation (nightly)

| Feature | Schedule |
|---------|----------|
| MFV v3 signals propagate | Nightly 03:00 UTC |
| MFV B-ML3 refresh | Nightly 22:30 UTC |
| MFV form-momentum refresh | Nightly 22:45 UTC |
| League CLV efficiency | Nightly 02:00 UTC |
| League draw rate | Nightly 02:30 UTC |
| League season phase | Nightly 03:30 UTC |
| Line velocity | Nightly 04:00 UTC |
| xG overperformance | Nightly 00:30 UTC |
| Injury severity | Nightly 01:00 UTC |
| Team avg player rating | Nightly 01:30 UTC |
| ALN auto-tune | Every 6h |

### 🎾 Tennis

| Feature | Schedule |
|---------|----------|
| Tennis scanner (scan ATP/WTA markets) | 06:00, 14:00 UTC |
| Tennis settlement | 02:00, 14:15 UTC |
| Tennis closing odds snap | 23:30 UTC |
| Coolbet tennis scanner (placement queue) | Every 30 min 07-22 UTC |

### 🎮 CS2 / Esports

| Feature | Schedule |
|---------|----------|
| CS2 match scanner (PandaScore) | 06:00, 10:00, 14:00, 18:00, 22:00 UTC |
| CS2 HLTV upcoming matches | Every 4h |
| CS2 HLTV match odds | Every 30 min |
| CS2 v7 predict | 5× daily |
| CS2 v8 predict | 5× daily |
| CS2 HLTV predict | 5× daily |
| CS2 CLV snapshot | Every 15 min |
| CS2 HLTV match details queue | Every 30 min |
| CS2 HLTV match details process | Every 30 min |
| CS2 HLTV player ratings | Every 4h |
| CS2 HLTV rosters | 02:00 UTC daily |
| CS2 HLTV pistol stats | Sun 03:30 UTC |
| CS2 HLTV teams bulk | Every 8h |
| CS2 HLTV top players | Every 8h |
| CS2 HLTV rankings | 05:00 UTC daily |
| CS2 weekly calibration | Sun 06:00 UTC |
| CS2 sneak peek backtest | 04:30 UTC daily |
| CS2 PandaScore matches | Every 30 min |
| CS2 PandaScore rosters | Every 30 min |
| CS2 Pinnacle scanner | Every 30 min |
| CS2 Coolbet scanner | Every 30 min 07-22 UTC |
| CS2 Coolbet placer | Every 30 min 10-23 UTC |
| CS2 bot (paper betting) | Every 30 min |
| CS2 settlement | Every h 12-02 UTC |
| CS2 settle supplementary | Every h |
| CS2 pipeline healthcheck | Every 30 min |

### 🌍 World Cup 2026 features

| Feature | Schedule |
|---------|----------|
| WC match previews | 07:30 UTC daily |
| WC market consensus | 06:00 UTC daily |
| WC Monte Carlo bracket | 06:30 UTC daily |
| WC insights | 08:00 UTC daily |
| WC lineup refresh | Every 5 min |
| WC bracket scoring | Every 30 min |
| WC bracket slot sync | Every 30 min |
| WC achievement detection | Every 4h |

### 🔔 Notifications & alerting

| Feature | Schedule |
|---------|----------|
| Coolbet daily summary (08:00 Telegram) | 08:00 UTC |
| Coolbet health ping | Every 30 min |
| Coolbet pre-kickoff alert | Every 30 min 07-22 UTC |
| Coolbet daemon healthcheck | Every 30 min |
| Pipeline failure alerter | Every 5 min |
| Pipeline failure digest | Every 2h |
| Daily real bets performance email | 23:30 UTC |
| Daily picks publish | 06:45 UTC |
| News checker (Gemini AI) | 09:00, 12:30, 16:30, 19:30 UTC |
| Match previews (AI) | 07:15 UTC |
| Health alerts morning | 09:35 UTC |
| Health alerts snapshot | Multiple daily |
| Health alerts settlement | 21:30 UTC |

### 🧰 Infrastructure / SaaS

| Feature | Schedule |
|---------|----------|
| Healthchecks.io ping | Every 5 min |
| Ops snapshot (DB metrics) | Every 30 min |
| Dashboard cache refresh | Every 30 min |
| Budget sync | Every hour |
| Stripe reconcile | 09:00 UTC |
| Prune anon users | Sun 02:00 UTC |
| Prune old live snapshots | Every hour |
| Cleanup orphaned runs | Every 30 min |
| FlareSolverr HLTV session refresh (auto-recover) | Every 6h |
| FlareSolverr sweep | Every hour |

---

## What moves to Hetzner (second Railway service)

| Service | Notes |
|---------|-------|
| FlareSolverr `flaresolverr-cf` | Local Docker on port 8191 — Hetzner docker-compose.yml ready |

---

## What stays on Mac (not on Railway at all)

| Feature | How |
|---------|-----|
| Coolbet placement daemon (CDP-Chrome, form-submit) | launchd → `com.oddsintel.coolbet-mac-daemon` |
| FlareSolverr for Coolbet (device trust, persistent profile) | Docker → `local/flaresolverr/docker-compose.yml` |
| Chrome sync (session seeding) | Manual: `local/launch_chrome_for_sync.sh` |

---

## What stays on GitHub Actions (not on Railway)

| Feature | How |
|---------|-----|
| DB migrations (supabase/migrations/) | `migrate.yml` — auto on push to main |
| Manual workflow_dispatch triggers | Various `.github/workflows/*.yml` |

---

## Summary: nothing has moved yet

All Railway pipeline features are **ready to move** (systemd unit + env vars audited)
but the cutover hasn't happened. Phase 5 is the remaining operator step:

1. Provide Hetzner SSH hostname/IP → run `bash local/setup-hetzner.sh`
2. Write `/opt/odds-intel-engine/.env` on Hetzner
3. Pause Railway `pipeline` service
4. `systemctl start oddsintel-scheduler`
5. 24h soak → cancel Railway

After that, all items in this document under "What moves to Hetzner" are live on Hetzner.
