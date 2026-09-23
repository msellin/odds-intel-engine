#!/usr/bin/env python3
"""Re-render already-sent consensus posts so each shows its grade ([[#095]]).

WHY. The B/C grade ([[#094]]) reached Telegram at ~13:00 on 2026-09-23. Every
consensus pick sent before that went out with no grade line, although all of
them were graded afterwards by `scripts/backfill_consensus_grades.py`. Owner:
*"would be good if we could edit previous messages as well to show grade"*.

HOW. The Bot API cannot READ a channel post back, so the post is re-RENDERED
from its ledger row with the publisher's own `render()` — the same function
that wrote it — and swapped in with editMessageText. Nothing about the pick
changes (odds, book, break-even are the stored values); only the grade line is
new. Previews stay off, exactly as at send time.

SAFE TO RE-RUN. A post that already matches returns "message is not modified",
which is counted and skipped. Paced at one edit per 3.1 s, under Telegram's
~20-per-minute limit for a single chat.

    python3 scripts/regrade_consensus_telegram_posts.py            # dry run: prints
    python3 scripts/regrade_consensus_telegram_posts.py --send     # edits the channel
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from scripts.publish_picks_forward_test import CONSENSUS_ARM, render  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402
from workers.notify.telegram import edit_telegram_message  # noqa: E402


def load_posts() -> list[dict]:
    return execute_query(
        """SELECT p.id, p.telegram_message_id, p.market, p.selection,
                  p.odds::float AS odds, p.bookmaker, p.edge::float AS edge,
                  p.p_sharp::float AS p_sharp, p.anchor_bookmaker, p.kickoff_at,
                  p.grade, p.grade_reasons,
                  ht.name AS home_team, at.name AS away_team, l.name AS league
             FROM picks_forward_test p
             JOIN matches m  ON m.id = p.match_id
             JOIN teams ht   ON ht.id = m.home_team_id
             JOIN teams at   ON at.id = m.away_team_id
             LEFT JOIN leagues l ON l.id = m.league_id
            WHERE p.arm = %s AND p.telegram_message_id IS NOT NULL
              AND p.grade IS NOT NULL
            ORDER BY p.published_at""", (CONSENSUS_ARM,))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually edit the channel posts")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    posts = load_posts()
    if a.limit:
        posts = posts[:a.limit]
    channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL")
    print(f"{len(posts)} graded consensus posts with a stored message id "
          f"({'EDITING' if a.send else 'dry run'})\n")
    ok = unchanged = failed = 0
    for i, p in enumerate(posts):
        text = render(p)
        if not a.send:
            if i < 2:
                print(f"--- message {p['telegram_message_id']} (grade {p['grade']}) ---\n{text}\n")
            continue
        if edit_telegram_message(channel, int(p["telegram_message_id"]), text,
                                 remove_buttons=False, disable_preview=True):
            ok += 1
        else:
            # "message is not modified" also lands here — logged by the helper.
            unchanged += 1
        time.sleep(3.1)
    if a.send:
        print(f"edited {ok}, not edited {unchanged + failed} (already current or failed — see log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
