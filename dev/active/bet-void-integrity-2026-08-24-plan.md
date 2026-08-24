# BET-VOID-INTEGRITY-2026-08-24 — plan

**Trigger**: user flagged `Fløya 5-3 Junkeren` showing `Void` on a `bot_no_pin_home_v1`
Home pick at 4.10 in `/admin/shadow-bots`. Home won 5-3 — a 1x2 Home bet can never void.

## Root causes found

### Class 1 — undocumented retroactive bulk void (143 shadow_bets rows)
An ad-hoc SQL UPDATE ran between **12:12:34 and 12:42:30 UTC on 2026-08-23** and voided every
then-pending `bot_no_pin_home_v1` pick satisfying `model_probability - 1/odds_at_pick > 0.20`
— exactly the `BOT-NO-PIN-MODEL-SANITY` rule committed at 12:02 UTC that morning (`0607c1f`).

Evidence the criterion is exact:
* 143 voided rows, gap range 0.205 → 0.410
* 0 rows written before the cutoff with gap > 0.20 escaped
* 0 voided rows with gap ≤ 0.20

Fløya's 12:42 pick survived only because it was written *after* the sweep. Its gap was 0.350 —
it should have been blocked at source, but the guard was on GitHub only; the VPS ran 21 commits
behind until `ENGINE-DEPLOY-2026-08-24`.

Two tells that this was ad-hoc and not a code path:
1. It left `pnl` NULL. Every void writer in the repo sets `pnl = 0`.
2. No commit, no PRIORITY_QUEUE entry, no smoke test.

133 of the 143 sit on matches that finished with a real score (8 games). They would have
settled 20W / 113L = **−57.8% ROI on €1,330**. The void therefore *flattered* the bot:
recorded ROI −4.31%, true ROI −11.52%. The 2026-08-24 retirement decision is reinforced.

### Class 2 — postponement voids are never reversed (57 shadow_bets + 4 simulated_bets)
`fix_stale_live_matches()` / `match_status_sweeper.py` mark a match `postponed` when AF reports
PST/CANC/SUSP/AWD/INT and void every pending bet on it. When AF later reports FT and the match
flips to `finished` with a score, **nothing reopens the voided bets**. There is no un-void path
anywhere in the repo.

Concrete: Piast Gliwice **1-1** Legia Warszawa (2026-08-22) — 19 `double_chance 1X` picks each
from `bot_dc_specialist`, `bot_dc_value`, `bot_dc_strong_fav`, all `void`. 1X on a draw wins.
Plus 4 `bot_v10_all` 1x2 rows in `simulated_bets` (2 of which won) — those touch bot bankroll.

### Class 3 — one permanent zombie
1 `bot_no_pin_home_v1` void sits on a match still `status='scheduled'`
(Borussia Mönchengladbach W v SC Sand W, 2026-08-23 12:00). Self-heals once the status sweeper
resolves the fixture — the new re-settler picks it up if it finishes.

### Real-money exposure
10 of the voided picks are marked in `user_pick_marks`; **6 are `state=2` ("bet placed")**.
Five of those six are misreported (1 won, 4 lost); only FC Thun v Servette is a genuine void.

## Fix

1. **Migration 282** — `void_reason TEXT` on `shadow_bets` + `simulated_bets`.
   Backfill `'quarantine'` for every `simulated_bets` void with `created_at < 2026-07-01`
   (all documented May–June cleanups: INPLAY-O-QUARANTINE, the OU pinnacle-cap and OU
   quality-fix sweeps). Those must never be resurrected. Everything later stays NULL.
2. **`resettle_wrongly_voided_bets()`** in `workers/jobs/settlement.py`, wired into the 15-min
   `settle_ready` sweep. Re-runs `settle_bet_result` over every void on a finished match with a
   score and `void_reason IS DISTINCT FROM 'quarantine'`; **writes only when the recomputed
   result is no longer `void`**, so genuine AH/DNB pushes are untouched and the pass is
   idempotent. Adjusts `bots.current_bankroll` by the pnl delta for `simulated_bets`.
   Telegram-alerts when it repairs anything — a repair means something upstream voided wrongly.
3. **Void writers stamp `void_reason`** — `'postponed'` in `fix_stale_live_matches()` and
   `match_status_sweeper.py`, so a deliberate void is distinguishable from a mystery one.
4. **`scripts/resettle_wrongly_voided_bets.py`** — CLI wrapper with CSV backup + `--dry-run`,
   for the one-off repair of the existing 190 rows.
5. **`shadow_bets_unique` view** — one row per `(bot_id, match_id, market, selection)`, latest
   pick. The 30-min refresh writes a fresh row per `shadow_cohort` **by design**
   (BET-TIMING-MONITOR timing A/B), so the insert key is intentionally left alone; the view
   gives engine-side analysis the same deduped basis the admin UI already computes client-side.

## Non-goals
* Changing the `shadow_bets` ON CONFLICT key — that would destroy the timing-cohort experiment.
* Resurrecting the May–June quarantines.
