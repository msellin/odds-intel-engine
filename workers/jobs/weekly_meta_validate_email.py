"""Weekly meta-validate → email digest.

Called by job_weekly_meta_validate after scripts/validate_meta_b_ml3.py finishes.
Parses the rich Table output (per-bundle verdict + activation summary) and
emails an HTML digest to ADMIN_ALERT_EMAIL via Resend so the 2026-06-10
activation decision is no longer a manual run.

Best-effort parser: pulls the per-bundle row that contains 'PASS', 'MARGINAL',
or 'FAIL' from the script's verdict block. If no bundle PASSes, the digest
still ships with the current best-Δ for tracking over time.
"""
from __future__ import annotations
import os
import re
from rich.console import Console

console = Console()

RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL        = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")


def _parse_summary(stdout: str) -> list[dict]:
    """Extract per-bundle verdicts from the script's stdout.

    Looks for lines matching the "Activation verdict per bundle" table:
      bundle name | model_type | top % | bot % | Δpp | verdict
    Returns list of dicts ordered by Δpp descending.
    """
    rows: list[dict] = []
    in_verdict_table = False
    for line in stdout.splitlines():
        if "Activation verdict per bundle" in line:
            in_verdict_table = True
            continue
        if not in_verdict_table:
            continue
        if line.startswith("│") and "PASS" in line or "MARGINAL" in line or "FAIL" in line:
            cells = [c.strip() for c in line.strip("│").split("│")]
            if len(cells) < 6:
                continue
            try:
                delta_pp = float(re.sub(r"[^\d.+\-]", "", cells[4]))
            except (ValueError, IndexError):
                continue
            rows.append({
                "bundle":    cells[0],
                "type":      cells[1],
                "top_pct":   cells[2],
                "bot_pct":   cells[3],
                "delta_pp":  delta_pp,
                "verdict":   cells[5] if len(cells) > 5 else "?",
            })
    rows.sort(key=lambda r: -r["delta_pp"])
    return rows


def _final_recommendation(stdout: str) -> str:
    """Extract the one-line bottom recommendation from the script."""
    for marker in ("Recommend: META_B_ML3_VERSION",
                   "Keep META_B_ML3_ENABLED=false",
                   "Don't activate"):
        for line in stdout.splitlines():
            if marker in line:
                return line.strip()
    return ""


def _render_html(rows: list[dict], recommendation: str) -> str:
    body_rows = "".join(
        f"<tr>"
        f"<td style='padding:6px 12px;font-family:monospace'>{r['bundle']}</td>"
        f"<td style='padding:6px 12px'>{r['type']}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{r['top_pct']}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{r['bot_pct']}</td>"
        f"<td style='padding:6px 12px;text-align:right'>{r['delta_pp']:+.1f}</td>"
        f"<td style='padding:6px 12px;font-weight:600'>{r['verdict']}</td>"
        f"</tr>"
        for r in rows
    )
    return f"""
    <div style='font-family:system-ui,-apple-system,sans-serif;max-width:680px'>
      <h2 style='margin:0 0 8px 0'>Weekly meta-model validation</h2>
      <p style='color:#666;margin:0 0 16px 0'>{recommendation or 'See attached verdict.'}</p>
      <table style='border-collapse:collapse;width:100%;border:1px solid #e0e0e0'>
        <thead style='background:#f5f5f5;text-align:left'>
          <tr>
            <th style='padding:8px 12px'>bundle</th>
            <th style='padding:8px 12px'>type</th>
            <th style='padding:8px 12px;text-align:right'>top<br/>CLV-beat%</th>
            <th style='padding:8px 12px;text-align:right'>bottom<br/>CLV-beat%</th>
            <th style='padding:8px 12px;text-align:right'>Δpp</th>
            <th style='padding:8px 12px'>verdict</th>
          </tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
      <p style='color:#888;font-size:13px;margin-top:16px'>
        Activation gate: top-quintile CLV-beat ≥ bottom + 5pp (PASS). Re-run any time:
        <code>python3 scripts/validate_meta_b_ml3.py --since 2026-05-25</code>
      </p>
    </div>
    """


def send_weekly_meta_validate_email(eval_stdout: str) -> None:
    """Public entry point — parse validator stdout and email digest."""
    rows = _parse_summary(eval_stdout)
    rec = _final_recommendation(eval_stdout)
    if not rows:
        console.print("[yellow]Weekly meta validate email: no verdict rows parsed from stdout[/yellow]")
        return

    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print("[yellow]Weekly meta validate email skipped — no Resend creds[/yellow]")
        return

    pass_count = sum(1 for r in rows if r["verdict"] == "PASS")
    fail_count = sum(1 for r in rows if r["verdict"] == "FAIL")
    subject = f"Meta-validate · {pass_count} PASS / {fail_count} FAIL · best Δ {rows[0]['delta_pp']:+.1f}pp"
    body = _render_html(rows, rec)

    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": body},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]Weekly meta validate email sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Weekly meta validate email failed ({resp.status_code})[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Weekly meta validate email error: {e}[/yellow]")
