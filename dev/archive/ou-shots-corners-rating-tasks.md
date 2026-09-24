# #077 — checklist

Parent row: **[[#077]]**. Plan: `dev/active/ou-shots-corners-rating-plan.md`.

- [x] Feasibility: usable n (Pinnacle O/U + both teams with prior stats)
- [x] League-mix comparison of the usable set vs the full population
- [x] Opponent-adjusted shots/corners ratings, time-decayed, leak-free
- [x] Leakage assertion (ratings use only matches strictly before the fixture)
- [x] Map predicted stats -> lambda -> P(over 2.5)
- [x] Score through `residual_test_ou`'s own functions (imported, not copied)
- [x] CONTROL: shipped bundle re-run on the identical restricted population
- [x] Verdict against the pre-committed stopping rule
- [x] Smoke test
- [x] Findings doc + queue row + SYSTEM_MAP if it closes O/U

---
**Closed 2026-09-23 — α = 0.0000, FAIL against the pre-committed rule.**

Two checklist items were done differently than planned, and both matter:

* **"CONTROL: shipped bundle on the identical restricted population"** was
  replaced by a BETTER control — the same rating machinery fed GOALS. The shipped
  bundle shares none of this construction, so it could only have told us whether
  the population was unusual; the goals arm holds ratings, GLM, population and
  scoring constant and moves exactly one variable. Population comparability was
  checked separately and more cheaply: market AUC 0.6184 here vs 0.6007 on the
  full 7,273, so the subset is if anything sharper than average.
* **The control BEAT the treatment**, which was not an anticipated outcome and is
  why the conclusion is narrower than the plan assumed. See the findings doc.

Nothing unticked, so nothing to promote back to the master list.
Archive to `dev/archive/` once [[#025]] is closed as moot.
