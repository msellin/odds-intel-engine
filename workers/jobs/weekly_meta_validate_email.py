"""Weekly meta-validate → email digest.

Called by job_weekly_meta_validate after scripts/validate_meta_b_ml3.py finishes.
Parses the rich Table output (per-bundle verdict + activation summary) and
emails an HTML digest to ADMIN_ALERT_EMAIL via Resend so the 2026-06-10
activation decision is no longer a manual run.

Best-effort parser: pulls the per-bundle row carrying a verdict token
(PASS / MARGINAL / FAIL / INVERTED / INSUFFICIENT-OOS) from the script's
verdict block. If no bundle PASSes, the digest still ships with the current
best-Δ for tracking over time.
"""
from __future__ import annotations
import os
import re
from rich.console import Console

console = Console()

RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL        = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")


VERDICTS = ("PASS", "MARGINAL", "FAIL", "INVERTED", "INSUFFICIENT-OOS")


def _parse_summary(stdout: str) -> list[dict]:
    """Extract per-bundle verdicts from the script's stdout.

    Handles both table shapes:
      legacy (pre-2026-09-06): bundle | type | top% | bottom% | Δpp | verdict
      current:  bundle | type | n(OOS) | r | t | Q1 CLV% | Q5 CLV% | Δpp | verdict

    Columns are read from the RIGHT (verdict last, Δpp second-last, and the
    two CLV columns before that), which is stable across both shapes.
    """
    rows: list[dict] = []
    in_verdict_table = False
    for line in stdout.splitlines():
        if "verdict per bundle" in line.lower():
            in_verdict_table = True
            continue
        if not in_verdict_table:
            continue
        if not line.lstrip().startswith("\u2502"):
            continue
        if not any(v in line for v in VERDICTS):
            continue
        cells = [c.strip() for c in line.strip().strip("\u2502").split("\u2502")]
        if len(cells) < 6:
            continue
        try:
            delta_pp = float(re.sub(r"[^\d.+\-]", "", cells[-2]))
        except (ValueError, IndexError):
            continue
        rows.append({
            "bundle":   cells[0],
            "type":     cells[1],
            "top_pct":  cells[-4],
            "bot_pct":  cells[-3],
            "delta_pp": delta_pp,
            "verdict":  cells[-1],
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
            <th style='padding:8px 12px;text-align:right'>Q1<br/>CLV%</th>
            <th style='padding:8px 12px;text-align:right'>Q5<br/>CLV%</th>
            <th style='padding:8px 12px;text-align:right'>Δpp</th>
            <th style='padding:8px 12px'>verdict</th>
          </tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
      <p style='color:#888;font-size:13px;margin-top:16px'>
        Gate (rewritten 2026-09-06): out-of-sample Pearson t ≥ +2.0 of meta score against real <code>clv_pinnacle_devig</code>, Q5−Q1 spread ≥ +2pp, n ≥ 200, no odds-mix warning. <b>INVERTED</b> means the score is anti-correlated with real CLV — gating on it is worse than no gate. Re-run any time:
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
