Parent row: [[#154]] MODEL-INPUTS-ROUND-2-BOTH-MARKETS-2026-09-25 (PRIORITY_QUEUE.md)

# Brief — model inputs round 2 (1X2 NEW+ and combined O/U)

Self-contained brief for a session that did NOT see the 2026-09-24/25 bots session. Read this, then the
files it points to. Verify numbers against code/DB before relying on them (CLAUDE.md "verify, don't trust
doc status").

## 0. Prerequisites — what must be finished before this starts

Status as of 2026-09-25 evening (the bots session closed everything else it owned):

| Prerequisite | Why it blocks | Status |
|---|---|---|
| [[#152]] `bot_v10_ou_comb_v1` generating picks | the live O/U comparison bot for `ou_comb_v1` | ✅ resolved 2026-09-26 — 0 picks at first refresh was by design (Pinnacle-required + implied-sum gates); first picks landed from 2026-09-25 16:05 |
| [[#162]] bot refactor phase 3 (other session) | it rewrites the bot object / `BOTS_CONFIG` / the pipeline's shadow passes. RESEARCH and backtests for #154 can start any time, but SHIPPING a twin bot (config + migration + registry) must wait for #162's pipeline changes to land, or be agreed with that session first | 🔄 #162 — ask it before touching `daily_pipeline_v2.py` |
| [[#176]] served probability fresh at decision time | ✅ done 2026-09-26 (066a9847). ⚠️ **Live picks made BEFORE it are contaminated:** decisions read a ~25-min-old probability (and missed Pinnacle's first quote). Counterfactual on the rebuildable picks: VIP `bot_combined_1x2_ev5_v1` 44 of 91 would NOT have been made (38 where Pinnacle had just started pricing), `bot_v10_1x2_newplus_v1` 9 of 108, `bot_v10_ou_comb_v1` 21 of 26. Evaluate live records ONLY on picks after 066a9847 is live (or flag the earlier ones); backtests were built on pick-time data and are not affected the same way — verify. | ✅ |
| Data coverage for idea 3 | Betfair (`exchange_quotes`) and `book_fair_probs` are young (fair probs since 2026-09-23) | measure rows per market/day first; if too thin, pre-register idea 3 for later |

Related but NOT prerequisites: [[#169]] serve NEW+ as the production 1X2 (owner said yes; different project, touches the pipeline), [[#160]] Coolbet price guard, [[#153]] admin models page,
[[#166]], [[#168]] b+, the owner's choice of N for TESTING → BETA, [[#148]] VIP channel launch.

## 1. What exists today (the baseline you must beat)

| Model | Market | Code | Stored in | Holdout log-loss (08-31..09-24, 12,640 matches) |
|---|---|---|---|---|
| NEW+ `r1x2_comb_v1` | 1X2 | `workers/model/combined_1x2.py` + consensus `workers/model/market_consensus_1x2.py`; ratings `workers/model/ratings_1x2.py` | `rating_1x2_predictions` (NEW+ rows) | **0.976** (old ensemble 1.071; NEW ratings-only ~1.005) |
| combined O/U `ou_comb_v1` | O/U 1.5/2.5/3.5 | `workers/model/combined_ou.py` | `ou_model_predictions` (`p_over` = SERVED: Pinnacle where priced, else `p_comb`), params `combiner_ou_params` | 0.5655 / 0.6738 / 0.6428; **Pinnacle alone 0.5612 / 0.6731 / 0.6422** |

Both refit twice daily inside `job_rating_1x2_shadow` (05:30/17:30 UTC) and re-price every 30 min
(since #176, 2026-09-26: as the FIRST step of every betting run — `betting_pipeline.refresh_served_probabilities()`; the :10/:40 cron is gone, and rows older than 20 min are ignored) — `workers/jobs/rating_1x2_shadow.py` (`_ou_run`, `_write_ou`, `ou_refresh`).
Version names are DESIGN versions (v1 = this recipe) — a new recipe is `_v2`, never a date.

Recipe (both): one logit per AVAILABILITY GROUP (Pinnacle & consensus / Pinnacle only / consensus only /
none) over log-odds of the rating probability + de-vigged consensus (+ log n_books) + de-vigged Pinnacle.
1X2 ratings = Elo / pi / dynamic Poisson on the goal DIFFERENCE; O/U = Poisson on the SUM `dp_lh + dp_la`.

Bots that consume them (changing the model changes these — see §5):
- ⭐ VIP `bot_combined_1x2_ev5_v1` (NEW+, EV = p×odds−1 ≥ 5%, Pinnacle required, odds 1.30–6.00, one/match).
- `bot_v10_1x2_newplus_v1` (NEW+, EV ≥ 3%, odds 1.30–3.00, TESTING).
- `bot_v10_ou_comb_v1` (ou_comb_v1, EV ≥ 3%, odds 1.30–3.00, min_prob 0.30, one/match, TESTING).
- Statuses since #175: EXPERIMENTAL → TESTING → ACTIVE (50 settled, sharp CLV > 0); public Telegram = ACTIVE + TESTING at EV ≥ 5% (#174).
- O/U TWO-ANCHOR (`bot_ou_sharp_2anchor_v1`, experimental) vs VIP O/U EARLY: owner review when both reach 50 settled (reminder `ops/verify/149-ou-vip-vs-two-anchor-review.yml`); at 09-26 TWO-ANCHOR CLV +3.3% (n 36) vs EARLY −1.0% (n 11).
- ⭐ VIP `bot_ou_sharp_early_v1` uses NO model — Pinnacle power-de-vigged fair price, EV 5–15%, quote ≥ 12 h out.
  O/U model only matters for it if it beats Pinnacle (idea 1 success bar).

## 2. The ideas to test (pre-register each round BEFORE running; Holm across the family)

1. **De-vig fix (1X2):** NEW+'s consensus de-vigs PROPORTIONALLY (`market_consensus_1x2.py:12,74`) —
   ANALYSIS_GOTCHAS #78 says never. Switch to power or Shin (O/U already uses power: `combined_ou.power_devig`).
2. **Per-book accuracy weights** in the consensus instead of an equal mean, shrunk toward equal
   (see #116 SMARTER-AND-THIN-CONSENSUS for what was already tried).
3. **Independent sharp inputs:** Betfair exchange (table `exchange_quotes`, #117) and Tonybet's
   Sportradar fair probabilities (`book_fair_probs`, since 2026-09-23) — only once they have enough history;
   measure coverage first.
4. **Shots/xG-based ratings** (Wheatcroft) as the rating input — O/U on the SUM, 1X2 on the DIFFERENCE.
   ⚠️ Read #118 first: "xG is the best input, and Pinnacle already prices it" — null in every cell on the 10
   top xG leagues. Expect gain only where Pinnacle is absent (groups C / none), and say so in the pre-reg.
   Shots backfill: #081 (53,309 match_stats rows). xG arrives 1–4 days late (#111).
5. **EARLY filter on the 1X2 VIP:** quote ≥ 12 h before kickoff lifted O/U CLV from ~+2% to ~+7% (round O3).
   Test the same on NEW+ EV5 — as a twin, never in place.
6. **NEW+ vs Pinnacle as the 1X2 VIP's fair price**, head to head (CLV at sharp close, flat stakes).
7. **More history:** owner asked (2026-09-24) to retrain NEW and NEW+ on more data after the API-Football
   backfill (`dev/active/1x2-model-rebuild-plan.md` §2.5). State on 2026-09-25: finished matches per year
   2021 854 · 2022 9,955 · 2023 22,810 · 2024 34,209 · 2025 41,644 · 2026 65,142 — so history is deep from
   2022, thin before. Ratings already use prior seasons; the open question is whether the COMBINER (fit on
   recent months only, because consensus/Pinnacle coverage starts later) gains from a longer fit window.
   Check per-league depth before spending a round.

8. **Third market** (from #149's "next ideas", `dev/active/market2-model-plan.md`): per-book reliability,
   O/U 4.5, Asian handicap 0/±0.5 — same consensus-outlier recipe; only after 1–7, and pre-registered.

9. **O/U implied-sum gate** (found 2026-09-25, #152 close): the pipeline zeroes both sides of an O/U line
   when the BEST over + BEST under across books sum to < 1.02 implied — which is exactly when one book is
   off-market, i.e. the outlier we want. Today it zeroed 30 of 214 Pinnacle-priced lines with no single
   book's own pair broken. Testing each book's own pair would lift `bot_v10_ou_comb_v1` from ~2.4 to 17–33
   matches/day, 1–2/day above 15% EV (likely stale/wrong quotes). It changes EVERY O/U bot → backtest it,
   add a 15% EV cap like O/U EARLY, and ship via twins only.

**First step before any round:** the pre-registered FORWARD check due ~2026-09-27 —
`python3 scripts/ab_1x2_rating_arms.py --forward` (both 1X2 versions vs served vs Pinnacle on matches after
09-24). It is the only clean window left: 08-31..09-24 is used up by rounds 1, 2, 3b, 3c.

**Success bars:** O/U — beat Pinnacle ALONE on Pinnacle-priced rows (today 0.6738 vs 0.6731 on 2.5); only
then test it as O/U EARLY's fair price. 1X2 — beat 0.976 on the same holdout by the pre-registered margin
(round-3 bar was ≥ 0.001), AND show it in bot terms (sharp-anchor CLV of the VIP rule), because log-loss
gains that don't move CLV don't help either direction.

## 3. Known negative results — do not re-derive

- **Round 3a:** score-only small ideas all failed the ≥ 0.001 bar — score inputs are saturated.
- **Round 3c lineups:** the apparent gain sat entirely on rows WITHOUT an XI (a presence-flag artefact,
  ANALYSIS_GOTCHAS #82); after forcing no-XI rows to the no-XI prediction, all four variants failed. Dropped.
- **α vs Pinnacle close = 0** for every ratings arm (Q2) — the normal published result, not a bug.
- **Old v10 rules starve on accurate models** (NEW+ twin of the old rule: 14 picks) — EV-unit rules are
  the natural unit for a market-anchored model.
- **High-odds lane weak**; a twin with 0 backtest picks was shipped and retired the same day — always
  show pick counts before shipping a rule.
- **No Pinnacle O/U closes before mid-July** (ANALYSIS_GOTCHAS #83) — pre-July O/U "CLV vs close" is circular.
- **Own-book-close CLV is negative by construction** for outlier strategies (#85) — use sharp anchor.
- The 1X2 served model was **home/away-swapped 2026-05-10..09-14** (#065); O/U calibration bug window
  09-03 10:49 .. 09-13 21:00 UTC — exclude or flag these windows.
- CLAUDE.md "Research before you train": difference vs sum shape, feature set sized DOWN, draws not worth
  targeting, time decay differs by market (~90 d without odds as a regressor).

## 4. How to evaluate (the definitions that bit us)

- **Decide on CLV, flat stakes.** CLV = `leg_clv_sharp.clv_sharp` (fresh Pinnacle close) else `clv_cons`
  (≥ 5-book consensus); |clv| > 1 = data fault. ROI is too noisy at these n. All backtests were flat.
- **Price bases:** `odds_at_pick` = MAX high-water (inflated, §55) — never use for ROI; `odds_at_pick_live`
  = best of our 4 Estonian books (OWN); public = "available at pick time" (`bot_performance.roi_public`).
- Scripts to reuse: `scripts/ab_ou_combined.py`, `scripts/backtest_ou_comb_bots.py` (`--o2`, `--o3`),
  `scripts/backtest_1x2_new_bots.py`, `scripts/backtest_1x2_lanes.py`,
  `scripts/backtest_model_bots_new_models.py`, `scripts/b5_outlier_persistence.py`,
  `scripts/ab_1x2_lineups.py`. Outputs (gitignored) under `data/models/_research/`.
- Epoch seconds only in pandas (pandas 3.0.4, RELIABILITY_LEDGER #26).

## 5. Shipping rules (owner policy)

- **Change a LIVE bot only via a twin** (ANALYSIS_GOTCHAS #84): new model version → `*_v2` model + twin
  bot(s), EXPERIMENTAL, compare live against the current bot; owner promotes.
- **Statuses decide distribution** (migration 442): new bots default EXPERIMENTAL; TESTING = sent + own
  record; BETA/CALIBRATED = + headline; ⭐ VIP is a channel. Promotion criteria in `docs/SYSTEM_MAP.md` §Lifecycle.
- **VIP FIRST** (`workers/utils/vip_guard.py`): a free pick VIP holds or would take is held back until
  kickoff. A better model changes VIP's range → changes what free bots publish; state that in the plan.
- **Flat stakes everywhere** (€10 unit; `compute_stake`), Kelly not used.
- Update `MODEL_WHITEPAPER.md`, `docs/SYSTEM_MAP.md` + `workers/registry/bot_registry.py` together.
- **Process (CLAUDE.md, 2026-09-25):** never wait on CI/deploys/scheduled runs; one push per task; act
  only on NEW smoke failures; put post-deploy checks in `ops/verify/<task>.yml` (job `verify_queue`).

## 6. Evidence

`dev/active/1x2-model-rebuild-plan.md` (rounds 1–3c, B–B5) · `dev/active/market2-model-plan.md` (O1–O3) ·
`dev/active/model-bots-new-models-plan.md` (#152 step 3, LANES) · `dev/active/per-market-feature-sets-design.md`
· `docs/MODELLING_DATA_AUDIT_2026_09_16.md` · `dev/active/bots-session-handover-2026-09-25.md` ·
ANALYSIS_GOTCHAS #78, #82–#85 · PRIORITY_QUEUE #081 #111 #116 #117 #118.
