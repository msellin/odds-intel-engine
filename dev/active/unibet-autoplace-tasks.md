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

### 1. Resume Coolbet + the dry-test that has never run (BLOCKED on decay)
- [ ] `--probe` until it says **OK** (it was CHALLENGED at handoff; 2 probes)
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
- [ ] **`echo 'COOLBET_AUTO_LOGIN_ON_HEAL=true' >> .env`** — still unset; makes
      the inactivity logout self-heal instead of needing the operator

### 3. Finish the consolidation (phase 2)
- [ ] **Shadow-mirror SQL floors** — the last copies. `coolbet_model_1x2_shadow.py`
      (`EDGE_FLOOR` default `"0.10"`) and `coolbet_model_ou_shadow.py`
      (`"0.08"`) define floors independently of `_MIN_EDGE_BY_MARKET`. Make the
      DEFAULT derive from the registry, keep the env override. Paper mirrors →
      lower risk, which is why they were left last.
- [ ] `coolbet_model_1x2_shadow.py` also has `odds>=2.80` as a SQL literal

### 4. Stuck settlement (small, real)
- [ ] One finished match (Independiente del Valle, Libertadores, 00:30 UTC
      2026-09-11) has `ht_score_home/away = NULL`, so its `1x2_1h` shadow bet
      stays pending and re-alerts forever. HT coverage is 99.0% — this is the 1%.
      Either backfill that match's HT score, or add "finished + no HT after N
      hours → void" so it cannot alert indefinitely.

### 5. Phase 3 — dashboard-configurable limits (unstarted)
- [ ] Separate the **picks→Telegram** policy from the **picks→real-money** policy
- [ ] Bounds the UI cannot exceed + audit trail + owner-gate on the staking half
- [ ] Mark each gate *tunable* / *bounded-tunable* / *code-only* — the map in
      `SYSTEM_MAP.md` §4 is the input for this
