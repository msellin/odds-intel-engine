# 🤖 OWN — the book universe is the binding constraint, and it is larger than we thought

Written 2026-09-14 after the OWN kill criterion closed line shopping. This is
about a *different* limit, one no audit had measured: **how many books we can
legally place at.**

## The measurement that started it

Same sharp rule, same time alignment, only the bettable set changes:

| bet at | n | ROI | 95% CI |
|---|---|---|---|
| all books | 1,234 | +3.83% | [−3.4, +11.0] |
| Coolbet / Epicbet / Unibet-Site | 92 | +16.00% | [−12.1, +44.1] |

**The books we can reach are not lower quality — there are just ~13× fewer
opportunities.** On 2026-09-14 the published rule found 8 picks across all books;
exactly **4** were at books an Estonian can use. Bet365 and Betfair supplied
**3 of the 4 largest edges** (+22.7%, +20.2%, +10.8%).

So the ceiling on OWN is not the model, the anchor, or the floor. It is the
number of licensed books we collect.

## What we believed vs what EMTA actually licenses

We had been treating the EMTA-legal set as ~3-4 books. The Estonian Tax and
Customs Board's own register of licensed operators is considerably larger, and
it includes books we had written off:

| operator | licensed domain | do we collect it? |
|---|---|---|
| **Hillside (New Media Malta) PLC — Bet365** | **www.bet365.ee** | AF feed only (unfaithful) |
| LEXBYTE DIGITAL — Unibet | www.unibet.ee | ✅ self-scraped (`Unibet-Site`) |
| OB Holding 1 OÜ — **Olybet** | www.olybet.ee | ❌ **not collected** |
| Optiwin OÜ — **Optibet** | www.optibet.ee | ❌ **not collected** |
| Triogames OÜ — **Betsafe** | www.betsafe.ee | ❌ **not collected** |
| Osaühing **Tonybet** | www.tonybet.com | ❌ **not collected** |
| Aktsiaselts PAFER — **Paf** | www.paf.ee | ❌ **not collected** |
| Moon Technologies — 20bet / 22bet | www.20bet.com / 22bet.com | ❌ not collected |
| Aktsiaselts Totalisaator | www.toto.ee | ❌ not collected |
| Dreambox Games — Chanz | www.chanz.ee | ❌ not collected |

Source: EMTA, *List of legal gambling operators*.

**Bet365 is licensed in Estonia.** It was removed from `ACCESSIBLE_BOOKMAKERS`
in `BET365-EXECUTION-AUDIT-2026-08-21` for a **feed** reason, never a legal one —
AF's Bet365 quotes run ~26.6% above contemporaneous Pinnacle. That commit's own
rollback note anticipated this exact situation:

> *"If Bet365 later becomes reachable via a different feed (direct scrape /
> different AF endpoint), re-audit before adding back."*

## Can we size the prize? No — and that is the finding

Adding AF's Bet365 to the executable set, with the new 20% price-ratio cap
already applied:

| executable set | n | ROI | 95% CI |
|---|---|---|---|
| current 3 books | 59 | +18.17% | [−15.8, +52.2] |
| + Bet365 | 449 | **+4.31%** | [−7.2, +15.9] |
| + Betano | 209 | +12.44% | [−3.8, +28.7] |
| + Bet365 + Betano | 550 | +6.00% | [−4.2, +16.2] |

Volume rises 7.6× and ROI falls from +18% to +4%. **But this cannot be read as
"Bet365 is a bad book."** We are measuring AF's Bet365 feed, which is precisely
the thing measured to be unfaithful. If the real bet365.ee prices are tighter
than AF reports — which is what "inflated ~26.6%" means — then against real
prices we would see **fewer** qualifying picks but **real** ones, not more picks
at +4%.

**The AF feed cannot answer a question about the book.** Every number in that
table is an upper bound on volume and an unknown on quality.

## The experiment that would answer it

Scrape `bet365.ee` directly for two weeks and pair it against AF's `Bet365` rows
on the same fixture, same market, time-matched within 15 minutes. Then:

* **The fidelity question:** what fraction of AF quotes are higher than the site
  actually offers? (Unibet: 33.1%. Kambi: 38%. Two for two on AF-fed books
  checked against their own site.)
* **The prize question:** rerun the executable-books backtest with real
  bet365.ee prices. If the qualifying set is small but the ROI holds near the
  +18% the three verified books show, the scraper pays for itself.

**Do not add Bet365 back to `ACCESSIBLE_BOOKMAKERS` on AF data.** That is what
the August audit already tried and reversed, and the smoke test
`ACCESSIBLE-BOOKMAKERS-FEEDS-ALIVE` now asserts the AF-fed 'Unibet' and
'Unibet-Kambi' never return. Bet365 should be held to the same standard: a book
enters the placeable set on a **verified** feed, or not at all.

## Ranked next actions

1. **Scrape one more licensed book we can verify.** Olybet, Optibet and Betsafe
   are Estonian-facing, licensed, and (unlike Bet365) carry no known feed
   history to unlearn. Each one directly multiplies the executable universe,
   which the measurement at the top says is the binding constraint.
2. **Then bet365.ee**, held to the fidelity test above before it is trusted.
3. **Do NOT** widen the rule's floors to manufacture volume. The volume problem
   is a book-count problem; loosening a floor to fix it trades real edge for
   picks, which is how the O/U calibrator's damage happened in the first place.

---

## CORRECTION (same day) — "all four sharp bots are positive" does NOT survive

I reported earlier today that all four SHARP-anchored trigger bots carried
positive margin-corrected EV (+0.27% to +5.24%) against six negative
model-anchored ones. **The sharp half of that is wrong.** Two errors, both mine:

1. **A flat margin.** I used `m = 7.6%`, a median across bots. The per-book
   closing margin actually spans **6.5% to 11.3%** (Coolbet 7.8, Epicbet 8.0,
   Pinnacle 9.1, Betfair 10.4, Unibet-Site 10.5, Bet365 11.3) — a spread wider
   than the thresholds it is compared against, so a flat `m` can invert the sign.
2. **Pooling rows whose `clv` was computed against an arbitrary book.** Requiring
   the closing book's own margin drops 488 of 2,447 rows, and the surviving
   subset tells a different story.

Recomputed with the closing book's own margin, per row:

| bot | anchor | n | raw CLV | m | EV @flat 7.6% | **EV @per-book** |
|---|---|---|---|---|---|---|
| `bot_unibet_trigger_sharp_1x2_v1` | SHARP | 67 | +12.05% | 8.60% | +4.13% | **+3.15%** |
| `bot_coolbet_trigger_sharp_1x2_v1` | SHARP | 66 | +4.51% | 7.63% | +0.84% | **−2.84%** |
| `bot_coolbet_trigger_sharp_ou_v1` | SHARP | 20 | +6.35% | 6.46% | −1.16% | **−0.05%** |
| `bot_trigger_1x2_model_v1` | model | 414 | +1.73% | 8.36% | −5.46% | −6.10% |
| `bot_coolbet_trigger_ou_v1` | model | 370 | +0.21% | 6.92% | −6.87% | −6.25% |
| `bot_unibet_trigger_1x2_v1` | model | 332 | +2.73% | 8.60% | −4.53% | −5.41% |
| `bot_coolbet_trigger_1x2_v1` | model | 297 | −0.20% | 7.31% | −7.25% | −6.97% |
| `bot_unibet_trigger_ou_v1` | model | 219 | +0.71% | 6.64% | −6.41% | −5.55% |
| `bot_trigger_ou_model_v1` | model | 160 | +0.50% | 6.85% | −6.60% | −5.93% |

**This independently reproduces the replication referee's finding from this
morning**, which I had treated as superseded:

> *"On the non-circular direct-book metric it does not replicate:
> `bot_coolbet_trigger_sharp_1x2_v1` +4.51% (n=66),
> `bot_unibet_trigger_sharp_1x2_v1` +12.05% (n=67). After the margin conversion
> those are −2.9% and +4.1% EV — **opposite signs**. The pooled +9.21% is two
> coin flips averaged."*

Their numbers and mine agree to ~0.3pp on two independently written harnesses.
The referee was right and I should not have set it aside.

### What this changes

* **What survives:** all six model-anchored bots are clearly negative
  (−5.4% to −7.0%) on large n. The model-vs-sharp separation is real and is the
  robust finding of the day.
* **What does not:** we do **not** have four working OWN bots. We have **one
  candidate** — `bot_unibet_trigger_sharp_1x2_v1`, +3.15% EV at **n=67**, which
  is 5 days of data and nowhere near a decision.
* **Operational consequence:** do not promote anything on this evidence. The
  pooled "sharp bots are CLV-positive" headline must not be quoted again without
  the per-book margin and the per-bot split.

**Method note worth keeping:** a flat average margin is never safe as a decision
variable when the quantity it corrects is the same size as the decision
threshold. Compute `m` per row from the book that actually set the closing
price, and leave it NULL when you cannot — a NULL is honest, an average is a
silent bias.
