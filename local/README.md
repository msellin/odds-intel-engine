# Local Mac setup — FlareSolverr, Coolbet session, launchd jobs

> **CHANGED 2026-09-25 (#162 W4.6).** This page used to set up the paper
> **Coolbet Mac daemon** (`workers.automation.coolbet_mac_daemon`, plist
> `com.oddsintel.coolbet-mac-daemon`). That daemon was retired 2026-09-10 and
> its code, keepalive script and plist are now **deleted**. Real money is placed
> only by the UI placer (`scripts/place_coolbet_ui.py`) and the best-price router
> (`workers/automation/best_price_router.py`) — their plists live in
> `local/launchd/paused/`. The session-keep and the Telegram heal button the
> daemon used to run are in the feed watchdog
> (`com.oddsintel.coolbet-feed-watchdog`). Steps 1–3 below are still current;
> the sections after "Mac sleep policy" describe the retired daemon and are kept
> as history only.

This directory holds what the Mac-at-home side needs: the local FlareSolverr,
the CDP-Chrome session and the launchd jobs in `local/launchd/`.

## One-time setup

```bash
# 1. Start local FlareSolverr (replaces the Railway FS for Coolbet)
cd local/flaresolverr
docker compose up -d
curl http://localhost:8191/    # should return "FlareSolverr is ready!"

# 2. Point .env at the local FS instead of Railway
#    Edit .env and change:
#      FLARESOLVERR_URL=https://flaresolverr-cf-production.up.railway.app
#    to:
#      FLARESOLVERR_URL=http://localhost:8191

# 3. Run enrollment ONE TIME against the local FS (this is the
#    last manual SMS the operator will see for months — local Chrome
#    profile is persisted in the docker volume oi_local_flaresolverr_profile)
python3 scripts/coolbet/flaresolverr_login_enroll.py start
# (wait for SMS)
python3 scripts/coolbet/flaresolverr_login_enroll.py verify 123456

# 4. Install the launchd jobs you need from local/launchd/ (NOT paused/ or
#    retired/ — the placers stay paused until the owner arms real money)
cp local/launchd/com.oddsintel.coolbet-feed-watchdog.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.oddsintel.coolbet-feed-watchdog.plist
launchctl list | grep oddsintel
python3 scripts/ops/launchd_drift_check.py   # installed copies == repo copies
```

## Mac sleep policy

The launchd jobs run in the user session, so Mac sleep stops it. Options:

- **Best**: System Settings → Battery → Options → "Prevent automatic
  sleeping when the display is off" while plugged in.
- **OK**: leave the Mac running with display sleep off — daemon stays alive.
- **Acceptable**: let Mac sleep; daemon catches up on missed picks
  when you wake it. Pre-match value bets sit for hours so missing a
  few while asleep is usually fine.

## Coexistence with the signaler (HISTORICAL — retired daemon)

The signaler keeps running on Railway, sending Telegram messages with
inline ✅ Placed / ⏭ Skip buttons. Either side wins:

- **Daemon places first**: writes to `real_bets`. Next signal-cohort
  tick's `NOT EXISTS (SELECT 1 FROM real_bets ...)` filter skips this
  pick, so no duplicate Telegram. (For picks ALREADY signaled before
  daemon placed, the message stays in chat — operator can ignore.)
- **Operator taps ✅ Placed first**: webhook updates
  `simulated_bets.user_placed_at` but does NOT write to `real_bets`
  (we don't have ticket/odds/stake from a manual placement). The
  daemon may still try to place a fresh row — that's a duplicate the
  operator needs to settle by checking Coolbet account history. To
  avoid this, run the daemon AND don't tap manual buttons.
- **Daemon offline (Mac sleeping)**: signal still fires. Operator taps
  ✅ Placed after manual placement. Daemon catches up when Mac wakes.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Daemon log says `FLARESOLVERR_URL` unset | `.env` not loaded | Run daemon from repo root; `dotenv` looks for `.env` in cwd. |
| `tab crashed` in FS logs | Out-of-memory in Docker | `docker compose restart flaresolverr`. Volume preserves device trust. |
| 403 on Coolbet login | Residential IP rotated and lost Imperva trust | Re-run enrollment. Cookies refresh, trust marker persists in profile volume. |
| `JWT expired and api_login disabled` | DB JWT expired AND no opt-in to login | Run `python3 scripts/coolbet/flaresolverr_login_enroll.py start` once. |
| `launchctl list` shows status != 0 | Crash loop — see log | `tail -100 dev/active/coolbet-mac-daemon.log` |
| `placement_paused=TRUE` | Operator-set kill switch | Set false via DB: `UPDATE coolbet_session_state SET placement_paused=FALSE WHERE id=1;` |
