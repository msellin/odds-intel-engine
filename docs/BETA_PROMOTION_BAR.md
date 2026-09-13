# Promoting a shadow bot to `/performance` (BETA) — the bar, and who clears it

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
