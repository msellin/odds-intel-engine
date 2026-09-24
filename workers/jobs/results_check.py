"""RESULTS CHECK ([[#121]] phase 1, 2026-09-24) — does API-Football's final score agree with
a second source (Tonybet's regular-time result) on every match both cover?

WHY. Settlement, CLV and the public track record all grade on `matches.score_home/away`
(API-Football). One wrong score silently mis-settles every bet on that match. The
data-quality audit found, on 89 overlapping matches in 7 days, two REAL disagreements:
JOS Watergraafsmeer v TEC (AF 4-4, Tonybet 2-2, both 1-2 at half-time) and Hapoel Ramat
HaSharon v Hapoel Sderot (AF 1-0, Tonybet 3-1, both 0-0 at half-time). Neither source is
assumed right — a disagreement is a question for a human.

WHAT. Every 2 h (after the Tonybet results job): matches both sources call FINISHED IN
NORMAL TIME (Tonybet matchStatusId 100 — extra-time/penalty endings 110/120 are skipped
because the two sources may legitimately differ on what "full time" means there) and
whose scores differ → one `results_disagree` finding per match, listing the bets already
settled on it (simulated_bets / shadow_bets / picks_forward_test) so they can be re-settled
once resolved, and a Telegram alert. Not-started rows (status 0) are never compared.

NOT DONE HERE (owner decision, see #121): HOLDING settlement until the sources agree.
Tonybet's results arrive every 2 h while settlement runs every 15 min, so a hold delays
every settlement; it needs a decision on how long to wait and what to do when the second
source never reports.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_ENDED_NORMAL_TIME = 100


def run(*, days: int = 3, dry_run: bool = False) -> dict:
    from workers.api_clients.db import execute_query
    from workers.utils.board_guard import record_finding
    rows = execute_query(
        """SELECT m.id::text AS mid, ht.name AS home, at2.name AS away, m.date,
                  m.score_home, m.score_away, m.ht_score_home, m.ht_score_away,
                  r.ft_home, r.ft_away, r.ht_home, r.ht_away
             FROM book_match_results r
             JOIN matches m ON m.id = r.match_id
             JOIN teams ht ON ht.id = m.home_team_id
             JOIN teams at2 ON at2.id = m.away_team_id
            WHERE m.status = 'finished' AND m.date > now() - make_interval(days => %s)
              AND r.match_status_id = %s AND r.ft_home IS NOT NULL AND m.score_home IS NOT NULL
              -- review #2: only when Tonybet's own kickoff agrees with ours (<= 15 min).
              -- Hapoel Ramat HaSharon "disagreed" because Tonybet's event started at 17:45
              -- against 17:15 at every other book — a mis-paired event, not a wrong score.
              AND abs(extract(epoch FROM (r.kickoff - m.date))) <= 900""",
        (days, _ENDED_NORMAL_TIME)) or []
    c = {"compared": len(rows), "disagree": 0, "new": 0}
    for r in rows:
        if (r["score_home"], r["score_away"]) == (r["ft_home"], r["ft_away"]):
            continue
        c["disagree"] += 1
        try:
            seen = execute_query(
                "SELECT 1 FROM data_quality_findings WHERE check_name = 'results_disagree' AND match_id = %s",
                (r["mid"],))
        except Exception:  # noqa: BLE001 — table not migrated yet: treat as unseen
            seen = []
        if seen:
            continue
        affected = execute_query(
            """SELECT 'simulated_bets' AS ledger, count(*) n FROM simulated_bets WHERE match_id = %s AND result <> 'pending'
               UNION ALL SELECT 'shadow_bets', count(*) FROM shadow_bets WHERE match_id = %s AND result <> 'pending'
               UNION ALL SELECT 'picks_forward_test', count(*) FROM picks_forward_test
                          WHERE match_id = %s AND outcome IN ('won', 'lost')""",
            (r["mid"], r["mid"], r["mid"])) or []
        detail = {"match": f"{r['home']} v {r['away']}", "kickoff": r["date"],
                  "api_football": f"{r['score_home']}-{r['score_away']} (HT {r['ht_score_home']}-{r['ht_score_away']})",
                  "tonybet": f"{r['ft_home']}-{r['ft_away']} (HT {r['ht_home']}-{r['ht_away']})",
                  "settled_bets": {a["ledger"]: a["n"] for a in affected}}
        c["new"] += 1
        log.warning("results-check: %s", detail)
        if dry_run:
            continue
        record_finding("results_disagree", r["mid"], "Tonybet", detail)
        try:
            from workers.notify.telegram import send_telegram
            bets = sum(detail["settled_bets"].values())
            send_telegram(f"⚠️ <b>Result disagreement</b> — {detail['match']}: API-Football "
                          f"{detail['api_football']}, Tonybet {detail['tonybet']}. {bets} settled bet(s) "
                          f"on this match graded on the API-Football score.",
                          dedup_key=f"results-disagree-{r['mid']}", dedup_window_s=86400 * 7)
        except Exception as e:  # noqa: BLE001
            log.debug("results-check alert failed: %s", e)
    log.info("results-check: %s", c)
    return c


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    print(run(days=a.days, dry_run=a.dry_run))
