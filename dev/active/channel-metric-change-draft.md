# DRAFT — channel message on the metric change (PICKS-EXPLAIN-EDGE-CHANGE)

> ⛔ **NOT SENT. DO NOT SEND FROM AN AGENT.** No Telegram function was called
> while writing this. It is here for the owner to read, edit and post by hand.
>
> Target: `@oddsintelpicks` (public). Format: Telegram HTML, same conventions as
> `workers/automation/coolbet_signaler.py::_format_public_signal` — `<b>`, `<i>`,
> `<pre>`, `<a href>`; no Markdown, no tables (Telegram renders neither).
>
> **Three messages, posted in order, a minute or two apart.** One message would
> be ~4,000 characters of bad news in a wall — the sequence gives each part its
> own beat, and message 3 is the one people will screenshot, so it should stand
> alone.

---

## Why this needs saying at all

Two things changed under readers' feet on the same day and neither is
self-explanatory:

1. The `/performance` bankroll curve fell off a cliff and nothing on the site
   explains why.
2. The number attached to every pick changed meaning. It used to read
   `Edge: +8.5%` (our model's probability minus the implied price). It now reads
   `Edge vs sharp line: +5.3%` (the price minus the de-vigged sharpest line, no
   model involved). **Different rulers, not a weaker pick** — today's top pick
   read +22.7%.

If we say nothing, the honest reading of the graph is "their model stopped
working", and the honest reading of the smaller number is "their picks got
worse". Neither is what happened, and the real story is worse in one way and
better in another.

**Tone check before sending:** this is not a comeback story. We shipped a bug
that inflated published numbers for months, and while fixing it we found the
model has no measurable edge over the market at all. The message should read
like someone telling you what it cost, not like someone announcing a
breakthrough.

---

## MESSAGE 1 — the graph

```html
📉 <b>About that graph</b>

If you've looked at our performance page recently, you saw the bankroll curve
drop hard. Here's what happened, in full.

We found a bug in how we calibrated our own probabilities. One of our
calibration curves was fitted on one set of numbers and then applied to a
different set — a plumbing mistake, not a modelling one. The effect was that it
manufactured roughly 8-9 percentage points of "edge" out of nothing, on every
pick it touched, for months.

Our main reference bot shows the damage cleanly. Over/Under picks went from
14.6% of everything it bet to 69.9%, because the bug made them look like the
best bets on the board. Its return went from +38.5% to −15.0%. Peak bankroll
+€756.06 ended at +€289.30.

<b>The curve on that page is real. The edge that was supposed to be driving it
was not.</b> We have left the graph up rather than quietly resetting it.
```

## MESSAGE 2 — the bigger thing we found while fixing it

```html
🔍 <b>And then it got worse</b>

Fixing the calibration bug meant re-testing whether our model actually adds
anything on top of the market price. That test has a simple form: take the
market's own price, take our model's probability, and ask whether the model
improves on the price at all.

The answer was <b>zero</b>. Not "small" — 0.0000. We checked it against four
independent benchmarks, including odds we scrape ourselves from the books we
can actually reach, and on every one the model-plus-market blend was <i>worse</i>
out of sample than the market alone.

That is an expensive thing to learn, and it is the reason we have not simply
shipped "a fixed model".

<b>The new picks use no model at all.</b> Every pick is now priced directly
against the sharpest line in the market with the bookmaker's margin stripped
out. If the best price available beats that line by enough, it's a pick. If it
doesn't, there's no pick that day — and some days there won't be.
```

## MESSAGE 3 — why a smaller number is a better number

```html
📊 <b>"The edge numbers got smaller"</b>

They did, and they're measured differently, so here is the comparison you
actually want.

The useful question isn't how big a claimed edge is. It's <b>how much of it
turns into money.</b> We measured that for both.

<pre>
                   claimed   realised   kept
model edge         +14.26%    +1.75%    0.12
sharp edge          +7.43%    +5.54%    0.74
  3-5% band         +3.85%    +4.38%    1.14
  5-8% band         +6.26%    +7.88%    1.26
</pre>

A claimed model edge returned about <b>12 cents on the dollar</b>. A claimed
sharp edge returns about <b>74 cents</b> — and roughly 1:1 in the 3-8% band
where most picks sit.

So a +6% sharp edge beats a +12% model edge in expectation: 6 × 0.74 ≈ +4.4%
against 12 × 0.12 ≈ +1.4%. Same arithmetic, different estimator.
<b>An edge is only as good as the probability estimate behind it.</b>

Two things we are not going to bury:

• The sharp column is a <b>backtest</b>. The model column is realised money.
That is not like-for-like, and no amount of backtesting fixes it — which is
exactly why we're now running this forward, in public, from today.

• <b>Bigger sharp edges are not better picks.</b> Above 8% the ratio drops to
0.28, and above 15% to 0.36. Large edges sit on wide-margin markets — obscure
leagues, reserve sides — where stripping the margin out is less reliable.
Today's biggest pick read +22.7% and is in exactly that band. Treat the big
numbers with more suspicion, not less.

<b>We are claiming no track record for this method.</b> It starts at zero on
14 September 2026. We've written down in advance what would make us stop, and
we'll publish the result either way.

<a href='https://oddsintel.app/picks'>Live picks</a>
```

---

## Constraints this draft is holding to — check these survive any edit

* **No track-record claim.** Nothing here says or implies the new method has
  made money. The only performance numbers attached to it are explicitly
  labelled a backtest, and the caveat is in the body, not a footnote.
* **The +5.5% backtest ROI is deliberately never quoted as a headline.** It
  appears only inside the realisation table as `+5.54% realised` against
  `+7.43% claimed` — i.e. as the *ratio's* input, which is what the comparison
  needs. Quoting "+5.5% ROI" on its own, next to a 95% CI of [−0.7, +11.7], is
  the exact move that got us here.
* **No link to `/performance`.** That page is the model-anchored ledger, priced
  on a manufactured edge and surviving in 2 bots of 46. Message 1 refers to the
  page because that is the page readers are asking about; it does not link to it
  as evidence of anything, and messages 2 and 3 link only to `/picks`.
* **"Different ruler" is stated, not asserted.** Message 3 gives the arithmetic
  rather than asking readers to take it on faith.
* **The bad news is not softened into a lesson.** Message 2 ends on "we have not
  simply shipped a fixed model", not on how much we learned.

## Numbers used, and where each comes from

| Figure | Source | Status |
|---|---|---|
| ~8–9pp manufactured edge | `docs/PLAN_AFTER_AUDITS_2026_09_14.md` §1 finding 3; migration 335 | settled |
| bot_v10_all 14.6%→69.9% O/U share, +38.5%→−15.0% ROI, +€756.06→+€289.30 | owner, measured | supplied for this draft, not re-derived here |
| α = 0.0000 over the market price, 4 benchmarks, blend worse OOS | `docs/PLAN_AFTER_AUDITS_2026_09_14.md` §1 finding 1 | settled |
| realisation table (0.12 / 0.74 / 1.14 / 1.26) and the >8% degradation (0.28, 0.36) | owner, measured | supplied for this draft, not re-derived here |
| today's top pick +22.7%, Iraqi league | `picks_forward_test`, verified 2026-09-14: Erbil v Al-Karma, 1x2/draw @3.00 Bet365, edge +22.68% | verified here |
| start date 2026-09-14, stopping rules pre-registered | `dev/active/picks-forward-test-preregistration.md` | settled |

**If the owner wants to re-derive the two owner-supplied rows before posting**,
they are the only figures in the message with no committed script behind them —
which is precisely the failure mode `PLAN_AFTER_AUDITS_2026_09_14.md` §6 names.
Everything else is reproducible from the repo. That is a flag, not a blocker:
they are the owner's own measurements and the message is the owner's to send.

## Open question for the owner

Message 1 says "we have left the graph up rather than quietly resetting it."
That is true today. If `/performance` is going to be reset, re-scoped or taken
down, that sentence has to change before this is posted — it would otherwise be
contradicted within the week.
