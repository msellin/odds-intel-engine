"""SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20 — neutralise the picks the
sharp-anchored bots raised off prices belonging to other fixtures.

WHY THESE ROWS CANNOT STAY AS 'won'/'lost'. `shadow_bot_scoreboard` counts every
row whose result is won or lost, at `odds_at_pick`. Nine of these "won" at prices
nobody could ever have taken — one at 101.00 on a fixture Pinnacle priced 1.83 —
so leaving them settled publishes a P&L built on money that could not have been
won. Voiding is the established remedy here: `void_reason` already carries
'KAMBI-CRITERION-CONTAMINATION-2026-09-05: raised off a phantom price' on 14
rows for the same class of fault, and the scoreboard's won/lost filter is what
makes a void disappear from the published number.

ROWS ARE KEPT, NOT DELETED. `PRIORITY_QUEUE.md` (1X2-HOME-AWAY-INVERSIONS) says
it plainly: the rows are evidence of an upstream fault, and discarding them
destroys the only record of how far it reached.

THE CRITERION IS THE PRODUCTION GATE, NOT A JUDGEMENT CALL. Every row whose edge
exceeds the `edge_ceiling` those bots now enforce is voided — i.e. exactly the
picks that would not be raised today. Picking rows by hand would leave a number
nobody could reproduce. `void_reason` additionally records whether the row had
DIRECT corroboration (anchor ratio > 1.5625x, or the book and the anchor
disagreeing about which side is favourite) so the strength of evidence per row
survives in the data rather than only in this commit message.

Run with --apply to write; default is a dry run.
"""
from __future__ import annotations

import argparse
import logging

from workers.api_clients.db import get_conn
from workers.automation.anchor_sanity import is_anchor_sane
from workers.automation.bot_configs import CONFIG_BY_NAME

log = logging.getLogger(__name__)

COHORTS = ("trigger_1x2_sharp", "trigger_ou_sharp")
# The `quarantine:` PREFIX IS LOAD-BEARING, not decoration. `settlement.
# resettle_wrongly_voided_bets` re-grades every void on a finished match and
# clears `void_reason` to NULL — it skips only reasons starting with
# "quarantine". The first run of this script used a bare descriptive reason and
# all 31 rows were resurrected within hours, putting the +549.9% ROI bot back on
# the scoreboard. (The `KAMBI-CRITERION-CONTAMINATION` rows that looked like a
# working precedent had survived only because their matches are postponed with
# NULL scores.) This is a deliberate, permanent quarantine: the price never
# existed, so there is no future state in which these rows become gradeable.
TAG = "quarantine: SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20"


def _evidence(cur, mid, market, selection, odds, book, kickoff) -> list[str]:
    """Direct corroboration that this quote is from another fixture."""
    def latest(bk):
        cur.execute(
            """SELECT DISTINCT ON (selection) selection, odds::float
                 FROM odds_snapshots
                WHERE match_id=%s AND market=%s AND bookmaker=%s
                  AND is_live IS NOT TRUE AND timestamp <= %s
                ORDER BY selection, timestamp DESC""",
            (mid, market, bk, kickoff))
        return dict(cur.fetchall())
    pin, bk = latest("Pinnacle"), latest(book)
    why = []
    if not is_anchor_sane(odds, pin.get(selection)):
        why.append("anchor ratio >1.5625x")
    common = set(pin) & set(bk)
    if len(common) >= 2 and min(common, key=lambda s: pin[s]) != min(common, key=lambda s: bk[s]):
        # Two real books cannot disagree about which outcome is FAVOURITE in the
        # same market on the same fixture. When they do, one of them is pricing
        # a different match.
        why.append("book and anchor disagree on the favourite")
    return why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a = ap.parse_args()

    ceilings = {c.shadow_cohort: c.edge_ceiling for c in CONFIG_BY_NAME.values()}
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT s.id, s.shadow_cohort, s.match_id, s.market, s.selection,
                      s.odds_at_pick::float, s.edge_percent::float,
                      s.recommended_bookmaker, s.result::text, m.date
                 FROM shadow_bets s JOIN matches m ON m.id = s.match_id
                WHERE s.shadow_cohort = ANY(%s) AND s.void_reason IS NULL""",
            (list(COHORTS),))
        rows = cur.fetchall()

        todo = []
        for rid, co, mid, mk, sel, odds, edge, book, res, kickoff in rows:
            ceiling = ceilings.get(co)
            if ceiling is None or edge is None or edge <= ceiling:
                continue
            why = _evidence(cur, mid, mk, sel, odds, book, kickoff)
            corrob = "; ".join(why) if why else ("no direct corroboration — voided on the "
                                                 "edge ceiling alone")
            reason = (
                f"{TAG}: raised off a phantom price. Fair value for this bot is a "
                f"Shin-de-vigged Pinnacle line, against which the largest overlay ever "
                f"observed on this project is +6.6%; this pick claimed +{edge*100:.1f}% at "
                f"{book} {odds:.2f}. The book quote belongs to a different fixture "
                f"(upstream fuzzy fixture-matching fault). Evidence: {corrob}. "
                f"Excluded from performance; row kept as evidence.")
            todo.append((rid, co, mk, sel, odds, edge, res, reason, bool(why)))

        by_co = {}
        for t in todo:
            by_co.setdefault(t[1], []).append(t)
        for co, ts in sorted(by_co.items()):
            won = sum(1 for t in ts if t[6] == "won")
            direct = sum(1 for t in ts if t[8])
            print(f"{co}: {len(ts)} rows to void ({won} currently 'won', "
                  f"{direct} with direct corroboration)")
        print(f"TOTAL {len(todo)}")

        if not a.apply:
            print("\nDRY RUN — re-run with --apply to write.")
            return 0
        for rid, _, _, _, _, _, _, reason, _ in todo:
            cur.execute(
                "UPDATE shadow_bets SET result='void', void_reason=%s WHERE id=%s",
                (reason, rid))
        conn.commit()
        print(f"\nvoided {len(todo)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
