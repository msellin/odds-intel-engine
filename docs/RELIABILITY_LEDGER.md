# Reliability Ledger — the failure patterns that keep coming back

**Read this before debugging a "mystery" outage.** Not a changelog: a list of
*patterns* that have each bitten more than once, what the tell is, and which
guard now exists. If a new incident matches a pattern here, start from the
guard — do not re-derive it.

Companion docs: `COOLBET_RUNBOOK.md` (symptom→cause→fix per Coolbet failure
mode), `BETTING_ARCHITECTURE.md` (who places what), `ANALYSIS_GOTCHAS.md`
(the same idea for analysis queries).

---

## 1. We blame the loud thing, not the broken thing

The most expensive pattern here, by a distance. Three times now an alert has
confidently named a cause it had no evidence for, and the real fault sat
somewhere quieter.

| Date | Alert said | Actually was |
|---|---|---|
| 2026-09-04 | cookies | a GET with no timeout, hung 15h15m |
| 2026-09-10 | Imperva / cookies | `COOLBET_NO_FS=true` in a **stale installed plist** |
| 2026-09-11 | cookies (again) | FS session challenged on reuse; our own request volume |
| 2026-09-18 | cookies (a fourth time) | a **crashed Chrome tab** inside the sweep's FS session |

The 2026-09-04 note says it best: *"the feed watchdog cheerfully re-harvested
cookies at a problem that was never about cookies."*

**Rule: when cookies are fresh and the feed is dead, it is not the cookies.**

**The guard that did NOT work, and why (2026-09-18).** After 09-10 the guard was
that the `STALE_COOKIES` *message* says "this is probably NOT the cookies — check
TRANSPORT first". On 2026-09-18 Coolbet odds were dead **9.7 hours**. The watchdog
ran 29 times, printed that exact sentence every single run — and re-harvested
cookies every single run, because only the message had been changed, never the
action. The outage surfaced when the owner noticed `/performance` had not moved in
two days.

**This is the sub-pattern worth naming: a better error message is not a remedy.**
It moves the cost from "diagnose it wrong" to "diagnose it right and still do
nothing", which looks like progress in the log and is identical in the DB. If the
watchdog can name the likely cause, it is close enough to *test* the cause.

**Guard (real this time):** `WEDGED-SESSION-SELF-HEAL` — when the feed is stale
and cookies are fresh, `classify()` now spends ONE `fo-tree` GET on the sweep's own
FS session and branches on the answer:

| probe | means | remedy |
|---|---|---|
| HTTP 500, fixed ~61s, **0 bytes** | dead Chrome tab inside FlareSolverr | `WEDGED_SESSION` → destroy **only** `coolbet_odds_reader` |
| small real body, ~2s | Imperva flag is live | `BLOCKED` → reduce footprint, do **not** cycle sessions |

Those two were previously indistinguishable from the DB and have opposite
remedies. FlareSolverr itself stayed **healthy** throughout the 09-18 outage —
`GET /` and `sessions.list` both green — so every container-level probe read fine;
a *fresh* session answered in 2.0s with 190,708 bytes while the sweep's session
returned 0. **Corollary: "FlareSolverr is up" says nothing about whether your
session works.** Smoke: `COOLBET-WEDGED-SESSION-SELF-HEAL`.

## 2. Two things that look identical are not the same thing

Coolbet's logged-out page shows the brand slogan **"STAY COOL"**. So does the
Imperva wall. They need opposite responses and were conflated for weeks.

| | Imperva wall | Just logged out |
|---|---|---|
| body | ~9 chars | full SPA |
| localStorage | ~nothing | **35 keys** |
| `x-iinfo` header | present | absent |
| fix | reduce footprint, wait | **log in** |

**The tell is the localStorage key count.** Same lesson, different costume:
`1x2_1h` graded on the full-time score looks like a normal settlement and
silently inverts the bet (HT 1-0 / FT 1-2 is a 1H *win*).

## 3. Editing a config file is not deploying it

`local/launchd/*.plist` is **source**. launchd runs the copy in
`~/Library/LaunchAgents/`. Editing the repo file changes nothing until you
`cp` + `unload` + `load`. Cost: **5.3h of dead real-money odds feed** on
2026-09-10, with the repo file already correct the whole time.

**Guard:** smoke `COOLBET-CDP-COOKIE-EXPORT` step 7 diffs installed vs repo and
fails on drift. Audit all at once:
```bash
python3 scripts/ops/launchd_drift_check.py --verbose
```
**The `diff -q` one-liner this replaced was actively harmful (2026-09-11).** It
byte-compared, so on 2026-09-11 it reported 7 lines of which **3 were false
alarms**: `best-price-router-monitor` and `unibet-site-odds` differed ONLY in
indentation (tabs vs 2 spaces — launchd parses plists as data and cannot see
whitespace), and `coolbet-mac-daemon` read MISSING because it had been
deliberately retired the day before. A guard whose output is mostly noise gets
skimmed, and the single real line — a stale INSTALLED plist — is exactly the one
that gets skimmed past. That is not a hypothetical: it is pattern #3 itself.

The replacement compares **parsed** plists (formatting invisible, and it names
the offending keys), skips `local/launchd/retired/`, and falls back to `plutil`
when Python's parser is stricter than Apple's. That last part matters: it found
`coolbet-ui-placer.plist` — the plist of the job that places **real money** —
carrying a `--` inside an XML comment, which XML forbids. Apple's lenient parser
accepted it so launchd ran it happily, while every strict tool went blind on the
single most safety-critical file. **A guard must never go dark on a file launchd
is running.** Exit code 1 on real drift, so it can page. Smoke:
`LAUNCHD-DRIFT-SEMANTIC`.

**Related (`MAC-PLIST-ORPHANS`):** running Mac jobs with **no repo plist at
all** — they exist only on the operator's Mac, so a dead disk loses them. Smoke
`COOLBET-CDP-COOKIE-EXPORT` step 8 now fails on any such orphan.

## 4. A second code path to the same money inherits none of the first one's gates

`place_coolbet_ui.py --execute` had seven per-pick gates. `best_price_router`,
written later to answer "which book is cheaper?", went straight from deciding
to placing with **one**. Enabling it would have moved real money onto the
*less* guarded path while looking like an upgrade.

**Guard:** the router now imports and reuses `place_coolbet_ui`'s own gate
functions — `already_placed` (fail closed), kickoff cutoff, `exposure_conflict`,
daily caps. **Reuse, never reimplement:** `canon_bet()` collapses the two market
vocabularies in `real_bets` (`'o/u'`+`'over 2.5'` vs `'over_under_25'`+`'over'`),
and a guard without it sees half the book and double-bets the half it cannot see.

## 4b. A gate whose only implementation lives in a research script is not a gate

**SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20.** §4 is about a second code
path inheriting no gates. This is its sibling: the gate was *written*, reviewed,
given a constant and a name — the "§9 outlier guard", `OUTLIER_MAX_RATIO = 1.25`
— and then lived only inside `scripts/anchor_book_sharpness_research.py`. The
`1X2-HOME-AWAY-INVERSIONS` queue row even said to *"verify that the guard is
actually applied on the placement path and not only in the line-shop
comparison."* Nobody did. It was not.

**Cost:** `bot_trigger_1x2_sharp_v1` published **+549.9% ROI / €1,319.80** on 24
settled picks, every one of them priced off a quote belonging to a different
fixture. `bot_trigger_ou_sharp_v1` the same, all 6.

**The tell** is the one this ledger keeps repeating in different clothes: *the
analysis script and the production path disagreed about what is possible, and
only the analysis script was ever run against reality.* A research script drops
the bad rows and reports a clean number, which is precisely why nobody notices
production is swallowing them.

**And a new one, specific to this shape.** There was ALSO a real production
guard — `pick_triggers.OUTLIER_MULT = 1.6` — live on the matcher engine the
whole time. It did not help, because `pick_generator._candidates_from_sharp` is
a **second implementation of the same sharp anchor** and the clone kept the
floor while dropping the cap. So this incident is §4 *and* §4b at once: a gate
in the wrong place, and a gate correctly placed on only one of two twins.

**Guard now:** three gates, in three different spaces (price ratio, edge, odds),
across both engines, pinned by smoke tests `ANCHOR-PRICE-SANITY` and
`SHARP-BOTS-HAVE-AN-EDGE-CEILING` — the second of which asserts the generator
*imports* `OUTLIER_MULT` rather than re-typing it, because a copied constant is
how the two paths diverged.

**The rule.** When a research script rejects rows as impossible, that rejection
is a finding about production, not a cleaning step. Either production enforces
the same rule or you are knowingly shipping the rows your own analysis refuses
to score. And when you clone a pricing path, diff the GATES, not the outputs —
the outputs of a clone that dropped a cap look *better*, not worse.


## 4d. A gate that fails open is missing exactly where the fault is worst

**1X2-HOME-AWAY-INVERSIONS-2026-09-22.** §4b is a gate in the wrong *place*.
This is a gate in the right place with a deliberate, documented, correct-looking
hole — and the hole and the fault turn out to be the *same population*.

`anchor_sanity.is_anchor_sane` fails open when Pinnacle has no line, and that
choice is right on its own terms: refusing every fixture Pinnacle skips would
delete most of the obscure-league coverage these bots run on, trading a known
fault for an invisible one. Its own docstring says so.

**But the fixtures Pinnacle skips are the obscure ones, and the obscure ones are
where fuzzy fixture matching goes wrong.** So the population the guard declines
to judge is enriched for the fault it exists to catch. Of the four paper picks
struck on a transposed 1x2 price, three were caught retrospectively by the ratio
test and **the one that was never caught — the only one still counted as a real
loss — is on Birkirkara v Hibernians, which has no Pinnacle 1x2 line at all.**
Seven other books priced it.

**A second, quieter version of the same shape:** the guard also had little
*power* where it did fire. Balzan v Sliema stored away at 3.40 against a true
2.12 — a ratio of 1.604 against a 1.5625 threshold. It passed by 2.5%. A
transposition is glaring in STRUCTURE (swap the sides and the whole triple
reconciles) and marginal in RATIO, so the statistic was simply the wrong one.

**The tell.** A gate's fail-open branch is usually justified by a *coverage*
argument ("we would lose too much data"), which is an argument about the
population's SIZE. Nobody checks whether that population's COMPOSITION is
different — and a guard that abstains on the risky half is not conservative, it
is absent.

**The rule.** When you write a fail-open branch, measure the fault rate *inside*
the abstention set, not just its size. If you cannot, add a second gate that uses
a different reference so the two abstain on different rows. Here that is
`mirror_guard`, which uses a **consensus of whatever books did price the
fixture** instead of one anchor, applies at **write** time instead of read time,
and tests **structure** instead of ratio — three independent choices, so the two
gates' blind spots do not overlap. Smoke `MIRROR-GUARD`.


## 4c. A cleanup is not done until the nightly repair job agrees with it

**SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20, second act.** 31 picks
priced off other fixtures' quotes were voided, the scoreboard went clean, and
the fix was reported as shipped. **By the next morning all 31 were back**,
`void_reason` cleared to NULL, and the bot was publishing +549.9% ROI again.

`settlement.resettle_wrongly_voided_bets` exists to undo WRONG voids — a good
job, doing its job. It skipped only `void_reason = 'quarantine'`, an exact match
on a bare magic string, so the only way to protect a deliberate cleanup was to
throw away its own explanation. Every quarantine that recorded WHY it happened
was silently reversible.

**The tell:** nothing alerted. The scoreboard simply read differently than it
had the night before. It was caught by a verification script returning **0 rows
where it should have returned 31** — i.e. by an assertion about the cleanup, not
by anything watching the system.

**Two rules.**
1. **Before you repair data, find out what else writes that column.**
   `grep -rn "UPDATE <table>"` over `workers/` takes a minute. A repair that a
   scheduled job disagrees with is a repair with a timer on it.
2. **Verify a cleanup on a later run, not on the write.** A `UPDATE ... ;
   SELECT` in the same breath proves nothing about durability. The smoke test
   added here executes the *competing* job's own query and asserts it returns
   none of the cleaned rows.

**And a corollary about precedent.** The `KAMBI-CRITERION-CONTAMINATION` rows
were cited as proof the void mechanism worked. They had survived only because
their matches are postponed with NULL scores, which the repair pass skips
anyway. *An outcome is not a mechanism.* Check why the precedent survived before
copying it.


## 5. A placement you cannot confirm is not a placement that did not happen

Three outcomes, not two. Collapsing the third into "didn't happen" causes a
double-bet, because the dedup reads `real_bets` and an unrecorded live bet is
invisible to it.

```
placed=True              confirmed by a balance delta
placed=False             confirmed NOT placed (balance read, unchanged)
placed=False + uncertain clicked, cannot tell -> exposure, never retry
```

Two real instances: `unibet_placer` reported `placed=False` when the balance
string failed to parse (bare except) *after* clicking "Tee panus"; and
`(balance or "0")` turned an unreadable balance into `0.0`, faking a
"balance unchanged" verdict with no exception at all.

**Guard:** uncertain placements record `store_real_bet(placed_real=None)` —
`match_exposure` counts `placed_real IS NOT FALSE` as exposure, so the retry is
blocked **without** claiming a confirmation we cannot evidence. Smoke
`UNIBET-UNCERTAIN-PLACEMENT`. **Principle: confirm by evidence, never by absence
of an exception.**

## 6. We are usually the cause of our own rate-limiting

The Coolbet feed died most days. The runbook always said the Imperva challenge
is *"usually triggered by our own request volume from one IP"* — we simply never
reduced the volume.

Measured 2026-09-11: the board sweep fetched **every** category's event list
each pass, then applied `--horizon-hours` — **802 events fetched, 215
near-term**, ~192 requests every 30 min with 73% of the payload binned. Passes
overlapped, so it was a continuous request stream at one bookmaker, all day.
FlareSolverr's own log shows the shape: a **fresh** session passes
("Challenge not detected!"), a **reused** one is challenged and times out.

**Guard:** `BOARD-SWEEP-NEARTERM-SKIP` remembers categories with nothing
near-term. **Do NOT "fix" this with more IPs or by rotating FS sessions** — that
dodges the detection and leaves the load, which is the actual problem.

**Bonus:** footprint and freshness were the same lever, not a trade-off. Quotes
were a median **197 min** old (p90 235) because a pass took so long — which is
what the placer's `drift` rejections actually were.

## 7. A cache that fails closed is worse than no cache

Every negative cache here (league prior, category memo) must:
1. **Never be permanent** — re-probe, or the zero becomes true by construction:
   we stop looking, so we never see the book add it, so it stays zero forever.
2. **Fail open** — a missing/corrupt memo sweeps everything. Failing closed
   silently stops collection and looks exactly like *"the book offers nothing"*.
3. **Never record an outage as a real absence.** 2026-09-06: a blocked search
   was written into `missed_leagues`, which would have taught us La Liga has no
   Coolbet coverage.

## 8. You cannot diagnose a rate-limit by retrying it

The only way to learn whether an Imperva flag had decayed was to run a sweep —
which is what sustains the flag. The question was unanswerable without making
the answer worse.

**Guard:** `coolbet_explorer --probe` — ONE request, reports
`ok` / `challenged` / `down`, and runs **above** the pause check so it works
while paused. `challenged` (wait) and `down` (our plumbing) are different
actions and must stay distinguishable.

## 9. A test that pins the old reality is worse than no test

Several smoke tests asserted things that had quietly become false, and each one
"passing" was actively misleading:

- the watchdog "must share the odds job's transport" — it makes **no** HTTP calls
- plists "must set `COOLBET_NO_FS=true`" — that setting was causing the outage
- the signaler "must import `_min_edge_for`" — replaced by `min_edge_for_pick`
- `lineshop` listed `Unibet-Kambi` as placeable in a **fallback** literal, under
  a comment claiming the two "can never drift"

**Rule:** when you fix a behaviour, grep for the test that pinned the old one.
And when asserting on source, **inspect code, not comments** — twice a comment
explaining a bug tripped the assertion forbidding it.

---

## 9b. Fixing one branch of a two-branch conflation guarantees a rerun

`coolbet_session_state.placement_paused` did two unrelated jobs: halt real-money
placement, and silence the customer `@oddsintelpicks` channel. In **August 2026**
the second job caused an outage — a daemon self-pause muted every Telegram signal
for 4 days / 12 picks. `SIGNAL-PAUSE-DECOUPLE` fixed it **for the self-pause
branch** and deliberately left the operator branch coupled, because "operator
`/pause` means full silence" sounded like a feature.

Five weeks later, **2026-09-14**: the OWN-path verdict set `placement_paused` via
migration 343 to close the automated-betting product. The surviving branch armed a
silent outage of the customer feed. It cost nothing only by luck of timing — the
last pick had been generated 23h earlier and the slate stayed flat — and the next
qualifying pick would have vanished with no error on any surface.

Three tells, all present both times:

- **The column name described one job, the code did two.** `placement_paused`
  never mentioned publishing; neither did the `/pause` help text, which promised
  "halt auto-placement until /resume".
- **The fix was scoped to the incident, not the conflation.** The August fix asked
  "was *this* pause a legitimate reason to mute?" instead of "should this flag
  decide muting at all?"
- **Nothing reported it.** `signaled_at` is stamped only when a send lands, so a
  muted pick and a day with no picks are the same row.

**Rule:** when one flag is found governing two decisions, split the flag, not the
branch. If a branch is genuinely wanted, give it its own switch with its own name —
here, `publishing_paused` + `/pausepicks`. And when the two decisions belong to
different **project directions** (🤖 OWN vs 👥 PICKS in `CLAUDE.md`), treat a shared
flag as a defect on sight: a decision about what *we* stake will eventually be
made by someone who is not thinking about what *readers* see.

---

## Open, and worth closing to call this stable

| Item | Why it matters |
|---|---|
| `MAC-PLIST-ORPHANS` | 3 running jobs unreproducible from git |
| Router dry-test never run green | the Coolbet arm was dead the whole time; `route(stage=True)` has never completed end-to-end |
| Coolbet arm stores no routing note | book-choice analysis is Unibet-only today |
| `ROUTER_ALLOW_REAL` | owner gate — deliberately unset |

## 10. The remedy that guarantees the fault persists

**2026-09-11.** Coolbet's odds feed was "blocked by Imperva" for 15 hours. It
was not blocked. Imperva served its ordinary JS challenge — HTTP **200**, ~900
bytes, `_Incapsula_Resource` — which **self-resolves on the next request to the
same browser session**. We had no retry, so the challenge page was returned to
callers as the answer.

**The diagnosis then closed the loop on itself:**
1. Feed fails -> "Imperva escalation, our request volume is the cause."
2. Remedy -> pause the footprint and wait for the flag to decay.
3. `--probe` to check -> every probe is a FIRST request on a fresh context, so
   it always gets the interstitial and always reports CHALLENGED.
4. Still challenged -> keep waiting. Go to 2.

**The pause was the one thing preventing recovery**, because the only thing that
clears the challenge is making a second request. Every piece of evidence
confirmed the theory, and the theory prescribed the action that sustained the
symptom.

**The tell we had all along and did not look at: HTTP 200.** A block is a 403 or
a ~9-char `STAY COOL` body. We had a 200 with a 900-byte body for 15 hours and
never printed it. The fix took 4 lines once the body was on screen.

**Guard:** `INCAPSULA-SELF-RESOLVES` + runbook §1b, which says explicitly *do
not pause the feed* for this signature.

**The pattern, stated generally:** when a remedy is "stop doing the thing and
wait", ask what evidence would distinguish *recovering* from *never having been
broken*. If the check you are using to decide is itself suppressed by the
remedy, you cannot tell the two apart — and you will wait forever. **Print the
raw response before theorising about who is blocking you.**

## 11. A number that looks impossible is usually stale, not wrong

Added 2026-09-11 after making this mistake twice in one morning while auditing
the data-task backlog.

`DB-RETENTION-POLICY` claimed `match_signals` held **45,807,706 rows at 30.0x
duplication**. Measured, it held 3,381,601 at 2.07x. The gap was so large that
the obvious inference was a measurement error — and it happened that
`odds_snapshots` sat at 46.2M, close enough to 45.8M that "the ticket measured
the wrong table" looked like the answer. It was filed as exactly that.

**It was not a measurement error. The number was correct when written, and a fix
had shipped in between** — `SIGNALS-STORE-ON-CHANGE-2026-09-03` stopped the
re-insertion and `scripts/prune_match_signals.py` reclaimed the history. The
ticket was not wrong, it was *done*.

**The tell:** an old ticket quoting a figure that today's data contradicts by an
order of magnitude. **The guard:** before concluding a past measurement was
wrong, run `git log --since=<measurement date> -- <the relevant paths>` and read
what shipped. A stale ticket is far more common than a bad measurement, and the
two have opposite fixes — one is "close it as done", the other is "re-measure
and correct the premise".

The same pass made the inverse error on `DB-ANCHOR-GROWTH`: total inflow (~1.8M
rows/day) was reported as the permanent-anchor growth the ticket was actually
about (484k/day), turning 76 GB/yr into a claimed 310. **Both errors share a
cause — comparing a fresh measurement against an old claim without first
checking that the two measure the same thing.**

## 13. An alerter's own bookkeeping is production code

2026-09-11: `odds_freshness._set_dedup_row` wrote a column that has never
existed (`last_reason`; migration 258 created `last_alert_reason`). The write
raised on every call, the module's broad `except` logged it as a warning, and
the DB dedup layer for the Epicbet watchdog was **dead from the day it
shipped** — `pipeline_health_state` never held a single `epicbet_odds` row.

Nothing looked broken, because the alert still *sent*. What broke was the part
that decides **how often** — and that half has no output of its own to check.

Three properties that make this class invisible:

1. **The broad `except` that keeps the alerter alive also hides its own
   failures.** `check_feed` is deliberately written to never raise ("an alerter
   that dies takes the alerting with it") — correct, and it means a bug in the
   alerter is a log line nobody reads, not an incident.
2. **The fallback dedup was in-process.** `send_telegram`'s `_LAST_SENT` is a
   dict, wiped on every restart — which is *exactly* the failure mode migration
   258 was created to survive. The scheduler restarted **4 times in 90 minutes**
   on routine deploys that day, so the true alert re-fired per restart.
3. **A dedup marker that is never written can never be cleared**, so the
   `✅ recovered` path was unreachable too. The bug hid its own recovery signal.

The operational cost is not a missing alert — it is the opposite. A real outage
alerts so often that the operator learns to ignore the channel, which is how the
next real one gets missed.

**Guard:** smoke `ODDS-FRESHNESS-DEDUP-COLUMN` pins both halves — the SQL may
name only columns present in the live `information_schema`, **and** the writer
is exercised and round-tripped, because valid-but-wrong SQL passes a
source-only check. When adding a healthcheck, the dedup write is not plumbing
to be eyeballed; test it like the alert itself.


## 13b. A remedy that resolves its target differently from the detector

2026-09-21 (`COOLBET-WEDGE-SELFHEAL-NEVER-FIRED`, found while triaging
`COOLBET-WEDGE-RECURRENCE-WATCH`). The Coolbet feed watchdog's wedge self-heal
shipped 2026-09-18 to bound a wedged FlareSolverr session at ~30 min. It had
**never once fired**: 16 `WEDGED_SESSION` verdicts on 09-19/20, 16
`fs_session_destroy_failed`, zero successes — on the price basis of the only
book we stake real money at.

The detector and the remedy resolved the same dependency two different ways.
The **detector** probes through `coolbet_explorer.probe_coolbet_reachable` →
`coolbet_session._fs_call`, which tries `COOLBET_FS_LOCAL_URL` first. The
**remedy**, `_destroy_fs_session`, hand-rolled `os.getenv("FLARESOLVERR_URL")`.
On the operator's Mac — the only host that runs this watchdog — that variable
still holds the pre-RAILWAY-ELIMINATION Railway host, which returns 404.

Why it survived both review and three days of logs:

1. **The diagnosis was perfect.** Every one of those 16 lines names the session,
   the byte count, the elapsed time, and correctly distinguishes a stuck session
   from an Imperva verdict. A log that precise does not read like a broken
   remedy.
2. **Recovery still happened**, via the next sweep building a fresh session. The
   feed always came back, so the outcome the self-heal was written to produce
   occurred anyway — just on the old, slow path it was meant to replace. The
   *benefit* was missing, not the service.
3. **The failure string was in the log the whole time.** `fs_session_destroy_failed`
   appeared 16 times and nobody was counting; the row that would have caught it
   was filed as a "watch for recurrence in ~2 weeks" reminder.

**The generalisable rule:** when a healthcheck can both *detect* and *fix*, both
halves must reach the target through the same resolver. A detector that is right
while its remedy is wrong is worse than one that is wrong outright — it produces
confident, specific, accurate incident logs describing a fix that never happened.

**Guard:** smoke `COOLBET-WEDGE-SELFHEAL-REACHES-THE-RIGHT-FS` stands up a fake
FlareSolverr on each candidate URL and asserts the destroy hits
`COOLBET_FS_LOCAL_URL` first — behavioural, because a source-only check cannot
tell which host a URL names. It also asserts `coolbet_prod` is refused before any
request leaves the process.

### 13c. …and the same shape again: a remedy chosen by symptom, never by history

2026-09-21, found doing `CDP-SELFHEAL-CANNOT-ESCALATE` on the same day as §13b,
which is the point of recording them together.

The Coolbet CDP self-heal picks a repair tier from the current symptom. It has
no memory, so a tier that cannot possibly work is re-chosen every 30 minutes
indefinitely — **auto-login failed 105 times**, the last 8 consecutively over
3.5h with an identical error, while the two escalation tiers were unreachable by
construction.

Unreachable how: `cdp_up` is an HTTP GET on `:9222/json/version`; every remedy
drives the browser through the patchright CDP driver. Those answered differently
— 200 with a Chrome version string, versus *"Frame was detached"*. So the
browser was UP to the chooser and USELESS to every repair, `cdp_up=True` blocked
relaunch, and the probe failure left `walled=None`, blocking re-bootstrap.

**Two rules, and the second is the transferable one:**

1. A health flag must answer the question the REMEDY asks, not the cheapest
   question available. `diagnose()` now reports `cdp_usable` (the driver can
   attach) alongside `cdp_up` (something answers the port).
2. **Which remedy to try is a function of the symptom; whether this remedy works
   is a fact only the history knows.** Any self-heal that re-derives its choice
   from scratch each tick can loop forever on a remedy that has never once
   succeeded — and will look busy the whole time.

Escalate after 3 consecutive failures of the same tier, not 1: the ladder has to
tolerate a transient, or one timed-out page triggers a multi-GB profile re-copy.

**Guard:** smoke `CDP-SELFHEAL-ESCALATES` drives the ladder against a synthetic
lifecycle log — escalates at 3, resets on success, counts per-tier and only
consecutively, and treats a rate-limit skip as "no attempt" rather than "failed
attempt".

## 12. A column filled by a nightly backfill can never be a model feature

Added 2026-09-11, after two features were proposed for the production ensemble
on the same false premise and neither could have been served.

* `pinnacle_drift_*` is `1/closing_odds − 1/opening_odds`. It needs the CLOSING
  price, so it cannot exist when we predict. **100% missing at inference.**
* the `*_at_t6h` family looked safe by contrast — genuinely pre-kickoff
  quantities. But they are populated by `job_nightly_mfv_b_ml3_refresh` at
  **22:30 UTC, for matches that finished that day**, precisely because the live
  MFV builder has "ordering issues with T-6h snapshot availability at build
  time" (its own comment). Measured: MFV rows are built a median **14.7 hours**
  before kickoff and only **166 of 4,030 (4.1%)** inside T−6h — so **~96%
  missing at inference.**

Training on either fits coefficients in the presence of information the model
will never have at serve time, and biases every *other* coefficient too. The
`<col>_missing` indicator handles *informative* missingness (weather runs ~50%
missing and is legitimate); it does not rescue a feature that is absent almost
always.

**The tell:** the feature's writer is a `scripts/backfill_*.py` on a cron, or the
column's only non-NULL rows belong to finished matches. **The guard:** before
adding any feature, ask which code path populates it *for an upcoming match*,
and measure its non-NULL rate on rows built before kickoff — not on the table as
a whole. Where the quantity is genuinely pre-kickoff, compute it live at serve
time instead: `PIN-CROSS-DRIFT-T6H-LIVE` did exactly that for the veto, sourcing
drift from `odds_snapshots` rather than the MFV columns.

Corollary worth checking whenever a post-hoc column has a live consumer: the
meta-model scores at pick time and reads three `*_at_t6h` columns, and
`meta_clv_score` lands on only **13.8%** of shadow picks.

---

### OU-CALIBRATOR-DOMAIN-MISMATCH — a calibrator fitted on one quantity, applied to another

**Added 2026-09-13** after the owner asked why a day of picks went 2-for-16 and the
bankroll "dropped so hard".

**The tell:** a sudden, large change in *pick composition and volume* with no model
version change. `bot_v10_all` went from 14.6% O/U share at +38.5% ROI (Aug 1 – Sep 2)
to **69.9% O/U share at −15.0% ROI** (Sep 3 – 13), ~3 bets/day to ~12 bets/day, peak
+€756.06 on 09-06 → +€289.30, a **−€466.76 drawdown with −€449.07 of it in one week**.
The model head never changed.

**The cause.** `scripts/fit_calibration_from_predictions.py:186` fits the Platt curve on
`predictions.model_probability` — the RAW ensemble probability.
`workers/model/improvements.py:227` applies it to `shrunk`
(`alpha * model_prob + (1 - alpha) * pinnacle_devig`), which is ~90% Pinnacle once
odds > 3.0 because `CAL-ALPHA-ODDS` floors alpha at 0.10. **Fitted in one domain,
applied in another.**

**Why validation could not catch it.** The script ships a fit only when it beats raw
out-of-sample on ECE — a genuine, honest check. But it measured the curve *on the raw
probabilities it was fitted on*, i.e. **a function production never executes**. Its ECE
improvement (under25 0.0817 → 0.0454) was real and irrelevant. This is the same trap
`_fit_platt`'s own docstring documents for logit-vs-probability, reached from the
opposite direction.

**Why the damage was shaped the way it was.** The shipped under-2.5 curve is
`sigmoid(1.5258·p − 0.8341)`: total output range **[0.3028, 0.6663]**, fixed point
**0.4713**. Everything below 0.4713 is inflated, and long-priced unders are all below it.
Live rows: input sd 0.2209 → output sd 0.0823, a 2.7× compression. So
`edge = cal_prob − 1/odds` degenerated into *"how far is this price from ~0.45"*, which
is monotonically maximised by the longest price on the board. **An 8% edge floor became
a longshot-finder.** Against 113,506 Pinnacle under-2.5 quotes on finished matches since
08-01: at market-implied 0.316 the curve says 0.413 while the actual rate is 0.271 —
a reported +9.7% edge on a true −4.5%. The market is well calibrated; the curve is a
horizontal line drawn through it.

**The guard now in place.** `fit_calibration_from_predictions.py` refuses `--apply`
outright until it is reworked to fit on `shrunk`, and carries a range guard that rejects
any curve whose output span collapses (`MIN_RANGE` / `MAX_FLOOR` / `MIN_CEILING`).
Migration 335 removed the rows (`apply_platt` is a graceful no-op with none) and
preserved them in `model_calibration_ou_domain_mismatch_backup`. Smoke tests
`OU-CALIBRATOR-DOMAIN-MISMATCH` (×3) fail CI if the rows return, the guards are removed,
or the real-money O/U bot is re-enabled.

**The generalisable lesson — two of them.**

1. **A calibrator must be fitted on the exact quantity inference passes it.** Not a
   correlated quantity, not the pre-shrinkage version. If you cannot state which
   variable production hands the curve, you cannot validate the curve.
2. **Average ECE is the wrong loss for a curve that only feeds a tail gate.** The fit was
   ECE-optimal over the whole universe and maximally wrong exactly where the 8% floor
   bites. Validate on the **selected** subpopulation (`edge ≥ floor`), not the universe.

**Measured alternatives** (`scripts/ou_calibrator_backtest.py`, held-out slice, TEST
n=8,963 candidates, stale-best-odds guarded):

| arm | picks | claimed | actual | gap | ROI | CLV vs Pinnacle close |
|---|---|---|---|---|---|---|
| live (broken) | 488 | 44.2% | 30.1% | **+14.1pp** | −11.3% | **−1.57% (t=−2.4)** |
| no calibrator | 48 | 53.0% | 47.9% | +5.1pp | +18.0% | **+24.72% (t=+5.5)** |
| refit on `shrunk` | 47 | 53.6% | 51.1% | **+2.5pp** | +24.6% | **+25.49% (t=+5.6)** |
| refit + odds (2-feature) | 1 | — | — | — | — | unusable, n=1 |

ROI t-stats at n≈48 are not significant and are not the basis for the decision — **CLV
is**, per this repo's own ~334-vs-9,300 sample rule. Note the volume: the broken curve
produced **10× the picks**. A 90% drop in O/U pick count after the fix is the fix
working, not a regression.

⚠️ **Caveat on the +24.7% CLV magnitude.** The backtest takes each book's *last*
pre-match quote (a STALE-BEST-ODDS guard — a first run without it showed inflated
+19–24% ROI). A dead feed's last quote can still look live (`ODDS-NO-MAX-AGE`), so treat
the *direction and significance* as robust and the *magnitude* as optimistic.


---

## A retirement that only changes the DB, while the code still resolves the bot

**Found 2026-09-14, while retiring three model-anchored 1x2 bots.**

Both generation paths resolved a bot name with a bare lookup:

```sql
SELECT id FROM bots WHERE name = %s      -- no retired_at check
```

`workers/automation/pick_generator.py:_bot_id` and
`workers/jobs/pick_trigger_matcher.py:_bot_id`, independently, the same shape.

So setting `retired_at` removed a bot from `/admin/shadow-bots`, from the
registry, from every dashboard — **and it carried on writing `shadow_bets`
underneath**. Every retirement migration in this repo's history had this
property. The bot looked dead from every surface a human checks.

**The tell:** a "retired" bot whose settled-pick count keeps rising. Nothing
errors, nothing alarms, and the page that would show you is the one place the
bot no longer appears.

**Why it survived so long:** retirement was always verified by looking at the
thing that hides retired bots. The check and the bug shared a blind spot.

**The guard:** both lookups now require `retired_at IS NULL`, so a DB retirement
is self-enforcing and needs no code edit to take effect. Smoke test
`RETIRED-BOTS-KEPT-GENERATING` pins both. The analogous gap in the placer's
`load_picks` is still open and is flagged in `docs/SYSTEM_MAP.md` §4c.

**The pattern, generally:** when a kill switch lives in one system and the thing
it kills lives in another, verify the kill at the *target*, never at the switch.

---

## A test that monkeypatches a shared module and never restores it

**Found 2026-09-14.** `BEST-PRICE-ROUTER-EXECUTE-WIRING` replaced
`best_price_router._dispatch_unibet` and `._dispatch_coolbet` with lambdas to
test routing without a browser, and left them replaced. The module object lives
for the whole process, so every later test that read those functions got a
lambda. `ROUTER-REAL-MONEY-CUTOVER` — which asserts the unverified-click path
records `placed_real=None`, a real-money safety property — was reading the
lambda's source and failing.

**The tell, and it is the nasty part:** it passed under `--filter` and failed
only in a full-suite run. So it looked like a regression introduced by whatever
commit happened to run the full suite next, and the obvious debugging move
(re-run that one test) reports green.

**The guard:** save and restore in a `try/finally`. This is the second instance
of "a test broke another test" in this file; the rule is that any smoke test
mutating shared module state or `os.environ` must restore it — see also the
`ROUTER_ALLOW_REAL` entry, where `os.environ.pop()` let `load_dotenv()` silently
re-populate the flag the test existed to verify.

---

## A circuit breaker whose "working" state is indistinguishable from the fault

**Found 2026-09-14, from a `🔴 coolbet_health_ping stuck` alert.**

The Coolbet health ping has a breaker: when no usable JWT is stored it does
**not** put a request on the wire, because a retry loop into the Imperva wall is
what sustains a lockout (runbook 2/7). Correct design.

But the skip returned `ok: False` and fell through to `exit 1` — the same exit
code as "Coolbet is down". The scheduler recorded `status=failed` in
`pipeline_runs` and pushed `status=down` to Kuma. **The breaker doing its job was
reported as the failure it exists to prevent.**

Measured over 9 hours: **26 recorded failures — 14 were one real outage
(20:20–21:25, self-healed), and the other 12 were isolated skips at :55 and :00**
as the 30-minute JWT rolled over. All four feeds were writing normally the whole
time (Coolbet 16,817 rows/hr).

**The tell:** failures in a clean periodic pattern, at the same minutes past the
hour, interleaved with successes. An outage is contiguous; a rollover is
rhythmic. `XXXXXXXXXXXXXX.....XX......................XX..........XX` — the first
run is an incident, everything after it is a clock.

**The guard:** exit 3 = skipped on purpose, and the scheduler records it as
completed. The skip stays fully visible — `mark_heartbeat()` records it and
`scripts/ops/status.py` reports JWT age independently — so a JWT genuinely dead
for hours still shows. Smoke test `HEALTH-PING-SKIP-IS-NOT-A-FAILURE` pins both
halves, including that a real failure is STILL recorded as failed (that was
itself a fix for a silent-failure trap and must not be undone by this one).

**The pattern, generally:** a monitor must distinguish *"I could not measure"*
from *"I measured, and it is bad."* Collapsing the two costs you the alert
channel — an operator who sees the same red every night stops reading it, and
that is exactly when the real 65-minute outage goes unnoticed.

---

### A value that is available at training time but not at prediction time

**Added 2026-09-14 (ELO-FORM-LEAK).** The fourth instance this year of one shape:
*computed correctly, consumed at the wrong moment.*

**The tell.** A feature, model or metric that performs impossibly well. Not
"surprisingly" well — **impossibly**: better than a benchmark that has strictly
more information. `elo_diff` scored AUC 0.7396 against a de-vigged market at
0.7270. A rating built only from past matches cannot out-predict a market that
knows everything the rating knows plus team news, lineups and money flow. The
number was not too good to be true; it was *logically impossible*, and it sat in
production for four months.

**Why nothing caught it.** Every offline evaluator scored the leaked feature and
reported a strong model. The leak was invisible to all of them because they all
read the same contaminated table. **A shared input is a shared blind spot** —
adding more evaluators against the same source adds no coverage.

**Related instances, same shape, all 2026:**

| | What was computed | When it was consumed |
|---|---|---|
| `WEEKLY-EVAL-OU-INVERTED` | over-2.5 probability | scored against P(under) |
| `1X2-CLASS-ORDER-INVERTED` | correct class probabilities | read by position, inverted |
| `OU-CALIBRATOR-DOMAIN-MISMATCH` | a curve fitted on raw probs | applied to shrunk probs |
| `ELO-FORM-LEAK` | post-match ELO | read as a pre-match feature |

**The guards that now exist.** `LEAKAGE-CANARY` (a benchmark no pre-match feature
may beat), `MODEL-OUTPUT-CALIBRATION` (scores what production actually emitted,
so it cannot share an evaluator's blind spot), and `ELO-FORM-LEAK` (asserts the
date bound both in source and against live data).

**The generalisable rule.** For every input, ask *when* its value becomes known,
not just whether it is correct. A date-bounded read of a table that is written
after the event is the specific pattern; `<=` where `<` is meant is the specific
bug. Any join to a table updated post-match deserves this question, and the
answer belongs in a comment next to the bound.

---

### Rebuilding a derived table from a pruned source is lossy, and silently so

**Added 2026-09-14 (MFV-REBUILD-DESTROYS-PRUNED-SIGNALS).**

`match_feature_vectors` was rebuilt to remove the ELO leak. The rebuild worked —
`elo_diff` AUC fell 0.7536 → 0.6134 — and in the same pass destroyed four
features it was never meant to touch:

| column | before | after | lost |
|---|---|---|---|
| `goals_for_avg_home` | 37,152 | 14,970 | **−59.7%** |
| `goals_against_avg_home` | 37,152 | 14,970 | −59.7% |
| `goals_for_avg_away` | 37,423 | 15,113 | −59.6% |
| `goals_against_avg_away` | 37,423 | 15,113 | −59.6% |

Those columns are not computed by the builder — they are **read through** from
`match_signals`, which `SIGNALS-STORE-ON-CHANGE` and `prune_match_signals.py` had
collapsed from 49.3M rows. The rebuild read the pruned present and wrote NULL over
data it could not reconstruct.

**The tell is that there isn't one.** The job reported success, upserted 49,509
rows across 134 dates, and the row count was unchanged. Every surface said healthy.
Only a **column-level coverage diff against a pre-rebuild snapshot** revealed it.

**Two guards now exist.**
1. `bulk_upsert(..., coalesce_columns=[...])` — listed columns are never downgraded
   to NULL by an upsert. The MFV builders pass the 25 signal-sourced columns.
2. `MFV-SIGNAL-COLUMNS-NOT-DRIFTED` — asserts the list is wired into *both* the bulk
   and the one-by-one fallback path, and that it still matches what the reader
   assigns.

**The distinction to carry elsewhere: COMPUTED vs READ-THROUGH.** A computed
column must be allowed to become NULL — "we now know there is no honest value
here" is a real result, and suppressing it would have undone the ELO fix in the
same commit that made it. A read-through column from a prunable source must not:
its NULL means "the source no longer holds it", which is not a fact about the
match.

**And the operational rule:** take the snapshot *before* a rebuild as routine, not
as a precaution. Here the backup was worth more as a **measuring instrument** than
as a rollback — without it the loss would have reached a retrain unnoticed.


## 14. Two safety reads pointing in opposite directions — and an env var's absence is not a pause

**2026-09-15, OWN-ARMED-UNDER-PAUSE.** Real money was "paused" (migration 343) and
the stack was armed underneath it in three ways nobody had listed:

1. `is_placement_paused()` fell **OPEN** on a DB error ("the system is more useful
   running than paralysed") while `ui_place_enabled_bots()` 200 lines away fell
   **CLOSED** ("cannot read the toggle ⇒ place nothing"). Same money, two
   reads, opposite defaults. Whichever one a given executor happened to call
   decided whether a DB blip staked or refused.
2. The router's "owner gate" was the **absence** of `ROUTER_ALLOW_REAL`. It was
   present — in `.env`, loaded by `db.py` on import — so the router ran in real
   mode every 30 minutes and staked nothing only because its pick loader found
   zero candidates. Two audits in two days recorded it as "inert, env var unset".
3. A third executor (the VPS manual-place drain, every 10 s) never read any
   pause at all; it was paper because `execute=False` was a literal in two call
   sites.

**Tell:** more than one function answers "may money move?", or the answer
depends on something not being set.

**Guard:** one `assert_may_place()` in `placement_gate.py`, fail-closed on every
read, called FIRST by every executor, with `real_money_armed` (mig 354) as an
explicit default-FALSE arming switch and a source-position smoke test
(`PLACEMENT-GATE-ALL-EXECUTORS`) that fails if any executor acts before it asks.
`coolbet_control --status` now reads the OS (loaded `--execute` agents, the env)
and ends with one line: `CAN_STAKE: yes/no`.

**Sibling found the same day — a FK that silently ate the ledger.**
EDGE-PCT-TAKEN-RECORDED (09-13) made the UI placer pass its `shadow_bets` pick id
into `real_bets.simulated_bet_id`, a FOREIGN KEY to `simulated_bets`. Every
confirmed placement after it raised on the ledger INSERT and was logged as
"placed but could not write real_bets". Money moved; the ledger stayed silent.
Guard: `store_real_bet` routes the id to the table that holds it
(`shadow_bet_id`, mig 354); `REAL-BETS-ATTEMPTS-RECONCILED` fails if any
confirmed attempt lacks a ledger row.

---

## 15. A whitelist the new writer never knew about — and "0" as a plausible number

**INPLAY-SLOWSTATE-COHORT-REJECTED, found 2026-09-18, live since 2026-09-15.**

Phase 1b shipped the in-play slow-state rig complete: a collector on the Mac
under launchd KeepAlive, two bots (`bot_inplay_slowstate_v1` + an AF control
arm), migration 357's `inplay_book_quotes`, a heartbeat with a consumer in
`health_alerts`, and five smoke tests. It collected 45,489 board snapshots
across 276 fixtures in three days. It recorded **zero picks**, and every part of
it looked healthy while doing so.

`shadow_bets.shadow_cohort` carries a CHECK constraint whitelisting cohort
strings. Nobody extended it for `'inplay_slowstate'`, so **every** pick INSERT
raised `CheckViolation` from the rig's first cycle. Measured cost: trigger T1
(0-0, 35'–54', under 2.5 at ≤ 2.20, unsuspended) was satisfied at **1,501
instants across 63 fixtures**, T2's window on 69 more — all dropped. The whole
in-play question waits on this rig's n, and its n was structurally pinned at 0.

**Why three days of "picks 0" read as normal.** `write_pick` catches the
exception, logs a warning and returns False. The cycle line then reports
`picks 0`, which is *exactly* what a genuinely quiet slate looks like — and a
slate where no trigger fires is common enough to be unremarkable. The warning
did fire, 2,694 times, into a log nothing alerts on. The heartbeat was green
throughout, because the collector really was collecting; only the recording
half was dead.

**Tell:** a new writer targets an existing table whose constraints predate it —
especially an enum-like CHECK, a FK, or a partial unique index. And, separately:
a success counter whose zero value is indistinguishable from a legitimate quiet
period, with the failure path counted nowhere.

**Guard:** migration 362 adds the cohort. `INPLAY-SLOWSTATE-COHORT-ACCEPTED`
extracts the cohort literal from the collector's own INSERT and asserts it
appears in the *body* of the newest `shadow_bets_shadow_cohort_check` (a prose
mention in the migration comment does not count — the first version of this test
passed its own mutation check for exactly that reason). `PICK_WRITE_FAILURES` is
counted and printed on every cycle line as `pickfail`, pinned by
`INPLAY-PICK-WRITE-FAILURES-VISIBLE`, so "collecting but recording nothing" can
no longer present as a quiet day.

**The general rule this is the third instance of:** when a component's healthy
state and its failed state produce the same observable, the observable is not
monitoring. See also §"A circuit breaker whose 'working' state is
indistinguishable from the fault" and the `real_bets` FK above — same shape,
three different subsystems.

---

## 16. A safety test that passes or fails on thread scheduling is not a safety test

**ROUTER-ENV-LOCK, 2026-09-18.** CI went red on a commit that touched only the
Coolbet in-play collector, with a failure in an unrelated real-money test:

```
GATE-STATUS-READS-THE-SAME-ENV
AssertionError: gate_status reported router_allow_real_env=True
                for ROUTER_ALLOW_REAL='' — expected False
```

Nothing in the commit went near the placement gate, and the test passed in
isolation locally. The cause: the smoke runner executes tests in a
**ThreadPoolExecutor**, `os.environ` is process-wide, and **three** tests mutate
`ROUTER_ALLOW_REAL` — one sweeping it through six values, one setting `"false"`,
one setting `"1"`. Each was individually correct and each restored in `finally`.
Concurrency alone made them wrong: one test read another's write mid-sweep.

**The commit did not introduce the bug — it introduced four unrelated tests,
which shifted the scheduling enough to make a latent race land.** That is the
worst property of this class: the blame points at whoever was standing nearby.

**Tell:** a test that mutates process-global state (`os.environ`, a module
attribute, CWD) under a concurrent runner. Restoring in `finally` does not help —
the window is between the write and the read, not after.

**Guard:** `_ROUTER_ENV_LOCK` in `scripts/smoke_test.py`, held by all three
mutation sites. This is the second instance of the same shape in three days; the
first was `_PUBLISHER_PATCH_LOCK` (a monkeypatched shared module, 2026-09-15),
documented a few lines above it in the same file. **When you add a third, the
answer is probably to stop sharing the state rather than to add a third lock** —
per-test subprocesses, or a runner that serialises anything touching globals.

**Related, and the reason this one bites so hard:** the same file already carries
a comment explaining that this test's failure "only reproduces in a process that
has not already imported db.py — which the smoke suite has". A test whose
correctness depends on what *else* the process has done is one scheduling change
away from lying in either direction.

---

## 17. HTTP 200 is not proof of content — and a challenge page reads as health

**IMPERVA-FLAG 2026-09-18/19.** Coolbet's feed died at 18:15 UTC and stayed dead
12.5h. It was diagnosed three times as a **wedged FlareSolverr session** and
treated accordingly — sessions destroyed repeatedly, then the FS container
restarted. All of that was the **opposite of the correct remedy**, because the
real cause was an Incapsula flag on the IP, and cycling sessions at a live flag
hardens it (runbook §7).

Three separate instruments agreed on the wrong answer:

| instrument | what it said | what was true |
|---|---|---|
| `scripts/diagnose/flaresolverr.py` | `✓ coolbet.com HTTP 200, 6,078 bytes` | those 6,078 bytes **were** the `_Incapsula_` challenge |
| `probe_coolbet_reachable` | `state=wedged` … *"that is a stuck session, not a challenge verdict"* | it had logged `Incapsula interstitial` one line earlier |
| the odds sweep | `fo-tree fetch failed … HTTP 500` ×3, every 30 min | 992 bytes of `Request unsuccessful` — a block, being retried 144×/day |

**The bytes were always there to read. Nothing looked at them.** One request
settled it in 70 seconds once asked properly — fetch the homepage and the API on
a brand-new session and *inspect the body*: 6,078 bytes of Incapsula JS, 992
bytes of "Request unsuccessful", against Epicbet's 1.59 MB of real page from the
same host. That last control is what proves it is the target and not the network.

**Tell:** a 200 whose body is far smaller than the page should be; any instrument
that reports a status code without asserting on content; a "wedged" verdict on a
**brand-new** session — a session that has never been used cannot be stuck, so
that verdict is self-refuting and names the IP as the problem.

**Guard:** the diagnostic now fails a 200 whose small body carries a challenge
marker, and says explicitly not to cycle sessions at it
(`FS-DIAGNOSTIC-DETECTS-CHALLENGE`). The size condition is part of the test —
marker-only matching flagged a 1.18 MB real page containing the word "captcha",
and a false RED in an incident tool is barely better than a false green.
`COOLBET-PROBE-CALLS-IMPERVA-A-WEDGE` is filed for the probe's own verdict.

**And the cause, which is the uncomfortable part:** the flag followed a
same-day collector that added ~30k requests/day to this IP's Coolbet footprint.
The footprint risk had been raised in review that afternoon and answered with a
manual pause switch. A switch nobody is watching is not a control. **Retrying is
not diagnosis** (§8) — 144 blocked requests a day is very likely why 12.5h
produced no decay.


## 18. A column's declared precision is part of the gate

**2026-09-22, EDGE-IS-DERIVED-NOT-STORED (queue #031).** `simulated_bets.edge_percent`
was declared `numeric(5,2)`. The writer was never wrong: `store_bet` passed the exact
edge the bot had gated on. Postgres rounded it to two decimals on the way in, silently,
with no error, no warning and no log line — and two decimals on a probability difference
is a granularity of one whole percentage POINT.

**Why that became a money bug rather than a display nit:** every per-market edge floor is
itself a two-decimal literal (1x2 0.13, 1x2 home-underdog 0.10, o/u 0.08, AH/DNB 0.05).
Round-half-up plus a two-decimal floor makes `stored >= floor` true across the entire
band `[floor − 0.005, floor)`. **114 picks all time (26 in 90d, 14 in 30d) cleared a floor
their real edge missed. Zero were wrongly rejected** — the error could only ever run in
the flattering direction, which is also why nothing ever complained. The consumers were
the Telegram signaler (the operator's placement prompt AND the public customer channel)
and the pre-kickoff catch-net.

**It had already been noticed and mis-attributed.** `ANALYSIS_GOTCHAS` 48 recorded "it is
also ROUNDED" months earlier without asking WHY, and the ticket that filed this diagnosed
it as a price mismatch — "the stored edge corresponds to a price of 2.5113" — because a
0.0766 edge stored as 0.0800 *does* imply a different price if you assume the arithmetic
is right and the storage is lossless. The measurement that settled it took one query:
99.7% of divergences were ≤ 0.005, which is the signature of `round(x, 2)` and of nothing
else.

**Tell:** a derived quantity that has its own column; a gate threshold and a stored value
that share a decimal place count; a divergence distribution whose maximum is suspiciously
close to half a unit in the last stored digit. Ask `information_schema.columns` for the
scale before theorising about the writer.

**Guard:** the column is `numeric(6,4)` (migration 367), `store_bet` derives the value it
writes from the price and probability in the same row, and **every gate re-derives on
read** (`coolbet_placer.model_edge`) — the last of which is the part that generalises:
a gate that derives cannot be fooled by any future cause of drift, precision or otherwise.
Smoke `EDGE-IS-DERIVED-NOT-STORED`. Historical rows were deliberately NOT backfilled:
rewriting `edge_percent` would rewrite what each bot is recorded as having cleared, which
is the evidence base for every floor we have set.

## 19. A floor and a ceiling, each correct alone, that together admit nothing (2026-09-22)

**The tell:** a bot with `is_active = true` whose emission count is not falling but
*flat at a small number*, then zero — and no alert, because nothing failed. Every job
ran, every job logged `completed`, and the gate that stopped it is the gate doing its
job.

**What happened ([[#007]]).** The sharp trigger bots set `edge_floor = 0.03`
explicitly, and `bot_configs.py` carries a ⚠️ comment saying exactly why a model floor
must not be inherited: their probability is a de-vigged Pinnacle line, so a 13% floor
"would demand a 13% overlay on Pinnacle, which is nearly unobservable (max seen +6.6%),
and the bot would silently never fire." The comment was right, the intent was right,
and the floor was inherited anyway — `best_price_router.decide_book()` applied
`clears_edge_floor()` **in addition to** the per-bot threshold, deliberately, under the
banner "two policies, both must pass, stricter wins." That rule was itself a fix
(`EDGE-FLOOR-ONE-PREDICATE`) for a real defect on a real-money path. **Both changes were
correct in isolation and neither author could see the other.**

Then `_SHARP_EDGE_CEILING = 0.08` shipped on 2026-09-20 — also correct in isolation, and
also a fix for a real defect (phantom prices inflate edge, so only a ceiling catches
them). Floor 0.10, ceiling 0.08. The admissible band became **empty**, and the two
merged sharp bots stopped emitting entirely.

**Two lessons, and the second is the one worth keeping.**

1. **An effective gate is not the sum of its declared parts.** `_floors()` returned the
   correct 0.03 the whole time. A test reading `_floors()` — or the config, or the
   registry — passes green over the entire defect, because no single declared value was
   ever wrong. Only a probe that asks *"can anything actually get through?"* sees it.
   The smoke test written for this is deliberately a probe: it constructs an edge in the
   middle of each bot's own declared band and asserts the router emits. Verified to fail
   10/10 against the pre-fix code, which is the only evidence a regression test is real.

2. **An unreachable floor does not produce no picks — it produces only WRONG picks.**
   This is the counter-intuitive half and it cost a full track record. Because
   `edge = p − 1/price` is *maximised by a bad price*, raising a floor past the maximum
   genuine opportunity does not filter toward quality; it filters toward data faults.
   `bot_trigger_1x2_sharp_v1` went **25/25 voided** — every pick priced off a quote
   belonging to a different fixture — while its per-book twin, running the floor it was
   supposed to, went **2/243**. It published +549.9% ROI before anyone noticed. It was
   never an unlucky strategy; it was a phantom-fixture detector wearing a bot's name.
   **Whenever a floor is above the largest effect the anchor can physically produce, the
   survivors are by construction the errors.** Same family as §67 in ANALYSIS_GOTCHAS.

**The guard that now exists:** `BOT-ADMISSIBLE-BAND-NON-EMPTY` probes every registered
`BotConfig` at the midpoint of its own band, and `SHARP-FLOOR-NOT-STACKED` pins that an
explicit `edge_floor` is the floor actually enforced while model-anchored bots keep the
stacked behaviour. Still missing, and worth having: an alert for any `is_active` bot
that emits zero picks for N consecutive days. Nothing in this incident was detected by
monitoring — it was found by reading the ticket.

## 20. A guard widened three times by references it was never about (2026-09-22)

**The tell:** a test that forgives a difference gets a new exception every time an
unrelated change trips it, and each exception is *wider* than the last. Eventually
it forgives everything and nobody notices, because a guard that never fires looks
exactly like a guard that is being satisfied.

**The case.** `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` asserts the bot registry matches
the `bots` table. Migrations and smoke tests both fire on push with no ordering
between them, so a correct retirement races its own migration — hence a
"pending-migration" discount. That discount was built as **two independent greps
OR-ed across every pending file**: *is this bot's name anywhere in the pending
SQL*, and *does the string `retired_at` appear anywhere in it*.

It has now been widened three times by things it was never about:

1. Migration 336's header **tabulated the bots it was deliberately NOT retiring** —
   so matching raw text discounted exactly those. Fixed by stripping comments.
2. Migration 368 rebuilt `picks_public_all`, which **names a live bot as a label**
   and filters `retired_at IS NULL` in a WHERE clause — enough to mark a live bot
   as retiring. Fixed by requiring an UPDATE that SETS `retired_at`.
3. Migration 375 retires ONE bot and, in an unrelated statement, sets
   `display_name` for **sixteen live ones** — so the whole fleet was discounted,
   the effective DB set went empty, and the test failed on a correct commit.

**The fix that should have been made the first time** is not another exception:
scope the check to the STATEMENT that sets `retired_at`, so a bot is only
discounted by a statement that actually retires *it*.

**And the narrow fix was wrong too, in the opposite direction.** Splitting SQL on
`;` shatters a `DO $$ ... $$` block, whose body is full of semicolons. Migration
375's retirement reads `UPDATE bots SET retired_at = now() WHERE id = v_old`, with
the name thirty lines earlier in `SELECT id INTO v_old ... WHERE name = '...'` —
different fragments, so a correctly-written retirement was not seen at all.
Strict in the wrong direction is just as broken as permissive. DO blocks are now
lifted out as single units before the split.

**The guard:** when a forgiveness rule fires on something it was not about, ask
whether its SCOPE is wrong before adding a condition. Two greps OR-ed over a
whole corpus is a scope bug wearing a logic bug's clothes. And whenever you
tighten a matcher, check the *other* direction too — a fix that stops
over-matching very often starts under-matching.

## 21. An applied migration edited in place — the change is invisible, not applied (2026-09-23)

**The tell:** CI is green, the migration workflow says success, and the column
does not exist. Nothing is red anywhere, because nothing ran.

**The case.** Migration 376 added ten columns, shipped, and `migrate.yml`
recorded it in `_schema_migrations`. Feedback then arrived that three more field
pairs should be captured, so 376 was **edited in place** and pushed again. The
runner keys on **filename**: an already-recorded file is never re-executed, so
the six new columns never appeared. The migration workflow reported success —
truthfully, because it had nothing to do. CI was green for two commits.

It surfaced when a human ran a backfill:
`psycopg2.errors.UndefinedColumn: column s.shots_off_target_home does not exist`.

**Why it is worth a ledger entry rather than a shrug.** Every signal available
said the change had landed: the file on disk contained the columns, the commit
was pushed, the migration job was green, and the smoke suite passed. The only
thing that disagreed was the database, and nothing was comparing the two.

**The guard:** `MIGRATION-EDITS-ARE-INVISIBLE` asserts every applied migration
still exists on disk, and pins the follow-up file so it cannot later be deleted
as a "duplicate" — it is not a duplicate, it is the half of 376 that never ran.

**The rule:** an applied migration is immutable. New columns get a new file,
always — even when the edit is one line and the original shipped ten minutes ago.
The cost of a second file is nothing; the cost of a silent no-op is a defect that
only a human running the right command will ever find.

## 22. A column parsed and then dropped by a hardcoded writer list — three times in one day (2026-09-23)

**The tell:** the parse produces a field, the column exists in the schema, and it
is NULL on every row. Nothing errors, because nothing is wrong — the value simply
never reaches the INSERT.

**Three instances found in a single audit round**, all the same shape:

1. **`Shots insidebox` / `Shots outsidebox`** — in every `/fixtures/statistics`
   response since 2018, parsed by nothing. Zero repo references to `insidebox`.
2. **Lineups from the batch** — `/fixtures?ids=` embeds `statistics`, `events`,
   `lineups` and `players`. Settlement pulled out three and ignored `lineups`,
   while the function's own header comment listed all four. Lineups sat at 6.5%
   against statistics at 32% as a direct result.
3. **`assist_name`** — the parse emitted it; the writer's column tuple omitted
   it; NULL on all 1,869,434 rows including 353,490 goals.

**And the third one repeated itself, which is the real lesson.** After fixing the
writer in `db.store_match_events_batch`, a backfill was started and wrote
**nothing** — assists stayed at 0 while lineups climbed beside them in the same
job. There are **TWO** event writers: the batch one in `db.py` and
`supabase_client.store_match_events_af`, each with its own hardcoded column list.
The fix landed in one. The backfill called the other.

**Why hardcoded column lists are the common cause.** Every one of these is a
literal tuple or a literal `INSERT (...) VALUES (%s, %s, ...)` that someone has to
remember to extend. The per-row fallback in the batch writer had NINE `%s` against
nine columns — adding one would have broken the retry path, which only executes
when the bulk insert has already failed.

**The guards now in place:**
* `SHOT-LOCATION-STORED` — parsed fields must also be in the storage list.
* `BATCH-EMBEDS-FOUR-BLOCKS` — settlement must consume every block the batch
  already paid for.
* `EVENT-WRITER-COLUMNS-COMPLETE` — **both** event writers must carry every
  column, and their upserts must refresh it. One writer passing is how instance 3
  reached production twice.

**The rule:** a second writer for the same table is a second place for a column to
be dropped. When adding a field, grep for every writer of that table — not the
one you happen to be editing. And derive placeholder counts from the column list
rather than typing them.

## 23. The same quantity computed in two places, diverging silently (2026-09-23)

**Three instances in one day.** This is the dominant failure mode of this
codebase and it is worth naming as a class rather than fixing three times.

| # | the quantity | path A | path B | consequence |
|---|---|---|---|---|
| 1 | `match_events.assist_name` | `db.store_match_events_batch` | `supabase_client.store_match_events_af` | fixed A, backfill called B, wrote nothing for minutes while lineups climbed beside it |
| 2 | `odds_volatility`, `sharp_consensus_*`, `pinnacle_line_move_*` | morning pipeline → `match_signals` | `backfill_mfv_b_ml3_v2_features` → MFV columns | **the model reads the copy the pipeline does not write** ([[#085]]) |
| 3 | the half-time rating | probe: fit once, predict forward | populate: fit, then update after each match | **a FAIL was reported on a construction that was not the one shipped** |

**Instance 3 is the instructive one, because it produced a wrong conclusion
rather than missing data.** Both paths imported the same `HalfRatings` class —
so "share the code" was satisfied — and then used it differently. **Sharing a
class is not sharing a construction.** The probe froze the rating at the cutoff;
the populated feature updated it after every match. Measured on the SAME
post-cutoff fixtures: frozen r = **+0.1117**, walk-forward r = **+0.2378**. The
probe tested a materially worse feature and its failure was reported as the
feature's failure.

**What actually caught it, and it was luck.** The leak audit printed r = 0.2580
against realised totals, which sat suspiciously close to the market's own 0.2371
— a number that looked *too good*, checked for the wrong reason (suspected leak),
and turned out to be a different bug. Had the walk-forward version been slightly
worse instead of better, nothing would have flagged it.

### The guards that generalise

1. **Measure the thing you ship.** The probe must call the same function the
   population calls, not a second assembly of the same parts. Where that is
   impractical, assert the two agree on a sample — see `HALF-TIME-ONE-CONSTRUCTION`.
2. **Two numbers for one quantity is a bug until proven otherwise.** When a
   dipstick and an audit disagree about the same rating on the same matches, the
   explanation is a construction difference until demonstrated otherwise.
3. **Keep an independent reference for every headline number.** The market's own
   correlation (r = 0.2371) is what made 0.2580 legible. A number with nothing to
   compare against cannot look wrong.
4. **Too good and too bad are equally strong signals.** The instinct to check
   fires easily on a flattering number; instance 3 arrived as a FAIL and was
   nearly accepted because failure matched the prior.

## Pattern: a reviver that reports success without verifying the world

**Seen twice in three days, both on Coolbet, both silent.**

- `RESUME-LOADED-BUT-NOT-RUNNING` (2026-09-20) — `launchctl load` returned 0 and
  the resume agent logged "loaded" for both Coolbet jobs while `launchctl list`
  showed neither. **14h outage.** Fix: verify against `launchctl list`, retry once.
- **2026-09-22, again.** The verify-and-retry was in place. The agent still fired
  twice, still logged "loaded", and the jobs were still not loaded. **4.3h
  outage**, on the price basis every real stake is sized from. `launchctl
  bootstrap` (the modern API) loaded them immediately where the legacy
  `launchctl load` had not.

**The tell:** a log line saying the fix was applied, next to a system that
disagrees. **The guard:** verifying is necessary but was not sufficient — the
legacy `load` verb can fail in ways the check ran too early to see, and the host
sleeping across the fire time defeats both. **The real fix is to stop depending on
a laptop being awake**, which is what VPS-CONSOLIDATION-2026-09-16 is for.


## 18. A shared stateful handle is not a service — two callers, one browser tab

**FS-SESSION-CROSSED-RESPONSES, 2026-09-23.** A FlareSolverr "session" looks like
an HTTP client, but it is **one browser tab**. A manual board sweep and the
scheduled sweep drove `coolbet_prod` at the same time, and Grorud v Moss's
sidebets request came back holding Hammarby W v Rangers W's markets. They were
stored as Grorud's prices and caught only because they happened to invert
Pinnacle's favourite. A crossing between two similarly-priced games would have
passed every check we have.

**The tell:** wrong data that no matcher could have produced (the names share
nothing), and the same write burst carrying two disagreeing values from two
transports. **The guard:** a per-session cross-process lock at the one choke point
(`_fs_call`), plus a check that the response describes the request (the URL FS
reports) so the next unguarded path fails loudly instead of mis-pricing.
**General rule:** anything with hidden state (a browser tab, a CDP target, a
logged-in session) needs an owner or a lock, and "it's only a manual run" is
exactly when the second caller appears.
