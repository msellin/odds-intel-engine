"""EMAIL-DELIVERY-CHECK (2026-06-01) — verify Resend DKIM/SPF/DMARC DNS records.

We send digest + watchlist + value-bet-alert + weekly-meta-validate emails from
the configured `DIGEST_FROM_EMAIL` address (default `digest@oddsintel.app`). If
the sending domain's DNS records drift out of sync with Resend's expected
values — DKIM key rotated, SPF flattened, DMARC policy missing — deliverability
silently degrades into spam folders.

This script does two checks:

1. **DNS introspection** — looks up SPF (TXT @), DMARC (TXT _dmarc), and the
   Resend DKIM CNAME (resend._domainkey). Flags missing or malformed records.

2. **Resend API verification** — calls `GET /domains` and confirms the sending
   domain's `status: 'verified'` (Resend marks domains as `pending`, `verified`,
   or `failed` based on their own DNS polling).

Run:
    python3 scripts/check_email_deliverability.py
    python3 scripts/check_email_deliverability.py --domain oddsintel.app
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from rich.console import Console

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")


def _from_email_domain() -> str:
    """Extract the domain from DIGEST_FROM_EMAIL env (handles both 'name <a@b.com>'
    and bare 'a@b.com' forms)."""
    raw = os.getenv("DIGEST_FROM_EMAIL", "digest@oddsintel.app")
    m = re.search(r"<([^>]+)>", raw)
    if m:
        raw = m.group(1)
    if "@" in raw:
        return raw.split("@", 1)[1].strip()
    return raw.strip()


def _dig_txt(name: str) -> list[str]:
    """Return TXT records for `name` via `dig +short`. Returns [] on lookup failure."""
    try:
        out = subprocess.run(
            ["dig", "+short", "TXT", name],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return []
        # dig wraps each TXT record in quotes; strip them
        lines = [l.strip().strip('"') for l in out.stdout.splitlines() if l.strip()]
        return lines
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _dig_cname(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["dig", "+short", "CNAME", name],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return out.stdout.strip().splitlines()[0].rstrip(".")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _check_resend_record(rec: dict, domain: str) -> tuple[bool, str]:
    """Check one record from Resend's /domains response against real DNS.

    Resend's record list tells us exactly where it expects each record:
      name='resend._domainkey', type='TXT'   → DKIM TXT at <name>.<domain>
      name='send', type='MX', value=..., priority=10 → MX record on subdomain
      name='send', type='TXT', value='v=spf1 ...'    → SPF TXT on subdomain
    Returns (ok, observed_value).
    """
    fq = f"{rec['name']}.{domain}".rstrip(".")
    rtype = rec.get("type")
    expected = (rec.get("value") or "").strip()

    if rtype == "TXT":
        observed_list = _dig_txt(fq)
        for v in observed_list:
            # Exact match OR (for SPF) presence of the expected include
            if v.strip() == expected:
                return True, v
            if "v=spf1" in expected and "v=spf1" in v and "include:amazonses.com" in v.lower():
                return True, v
            if "p=" in expected and "p=" in v:
                # DKIM public key — check the key body matches (strip whitespace)
                if re.sub(r"\s+", "", expected) == re.sub(r"\s+", "", v):
                    return True, v
        return False, "; ".join(observed_list) or "MISSING"

    if rtype == "MX":
        try:
            out = subprocess.run(["dig", "+short", "MX", fq],
                                 capture_output=True, text=True, timeout=8)
            observed = out.stdout.strip()
            if expected in observed:
                return True, observed
            return False, observed or "MISSING"
        except Exception:
            return False, "DNS lookup failed"

    return False, f"unsupported type {rtype}"


def check_dns(domain: str, resend_records: list[dict] | None) -> dict:
    """Cross-check the sending domain's DNS records against Resend's required
    set (passed in from the /domains API response). Plus a standalone DMARC
    check because Resend doesn't require DMARC but receivers do."""
    results: list[dict] = []
    if resend_records:
        for rec in resend_records:
            ok, observed = _check_resend_record(rec, domain)
            results.append({
                "record":   rec["record"],
                "where":    f"{rec.get('name', '')}.{domain}",
                "type":     rec.get("type"),
                "expected": (rec.get("value") or "")[:80],
                "observed": observed[:120],
                "ok":       ok,
            })

    # DMARC — Resend doesn't manage it but big mailbox providers expect it.
    dmarc_records = [t for t in _dig_txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    dmarc_policy = None
    for r in dmarc_records:
        m = re.search(r"p=(\w+)", r)
        if m:
            dmarc_policy = m.group(1).lower()
            break

    return {
        "resend_check": results,
        "dmarc_records": dmarc_records,
        "dmarc_policy":  dmarc_policy,
    }


def check_resend_api(domain: str) -> dict | None:
    """Query Resend's /domains endpoint. Returns the full domain detail
    including the `records` list (DKIM + SPF/MX). Returns None when no API
    key is configured."""
    if not RESEND_API_KEY:
        return None
    try:
        import httpx
        list_resp = httpx.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=10,
        )
        if list_resp.status_code != 200:
            return {"_error": f"HTTP {list_resp.status_code}: {list_resp.text[:200]}"}
        match = None
        for d in list_resp.json().get("data", []):
            if d.get("name", "").lower() == domain.lower():
                match = d
                break
        if not match:
            return {"_error": f"Domain {domain} not found in Resend account"}
        detail_resp = httpx.get(
            f"https://api.resend.com/domains/{match['id']}",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=10,
        )
        return detail_resp.json() if detail_resp.status_code == 200 else match
    except Exception as e:
        return {"_error": f"API call failed: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None,
                    help="Override sending domain (default: parsed from DIGEST_FROM_EMAIL)")
    args = ap.parse_args()

    domain = args.domain or _from_email_domain()
    console.print(f"\n[bold]EMAIL-DELIVERY-CHECK — domain: {domain}[/bold]\n")

    # Query Resend first so we know which records to check
    api = check_resend_api(domain)
    resend_records = None
    if api and "_error" not in api:
        resend_records = api.get("records") or []

    dns = check_dns(domain, resend_records)

    console.print("[bold]Resend API[/bold]")
    if api is None:
        console.print("  [yellow]skipped — RESEND_API_KEY not set[/yellow]")
    elif "_error" in api:
        console.print(f"  [red]check failed: {api['_error']}[/red]")
    else:
        status = api.get("status", "unknown")
        g = "[green]✓[/green]" if status == "verified" else "[yellow]⚠[/yellow]"
        console.print(f"  {g} status={status}  region={api.get('region', '?')}")

    console.print()
    console.print("[bold]Resend-required records cross-checked against DNS[/bold]")
    for r in dns["resend_check"]:
        g = "[green]✓[/green]" if r["ok"] else "[red]✗[/red]"
        console.print(f"  {g} {r['record']:<6} {r['type']:<4} at {r['where']}")
        if not r["ok"]:
            console.print(f"      expected: {r['expected']}")
            console.print(f"      observed: {r['observed']}")

    console.print()
    console.print("[bold]DMARC (recommended by major mailbox providers, not required by Resend)[/bold]")
    dg = "[green]✓[/green]" if dns["dmarc_policy"] in ("quarantine", "reject") else "[yellow]⚠[/yellow]" if dns["dmarc_policy"] == "none" else "[red]✗[/red]"
    console.print(f"  {dg} _dmarc.{domain} policy={dns['dmarc_policy'] or 'MISSING'}")
    if not dns["dmarc_records"]:
        console.print(f"      [yellow]Recommended action:[/yellow] add TXT _dmarc.{domain} = "
                      f'"v=DMARC1; p=none; rua=mailto:postmaster@{domain}; pct=100"')
        console.print(f"      Start with p=none for monitoring, raise to p=quarantine after 14d of clean reports.")

    console.print()
    resend_ok = all(r["ok"] for r in dns["resend_check"]) if dns["resend_check"] else False
    api_ok = api is None or api.get("status") == "verified"
    if resend_ok and api_ok:
        if dns["dmarc_records"]:
            console.print("[bold green]✓ All checks pass — digests should land in inbox[/bold green]")
        else:
            console.print("[bold yellow]⚠ Resend records OK but DMARC missing — works today, add DMARC before scale[/bold yellow]")
    else:
        console.print("[bold red]✗ One or more checks failed — review records above and the Resend dashboard[/bold red]")


if __name__ == "__main__":
    main()
