Parent: [[#141]] 1X2-MODEL-REBUILD-2026-09-24 — 🔴 P0 TOP PRIORITY (owner, 2026-09-24)

# HANDOVER — #141 1X2 model rebuild (read this first; it is self-contained)

Written 2026-09-24 ~18:45 UTC when the owner switched accounts. Detail lives in three companion
docs, but everything needed to continue is here:
`dev/active/1x2-model-rebuild-plan.md` (every pre-registration and result, dated) ·
`dev/active/1x2-model-rebuild-tasks.md` (checklist) · `dev/active/1x2-model-rebuild-context.md` (notes).

---

## 1. What was achieved (2026-09-24)

| 1X2 model | log-loss on 12,640 unseen matches (2026-08-31..09-24) | status |
|---|---|---|
| Shipped XGBoost head `v20260830` (production) | 1.0711 — worse than always guessing the base rate (1.068) | live |
| Stored served blend (`predictions` source='ensemble') | 1.144 — **worse than 1/3-1/3-1/3** (its Poisson leg: 1.147) | live |
| **Rating model** `r1x2_d8plus_v1` (Elo / pi / dynamic Poisson from scores + 258k prior-season results) | ~1.005 | shadow |
| **COMBINED model** `r1x2_comb_v1` (ratings + de-vigged 18-book consensus + Pinnacle; API-Football only where no book prices the match) | **0.9763** (−8.9% vs production) | shadow |
| Pinnacle alone, on the matches it prices | 0.9812 vs combined 0.9795 (closing prices; with opening prices the gain is not significant) | — |

Honest framing: the combined model is far better than what we serve; on Pinnacle-priced matches it
*matches or slightly edges* Pinnacle because it includes Pinnacle plus 17 other books — it is not an
independent beat of Pinnacle. A betting edge still has to be proven on CLV.

Also done: weekly retrain eval fixed (it had exited without a verdict every Sunday since 09-06);
a pandas 3.0.4 segfault that would have killed the scheduler found and avoided (#145);
CI smoke suite unbroken (another commit had left `smoke_test.py` unparseable).

**Bots (both `experimental`, paper `simulated_bets`, no real-money path, visible on `/admin/bots`):**
- `bot_rating_1x2_v1` — **"1x2 market NEW"** — rating model.
- `bot_combined_1x2_v1` — **"1x2 market NEW+"** — combined model (effectively consensus-outlier picks).
Both are exact twins of `bot_v10_1x2` (same thresholds, odds 1.30–4.50, min_prob 0.30, cohort, Pinnacle
veto, meta gate, staking) except the probability source, used as is (smoke `RATING-1X2-BOT`). No picks yet
at handover — the evening window was quiet; first picks expected from the 04:00 UTC morning run.

## 2. What to do next — in this order

| # | Task | Direction | Est | Notes |
|---|---|---|---|---|
| A | **Finish the fixture-details fetch** `python3 -u scripts/fetch_fixture_details_cache.py --max-calls 25000` | 🤖👥 BOTH | ~40 min | Resumable (skips done fixtures), reserve 20k, stops 23:45 UTC, 350/min. **7,900 / 19,733 calls done at 18:42 UTC 09-24.** Cache on the owner's Mac only (`data/models/_research/1x2/fixture_details/`, gitignored). Owner OK'd using today's quota minus headroom. |
| A2 | **Round 3c lineup test** `python3 scripts/ab_1x2_lineups.py --select` → record the chosen half-life in the plan doc → `--confirm --half-life <h>` **once** | 🤖👥 BOTH | 30 min | Pre-registered (plan doc "ROUND 3c"): L1–L4, Holm m=4. |
| A3 | If 3c passes: production player strength (private table, daily `/fixtures?ids=` fetch 20/call, budget-aware backfill — owner: up to ~50k/day on quiet days, less on busy — XI features into the combiner) | 🤖👥 BOTH | ~1 d | |
| B | **Honest backtest** of NEW, NEW+ and `bot_v10_1x2` (baseline) | 👥 PICKS (early record) | ½ d | Owner wants a track record to show. Window **fixed** at 2026-08-31..09-24 (the only out-of-sample window) — **no cherry-picked periods**; opening prices, Pinnacle-close CLV, every pick; stored separately; labelled "Backtest (simulated)" beside "Live since 2026-09-25"; public display = owner decision after seeing it. |
| C | Forward check ~2026-09-27: `python3 scripts/ab_1x2_rating_arms.py --forward` | 🤖👥 BOTH | 15 min | Both model versions vs served vs Pinnacle on matches predicted before kickoff. |
| D | **OWNER DECISION (recommended yes):** serve the combined model's 1X2 in place of the `predictions` ensemble | 👥 PICKS + 🤖 OWN | ½ d | 1X2 only; refit or bypass the 1X2 Platt params in `model_calibration`; `daily_pipeline_v2.py` ~3140 / `ensemble_prediction`. |
| E | Promotion rule for the NEW bots (write BEFORE results) | 👥 PICKS | 10 min | Owner idea "Grade A". Proposed: after 150 settled picks, CLV > 0 with CI above zero AND above the grade-B consensus bot → public "Grade A (testing)". Owner sets threshold/wording. `/performance` shows only calibrated/beta bots by design. |
| F | Model ideas still open | 🤖👥 BOTH | — | Tonybet/Sportradar fair probs + Betfair exchange as consensus inputs once they have history; power/Shin de-vig; per-book accuracy weights; should NEW (rating) move to the combined model. |
| G | Close-out | — | 1 h | MODEL_WHITEPAPER / ROADMAP / SYSTEM_MAP final state, archive the four dev docs, close #141. |

Related rows: **#144** API-Football predictions + Tonybet fair probabilities evaluation (owner brief, after #141);
**#145** pandas 3.0.4 segfault.

## 3. Where everything is

**Production code:** `workers/model/ratings_1x2.py` (ratings; tuned params; leak guard) ·
`workers/model/market_consensus_1x2.py` (18-book consensus; SBO/Kambi/Avg/Max/exchange excluded; Pinnacle separate) ·
`workers/model/combined_1x2.py` (one logit per availability group P&C / P / C / none) ·
`workers/model/player_strength_1x2.py` (round 3c) · `workers/jobs/rating_1x2_shadow.py` (`run`, `--refresh`, `--dry-run`).
**Scheduler:** `job_rating_1x2_shadow` 05:30/17:30 UTC (~3.5 min, ~0.9 GB, subprocess) · `job_combined_1x2_refresh`
:10/:40 (~1 s). **Pipeline:** `daily_pipeline_v2.py` `prob_source` = `rating_1x2` / `combined_1x2` for the two bots.
**DB (migrations 412–416):** `rating_history_results` (258,222) · `rating_1x2_predictions` (`r1x2_d8plus_v1`,
`r1x2_comb_v1`, `sources`) · `combiner_1x2_params` · bots rows. All private (#072).
**Research scripts:** `scripts/ab_1x2_rating_arms.py` (rounds 1–2, `--forward`) · `ab_1x2_combined.py` (3b) ·
`ab_1x2_lineups.py` (3c) · `fetch_1x2_history_cache.py` · `fetch_fixture_details_cache.py`.
**Research cache (gitignored, owner's Mac):** `data/models/_research/1x2/` — rebuild with
`ab_1x2_rating_arms.py --refresh-cache`, `ab_1x2_combined.py --pull`; `features_hist_full.parquet` is written by
`load_all(history=True)`.
**Docs updated:** MODEL_WHITEPAPER §4.2 note, §4.4, §4.4b · WORKFLOWS · DATA_SOURCES · ROADMAP · MODEL_ANALYSIS ·
SYSTEM_MAP (both bots) · RELIABILITY_LEDGER #26.

## 4. Gotchas learned the hard way
- **pandas 3.0.4 (VPS + CI) segfaults** on any take/filter/merge over a tz-aware datetime column → use epoch seconds and
  run heavy jobs as subprocesses (a segfault kills `oddsintel-scheduler`, not just the job). #145.
- **Shared checkout and index:** other sessions keep uncommitted edits (often `scripts/smoke_test.py`) and commit at the
  same moment. Stage only your own hunks (build the file from HEAD + your edit, `git hash-object -w --stdin` +
  `git update-index --cacheinfo`), commit immediately, **never `git stash`**, re-check `git show --stat HEAD` after.
- A new bot needs: `BOTS_CONFIG` + `BOT_TIMING_COHORTS` + registry `BotSpec` + SYSTEM_MAP row + `bots` migration +
  `scripts/gen_frontend_floors.py` (commits `odds-intel-web/src/lib/generated/engine-floors.ts`).
- Old odds history keeps only the opening and the latest pre-kickoff row after 7 days — backtests price at open.
- 34% of Pinnacle's non-live 1X2 rows are stamped after kickoff — always bound by `timestamp < matches.date`.
- `/fixtures?ids=` returns 20 fixtures per call with lineups + players + statistics nested — use it for any backfill.
