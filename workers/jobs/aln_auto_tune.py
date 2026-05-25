"""ALN-AUTO (2026-05-25) — monthly alignment-class bump tuner.

Wraps scripts/aln1_tune_analysis.py:
  1. Run the analysis on a 60-day window
  2. Compare its recommendation to the current production _ALN_BUMP
  3. If any class has a diff ≥ 0.005 AND n≥100 in the analysis window,
     email the diff via Resend so the user can review + apply manually
  4. Otherwise log "no change" and exit

Never auto-applies the diff — alignment bumps directly affect bet
placement, so a human still approves the change.

Cron: 1st of every month at 03:30 UTC (workers/scheduler.py).
"""
from __future__ import annotations
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from rich.console import Console

console = Console()


def _load_current_bumps() -> dict[str, float]:
    """Read _ALN_BUMP from daily_pipeline_v2.py to compare against."""
    from workers.jobs.daily_pipeline_v2 import _ALN_BUMP  # noqa: F401
    try:
        # Re-import inside the candidate-eval scope by parsing the file once
        # (the constant is defined inside run_morning, not at module level).
        src = (Path(__file__).resolve().parent.parent / "jobs" / "daily_pipeline_v2.py").read_text()
        # Find the line: _ALN_BUMP = {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("_ALN_BUMP = {"):
                # Safe eval — known shape
                d = eval(s.split("=", 1)[1].split("}", 1)[0] + "}")
                return {k: float(v) for k, v in d.items()}
    except Exception:
        pass
    # Fallback to today's known values
    return {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}


def _send_email(subject: str, html: str) -> None:
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")
    FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", "alerts@oddsintel.com")
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print(f"[yellow]aln_auto_tune: no Resend creds — skip email. Subject: {subject}[/yellow]")
        return
    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": html},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]aln_auto_tune email sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Resend {resp.status_code}: {resp.text[:200]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Resend error: {e}[/yellow]")


def run_aln_auto_tune() -> None:
    """Entrypoint — called from scheduler."""
    console.print("[bold]ALN-AUTO — monthly alignment bump tuning[/bold]")
    repo_root = Path(__file__).resolve().parent.parent.parent
    out = subprocess.run(
        [sys.executable, "scripts/aln1_tune_analysis.py", "--days", "60"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=300,
    )
    if out.returncode != 0:
        console.print(f"[red]aln1_tune_analysis failed: {out.stderr[-500:]}[/red]")
        return
    console.print(out.stdout[-1500:])

    # Look for the recommendation file the analysis wrote
    rec_file = repo_root / "dev" / "active" / f"aln1_tune_recommendation_{date.today().strftime('%Y%m%d')}.md"
    if not rec_file.exists():
        console.print(f"[yellow]aln_auto_tune: no recommendation file at {rec_file}[/yellow]")
        return
    rec_text = rec_file.read_text()

    current = _load_current_bumps()
    # Parse "recommended bump" column from the markdown table
    proposed: dict[str, float] = {}
    n_by_class: dict[str, int] = {}
    for line in rec_text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 7:
            continue
        cls = cells[1]
        if cls not in ("NONE", "LOW", "MEDIUM", "HIGH"):
            continue
        try:
            n_by_class[cls] = int(cells[2])
            proposed[cls] = float(cells[6])
        except (ValueError, IndexError):
            continue

    diffs = []
    for cls, new in proposed.items():
        cur = current.get(cls, 0.0)
        if abs(new - cur) >= 0.005 and n_by_class.get(cls, 0) >= 100:
            diffs.append((cls, cur, new, n_by_class[cls]))

    if not diffs:
        console.print("[green]ALN-AUTO: no actionable diff (all changes < 0.005 or n<100). Skipping email.[/green]")
        return

    subject = f"ALN-AUTO: {len(diffs)} alignment bump change(s) recommended"
    rows_html = "".join(
        f"<tr><td>{c}</td><td>{cur:+.4f}</td><td>{new:+.4f}</td><td>{(new - cur):+.4f}</td><td>{n}</td></tr>"
        for c, cur, new, n in diffs
    )
    html = f"""
    <h2>ALN-AUTO — recommended _ALN_BUMP changes</h2>
    <p>Window: last 60 days. Only classes with n ≥ 100 and |diff| ≥ 0.005 shown.</p>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Class</th><th>Current</th><th>Recommended</th><th>Diff</th><th>n</th></tr>
      {rows_html}
    </table>
    <p>Apply: edit <code>workers/jobs/daily_pipeline_v2.py</code> <code>_ALN_BUMP</code>
    constant (around line 2704). Full recommendation doc:
    <code>dev/active/aln1_tune_recommendation_{date.today().strftime('%Y%m%d')}.md</code></p>
    <p>This is a recommendation, not an auto-applied change — alignment bumps
    directly affect bet placement so a human approves each move.</p>
    """
    _send_email(subject, html)


if __name__ == "__main__":
    run_aln_auto_tune()
