"""/admin/feeds preview snapshot (#139). Same shapes src/lib/admin-feeds.ts reads in production:
feed_status, feed_book_stats, data_quality_findings (7 d), today's ops_snapshots row (odds
pipeline / live tracker / API-Football budget moved here from /admin/ops, IA §2.1) and the
newest live_match_snapshots time."""


def snapshot(rows) -> dict:
    snap = rows(
        "SELECT * FROM ops_snapshots WHERE snapshot_date = (now() AT TIME ZONE 'UTC')::date "
        "ORDER BY created_at DESC LIMIT 1")
    live = rows("SELECT max(captured_at) AS at FROM live_match_snapshots "
                "WHERE captured_at > now() - interval '3 days'")
    return {
        "feeds": rows("SELECT * FROM feed_status"),
        "books": rows("SELECT * FROM feed_book_stats"),
        "dq": rows("SELECT * FROM data_quality_findings WHERE found_at > now() - interval '7 days' "
                   "ORDER BY found_at DESC LIMIT 100"),
        "snapshot": snap[0] if snap else None,
        "last_live_at": live[0]["at"] if live else None,
    }
