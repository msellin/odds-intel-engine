"""Weekly retrain → email digest.

Called by job_weekly_retrain after scripts/weekly_eval_and_compare.py produces
its SUMMARY_JSON output. Parses that JSON and emails a per-market comparison
to ADMIN_ALERT_EMAIL via Resend so the human in the loop actually sees the
retrain result instead of it going to /dev/null on the the VPS cron logs.
"""
from __future__ import annotations
import os, re, json
from rich.console import Console

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")


def _extract_summary(stdout: str) -> dict | None:
    """Pull the SUMMARY_JSON line from weekly_eval_and_compare.py output."""
    m = re.search(r"^SUMMARY_JSON:\s*(\{.*\})$", stdout, re.MULTILINE)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _render_html(summary: dict) -> str:
    candidate = summary["candidate"]
    production = summary["production"]
    verdicts = summary.get("market_verdicts", {})
    cand_m = summary.get("candidate_metrics", {})
    prod_m = summary.get("production_metrics", {})
    n = summary.get("n_matches", 0)
    window = summary.get("holdout_window", "")

    better = sum(1 for v in verdicts.values() if v == "BETTER")
    worse = sum(1 for v in verdicts.values() if v == "WORSE")
    ties = sum(1 for v in verdicts.values() if v == "TIE")

    overall = (
        "🟢 candidate improves" if better > worse and worse == 0
        else "🟡 mixed — review per market" if better and worse
        else "🔴 candidate regresses" if worse > better
        else "⚪ no material change"
    )

    rows_html = []
    for mkt in ("1x2_home", "1x2_draw", "1x2_away", "over25", "under25"):
        c = cand_m.get(mkt); p = prod_m.get(mkt)
        if not c or not p:
            continue
        ll_delta = 100 * (c["log_loss"] - p["log_loss"]) / p["log_loss"]
        verdict = verdicts.get(mkt, "—")
        color = {"BETTER": "#0a7", "WORSE": "#c33", "TIE": "#888"}.get(verdict, "#888")
        rows_html.append(
            f"<tr>"
            f"<td style='padding:6px 12px'>{mkt}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{c['log_loss']:.4f}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{p['log_loss']:.4f}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:{color}'>{ll_delta:+.1f}%</td>"
            f"<td style='padding:6px 12px;color:{color};font-weight:600'>{verdict}</td>"
            f"</tr>"
        )

    return f"""
    <div style='font-family:system-ui,-apple-system,sans-serif;max-width:680px'>
      <h2 style='margin:0 0 8px 0'>Weekly retrain — {candidate} vs {production}</h2>
      <p style='color:#666;margin:0 0 12px 0'>{overall} · {better} better / {worse} worse / {ties} tied · held-out {window} (n={n})</p>
      <table style='border-collapse:collapse;width:100%;border:1px solid #e0e0e0'>
        <thead style='background:#f5f5f5;text-align:left'>
          <tr>
            <th style='padding:8px 12px'>market</th>
            <th style='padding:8px 12px;text-align:right'>log_loss<br/>candidate</th>
            <th style='padding:8px 12px;text-align:right'>log_loss<br/>production</th>
            <th style='padding:8px 12px;text-align:right'>Δ log_loss</th>
            <th style='padding:8px 12px'>verdict</th>
          </tr>
        </thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
      <p style='color:#888;font-size:13px;margin-top:16px'>
        To promote: <code>python3 scripts/promote_model.py {candidate}</code><br/>
        To inspect details: <code>SELECT cv_metrics FROM model_versions WHERE version IN ('{candidate}','{production}');</code>
      </p>
    </div>
    """


def send_weekly_retrain_email(candidate: str, production: str, eval_stdout: str) -> None:
    """Public entry point — parse stdout from weekly_eval_and_compare.py and email digest."""
    summary = _extract_summary(eval_stdout)
    if not summary:
        console.print(f"[yellow]Weekly retrain email: no SUMMARY_JSON found in eval output[/yellow]")
        return

    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print(f"[yellow]Weekly retrain email skipped — no Resend creds[/yellow]")
        return

    subject = f"Retrain {candidate} vs {production} — {sum(1 for v in summary.get('market_verdicts', {}).values() if v == 'BETTER')} better / {sum(1 for v in summary.get('market_verdicts', {}).values() if v == 'WORSE')} worse"
    body = _render_html(summary)

    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": body},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]Weekly retrain email sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Weekly retrain email failed ({resp.status_code})[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Weekly retrain email error: {e}[/yellow]")
