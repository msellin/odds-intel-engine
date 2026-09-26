#!/usr/bin/env python3
"""Add the status line to public Telegram posts that went out before it existed ([[#183]]).

WHY. Since #183 every public pick opens with its bot's status and name
("🟢 ACTIVE · Sharp-line picks — 1x2" / "🧪 TESTING · Goals over/under — new model"), stamped by
workers/notify/pick_sender.send_pick. Owner 2026-09-26: "can we also edit all the todays picks
messages on telegram?" — so the day's earlier posts read the same way as the new ones.

HOW. The Bot API cannot read a channel post back, so each post is RE-RENDERED from its ledger row
with the sender's own formatter (the forward-test publisher's `render()`, the signaler's
`_format_public_signal()`), prefixed with `public_status_line()` for the bot's CURRENT status, and
swapped in with editMessageText. Which posts: `pick_sends` rows (channel public, status sent, with a
message id) — the audited send log, so only posts we actually made are touched.

The prices come from the stored ledger values. The dry run prints every message first — compare a
few against the channel before running with --send.

    python3 scripts/relabel_telegram_posts.py --since 2026-09-26            # dry run
    python3 scripts/relabel_telegram_posts.py --since 2026-09-26 --send     # edits the channel

Safe to re-run: an unchanged post returns "message is not modified" (counted as not edited).
Paced at one edit per 3.1 s, under Telegram's ~20-per-minute limit for one chat.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402
from workers.utils.bot_status import public_status_line  # noqa: E402

_SENT = """SELECT ps.pick_table, ps.pick_id, ps.bot_name, ps.message_id, ps.sent_at,
                  bd.status, bd.display_name
             FROM pick_sends ps
             LEFT JOIN bot_distribution bd ON bd.bot_name = ps.bot_name
            WHERE ps.channel = 'public' AND ps.status = 'sent' AND ps.message_id IS NOT NULL
              AND ps.sent_at >= %s::date
            ORDER BY ps.sent_at"""

_FT = """SELECT p.id, p.market, p.selection, p.odds::float AS odds, p.bookmaker, p.edge::float AS edge,
                p.p_sharp::float AS p_sharp, p.anchor_bookmaker, p.kickoff_at, p.grade, p.grade_reasons,
                ht.name AS home_team, at.name AS away_team, l.name AS league
           FROM picks_forward_test p
           JOIN matches m  ON m.id = p.match_id
           JOIN teams ht   ON ht.id = m.home_team_id
           JOIN teams at   ON at.id = m.away_team_id
           LEFT JOIN leagues l ON l.id = m.league_id
          WHERE p.id = %s"""

# The fields coolbet_signaler._format_public_signal reads, from the row that was sent.
_SIM = """SELECT sb.market, sb.selection, sb.odds_at_pick, sb.edge_percent, sb.recommended_bookmaker,
                 m.date AS match_date, ht.name AS home_team, at2.name AS away_team,
                 l.name AS league, l.country AS country
            FROM simulated_bets sb
            JOIN matches m   ON m.id = sb.match_id
            JOIN teams ht    ON ht.id = m.home_team_id
            JOIN teams at2   ON at2.id = m.away_team_id
            LEFT JOIN leagues l ON l.id = m.league_id
           WHERE sb.id = %s"""


def rebuild(s: dict) -> str | None:
    if s["pick_table"] == "picks_forward_test":
        from scripts.publish_picks_forward_test import render
        rows = execute_query(_FT, (s["pick_id"],))
        body = render(rows[0]) if rows else None
    else:
        from workers.automation.coolbet_signaler import _format_public_signal
        rows = execute_query(_SIM, (s["pick_id"],))
        body = _format_public_signal(rows[0]) if rows else None
    if body is None:
        return None
    return public_status_line(s.get("status"), s.get("display_name")) + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD (UTC): posts sent on/after this day")
    ap.add_argument("--send", action="store_true", help="actually edit the channel posts")
    a = ap.parse_args()

    sent = execute_query(_SENT, (a.since,))
    channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL")
    print(f"{len(sent)} public posts since {a.since} ({'EDITING' if a.send else 'dry run'})\n")
    ok = not_edited = missing = 0
    for s in sent:
        text = rebuild(s)
        if text is None:
            missing += 1
            print(f"!! no ledger row for {s['pick_table']} {s['pick_id']} (message {s['message_id']})")
            continue
        if not a.send:
            print(f"--- message {s['message_id']} · {s['bot_name']} · sent {s['sent_at']:%H:%M} ---\n{text}\n")
            continue
        from workers.notify.telegram import edit_telegram_message
        if edit_telegram_message(channel, int(s["message_id"]), text,
                                 remove_buttons=False, disable_preview=True):
            ok += 1
        else:
            not_edited += 1          # "message is not modified" also lands here (logged by the helper)
        time.sleep(3.1)
    if a.send:
        print(f"edited {ok}, not edited {not_edited}, no ledger row {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
