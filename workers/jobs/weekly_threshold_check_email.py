"""Weekly threshold_check → email digest.

Called by job_weekly_threshold_check (Sunday 06:00 UTC, after Sunday's retrain
chain finishes at 03:00 / meta-retrain 04:00 / meta-validate 05:00). Ships
the full threshold_check.py stdout — the key counts that gate every
"is X ready?" decision in PRIORITY_QUEUE.md — to ADMIN_ALERT_EMAIL via
Resend.

Origin: 2026-06-06 audit found threshold_check.py output was 13 days stale
(last manual run 2026-05-24) AND had 3 silent bugs masking what was true.
Hours of debugging would have been avoided if a fresh snapshot landed
every week.

Best-effort renderer: just wraps the stdout in <pre> so the report shape
stays identical to what an operator sees running the script locally.
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
    <div style='font-family:system-ui,-apple-system,sans-serif;max-width:760px'>
      <h2 style='margin:0 0 8px 0'>Weekly threshold snapshot</h2>
      <p style='color:#666;margin:0 0 16px 0'>
        Counts that gate every "is X ready?" decision in PRIORITY_QUEUE.md.
        Generated {ran_at}.
      </p>
      <pre style='background:#f7f7f7;border:1px solid #e0e0e0;padding:14px;
                  border-radius:6px;font-family:monospace;font-size:13px;
                  line-height:1.45;overflow-x:auto;white-space:pre'>{safe}</pre>
      <p style='color:#888;font-size:13px;margin-top:16px'>
        Re-run any time: <code>python3 scripts/threshold_check.py</code>.
        Filters mirror <code>fit_platt.py</code> exactly so the counts ARE
        the gate.
      </p>
    </div>
    """


def send_weekly_threshold_check_email(stdout: str, ran_at: str) -> None:
    """Public entry point — wrap threshold_check stdout and email digest."""
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print("[yellow]Weekly threshold check email skipped — no Resend creds[/yellow]")
        return
    if not stdout.strip():
        console.print("[yellow]Weekly threshold check email skipped — empty stdout[/yellow]")
        return

    subject = f"Threshold snapshot · {ran_at}"
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
            console.print(f"[green]Weekly threshold check email sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Weekly threshold check email failed ({resp.status_code}): {resp.text[:200]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Weekly threshold check email error: {e}[/yellow]")
