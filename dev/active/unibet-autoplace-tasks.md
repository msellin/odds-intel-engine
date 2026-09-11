# UNIBET-AUTOPLACE + GATE CONSOLIDATION — tasks

## ✅ Done 2026-09-11

- [x] Recon: full map of every gate from generation → every surface
- [x] **Router Coolbet arm was DEAD** — `page = up.attach(pw)` got a
      `(browser, page)` tuple; every dispatch failed indistinguishably from a decline
- [x] **Router loosened second gate** — `edge_threshold` never passed (0.03 not 0.08/0.10)
- [x] **Unibet double-bet** — arm wrote nothing; now records to `real_bets`
- [x] **Unibet uncertain placement** — clicked-but-unconfirmable now creates
      exposure (`placed_real=NULL`) without claiming confirmation
- [x] Unibet session heartbeat (`ensure_logged_in`) on the placement path
- [x] **Router gate parity** — already_placed, kickoff cutoff, per-match family
      guard, daily caps; reuses `place_coolbet_ui`'s own functions
- [x] Routing audit on BOTH arms; records every book PRICED incl. absent ones
- [x] **`PLACER-EDGE-GATE-FAILED-OPEN`** — the only real-money edge gate failed open
- [x] **`EDGE-FLOOR-ONE-PREDICATE`** — Decimal-vs-float dropped at-floor picks
- [x] **`EDGE-FLOOR-ALL-CALLERS`** — live re-eval, in-play, router all migrated
- [x] **`FLOORS-ONE-SOURCE-CROSS-LANGUAGE`** — frontend floors now GENERATED
- [x] `MAC-PLIST-ORPHANS` — 3 unreproducible jobs exported; drift guard extended
- [x] `BOARD-SWEEP-NEARTERM-SKIP` + `--kickoff-band` + `--probe`
- [x] `1X2-1H-SETTLEMENT`, `KAMBI-NOT-PLACEABLE`, `DAILY-SUMMARY-REPORTED-ZERO`
- [x] `SYSTEM_MAP.md` §3/§4 rewritten; `RELIABILITY_LEDGER.md` created

## ⬜ NEXT — in priority order

### 1. Resume Coolbet + the dry-test that has never run (STILL BLOCKED on decay)
**Re-verified 2026-09-11 08:3x UTC: the flag is LIVE.** Use the new fast path:
`python3 -m workers.automation.coolbet_explorer --probe --fresh-session`
→ answered in **1.8s with an 881-byte challenge page**. That is Coolbet's real
verdict, so the footprint STAYS paused. (The plain `--probe` reads 60.4s/0 bytes
because the long-lived `coolbet_prod` FS session is ALSO wedged — a separate
fault that was hiding behind the same label. It now reports `wedged`, exit 3.)
- [ ] `--probe --fresh-session` until it says **OK** (2s per check, not 60s)
- [ ] `bash scripts/ops/coolbet_pause_resume.sh resume`
- [ ] Watch ONE banded sweep — confirm `cats_skipped_empty > 0` and that the feed
      writes again (last healthy hour wrote ~18k rows / 204 matches)
- [ ] **`python3 -m workers.automation.best_price_router --stage --limit 1`**
      ← this has NEVER completed green, because the Coolbet arm was dead its whole
      life. Do not skip it. It drives the real slip and stops before the click.
- [ ] Only then is `ROUTER_ALLOW_REAL` a decision for the OWNER

### 2. Owner decisions (do not guess these)
- [ ] **`max_odds_drop_pct` is DEAD** — defaults to 100.0 and `place_for_bot`
      never overrides it, so the odds-drift gate never rejects anything.
      Needs a real number (~10-20% is the sane region). Real-money policy.
- [ ] **`ROUTER_ALLOW_REAL`** — after the dry-test above
- [x] **`COOLBET_AUTO_LOGIN_ON_HEAL` — DONE 2026-09-11, and why it kept not
      sticking is the interesting part.** It was never merely "unset": the ONLY
      plist setting it was the **retired** paper mac-daemon's, and `.env` never
      had it — so the capability was **dead**. Session-keep moved to the
      feed-watchdog on 2026-09-10 and nothing still running read the flag, which
      is why "just set this flag" kept being recommended and kept not working.
      Now pinned in `local/launchd/com.oddsintel.coolbet-feed-watchdog.plist`
      (git-tracked, unlike `.env`). **Needs the operator to install the plist** —
      see "OPERATOR STEP" in the context doc; the drift guard flags it now.

### 3. Finish the consolidation (phase 2) — ✅ DONE 2026-09-11
- [x] **Shadow-mirror floors now derive from the engine registry.** Both mirrors
      took their defaults from re-typed literals that only happened to match, and
      the 1x2 one inlined `>= 2.80` inside its SQL where no constant reached it.
      Defaults now come from `_MODEL_1X2_HOME_FLOOR` / `_MIN_EDGE_BY_MARKET['o/u']`
      / `_min_odds_for('1x2')`; the odds floor is a bound parameter; env overrides
      kept. The o/u mirror RAISES if the registry retires the market rather than
      substituting a number.
- [x] **Phase 2 is complete** — every copy the audit found (Python callers,
      frontend, shadow mirrors) is closed. What is left is phase 3 *policy*:
      which gates become configurable, and with what bounds.

### 4. Stuck settlement — ✅ DONE 2026-09-11 (both halves)
- [x] **Backfilled + graded.** AF *did* have the score (HT 0-0, FT 0-2), so the
      bet was GRADED rather than voided — `home` lost, PnL -10.00. The sweep had
      simply not run since the match finished (it fires 22:30 daily), so this
      one was a timing gap, not missing data.
- [x] **Added the safety net anyway, because the permanent case is real.** That
      same backfill returned **`no_ht=7`** — seven finished matches carrying 1H
      odds for which AF has no halftime score at all. For those, "leave pending
      and alert" re-fires the 6h-deduped Telegram indefinitely, which is how a
      real alert decays into noise. `settlement.void_ungradeable_1h_bets()`
      voids them after 30h (> the 24h sweep gap, so the backfill gets a full
      cycle first), narrowly (1H markets only, finished matches only, HT
      genuinely absent) and **reversibly** — `void_reason='no_ht_score'` is
      admitted by `resettle_wrongly_voided_bets`, so the row re-grades if AF
      ever backfills the score. Smoke `HT-SCORE-NEVER-ARRIVES`.

### 5. Phase 3 — dashboard-configurable limits (unstarted)
- [ ] Separate the **picks→Telegram** policy from the **picks→real-money** policy
- [ ] Bounds the UI cannot exceed + audit trail + owner-gate on the staking half
- [ ] Mark each gate *tunable* / *bounded-tunable* / *code-only* — the map in
      `SYSTEM_MAP.md` §4 is the input for this
