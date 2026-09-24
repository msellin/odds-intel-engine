"""/admin/activity preview snapshot (#139, IA gap G4). Same shapes src/lib/admin-activity.ts reads
in production: control_changes and feed_actions (newest 500 each), plus feed labels and bot
display names so the sentences read in words."""


def snapshot(rows) -> dict:
    return {
        "changes": rows(
            "SELECT id, created_at, actor, source, control, bot_name, old_value, new_value, reason, "
            "outcome, refusal FROM control_changes ORDER BY id DESC LIMIT 500"),
        "feed_actions": rows("SELECT * FROM feed_actions ORDER BY id DESC LIMIT 500"),
        "feeds": rows("SELECT feed_id, label FROM feed_status"),
        "bots": rows("SELECT name, display_name FROM bots"),
    }
