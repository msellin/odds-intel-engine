# CS2 Coolbet Automated Placer — Plan

## Goal

Automatically place (paper-first) CS2 bot picks on Coolbet, capturing
the gap between bot's `odds_at_pick` and Coolbet's actual price at
placement time. Real-money execution stays gated behind explicit
`--execute` authorization (memory: `feedback_coolbet_execute_safety`).

## What we already have

| Piece | Status |
|---|---|
| `CoolbetSession` (JWT auth, anon API) | ✅ `workers/automation/coolbet_session.py` |
| `coolbet_placer.py` (soccer) | ✅ reference implementation |
| `cs2_coolbet_scanner.py` | ✅ already scrapes Coolbet CS2 odds → `cs2_upcoming_matches.bookie_odds` |
| `cs2_bot.py` fires v8 picks every 30min | ✅ writes to `cs2_simulated_bets` |
| `bots` table (bot_cs2_v8 etc.) | ✅ tracked |

## What's missing

Just one script that bridges the gap:

```
cs2_simulated_bets   →   cs2_coolbet_placer.py   →   cs2_real_bets
  (bot picks)         (verify + record)             (paper for now)
```

## Schema — new table `cs2_real_bets`

Mirrors soccer's `real_bets` but:
- `cs2_simulated_bet_id` (FK to cs2_simulated_bets.id)
- `bo3gg_id` not `match_id`
- `bot_name` directly (matches CS2 convention)
- `paper` boolean (true for now — flip to false manually after operator review)

## Placement flow (paper mode v1)

1. Query unplaced fresh picks:
   ```sql
   SELECT * FROM cs2_simulated_bets sb
   WHERE result IS NULL
     AND kickoff_time > NOW()
     AND kickoff_time < NOW() + INTERVAL '24h'
     AND bot_name LIKE 'bot_cs2_%'
     AND NOT EXISTS (SELECT 1 FROM cs2_real_bets rb WHERE rb.cs2_simulated_bet_id = sb.id)
   ```

2. For each, look up Coolbet odds from
   `cs2_upcoming_matches.bookie_odds` JSON keyed by bookmaker name.

3. Compute slippage:
   ```
   slippage_pct = (coolbet_odds - bot.odds_at_pick) / bot.odds_at_pick
   ```

4. Gate: skip if slippage < -5% (Coolbet moved against us, no longer +EV).

5. Insert into `cs2_real_bets` with `paper = true`, `stake = sb.stake_eur`,
   `captured_odds = coolbet_odds`, `clv_pinnacle = NULL` (filled at
   settlement via `cs2_clv_snapshot`).

## Scheduler

Every 30 min, fire 2 min after `cs2_bot` so picks have just landed.
`CronTrigger(hour="10-23", minute="8,38")`.

## Settlement

Extend `cs2_settlement.py` to also update `cs2_real_bets.result` and
`pnl` once the match finishes. PnL = stake × (odds - 1) if won,
−stake if lost, 0 if voided.

## Real-money flip (out of scope for v1)

Adding `--execute` later requires:
- Operator authorization in chat (`EXECUTE AUTHORIZED`)
- POST to Coolbet `/c/bet/place` with the bet selection ID
- Capture ticket_id, log to cs2_real_bets.ticket_id
- Skip flag: if any failed pre-flight (balance check, market closed, etc.)

## Acceptance criteria (v1, paper only)

- [ ] Migration 233 adds `cs2_real_bets` table
- [ ] `cs2_coolbet_placer.py` runs in `--record` mode, picks land in DB
- [ ] Scheduler cron wired
- [ ] Smoke test asserts column existence + placer functions
- [ ] No `--execute` path yet
