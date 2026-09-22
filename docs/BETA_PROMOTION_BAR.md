# Promoting a shadow bot to `/performance` (BETA) — the bar, and who clears it

> **CHANGED 2026-09-22 — V10-SPLIT-BY-MARKET (migration 375, [[#040]]).** Every
> `bot_v10_all` figure below is a **BLEND of two markets that measure on opposite
> sides of zero** (1x2 de-vigged CLV +2.50% n=335; O/U 2.5 −3.85% n=181). The bot
> is now `bot_v10_1x2` + `bot_v10_ou`. The numbers here remain true as of their
> date; they are no longer true of any single bot.


**Owner, 2026-09-13:** *"what I wanna do is move some of the actually positive
shadow bots also to /performance… make a copy there and call it beta. But since
this means those picks will land to users in Telegram, those bots must be
verified and well studied before, even for a BETA badge."*

Exactly right, and this document exists so the bar is written down **before**
picking winners. Choosing the bar after seeing which bots pass is how you end up
publishing noise with a badge on it.

---

## Headline: nothing clears a "profitable" bar today. Not one bot.

**Every ROI confidence interval in this system spans zero.** Across all six
unanimously-CLV-positive bots, at every odds floor, on all books and on
placeable books. The best case is `bot_v10_all` at +5.0% with CI [−6, +16].

That is not a near miss — it means we cannot tell any of them from a coin. So
**no ROI claim can be published**, in BETA or otherwise.

---

## Two filters that change the ranking, both discovered by asking the question properly

### 1. The edge roughly HALVES on books a user can actually bet

Half the apparent edge of the line-shop bots comes from prices readers cannot
take — most damagingly `Unibet-Kambi`, which this repo already established
**disagrees with the real site on 91% of quotes and reads HIGHER on 29%**
(KAMBI-FEED-DIVERGENCE). It is a phantom feed.

| Bot | CLV, all books | CLV, **placeable only** | t | ROI (placeable) |
|---|---|---|---|---|
| `bot_pin_1x2_home_v1` | +6.5% | **+2.9%** | +6.7 | −0.6% [−13, +11] |
| `bot_sweep_ou25_v1` | +4.6% | **+3.3%** | +8.0 | −6.9% [−18, +4] |
| `bot_sweep_ou35_v1` | +4.3% | **+3.4%** | +6.9 | −2.5% [−18, +13] |
| `bot_coolbet_value_v1` | +3.1% | **+3.1%** | +7.6 | +1.4% [−11, +13] |
| `bot_v10_all` | +3.8% | **+1.7%** | **+1.5** | +3.9% [−11, +19] |

`bot_pin_1x2_home_v1` took **33% of its picks at Unibet-Kambi**. And
`bot_v10_all` — which I called the strongest configuration in the sweep —
**loses significance entirely** once restricted to placeable books (t=+1.5). It
also priced 70 picks at Pinnacle, which is both unbettable here and the very
reference we measure CLV against, so those are circular.

**`bot_coolbet_value_v1` is the only candidate whose edge does not shrink at
all**, because it was always a single placeable book.

### 2. All four strongest candidates are RETIRED

`bot_pin_1x2_home_v1`, `bot_sweep_ou25_v1`, `bot_sweep_ou35_v1` and
`bot_coolbet_value_v1` were all **retired 2026-09-08**. Their records are
historical and stop there. You cannot promote a retired bot to BETA — there is
nothing coming out of it.

---

## The proposed bar for a BETA badge

A bot may be copied to `/performance` with a BETA badge only when **all** hold:

1. **CLV vs Pinnacle positive on PLACEABLE BOOKS ONLY**, t ≥ 3. Not all-books —
   see filter 1. A reader cannot take a Kambi price.
2. **n ≥ 334 settled picks** on that placeable subset. This repo's own figure for
   a useful CLV read; below it the number is not yet information.
3. **Fold-robust**: CLV positive in *every* walk-forward fold, not just overall.
4. **ROI not significantly negative** (CI upper bound above zero). We are not
   claiming profit — we are refusing to publish something with evidence against
   it.
5. **FORWARD data, gathered after the bot was selected.** This is the one that
   matters most and the easiest to skip: judging a bot on the same data that
   picked it out of ~35 candidates is textbook overfitting. A minimum of ~4
   weeks live after selection.
6. **The badge copy states what is proven and what is not**, in the reader's
   terms: *"beats the closing line; profitability not yet established"*. Not an
   ROI figure.

### Who clears it today

**Nobody.** Four candidates fail (5) outright by being retired; the rest fail
(2) or (1). That is the honest answer, and it is a better answer than a badge
on a bot we would have to quietly withdraw.

### The shortest honest path to a BETA bot

1. **Un-retire `bot_coolbet_value_v1`** — best candidate: single placeable book,
   CLV +3.1% at t=+7.6 unaffected by the placeability filter, ROI mildly
   positive. Check *why* it was retired first; the retirement reason recorded on
   2026-09-08 was an O/U line-shop leak (−17% ROI on that market), which may be
   market-specific rather than a verdict on the whole bot.
2. **Let it run forward ~4 weeks** without changing its gates, so criterion (5)
   is satisfied by construction.
3. **Re-measure on placeable books only**, fold-robust, then badge it.

Meanwhile the **sharp-anchored bots** (`bot_coolbet_trigger_sharp_1x2_v1`
+10.9%, `bot_unibet_trigger_sharp_1x2_v1` +10.0%) have the strongest CLV in the
system and are the most likely future BETA candidates — but at n=96 and n=77
they fail criterion (2) and need roughly 3-4× their current volume.

---

## Why this bar and not a looser one

The 👥 PICKS direction in `CLAUDE.md` is judged on *"whether a reader can act on
it and whether the number survives scrutiny."* Both filters above exist because
a number that looked strong did **not** survive scrutiny:

* `bot_pin_1x2_home_v1` at +6.5% CLV looks like the best bot in the system until
  you notice a third of it is priced at a feed that does not exist at the venue.
* `bot_v10_all` at +51.9% ROI on its best cell is the single most quotable
  number produced this week, and it evaporates on placeable books.

Publishing either would have been defensible from the summary table and
indefensible from the data.


---

> ⚠️ **UPDATED 2026-09-14.** The conclusion below (do not un-retire) STANDS, but
> its premise about the retirement reason was incomplete. Re-measured on era-1
> data only — excluding the OU-CALIBRATOR-DOMAIN-MISMATCH window — this bot is
> **CLV +4.29% (t=+8.3) with ROI +10.3% on n=325**, and its O/U legs are +3.1%
> (2.5) and +15.9% (3.5), not the −17% on record. The recorded reason was
> corrected by migration 337. It stays retired purely because the sharp
> successor scores 2–3× better, which is exactly the argument made below.
> See `docs/SHADOW_BOT_VERDICTS.md` Addendum 3.

# ADDENDUM 2 — `bot_coolbet_value_v1`: why it was retired, and why NOT to un-retire it

Owner asked four things: why was it retired, backtest its picks, does it need an
odds floor, and **is there an active bot doing the same?** The fourth turns out
to settle the other three.

## Why it was retired (migration 317, 2026-09-08)

> *"line-shop loses out-of-sample — it was **−17% on O/U every month** and the
> 1x2 raw signal is negative OOS (§52)."*

## That reason was PARTIALLY WRONG, and the segment table shows where

All 544 settled picks, every segment, fold-checked:

| Config | n | folds CLV | folds ROI | robust? |
|---|---|---|---|---|
| ALL (as retired) | 544 | +3.9 / +3.9 / +1.4 | +21 / −1 / −16 | CLV yes |
| edge ≥8% | 165 | +9.0 / +10.1 / +4.3 | −4 / −0 / +11 | CLV yes |
| edge ≥10% | 109 | +10.6 / +11.6 / +6.4 | −12 / +25 / +18 | CLV yes |
| **edge ≥8% AND exclude `under`** | **148** | **+8.9 / +9.8 / +5.5** | **+3 / +12 / +18** | **both** |
| 1x2 only | 363 | +4.1 / +4.1 / +1.9 | +22 / +2 / −13 | CLV yes |

**The leak was one selection, not the strategy.** `selection=under` is the only
significantly negative ROI in the entire table: **−29.5% (t = −2.6)**, and O/U
2.5 overall −10.1%. Meanwhile `market=1x2` is CLV +3.37% (t=+6.9) with ROI
+3.5%, and **every one of the bot's 25 segments has positive CLV**.

So "line-shop loses OOS" over-generalised from the O/U leg to the whole bot. The
answer to *"does it need a floor?"* is yes — **edge ≥8% plus dropping `under`**
is the only configuration positive in all three folds on **both** metrics.

## But do NOT un-retire it — the strategy is already running

`bot_coolbet_value_v1` = *"Coolbet's price valued against de-vigged Pinnacle."*
`bot_coolbet_trigger_sharp_1x2_v1` = *"Coolbet 1x2 odds vs pick_triggers where
cal_prob = de-vigged Pinnacle prob."*

**Same strategy.** And it has three live siblings:

| Successor | active | CLV |
|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | ✅ | **+10.9%** |
| `bot_unibet_trigger_sharp_1x2_v1` | ✅ | **+10.0%** |
| `bot_coolbet_trigger_sharp_ou_v1` | ✅ | — |
| `bot_trigger_1x2_sharp_v1` (book-agnostic) | ✅ | — |

> **⚠️ 2026-09-20 — THIS TABLE'S CLV FIGURES ARE NOT SAFE TO PROMOTE ON.**
> `SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES` voided all 31 picks of
> `bot_trigger_1x2_sharp_v1` / `bot_trigger_ou_sharp_v1` (every one priced off
> another fixture's quote; the 1x2 bot had reached +549.9% ROI). The two
> per-book successors quoted at **+10.9%** and **+10.0%** CLV above are
> **not re-verified** and are inflated by the same fault — 70% of
> `bot_unibet_trigger_sharp_1x2_v1`'s P&L comes from the 11.3% of its picks
> whose edge exceeds the 8% plausibility ceiling. **Nothing in this family
> clears the BETA bar until those rows are checked per-row** (PRIORITY_QUEUE →
> MERGED-TRIGGER-BOTS-UNDERFIRE).

The successors have **more than triple the CLV** of the retired bot (+10.9% vs
+3.1%) — and, critically, their data is **forward data gathered after
value_v1 was selected out**, which satisfies the BETA bar's criterion (5) by
construction. Un-retiring value_v1 would add a duplicate of a live strategy and
re-introduce the `under` leak.

## Do the value_v1 lessons transfer to the successors? NOT YET — n is too small

| group | n | CLV | t | ROI |
|---|---|---|---|---|
| all sharp bots | 206 | **+10.4%** | **+9.6** | +3.5% |
| `selection=under` | **18** | +6.5% | +3.5 | **+29.5%** |
| excluding `under` | 188 | +10.8% | +9.2 | +1.0% |
| edge ≥8% | **16** | +31.3% | +4.6 | **+91.0%** |

**Do not copy the fixes across.** In the successors `under` is *positive*
(the opposite of value_v1), and the edge ≥8% slice showing +31% CLV / +91% ROI
rests on **n=16** — that is the single most tempting and least trustworthy number
in this entire analysis. Sub-slicing 206 picks collapses every cell below n=20.

## Recommendation

1. **Leave `bot_coolbet_value_v1` retired.** Its successor is live, is the same
   strategy, and is three times better on CLV.
2. **Change nothing on the sharp bots.** Their aggregate CLV (+10.4%, t=+9.6,
   n=206) is strong evidence of edge; every sub-slice is n<20 and would be
   fitting noise.
3. **Let them reach n ≥ 334**, then re-run this analysis. At the current rate
   that is weeks, not months.
4. **Record the correction**: "line-shop loses OOS" was true of the O/U leg and
   false of the 1x2 leg. Worth keeping so the same over-generalisation is not
   made again — the bot was CLV-positive in all folds when it was retired.
