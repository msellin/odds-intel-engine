# ODDS-QUALITY-CLEANUP — Full Plan

> Started: 2026-05-10. Goal: stop garbage Over/Under odds at the gate, purge what's already in the DB, repair affected bot bets/bankrolls, verify ML-pipeline derivatives are unaffected. Owner: agent + Margus. Estimated effort: 4–5h.

## The bug (one paragraph)

`workers/jobs/daily_pipeline_v2.py:1086-1102` aggregates best price across all bookmakers via `MAX(odds)`. For Over/Under markets, three sources ship clearly broken data and consistently win that max:
- `bookmaker='api-football'` (the synthetic AF source) — **100%** of OU 1.5 pairs are mathematically invalid (`1/over + 1/under < 1.0`), avg implied-sum 0.63. Every OU line (0.5/1.5/2.5/3.5/4.5) is broken.
- `bookmaker='William Hill'` — **88%** of OU 1.5 pairs are Under-favored with avg over odds 2.44, **100%** Under-favored on OU 2.5/3.5/4.5. Strongly suggests their over/under labels are swapped or line-shifted.
- `bookmaker='api-football-live'` — in-play live odds leaking into pre-match best-price aggregation; max odds reach 21.0.

Net effect: bots that bet Over/Under (especially `bot_ou15_defensive`) see fake "best price" of 2.5–3.5 when the real market is 1.30–1.55. They book ghost bets at impossible odds, win ~76% (because Over 1.5 actually hits in normal football), and post inflated PnL on bets that could never be placed in real life.

## What's NOT poisoned (verified before writing this plan)

- **1X2 odds** — all books <0.05% invalid, normal margins. Clean.
- **BTTS odds** — only `api-football` source has a marginal 1.91% invalid rate. Clean.
- **`match_feature_vectors`** — `build_match_feature_vectors` (`supabase_client.py:1140`) only reads `market='1x2'`. **Not poisoned.**
- **`workers/model/train.py`** — reads MFV, labels from match scores. Features `opening_implied_*` are 1X2-only. **Not poisoned.**
- **Platt calibration** (`scripts/fit_platt.py:43`) — fits on `predictions` vs match outcomes, no odds. **Not poisoned.**
- **ELO** — uses only match results. **Not poisoned.**

This means **the v10 retrain can proceed in parallel** with this cleanup. The cleanup is required before bot-performance numbers can be trusted (leaderboard, ROI, A/B harness comparisons), and before any future feature that pulls OU odds into MFV.

## What IS poisoned

| Surface | Damage | Detection query |
|---|---|---|
| `odds_snapshots` | ~50K–200K rows of garbage OU prices | `SELECT bookmaker, market, count(*) FROM odds_snapshots WHERE market LIKE 'over_under_%' GROUP BY 1,2` |
| `simulated_bets` | OU bets at fake `odds_at_pick`, fake PnL | OU bets where `odds_at_pick` has no matching pair in cleaned snapshots |
| `bots.current_bankroll` | Inflated for OU bots | Recompute from sum(pnl) per bot |
| `dashboard_cache.bot_breakdown` | Reflects inflated bankrolls | Rebuild after C1–C5 |

Affected bots, ranked by exposure:
- `bot_ou15_defensive` — **heavy** (OU 1.5, every recent pick used inflated odds)
- `bot_ou35_attacking` — **heavy** (OU 3.5, same parser problem)
- `bot_ou25_global`, `bot_opt_ou_british` — **light** (OU 2.5 is mostly clean except William Hill)
- `bot_btts_all`, `bot_btts_conservative` — **negligible** (BTTS is clean; `api-football` BTTS marginal)
- All `1x2`-only bots — **not affected**

## Stages

### Stage A — Stop the bleeding (1h)

A1. Add blacklist + sanity gate to best-price loop in `_load_today_from_db` (`daily_pipeline_v2.py:1086-1102`):
   - Skip any row where `bookmaker IN ('api-football','api-football-live','William Hill') AND market LIKE 'over_under_%'`
   - After best-price selection, drop the (over, under) pair for a match if `1/over + 1/under < 1.02` (impossible market — silently quarantines whatever broken source appears next without code changes)
   - Log per-run row-skip count so an upstream regression is visible

A2. Same blacklist + sanity gate at ingestion (`supabase_client.py:store_match_odds`, line 580+). Belt + suspenders — keeps the table clean even if A1 ever regresses.

A3. Smoke tests:
   - `ODDS-QUALITY-OU-BLACKLIST-READ` — source-inspect the read-path blacklist
   - `ODDS-QUALITY-OU-SANITY-GATE-READ` — fixture proves an impossible (over=3.0, under=2.0) pair is rejected
   - `ODDS-QUALITY-OU-BLACKLIST-WRITE` + `ODDS-QUALITY-OU-SANITY-GATE-WRITE` — same at the storage path

A4. Disable `bot_ou15_defensive` + `bot_ou35_attacking` (`UPDATE bots SET active=false`) until Stage C completes. `bot_ou25_global` and `bot_opt_ou_british` can stay live since OU 2.5 is mostly clean — A1 will protect them from now on.

**Exit criteria:** new pipeline runs add zero bookmaker='api-football' OU rows; new bets only fire on prices that pass the implied-sum gate.

### Stage B — Purge historical garbage (1–2h)

B1. **Snapshot before-counts** to `dev/active/odds-quality-cleanup-context.md` for audit trail:
   ```sql
   SELECT bookmaker, market, count(*) FROM odds_snapshots
    WHERE market LIKE 'over_under_%'
    GROUP BY 1,2 ORDER BY 1,2;
   ```

B2. **Optional safety net**: copy rows-about-to-be-deleted into `odds_snapshots_quarantined` (same schema). ~30s for ~500K row copy, costs negligible storage. Lets us roll back without a backup restore.

B3. **Hard-delete the obvious garbage:** new script `scripts/cleanup_ou_odds_garbage.py`:
   ```sql
   DELETE FROM odds_snapshots
    WHERE market LIKE 'over_under_%'
      AND bookmaker IN ('api-football','api-football-live','William Hill');
   ```
   Use chunked DELETE if pgbouncer holds it open too long. Expected: ~50K–200K rows.

B4. **Pair-validation sweep** for the survivors:
   - Find all `(match_id, bookmaker, market, timestamp)` triples with both over+under and `1/over + 1/under < 1.02`
   - Delete BOTH rows of each invalid pair
   - Single-sided rows (only over OR only under, no pair to validate) are kept — they're harmless to a max-aggregator since the gate catches the missing-pair case

B5. **Verify** with the audit query from this conversation — expect every per-(bookmaker, market) invalid rate <5%, blacklisted bookmakers gone entirely.

B6. Smoke test `ODDS-QUALITY-POST-CLEANUP-AUDIT` — query asserts no rows from blacklisted bookmakers in OU markets, asserts overall invalid pair rate <5%.

**Exit criteria:** the audit table from `bash python3 -c '...'` shows clean state.

### Stage C — Resettle affected simulated_bets (1h)

C1. **Identify affected bets** — pattern matches `scripts/resettle_after_btts_fix.py`:
   - Predicate: `market = 'O/U'` AND `odds_at_pick` does not match any surviving `odds_snapshots` row for the same `(match_id, market_label, selection)` within ±5% at pick_time
   - Equivalent: the bet's price came from a row we just deleted

C2. **Decision per affected bet**:
   - **Settled (won/lost)**: void → `result='voided'`, `pnl=0`, prepend `[ODDS-QUALITY-CLEANUP-{date}]` to `reasoning` for forensic traceability. Don't delete (keeps audit history).
   - **Pending (unsettled)**: delete row (would have been fake anyway).

C3. New script `scripts/cleanup_ou_bets_after_quality_fix.py`:
   - Atomic per-bot transaction
   - Dry-run by default (`--dry-run`), prints summary: bets voided, bets deleted, PnL erased per bot
   - `--apply` to execute
   - Idempotent: skips bets that already carry the `[ODDS-QUALITY-CLEANUP-` marker

C4. **Recompute `bots.current_bankroll`** for affected bots:
   ```python
   new_bankroll = STARTING_BANKROLL + sum(pnl) WHERE bot_id=X AND result IN ('won','lost')
   ```

C5. **Recompute `simulated_bets.bankroll_after`** for every surviving row of affected bots in chronological order. Otherwise the bankroll trajectory chart goes wrong.

C6. Smoke test `ODDS-QUALITY-VOIDED-MARKER` — asserts the marker exists in reasoning of voided rows so a re-run never double-processes.

**Exit criteria:** affected bots' bankrolls reset to realistic values; voided bets carry the marker; dry-run a second time produces zero changes.

### Stage D — Refresh derivatives (30m)

D1. `dashboard_cache.bot_breakdown` — invalidate + regenerate so the leaderboard reflects cleaned numbers. Find the existing refresh script (likely `scripts/refresh_dashboard_cache.py` or similar).

D2. **CLV recompute** for surviving bets — `closing_odds` came from the same poisoned snapshots. Re-pull from cleaned `odds_snapshots`, recompute `clv` and `clv_pinnacle`. Existing helper likely in `scripts/backfill_clv_pinnacle.py` — re-run it for the affected date range.

D3. **MFV** — verify no rebuild needed: `build_match_feature_vectors` (`supabase_client.py:1140`) only reads `market='1x2'`. **Not poisoned.** Document this in the closing notes; no action needed.

D4. **Platt calibration** (`scripts/fit_platt.py`) — fits on predictions + match outcomes, no odds_at_pick. **Not poisoned.** Document; no re-fit needed.

D5. **Frontend cache** — bust whatever cache surfaces bot performance (Vercel ISR, Supabase materialized view, whatever's in front of `bot_breakdown`).

**Exit criteria:** leaderboard numbers match recomputed bot bankrolls; CLV columns reflect post-cleanup closing odds.

### Stage E — Verification (30m)

E1. Re-run the audit queries from this conversation in `dev/active/odds-quality-cleanup-context.md`:
   - Per-bookmaker, per-OU-market invalid pair rate → blacklisted books absent, all others <5%
   - `bot_ou15_defensive` recent picks (after re-enabling) — odds_at_pick now <2.0 typically, not 2.5–3.5
   - Bot bankrolls in realistic range, not inflated

E2. Run full smoke suite locally **once** then push to trigger CI. (Per CLAUDE.md, normally only the new test runs locally; this is a system-wide cleanup so the full local pass is justified.)

E3. Re-enable `bot_ou15_defensive` and `bot_ou35_attacking`. Watch the next 2–3 pipeline runs — confirm no new garbage bets fire.

**Exit criteria:** smoke suite green in CI; first 24h post-re-enable shows OU bot odds_at_pick consistent with cleaned market.

### Stage F — Documentation (15m)

F1. `PRIORITY_QUEUE.md` — add `ODDS-QUALITY-CLEANUP` row, mark `🔄 In Progress` at Stage A start, `✅ Done YYYY-MM-DD` after Stage E
F2. `DATA_SOURCES.md` — short section: "OU bookmaker blacklist" naming the three excluded sources and the implied-sum gate
F3. `WORKFLOWS.md` — note the gates in step ③ (Odds) and step ⑤ (Betting)
F4. `MODEL_WHITEPAPER.md` — paragraph under "Data sources / quality gates" describing the post-2026-05-10 OU cleanup
F5. `dev/active/odds-quality-cleanup-{context,tasks}.md` — closeout summary with row counts deleted, bets voided, bankroll deltas

## Sequencing vs the ML retrain (ML-PIPELINE-UNIFY)

- This cleanup and the v10 retrain are **independent**: training reads MFV which uses 1X2 odds only.
- ML blockers (Stage 0e MFV rebuild, Stage 0d `backfill_team_season_stats.py`, Stage 2a NaN handling) **don't depend on this cleanup**.
- Recommended order: **A in parallel with ML Stage 0d/2a**, then B/C/D/E around or after first retrain. Stages A+B should land before any **bot-performance-based** A/B promotion (Stage 4 of ML-PIPELINE-UNIFY).

## Alignment with `~/.claude/plans/logical-dancing-cocke.md` (Unified ML Pipeline status doc)

That doc lists this odds cleanup as a **hard prerequisite to first v10 candidate** based on three concerns. After tracing the actual code, two of the three are not real:

| Concern in that doc | Verified reality | Verdict |
|---|---|---|
| "MFV poisoning — `build_match_feature_vectors` reads `odds_snapshots` for `opening_implied_*`, `closing_implied_*`, `bookmaker_disagreement`" | `build_match_feature_vectors` (`supabase_client.py:1140`) reads only `market='1x2'`. `opening_implied_*`/`closing_implied_*` derive from those 1X2 rows (`:1313-1338`). `bookmaker_disagreement` is computed in `compute_bookmaker_disagreement` (`:2685`) and the morning-signal pass (`:3613`), both filtered to `market='1x2' AND selection='home'`. **All three derive from 1X2 only.** | ❌ **Not poisoned.** Stage 0e MFV rebuild can start in parallel — won't bake any OU bug into history. |
| "Pseudo-CLV contamination — `(1/open)/(1/close) - 1` against fake-max prices" | `pseudo_clv_*` columns (`supabase_client.py:1046-1048`) are computed from `odds_snapshots` filtered to 1X2. Same source as above. | ❌ **Not poisoned.** No CLV cleanup needed for ML inputs (still need it for bot-performance leaderboard, see Stage D2). |
| "`simulated_bets.odds_at_pick` already polluted — phantom edges, P&L is real but edge was hallucinated" | ✅ **Correct.** OU bot bets carry fake `odds_at_pick`. Settled PnL reflects real outcomes at fake prices. | ✅ **Real.** Fixed in Stage C. Blocks any bot-performance-based promotion (ML-PIPELINE-UNIFY Stage 4 promotion criteria) but **not training itself.** |
| "1X2 risk — api-football-live polluting pre-match aggregation is market-agnostic; quick check `1/home + 1/draw + 1/away < 1.0` on api-football-live 1X2 rows" | Audited: api-football-live 1X2 has 0.00% invalid rate, avg implied-sum 1.078. Same for every other bookmaker on 1X2 (<0.05% invalid across the board). | ❌ **Not affected.** 1X2 is clean everywhere. |

**Net change to the other agent's critical path:**

- Their step 3 ("Apply the same source filter to MFV's odds reads — wherever `build_match_feature_vectors` derives best-price features, apply the same blacklist") is **unnecessary**. MFV uses `market='1x2'` only and 1X2 is clean. Skipping this step removes ~1h from their critical path.
- Their step 4 ("Rebuild MFV cleanly (Stage 0e)") **does not have to wait** for any odds cleanup. The Stage 0e MFV historical rebuild can start as soon as Stage 0d (team_season_stats backfill) lands. They were blocking themselves on a non-issue.
- Stages A and C of this plan still matter for: bot leaderboard correctness (Stage D1), CLV-based meta-models if they ever exist (Stage D2), any future strategy backtest that reads `odds_at_pick` from `simulated_bets`, and the v10 promotion gate that uses bot ROI.

**TL;DR for the other agent**: start Stage 0e MFV rebuild and Stage 0d backfill **now** — they're safe. Wait for Stages A+B+C of this plan only when you reach Stage 4 promotion that uses bot performance.

## Out of scope (file as separate queue items)

- **`OU-LINE-DRIFT-INVESTIGATE`** — Some books (Unibet, Betano, BetVictor, Bet365) occasionally return Over 2.5–level prices in the Over 1.5 slot. Implied-sum gate catches the worst cases but the root cause (AF parser edge case? AF feed-time anomaly?) is unresolved. Would need to log raw AF responses for a day and diff.
- **`NORDIC-BOOKS-INTEGRATION`** — AF doesn't carry Paf, Coolbet, Veikkaus, Svenska Spel, Norsk Tipping. Adding Nordic-region pricing requires a separate scraper.
- **`OU-SANITY-GATE-BTTS`** — generalize the implied-sum gate to BTTS. Low value (BTTS is mostly clean) but cheap if A1 is parameterized by market.
- **`AF-PAGE-DUP`** (already filed earlier) — same fixture appearing across multiple AF pages with slightly different odds; lower-impact (<1.5× spreads).

## Risk register

| Risk | Mitigation |
|---|---|
| Hard delete is destructive | B2 quarantine table copy first |
| Voiding bets changes leaderboard | Correct behavior — current numbers are wrong. Communicate in release notes. |
| Resettlement script bug double-voids | C6 idempotency marker + dry-run by default |
| `bots.active=false` accidentally left on | E3 explicit re-enable step + smoke test guard |
| Stage A blacklist drifts (someone re-adds a source) | A3 source-inspection smoke tests |

## Total effort

| Stage | Effort | Blocking ML retrain? |
|---|---|---|
| A — Stop bleeding | 1h | No |
| B — Purge historical | 1–2h | No |
| C — Resettle bets | 1h | Only blocks bot-performance-based promotion |
| D — Refresh derivatives | 30m | No |
| E — Verification | 30m | No |
| F — Docs | 15m | No |
| **Total** | **~4–5h** | — |
