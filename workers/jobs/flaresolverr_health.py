"""
FLARESOLVERR-HEALTH-WATCHDOG (2026-09-07) — COOLBET-FS-WATCHDOG-AND-ENV.

Why this exists, and why it is separate from COOLBET-ODDS-FRESHNESS-WATCHDOG:
on 2026-09-07 the Coolbet sweep failed for ~a day. The site, the session and
the sportsbook endpoints were all fine — the local FlareSolverr container
(oi_local_flaresolverr, :8191) was simply not running. The Coolbet reader
proxies every request through FS (a real browser) to pass Imperva; with FS down
it falls back to plain `requests`, which Imperva answers with a soft HTTP 404
(not 403) to the non-browser fingerprint. The sweep aborted cleanly every cycle
("Coolbet unreachable") and NOTHING paged.

The odds-freshness watchdog *would* eventually fire (odds go stale), but its
signal is "Coolbet odds are stale" — which could be Imperva, a dead session, no
fixtures, or FS. This watchdog answers the one question with a one-line remedy:
is FlareSolverr reachable at all? If not, the fix is always the same and this
says so: `cd local/flaresolverr && docker compose up -d`.

It probes the SAME URLs coolbet_session._fs_call resolves, in the same order:
COOLBET_FS_LOCAL_URL first, then FLARESOLVERR_URL, then the localhost default.
"Healthy" means at least one of them returns 200 with the FlareSolverr ready
banner. Never raises — the alerter must not break the alerter.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_FS_URL_DEFAULT = "http://localhost:8191"
# How long FS must be unreachable before we alert. FS restarts are quick and a
# single missed probe during a container restart is not worth a page; two
# consecutive misses on the 30-min cadence is ~1h, still far inside a sweep gap.
STALE_MINUTES = float(os.getenv("FS_HEALTH_STALE_MINUTES", "45"))
ALERT_DEDUP_HOURS = int(os.getenv("FS_HEALTH_ALERT_DEDUP_HOURS", "6"))
_PIPELINE = "flaresolverr-health"


def _candidate_urls() -> list[str]:
    """The FS URLs coolbet_session tries, in resolution order, de-duplicated."""
    urls: list[str] = []
    for u in (os.getenv("COOLBET_FS_LOCAL_URL"),
              os.getenv("FLARESOLVERR_URL"),
              _FS_URL_DEFAULT):
        if u:
            u = u.rstrip("/")
            if u not in urls:
                urls.append(u)
    return urls


def _probe(url: str, timeout: float = 5.0) -> bool:
    """True iff url returns 200 and looks like a FlareSolverr instance."""
    try:
        with urllib.request.urlopen(url + "/", timeout=timeout) as r:
            if r.status != 200:
                return False
            body = r.read(300).decode("utf-8", "replace")
            return "FlareSolverr" in body
    except Exception:
        return False


def _read_dedup_row(cur) -> dict | None:
    cur.execute(
        "SELECT last_alert_at, last_alert_reason FROM pipeline_health_state "
        "WHERE pipeline_name = %s",
        (_PIPELINE,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"last_alert_at": row[0], "last_alert_reason": row[1]}


def _set_dedup_row(cur, *, ts: datetime | None, reason: str | None) -> None:
    cur.execute(
        """INSERT INTO pipeline_health_state
               (pipeline_name, last_alert_at, last_alert_reason, updated_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (pipeline_name) DO UPDATE
              SET last_alert_at     = EXCLUDED.last_alert_at,
                  last_alert_reason = EXCLUDED.last_alert_reason,
                  updated_at        = now()""",
        (_PIPELINE, ts, reason),
    )


def _format_alert(checked: list[str]) -> str:
    lines = [
        "🚨 <b>FlareSolverr is DOWN</b>",
        "",
        "Every Coolbet request proxies through FlareSolverr to pass Imperva. "
        "With it down the sweep falls back to plain requests and Imperva 404s "
        "them — Coolbet (and any FS-routed) odds stop landing, silently.",
        "",
        "<b>Checked (all unreachable):</b>",
    ]
    for u in checked:
        lines.append(f"  • {u}")
    lines += [
        "",
        "<b>Fix:</b>",
        "  <code>cd local/flaresolverr &amp;&amp; docker compose up -d</code>",
        "  verify: <code>curl http://localhost:8191/</code>",
    ]
    return "\n".join(lines)


def _format_recovery(url: str) -> str:
    return (
        "✅ <b>FlareSolverr recovered</b>\n\n"
        f"Reachable again at {url}. Coolbet collection can proxy through it."
    )


def run_flaresolverr_health_check(*, dry_run: bool = False) -> dict:
    """Main entry point. Never raises."""
    counters: dict = {
        "status": "unknown",
        "reachable_url": None,
        "checked": [],
        "alert_sent": False,
        "recovery_sent": False,
        "dedup_skipped": False,
    }
    try:
        from workers.api_clients.db import get_conn
        from workers.notify.telegram import send_telegram

        now = datetime.now(timezone.utc)
        checked = _candidate_urls()
        counters["checked"] = checked

        reachable = next((u for u in checked if _probe(u)), None)
        counters["reachable_url"] = reachable
        status = "healthy" if reachable else "down"
        counters["status"] = status

        with get_conn() as conn:
            with conn.cursor() as cur:
                dedup = _read_dedup_row(cur)
                last_alert = (dedup or {}).get("last_alert_at")

                if status == "down":
                    if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                        counters["dedup_skipped"] = True
                        return counters
                    if dry_run:
                        counters["alert_sent"] = True
                        return counters
                    tg_id = send_telegram(
                        _format_alert(checked),
                        dedup_key="flaresolverr-health",
                        dedup_window_s=ALERT_DEDUP_HOURS * 3600,
                    )
                    if tg_id is not None:
                        counters["alert_sent"] = True
                        _set_dedup_row(cur, ts=now, reason="flaresolverr unreachable")
                        conn.commit()

                elif status == "healthy" and last_alert is not None:
                    if dry_run:
                        counters["recovery_sent"] = True
                        return counters
                    send_telegram(
                        _format_recovery(reachable),
                        dedup_key="flaresolverr-health-recovery",
                        dedup_window_s=300,
                    )
                    counters["recovery_sent"] = True
                    _set_dedup_row(cur, ts=None, reason=None)
                    conn.commit()

    except Exception as e:
        log.warning("flaresolverr health check raised (non-fatal): %s", e)

    return counters


def main() -> int:
    import json
    dry = "--dry-run" in os.sys.argv
    print(json.dumps(run_flaresolverr_health_check(dry_run=dry), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
