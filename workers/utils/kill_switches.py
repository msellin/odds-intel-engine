"""
Operator kill switches — disable subsystems via Railway env vars without a redeploy.

Usage: set the env var to "1" or "true" in Railway → subsystem skips its next cycle.
Unset or "0" = enabled (default). Changes take effect within one cycle (30s–5min).

Available flags:
  DISABLE_INPLAY_STRATEGIES  — InplayBot stops placing paper bets (LivePoller keeps running)
  DISABLE_ENRICHMENT         — Morning enrichment skipped (standings, H2H, team stats, injuries)
  DISABLE_NEWS_CHECKER       — Gemini news analysis skipped (saves API cost during incidents)
  DISABLE_PAPER_BETTING      — Betting pipeline skipped (no new paper bets placed)
"""

import os
from rich.console import Console

console = Console()

_FLAGS = {
    "inplay":        "DISABLE_INPLAY_STRATEGIES",
    "enrichment":    "DISABLE_ENRICHMENT",
    "news_checker":  "DISABLE_NEWS_CHECKER",
    "paper_betting": "DISABLE_PAPER_BETTING",
}


def is_disabled(flag: str) -> bool:
    """Return True and log a warning if the kill switch env var is active."""
    var = _FLAGS.get(flag)
    if not var:
        return False
    val = os.getenv(var, "").strip().lower()
    if val in ("1", "true", "yes"):
        console.print(f"[bold yellow]⚡ KILL SWITCH ACTIVE: {flag} — {var}='{val}' — skipping[/bold yellow]")
        return True
    return False
