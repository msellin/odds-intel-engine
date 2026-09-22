# V10 SPLIT + BOT NAMES — context

Parent rows: **[[#040]]**, **[[#069]]**. Plan + measurement:
`dev/active/v10-split-and-bot-names-plan.md`.

## Key files

| file | why |
|---|---|
| `supabase/migrations/375_v10_split_and_bot_display_names.sql` | the whole identity change |
| `workers/jobs/daily_pipeline_v2.py` | `BOTS_CONFIG` (~line 61), `BOT_TIMING_COHORTS` (~line 996) |
| `workers/jobs/coolbet_feed_watchdog.py:125` | `PICKS_BOT` constant |
| `workers/registry/bot_registry.py:177` | the `bot_v10_all` BotSpec |
| `odds-intel-web/src/lib/engine-data.ts:1556` | the `bots` select list |
| `odds-intel-web/src/lib/bot-aggregates.ts` | `BotDbRow`, `PublicBotStatShape`, `PUBLIC_MATURITY_LABELS` |
| `odds-intel-web/src/components/performance-leaderboard.tsx:241,544,625` | where `bot.name` is rendered |
| `odds-intel-web/src/app/(app)/performance/page.tsx:441` | where `maturityLabel: "testing"` is hardcoded |

## Decisions made

* **`bots.name` is never renamed.** It is the join key for `simulated_bets`,
  `shadow_bets`, `real_bets`, `picks_public_all`, `ENGINE_BOT_FLOORS` and every
  analysis script. `display_name` is a new, additive, display-only column.
* **`bot_v10_ou` ships `beta`, not `calibrated`.** Its de-vigged Pinnacle CLV is
  -3.85% with a 95% CI of [-5.01, -2.69] at n=181, negative in every month and
  every model version. `calibrated` is the label the /performance legend sells as
  proven; stamping it here would be the page lying.
* **`show_on_picks` left TRUE on both halves.** The label change is identity-only.
  Whether the O/U half should keep publishing is the owner's call and is flagged
  on the queue row. It has published nothing since 2026-09-13 anyway.
* **`testing` becomes a real DB label** rather than a string the page stamps.

## Display names chosen

The method chip (MODEL / SHARP LINE / CONSENSUS) already tells a reader what a bot
prices against, so the NAME says what it BETS. Names that repeat the chip waste the
only line a reader reads.

| `bots.name` (key, unchanged) | `display_name` |
|---|---|
| `bot_v10_1x2` | Match result |
| `bot_v10_ou` | Goals over/under 2.5 |
| `bot_high_roi_global_v2` | High-odds match result |
| `bot_sharp_forward_test_v1` | Sharp-line picks |
| `bot_consensus_anchor_v1` | Consensus picks |

## Next steps
See `v10-split-and-bot-names-tasks.md`.

## Two test defects found while shipping this (both fixed here)

**`SYSTEM-MAP-REGISTRY-NOT-DRIFTED` discounted the whole fleet.** Its
pending-migration guard computed `_sets_retired` as ONE boolean OR-ed across every
pending file, then discounted any bot NAMED anywhere in that pending set. Migration
375 retires one bot and, in an unrelated statement, sets `display_name` for sixteen
live ones — so every active bot was discounted, `db_effective` went empty and the
assertion failed on a correct commit. Fixed by scoping the check to the STATEMENT
that sets `retired_at`.

**...and the first fix was itself wrong.** Splitting on `;` shatters a
`DO $$ ... $$` block, whose body is full of them. Migration 375's retirement reads
`UPDATE bots SET retired_at = now() WHERE id = v_old`, with the bot NAME thirty
lines earlier in `SELECT id INTO v_old ... WHERE name = 'bot_v10_all'` — different
fragments, so a correctly-written retirement was not seen at all. Now DO blocks are
lifted out as single units before the split. This guard has been widened three
times by unrelated references; it is finally scoped rather than broadened.

**`COOLBET-FEED-WATCHDOG-NO-RETIRED-BOT` pinned a literal.** It asserted
`w.PICKS_BOT == "bot_v10_all"` — the spelling of a constant, not the property that
matters. Re-pinned as `PICKS_BOT in active_names()`, which is what the watchdog
actually needs and would have caught the original bot_coolbet_value_v1 bug too.
RELIABILITY_LEDGER #9.
