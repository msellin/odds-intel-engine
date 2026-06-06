# CLV-BACKFILL — Pinnacle historical backfill via OddsPapi

## Status: 🔄 In Progress (started 2026-06-06)

Pulling real Pinnacle closing-line data for ~177 high-value settled paper bets
to enable proper CLV (closing-line value) analysis. Free OddsPapi tier (250
credits) is enough to cover this as a one-shot extraction.

## Why

`real_bets.clv` and `simulated_bets.edge_percent` are currently computed against
AF's approximation of Pinnacle close — but AF often (a) doesn't have Pinnacle
for the match at all, or (b) stops capturing odds well before kickoff, so the
"last snapshot" we have is not the true close. CLV computed this way is noisy
and biased.

Real Pinnacle closing line is the gold-standard skill metric. Adding it for our
~200 highest-stake settled bets directly improves:
- Bot ranking by CLV (sharper signal than ROI at n≈50-100 bets per bot)
- The CHERRY-PICK-PLACER Phase 3 gate flip scheduled for 2026-06-08 — CLV gives
  us concrete promotion criteria for which bots to flip to `calibrated`
- Out-of-sample validation of the `pinnacle_drift_*` feature (migration 179)
- Detection of edge-inflation per bot (true edge vs reported edge)

## OddsPapi facts learned

- Auth: `?apiKey=<uuid>` query param
- Base: `https://api.oddspapi.io/v4`
- Cost: **1 request = 1 credit** regardless of response size
- Headers don't return remaining quota — track via dashboard
- `/sports` is free (metadata, doesn't count)
- `/odds-by-tournaments` caps at **5 tournament IDs per call**
- `/fixtures` requires at least one filter; supports `from`/`to` ISO dates +
  `tournamentId` + `statusId` (0=upcoming, 2=finished, etc.)
- `/historical-odds?fixtureId=X&bookmakers=pinnacle` returns **all timestamped
  Pinnacle snapshots** (open → close, ~60-65K rows / ~6.6 MB per fixture)
- `hasOdds=false` in /fixtures refers to **live odds availability**, NOT
  historical — historical-odds works for past settled fixtures regardless
- Cooldowns: /sports n/a, /tournaments 1s, /fixtures 2s, /odds 0.5s,
  /historical-odds **5s** (304 still counts)

## Phase plan + credit ledger

| Phase | Status | Credits | Output |
|---|---|---:|---|
| 0. Initial probe (5 calls) | ✅ | 4 used (1 was free `/sports`) | API spec confirmed |
| Verification — bulk cap test | ✅ | 1 | Cap = 5 tournaments per /odds-by-tournaments |
| Verification — past WC fixture | ✅ | 1 | WC hasn't started (group stage 2026-06-11) |
| Verification — past Brasileirão | ✅ | 2 | Historical works for settled, 65K snapshots/fixture |
| 1. Discovery (`/fixtures`) | ✅ | 27 | OP fixture metadata for 27 mapped tournaments |
| 2. Local match (no API) | ✅ | 0 | 177/183 unique matches mapped (96.7%) |
| 3. Historical backfill | 🔄 | ~177 | Raw JSONs + extracted snapshots per fixture |
| 4. Analysis + ingestion | ⬜ | 0 | CLV report + DB rows |

**Estimated total: ~213 credits used / 250 budget → ~37 reserve**

## File layout

- `/tmp/op_3_tournaments.json` — full OddsPapi soccer tournament list (1,702)
- `/tmp/op_phase1_fixtures.json` — OP fixtures per league
- `/tmp/op_phase2_mapping.json` — match_id ↔ op_fixture_id mapping
- `/tmp/op_phase3_extracted.json` — per-fixture extracted snapshots (opening,
  close, KO-24h/2h/30m/5m for each outcome)
- `dev/active/pinnacle-backfill-jsons/<fixture_id>.json.gz` — raw historical
  response per fixture (gzipped, ~600KB-1MB each, ~150 MB total, gitignored)
- `/tmp/op_phase3_failed.json` — fixtures that returned 404 or other errors
- `/tmp/op_phase3_progress.log` — running progress log

## Mapping notes (corrections to fuzzy match)

The fuzzy matcher got several leagues wrong against the OddsPapi catalog;
corrected manually before Phase 1:

| Our league | Wrong fuzzy ID | Correct OP ID |
|---|---:|---:|
| MLS | 28424 (National Premier Soccer League) | **242** |
| MLS Next Pro | 242 (MLS) | **36479** |
| 2. Bundesliga | 35 (Bundesliga) | **44** |
| Spain Segunda División | 544 (Segunda Federacion) | **54** (LaLiga 2) |
| Bulgaria First League | 47656 (Treta Liga) | **247** (Parva Liga) |
| Italy Serie C - Promotion - Play-off | 26562 (general Serie C) | **26560** |

## What's downstream

After Phase 3 completes:
1. `scripts/clv_report.py` — compute CLV per bot from extracted snapshots
2. `dev/active/clv-analysis.md` — bot rankings by CLV with confidence intervals
3. Optional: ingest extracted snapshots into `odds_snapshots` (bookmaker='Pinnacle',
   is_closing=true). User approval gate before write.
4. `dev/active/pinnacle-drift-live-validation.md` — compare CSV-derived drift
   signal (migration 179 backtest) vs live drift signal from these 200 bets
5. Bot promotion shortlist for CHERRY-PICK-PLACER gate flip on 2026-06-08
