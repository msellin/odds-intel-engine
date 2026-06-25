# OddsIntel — Open-Source Football Prediction Model

[![Track Record](https://img.shields.io/badge/Live%20Ledger-oddsintel.app%2Fapi%2Fv1%2Ftrack--record-22c55e)](https://oddsintel.app/api/v1/track-record)
[![Methodology](https://img.shields.io/badge/Methodology-oddsintel.app%2Fmethodology-blue)](https://oddsintel.app/methodology)
[![Bitcoin-anchored](https://img.shields.io/badge/Verified-OpenTimestamps-orange)](https://opentimestamps.org/)

A football prediction model that publishes every pick before kickoff to a
public, time-stamped ledger anchored on the Bitcoin blockchain via
OpenTimestamps. No paywall on the picks. No paywall on the code. Every
historical bet — including the losing ones — is in `ledger/`.

**Live site:** [oddsintel.app](https://oddsintel.app)
**Live picks Telegram channel:** [@oddsintelpicks](https://t.me/oddsintelpicks)
**Methodology:** [oddsintel.app/methodology](https://oddsintel.app/methodology)

---

## The numbers (matched 7-week window · €10 flat stake · 1X2 + O/U 2.5)

| Source | ROI | n |
|---|---|---|
| **OddsIntel (us)** | **+11.91%** | 989 |
| WinnerOdds | +6.78% | 1,007 |
| Forebet | +15.33% | 1,434 |
| SignalOdds | -0.44% | 1,157 |
| Tipstrr | -5.22% | 209 |
| DeepBetting | -9.15% | 235 |

Same window for everyone. Same flat stake. Same market scope. Each
competitor's picks pulled from their public endpoint, settled at their
own published odds. Forebet beats us — driven entirely by a single-market
OU 2.5 streak (their 1X2 alone is +0.23%). We say so on
[/methodology](https://oddsintel.app/methodology). The rest we beat
comfortably.

Auto-refreshed every Sunday via
[.github/workflows/competitor_audits_weekly.yml](.github/workflows/competitor_audits_weekly.yml).
Raw audit JSONs in [`ledger/comparison_*.json`](ledger/).

---

## Verification stack — three independent anchors

1. **Live JSON feed.** [oddsintel.app/api/v1/track-record](https://oddsintel.app/api/v1/track-record) — every settled
   bet straight from the production database. No auth, no paywall, CORS-open.
2. **Daily GitHub-signed commit.** [ledger/YYYY-MM-DD.json](ledger/) committed
   by `github-actions[bot]` at 22:45 UTC nightly. GitHub verifies the commit
   signature.
3. **Bitcoin blockchain anchor.** Each daily snapshot is hashed and stamped
   via [OpenTimestamps](https://opentimestamps.org/). The `.ots` files in
   [`ledger/`](ledger/) are independently verifiable —
   `ots verify ledger/YYYY-MM-DD.json` returns the Bitcoin block height
   the snapshot was included in.

```bash
# Verify any historical pick yourself
pip install opentimestamps-client
git clone https://github.com/msellin/odds-intel-engine
cd odds-intel-engine
ots verify ledger/2026-06-24.json
```

---

## How it works (one-paragraph version)

A Poisson goal-rate model with Dixon-Coles correction blends with an
XGBoost gradient-boosted classifier trained on form, ELO, expected
goals, lineups, injuries, and market movement signals. Outputs run
through three calibration stages (shrinkage toward Pinnacle, per-market
Platt scaling, isotonic regression). Calibrated edge ≥ threshold →
write a row to `simulated_bets` before kickoff. The whole thing is
retrained Sunday 03:00 UTC; promotion stays manual.

Full technical detail: [MODEL_WHITEPAPER.md](MODEL_WHITEPAPER.md).
Live architecture: [WORKFLOWS.md](WORKFLOWS.md).

---

## What's in this repo

| Path | What |
|---|---|
| [`workers/`](workers) | Production pipeline — scheduler, jobs, model training, calibration, settlement |
| [`scripts/`](scripts) | One-off + scheduled scripts: audits, scrapers, backfills, smoke tests |
| [`supabase/migrations/`](supabase/migrations) | Postgres schema migrations (~258 to date) |
| [`ledger/`](ledger) | Daily public track-record snapshots + OpenTimestamps proofs + competitor comparison JSONs |
| [`.github/workflows/`](.github/workflows) | GitHub Actions crons (nightly ledger, weekly competitor audits, daily ledger anchor) |
| [`MODEL_WHITEPAPER.md`](MODEL_WHITEPAPER.md) | Full model description — features, calibration, bot strategies, known limitations |
| [`WORKFLOWS.md`](WORKFLOWS.md) | Live pipeline schedule (every cron, every job) |
| [`PRIORITY_QUEUE.md`](PRIORITY_QUEUE.md) | Open work / in-progress / done — operator's source of truth |

---

## Getting it running locally

```bash
# 1. Clone
git clone https://github.com/msellin/odds-intel-engine
cd odds-intel-engine

# 2. Python 3.12+, virtual env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Environment
cp .env.example .env  # then fill in DATABASE_URL, API_FOOTBALL_KEY, etc.

# 4. Migrate
supabase db push  # if you're running your own Supabase

# 5. Verify
python3 scripts/smoke_test.py
```

Most of the pipeline expects API-Football Ultra ($29/mo) + a Supabase
Pro project. The verification stack ([`ledger/`](ledger), audit
scripts, ledger workflow) runs without any external paid service.

---

## Other open-source models in this space

Honest acknowledgement of who came before. None of them publish ledgers
quite this way, but all are worth knowing about:

- **WinnerOdds** — proprietary, closed-source, but publishes a CSV
  ledger of every bet. The standard sharp-bettor reference.
- **Trademate Sports / Betaminic** — closed-source value-betting
  platforms with internal track records.
- **soccer-analytics / dixon-coles** repos on GitHub — academic /
  reproduction-of-paper code, no live model.

OddsIntel is unusual in being both **open-source AND
cryptographically anchored AND beating most paid competitors
out-of-the-box.**

---

## Contributing

Issues and PRs welcome. Particularly:

- Bugs in the calibration pipeline
- New competitor scrapers ([`scripts/audit_vs_*.py`](scripts))
- Edge improvements (especially on OU 2.5 where Forebet currently beats us)
- Documentation gaps

No CLA, no contributor agreement — issues are issues.

---

## Disclaimer

Not financial or gambling advice. Past results are real (every row in
[`ledger/`](ledger) is verifiable) but the future is variance. Bet
responsibly. 18+ only.
