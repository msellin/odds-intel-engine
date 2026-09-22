# Can an odds floor save the O/U bot? — No. 2026-09-22

**Question (owner):** *"is it possible to backtest OU model bot configuration
against historical data? i see that the long failing period came when we only
took higher odds picks for OU... test it backwards with different odds levels...
min threshold 1.8+, 2.0+, 2.2+, 2.4+ etc up until lets say 2.8+"*

**Answer: no floor in that range rescues it, and the premise does not survive the
clean window.** Script: `scripts/ou_odds_floor_sweep.py`. Row: [[#073]].

---

## Headline

On the **157 settled picks from the clean pre-bug era** (May 8 → Sep 2), with
de-vigged Pinnacle CLV as the primary metric:

| gate | n | CLV | ROI (executable) |
|---|---|---|---|
| ALL | 157 | **−4.17% ± 1.03** | +6.7% ± 18 |
| ≥ 1.80 | 130 | −4.42% ± 1.21 | +7.8% ± 21 |
| ≥ 2.00 | 113 | −4.66% ± 1.37 | +4.0% ± 23 |
| ≥ 2.20 | 96 | −4.52% ± 1.55 | +9.3% ± 25 |
| ≥ 2.40 | 67 | −4.92% ± 2.01 | −4.7% ± 31 |
| ≥ 2.60 | 45 | −3.74% ± 2.36 | −18.6% ± 38 |
| ≥ 2.80 | 20 | −3.64% ± 3.86 | −22.6% ± 61 |

**Every floor is negative. So is every disjoint band** — −2.95, −2.83, −5.48,
−3.59, −7.33, −3.81, −3.64 from the bottom of the ladder to the top. The bot is
losing to the closing line at roughly the same rate at every price, so raising
the floor buys fewer of the same losing bets.

**Centred grid permutation (n=157, 2,000 draws): best |t| anywhere in the grid =
1.91, family-wise p = 0.344.** No price band differs from the bot's own average
by more than chance produces.

## The premise does not survive the clean window

The chart's drawdown runs Sep 3 → Sep 13, which is **exactly**
`OU-CALIBRATOR-DOMAIN-MISMATCH` (migration 335). That bug made
`edge = cal_prob − 1/odds` degenerate into "how far is this price from ~0.45",
**which is maximised by the longest price on the board** — so it manufactured the
high-odds picks:

| era | n | share at 2.8+ |
|---|---|---|
| A pre-bug | 157 | 25 (**16%**) |
| B bug window | 80 | 59 (**74%**) |
| C post-fix | 15 | 12, and **0 have CLV** |

"High-odds O/U" and "the bug window" are therefore nearly the same rows. Pooling
them would re-measure the bug and report it as an odds-band effect — gotcha §47
arriving through §39.

**And inside the clean era the ordering is the opposite of the hypothesis:** the
2.8+ band is **−3.64%**, one of the *least* bad cells, while 2.4–2.6 is **−7.33%**
and 2.0–2.2 is **−5.48%**. Long prices were not the problem; the bug was.

## ROI would have given the opposite answer, and would have been wrong

Era A reads **ROI +6.7%** against **CLV −4.17%**. Adjacent bands read **+33.3%**,
**−25.8%**, **+41.5%** — with intervals of ±40 to ±50 points. That is `§8` in one
table: ROI is still noise at this n while CLV already has a decisive answer.
Anyone reading the ROI column alone would conclude the O/U bot is profitable and
that 2.2–2.4 is a goldmine.

## One methodological correction, recorded because it nearly shipped

The first run reported **best |t| = 7.17 at family-wise p = 0.59** — nonsense on
its face. Cause: the `≥ 1.80` cell holds 130 of 157 rows, so its mean *is* the
population mean under every shuffle, and the population mean is −4.17% — a real
fact about the bot that has nothing to do with price. The grid's largest |t| was
measuring "the O/U bot has negative CLV", which was never in question.

Centring the CLV within the population before permuting tests the question a
floor decision actually asks — *is any band different from the bot's own
average* — and the answer changes from a meaningless 7.17 to 1.91, p = 0.344.

## What this cannot answer

* **Lowering** the floor below what the bot admitted. Those picks are not in the
  ledger; that needs a gate replay over all historical O/U odds (the "idealized"
  basis in `scripts/edge_floor_backtest.py`) and carries best-of-books bias.
* **Era C**, the only window where the bot's probabilities are honest: n=15 and
  zero CLV rows. Nothing is measurable there yet, and the bot has emitted nothing
  since 2026-09-13.

## What follows

The O/U problem is **not price selection**, so no floor is the fix. The bot's
model has no O/U edge to gate — consistent with `MODELLING_DATA_AUDIT_2026_09_16`
(1x2 and O/U both measured residual α = 0 on one shared feature set) and with
`bot_v10_ou`'s own −3.85% CLV over its whole history.

Recommended: **do not ship an O/U odds floor.** The open question is whether the
O/U market is worth keeping at all, which is a different and larger decision than
a threshold, and belongs to the owner.
