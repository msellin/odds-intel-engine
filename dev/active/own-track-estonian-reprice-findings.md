Parent row: [[#172]] OWN-TRACK-BOT-RESEARCH-ESTONIAN-BOOKS-2026-09-26 (PRIORITY_QUEUE.md)

# #172 — Does any bot's value survive at the Estonian books we can bet?

**2026-09-26 · read-only research, nothing in production changed.**
Script: `scripts/analysis/own_track_estonian_reprice.py` (re-runnable; outputs in `data/models/_research/own172/`).

## 1. Expected result (written BEFORE the numbers were run)

* **Most bots lose most of their measured value.** Their recorded odds are the best price across many
  books (incl. AF-fed books later shown to quote phantom prices — `Unibet` AF, Kambi, Bet365 — and books
  we cannot reach: Marathonbet, Betano, Pinnacle). Opening the book set was worth +2.1–2.2% on price
  (BOOK_SET_COUNTERFACTUAL); a bot whose sharp-anchor CLV is +2–5% at its recorded price should land near
  zero or below at the best of four Estonian books.
* **Coverage will be poor for anything settled before ~2026-09-19.** Retention (ANALYSIS_GOTCHAS §59) keeps
  only open / close / latest-pre-KO rows after 7 days, and before 2026-09-11 our books had ONE surviving row
  per series (usually AFTER the pick). A "latest quote ≤ pick_time, ≤ 180 min old" price therefore mostly
  exists only for the last ~7 days, plus Tonybet only since 2026-09-21. Retired bots will mostly be
  un-scorable, not "value lost".
* **Bots that fire AT an Estonian book keep most of what they had** — the per-book sharp triggers
  (`bot_coolbet_trigger_sharp_*`, `bot_unibet_trigger_sharp_*`) and the Coolbet model bots price at their own
  book, so re-pricing should change little (the best of four ≥ the triggering book). Their independent
  judge (clv_cons, Pinnacle and own book excluded) was +2–6% in #150; expect that to hold but with CIs
  that include zero at this n.
* **Sharp generators that pick the best of ALL books** (`bot_trigger_1x2_sharp_*`, forward-test sharp arms,
  consensus arms) should lose a large share — their edge is often located at a book we cannot use.
* **The junk-anchor control should read ≈ −margin** (−4…−7%) at Estonian prices; if it does not, the
  method is broken.
* Family-wise: with ~15–25 bots tested, expect at most 1–3 to survive Holm, and those only the per-book
  sharp triggers.

## 2. Method (as run)

* **Book set, verified in code:** `ACCESSIBLE_BOOKMAKERS` (`workers/jobs/daily_pipeline_v2.py:1311`) =
  **Coolbet, Unibet-Site, Epicbet, Tonybet** (Betano removed 2026-09-23). An automated executor exists only
  for **Coolbet + Unibet-Site** (`best_price_router.PLACEABLE_BOOKS`) — reported as a second price ("exec").
* **Population:** every settled (won/lost), pre-match (`NOT is_inplay`) leg in `bot_ledger`, all sources,
  active + retired: **14,233 legs, 76 bots**. Family/anchor from `bot_registry`; retired bots' anchor from
  `bots.strategy_description` (pin_/sweep_/value/corners/team-total/1H = sharp; the rest = model).
* **Re-price:** per book, the latest `odds_snapshots` row for the same match/market/selection with
  `timestamp <= pick_time`, `is_live` not true; competes only if **≤ 180 min old** (`ODDS_FRESH_MAX_MIN`, the
  executable router), passes ANCHOR-PRICE-SANITY vs Pinnacle's latest quote, and is not an O/U-blacklisted
  source. `est_odds` = best of the four. Markets mapped 1:1 (1x2, 1x2_1h, btts, DC, DNB, O/U, team totals,
  corners); **asian_handicap (486 legs) not mapped** (line convention differs; no sharp close either).
  Sensitivity: `pick_price`'s rule (≤ 6 h lag, ≤ 48 h age) — same conclusions, not shown.
* **Scores:** `clv_est = est_odds × anchor_p_close − 1` (leg_clv_sharp, §85/§86); `clv_rec` = same at the
  recorded odds; **`clv_cons_est`** (consensus close, Pinnacle AND own book excluded) = the judge for
  sharp/consensus bots, `clv_est` for model bots (and for pre-#113 sharp bots with no consensus close —
  circular, flagged). Flat ROI at est_odds. 95% bootstrap over matches (10k, seed 172), one-sided p, **Holm
  across the 17 bots with ≥ 50 re-priced legs**. #065-flagged model legs (1X2 05-10..09-14, O/U 09-03
  10:49..09-13 21:00; 3,843 legs) are excluded from every verdict.
* Outputs: `data/models/_research/own172/{legs.csv, per_bot.csv, summary.json}`. Runtime ≈ 25 s.

## 3. Coverage — the data only answers for the last ~3 weeks

7,493 of 14,233 legs (53%) get an Estonian price. By pick week: **0% before W33 (mid-Aug)**, 6% W33–34,
26–34% W35–36, **74% W37, 86% W38, 97% W39**. Winning book: Epicbet 58%, Coolbet 27%, Unibet-Site 15%,
Tonybet 1%. Two reasons, both structural, not bot quality: our books were barely scraped before
September (Coolbet ≈ 15 rows/week in May–July), and retention (§59) keeps only open/close/latest-pre-KO
rows after 7 days, so an older pick's "quote at pick time" is usually gone. **41 of the 52 retired bots
have < 20 re-priced legs (pre-August kickoffs — bot_aggressive, high_alignment, dc_*, ah_*, btts_* — or
only #065-flagged legs): un-scorable, not "value lost".**

## 4. Results — bots with ≥ 20 re-priced legs (CLV / ROI in %, [95% CI])

`rec` = at the bot's recorded odds, `est` = best Estonian book, `exec` = Coolbet/Unibet-Site only (mean).

| bot | anchor | priced/settled | CLV rec | CLV est | clv_cons rec → est (n) | cons exec | ROI est | Holm p | verdict |
|---|---|---|---|---|---|---|---|---|---|
| `bot_trigger_1x2_sharp_tight_v1` | sharp | 288/293 | +1.9 | +0.6 [−0.5, +1.8] | +1.8 → **+1.3 [+0.1, +2.6]** (208) | −2.4 | +5.3 [−6, +17] | 0.23 | undetermined |
| `bot_coolbet_trigger_sharp_1x2_v1` | sharp | 234/276 | +8.4 | +3.5 [+1.8, +5.1]* | +3.6 → +1.6 [−1.0, +4.1] (102) | +1.7 | −3.3 [−20, +15] | 1.00 | undetermined |
| `bot_unibet_trigger_sharp_1x2_v1` | sharp | 222/264 | +6.6 | +1.8 [−0.2, +3.8]* | +5.6 → +2.6 [−0.4, +5.6] (122) | +2.4 | −6.1 [−24, +13] | 0.67 | undetermined |
| `bot_trigger_1x2_sharp_v1` | sharp | 73/73 | +6.9 | +6.5 [+2.7, +10.3]* | +2.2 → +1.9 [−2.1, +5.4] (68) | +1.0 | +5.9 [−31, +50] | 1.00 | undetermined |
| `bot_sharp_1x2_v1` (fwd test) | sharp | 73/75 | +1.9 | −0.8 [−2.1, +0.5] | +1.1 → −0.8 [−2.2, +0.5] (56) | −3.4 | +3.2 [−27, +35] | 1.00 | undetermined |
| `bot_coolbet_trigger_sharp_ou_v1` | sharp | 58/76 | +4.9 | +1.2 [−1.6, +4.2] | −0.4 → −2.2 [−7.3, +2.5] (29) | −3.5 | −29 [−55, −2] | 1.00 | undetermined |
| `bot_unibet_trigger_sharp_ou_v1` | sharp | 51/59 | +5.0 | +1.3 [−1.6, +4.3] | +5.7 → +2.7 [−0.9, +6.5] (25) | +3.2 | −25 [−48, 0] | 1.00 | undetermined |
| `bot_coolbet_value_v1` (ret.) | sharp | 220/544 | +3.3 | +1.8 [+0.4, +3.3]* | — (pre-#113) | — | −2.2 [−20, +17] | 0.13 | undetermined |
| `bot_unified_gate_1x2_paper_v1` | model | 287/287 | −7.9 | **−9.8 [−12.9, −6.6]** | −11.4 → −13.3 (241) | −15.8 | **−29.5 [−52, −5]** | 1.00 | negative |
| `bot_ou35_model_v1` (ret.) | model | 285/485 | −6.5 | −6.9 [−7.6, −6.2] | −7.5 → −7.2 (187) | −8.2 | −8.5 [−21, +5] | 1.00 | negative |
| `bot_team_total_paper_shadow_v1` (ret.) | sharp | 1116/1237 | −1.4 | −2.9 [−3.5, −2.3]* | — | — | −1.2 [−9, +6] | 1.00 | negative |
| `bot_corners_paper_shadow_v1` (ret.) | sharp | 447/1185 | +2.6 | −4.6 [−5.1, −4.1] | −1.6 → −4.8 (31) | −5.1 | −4.4 [−17, +9] | 1.00 | negative |
| `bot_1h_1x2_paper_shadow_v1` (ret.) | sharp | 396/510 | −1.4 | −3.0 [−3.7, −2.3] | −3.6 → −5.1 (202) | −9.2 | −4.2 [−18, +11] | 1.00 | negative |
| `bot_pin_1x2_home_v1` (ret.) | sharp | 162/751 | +6.1 | −3.0 [−4.4, −1.6]* | — | — | −17 [−34, +1] | 1.00 | negative |
| `bot_sweep_ou25_v1` / `_ou35_v1` (ret.) | sharp | 158/478, 141/402 | +6.0 / +5.4 | −4.1 / −4.1 (CI < 0)* | — | — | −10.9 / −2.4 | 1.00 | negative |
| `control_junk_anchor` (control) | control | 608/623 | −2.4 | −4.6 [−4.9, −4.3] | −2.2 → **−4.5 [−4.8, −4.2]** (532) | −5.4 | −6.5 [−15, +2] | — | negative (as designed) |
| `bot_consensus_c_v1` / `_d_v1` / `_b_v1` | consensus | 49 / 32 / 6 | −0.9 / −1.4 / — | −2.9 / −6.5 / — | → −2.3 / **−5.3 [−10, −1]** / — | −5.4 / −7.5 | ≈ 0 | — | too few |
| `bot_combined_1x2_ev5_v1` (VIP #1) | model | 37/41 | +1.3 | −1.4 [−4.7, +2.1] | +1.3 → −1.4 (36) | −3.3 | +23 [−13, +61] | — | too few |
| `bot_ou_sharp_2anchor_v1` / `_early_v1` (VIP #2) / `bot_trigger_ou_sharp_v1` | sharp | 37 / 11 / 18 | +3.6 / −1.0 / +8.0 | +0.1 / −4.9 / +8.5 | → −0.6 / −1.5 / +0.5 | — | −21 / −7 / −40 | — | too few |
| `bot_v10_1x2_newplus_v1`, `bot_sharp_ou_v1` | model / sharp | 28 / 28 | +1.6 / −0.2 | −1.6 / −3.8 | → −1.9 / −3.4 | — | +15 / +1 | — | too few |

\* Pinnacle-close CLV — partly circular for a Pinnacle-triggered bot (§85); not a verdict.
**Recent-window sensitivity** (picks ≥ 2026-09-19, inside full-resolution retention): same picture — the
per-book 1X2 triggers keep most of their Pinnacle-close CLV (Coolbet +6.7 → +6.1, Unibet +6.9 → +5.3) but
their independent clv_cons is +1.4% / +2.4% with CIs spanning 0; tight +1.2% [−0.2, +2.8]; no bot survives
Holm (best recent Holm p = 0.41).
Model/trigger-model bots with only #065-flagged legs (coolbet/unibet/trigger `_1x2_v1` model twins,
`bot_coolbet_ou_model_v1`, `bot_v10_ou`, most of `bot_v10_1x2` 358/396) cannot be judged at all.

## 5. Data caveat found on the way — recorded prices are not always reproducible

For picks ≥ 2026-09-19 whose recorded book is an Estonian book, the recorded price matches that book's
latest snapshot ≤ pick_time on only **36% (unified_gate 103/287), 56% (tight 91/162), 67% (trigger_1x2_sharp
49/73), 75% (Unibet trigger 73/97), 81% (Coolbet trigger 63/78)** of legs; the forward-test / consensus arms
and the control match 93–100%. In 5–22 legs per sharp bot the recorded price equals the book's **NEXT**
snapshot, written 20–80 min AFTER pick_time (e.g. tight, 2026-09-24 17:45: Coolbet home recorded 2.00; the
snapshots read 1.72 at 17:36 and 2.00 only at 18:06). Either the engine sees prices before they are
persisted, or the recorded odds carry a look-ahead. Until explained, a sharp bot's recorded-price CLV is an
upper bound, and the Estonian re-price (which only uses quotes timestamped ≤ pick_time) is the honest
figure. **Should become its own queue row** (OWN placement relies on the same price at the same instant).

## 6. Answer and recommendation (owner decides)

**Does value survive at Estonian books?** Not demonstrably. No bot survives Holm on its independent judge.
The expectation held in shape: bots priced off the whole market lose 2–10 pp of Pinnacle-close CLV moving
to our books (pin_/sweep_ turn from +5–6% to −3–4%; corners +2.6% → −4.6%); the per-book triggers keep most
of their Pinnacle-close CLV but on the independent close only +1.3–2.6%, CIs touching zero; the junk
control sits at −4.5% ≈ −margin, so the method behaves. (For legs older than 7 days part of the drop is
retention, not price: the at-pick quote may be pruned while a worse one survives — the retired bots'
negatives are an upper bound on the loss, which does not change their verdict.) **Where the best candidate's value lives matters:**
the tight bot's +1.3% is Epicbet-driven (66% of its best prices); at the two books with an executor it is
**−2.4%**. The per-book Coolbet/Unibet 1X2 triggers are the only line that is positive (+1.7% / +2.4%) at an
executable book — and still undetermined.

**So: yes, OWN needs its own bots**, built around the books we scrape and can place at: one per-book
sharp-lag trigger per executable book (Coolbet, Unibet-Site; Epicbet only if an executor is built),
using that book's own margin, line set, quote freshness and limits, pre-registered (research-first rule,
CLAUDE.md), judged on clv_cons at the executed price. Prerequisites before any real money: (1) resolve the
§5 price-timestamp question; (2) **a Unibet bet-history (account) reader — none exists** (unibet_browser_sync
has login/session only, unibet_placer only a balance), so no Unibet real money until it does; (3) enough
post-2026-09-19 legs — at the current rate (~10 legs/day per per-book trigger) n ≈ 300 on the independent
close takes ~4–6 more weeks.

| bot | recommendation | why |
|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | **paper** | best OWN candidate: positive at Coolbet on the independent close (+1.6%, CI spans 0); keep collecting |
| `bot_unibet_trigger_sharp_1x2_v1` | **paper** | +2.6% [−0.4, +5.6]; also blocked by the missing Unibet account reader |
| `bot_trigger_1x2_sharp_tight_v1` | **replace** | its edge is at Epicbet (no executor); for OWN rebuild as per-book triggers — keep it as the PICKS instrument |
| `bot_trigger_1x2_sharp_v1` | **paper** | n 73, +1.9% cons, undetermined |
| `bot_coolbet_trigger_sharp_ou_v1` / `bot_unibet_trigger_sharp_ou_v1` | **paper** | n ≈ 25 on the judge; ROI −25…−29% worth watching |
| `bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, consensus b/c/d, `control_junk_anchor` | **paper** | forward-test (PICKS) instruments; not OWN candidates (≤ 0 at our books); consensus_d −5.3% [−10, −1] at n 32 |
| `bot_combined_1x2_ev5_v1`, `bot_ou_sharp_2anchor_v1`, `bot_ou_sharp_early_v1`, `bot_trigger_ou_sharp_v1`, `bot_v10_1x2_newplus_v1`, `bot_rating_1x2_v1`, `bot_v10_ou_comb_v1`, `bot_v10_1x2`, `bot_high_roi_global_v2` | **paper** | < 50 clean re-priced legs; nothing to judge yet (VIP #1 −1.4% at our books so far) |
| `bot_coolbet_1x2_model_v1` | **paper** | 7 clean legs (13 #065-flagged) — no evidence either way; keep locked from real money |
| `bot_coolbet_ou_model_v1` | **paper** | 0 clean legs (all in the #065 O/U window); stays OFF + locked per registry |
| `bot_unified_gate_1x2_paper_v1` | **retire** | CLV −9.8% [−12.9, −6.6], clv_cons −13%, ROI −29.5% [−52, −5] at our books |
| retired: `ou35_model`, `team_total`, `corners`, `1h_1x2`, `pin_1x2_home`, `sweep_ou25/35` | **retire** (confirm) | negative at Estonian prices, CIs below 0 |
| retired: `bot_coolbet_value_v1` | **replace** | +1.8% on the (circular) Pinnacle close at 40% coverage — its idea is the Coolbet sharp trigger, which exists |
| all other retired bots (41, pre-Aug or #065-only) | **retire** (unchanged) | un-scorable: no Estonian quote at pick time survives |
