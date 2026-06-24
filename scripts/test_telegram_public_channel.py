"""
Smoke-test the public Telegram channel send pipe.

Posts a one-off "channel wiring verified" message to whatever channel
TELEGRAM_PUBLIC_CHANNEL points at, using TELEGRAM_BOT_TOKEN. Used as a
once-only check after creating the channel and adding the bot as admin.

Usage:
    TELEGRAM_PUBLIC_CHANNEL=@oddsintelpicks python3 scripts/test_telegram_public_channel.py

Exit code:
    0  Posted successfully (Telegram returned the new message_id)
    1  Failed — most commonly because the bot is not yet an admin on the
       channel, in which case Telegram returns "Forbidden: bot is not a
       member of the channel chat". Add the bot as admin with 'Post
       Messages' permission and re-run.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from workers.notify.telegram import send_telegram_public  # noqa: E402


def main() -> int:
    channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL")
    if not channel:
        print("FAIL: TELEGRAM_PUBLIC_CHANNEL env var not set.")
        print("      Set it to '@oddsintelpicks' (or the numeric -100… ID) and re-run.")
        return 1
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("FAIL: TELEGRAM_BOT_TOKEN env var not set.")
        return 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        "🟢 <b>Channel wiring verified</b>\n"
        f"OddsIntel auto-poster is live as of {now}.\n\n"
        "Live picks will start arriving here once production strategies fire. "
        "Every pick is also logged at "
        "<a href='https://oddsintel.app/picks'>oddsintel.app/picks</a> and "
        "settled outcomes append to "
        "<a href='https://oddsintel.app/performance'>oddsintel.app/performance</a>."
    )
    mid = send_telegram_public(msg)
    if mid:
        print(f"OK posted message_id={mid} to {channel}")
        return 0
    print(f"FAIL: send returned no message_id for {channel}")
    print("Most likely cause: the bot is not yet an admin on the channel.")
    print("Fix: in Telegram, open the channel → Manage → Administrators →")
    print("     Add Admin → search for your bot → grant 'Post Messages' permission.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
