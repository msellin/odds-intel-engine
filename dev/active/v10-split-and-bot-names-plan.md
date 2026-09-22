# V10 SPLIT BY MARKET + READABLE BOT NAMES — plan

Parent rows in `PRIORITY_QUEUE.md`: **[[#040]] V10-SPLIT-BY-MARKET** and
**[[#069]] BOT-NAMES-AND-LABELS-ARE-NOT-FOR-READERS**.

Done together on the owner's instruction (2026-09-22: *"when you split you need to
come up with names and this matches that task a bit"*). #069's own row already said
#040 *"should land BEFORE display names, or the split immediately makes the new
names wrong"* — so one commit, one migration, one doc pass.

---

## What the split turned up — it is NOT accounting only

The #040 row estimated the value as *"organizational, not performance"*. That was
wrong, and the measurement is the reason to ship it rather than defer it.

De-vigged Pinnacle CLV (`simulated_bets.clv_pinnacle_devig`), settled rows only.
CLV rather than ROI per `ANALYSIS_GOTCHAS §8` — it converges ~200x faster:

| market | n | CLV | 95% CI | ROI |
|---|---|---|---|---|
| `1x2` | 335 | **+2.50%** | [+0.41, +4.60] — excludes 0 | +12.80% (n=400) |
| `over_under_25` | 181 | **-3.85%** | [-5.01, -2.69] — excludes 0 | -0.54% (n=252) |

The published "+11-13% calibrated reference bot" is **one market carrying the
other**, and the two halves are on opposite sides of zero with both CIs excluding it.

### It survives the calibration-change test (`§39`)

The O/U result is not an artefact of any one model version or any one month:

* **Negative in 5 of 5 months** — May -4.53%, Jun -3.74%, Jul -3.55%, Aug -4.84%, Sep -1.76%.
* **Negative in 7 of 7 model versions** — v14 -3.48%, v20260524_market -6.12%,
  v20260607 -3.44%, v20260621 -2.75%, v20260705 -4.04%, v20260712 -2.73%, v9a -0.97%.

It therefore predates and outlives `OU-CALIBRATOR-DOMAIN-MISMATCH` (migration 335,
2026-09-13). That bug made a bad half worse; it did not create it.

### The 1x2 result is weaker than the pooled number looks — say so

1x2 CLV by month: May **-0.87%**, Jun **-2.15%**, Jul **+8.35%**, Aug **+8.24%**,
Sep **+7.25%**. The pooled +2.50% is **entirely a July-onward effect** (n=142 in the
positive era against n=193 before it). `bot_v10_1x2` is honestly `calibrated` on
current evidence, but the record is a three-month record, not a five-month one, and
the promotion rule below is written so that stays visible.

### Cost of acting on the O/U half: zero picks

`picks_public_all` shows `bot_v10_all` / `over_under_25` last published
**2026-09-13** — the day migration 335 removed the broken calibrator. The O/U half
has emitted nothing for nine days, so demoting it does not reduce Telegram volume.
That matters because *"we dont dry up picks for telegram users"* is the binding
constraint on this whole block.

**Not decided here:** whether O/U should keep `show_on_picks`. It is left TRUE so
this change is label-and-identity only; the publication call is the owner's and is
flagged on the queue row.

---

## Phases

### Phase 1 — migration 375 (identity + display + labels), one transaction
1. `ALTER TABLE bots ADD COLUMN display_name text` — **display only, never a join key.**
2. Extend `bots_maturity_label_check` to admit `testing` (#069 (c): today it is a
   string the /performance page stamps on injected rows, so the legend documents
   three tiers of which one has no backing field).
3. Insert `bot_v10_1x2` (calibrated) and `bot_v10_ou` (**beta**, not calibrated —
   the CI above forbids it) inheriting bankroll and description.
4. Re-attribute history by market: `simulated_bets` and `shadow_bets`
   `bot_id` -> the new bots, keyed on `market`.
   **Safe against the unique indexes** — `uq_bet_per_bot_match_market_selection`
   and `uq_shadow_bet_per_cohort` both contain `market`, and the two target bots
   take disjoint market sets, so no row can collide with another.
5. Retire `bot_v10_all` with a reason (keep the row — it is referenced by history
   and by a dozen migration headers).
6. Backfill `display_name` for the active fleet; stamp `testing` on the two
   forward-test bots.

### Phase 2 — engine
* `daily_pipeline_v2.BOTS_CONFIG`: one entry becomes two (`markets` `["1x2"]` /
  `["ou"]`), everything else identical. `BOT_TIMING_COHORTS` likewise.
* `coolbet_feed_watchdog.PICKS_BOT`: now a tuple — the watchdog must not alarm
  because one of two bots was quiet.
* `bot_registry.py`: two BotSpecs replace one, plus `display` on every spec.
* `docs/SYSTEM_MAP.md` in the same commit (drift test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED`).

### Phase 3 — web
* `engine-data.ts` selects `display_name`; `bot-aggregates.ts` carries it through.
* `performance-leaderboard.tsx` renders the display name, with the DB name kept as
  small mono secondary text so an operator can still map a row to a query.
* `labels.ts` prefers `display_name`, falls back to `BOT_SHORT_LABELS`.

### Phase 4 — the promotion rule (#069 (b))
There is currently **no written beta->calibrated threshold**; `calibrated` means
"somebody typed calibrated". Write one with a number in it, in `docs/SYSTEM_MAP.md`.

---

## Risks

| risk | guard |
|---|---|
| Re-attribution loses rows | count before/after inside the same transaction, `RAISE EXCEPTION` on mismatch |
| A unique index blocks the UPDATE | both indexes contain `market`; targets are disjoint — asserted in the migration header |
| `bankroll_after` on historical rows becomes a discontinuous series per new bot | accepted and documented: the column is a per-bot running total that only ever fed the equity chart; the chart reads `startingBankroll` + per-bot bets, so each new bot's curve is re-derived, not inherited |
| Something still queries the literal `bot_v10_all` | full-repo sweep + a smoke test that fails if the name reappears in an executable position |
| Renaming breaks attribution | **`bots.name` is NOT touched.** `display_name` is additive. This is the explicit warning on #069's row. |
