# PICKS v4 — handover, 2026-09-15

**Read this whole file before touching anything.** It covers a LIVE, PUBLIC
surface: a Telegram channel with **62 real subscribers** and the `/picks` page.
A mistake here is visible to strangers, not just to the owner.

Status: **emergency fixes shipped; two adversarial reviews complete; the v4
change is specified but NOT built.**

**If you read only one section, read §6.** The headline number that motivated
this work (+16.56% ROI) was independently re-derived to the second decimal and
then **refuted as a claim**. The change is still worth making — on coverage
grounds alone, with no return claim attached.

---

## 0. What the owner actually wants

Verbatim, 2026-09-15:

> "we have a little bug and glitch and performance dropped and now i need to win
> back users, provide them with picks, maybe even look again into the grading
> system thing, provide more picks but with grading. but atm i just want to
> provide picks that will be at least breakeven"

In his priority order: **(1)** more picks reaching users, **(2)** the page and
channel not looking broken, **(3)** grading so that more picks does not mean
worse picks, **(4)** honesty — "at least break-even" is the bar he is asking
for, and §6 explains why it cannot be promised.

Direction (CLAUDE.md): **👥 PICKS** throughout. The 🤖 OWN automated-betting path
is CLOSED (`docs/OWN_PATH_VERDICT_2026_09_14.md`, migration 343); nothing here
reopens it and `placement_paused` stays TRUE.

---

## 1. Ground truth: what actually publishes

Verified 2026-09-15, not assumed.

| Surface | State |
|---|---|
| `@oddsintelpicks` | **live, 62 subscribers** (`getChat` + `getChatMemberCount`) |
| `/picks` | live, reads `picks_forward_test_public` (the `arm='live'` filter is in the VIEW, not TypeScript) |
| Users with Telegram linked | **0 of 52** — `profiles.telegram_chat_id` null on every row |
| `send_telegram_to_users` | pro/elite only: 1 elite user, unlinked. **Delivers to nobody.** |
| Email digest | last sent **2026-06-25**; jobs deliberately removed |

**TWO independent paths publish to the same channel.** This matters more than
anything else here:

- **Path A — model signaler.** `workers/jobs/betting_pipeline.py::_run_coolbet_signal`
  → `workers/automation/coolbet_signaler.py::signal_all_bets`. Reads
  `simulated_bets` from `maturity_label='calibrated'` bots. **Publishes nothing
  since migration 335** removed the O/U calibrator. Last `simulated_bets` row:
  **2026-09-13 13:06 UTC**.
- **Path B — forward-test publisher.** `scripts/publish_picks_forward_test.py`,
  scheduled as `workers/scheduler.py::job_publish_picks_forward_test`.
  **The only path that has ever put a pick in the channel.**

A change aimed at "publishing" that touches only Path A does nothing. That
mistake was made today — see §2.1.

---

## 2. What was done today

Five commits on `main`, CI green.

### 2.1 `a1cb9167` — PICKS-PUBLISH-DECOUPLED-FROM-OWN-PAUSE
`_run_coolbet_signal` returned early on `placement_paused` — the **real-money**
kill switch — so closing the OWN path on 2026-09-14 12:05 UTC (migration 343)
armed a customer-feed outage nobody chose. Cost: **zero**, by timing.
Fix: migration **353** adds `publishing_paused`; `coolbet_state.is_publishing_paused()`
/ `set_publishing_paused()` (falls **open** on DB error); Telegram `/pausepicks`
and `/resumepicks`; `/status` shows both switches. Ledger pattern **9b** —
*split the flag, not the branch.*

> ⚠️ **Aimed at Path A, which publishes nothing.** Correct, but nearly useless
> until `59034d7c` wired the same flag into Path B. **Establish which code path
> reaches the surface before fixing anything.**

### 2.2 `7d6d01d7` — PICKS-CI-GUARD-VS-DROPPED-BLOCK
`PICKS-FORWARD-TEST-SURFACE` had been red since 2026-09-14 20:15 because web
commit `926aaaf` dropped `/picks`'s running-result block, which carried the CI
the test asserted. The page is right — it publishes no aggregate. Guard made
conditional **and paired with its complement** (no summary import ⇒ no `ROI` /
`roi_pct` / `hit rate` in rendered source). Mutation-verified.

### 2.3 `8a7fefb4` — MIGRATE-ONE-QUERY-FOR-THE-APPLIED-SET
`migrate.yml` opened one psql connection **per migration file** — 352 through an
SSH tunnel to apply one `ALTER TABLE`, >7 min of a 15-min budget. Now one query.

### 2.4 `59034d7c` — SCHEDULER-PUBLISHER-NEVER-RAN ← **the important one**

**`pipeline_runs` held ZERO rows for `publish_picks_forward_test`.** Registered
at 10:00 UTC daily, never once completed. Four independent fatal defects:

1. `picks = load_candidates()` — returns a **2-tuple** since `2796dbd6`, which
   **predates** the commit registering the job. `if not picks` never true;
   `render()` got a list → `TypeError`.
2. `junk_anchor_arm(picks, picks)` against a **one-argument** signature.
3. `log.info(...)` with **no `log` in the module** → `NameError`.
4. The junk arm got the SELECTED picks, not the POOL — the degenerate control
   supposedly fixed 2026-09-14.

**Every pick ever published came from a manual CLI run on 2026-09-14 (8 picks,
`sharp_edge_v1`). v2 and v3 published nothing because they never could.**

Also fixed: `job_pinnacle_drift_refresh` had the same `NameError`. Also: the
publisher now reads `is_publishing_paused()`.

`PICKS-FORWARD-TEST-SCHEDULED` asserted the **import line as a string** and was
green throughout. It now **exercises** the job with sends stubbed;
mutation-verified. Deployed to the VPS 09:08 UTC, ahead of the 10:00 fire.

---

## 3. The rule

`scripts/publish_picks_forward_test.py`, `RULE_VERSION = "sharp_edge_v3_2026_09_14"`.
Pre-registration: `dev/active/picks-forward-test-preregistration.md`.

```
anchor = Shin de-vig of the Pinnacle triple      (workers.model.devig.devig)
edge   = P_shin * best_book_price - 1   >= 0.03  ← EXPECTED ROI, not P - 1/odds
odds <= 4.0 ; price ratio book/anchor - 1 <= 0.20  [v2]
anchor overround <= 0.04                           [v3]
alignment: anchor and bet quote within 60 min
markets: 1x2, over_under_25 ; EXCLUDED_BOOKS applies
selection: top 8 per day by edge
window: kickoff in (now+45min, now+14h); snapshots from last 6h
```

⚠️ `MIN_LEAD_MIN = 45` and `LOOKAHEAD_H = 14` are **NOT** in the LOCKED block and
**NOT** in the smoke `locked` dict (`publish_picks_forward_test.py:111-112`
only). A schedule change silently moves an unpinned rule parameter. **Lock both
in the v4 commit.**

⚠️ This rule's `edge` is **expected ROI**; the rest of the repo uses `P − 1/odds`.
A 3% floor on one admits ~2× the picks of a 3% floor on the other. Public label
must read **"Expected return"**, never "Edge".

⚠️ **The 60-minute alignment gate is nearly inert:** 93.4% of pool legs have an
alignment gap of exactly **0.0 minutes**, because AF writes every book in one
bulk sweep under one timestamp. That measures write granularity, not
simultaneity (ANALYSIS_GOTCHAS §63).

---

## 4. The proposed change (v4) — specified, NOT built

1. **Continuous publishing** instead of one 10:00 UTC batch.
2. **`TOP_N = 8` becomes a daily CAP, not a selection rule.**
3. **Grade, don't filter:** A = 2+ books at this price, B = single-book. Publish
   both, label both, **record the grade as a column**.
4. **`/picks` filters on publish time, not kickoff time.**
5. A publisher health check (see §5 P1-9 — the naive version is wrong).

---

## 5. Open defects NOT fixed

### P0

**P0-1. Send-then-record would post each pick 6–13 times under continuous runs.**
`publish_picks_forward_test.py:352-358` and the scheduler send **first**, then
`record()`. The unique index (`342_picks_forward_test.sql:65`) is correct but
`ON CONFLICT ... DO NOTHING` suppresses the **row**, never the **message**. A leg
re-qualifies in **median 6, mean 6.8, max 13** consecutive runs; only 8% are
single-run transients. One measured day's 13 picks → **~88 messages**. The
ledger also keeps the *first* row while the channel shows the *last* message,
breaking the invariant at `odds-intel-web/src/lib/forward-test-picks.ts:38-41`.
**Fix:** `... ON CONFLICT (...) DO NOTHING RETURNING id`; send only on a created
row; `UPDATE` the message id after. **Do NOT rely on `_LAST_SENT`** —
`send_telegram_public` has no dedup window (`workers/notify/telegram.py:210`) and
`_LAST_SENT` is an in-process dict wiped on restart (ledger #13).
**Hard prerequisite for the cadence change.**

**P0-2. Every rule-version bump silently erases the public track record.**
`odds-intel-web/src/lib/engine-data.ts:6019-6020` takes
`.order("started_at", desc).limit(1)`; `src/components/picks-forward-test-panel.tsx:27`
renders it as the current method. **Today the panel presents v1 — a closed rule —
as current**, and on v4's first pick v1's record vanishes. That is a mechanism
that resets the scoreboard whenever the number goes bad.
`fetchForwardTestSummary` already returns `{current, closed}`;
`getPicksForwardTestSummary` throws the closed rows away.

**P0-3. The books supplying 54% of picks have never been validated, and the test
that said they were fine was the wrong test.** See §6.3. Treat as a blocker on
any return claim, not on shipping coverage.

### P1

**P1-4. Continuous runs destroy the junk-anchor control.** `junk_anchor_arm`
(`:247`) selects top-8 **per invocation** into the same `DO NOTHING` index, so
over N runs the control accumulates the **union of N draws** (uncapped) while
live accumulates one capped set. `rng = random.Random(20260914)` (`:271`) is
re-seeded identically every run, so draws are correlated, not independent.
**Fix:** same cadence and same daily cap as live; seed per `(date, run)`.
`PICKS-FORWARD-TEST-JUNK-ARM-SELECTS` (`smoke_test.py:41167`) will keep passing
through this.
**Note:** the current junk arm selects **n ≈ 1,277** vs live's 72 — 18× — so its
sampling sd is 3.5pp and it *cannot* produce a large positive by chance. It
tests that the harness isn't broken; it says **nothing** about significance.

**P1-5. No cross-run accounting, so "8 per day" has no implementation.**
`select()` (`:240-244`) caps per call. Count on **`published_at::date` UTC** —
with `LOOKAHEAD_H=14` one run spans two kickoff dates. 00:00–03:00 kickoffs are
only visible from ~10:00–13:00 the previous day and consume the **previous**
day's cap. Consider a rolling 24h window.

**P1-6. Lead time moves outside what was measured.** Continuous first-sighting
mean lead **7.41h**, max **13.92h** (the `LOOKAHEAD_H` ceiling), vs v1's actual
5.58h / 10.41h. The scheduler docstring's claim that the backtest "was measured
on quotes at least 4h out" is **unsourced** — the pre-registration stratifies by
*alignment*, never by lead. First-sighting edge inflation was looked for and
**not found** (n=5 — unmeasured, not absent). **Lock the constants and record
`lead_minutes` per row.**

**P1-7. Smoke tests that keep PASSING while the rule changes** (ledger #9):
`PICKS-FORWARD-TEST-RULE-LOCKED` (`:40991`) asserts `TOP_N == 8` as a **number** —
turning it from a selection rule into a daily cap leaves it green.
`PICKS-FORWARD-TEST-SURFACE` (`:41225`) says nothing about the
kickoff-vs-publish-time basis. `…-JUNK-ARM-SELECTS` — see P1-4.
`PICKS-FORWARD-TEST-SCHEDULED` asserts `CronTrigger(hour="10", minute="0")` and
**will fail, correctly** — rewrite it to pin the invariant, not the hour.

**P1-8. Feed liveness is not checked at publish time.** At review:
**10Bet dark 82h, Superbet 72h, Dafabet 34h** — and **35% of qualifying legs come
from books dark >24h**. A price from a dark feed is a stale price being published
as live. **Add a per-book freshness gate before publishing at that book's price.**

**P1-9. The health-alert change as proposed targets the wrong table.**
`NO_PICKS_AFTER_HOURS` (`workers/jobs/health_alerts.py:695`) reads
`MAX(created_at) FROM simulated_bets` — **Path A**. Dropping it to 18h does
nothing for picks silence and will cry wolf on a thin weekend. **Leave it at 48.**
Add a separate check on `pipeline_runs` for **job failed / job never ran** — not
on zero picks, which is a valid outcome. This gap is why §2.4 went undetected.

### P2

**P2-10.** `fetchForwardTestPicks` (`forward-test-picks.ts:141-155`) is shared by
`/picks` **and** `/api/v1/upcoming/route.ts:50-52`, pinned by `PICKS-COHORT-ALIGN`.
Change the basis in the shared fetcher. `page.tsx:135-138` **groups** by kickoff
while you would **filter** by publish time — headings and headline will start
counting settled picks. `.limit(200)` becomes a silent truncation as volume rises.

**P2-11.** `docs/SYSTEM_MAP.md:183` and `workers/registry/bot_registry.py:177`
still describe **v1**; `SYSTEM_MAP.md:253` still describes the removed
running-result block. The drift test only checks map-vs-registry, never
map-vs-script.

---

## 6. EVIDENCE — what survived adversarial review

Two independent agents reviewed this. The second re-implemented the rule from
scratch (own Shin solver, own SQL, no import of the original scripts) and
**reproduced all six headline numbers to the second decimal** — then refuted the
inferences drawn from them.

### 6.1 Verdicts

| Claim | Verdict |
|---|---|
| 72 legs, median 3/day, mean 4.0/day, cap binds 3/18 days | **WEAKENED** |
| **+16.56% ROI, hit 50.0%, CI [−11.3,+45.2]** | **arithmetic confirmed / claim REFUTED** |
| Early kickoffs (+19.56%) not worse than late (+13.87%) | **REFUTED** |
| **47.2% of legs invisible to a 10:00 run** | **CONFIRMED** |
| Winner's curse: edge +5.11% → +0.46% at 2nd-best | **CONFIRMED and strengthened** |
| No supplying book is systematically phantom-high | **REFUTED** |

### 6.2 The retention artifact — affects every backtest in this repo

**`prune_old_simple` (ANALYSIS_GOTCHAS §59) keeps at most three rows per series
after 7 days.** Measured rows per `(match, book, market, selection)`:

| kickoff era | rows |
|---|---|
| 2026-08-16 → 09-07 (pruned) | **2.6 – 4.7** |
| 2026-09-08 → 09-15 (intact) | **13.1 – 20.2** |

On pruned days `DISTINCT ON … timestamp DESC` does not return "the last price
before KO−45min" — it returns **the only surviving price**, which retention chose
to be the latest pre-kickoff row. Consequences:

- The 30-day +16.56% decomposes into **+28.40% (n=35, pruned, unreconstructable)**
  and **+5.35% (n=37, intact)**. Half the headline comes from a data regime that
  no longer exists.
- On the two biggest contributing days (08-29, 09-02 — 10 legs each) a
  point-in-time replay finds **ZERO** qualifying legs at any hour. **20 of 72
  legs (28%) are reconstructions the live job could not have produced.**
- A 90-day run gives n=81, of which **72 fall in the last 30 days**. The window is
  not cherry-picked — it is the only window that produces data.

**Rule for the next agent: a "30-day backtest" on `odds_snapshots` is an ~8-day
measurement plus 22 days of retention artifact.** This deserves its own
ANALYSIS_GOTCHAS entry.

### 6.3 The book-fidelity finding — my own test was the wrong test

I reported that no supplying book is phantom-high, using **median** price vs
contemporaneous Pinnacle (Bet365 −1.44%, 10Bet −1.41%, Dafabet +0.83%,
SBO −7.73%). **The median is irrelevant because the rule selects the maximum.**
Where the *selected* legs actually sit in each book's own distribution:

| book | legs | median ratio on selected legs | percentile of that book's own distribution |
|---|---|---|---|
| SBO | 15 | +8.51% | **99.4th** |
| 10Bet | 14 | +9.52% | **98.8th** |
| Dafabet | 10 | +9.88% | **99.3rd** |
| Bet365 | 6 | +7.22% | **99.8th** |
| BetVictor | 4 | +7.50% | **99.9th** |

Every supplying book contributes at the **98.8th–99.9th percentile of its own
disagreement with Pinnacle** — exactly where a stale, shell or phantom quote
lives. A median of −1.4% is fully compatible with a 1% tail that is entirely
unfaithful.

**SBO, 10Bet and Dafabet supply 54% of legs at +26.15% ROI and none has ever been
validated against its own site.** `daily_pipeline_v2.py:1099` states the base
rate: *"Three AF-fed books have now been checked against their own site; three
were unfaithful"* — Bet365 ~26.6% high, AF-Unibet 33.1%, Kambi 38%.

### 6.4 Significance, power, concentration

- **+16.56% → one-sided p = 0.127** unadjusted (permutation under a zero-EV null,
  B=200,000). **P(at least one of 13 examined cuts ≥ +16.56%) under the null =
  0.656.** Only `odds ≤ 2.5` clears p<0.05 unadjusted (0.047); Bonferroni → 0.61.
- An n-matched junk-anchor draw reaches ≥ +16.56% in **12.4%** of draws.
- **65% of all profit comes from 3 legs.** Excluding them: **+6.03%**. Two of the
  three are Bet365 legs priced ~7% over Pinnacle.
- The rule's own **claimed** mean edge on those legs is **+5.89%**; realised
  +16.56% is 0.73 sd above what the rule itself predicted. The excess is noise.
- Per-bet sd **1.238**. To distinguish from zero at 80% power: **+16.56% → 438
  bets**; **+5% → 4,808 (3.3 years)**; **+3% → 13,355 (9.1 years)**. **MDE at
  n=72 is ±40.9%.**
- **Internal inconsistency in my own numbers:** 72 legs were quoted while the
  rule caps at 8/day. The publishable set is **64 legs at +14.25%**.
- The monotonicity control the pre-registration cites as passing **fails on this
  window**: <0% → −2.90%, **0–1% → −7.47%**, 1–3% → +10.76%, 3–5% → +18.65%,
  5–10% → +11.52%, ≥10% → +28.71%.
- Same rule, two measurements, opposite signs, both n=72: the pre-registration's
  <4%-overround band is **−3.36%** over 90d on the three bettable books; this
  backtest is **+16.56%** over 30d on best-of-all-books. **The difference is the
  price basis.**

### 6.5 What IS clean

**The coverage fact.** ~47% of qualifying legs (34/72) kick off before ~10:45 UTC
and are structurally unreachable by a single 10:00 run; 00:00–03:00 kickoffs are
unreachable by *any* 10:00 run. An independent 2-day replay of the live rule
found the 10:00 run captures **5 of 16 legs (31%)**. Point-in-time simulation
over the intact window: continuous hourly **46 legs** vs single-10:00 **18 legs**
in seven days. **This survives everything above.**

### 6.6 What must NOT be said publicly

1. **No "+16.56%"** or anything derived from it.
2. **Not** "early picks are as good as late" — say *indistinguishable*; the sign
   **flips** under the rule's own cap (early +12.07% vs late +16.06%).
3. **Not** "a 30-day backtest shows…" — quote 2026-09-08 → 09-14 and its n.
4. **Not** "prices are corroborated / no book is phantom-high" — §6.3.
5. **Not** "break-even or better" — the only live evidence is v1 at **ROI −37.1%,
   margin-corrected CLV −11.1%, n=8**, every row negative, against a
   pre-registered n=200 STOP threshold of −2%.
6. **Not** "4 picks/day" without saying which days — forward-relevant is ~4.6/day
   on unpruned days, and the live publisher produced **0** on its first gated day.

**What can honestly be said:** a single 10:00 run cannot reach ~47% of qualifying
picks. Publishing continuously roughly doubles reachable picks. **Make the change
on coverage grounds, state no return claim, and let the pre-registered forward
test decide.**

---

## 7. Implementation plan — ordered, with exit criteria

Follow CLAUDE.md: mark `🔄 In Progress` in `PRIORITY_QUEUE.md` **before** code,
add a smoke test per step, commit docs with code, ripple-check
(`grep -rln "<thing>" docs/ *.md`).

**Step 0 — verify the emergency fix held.** Confirm `pipeline_runs` has a
successful `publish_picks_forward_test` row for 2026-09-15 10:00 UTC and that
`picks_forward_test` gained `arm='live'` rows under `sharp_edge_v3…`. If it
failed, fix that first. *Exit: one green scheduled run.*

**Step 1 — P0-1, invert send/record.** Prerequisite for everything else.
*Exit: mutation-verified test that fails against the current ordering.*

**Step 2 — P0-2, stop version bumps erasing history.**
*Exit: the panel shows v1's n=8 record after v4 starts.*

**Step 3 — register v4.** ONE `sharp_edge_v4_2026_09_15` covering cadence +
daily-cap semantics + grading, with `MIN_LEAD_MIN`/`LOOKAHEAD_H` added to the
LOCKED block and the smoke `locked` dict, and `lead_minutes` + `grade` recorded
per row. **Cost is zero right now — v2 and v3 published nothing.**
*Exit: pre-registration updated and dated before the first v4 pick.*

**Step 4 — cadence.** `:05/:35`; daily cap on `published_at::date` UTC; junk arm
under the same cadence and cap with a per-run seed (P1-4, P1-5); per-book
freshness gate (P1-8). *Exit: a full day with no duplicate message and a junk arm
whose n tracks live's.*

**Step 5 — `/picks` publish-time basis** in the shared fetcher, grouping and
headline fixed (P2-10). *Exit: yesterday's settled pick is off the board while
today has picks.*

**Step 6 — publisher health check** on `pipeline_runs` job-failure (P1-9). Leave
`NO_PICKS_AFTER_HOURS` at 48. *Exit: a deliberately broken run pages.*

**Step 7 — docs ripple.** `SYSTEM_MAP.md`, `bot_registry.py`, `WORKFLOWS.md` (the
10:00 line), the pre-registration, and a new **ANALYSIS_GOTCHAS** entry for §6.2.

**Do NOT do:** the 2nd-best-price gate (proposed on the edge metric; settled data
does not support that severity, and at n=72 neither number is readable). Grade
instead of filter.

**Worth proposing to the owner separately:** validate SBO / 10Bet / Dafabet
against their own sites, the way Kambi and AF-Unibet were. Base rate is 3/3
unfaithful. Until then, every return figure from this rule rests on unvalidated
prices at the 99th percentile of their own books' disagreement with Pinnacle.

---

## 8. Scratch artefacts (session-local, not committed)

`/private/tmp/claude-501/-Users-margussellin-www-odds-intel-engine/73a078e0-.../scratchpad/`
— mine: `supply.py`, `settle_v3.py`, `bookfid.py`, `v10ci.py`, `funnel.py`.
Reviewer's: `lib.py`, `claimed.py`, `a1_hourly.py`, `a2a3.py`, `c6.py`, `junk.py`.
**Prefer the reviewer's** — `bookfid.py` in particular embodies the §6.3 error
(median where the maximum is what the rule selects). Re-derive rather than trust.
