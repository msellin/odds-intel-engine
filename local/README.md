# Local Mac daemon — Coolbet placement (option B)

This directory holds everything the Mac-at-home daemon needs to run.
Railway keeps doing data ingestion + edge detection + signaling; this
Mac handles the final Coolbet POST from a residential IP.

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

# 4. Sanity check — daemon does ONE tick and exits
python3 -m workers.automation.coolbet_mac_daemon --once --dry-run

# 5. Install launchd agent
cp local/launchd/com.oddsintel.coolbet-mac-daemon.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.oddsintel.coolbet-mac-daemon.plist
launchctl list | grep oddsintel    # confirm RUNNING with PID
```

## Verifying it works

```bash
# Tail daemon log
tail -f dev/active/coolbet-mac-daemon.log

# Force a tick now instead of waiting POLL_INTERVAL_S
launchctl kickstart -k gui/$(id -u)/com.oddsintel.coolbet-mac-daemon
```

## Mac sleep policy

The daemon runs in the user session, so Mac sleep stops it. Options:

- **Best**: System Settings → Battery → Options → "Prevent automatic
  sleeping when the display is off" while plugged in.
- **OK**: leave the Mac running with display sleep off — daemon stays alive.
- **Acceptable**: let Mac sleep; daemon catches up on missed picks
  when you wake it. Pre-match value bets sit for hours so missing a
  few while asleep is usually fine.

## Coexistence with the signaler

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
