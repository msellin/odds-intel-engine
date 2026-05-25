"""EMAIL-DELIVERY-CHECK — verify Resend deliverability + DKIM/SPF for ALERT_FROM_EMAIL.

What it checks:
  1. Required env vars present (RESEND_API_KEY, ADMIN_ALERT_EMAIL, ALERT_FROM_EMAIL)
  2. ALERT_FROM_EMAIL domain has SPF record that includes Resend (`include:_spf.resend.com` or equivalent)
  3. DKIM selector `resend._domainkey.<domain>` resolves
  4. Sends a real test email to ADMIN_ALERT_EMAIL via Resend API; reports status code

Run:
  python3 scripts/email_delivery_check.py                # all checks, no send
  python3 scripts/email_delivery_check.py --send         # +real test email
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console

console = Console()


def _check_env() -> tuple[str, str, str] | None:
    api = os.getenv("RESEND_API_KEY", "")
    admin = os.getenv("ADMIN_ALERT_EMAIL", "")
    # Resolve from-address: ALERT_FROM_EMAIL → DIGEST_FROM_EMAIL → fail.
    # On Railway today, DIGEST_FROM_EMAIL is the configured sender used by
    # the daily perf email job; we use the same address for alerts.
    from_email = os.getenv("ALERT_FROM_EMAIL", "") or os.getenv("DIGEST_FROM_EMAIL", "")
    from_source = "ALERT_FROM_EMAIL" if os.getenv("ALERT_FROM_EMAIL") else "DIGEST_FROM_EMAIL"
    missing = [n for n, v in (("RESEND_API_KEY", api), ("ADMIN_ALERT_EMAIL", admin)) if not v]
    if not from_email:
        missing.append("ALERT_FROM_EMAIL or DIGEST_FROM_EMAIL")
    if missing:
        console.print(f"[red]✗ Missing env vars: {missing}[/red]")
        return None
    console.print(f"[green]✓ env vars present[/green]")
    console.print(f"    RESEND_API_KEY     set ({len(api)} chars)")
    console.print(f"    ADMIN_ALERT_EMAIL  {admin}")
    console.print(f"    from-address       {from_email}  (via {from_source})")
    return api, admin, from_email


def _dig(host: str, rtype: str = "TXT") -> str:
    """dig wrapper. Returns concatenated answer text (empty string if no record)."""
    try:
        out = subprocess.run(
            ["dig", "+short", rtype, host],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"<dig error: {e}>"


def _check_spf(domain: str):
    txt = _dig(domain, "TXT")
    has_spf = any("v=spf1" in line for line in txt.split("\n"))
    if not has_spf:
        console.print(f"[red]✗ No SPF record on {domain}[/red]")
        return False
    if "resend.com" in txt or "_spf.resend.com" in txt:
        console.print(f"[green]✓ SPF includes Resend[/green]")
        return True
    console.print(f"[yellow]⚠ SPF found but no Resend include — emails may be marked spam[/yellow]")
    console.print(f"    Got: {txt[:200]}")
    return False


def _check_dkim(domain: str):
    selector = "resend._domainkey." + domain
    txt = _dig(selector, "TXT")
    if not txt:
        console.print(f"[red]✗ No DKIM record at {selector}[/red]")
        return False
    if "p=" in txt or "k=" in txt:
        console.print(f"[green]✓ DKIM record present at {selector}[/green]")
        return True
    console.print(f"[yellow]⚠ DKIM record exists but missing p=/k= keys[/yellow]")
    return False


def _send_test(api: str, from_email: str, admin: str) -> bool:
    import httpx
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    console.print(f"\n[cyan]Sending test email to {admin}...[/cyan]")
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api}", "Content-Type": "application/json"},
            json={
                "from": from_email, "to": [admin],
                "subject": f"OddsIntel email_delivery_check — {ts[:19]}",
                "html": f"<p>Email delivery check at {ts}. Reply not needed; this is a deliverability test.</p>",
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            console.print(f"[green]✓ Resend accepted (id={data.get('id', '?')})[/green]")
            return True
        console.print(f"[red]✗ Resend {resp.status_code}: {resp.text[:300]}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]✗ Resend error: {e}[/red]")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="Actually send a test email")
    args = ap.parse_args()

    console.print("[bold]EMAIL-DELIVERY-CHECK — Resend + SPF/DKIM[/bold]\n")
    creds = _check_env()
    if not creds:
        sys.exit(1)
    api, admin, from_email = creds
    domain = from_email.split("@", 1)[-1] if "@" in from_email else from_email
    console.print(f"\n[bold]DNS checks for {domain}[/bold]")
    _check_spf(domain)
    _check_dkim(domain)
    if args.send:
        _send_test(api, from_email, admin)
    else:
        console.print("\n[yellow]Pass --send to send a real test email[/yellow]")


if __name__ == "__main__":
    main()
