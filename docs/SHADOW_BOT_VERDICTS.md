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

1. **Stop staking `bot_coolbet_ou_model_v1`** — significantly negative CLV
   (t=−4.6) and the largest real-money exposure. *Owner decision, live money.*
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
