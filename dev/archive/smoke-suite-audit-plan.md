# SMOKE-SUITE-AUDIT — plan

## Why

`CLAUDE.md` treats the GitHub Actions smoke run as *the* regression gate
("that's the gate, not your local run"). It has not passed once in the last
100 runs. Latest: **685 passed, 23 failed, 54 skipped** across **762 tests /
26,798 lines** in a single `scripts/smoke_test.py`.

Two separate problems, and the second is the bigger one:

1. **23 failing** — a red gate means a genuine regression is indistinguishable
   from standing noise.
2. **Dead tests that still pass.** Whole product areas were removed (CS2,
   HLTV/Leetify scraping, LoL, tennis, and the World Cup 2026 seasonal
   surface). A test that asserts a *source string* still exists will keep
   passing forever after the feature is gone. Those are worse than failures:
   they inflate the pass count and make the suite look healthier than it is.

## Measured domain liveness (2026-08-31)

| Domain | Code files | Scheduler | DB rows | Last activity | Verdict |
|---|---|---|---|---|---|
| CS2 | 1 | 6 refs | `cs2_hltv_news` 0 | — | dead (backup at `dev/active/cs2-removal-2026-08-26/`) |
| HLTV | 0 | 1 ref | tables dropped | — | dead |
| Leetify | 0 | 0 | tables dropped | — | dead |
| LoL | 2 | 0 | `lol_bets` 0 | 2026-06-14 | dead |
| Tennis | 6 | 40 refs | `tennis_value_bets` 38 | **2026-07-08** | dormant ~8wk |
| WC 2026 | 26 | 68 refs | `wc_bracket_picks` 1,440 | **2026-07-19** | tournament over |

Tennis and WC still have scheduler wiring — decide *retire vs seasonal-park*
before deleting their tests; the others have no code left at all.

## Approach

Evidence over eyeballing. Build `scripts/audit_smoke_tests.py` that, per test,
extracts every referenced module / file path / DB table / migration and checks
whether each still exists, then joins that against live CI pass/fail/skip.

Classification:

- **KEEP** — guards live behaviour, passing.
- **FIX** — guards live behaviour, failing. Real regression or stale assertion.
- **DELETE** — references a removed feature. Includes passing ones.
- **PARK** — seasonal (WC) or dormant (tennis); keep only if the feature comes back.
- **WEAKEN** — passes only because it greps a source string; guards nothing.

## Risks

- Deleting a test that looks dead but guards a live edge case. Mitigation:
  require *code-level* evidence (module/table gone), never a name match alone.
- The 54 skips may be hiding failures. Audit them explicitly.
- Do not mass-delete in one commit — batch by domain so each is revertible.
