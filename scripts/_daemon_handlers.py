"""Telegram command handlers for coolbet_daemon. Imported by the daemon at
startup. Kept in a separate file so the daemon's main script stays focused
on the loop and these handlers can be unit-tested without firing the loop."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


_HELP = (
    "<b>Coolbet daemon — commands</b>\n"
    "<code>/status</code>     — daemon health snapshot\n"
    "<code>/pause</code>      — stop the placement loop (sweep keeps going)\n"
    "<code>/resume</code>     — re-enable placement\n"
    "<code>/place_mode dry|record|execute</code> — change mode at runtime\n"
    "<code>/summary</code>    — force-send the daily summary now\n"
    "<code>/relogin</code>    — force a fresh JWT now via /s/auth/renew-token\n"
    "                  (auto-runs every 20 min — use this to force-early)\n"
    "<code>/help</code>       — this message"
)


def build_handlers(args, ctrl: dict) -> dict[str, Callable[[list[str]], Optional[str]]]:
    """Construct handler dict bound to the daemon's CLI args + control flags.
    `args` is the daemon's argparse Namespace; `ctrl` is the mutable runtime
    flag dict that the daemon's main loop reads each tick."""

    def _help(_args):
        return _HELP

    def _status(_args):
        # Run the existing status CLI in a subprocess; capture output.
        # Cheap (no Coolbet API hits) so safe to fire on every command.
        try:
            out = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "coolbet_status.py")],
                capture_output=True, text=True, timeout=20,
            ).stdout
            # Telegram doesn't render Rich's color codes well — strip them
            import re
            clean = re.sub(r"\x1b\[[0-9;]*m", "", out)
            # Replace heavy box characters that don't always render on mobile
            clean = clean.replace("━", "-").replace("┓", "+").replace("┛", "+")
            clean = clean.replace("┃", "|").replace("┏", "+").replace("┗", "+")
            clean = clean.replace("┡", "+").replace("┩", "+").replace("│", "|")
            clean = clean.replace("╇", "+").replace("╈", "+").replace("┳", "+")
            clean = clean.replace("┻", "+").replace("─", "-")
            return f"<pre>{clean[:3500]}</pre>"
        except Exception as e:
            return f"❌ status failed: <pre>{e}</pre>"

    def _pause(_args):
        ctrl["paused"] = True
        return "⏸ Placement paused. Sweep + keepalive continue.\nResume with /resume."

    def _resume(_args):
        ctrl["paused"] = False
        return "▶ Placement resumed."

    def _place_mode(cmd_args):
        if not cmd_args or cmd_args[0] not in ("dry", "record", "execute"):
            return ("Usage: <code>/place_mode dry|record|execute</code>\n"
                    f"Current: <b>{ctrl.get('place_mode') or args.place_mode}</b>")
        new = cmd_args[0]
        if new == "execute":
            # Belt + braces — refuse to flip execute via Telegram unless safety
            # guardrails are configured. The daemon's --max-stake-per-bet
            # cap is the hard backstop; if it's absent, refuse.
            if not getattr(args, "max_stake_per_bet", None):
                return ("⛔ Refusing to flip <b>execute</b> via Telegram — "
                        "daemon was launched without <code>--max-stake-per-bet</code>. "
                        "Restart the daemon with a cap (eg <code>--max-stake-per-bet 5</code>) "
                        "first, then try again.")
        ctrl["place_mode"] = new
        return f"✓ Placement mode set to <b>{new}</b> for this session."

    def _summary(_args):
        ctrl["force_summary"] = True
        return "📊 Daily summary queued — will send within ~30s."

    def _relogin(_args):
        ctrl["force_login"] = True
        return "🔄 Login refresh queued — will fire on the next loop iteration."

    return {
        "/help":       _help,
        "/start":      _help,
        "/status":     _status,
        "/pause":      _pause,
        "/resume":     _resume,
        "/place_mode": _place_mode,
        "/summary":    _summary,
        "/relogin":    _relogin,
    }
