# Roadmap — ordered plan from the three research tasks

**Date:** 2026-08-28 · **Inputs:** research tasks 1–3 + this week's operational findings.

## The ordering principle

Two facts drive the sequence, and both argue against doing the interesting work first.

1. **Our fair-value anchor is compromised** (Task 2, Finding 1). Pinnacle in our feed
   carries an ~8% margin against the real ~2–3%, is wider than Coolbet at every tier,
   and is matched by Bet365 on Brier across 2,327 settled fixtures. **Every edge number
   we compute — live picks, backtests, CLV, the DC and AH verdicts — inherits that
   error.** Nothing measured before this is fixed can be trusted, so it goes first.

2. **We start verticals faster than we finish measuring them** (Task 3). CS2 built and
   deleted; tennis abandoned at n=38 *while showing +7.62% CLV*; DC and AH scoped and
   rejected inside a day. The roadmap therefore gates new verticals behind one
   completed measurement.

Everything below is ordered so that each phase makes the next one *interpretable*.

---

## Phase 0 — Measurement integrity (do first, blocks everything)

Nothing here adds a feature. It makes our numbers mean something.

| # | Task | Type | Effort | Why it is first |
|---|---|---|---|---|
| **0.1** | **Fix the fair-value anchor.** Adapt the existing `scripts/tennis/odds_api_scanner.py` client to soccer, sourcing genuine Pinnacle. Fall back to a de-vigged multi-book consensus (we hold 15 books) if the $99/mo Business tier is not approved. | Dev | 4–6h | Every downstream number depends on it. The client is already written. |
| **0.2** | **Re-score history under the corrected anchor.** How many of our 24 real bets would still have qualified? How much does claimed edge move? | Audit | 2h | Tells us whether the live bot has been betting real edge or noise — the single most important open question. |
| **0.3** | **Stake policy decision while evidence is thin.** Current caps are 20 bets / €200 per day against 24 bets of evidence and a suspect anchor. | Decision | 30m | Cheap insurance. Operator's call, not mine. |
| **0.4** | **Verify the AF Pinnacle feed defect.** Is it delayed, derived, or mislabelled? Affects every other bot that uses Pinnacle, not just the Coolbet one. | Investigation | 2h | Scope of the damage is currently unknown. |

**Exit criterion:** we can state, with a trustworthy anchor, whether `bot_coolbet_value_v1`
has positive expected edge.

---

## Phase 1 — Make the live bot reliable and measurable

Runs in parallel with Phase 0 where it does not depend on the anchor.

| # | Task | Type | Effort | Notes |
|---|---|---|---|---|
| **1.1** | **Scraper cookie autonomy.** Imperva cookies went stale three times this week (6h, 4.5h, 80h historically), and each outage silently empties the bot. The watchdog now detects it; make the re-harvest reliable without a human. | Dev | 3h | Feed death is the top cause of lost picks. |
| **1.2** | **Zero-picks alarm.** A dead scraper produces "0 picks" which looks identical to a quiet day. Alert on picks-count anomalies, not just odds staleness. | Dev | 1h | Direct lesson from the blinded watchdog. |
| **1.3** | **Match-failure audit.** ~6–11 picks per pass fail at the match stage. Separate "Coolbet does not offer this fixture" (correct) from "we failed to find it" (bug), now that the reason field distinguishes them. | Audit | 2h | Currently losing ~20% of the list every pass. |
| **1.4** | **Retry-through-the-day analysis.** We now record every attempt with a price. Do below-floor picks actually clear later? Yesterday suggests yes (Larne backed at 3.51 vs a 3.28 floor). | Investigation | 2h | Validates or kills the 30-min cadence. |
| **1.5** | **Accumulate to n≈78 CLV.** Time-gated, ~2–3 weeks at current volume. | Wait | — | **No new verticals during this window.** |

**Exit criterion:** a CLV read on the live bot, measured against a real anchor.

---

## Phase 2 — Expand access (the 80% we currently forfeit)

Do after Phase 0, because adding venues multiplies whatever edge we have — including a
negative one.

| # | Task | Type | Effort | Notes |
|---|---|---|---|---|
| **2.1** | **Second placement venue.** Unibet (best qualifying price 21.8% of the time) or Bet365 (12.7%) — both EMTA-licensed and already in our odds feed. Coolbet captures only **19%** of available 3% opportunities. | Dev | 6–8h | Biggest single volume win. UI placer proves we can automate a book with no API. |
| **2.2** | **Betfair Exchange investigation.** Legal in Estonia (one of few EU countries). No limiting of winners, ~2% on net winnings, and doubles as a superior anchor. | Investigation | 4h | Only option that survives us becoming consistently profitable. |
| **2.3** | **Epicbet as placement venue.** Already ingested, EMTA-licensed, best price 14% of the time. | Dev | 4h | Cheaper than 2.1 since the data path exists. |
| **2.4** | **Account-limiting watch.** Task 1: books limit on CLV, not results, and props go first. Track stake acceptance and max-bet over time. | Dev | 2h | We will not notice being limited until we measure it. |

---

## Phase 3 — Re-test what the bad anchor may have condemned

Strictly after Phase 0. These verdicts were reached using the compromised anchor.

| # | Task | Type | Effort | Notes |
|---|---|---|---|---|
| **3.1** | **Re-test Asian handicap.** Backtested −15.3% ROI on n=65 with claimed edge +7.44% — a gap the anchor error could explain. AH is Pinnacle's deepest market (3× its 1X2 coverage). | Investigation | 4h | Use same-window pairing; do **not** compute CLV by fixing the line (gotcha 16). |
| **3.2** | **Double chance — do not revisit.** Zero qualifying picks, median −5.8%, plus n=198 of historical negative CLV. Dead on its own merits. | — | — | ANALYSIS_GOTCHAS #15. |
| **3.3** | **Softer markets scan.** Task 1 points at corners, cards, props as least efficiently priced. Check whether Coolbet/Unibet price them and whether we can source a fair value. | Research | 3h | Only after the anchor works — otherwise unmeasurable. |

---

## Phase 4 — Second vertical (only after Phase 1 concludes)

| # | Task | Type | Effort | Notes |
|---|---|---|---|---|
| **4.1** | **Finish tennis settlement.** 38 bets from June sit unsettled at **+7.62% CLV**. Settling them costs hours and may already answer the question. | Dev | 3h | Highest information-per-hour item on this roadmap. |
| **4.2** | **Tennis restart decision.** Structurally better than soccer (individual, higher scoring, documented bias, 3.8% ROI study). Restart only with settlement working and a commitment to reach n≈78. | Decision | — | The failure mode to avoid is abandoning at n=38 again. |
| **4.3** | **Esports reconsideration.** Research says it is genuinely soft. We deleted 24k lines two days ago — reversing that needs a much better reason than "the research is encouraging". | Decision | — | Do not rebuild on a whim. |

---

## Explicitly not on this roadmap

* **A better forecasting model.** Task 1 is unambiguous that bookmaker odds are better
  calibrated than published models, and our live bot does not use our model at all.
  Model work is the least promising direction available to us.
* **Scaling stakes on the current record.** 24 bets is noise, against a suspect anchor.
* **New sports beyond tennis** until one vertical is measured to conclusion.
* **Prediction markets.** Real arbitrage exists, but differing resolution criteria can
  lose both legs and capital logistics kill most attempts. Not while simpler wins are
  unclaimed.

---

## The three things that matter most

1. **0.1 — fix the anchor.** Everything else is unmeasurable until this lands, and the
   client is already in our repo.
2. **2.1 — a second venue.** We forfeit 81% of qualifying opportunities by placing at
   one book.
3. **4.1 — settle the tennis bets.** Three hours to convert an abandoned experiment
   into an answer, on a sport the research says suits us better than soccer.
