"""TEAM-SCORING-RATES-OWN-RESULTS — rolling team goal rates from our own results.

Team scoring rate is the most obviously relevant feature for a total-goals
model, and it was the thinnest one we had: `match_feature_vectors.goals_for_avg_*`
is sourced from `match_signals` and populated on only 27.8% of rows.

The fix queued for that gap was UNDERSTAT-SCRAPER-BIG5-XG. Re-measured on
2026-09-03 against the leagues we actually bet, Understat covers **1.62%** of
the O/U universe — we bet Friendlies, J1 League, MLS Next Pro, Argentina
Primera C, Brazil Serie D and Iceland 1. Deild, and Understat has none of them.

We already hold 163,901 settled matches back to 2017-03-22. Computing the rate
ourselves reaches **73.2%** of the same universe (both teams with >=5 prior
matches in the trailing year). 45x the coverage, no scraper, no team-name fuzzy
matching, no IP-block risk, no new cron.

SEMANTICS
---------
`goals_for_avg_home` is the average goals **scored by the home team** across
its own last matches — home *and* away fixtures both count. It is a property of
the team, not of the venue. `goals_against_avg_home` is what that same team
conceded. The `_away` pair is the same for the away team.

LEAKAGE
-------
This is the entire risk. A rate that includes the fixture it describes — or any
fixture kicking off after it — backtests beautifully and loses money live.
Two guards, both asserted by the smoke test:

  * `p.date < m.date` is STRICT, compared on the full kickoff timestamp rather
    than the date. Same-day earlier fixtures are legitimate prior information;
    the match itself and anything later can never be included.
  * `p.id <> m.id` belt-and-braces, so a self-join cannot survive a future edit
    that loosens the timestamp comparison.

MIN_MATCHES exists for the same reason honesty matters elsewhere here: a rate
from two fixtures is noise wearing a number's clothes. Below the floor we write
NULL and let the model's missing-indicator handle it, rather than emitting a
figure the model will treat as real.
"""
from __future__ import annotations

# A rate from fewer than this many matches is noise. The model has
# `<col>_missing` indicators (Stage-2a, xgboost_ensemble) so NULL is handled.
MIN_MATCHES = 5

# Trailing window. Long enough to survive a mid-season international break,
# short enough that a promoted or rebuilt squad is not described by last year.
WINDOW_DAYS = 365

# One statement, computed server-side. The per-team history is unnested once
# into (team, kickoff, scored, conceded) rather than correlated per column, so
# each fixture costs two index range scans instead of eight.
FILL_SQL = """
WITH tgt AS (
    SELECT f.match_id, m.date AS kickoff, m.id AS mid,
           m.home_team_id AS h, m.away_team_id AS a
      FROM match_feature_vectors f
      JOIN matches m ON m.id = f.match_id
     WHERE m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
       AND f.match_date >= %(since)s AND f.match_date < %(until)s
       {only_null}
),
rate AS (
    SELECT t.match_id,
           side.which,
           AVG(hist.gf)::numeric AS gf,
           AVG(hist.ga)::numeric AS ga,
           COUNT(*)              AS n
      FROM tgt t
      CROSS JOIN LATERAL (VALUES ('h', t.h), ('a', t.a)) AS side(which, tid)
      JOIN LATERAL (
            SELECT p.score_home AS gf, p.score_away AS ga
              FROM matches p
             WHERE p.home_team_id = side.tid
               AND p.score_home IS NOT NULL
               -- strict, on the kickoff timestamp, and never the fixture itself
               AND p.date < t.kickoff
               AND p.date >= t.kickoff - (%(window)s || ' days')::interval
               AND p.id <> t.mid
            UNION ALL
            SELECT p.score_away AS gf, p.score_home AS ga
              FROM matches p
             WHERE p.away_team_id = side.tid
               AND p.score_home IS NOT NULL
               AND p.date < t.kickoff
               AND p.date >= t.kickoff - (%(window)s || ' days')::interval
               AND p.id <> t.mid
      ) hist ON TRUE
     GROUP BY t.match_id, side.which
    HAVING COUNT(*) >= %(min_matches)s
),
wide AS (
    SELECT match_id,
           MAX(gf) FILTER (WHERE which = 'h') AS gf_h,
           MAX(ga) FILTER (WHERE which = 'h') AS ga_h,
           MAX(gf) FILTER (WHERE which = 'a') AS gf_a,
           MAX(ga) FILTER (WHERE which = 'a') AS ga_a
      FROM rate GROUP BY match_id
)
UPDATE match_feature_vectors f
   SET goals_for_avg_home     = COALESCE(f.goals_for_avg_home,     w.gf_h),
       goals_against_avg_home = COALESCE(f.goals_against_avg_home, w.ga_h),
       goals_for_avg_away     = COALESCE(f.goals_for_avg_away,     w.gf_a),
       goals_against_avg_away = COALESCE(f.goals_against_avg_away, w.ga_a)
  FROM wide w
 WHERE f.match_id = w.match_id
   AND (w.gf_h IS NOT NULL OR w.gf_a IS NOT NULL)
"""

# COALESCE above means an existing signal-sourced value always wins; this only
# fills gaps. Recomputing a value the signals pipeline already wrote would make
# the column's provenance depend on job ordering.

ONLY_NULL = """AND (f.goals_for_avg_home IS NULL OR f.goals_for_avg_away IS NULL
                    OR f.goals_against_avg_home IS NULL
                    OR f.goals_against_avg_away IS NULL)"""


def build_sql(*, only_null: bool = True) -> str:
    return FILL_SQL.format(only_null=ONLY_NULL if only_null else "")


def fill_window(cur, since: str, until: str, *, only_null: bool = True) -> int:
    """Fill scoring rates for fixtures in [since, until). Returns rows updated."""
    cur.execute(build_sql(only_null=only_null),
                {"since": since, "until": until, "window": str(WINDOW_DAYS),
                 "min_matches": MIN_MATCHES})
    return cur.rowcount
