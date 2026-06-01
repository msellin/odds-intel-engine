# Cherry-pick placer — tasks

## Phase 1 — Code lands, gate disabled (target: 2026-06-02 → 06-04)

- [ ] **P1.1** Add `_allowed_maturity_labels()` helper in `coolbet_placer.py`
  - Reads `COOLBET_RECORD_ALLOWED_MATURITY` env, parses comma-separated list
  - Empty / unset / `*` returns `None` (no filter)
  - Returns list of strings otherwise
- [ ] **P1.2** Thread the helper into `load_qualified_bets()` (singles)
  - SQL: `AND (%s::text[] IS NULL OR b.maturity_label = ANY(%s))` (NULL = no filter)
  - Skip the filter entirely when `bet_id_filter` is set (admin override)
- [ ] **P1.3** Thread the helper into `load_qualified_combo_bets()` (combos)
  - Same gate, same admin bypass
- [ ] **P1.4** Thread the helper into `load_qualified_inplay_bets()` in `inplay_bot.py`
  - Same gate. Inplay's `bet_id_filter` path also bypasses.
- [ ] **P1.5** Add diagnostic log when filter drops candidates
  - `log.info("Cherry-pick gate: dropped %d candidates with maturity in {%s}", n, blocked_labels)` — visibility for ops
- [ ] **P1.6** Smoke tests:
  - `CHERRY-PICK-GATE-DEFAULT-OPEN` — no env var → no filter applied (source-inspect)
  - `CHERRY-PICK-GATE-FILTERS` — env=`calibrated` → SQL contains the ANY check
  - `CHERRY-PICK-GATE-ADMIN-BYPASS` — `bet_id_filter` set → gate skipped
  - `CHERRY-PICK-GATE-ALL-LOADERS` — all three loaders include the gate
- [ ] **P1.7** Commit + push. Verify Railway picks up the code with `COOLBET_RECORD_ALLOWED_MATURITY` UNSET (no behaviour change).
- [ ] **P1.8** Update PRIORITY_QUEUE + WORKFLOWS.md (placer section). Mark Phase 1 done.

## Phase 2 — Promotion surface + written rule (target: 2026-06-05 → 06-06)

- [ ] **P2.1** Write `scripts/promotion_candidates.py`
  - CLI prints 30-day rolling per-bot stats: n_settled, ROI, avg_clv, hit_rate vs predicted, current maturity
  - Sorted by "closeness to calibrated threshold"
  - Smoke: `PROMOTION-CANDIDATES-SCRIPT` (source-inspect)
- [ ] **P2.2** Add `getPromotionCandidates()` to `odds-intel-web/src/lib/engine-data.ts`
  - Same query as P2.1 but via Supabase admin client
- [ ] **P2.3** New page `odds-intel-web/src/app/(app)/admin/promotion-candidates/page.tsx`
  - Server-side admin gate (is_superadmin only)
  - Table render of the candidates with promote button
- [ ] **P2.4** Server action: `promoteBot(name)` → `UPDATE bots SET maturity_label='calibrated' WHERE name=$1`
  - Admin-only, logs the change to a new `bot_maturity_history` table OR a notes column
- [ ] **P2.5** Write `docs/PROMOTION_RULES.md` with the criteria
  - n ≥ 150 settled bets / 14d
  - ROI ≥ 0% (preferably ≥ 2%)
  - avg CLV ≥ 0%
  - hit rate within 3pp of model-predicted
  - Demotion: ROI or CLV < -2% on n ≥ 50 bets / 7d
- [ ] **P2.6** Reference PROMOTION_RULES.md from CLAUDE.md so future agents follow it
- [ ] **P2.7** Smoke: `PROMOTION-CANDIDATES-PAGE-ADMIN-ONLY` (source-inspect for is_superadmin gate)

## Phase 3 — Flip the gate (target: 2026-06-08, day after Phase 3.5 closes)

- [ ] **P3.1** On 2026-06-07: read Phase 3.5 / Phase 4 verdict
- [ ] **P3.2** Run `scripts/promotion_candidates.py --days 30` and pick candidates
- [ ] **P3.3** Manually promote 2-4 bots to `calibrated` (via `/admin/promotion-candidates` or direct SQL)
  - Expected set on 2026-06-08: `bot_v10_all` (already calibrated), plus whichever v2 bots clear the bar
- [ ] **P3.4** Flip Railway env: `COOLBET_RECORD_ALLOWED_MATURITY=calibrated`
- [ ] **P3.5** Watch placer logs for 24h — count of placed bets per day should be lower but non-zero
- [ ] **P3.6** After 7 days: real_bets cohort ROI vs simulated_bets cohort ROI on the same window
  - Acceptance: real_bets ROI - simulated_bets ROI ≥ +2pp
- [ ] **P3.7** If fewer than 5 bets placed/day across all bots → widen to `active,calibrated`
- [ ] **P3.8** Write up Phase 3 outcome in `dev/done/cherry-pick-placer-outcome.md`, move dev/active/ files to dev/done/

## Cross-cutting

- [ ] **X.1** Performance page attractiveness is a SEPARATE task — do not bundle. (Plan: `dev/active/performance-page-attractiveness-plan.md` — TBD.)
- [ ] **X.2** SELF-USE-VALIDATION Phase 4 decision matrix references `real_bets` ROI. Once the cherry-pick gate is flipped, the matrix's "real ROI over 200+ bets" threshold needs a clarifying note that it's now measured on the curated subset. Update `dev/active/self-use-validation-context.md` after P3.4.
