# Uptime Kuma — Push Monitor Setup (Tier 1)

The scheduler is wired to push heartbeats to Uptime Kuma from
`workers/utils/kuma.py`. This doc lists the 7 monitors to create in the
Kuma UI and how to configure the VPS environment so the pushes actually
land.

- **Kuma dashboard:** https://status.oddsintel.app
- **Helper:** `workers/utils/kuma.py::push(job_id, ...)`
- **Where pushes fire from:** `workers/scheduler.py::_run_job` (all jobs
  that flow through it) + explicit calls in `job_coolbet_health_ping`
  and `job_healthcheck_ping`
- **Silent by default:** if `KUMA_URL_BASE` or a job's `KUMA_TOKENS`
  entry is missing, `push()` is a no-op. Adding a token = turning on
  the monitor for that job. Removing = turning off.

---

## 1. Create the monitors in Kuma UI

In `status.oddsintel.app`, click **Add New Monitor** for each row. Type
= **Push**. Copy the resulting token from the "Push URL" Kuma shows
(the last path segment) into a note; you'll bundle them into `KUMA_TOKENS`
in step 2.

| # | Kuma monitor name        | KUMA_TOKENS key       | Cadence                       | Heartbeat interval | Retries × interval | Notify? |
|---|--------------------------|-----------------------|-------------------------------|--------------------|--------------------|---------|
| 1 | Morning Pipeline         | `morning_pipeline`    | daily 04:00 UTC               | 90 min             | 2 × 30 min         | **Yes** |
| 2 | Betting Refresh          | `betting_refresh`     | every 30 min                  | 90 min             | 2 × 15 min         | **Yes** |
| 3 | Settlement               | `settlement`          | daily 21:00 / 23:30 / 01:00   | 4 h                | 2 × 30 min         | **Yes** |
| 4 | Coolbet Health Ping      | `coolbet_health_ping` | every 5 min                   | 15 min             | 2 × 5 min          | **Yes** |
| 5 | Scheduler Heartbeat      | `healthcheck_ping`    | every 5 min                   | 15 min             | 2 × 5 min          | **Yes** (root-cause signal) |

> **CS2 monitors retired 2026-09-01.** `cs2_bot` and `cs2_v8_predict` were
> Tier-1 until CS2-REMOVAL-2026-08-26 deleted the jobs. **Delete these two
> monitors in the Kuma UI** — a push monitor whose job no longer exists goes
> "down" forever and trains you to ignore the dashboard. Also drop their keys
> from `KUMA_TOKENS`.

**About "Heartbeat interval"** — Kuma's own term for how often it
*checks* whether a push arrived. Set it to slightly less than the job's
own cadence so a single missed fire doesn't page. **Retries × interval**
sets the grace window after Kuma first notices silence before it fires
the notification.

**CS2 v8 quiet hours:** monitor 5's job only runs 10-23 UTC. Kuma will
alert every night at 23:00+ if you don't set a maintenance window. In
Kuma → Monitor 5 → **Maintenance**, add a daily recurring window
`23:00-10:00 UTC` so overnight silence doesn't page.

**Notify targets:** configure once in Kuma → **Settings → Notifications**
(Telegram / email / etc.) and select for each of the 7 monitors. Kuma
handles de-dup so a stuck monitor pages once, not every retry.

---

## 2. Wire the tokens into the VPS

Two env vars land in `/opt/odds-intel-engine/.env` on the VPS:

```bash
KUMA_URL_BASE=https://status.oddsintel.app/api/push
KUMA_TOKENS={"morning_pipeline":"AbCd1234","betting_refresh":"...","settlement":"...","coolbet_health_ping":"...","healthcheck_ping":"..."}
```

`KUMA_TOKENS` is one JSON object on a single line. Missing keys = the
corresponding job silently doesn't ping (Kuma will treat as if the
monitor doesn't exist — no false alerts). Malformed JSON = the module
logs a warning at import and silently no-ops everything.

Apply:

```bash
sshpass -p "$VPS_ROOT_PASSWORD" ssh root@204.168.199.8
cd /opt/odds-intel-engine
# Edit .env — either nano or the sed one-liner if the env line is a
# straight replacement.
systemctl restart oddsintel-scheduler
journalctl -u oddsintel-scheduler -n 40 --no-pager | grep -i kuma
```

If the token JSON parsed cleanly, you'll see nothing about Kuma in the
logs (silent by design). If it didn't, you'll see the parse-error
warning from `workers/utils/kuma.py`.

---

## 3. Verify pings are landing

Wait one full cadence for the fastest job (`coolbet_health_ping` and
`healthcheck_ping` fire every 5 min). Then in Kuma each monitor should
show a green tile with the last ping timestamp. If a tile stays gray
after 6 min, the token is wrong or the env var didn't reload — check
`journalctl -u oddsintel-scheduler` for the kuma warning line.

**Manual test from the VPS** (bypasses the scheduler):

```bash
cd /opt/odds-intel-engine
venv/bin/python3 -c "
from workers.utils.kuma import push
push('healthcheck_ping', status='up', msg='manual verify')
"
```

That should turn monitor #7 green within a few seconds.

---

## 4. Tier 2 (later)

Not yet monitored — surface on the Kuma status page but no paging:

- `fetch_odds` (every 30 min)
- `fetch_fixtures` (daily 04:00 UTC)
- `fetch_enrichment` (daily 04:15 / 12:00 / 16:00 UTC)
- `fetch_predictions` (daily 05:30 UTC)
- `betting_pipeline` (daily 06:00 UTC — subset of morning_pipeline)
- `live_poller` / `live_tracker` (continuous during match hours)
- `news_checker` (5 slots daily)
- `write_ops_snapshot` (hourly)
- `weekly_retrain` + `weekly_meta_retrain` (Sundays)
- `pipeline_failure_alerter` (hourly)

Add them the same way when you're ready — the scheduler side already
pushes for every job that flows through `_run_job`, so it's just
"create the monitor + add the token to `KUMA_TOKENS`."

---

## 5. External safety net (do this once)

Kuma itself runs on the same VPS. If the VPS dies, so does Kuma → no
paging. Add ONE external monitor to catch that failure mode:

- **healthchecks.io** free tier — create a check at `hc-ping.com/…`,
  point Kuma's own "Push URL" for a "Kuma alive" monitor at it, and
  configure healthchecks.io to email/SMS you on missed pings.
- OR **BetterUptime** free tier — HTTP monitor on
  `https://status.oddsintel.app/`, alerts if the page 5xx's or
  times out.

Without this, a full-VPS outage means Kuma can't tell you Kuma is down.
