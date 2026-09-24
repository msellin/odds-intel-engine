"""Fixture for /admin/shadow-bots — the Pick queue (#139 IA move P6, 2026-09-24).

Same shapes as odds-intel-web src/lib/shadow-bots/queries.ts: `page` = ShadowBotsPageData
(loadShadowBotsPage) and `state` = SessionState (loadSessionState). Read-only SQL.
"""

SNAPSHOT_BOOKS = ["Coolbet", "Unibet-Site", "Epicbet", "Tonybet"]


def _num(v):
    return None if v is None else float(v)


def _int(v):
    return None if v is None else int(v)


def snapshot(rows) -> dict:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    bots = rows("SELECT id::text, name, display_name, maturity_label, is_active FROM bots WHERE retired_at IS NULL ORDER BY name")
    placer = rows("SELECT bot_name, ui_place_enabled, note FROM coolbet_placer_bots ORDER BY bot_name")
    real = rows(
        """SELECT bookmaker, stake, placed_real, shadow_bet_id::text AS shadow_bet_id FROM real_bets
            WHERE placed_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' LIMIT 500"""
    )
    today = {"confirmedCount": 0, "confirmedStake": 0.0, "unconfirmedCount": 0, "unconfirmedStake": 0.0}
    logged = set()
    for r in real:
        if r["shadow_bet_id"]:
            logged.add(r["shadow_bet_id"])
        s = float(r["stake"] or 0)
        if r["placed_real"] is True:
            today["confirmedCount"] += 1
            today["confirmedStake"] += s
        elif r["placed_real"] is None:
            today["unconfirmedCount"] += 1
            today["unconfirmedStake"] += s

    pick_sql = """SELECT su.id::text, su.bot_id::text, su.bot_name, su.match_id::text, su.market, su.selection,
                         su.odds_at_pick, su.calibrated_prob, su.model_probability, su.recommended_bookmaker,
                         su.pick_time, su.decision_quote_age_min, su.inplay_minute, su.inplay_score_home,
                         su.inplay_score_away, m.date AS kickoff, ht.name AS home, at.name AS away,
                         l.name AS league, l.country, l.tier
                    FROM shadow_bets_unique su
                    JOIN matches m ON m.id = su.match_id
                    LEFT JOIN leagues l ON l.id = m.league_id
                    LEFT JOIN teams ht ON ht.id = m.home_team_id
                    LEFT JOIN teams at ON at.id = m.away_team_id
                   WHERE su.bot_retired_at IS NULL AND su.result = 'pending' AND {cond}
                   ORDER BY {order} LIMIT {lim}"""
    pre = rows(pick_sql.format(cond="m.date >= now()", order="m.date", lim=1500))
    inp = rows(pick_sql.format(cond="su.inplay_minute IS NOT NULL", order="su.pick_time DESC", lim=300))
    seen = {p["id"] for p in pre}
    picks = []
    for r in pre + [p for p in inp if p["id"] not in seen]:
        picks.append({
            "id": r["id"], "bot_id": r["bot_id"], "bot_name": r["bot_name"] or "?", "match_id": r["match_id"],
            "market": r["market"], "selection": r["selection"], "odds_at_pick": _num(r["odds_at_pick"]),
            "calibrated_prob": _num(r["calibrated_prob"]), "model_probability": _num(r["model_probability"]),
            "recommended_bookmaker": r["recommended_bookmaker"], "pick_time": r["pick_time"],
            "decision_quote_age_min": _num(r["decision_quote_age_min"]), "inplay_minute": _int(r["inplay_minute"]),
            "inplay_score_home": _int(r["inplay_score_home"]), "inplay_score_away": _int(r["inplay_score_away"]),
            "kickoff": r["kickoff"], "home": r["home"] or "Home", "away": r["away"] or "Away",
            "league": r["league"], "country": r["country"], "tier": r["tier"],
        })

    quotes: dict = {}
    match_ids = sorted({p["match_id"] for p in pre})
    markets = sorted({p["market"].lower() for p in pre})
    if match_ids and markets:
        # best price inside a 15 s burst of the newest row per (book, key) — the loader's rule
        q = rows(
            """WITH s AS (
                 SELECT bookmaker, match_id::text AS match_id, lower(market) AS market, lower(selection) AS selection,
                        odds, timestamp,
                        max(timestamp) OVER (PARTITION BY bookmaker, match_id, lower(market), lower(selection)) AS newest
                   FROM odds_snapshots
                  WHERE match_id::text = ANY(%s) AND lower(market) = ANY(%s) AND bookmaker = ANY(%s)
                    AND is_live = false AND timestamp >= now() - interval '12 hours')
               SELECT DISTINCT ON (bookmaker, match_id, market, selection)
                      bookmaker, match_id, market, selection, odds, timestamp
                 FROM s WHERE newest - timestamp <= interval '15 seconds'
                ORDER BY bookmaker, match_id, market, selection, odds DESC""",
            (match_ids, markets, SNAPSHOT_BOOKS),
        )
        for r in q:
            k = f"{r['match_id']}|{r['market']}|{r['selection']}"
            quotes.setdefault(k, []).append({"book": r["bookmaker"], "odds": float(r["odds"]), "ts": r["timestamp"]})

    scoreboard = rows(
        """SELECT bot_id::text, bot_name, settled_n, settled_won, settled_pnl_eur, settled_roi, clv_n,
                  clv_mc_mean, clv_mc_sd, decision_fresh_n, decision_age_known_n
             FROM shadow_bot_scoreboard WHERE bot_id IN (SELECT id FROM bots WHERE retired_at IS NULL)"""
    )
    st = rows(
        """SELECT placement_paused, placement_paused_reason, publishing_paused, publishing_paused_reason,
                  daemons_paused, daemons_paused_reason, real_money_armed, real_money_armed_reason,
                  mac_daemon_last_tick_at FROM coolbet_session_state WHERE id = 1"""
    )
    return {
        "page": {
            "bots": bots,
            "placerBots": placer,
            "todayRealBets": today,
            "upcoming": picks,
            "quotes": quotes,
            "truncatedBooks": [],
            "scoreboard": scoreboard,
            "loggedPickIds": sorted(logged),
            "loadedAt": now.isoformat(),
            "queryCount": 0,
        },
        "state": st[0] if st else None,
    }
