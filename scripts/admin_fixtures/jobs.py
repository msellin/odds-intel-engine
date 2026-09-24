"""/admin/ops (Jobs) preview snapshot (#139). Same shapes src/lib/admin-jobs.ts reads in
production: view pipeline_job_latest (migration 417), failed pipeline_runs of the last 14 days,
pending simulated_bets with their kickoff, today's ops_snapshots row, signups in 7 days; plus
(#139 UX fix round) the feeds with a run-now control and the newest 20 runs of every job for the
detail drawer (/api/admin/job-runs answers from `recent_runs` in the preview)."""


def snapshot(rows) -> dict:
    snap = rows(
        "SELECT * FROM ops_snapshots WHERE snapshot_date = (now() AT TIME ZONE 'UTC')::date "
        "ORDER BY created_at DESC LIMIT 1")
    return {
        "jobs": rows("SELECT * FROM pipeline_job_latest"),
        "failed_runs": rows(
            "SELECT job_name, started_at FROM pipeline_runs WHERE status = 'failed' "
            "AND started_at > now() - interval '14 days' "
            "AND job_name NOT IN ('hist_backfill','backfill_coaches','backfill_transfers') "
            "ORDER BY started_at DESC LIMIT 5000"),
        "pending": rows(
            "SELECT b.id, b.market, b.pick_time, b.bot_id, m.date AS match_kickoff "
            "FROM simulated_bets b LEFT JOIN matches m ON m.id = b.match_id "
            "WHERE b.result = 'pending' ORDER BY b.pick_time LIMIT 5000"),
        "snapshot": snap[0] if snap else None,
        "feeds": rows("SELECT feed_id, label, book, schedule, controls, paused, run_now_pending FROM feed_status"),
        "recent_runs": _recent_runs(rows),
        "signups_7d": rows("SELECT count(*) AS n FROM profiles WHERE created_at > now() - interval '7 days'")[0]["n"],
    }


def _recent_runs(rows) -> dict:
    """{job_name: newest 20 runs}; the 48 shadow_HHMM slots share one key, like the page's row."""
    out: dict = {}
    for r in rows(
            "SELECT * FROM (SELECT CASE WHEN job_name ~ '^shadow_[0-9]{4}$' THEN 'shadow_HHMM' ELSE job_name END AS k, "
            "job_name, status, started_at, completed_at, records_count, left(error_message, 2000) AS error_message, "
            "row_number() OVER (PARTITION BY CASE WHEN job_name ~ '^shadow_[0-9]{4}$' THEN 'shadow_HHMM' ELSE job_name END "
            "ORDER BY started_at DESC) AS rn FROM pipeline_runs WHERE started_at > now() - interval '35 days') t "
            "WHERE rn <= 20 ORDER BY k, started_at DESC"):
        k = r.pop("k")
        r.pop("rn", None)
        out.setdefault(k, []).append(r)
    return out
