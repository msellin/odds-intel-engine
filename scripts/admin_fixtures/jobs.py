"""/admin/ops (Jobs) preview snapshot (#139). Same shapes src/lib/admin-jobs.ts reads in
production: view pipeline_job_latest (migration 417), failed pipeline_runs of the last 14 days,
pending simulated_bets with their kickoff, today's ops_snapshots row, signups in 7 days."""


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
        "signups_7d": rows("SELECT count(*) AS n FROM profiles WHERE created_at > now() - interval '7 days'")[0]["n"],
    }
