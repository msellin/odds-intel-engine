Parent row: #140 in PRIORITY_QUEUE.md

# #140 BOT-SLICE-ANALYSIS — pre-registration (written 2026-09-24, BEFORE any slice outcome was computed)

Direction: **🤖 OWN first** (would a gate / floor / league / odds-band selection make a real-money-capable
bot worth staking?) and **👥 PICKS second** (a surviving slice would be a defensible published rule).

What had been looked at when this was written: column COVERAGE only (which rows carry which CLV,
dates, markets, books, league countries) and each bot's headline scoreboard number (already public on
/admin/bots). No slice of any bot has been scored. Script: `scripts/analysis/bot_slice_analysis.py`
(read-only). Results go to `dev/active/bot-slice-analysis-results.md`; nothing below is edited after
the run.

## 1. Question

For each bot below: is there a pre-defined subset of its settled picks (a league group, odds band,
side, market, book, time-to-kickoff, edge band or quote-freshness band) whose closing-line value is
**positive**, on older picks, after correcting for every subset tested, **and still positive on the
newer picks** it was not chosen on?

## 2. Expected result (stated up front)

**Most likely: nothing survives.** Reasons, all on record before this run:

* #128 (O/U low lines, 44-cell Holm family): 9 survivors, **all negative** (the margin, measured); the
  one bright spot was a pricing lapse that had already ended.
* The stale-window study (`docs/STALE_WINDOW_STUDY_2026_09_24.md`): the "93% positive" sharp-move
  signal was the §67 circularity; against an independent close −2.4%, and every cell was too small
  for Holm.
* ANALYSIS_GOTCHAS §47 / §52 / §62: odds-band and line-shop "edges" have repeatedly been bot, market
  or measurement effects, and line-shop +14% train became −32% test.
* Power: the bots are ~2 weeks old. Per-bet CLV sd is ~0.09 (§8). A 30-pick slice has SE ≈ 1.6 pt;
  with ~100–200 tests Holm needs roughly z ≥ 3.4–3.7, i.e. a slice mean of **about +5–6 pt CLV** on
  discovery. Only a large, real effect can pass. A null here means "no large edge in any slice", not
  "no edge".

## 3. Bots (fixed list)

**A. The 11 real-money-capable bots** (rows of `coolbet_placer_bots`, migration 413 — pre-match
`shadow_bets` bots priced at Coolbet or Unibet-Site; `src/lib/bot-controls/placement-path.ts`):
`bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1` (locked), `bot_coolbet_trigger_sharp_1x2_v1`,
`bot_coolbet_trigger_sharp_ou_v1`, `bot_ou35_model_v1`, `bot_trigger_1x2_sharp_tight_v1`,
`bot_trigger_1x2_sharp_v1`, `bot_trigger_ou_sharp_v1`, `bot_unibet_trigger_sharp_1x2_v1`,
`bot_unibet_trigger_sharp_ou_v1`, `bot_unified_gate_1x2_paper_v1`.

**B. The #140 row's other large losing bots** (not capable, run second): `bot_inplay_slowstate_v1`
(in-play, own metric), `bot_v10_1x2` (simulated_bets, model).

Bots whose designated metric has fewer than 60 settled picks in total cannot give two halves above the
minimum slice size; they are reported as "too few picks" and contribute no tests (expected: the two
Coolbet model bots, both new generators, and the sharp O/U bots).

## 4. Data (one ledger per bot, §18)

* shadow bots: `shadow_bets_unique` (first emission, §5/§9a-e), `result IN ('won','lost')`.
* `bot_v10_1x2`: `simulated_bets`, `combo_legs IS NULL`, `result IN ('won','lost')`.
* League: `matches.league_id → leagues` (never `teams.league_id`, §31).
* Executable price = `COALESCE(odds_at_pick_live, odds_at_pick)` (§30, §55).
* **Data-fault exclusion (§79):** drop a pick if its `(match_id, recommended_bookmaker)` has any row in
  `odds_snapshots_quarantined` or `data_quality_findings`. Counts reported.
* CLV outlier guard: drop |metric| > 1 (same rule as `bot_scoreboard`); counts reported.

## 5. Metrics — the designated (tested) metric per bot family

| family | bots | designated metric(s) — every one enters the Holm family | why |
|---|---|---|---|
| sharp trigger / generator | the 7 `*sharp*` bots | **clv_mc_fresh** = `clv_margin_corrected` on rows with `closing_fresh` (own-book close ≤ 60 min before KO) | as instructed. Pinnacle CLV is **circular** for these (entry = soft price beats de-vigged Pinnacle, §47/§67), so it is reported but never tested. Non-fresh rows are self-comparisons (§70, `clv = 0` by construction) and are excluded. |
| model shadow | `bot_ou35_model_v1`, `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`, `bot_unified_gate_1x2_paper_v1` | **clv_mc_fresh** (as instructed) **and clv_sharp** = `leg_clv_sharp.clv_sharp` (executable price × Shin de-vigged Pinnacle close, close ≤ 60 min old) | clv_mc_fresh covers only ~15–55% of these picks; clv_sharp covers ~98% and is not the entry rule for a model bot. Both are tested; both count toward the Holm total. |
| model sim | `bot_v10_1x2` | **clv_pin_live** = `odds_exec × p_close − 1`, `p_close = (1 + clv_pinnacle_devig) / odds_at_pick` | as instructed (Pinnacle-devig CLV for model_sim bots), re-priced at the executable price because the stored value is at the high-water price (§44). |
| in-play | `bot_inplay_slowstate_v1` | **hit_minus_p** = `1{won} − calibrated_prob` (the book's own de-vigged probability at pick; verified mean `calibrated_prob × odds` ≈ 0.96) | CLV is meaningless in-play (§14). |

Reported beside every slice, never tested: flat-stake **ROI at the executable price** (sanity check),
`clv_cons` (≥5-book consensus close, own book excluded — only the last ~7 days carry it), and
`clv_sharp` for the sharp bots (circular, labelled as such).

## 6. Discovery / holdout split

Per bot **and per designated metric**: order the picks that carry that metric by `pick_time`; the
older half (pick_time ≤ the median) is **DISCOVERY**, the newer half is **HOLDOUT**. (Splitting on the
metric-bearing rows, not on all picks, because fresh own-book closes only became common after the
2026-09-22 settlement fix — an all-picks median would leave discovery almost empty for clv_mc_fresh.)

## 7. Slice families (cells fixed now; a family is skipped for a bot where it has one cell only)

| # | family | cells |
|---|---|---|
| F1 | featured league | `leagues.priority IS NOT NULL` (48 curated leagues + major cups) / not |
| F2 | region | Europe (UEFA countries incl. England/Scotland/Wales/N. Ireland/Israel/Kazakhstan/Faroe) / international (`country = 'World'`) / rest of world |
| F3 | odds band (executable price) | < 1.80 / 1.80–2.49 / 2.50–3.49 / ≥ 3.50 |
| F4 | side | 1x2: home / draw / away; totals: over / under |
| F5 | market | 1x2 / over_under_25 / … (only multi-market bots) |
| F6 | book | `recommended_bookmaker` (only multi-book bots) |
| F7 | time to kickoff (pre-match bots) | < 3 h / 3–12 h / ≥ 12 h (`kickoff − pick_time`) |
| F7' | in-play minute (in-play bot, replaces F7) | < 30 / 30–59 / ≥ 60 |
| F8 | edge band | terciles of `calibrated_prob × odds_exec − 1` (multiplicative EV, one definition for every bot — §42/§48: derived, never read from `edge_percent`), cut points computed on the DISCOVERY half only (outcome-blind) |
| F9 | quote freshness at decision | `decision_quote_age_min` ≤ 10 / 10–60 / > 60 (only where ≥ 50% of the bot's picks carry it) |
| F9' | goals on board (in-play, replaces F9) | 0 / 1 / ≥ 2 goals at pick |

A "slice of a slice" (two-way combinations) is NOT tested — it multiplies the family and is
exactly the §47 fishing the method exists to prevent.

## 8. Test, correction and the survival rule

* Unit = **match** (picks on one match are collapsed to their mean first, so re-emitted or same-match
  legs cannot inflate n). Minimum slice size: **≥ 30 matches in discovery** and **≥ 20 matches in
  holdout**; smaller cells are listed but not tested.
* Test on discovery: one-sided t-test of H0 "slice mean ≤ 0" (Student t on match means).
* **Holm** step-down at family-wise α = 0.05 over **every** test that meets the size minimum, across all
  bots, all designated metrics and all families together (one family, no per-bot allowance).
* **A slice SURVIVES only if** (1) it passes Holm on discovery, **and** (2) on holdout its mean is > 0,
  is ≥ +1.0 pt (in the metric's own units, i.e. +1% CLV / +1 pt of hit rate) and is ≥ 50% of the
  discovery mean.
* **Near miss** (reported, never acted on): raw discovery p < 0.05 that fails Holm or fails holdout.
  Every near miss is shown with its holdout number, whatever it is.
* Also reported (descriptive, not a survival criterion): each slice's deviation from its bot's own
  average (the centred reading, §71), and each bot's overall designated metric by half.

## 9. What happens to a survivor

Nothing is changed on any bot. A survivor is written up as a **proposed new bot config** (base bot +
the one slice as an extra gate) that would enter #139's lifecycle as a NEW bot: collecting (paper) →
published → real-money, with its own fresh forward record. The old bot is never silently edited.
