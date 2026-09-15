# Paused launchd agents — OWN Phase 0 (2026-09-15)

Both `--execute` real-money jobs are **unloaded** on the operator's Mac
(`launchctl bootout`) and their installed copies live in
`~/Library/LaunchAgents/paused/`. The repo copies were moved here on
2026-09-15 so `scripts/ops/launchd_drift_check.py` stops reporting them as
"NOT-LOADED" drift and so nobody reinstalls one by copying the whole folder.

**Why they are parked, not deleted:** real money is paused
(migration 343, `placement_paused`) and disarmed (migration 354,
`real_money_armed = FALSE`). A paused product must not depend on an allowlist
happening to be empty or an env var happening to be unset — `ROUTER_ALLOW_REAL`
was in fact SET, so `best-price-router` ran in real mode every 30 minutes and
staked nothing only because it found no candidates.

**Reload ONLY after Phase 3 of `dev/active/own-implementation-plan.md`** (the
owner arms `real_money_armed` with a reason):

```bash
cp local/launchd/paused/com.oddsintel.coolbet-ui-placer.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.oddsintel.coolbet-ui-placer.plist
```

Every executor still calls `workers/automation/placement_gate.py` first, so a
reload alone cannot stake while either flag is in the safe state.
