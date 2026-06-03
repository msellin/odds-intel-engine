"""Q1-A validation: pull top N finished matches by Pinnacle line-move magnitude
over the last 30 days. Outputs a CSV with pre-built browser/search URLs so we
can manually (or via WebSearch) audit whether public X/Twitter content
preceded each line move.

Decision rule downstream:
  - X-precedence ≥40% from credible sources → buy the X API
  - 20–40% → marginal, try official RSS only
  - <20% → kill Q1-A

Usage:
    DATABASE_URL=postgresql://... python3 scripts/q1a_validation_audit.py
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

# Allow running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query


SQL = """
SELECT
    m.id::text                                AS match_id,
    th.name                                   AS home_team,
    ta.name                                   AS away_team,
    l.name                                    AS league,
    l.country                                 AS country,
    m.date                                    AS kickoff,
    mfv.opening_implied_home                  AS opening_home,
    mfv.opening_implied_draw                  AS opening_draw,
    mfv.opening_implied_away                  AS opening_away,
    mfv.pinnacle_line_move_home_at_t6h        AS move_home,
    mfv.pinnacle_line_move_draw_at_t6h        AS move_draw,
    mfv.pinnacle_line_move_away_at_t6h        AS move_away,
    GREATEST(
        COALESCE(ABS(mfv.pinnacle_line_move_home_at_t6h), 0),
        COALESCE(ABS(mfv.pinnacle_line_move_draw_at_t6h), 0),
        COALESCE(ABS(mfv.pinnacle_line_move_away_at_t6h), 0)
    )                                         AS max_abs_move,
    -- Did our existing news_checker flag anything?
    mfv.news_impact_score                     AS news_impact,
    mfv.lineup_confirmed                      AS lineup_confirmed,
    mfv.injury_severity_score_home            AS inj_home,
    mfv.injury_severity_score_away            AS inj_away,
    m.score_home                              AS final_score_home,
    m.score_away                              AS final_score_away
FROM matches m
JOIN teams       th  ON th.id  = m.home_team_id
JOIN teams       ta  ON ta.id  = m.away_team_id
JOIN leagues     l   ON l.id   = m.league_id
JOIN match_feature_vectors mfv ON mfv.match_id = m.id
WHERE m.status   = 'finished'
  AND m.date    >= NOW() - INTERVAL '30 days'
  AND m.date    <  NOW()
  AND mfv.opening_implied_home IS NOT NULL
  AND (
      ABS(COALESCE(mfv.pinnacle_line_move_home_at_t6h, 0)) >= 0.04
   OR ABS(COALESCE(mfv.pinnacle_line_move_draw_at_t6h, 0)) >= 0.04
   OR ABS(COALESCE(mfv.pinnacle_line_move_away_at_t6h, 0)) >= 0.04
  )
ORDER BY max_abs_move DESC
LIMIT 30;
"""


def main() -> int:
    rows = execute_query(SQL)
    if not rows:
        print("No matches found. Either the line-move feature is sparse or "
              "the time window has no data.")
        return 1

    out_path = Path(__file__).resolve().parent.parent / "dev" / "active" / "q1a_validation_audit.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "rank", "match_id", "kickoff_utc", "league", "country",
        "home_team", "away_team", "final_score",
        "opening_home", "opening_draw", "opening_away",
        "move_home", "move_draw", "move_away", "max_abs_move",
        "our_news_impact", "our_lineup_confirmed", "our_inj_home", "our_inj_away",
        "x_search_url", "google_news_url", "credible_source_found",
        "minutes_lead", "notes",
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, r in enumerate(rows, start=1):
            kickoff = r["kickoff"]
            date_str = kickoff.strftime("%Y-%m-%d") if kickoff else ""
            until_str = (kickoff.strftime("%Y-%m-%d") if kickoff else "")

            # Twitter/X search URL — looks for "<home> <away>" tweets in the 24h before KO.
            x_query = f'"{r["home_team"]}" "{r["away_team"]}" (lineup OR injury OR out OR doubt OR XI) until:{until_str}'
            x_url = f"https://x.com/search?q={quote_plus(x_query)}&f=live"

            # Google news fallback — catches reporters who write articles + tweets.
            g_query = f'"{r["home_team"]}" "{r["away_team"]}" lineup injury before:{until_str}'
            g_url = f"https://www.google.com/search?q={quote_plus(g_query)}&tbm=nws"

            final_score = (
                f"{r['final_score_home']}-{r['final_score_away']}"
                if r["final_score_home"] is not None and r["final_score_away"] is not None
                else ""
            )

            writer.writerow({
                "rank": i,
                "match_id": r["match_id"],
                "kickoff_utc": kickoff.isoformat() if kickoff else "",
                "league": r["league"],
                "country": r["country"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "final_score": final_score,
                "opening_home": f"{r['opening_home']:.3f}" if r["opening_home"] is not None else "",
                "opening_draw": f"{r['opening_draw']:.3f}" if r["opening_draw"] is not None else "",
                "opening_away": f"{r['opening_away']:.3f}" if r["opening_away"] is not None else "",
                "move_home": f"{r['move_home']:+.3f}" if r["move_home"] is not None else "",
                "move_draw": f"{r['move_draw']:+.3f}" if r["move_draw"] is not None else "",
                "move_away": f"{r['move_away']:+.3f}" if r["move_away"] is not None else "",
                "max_abs_move": f"{r['max_abs_move']:.3f}",
                "our_news_impact": f"{r['news_impact']:.3f}" if r["news_impact"] is not None else "",
                "our_lineup_confirmed": r["lineup_confirmed"] if r["lineup_confirmed"] is not None else "",
                "our_inj_home": f"{r['inj_home']:.3f}" if r["inj_home"] is not None else "",
                "our_inj_away": f"{r['inj_away']:.3f}" if r["inj_away"] is not None else "",
                "x_search_url": x_url,
                "google_news_url": g_url,
                "credible_source_found": "",
                "minutes_lead": "",
                "notes": "",
            })

    print(f"\nWrote {len(rows)} rows to {out_path}\n")
    print("Top 10 by line-move magnitude:")
    print("-" * 100)
    for i, r in enumerate(rows[:10], start=1):
        ki = r["kickoff"].strftime("%m-%d %H:%MZ") if r["kickoff"] else ""
        # Identify which selection moved most.
        moves = [
            ("H", r["move_home"] or 0),
            ("D", r["move_draw"] or 0),
            ("A", r["move_away"] or 0),
        ]
        moves.sort(key=lambda x: abs(x[1]), reverse=True)
        big = moves[0]
        news_flag = "📰" if (r["news_impact"] is not None and abs(r["news_impact"]) > 0.01) else "  "
        lineup_flag = "👥" if r["lineup_confirmed"] else "  "
        print(
            f"{i:>2}. {ki}  {r['home_team'][:22]:>22} vs {r['away_team'][:22]:<22} "
            f"[{r['league'][:20]:<20}] "
            f"{big[0]}{big[1]:+.2%}  news={news_flag} lineup={lineup_flag}"
        )

    print()
    print(f"CSV ready at: {out_path}")
    print()
    print("Next: spawn a research agent to audit each row's x_search_url and ")
    print("google_news_url, scoring credible_source_found + minutes_lead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
