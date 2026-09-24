"""FEED HEALTH (#107 FEEDS-DASHBOARD, 2026-09-23) — evaluate every feed in
workers/registry/feed_registry.py into `feed_status` / `feed_book_stats`
(migration 388), every 5 minutes. /admin/feeds renders those two tables.

A feed's status comes from its registry `health` basis — see the registry
docstring for why output beats the job's own verdict. Rules, deliberately few:

  data     fail  newest row older than stale_after_min
           warn  newest row older than 1.5 × interval (+5 min), or the last job
                 run failed
  runs     fail  2+ failed runs in a row, or no run for stale_after_min
           warn  last run failed
  service  fail  a required systemd unit / docker container is not up
  any      fail  a unit the feed depends on (e.g. the Estonian exit) is down

Never raises past a single feed: one broken check must not blank the page.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, get_conn
from workers.registry.feed_registry import COVERAGE_BOOKS, FEEDS

log = logging.getLogger(__name__)

_OK_RUN = ("completed", "success", "ok")


def _unit_state(unit: str) -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                           text=True, timeout=5)
        return (r.stdout or r.stderr).strip() or "unknown"
    except Exception:  # noqa: BLE001 — no systemd here (e.g. the Mac)
        return "unknown"


def _docker_state(name: str) -> str:
    try:
        r = subprocess.run(["docker", "inspect", "-f",
                            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                            name], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _host_check(kind: str) -> tuple[str, str, str | None]:
    """(status, human state, reason) for a host resource on the VPS."""
    try:
        if kind == "disk":
            import shutil
            u = shutil.disk_usage("/")
            pct = 100 * u.used / u.total
            state = f"{pct:.0f}% used · {u.free / 1e9:.0f} GB free"
            if pct >= 90:
                return "fail", state, f"disk {pct:.0f}% full"
            return ("warn", state, f"disk {pct:.0f}% full") if pct >= 80 else ("ok", state, None)
        if kind == "memory":
            info = {}
            with open("/proc/meminfo") as fh:
                for line in fh:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0])
            avail_gb = info.get("MemAvailable", 0) / 1e6
            state = f"{avail_gb:.1f} GB available of {info.get('MemTotal', 0) / 1e6:.0f} GB"
            if avail_gb < 0.5:
                return "fail", state, f"only {avail_gb:.1f} GB memory available"
            return ("warn", state, f"only {avail_gb:.1f} GB memory available") if avail_gb < 1 else ("ok", state, None)
    except Exception:  # noqa: BLE001 — not readable here (e.g. the Mac)
        pass
    return "unknown", "unknown", "not readable here"


def _age_min(ts) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60


def _fmt_age(m: float | None) -> str:
    if m is None:
        return "never"
    if m < 90:
        return f"{m:.0f} min"
    if m < 60 * 36:
        return f"{m / 60:.1f} h"
    return f"{m / 1440:.1f} d"


def _runs_by_job(jobs: list[str]) -> dict[str, list[dict]]:
    if not jobs:
        return {}
    rows = execute_query(
        """SELECT job_name, status, started_at, completed_at, error_message
             FROM (SELECT *, row_number() OVER (PARTITION BY job_name ORDER BY started_at DESC) rn
                     FROM pipeline_runs
                    WHERE job_name = ANY(%s) AND started_at > now() - interval '7 days') x
            WHERE rn <= 30
            ORDER BY job_name, started_at DESC""", (jobs,)) or []
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["job_name"], []).append(r)
    return out


def _odds_agg() -> dict[str, dict]:
    rows = execute_query(
        """SELECT bookmaker,
                  max(timestamp) AS last_at,
                  count(*) FILTER (WHERE timestamp > now() - interval '1 hour') AS rows_1h,
                  count(*) AS rows_24h,
                  count(*) FILTER (WHERE timestamp >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS rows_today,
                  count(DISTINCT market) AS markets
             FROM odds_snapshots
            WHERE timestamp > now() - interval '24 hours'
            GROUP BY bookmaker""") or []
    out = {r["bookmaker"]: r for r in rows}
    # #117: the exchange writes exchange_quotes, not odds_snapshots — without this the
    # feeds card read "0 prices stored today" on a day with ~12k exchange rows.
    x = (execute_query(
        """SELECT max(captured_at) AS last_at,
                  count(*) FILTER (WHERE captured_at > now() - interval '1 hour') AS rows_1h,
                  count(*) AS rows_24h,
                  count(*) FILTER (WHERE captured_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS rows_today,
                  count(DISTINCT market) AS markets
             FROM exchange_quotes
            WHERE captured_at > now() - interval '24 hours'""") or [{}])[0]
    if x.get("rows_24h"):
        out["Betfair-Exchange"] = {"bookmaker": "Betfair-Exchange", **x}
    return out


def _exchange_liquid() -> dict[str, int]:
    """Our fixtures today / yesterday (UTC) whose LATEST exchange 1X2 is a usable price:
    every runner two-sided with lay/back - 1 <= MAX_SPREAD and the market >= MIN_MARKET_MATCHED
    — the same rule as betfair_exchange_feed.is_liquid. The coverage line counts a fixture
    as priced when it is merely LISTED (thin placeholders included); on 2026-09-24 that read
    65/91 while 9 were usable, which is the number that matters for an anchor."""
    from workers.automation.betfair_exchange_feed import MAX_SPREAD, MIN_MARKET_MATCHED
    rows = execute_query(
        """WITH f AS (SELECT id, (date AT TIME ZONE 'UTC')::date AS d FROM matches
                       WHERE date >= (now() AT TIME ZONE 'UTC')::date - 1
                         AND date <  (now() AT TIME ZONE 'UTC')::date + 1),
                l AS (SELECT DISTINCT ON (x.match_id, x.selection) x.match_id, x.back, x.lay, x.market_matched
                        FROM exchange_quotes x JOIN f ON f.id = x.match_id
                       WHERE x.market = '1x2'
                       ORDER BY x.match_id, x.selection, x.captured_at DESC),
                m AS (SELECT match_id,
                             bool_and(back > 0 AND lay IS NOT NULL AND lay / back - 1 <= %s) AS tight,
                             max(market_matched) AS matched
                        FROM l GROUP BY match_id)
           SELECT f.d, count(*) FILTER (WHERE m.tight AND m.matched >= %s) AS liquid
             FROM f JOIN m ON m.match_id = f.id GROUP BY f.d""",
        (MAX_SPREAD, MIN_MARKET_MATCHED)) or []
    today = datetime.now(timezone.utc).date()
    return {("t" if r["d"] == today else "y"): int(r["liquid"]) for r in rows}


def _table_data(spec: dict) -> dict:
    where = f"AND {spec['where']}" if spec.get("where") else ""
    ts = spec["ts"]
    r = (execute_query(
        f"""SELECT max({ts}) AS last_at,
                   count(*) FILTER (WHERE {ts} > now() - interval '1 hour') AS rows_1h,
                   count(*) AS rows_24h
              FROM {spec['table']}
             WHERE {ts} > now() - interval '24 hours' {where}""") or [{}])[0]
    if r.get("last_at") is None:     # nothing in 24 h — find the true last row
        r2 = (execute_query(f"SELECT max({ts}) AS last_at FROM {spec['table']} WHERE true {where}") or [{}])[0]
        r["last_at"] = r2.get("last_at")
    return r


def _coverage() -> list[tuple]:
    """Our fixtures today / yesterday (UTC) and how many each book priced (1X2 —
    the near-universal market, and the cheap index path)."""
    rows = execute_query(
        """WITH f AS (
               SELECT id, (date AT TIME ZONE 'UTC')::date AS d
                 FROM matches
                WHERE date >= (now() AT TIME ZONE 'UTC')::date - 1
                  AND date <  (now() AT TIME ZONE 'UTC')::date + 1),
           b AS (SELECT unnest(%s::text[]) AS book)
           SELECT b.book, f.d, count(*) AS fixtures,
                  count(*) FILTER (WHERE EXISTS (
                      SELECT 1 FROM odds_snapshots o
                       WHERE o.match_id = f.id AND o.market = '1x2' AND o.bookmaker = b.book)
                    -- #117: the exchange keeps its own table (back/lay + liquidity)
                    OR (b.book = 'Betfair-Exchange' AND EXISTS (
                      SELECT 1 FROM exchange_quotes x
                       WHERE x.match_id = f.id AND x.market = '1x2'))) AS priced
             FROM f CROSS JOIN b
            GROUP BY b.book, f.d""", (list(COVERAGE_BOOKS),)) or []
    today = datetime.now(timezone.utc).date()
    by = {}
    for r in rows:
        k = "t" if r["d"] == today else "y"
        by.setdefault(r["book"], {})[k] = (r["fixtures"], r["priced"])
    return [(b, *(by.get(b, {}).get("t") or (0, 0)), *(by.get(b, {}).get("y") or (0, 0)))
            for b in COVERAGE_BOOKS]


# BOOK FOOTPRINT (#110) — the signals that come BEFORE a block. #108's Imperva flag
# followed hours of 750–1,500 req/h; these warn while there is still time to back off.
FOOTPRINT_WARN_SHARE = 0.8      # of the hourly budget, this clock hour
FOOTPRINT_CHALLENGE_MIN = 5     # bot-check / 403 / 429 answers this hour …
FOOTPRINT_CHALLENGE_RATE = 0.05  # … and at least this share of requests


def _footprint() -> dict[str, dict]:
    """Per book: this clock hour's requests / challenges / errors / refusals, the
    last 24 h's requests, and the budget (workers/utils/footprint.py)."""
    from workers.utils.footprint import budget
    try:
        rows = execute_query(
            """SELECT book,
                      sum(requests)   FILTER (WHERE hour = date_trunc('hour', now())) AS req_1h,
                      sum(challenges) FILTER (WHERE hour = date_trunc('hour', now())) AS ch_1h,
                      sum(errors)     FILTER (WHERE hour = date_trunc('hour', now())) AS err_1h,
                      sum(refused)    FILTER (WHERE hour = date_trunc('hour', now())) AS ref_1h,
                      sum(requests) AS req_24h
                 FROM book_footprint WHERE hour > now() - interval '24 hours'
                GROUP BY book""") or []
    except Exception as e:  # noqa: BLE001 — table may predate migration 392
        log.debug("footprint read failed: %s", e)
        return {}
    return {r["book"]: {"requests_1h": int(r["req_1h"] or 0), "challenges_1h": int(r["ch_1h"] or 0),
                        "errors_1h": int(r["err_1h"] or 0), "refused_1h": int(r["ref_1h"] or 0),
                        "requests_24h": int(r["req_24h"] or 0), "budget_1h": budget(r["book"])}
            for r in rows}


def footprint_warnings(fp: dict | None) -> list[str]:
    """Early-warning reasons for one book's footprint (empty when quiet)."""
    if not fp:
        return []
    out = []
    req, cap = fp.get("requests_1h") or 0, fp.get("budget_1h")
    if fp.get("refused_1h"):
        out.append(f"request budget spent — {fp['refused_1h']} requests refused this hour")
    elif cap and req >= FOOTPRINT_WARN_SHARE * cap:
        out.append(f"{req}/{cap} requests this hour — near the budget")
    ch = fp.get("challenges_1h") or 0
    if ch >= FOOTPRINT_CHALLENGE_MIN and req and ch / req >= FOOTPRINT_CHALLENGE_RATE:
        out.append(f"{ch} bot-check answers this hour ({ch / req:.0%}) — back off before a block")
    return out


def evaluate() -> list[dict]:
    runs = _runs_by_job([f["job"] for f in FEEDS if f.get("job")])
    odds = _odds_agg()
    fp = _footprint()
    out = []
    for f in FEEDS:
        try:
            row = _evaluate_one(f, runs.get(f.get("job"), []), odds)
            # One warning per book: on its pre-match block, which is the one the
            # operator opens — and never over a louder fail/paused state.
            warn = footprint_warnings(fp.get(f.get("book"))) if f["id"].endswith("_prematch") else []
            if warn and row.get("status") in ("ok", "warn"):
                row["status"] = "warn"
                row["status_reason"] = "; ".join(filter(None, [row.get("status_reason"), *warn]))
            out.append(row)
        except Exception as e:  # noqa: BLE001 — one check must not blank the page
            log.warning("feed_health %s failed: %s", f["id"], e)
            out.append({"feed_id": f["id"], "status": "unknown",
                        "status_reason": f"health check error: {str(e)[:150]}"})
    return out


def _evaluate_one(f: dict, runs: list[dict], odds: dict) -> dict:
    row = {"feed_id": f["id"]}
    reasons_fail, reasons_warn = [], []

    if f.get("host"):
        st, state, reason = _host_check(f["host"])
        row.update(service_state={f["host"]: state}, status=st, status_reason=reason)
        return row

    # services
    svc = {u: _unit_state(u) for u in f.get("units") or []}
    if f.get("docker"):
        svc[f["docker"]] = _docker_state(f["docker"])
    down = [k for k, v in svc.items() if v not in ("active", "healthy", "running", "unknown")]
    if down:
        reasons_fail.append("down: " + ", ".join(f"{k} ({svc[k]})" for k in down))
    row["service_state"] = svc

    # job runs
    if runs:
        last = runs[0]
        row["last_run_at"] = last["started_at"]
        row["last_run_status"] = last["status"]
        if last.get("completed_at") and last.get("started_at"):
            row["last_run_seconds"] = round((last["completed_at"] - last["started_at"]).total_seconds(), 1)
        ok_runs = [r for r in runs if r["status"] in _OK_RUN]
        row["last_success_at"] = ok_runs[0]["started_at"] if ok_runs else None
        err = next((r["error_message"] for r in runs if r["status"] not in _OK_RUN and r.get("error_message")), None)
        row["last_error"] = (err or "")[:500] or None
        recent = [r for r in runs if _age_min(r["started_at"]) <= 1440]
        row["runs_24h"] = len(recent)
        row["failures_24h"] = sum(1 for r in recent if r["status"] not in _OK_RUN + ("running",))
        streak = 0
        for r in runs:
            if r["status"] in _OK_RUN or r["status"] == "running":
                break
            streak += 1
        row["fail_streak"] = streak
    elif f.get("job"):
        row.update(runs_24h=0, failures_24h=0, fail_streak=0)

    # data written
    d = f.get("data") or {}
    if d.get("odds_books"):
        hits = [odds[b] for b in d["odds_books"] if b in odds]
        if hits:
            row["last_data_at"] = max(h["last_at"] for h in hits)
            row["rows_1h"] = sum(h["rows_1h"] for h in hits)
            row["rows_24h"] = sum(h["rows_24h"] for h in hits)
    elif d.get("table"):
        t = _table_data(d)
        row["last_data_at"], row["rows_1h"], row["rows_24h"] = t.get("last_at"), t.get("rows_1h"), t.get("rows_24h")

    data_age = _age_min(row.get("last_data_at"))
    stale, interval = f.get("stale_after_min"), f.get("interval_min")
    basis = f["health"]
    if basis == "data":
        if data_age is None or (stale and data_age > stale):
            reasons_fail.append(f"no data for {_fmt_age(data_age)} (stale after {stale} min)")
        elif interval and data_age > interval * 1.5 + 5:
            reasons_warn.append(f"last data {_fmt_age(data_age)} ago (runs every {interval} min)")
        if row.get("last_run_status") and row["last_run_status"] not in _OK_RUN + ("running",):
            reasons_warn.append("last run failed")
    elif basis == "runs":
        run_age = _age_min(row.get("last_run_at"))
        if not runs and f.get("job"):
            # A brand-new job before its first scheduled run is not a fault — it
            # read as red "not run for never" on the day Tonybet results shipped.
            row["status"], row["status_reason"] = "unknown", "waiting for its first scheduled run"
            return row
        if row.get("fail_streak", 0) >= 2:
            reasons_fail.append(f"{row['fail_streak']} failed runs in a row")
        elif row.get("last_run_status") and row["last_run_status"] not in _OK_RUN + ("running",):
            reasons_warn.append("last run failed")
        if run_age is None or (stale and run_age > stale):
            reasons_fail.append(f"not run for {_fmt_age(run_age)}")
    elif basis == "service" and d and stale and (data_age is None or data_age > stale):
        reasons_warn.append(f"no data for {_fmt_age(data_age)}")

    if reasons_fail:
        row["status"], row["status_reason"] = "fail", "; ".join(reasons_fail + reasons_warn)
    elif reasons_warn:
        row["status"], row["status_reason"] = "warn", "; ".join(reasons_warn)
    elif basis == "service" and not svc:
        row["status"], row["status_reason"] = "unknown", "no service check configured"
    elif basis == "service" and all(v == "unknown" for v in svc.values()):
        row["status"], row["status_reason"] = "unknown", "service state not readable here"
    else:
        row["status"], row["status_reason"] = "ok", None
    return row


_COLS = ("feed_id", "label", "book", "category", "kind", "schedule", "interval_min",
         "stale_after_min", "health_basis", "status", "status_reason", "last_run_at",
         "last_run_status", "last_run_seconds", "last_success_at", "last_error", "runs_24h",
         "failures_24h", "fail_streak", "last_data_at", "rows_1h", "rows_24h",
         "service_state", "runbook", "controls", "paused", "paused_reason", "paused_by",
         "paused_at", "run_now_pending", "auto_resume_at")

FEEDS_URL = "https://www.oddsintel.app/admin/feeds"


def _controls() -> dict[str, dict]:
    try:
        return {r["feed_id"]: r for r in execute_query(
            """SELECT feed_id, paused, paused_reason, paused_by, paused_at, auto_resume_at,
                      (run_now_requested_at IS NOT NULL AND (run_now_started_at IS NULL
                         OR run_now_started_at < run_now_requested_at)) AS run_now_pending
                 FROM feed_controls""") or []}
    except Exception:  # noqa: BLE001 — table missing before migration 389
        return {}


def _alert_transitions(prev: dict[str, str], evals: list[dict]) -> int:
    """Telegram on TRANSITIONS only: a feed newly red, or recovered from red. A feed
    already red does not re-alert every 5 min; a paused feed never alerts (the
    operator chose that). #108 ran 4 h with nobody told — this is the fix."""
    from workers.notify.telegram import send_telegram
    from workers.registry.feed_registry import FEEDS_BY_ID
    sent = 0
    for e in evals:
        fid, new, old = e["feed_id"], e["status"], prev.get(e["feed_id"])
        if old is None or new == old or new == "paused" or old == "paused":
            continue
        label = FEEDS_BY_ID[fid]["label"]
        if new == "fail":
            msg = (f"🔴 <b>{label}</b> is failing\n{e.get('status_reason') or ''}\n"
                   f"<a href=\"{FEEDS_URL}\">Open /admin/feeds</a> — pause, run now, or check the error")
        elif old == "fail" and new == "ok":
            msg = f"🟢 <b>{label}</b> recovered\n<a href=\"{FEEDS_URL}\">/admin/feeds</a>"
        else:
            continue
        try:
            send_telegram(msg, dedup_key=f"feed-{fid}-{new}", dedup_window_s=3600)
            sent += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("feed alert %s failed: %s", fid, ex)
    return sent


def run_feed_health() -> dict:
    from workers.registry.feed_registry import FEEDS_BY_ID
    evals = evaluate()
    try:  # circuit breaker first, so this run already renders the new pause
        from workers.jobs.feed_control import apply_auto_pause
        apply_auto_pause(evals)
    except Exception as ex:  # noqa: BLE001 — never let the breaker blank the page
        log.warning("auto-pause failed: %s", ex)
    ctl = _controls()
    prev = {r["feed_id"]: r["status"] for r in (execute_query(
        "SELECT feed_id, status FROM feed_status") or [])}
    for e in evals:
        c = ctl.get(e["feed_id"]) or {}
        e["paused"] = bool(c.get("paused"))
        e["paused_reason"], e["paused_by"], e["paused_at"] = (
            c.get("paused_reason"), c.get("paused_by"), c.get("paused_at"))
        e["run_now_pending"] = bool(c.get("run_now_pending"))
        e["auto_resume_at"] = c.get("auto_resume_at")
        if e["paused"]:
            e["status"] = "paused"
            if c.get("paused_by") == "auto":
                e["status_reason"] = (c.get("paused_reason") or "auto-paused") + (
                    f" — next test {c['auto_resume_at']:%H:%M} UTC" if c.get("auto_resume_at") else "")
            else:
                e["status_reason"] = (f"paused by {c.get('paused_by') or 'operator'}"
                                      + (f": {c['paused_reason']}" if c.get("paused_reason") else ""))
    rows = []
    for e in evals:
        f = FEEDS_BY_ID[e["feed_id"]]
        merged = {"label": f["label"], "book": f.get("book"), "category": f["category"],
                  "kind": f.get("kind"), "schedule": f.get("schedule"),
                  "interval_min": f.get("interval_min"), "stale_after_min": f.get("stale_after_min"),
                  "health_basis": f["health"], "runbook": f.get("runbook"),
                  "controls": f.get("controls") or [], **e}
        merged["service_state"] = json.dumps(merged.get("service_state") or {})
        rows.append(tuple(merged.get(c) for c in _COLS))
    coverage = _coverage()
    odds = _odds_agg()
    fp = _footprint()
    liq = _exchange_liquid()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO feed_status ({', '.join(_COLS)}, updated_at)
                    VALUES ({', '.join(['%s'] * len(_COLS))}, now())
                    ON CONFLICT (feed_id) DO UPDATE SET
                    {', '.join(f'{c} = EXCLUDED.{c}' for c in _COLS[1:])}, updated_at = now()""",
                rows)
            cur.execute("DELETE FROM feed_status WHERE NOT (feed_id = ANY(%s))",
                        ([e["feed_id"] for e in evals],))
            cur.executemany(
                """INSERT INTO feed_book_stats (book, fixtures_today, priced_today, fixtures_yesterday,
                         priced_yesterday, rows_today, market_families, last_row_at,
                         requests_1h, budget_1h, challenges_1h, errors_1h, requests_24h, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (book) DO UPDATE SET
                     fixtures_today = EXCLUDED.fixtures_today, priced_today = EXCLUDED.priced_today,
                     fixtures_yesterday = EXCLUDED.fixtures_yesterday,
                     priced_yesterday = EXCLUDED.priced_yesterday, rows_today = EXCLUDED.rows_today,
                     market_families = EXCLUDED.market_families, last_row_at = EXCLUDED.last_row_at,
                     requests_1h = EXCLUDED.requests_1h, budget_1h = EXCLUDED.budget_1h,
                     challenges_1h = EXCLUDED.challenges_1h, errors_1h = EXCLUDED.errors_1h,
                     requests_24h = EXCLUDED.requests_24h, updated_at = now()""",
                [(b, ft, pt, fy, py, (odds.get(b) or {}).get("rows_today"),
                  (odds.get(b) or {}).get("markets"), (odds.get(b) or {}).get("last_at"),
                  *((fp.get(b) or {}).get(k) for k in
                    ("requests_1h", "budget_1h", "challenges_1h", "errors_1h", "requests_24h")))
                 for b, ft, pt, fy, py in coverage])
            cur.execute("""UPDATE feed_book_stats SET liquid_today = %s, liquid_yesterday = %s
                            WHERE book = 'Betfair-Exchange'""", (liq.get("t", 0), liq.get("y", 0)))
        conn.commit()
    counts = {}
    for e in evals:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    counts["alerts"] = _alert_transitions(prev, evals)
    return {"feeds": len(evals), **counts}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for e in evaluate():
        print(f"{e['status']:7s} {e['feed_id']:18s} {e.get('status_reason') or ''}")
