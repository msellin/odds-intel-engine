"""FEATURE-DENSIFY-ROUND-2 — fill sparse MFV features from data we already hold.

TEAM-SCORING-RATES-OWN-RESULTS established the pattern: a feature the model
wants, sitting sparse, whose inputs are already in our database. Feature
importance on the promoted O/U head (`v20260903_cut0820`) shows three more in
that state:

    feature                 OU importance   coverage   reachable
    rest_days_home/_away        1.35%         31.2%      84.5%
    season_progress             1.72%         11.9%     100.0%
    league_draw_rate_ytd        1.83%         17.0%      high

Importance is *suppressed* by sparsity — the model can only lean on what it
has. `goals_for_avg_*` went from 5.99% at 46% coverage to 9.28% at 84%, and
that move produced the O/U gain (t=+7.17) that got v20260903_cut0820 promoted.

Deliberately NOT chasing xG here. `xg_overperf_away` carries 1.43% importance
at 2.4% coverage and needs an external source; the obvious one (Understat)
covers 1.62% of the leagues we actually bet. It is the worst payoff available,
not the best — see the blocked UNDERSTAT-SCRAPER-BIG5-XG ticket.

LEAKAGE
-------
Two of the three look backward and must never see the fixture they describe:

  * `rest_days_*` — the team's previous PLAYED match, strictly before kickoff.
  * `league_draw_rate_ytd` — results in the league-season strictly before
    kickoff. Requires MIN_LEAGUE_MATCHES prior results, otherwise NULL; a draw
    rate from three games is noise wearing a number's clothes.

`season_progress` is the exception and is safe by construction: it is a
fixture's position in its league-season *schedule*, not an outcome. The full
schedule is published in advance, so using its span is not hindsight. It is
computed from match dates only and never touches a score.

Every fill uses COALESCE so an existing value wins — this closes gaps, it does
not restate what another writer already produced.
"""
from __future__ import annotations

# A draw rate needs enough of a season behind it to mean anything.
MIN_LEAGUE_MATCHES = 20

# Longest gap we will still call "rest". Beyond this the team was not resting,
# it was between seasons or the fixture list has a hole, and a 190-day "rest"
# is a different thing than a 4-day one. Leave NULL and let the model's
# missing-indicator handle it.
MAX_REST_DAYS = 60

REST_DAYS_SQL = """
WITH tgt AS (
    SELECT f.match_id, m.date AS kickoff, m.id AS mid,
           m.home_team_id AS h, m.away_team_id AS a
      FROM match_feature_vectors f
      JOIN matches m ON m.id = f.match_id
     WHERE m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
       AND f.match_date >= %(since)s AND f.match_date < %(until)s
       AND (f.rest_days_home IS NULL OR f.rest_days_away IS NULL)
),
prev AS (
    SELECT t.match_id, side.which,
           MAX(p.date) AS last_played
      FROM tgt t
      CROSS JOIN LATERAL (VALUES ('h', t.h), ('a', t.a)) AS side(which, tid)
      JOIN matches p
        ON (p.home_team_id = side.tid OR p.away_team_id = side.tid)
       AND p.score_home IS NOT NULL
       -- strict, on the kickoff timestamp, never the fixture itself
       AND p.date < t.kickoff
       AND p.date >= t.kickoff - (%(max_rest)s || ' days')::interval
       AND p.id <> t.mid
     GROUP BY t.match_id, side.which
),
wide AS (
    SELECT t.match_id,
           EXTRACT(epoch FROM (t.kickoff - MAX(p.last_played)
                   FILTER (WHERE p.which = 'h'))) / 86400.0 AS rest_h,
           EXTRACT(epoch FROM (t.kickoff - MAX(p.last_played)
                   FILTER (WHERE p.which = 'a'))) / 86400.0 AS rest_a
      FROM tgt t JOIN prev p ON p.match_id = t.match_id
     GROUP BY t.match_id, t.kickoff
)
UPDATE match_feature_vectors f
   SET rest_days_home = COALESCE(f.rest_days_home, ROUND(w.rest_h)::int),
       rest_days_away = COALESCE(f.rest_days_away, ROUND(w.rest_a)::int)
  FROM wide w
 WHERE f.match_id = w.match_id
   AND (w.rest_h IS NOT NULL OR w.rest_a IS NOT NULL)
"""

# season_progress: where this fixture sits in its league-season schedule.
# Uses the season's full date span, which is published in advance — schedule,
# not outcome. Guarded against a zero-length span (single-fixture seasons).
SEASON_PROGRESS_SQL = """
WITH span AS (
    SELECT league_id, season,
           MIN(date) AS lo, MAX(date) AS hi, COUNT(*) AS n
      FROM matches
     WHERE league_id IS NOT NULL AND season IS NOT NULL
     GROUP BY league_id, season
    HAVING COUNT(*) >= 10 AND MAX(date) > MIN(date)
),
prog AS (
    SELECT f.match_id,
           GREATEST(0.0, LEAST(1.0,
             EXTRACT(epoch FROM (m.date - s.lo))
             / NULLIF(EXTRACT(epoch FROM (s.hi - s.lo)), 0)
           ))::numeric AS p
      FROM match_feature_vectors f
      JOIN matches m ON m.id = f.match_id
      JOIN span s ON s.league_id = m.league_id AND s.season = m.season
     WHERE f.season_progress IS NULL
       AND f.match_date >= %(since)s AND f.match_date < %(until)s
)
UPDATE match_feature_vectors f
   SET season_progress = COALESCE(f.season_progress, prog.p)
  FROM prog
 WHERE f.match_id = prog.match_id
"""

# league_draw_rate_ytd: draw rate in this league-season BEFORE this fixture.
LEAGUE_DRAW_RATE_SQL = """
WITH tgt AS (
    SELECT f.match_id, m.date AS kickoff, m.id AS mid,
           m.league_id, m.season
      FROM match_feature_vectors f
      JOIN matches m ON m.id = f.match_id
     WHERE f.league_draw_rate_ytd IS NULL
       AND m.league_id IS NOT NULL AND m.season IS NOT NULL
       AND f.match_date >= %(since)s AND f.match_date < %(until)s
),
rate AS (
    SELECT t.match_id,
           AVG(CASE WHEN p.score_home = p.score_away THEN 1.0 ELSE 0.0 END)::numeric AS dr,
           COUNT(*) AS n
      FROM tgt t
      JOIN matches p
        ON p.league_id = t.league_id AND p.season = t.season
       AND p.score_home IS NOT NULL
       AND p.date < t.kickoff          -- strictly prior
       AND p.id <> t.mid
     GROUP BY t.match_id
    HAVING COUNT(*) >= %(min_matches)s
)
UPDATE match_feature_vectors f
   SET league_draw_rate_ytd = COALESCE(f.league_draw_rate_ytd, r.dr)
  FROM rate r
 WHERE f.match_id = r.match_id
"""

FILLS = (
    ("rest_days", REST_DAYS_SQL),
    ("season_progress", SEASON_PROGRESS_SQL),
    ("league_draw_rate_ytd", LEAGUE_DRAW_RATE_SQL),
)


def fill_window(cur, name: str, sql: str, since: str, until: str) -> int:
    cur.execute(sql, {"since": since, "until": until,
                      "min_matches": MIN_LEAGUE_MATCHES,
                      "max_rest": str(MAX_REST_DAYS)})
    return cur.rowcount
