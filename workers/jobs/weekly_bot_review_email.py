"""Weekly bot maturity review → email digest.

Called by job_weekly_bot_review (Sunday 06:30 UTC, after the 03/04/05/06 chain
finishes). Ships the full weekly_bot_review.py stdout — per-bot 30/60/90d
hit-rate / ROI / CLV / sim-vs-real divergence + PROMOTE / DEMOTE / HOLD verdict
— to ADMIN_ALERT_EMAIL via Resend.

Origin: 2026-06-13 audit found `bot_high_alignment` (maturity=beta, -€56 over
50 real bets) had been auto-placing real money for days because the Mac
daemon lacked a maturity gate. The decision "which bots are trustworthy
enough to spend real money on?" was manual and ad-hoc. This email lands every
Sunday so the operator can't miss it.

Best-effort renderer: wraps the stdout in <pre> so the report shape stays
identical to what the operator sees running the script locally.
"""
from __future__ import annotations
import os
from rich.console import Console

console = Console()

RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL        = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")


def _render_html(stdout: str, ran_at: str) -> str:
    safe = (
        stdout.replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
    )
    return f"""
    <div style='font-family:system-ui,-apple-system,sans-serif;max-width:900px'>
      <h2 style='margin:0 0 8px 0'>Weekly bot maturity review</h2>
      <p style='color:#666;margin:0 0 16px 0'>
        Per-bot 30/60/90d hit-rate, ROI, CLV, and sim-vs-real divergence with a
        PROMOTE / DEMOTE / HOLD verdict. Generated {ran_at}.
      </p>
      <pre style='background:#f7f7f7;border:1px solid #e0e0e0;padding:14px;
                  border-radius:6px;font-family:monospace;font-size:12px;
                  line-height:1.45;overflow-x:auto;white-space:pre'>{safe}</pre>
      <p style='color:#888;font-size:13px;margin-top:16px'>
        Re-run any time: <code>python3 scripts/weekly_bot_review.py</code>.
        Thresholds (real ROI &gt; +10% / sim CLV &gt; +5% to promote, real ROI &lt; -5%
        to demote a calibrated bot) are starting points — refine after the first
        2-3 weeks. Background: PRIORITY_QUEUE.md → BOT-MATURITY-REVIEW-WEEKLY.
      </p>
    </div>
    """


def send_weekly_bot_review_email(stdout: str, ran_at: str) -> None:
    """Public entry point — wrap weekly_bot_review stdout and email digest."""
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print("[yellow]Weekly bot review email skipped — no Resend creds[/yellow]")
        return
    if not stdout.strip():
        console.print("[yellow]Weekly bot review email skipped — empty stdout[/yellow]")
        return

    subject = f"Bot maturity review · {ran_at}"
    body = _render_html(stdout, ran_at)

    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": body},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]Weekly bot review email sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Weekly bot review email failed ({resp.status_code}): {resp.text[:200]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Weekly bot review email error: {e}[/yellow]")
