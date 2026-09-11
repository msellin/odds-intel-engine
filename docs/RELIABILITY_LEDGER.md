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

The 2026-09-04 note says it best: *"the feed watchdog cheerfully re-harvested
cookies at a problem that was never about cookies."*

**Guard:** the `STALE_COOKIES` message now says *"this is probably NOT the
cookies — check TRANSPORT first"* and names the three checks. **Rule: when
cookies are fresh and the feed is dead, it is not the cookies.**

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
