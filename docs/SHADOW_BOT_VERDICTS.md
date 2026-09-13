# Shadow bots — the verdict table (2026-09-13)

**Owner:** *"we have too many active bots we do nothing with… now its time to
find out the winners, bots that can in some configuration and gates and floors,
be profitable."*

Every bot on `/admin/shadow-bots`, measured on **placeable books only**
(Unibet-Kambi and Pinnacle excluded — Kambi disagrees with the real site on 91%
of quotes, and Pinnacle is both unbettable here and the reference CLV is measured
against). `best cfg` is **fold-robust**: CLV positive in *every* walk-forward
fold. A config positive overall but negative in one fold is discarded — that is
what overfitting looks like from the inside.

---

## 🔴 URGENT — we are staking real money on a bot with significantly negative CLV

| Bot | n | CLV | t | ROI | staked 30d |
|---|---|---|---|---|---|
| **`bot_coolbet_ou_model_v1`** | 32 | **−5.7%** | **−4.6** | **−43.5%** | **22 bets, €220** |
| `bot_coolbet_1x2_model_v1` | 13 | −2.8% | −1.0 | +5.2% | 6 bets, €60 |

`bot_coolbet_ou_model_v1` is the bot we stake **most**, and its CLV is negative
at **t = −4.6** — not noise. No configuration of it is fold-robust. **Recommend
stopping its stake** (flip `ui_place_enabled=false`) until it can show a
fold-robust positive slice.

Its 1x2 sibling is inconclusive at n=13 — too thin to judge either way, which is
its own problem after months of running.

---

## ✅ THE WINNERS — fold-robust positive, with the configuration that does it

| Bot | anchor | as-is CLV | **best fold-robust cfg** | cfg CLV | cfg n |
|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | sharp | +12.0% (t=+8.3) | **odds ≥ 2.2** | **+14.0%** | 37 |
| `bot_unibet_trigger_sharp_1x2_v1` | sharp | +10.7% (t=+4.4) | **odds ≥ 2.2** | **+14.0%** | 31 |
| `bot_sweep_ou25_v1` *(retired)* | line-shop | +3.3% (t=+8.0) | **edge ≥ 13%** | **+14.4%** | 37 |
| `bot_coolbet_value_v1` *(retired)* | line-shop | +3.1% (t=+7.6) | **edge ≥ 13%** | **+12.6%** | 65 |
| `bot_sweep_ou35_v1` *(retired)* | line-shop | +3.4% (t=+6.9) | **edge ≥ 13%** | +9.4% | 32 |
| `bot_pin_1x2_home_v1` *(retired)* | line-shop | +2.9% (t=+6.7) | **edge ≥ 13%** | +8.8% | 67 |

**Two configurations, and they are consistent across bots:**

* **Sharp-anchored → an ODDS floor at 2.2.** Both sharp 1x2 bots land on the same
  number independently, and both rise to the same +14.0%.
* **Line-shop → an EDGE floor at 13%.** All four line-shop bots land on 13%,
  independently, lifting CLV 3× (from ~+3% to +9-14%).

That two families each converge on one gate — rather than each bot needing its
own bespoke number — is the strongest sign in this analysis that these are real
frames and not fitted noise.

⚠️ **Every cfg n is 31-67.** These are configurations worth *running forward*,
not worth publishing or staking today. n ≥ 334 is the threshold for a CLV read
this repo trusts.

---

## ❌ THE LOSERS — no configuration works

Searched over edge floors (5/8/10/13%), odds floors (2.2/2.8/3.2) and dropping
each selection. **All returned "none fold-robust":**

| Bot | n | CLV | t | ROI |
|---|---|---|---|---|
| `bot_coolbet_trigger_1x2_v1` | 272 | −9.1% | −14.2 | −22.8% |
| `bot_coolbet_trigger_ou_v1` | 343 | −8.7% | −30.9 | −3.4% |
| `bot_unibet_trigger_1x2_v1` | 302 | −8.5% | −6.8 | −7.0% |
| `bot_trigger_1x2_model_v1` | 370 | −8.4% | −11.9 | −14.6% |
| `bot_trigger_ou_model_v1` | 136 | −8.2% | −16.5 | +3.2% |
| `bot_unibet_trigger_ou_v1` | 205 | −7.8% | −23.0 | +5.5% |
| `bot_ou35_model_v1` | 190 | −7.0% | −14.6 | −17.4% |

**Every one is MODEL-anchored.** At n=136-370 with t from −6.8 to −30.9, these
are not underpowered — they are measured, and they lose. **"No configuration
works" is a stronger and more useful statement than "it is losing"**: it means
tuning their floors is wasted effort. Recommend retiring all seven.

Note `bot_trigger_ou_model_v1` and `bot_unibet_trigger_ou_v1` show *positive* ROI
(+3.2%, +5.5%) alongside deeply negative CLV. At these volumes ROI is noise and
CLV is not; do not let the green number rescue them.

## ⚪ NO SIGNAL — bots with no CLV recorded at all

> ⚠️ **CORRECTED 2026-09-13 — this section was WRONG. See Addendum below.**
> Pinnacle quotes all three markets heavily; the CLV was NULL because
> `_market_complement_selections` had never been taught the market names. Fixed.
> `bot_corners_paper_shadow_v1` is **CLV +3.11% at t=+12.66 on n=381**.

`bot_team_total_paper_shadow_v1` (297), `bot_corners_paper_shadow_v1` (381),
`bot_1h_1x2_paper_shadow_v1` (170) have **no `clv_pinnacle` on any pick** —
Pinnacle does not quote these markets, so there is no closing reference. They can
only ever be judged on ROI, which needs ~9,300 bets. Either accept they are a
multi-year experiment, or retire them.

`bot_trigger_1x2_sharp_v1` (n=2) and `bot_trigger_ou_sharp_v1` (n=0) are the new
merged bots — **too new to have data**, and worth keeping precisely because they
are the sharp-anchored family that wins above.

---

## Recommended actions

1. ✅ **DONE 2026-09-13 — stopped staking `bot_coolbet_ou_model_v1`.**
   `ui_place_enabled=false` via migration 335. The root cause was found the same
   day and it was not the bot: OU-CALIBRATOR-DOMAIN-MISMATCH, a Platt curve fitted
   on raw ensemble probabilities and applied to Pinnacle-shrunk ones, which
   manufactured the edge on every O/U pick from 2026-09-03. Its −43.5% ROI /
   CLV −4.6 record is therefore a measurement of the calibrator, not of the bot.
   ⚠️ **Re-measure on a post-fix window before judging it** — and note the same
   contamination applies to every model-anchored **O/U** verdict in this document
   (see `docs/ANALYSIS_GOTCHAS.md`, "O/U calibration has THREE eras"). The 1x2 and
   sharp-anchored verdicts here are unaffected.
2. **Retire the seven model-anchored losers.** No configuration rescues them, so
   they are pure noise on the page.
3. **Keep and watch the sharp family** — the two live sharp 1x2 bots plus the two
   new merged ones. Apply **odds ≥ 2.2** when they reach volume.
4. **Do not un-retire the line-shop four** — `bot_coolbet_value_v1`'s strategy is
   already live as `bot_coolbet_trigger_sharp_1x2_v1`, which scores better. Their
   **edge ≥ 13%** result is the useful inheritance, not the bots themselves.
5. **Decide on the three no-CLV paper bots** — they cannot be evaluated on the
   metric this system runs on.

That would take the page from 28 bots to roughly 8 that are actually being
learned from.

---

# ADDENDUM — 2026-09-13: two corrections, and why the retirements are HELD

## Correction 1 — "Pinnacle does not quote these markets" was WRONG

The ⚪ NO SIGNAL section above said `bot_team_total_paper_shadow_v1`,
`bot_corners_paper_shadow_v1` and `bot_1h_1x2_paper_shadow_v1` "can only ever be
judged on ROI" because Pinnacle has no closing reference for them.

**Pinnacle quotes all three heavily** — 66,313 snapshots on `corners_ou_95`,
133,152 on `team_total_home_15`, 118,272 on `1x2_1h`. The picks had
`clv_pinnacle = NULL` because `_market_complement_selections`
(`workers/jobs/settlement.py`) knew only `1x2`, `btts` and `over_under*`, so
every one of these markets fell through to `return None` and the de-vig never
ran. A missing four-line mapping, not a missing market.

Fixed this commit. Computed over all 853 settled picks:

| Bot | n | CLV | t | ROI |
|---|---|---|---|---|
| **`bot_corners_paper_shadow_v1`** | 381 | **+3.11%** | **+12.66** | −3.7% |
| `bot_1h_1x2_paper_shadow_v1` | 171 | +0.97% | +1.40 | +3.8% |
| `bot_team_total_paper_shadow_v1` | 301 | +0.64% | +0.68 | −1.2% |

**The corners bot is significantly CLV-positive** and nobody could see it. It is
sharp-anchored (price vs de-vigged Pinnacle), so it is *not* affected by the
calibrator bug below. n=381 clears this repo's ~334 threshold. Its ROI is −3.7%,
which at n=381 is noise (~9,300 bets are needed for ±2%) — CLV is the read.

The other two are **indistinguishable from zero**, not negative. Judge later.

## Correction 2 — the edge floors in the table above were never applied

These three bots stored `edge_percent` **×100** (median 2.83, i.e. "283%",
against 0.127 for a normal bot), because they wrote `round(edge * 100.0, 4)`
while every other writer stores the fraction. An `edge ≥ 13%` filter means
`edge ≥ 0.13`, which retained **97%** of their picks instead of ~8%. Every
"no floor helps" statement about them was made on floors that never bound.

Fixed this commit (code + migration 334 backfill). Re-measured with real floors,
**no floor on any of the three reaches |t| ≥ 2** — so the conclusion survives.
But the *shape* changed and is worth recording: on team totals and corners, ROI
falls **monotonically** as the edge floor rises (team totals −1.2% → −8.4% at
≥3%; corners −3.7% → −6.6% at ≥2%). A claimed edge that is anti-predictive is a
different diagnosis from "no signal", and it was invisible while units were wrong.

## The seven retirements are HELD, not cancelled

Staged at `dev/active/HELD_retire_model_anchored_losers.sql`, deliberately
outside `supabase/migrations/` so the auto-apply workflow cannot run it.

A separate investigation the same day confirmed a bug in the O/U Platt
calibrator (`model_calibration` rows `under25` **and `under35`**, both fitted
2026-09-03 10:48:19 UTC): fitted on `predictions.model_probability` but applied
to `shrunk`. Verified independently — `under25` has range [0.3028, 0.6663] and a
fixed point at 0.4713, so `edge = cal_prob − 1/odds` degenerates into "how far is
this price from ~0.45", which the longest price on the board always maximises.

**All seven bots have 100% of their settled picks after that fit — zero rows
before it.** There is no clean window for any of them:

| bot | settled | before fit | after fit |
|---|---|---|---|
| `bot_coolbet_trigger_ou_v1` | 484 | 0 | 484 |
| `bot_coolbet_trigger_1x2_v1` | 405 | 0 | 405 |
| `bot_unibet_trigger_1x2_v1` | 388 | 0 | 388 |
| `bot_trigger_1x2_model_v1` | 374 | 0 | 374 |
| `bot_unibet_trigger_ou_v1` | 260 | 0 | 260 |
| `bot_ou35_model_v1` | 190 | 0 | 190 |
| `bot_trigger_ou_model_v1` | 141 | 0 | 141 |

So the four **O/U** verdicts are measuring the calibrator and the bot together
and cannot be separated on existing data. The three **1x2** verdicts stand —
`1x2_home` has been `a=1.6081, b=-0.8604` continuously since 2026-08-30 with no
step change on 09-03 — but they are held in the same file to keep one decision
in one place.

⚠️ **`bot_ou35_model_v1` is in scope even though the bug report named only the
2.5 line.** `under35` was fitted in the same batch at the same second and is
compressed the same way (range [0.4787, 0.7287], fixed point 0.6480).

**To proceed:** refit the calibrator, re-measure the four O/U bots on post-fix
data only (expect volume to fall 90–99% — that is the fix working), then move
whatever still fails into a numbered migration.
