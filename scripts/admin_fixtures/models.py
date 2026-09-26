"""/admin/models preview snapshot ([[#153]]). Same shapes src/lib/admin-models.ts reads in production:
model_accuracy (computed here with the job's own code when the table is not there yet), bot_config,
bots, bot_performance, bot_rule_history (else the same GROUP BY on bot_ledger) and the older
hand-written bot_config_history log."""


def snapshot(rows) -> dict:
    try:
        acc = rows("SELECT * FROM model_accuracy")
    except Exception:  # noqa: BLE001 — table not applied yet: compute with the job's code
        from workers.jobs.model_accuracy import compute
        acc = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in compute()]
    try:
        hist = rows("SELECT * FROM bot_rule_history")
    except Exception:  # noqa: BLE001
        hist = rows("SELECT bot_name, rule_version, model_version, min(pick_time) AS first_pick, "
                    "max(pick_time) AS last_pick, count(*) AS picks FROM bot_ledger GROUP BY 1, 2, 3")
    return {
        "accuracy": acc,
        "config": rows("SELECT bot_name, family, markets, prob_source, edge_floor, edge_floor_source, odds_min, "
                       "odds_max, gates, books, anchor, placeable, published, telegram, exported_at FROM bot_config"),
        "bots": rows("SELECT name, display_name, maturity_label, vip, show_on_performance, hide_pending, "
                     "retired_at, rule_version FROM bots"),
        "performance": rows("SELECT bot_name, picks_total, settled, clv_n, clv_public, roi_public, first_pick_at, "
                            "last_pick_at FROM bot_performance"),
        "rule_history": hist,
        "change_log": rows("SELECT bot_name, effective_from, superseded_at, change_ref, rationale FROM "
                           "bot_config_history ORDER BY effective_from DESC"),
    }
