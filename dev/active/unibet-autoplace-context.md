# UNIBET-AUTOPLACE + GATE CONSOLIDATION — context / handoff

> ## ⚠️ OPERATOR STEP OUTSTANDING (2026-09-11)
> One command, needs your shell (installing a launchd agent is not something the
> agent does):
> ```bash
> cp local/launchd/com.oddsintel.coolbet-feed-watchdog.plist ~/Library/LaunchAgents/
> ```
> It is already unloaded by the footprint pause, so `coolbet_pause_resume.sh
> resume` will pick up the new copy — no separate unload/load needed. Verify with
> `python3 scripts/ops/launchd_drift_check.py`.
>
> **What it turns on:** `COOLBET_AUTO_LOGIN_ON_HEAL=true`, so the daily
> inactivity logout self-heals. **Why it was never actually on:** the only plist
> setting it belonged to the paper mac-daemon **retired 2026-09-10**, and `.env`
> never carried it — so every past "just set this flag" recommendation landed in
> a file no running process read. Session-keep now lives in the feed-watchdog,
> which is why the flag now lives in the watchdog's plist.
>
> ## Coolbet: still challenged, but check it the fast way now
> ```bash
> python3 -m workers.automation.coolbet_explorer --probe --fresh-session   # ~2s
> ```
> Re-verified 2026-09-11: **1.8s, 881-byte challenge page** — the flag is LIVE,
> so the footprint stays paused. The plain `--probe` burns 60.4s and returns 0
> bytes because the long-lived `coolbet_prod` FS session is *also* wedged; that
> was a second, separate fault hiding behind the same "challenged" label, and it
> now reports as `wedged` (exit 3). A fresh session showing `challenged` is the
> real verdict — and cycling sessions is NOT a way around it.

**Handoff written 2026-09-11.** Read this first, then `-tasks.md`.
Single most useful companion: **`docs/RELIABILITY_LEDGER.md`** (the recurring
failure patterns) and **`docs/SYSTEM_MAP.md` §4** (the gate stack, rewritten).

---

## LIVE STATE — verify before doing anything

| Thing | State | How to check |
|---|---|---|
| Coolbet footprint | ⏸ **PAUSED** (odds-snapshot + feed-watchdog unloaded) | `bash scripts/ops/coolbet_pause_resume.sh status` |
| Auto-resume | **ARMED** — it can resume on its own | same command |
| Coolbet reachability | **CHALLENGED** — re-verified 2026-09-11 on a fresh session (1.8s, 881-byte challenge page) | `coolbet_explorer --probe --fresh-session` (2s; plain `--probe` takes 60s and reports `wedged`) |
| Coolbet UI placer | ✅ **LIVE, placing real money** — never paused | `python3 -m workers.automation.coolbet_control --status` |
| 24h real bets | 7 placed, €70 staked | daily summary, now accurate |
| `ROUTER_ALLOW_REAL` | **UNSET** — router is report-only | owner gate, do not set |
| `COOLBET_AUTO_LOGIN_ON_HEAL` | ✅ **SET 2026-09-11** in the feed-watchdog plist (NOT `.env` — see the operator step at the top; it was DEAD before, set only in the retired daemon's plist) | `python3 scripts/ops/launchd_drift_check.py` |

⚠️ **The Coolbet feed is paused deliberately, not broken.** Do not "fix" it by
resuming the sweep to see if it works — that is what sustains the escalation.
Use `--probe --fresh-session` (ONE request, ~2s, safe while paused). Resume only
after it says `OK`.

⚠️ **The 🔄 Heal button in Telegram is inert while the watchdog is paused** — its
drain lives in `coolbet_feed_watchdog.py:446`. Pause/Resume still work (direct DB
writes). Resuming the watchdog restores Heal.

---

## THE BIG PICTURE (owner's framing, 2026-09-11)

Three phases. Phase 1 is done, phase 2 is most of the way, phase 3 is unstarted.

1. **MAP** — understand every place a pick is gated/floored/limited. ✅ Done;
   the result is `SYSTEM_MAP.md` §4 (rewritten) + §4e (generation-stage gates).
2. **CONSOLIDATE** — remove hardcoded copies so one change reaches everywhere.
   ✅ Python done, ✅ frontend done, ✅ shadow-mirror SQL done — **phase 2 COMPLETE**.
3. **CONFIGURE** — make limits editable from a local dashboard (e.g. separate
   "picks → Telegram" policy from "picks → real money" policy).

**Design rule the owner set, and it is the right one:** callers pass **WHO THEY
ARE** (market, selection, odds) — *never* their own floor %. Passing floors as
arguments keeps every copy and merely relocates the duplication.

**Safety note for phase 3 (agreed, not yet built):** the moment a real-money
floor is editable from a web form, a typo becomes a financial event. That layer
needs bounds the UI cannot exceed, an audit trail, and owner-gating on the
staking half. The picks/Telegram policy can be freely tunable; the staking policy
should not be one click away.

---

## WHAT WAS FIXED TODAY (and why it matters)

Four real-money defects, all found by auditing rather than by failures:

1. **`PLACER-EDGE-GATE-FAILED-OPEN`** — `place_coolbet_ui.py` has NO edge
   comparison of its own; the only edge gate was `stage_bet`'s min-odds check,
   and it read `if floor is not None and ...`. When no floor was computable the
   gate was SKIPPED and the bet placed ungated — *including* picks whose
   probability was at or below the bot's threshold, i.e. those that could never
   clear at any price. Now fails closed.
2. **`EDGE-FLOOR-ONE-PREDICATE`** — `edge_percent` is a Decimal, floors are
   floats, and `float(0.08)` is fractionally larger than exact `0.08`. So
   `Decimal("0.0800") < 0.08` is True and every pick sitting EXACTLY on its floor
   was silently dropped by the signaler — while the placer (which casts to float)
   staked them. Fixed by sharing the *predicate*, not just the floor.
3. **`EDGE-FLOOR-ALL-CALLERS`** — `best_price_router.decide_book` gated on the
   per-bot threshold alone, which is selection-blind, so it applied the 1x2 bot's
   10% to AWAY and HOME-FAV selections where the backtest requires 13%. The
   newest real-money path was the most permissive on exactly the selections
   FAVLONG-CUTS excluded. Rule now: **two policies, both must pass, stricter wins.**
4. **Unibet double-bet** — `unibet_placer` wrote NOTHING anywhere, and the
   cross-book dedup reads `real_bets`, so a confirmed Unibet bet was invisible and
   the next pass would place it again. Plus an unreadable balance reported a live
   bet as not-placed (same bug, second route) — now a third `uncertain` state that
   records `placed_real=NULL` (counts as exposure, does not claim confirmation).

Plus: the Coolbet feed's recurring death is **largely self-inflicted** — the board
sweep fetched every category then applied the horizon (802 events fetched, 215
near-term, passes overlapping into a continuous stream from one IP).

---

## KNOWN-GOOD COMMANDS

```bash
# is Coolbet answering? ONE request, safe while paused
python3 -m workers.automation.coolbet_explorer --probe

# resume the footprint (ONLY after --probe says OK)
bash scripts/ops/coolbet_pause_resume.sh resume

# can real money be placed right now?
python3 -m workers.automation.coolbet_control --status

# what would the router do? (report-only, touches nothing)
python3 -m workers.automation.best_price_router

# regenerate the frontend's floors after ANY engine floor change
python3 scripts/gen_frontend_floors.py

# installed-vs-repo launchd drift (bit us for 5.3h)
# Replaced the `diff -q` one-liner 2026-09-11: it byte-compared, so 3 of its 7
# lines were false alarms (two plists differed only in INDENTATION, which launchd
# cannot see; one was a deliberately retired job). This parses the plists, names
# the offending keys, skips retired/, and exits 1 on real drift.
python3 scripts/ops/launchd_drift_check.py --verbose
```
