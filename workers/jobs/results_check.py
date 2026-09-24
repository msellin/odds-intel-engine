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

TIE-BREAK ([[#121]] 6b, owner chose option C, 2026-09-24). A two-source disagreement has
no majority, so a third opinion is taken on every open disagreement:
  * API-Football RE-FETCHED now (`score.fulltime` — regular time), which catches AF having
    corrected its own feed since we stored the score, and
  * ESPN, where the league is covered (~25 top leagues; most disagreements are lower-tier,
    so ESPN often has nothing to say — that is why the AF re-fetch is a vote too).
Verdicts:
  * `corrected`   — AF-now agrees with Tonybet, OR ESPN agrees with Tonybet against AF.
                    `matches` gets the majority score and every already-settled bet on the
                    match is re-graded in place (see `_regrade`). Recorded as a
                    `results_corrected` finding + Telegram so every rewrite is auditable.
  * `af_confirmed`— ESPN agrees with AF: Tonybet is the odd one out. Nothing changes; the
                    match is not re-checked again.
  * `unresolved`  — no majority. Alert-only, exactly as phase 1; re-tried every run (AF may
                    still correct itself), but alerted once.
The stored AF score is NOT a vote of its own — it is the thing being judged, and the
AF re-fetch already represents that source.

Why a corrected score sticks: settlement (`run_settlement`) and the live poller
(`finish_match_sql`) only write scores for matches NOT yet `finished`, so nothing
re-imports the old AF number over the correction.

NOT DONE HERE (owner decision, see #121): HOLDING settlement until the sources agree.
Tonybet's results arrive every 2 h while settlement runs every 15 min, so a hold delays
every settlement; it needs a decision on how long to wait and what to do when the second
source never reports.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_ENDED_NORMAL_TIME = 100


def _af_now(af_id) -> tuple[int, int] | None:
    """Fresh AF regular-time score for one fixture (1 request)."""
    if not af_id:
        return None
    try:
        from workers.api_clients.api_football import get_fixtures_batch
        fx = get_fixtures_batch([int(af_id)]).get(int(af_id)) or {}
        ft = (fx.get("score") or {}).get("fulltime") or {}
        if ft.get("home") is None or ft.get("away") is None:
            return None
        return int(ft["home"]), int(ft["away"])
    except Exception as e:  # noqa: BLE001
        log.debug("results-check: AF re-fetch %s failed: %s", af_id, e)
        return None


def _espn(home: str, away: str, day: str, cache: dict) -> tuple[int, int] | None:
    """ESPN score by fuzzy team match; None when ESPN does not cover the league."""
    try:
        if day not in cache:
            from workers.scrapers.espn_results import get_finished_matches_espn
            cache[day] = get_finished_matches_espn(day) or []
        from workers.jobs.settlement import find_result_for_match
        hit = find_result_for_match(home, away, cache[day])
        return (int(hit["home_goals"]), int(hit["away_goals"])) if hit else None
    except Exception as e:  # noqa: BLE001
        log.debug("results-check: ESPN %s failed: %s", day, e)
        return None


def verdict(af_now, tonybet, espn) -> tuple[str, tuple[int, int] | None]:
    """Majority of three independent reads. Returns (verdict, score_to_store)."""
    if af_now is not None and af_now == tonybet:
        return "corrected", tonybet
    if espn is not None and espn == tonybet and espn != af_now:
        return "corrected", tonybet
    if espn is not None and af_now is not None and espn == af_now:
        return "af_confirmed", None
    return "unresolved", None


_REGRADE_SQL = """SELECT b.id, b.bot_id, b.match_id::text AS match_id, b.market, b.selection,
                         b.stake, b.odds_at_pick, b.result, b.pnl
                    FROM {table} b
                   WHERE b.match_id = %s AND b.result IN ('won', 'lost', 'void')
                     AND (b.void_reason IS NULL OR LEFT(b.void_reason, 10) <> 'quarantine')"""


def _regrade(mid: str, hg: int, ag: int) -> dict:
    """Re-grade every already-settled bet on one match against the corrected score.

    shadow_bets / simulated_bets: recompute with settlement.settle_bet_result and rewrite
    result + pnl ONLY where the grade changes (closing odds / CLV do not depend on the
    score and are left alone). Quarantined voids are never touched — same predicate as
    resettle_wrongly_voided_bets. 'skip' (market needs stats, e.g. corners) is left as is.
    A simulated_bets change moves its bot's bankroll by exactly the pnl delta.
    picks_forward_test: reopened (outcome NULL) and settled again through
    settle_picks_forward_test, so the pre-registered ledger is graded by its own code."""
    from workers.api_clients.db import execute_query, execute_write, execute_write_returning
    from workers.jobs.settlement import settle_bet_result, settle_picks_forward_test
    out = {"shadow_bets": 0, "simulated_bets": 0, "picks_forward_test": 0, "pnl_delta": 0.0}
    for table in ("shadow_bets", "simulated_bets"):
        for b in execute_query(_REGRADE_SQL.format(table=table), (mid,)) or []:
            new = settle_bet_result(b, hg, ag, None)
            if new["result"] in ("skip", b["result"]):
                continue
            delta = float(new["pnl"]) - float(b["pnl"] or 0)
            execute_write(f"UPDATE {table} SET result = %s, pnl = %s WHERE id = %s",
                          (new["result"], new["pnl"], b["id"]))
            if table == "simulated_bets" and delta:
                execute_write("UPDATE bots SET current_bankroll = current_bankroll + %s WHERE id = %s",
                              (round(delta, 2), b["bot_id"]))
            out[table] += 1
            out["pnl_delta"] = round(out["pnl_delta"] + delta, 2)
    reopened = execute_write_returning(
        """UPDATE picks_forward_test SET outcome = NULL, pnl = NULL, settled_at = NULL
            WHERE match_id = %s AND outcome IN ('won', 'lost', 'push') RETURNING id""", (mid,)) or []
    if reopened:
        settle_picks_forward_test([mid])
        out["picks_forward_test"] = len(reopened)
    return out


def _apply(r: dict, score: tuple[int, int]) -> dict:
    from workers.api_clients.db import execute_write
    hg, ag = score
    res = "home" if hg > ag else "away" if ag > hg else "draw"
    execute_write(
        """UPDATE matches SET score_home = %s, score_away = %s, result = %s,
                  h2_score_home = CASE WHEN ht_score_home IS NULL THEN h2_score_home ELSE %s - ht_score_home END,
                  h2_score_away = CASE WHEN ht_score_away IS NULL THEN h2_score_away ELSE %s - ht_score_away END
            WHERE id = %s""", (hg, ag, res, hg, ag, r["mid"]))
    return _regrade(r["mid"], hg, ag)


def run(*, days: int = 3, dry_run: bool = False) -> dict:
    from workers.api_clients.db import execute_query
    from workers.utils.board_guard import record_finding
    rows = execute_query(
        """SELECT m.id::text AS mid, m.api_football_id AS af_id, ht.name AS home, at2.name AS away, m.date,
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
    c = {"compared": len(rows), "disagree": 0, "new": 0, "corrected": 0, "af_confirmed": 0}
    espn_cache: dict = {}
    for r in rows:
        if (r["score_home"], r["score_away"]) == (r["ft_home"], r["ft_away"]):
            continue
        c["disagree"] += 1
        try:
            seen = {x["verdict"] for x in execute_query(
                """SELECT coalesce(detail->>'verdict', 'unresolved') AS verdict FROM data_quality_findings
                    WHERE check_name = 'results_disagree' AND match_id = %s""", (r["mid"],)) or []}
        except Exception:  # noqa: BLE001 — table not migrated yet: treat as unseen
            seen = set()
        if "af_confirmed" in seen:
            continue
        tonybet = (int(r["ft_home"]), int(r["ft_away"]))
        af_now = _af_now(r["af_id"])
        espn = _espn(r["home"], r["away"], str(r["date"])[:10], espn_cache)
        v, score = verdict(af_now, tonybet, espn)
        affected = execute_query(
            """SELECT 'simulated_bets' AS ledger, count(*) n FROM simulated_bets WHERE match_id = %s AND result <> 'pending'
               UNION ALL SELECT 'shadow_bets', count(*) FROM shadow_bets WHERE match_id = %s AND result <> 'pending'
               UNION ALL SELECT 'picks_forward_test', count(*) FROM picks_forward_test
                          WHERE match_id = %s AND outcome IN ('won', 'lost')""",
            (r["mid"], r["mid"], r["mid"])) or []
        fmt = lambda t: f"{t[0]}-{t[1]}" if t else "n/a"  # noqa: E731
        detail = {"match": f"{r['home']} v {r['away']}", "kickoff": r["date"],
                  "api_football": f"{r['score_home']}-{r['score_away']} (HT {r['ht_score_home']}-{r['ht_score_away']})",
                  "tonybet": f"{r['ft_home']}-{r['ft_away']} (HT {r['ht_home']}-{r['ht_away']})",
                  "api_football_now": fmt(af_now), "espn": fmt(espn), "verdict": v,
                  "settled_bets": {a["ledger"]: a["n"] for a in affected}}
        log.warning("results-check: %s", detail)
        if v == "unresolved" and seen:
            continue                      # already alerted; keep re-trying silently
        c["new"] += 1
        if dry_run:
            continue
        if v == "corrected":
            detail["regraded"] = _apply(r, score)
            c["corrected"] += 1
        elif v == "af_confirmed":
            c["af_confirmed"] += 1
        record_finding("results_disagree", r["mid"], "Tonybet", detail)
        if v == "corrected":
            record_finding("results_corrected", r["mid"], "API-Football", detail)
        try:
            from workers.notify.telegram import send_telegram
            head = f"⚠️ <b>Result disagreement</b> — {detail['match']}: API-Football " \
                   f"{detail['api_football']}, Tonybet {detail['tonybet']}, AF re-fetch " \
                   f"{detail['api_football_now']}, ESPN {detail['espn']}."
            if v == "corrected":
                g = detail["regraded"]
                tail = (f" ✅ Majority says {fmt(score)} — score corrected; re-graded "
                        f"{g['shadow_bets']} shadow / {g['simulated_bets']} simulated / "
                        f"{g['picks_forward_test']} forward-test (PnL {g['pnl_delta']:+.2f}).")
            elif v == "af_confirmed":
                tail = " ESPN agrees with API-Football — Tonybet is wrong, nothing changed."
            else:
                bets = sum(detail["settled_bets"].values())
                tail = f" No majority — {bets} settled bet(s) stay graded on API-Football; check by hand."
            send_telegram(head + tail, dedup_key=f"results-disagree-{r['mid']}-{v}",
                          dedup_window_s=86400 * 7)
        except Exception as e:  # noqa: BLE001
            log.debug("results-check: alert failed: %s", e)
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
