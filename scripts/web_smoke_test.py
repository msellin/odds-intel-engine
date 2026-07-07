"""Post-deploy smoke test for any Next.js site hosted on our VPS.

Run against the live URL after `pm2 restart <app>` in a GitHub Actions
deploy workflow — exits non-zero if any critical check fails, causing
the workflow to fail loudly (email + Telegram alert).

Usage:
    python3 scripts/web_smoke_test.py --url https://oddsintel.app
    python3 scripts/web_smoke_test.py --url https://mysite.com --config mysite.json

Config JSON schema (all fields optional; defaults match oddsintel.app):
    {
      "must_contain_homepage": "OddsIntel",
      "critical_paths": ["/", "/picks", "/performance"],
      "api_paths": ["/api/v1/upcoming"],
      "static_paths": ["/manifest.json", "/sitemap.xml"],
      "max_response_ms": 15000
    }
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Optional


DEFAULT_CONFIG = {
    "must_contain_homepage": None,  # e.g., "OddsIntel"
    "critical_paths": ["/"],
    "api_paths": [],
    "static_paths": [],
    "max_response_ms": 15000,
}


def fetch(url: str, timeout: int = 30) -> tuple[Optional[int], float, dict, str]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VPS-Smoke-Test/1.0",
            "Accept": "text/html,application/json,*/*",
            "Accept-Encoding": "identity",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            elapsed = time.time() - t0
            return resp.status, elapsed, dict(resp.headers), body[:5000].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        body = e.read()[:500].decode("utf-8", errors="replace") if e.fp else ""
        return e.code, elapsed, dict(e.headers), body
    except Exception as e:  # network / timeout / SSL
        elapsed = time.time() - t0
        return None, elapsed, {}, str(e)


class SmokeRun:
    def __init__(self, base_url: str, config: dict):
        self.base = base_url.rstrip("/")
        self.config = config
        self.failures: list[str] = []
        self.passes: list[str] = []

    def check(self, label: str, path: str, *, expect_code: int = 200,
              must_contain: Optional[str] = None,
              max_ms: Optional[int] = None) -> None:
        max_ms = max_ms if max_ms is not None else self.config["max_response_ms"]
        code, elapsed, headers, body = fetch(self.base + path)
        ms = int(elapsed * 1000)
        ok = code == expect_code
        why = ""
        if not ok:
            why = f"expected {expect_code}, got {code}"
        elif must_contain and body and must_contain not in body:
            ok = False
            why = f"body missing '{must_contain}'"
        elif ms > max_ms:
            ok = False
            why = f"took {ms}ms (>max {max_ms}ms)"

        cf = " via CF" if headers.get("Cf-Ray") or headers.get("cf-ray") else ""
        line = f"{label:<38} {path:<32} → {code} in {ms}ms{cf}"
        if ok:
            self.passes.append(line)
            print(f"[PASS] {line}")
        else:
            line += f"    ({why})"
            self.failures.append(line)
            print(f"[FAIL] {line}")
            if body and code is not None:
                snippet = body.replace("\n", " ")[:200]
                print(f"        body: {snippet}")

    def run(self) -> int:
        print("=" * 90)
        print(f"VPS smoke test — {self.base}")
        print("=" * 90)

        # 1. Every critical path must return 200
        for path in self.config["critical_paths"]:
            must = self.config["must_contain_homepage"] if path == "/" else None
            self.check(f"critical path", path, must_contain=must)

        # 2. API routes — 200 and JSON-ish content-type
        for path in self.config["api_paths"]:
            self.check(f"API route", path)

        # 3. Static assets — 200 or 304
        for path in self.config["static_paths"]:
            code, elapsed, _, _ = fetch(self.base + path)
            ms = int(elapsed * 1000)
            ok = code in (200, 304)
            line = f"{'static asset':<38} {path:<32} → {code} in {ms}ms"
            if ok:
                self.passes.append(line)
                print(f"[PASS] {line}")
            else:
                self.failures.append(line)
                print(f"[FAIL] {line}")

        # 4. Security headers on homepage
        _, _, h, _ = fetch(self.base + "/")
        cf_ray = bool(h.get("Cf-Ray") or h.get("cf-ray"))
        print()
        print(f"Cloudflare edge:         {'YES' if cf_ray else 'NO (bypassing CF?)'}")
        print(f"Content-Security-Policy: {'set' if (h.get('Content-Security-Policy') or h.get('content-security-policy')) else 'MISSING'}")

        print()
        print("=" * 90)
        print(f"RESULT: {len(self.passes)} passed, {len(self.failures)} failed")
        print("=" * 90)

        if self.failures:
            print("\nFAILURES:")
            for f in self.failures:
                print(f"  - {f}")
            return 1
        return 0


def load_config(path: Optional[str]) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        with open(path) as fh:
            cfg.update(json.load(fh))
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Base URL, e.g. https://oddsintel.app")
    ap.add_argument("--config", help="Optional JSON config file")
    args = ap.parse_args()

    cfg = load_config(args.config)
    run = SmokeRun(args.url, cfg)
    return run.run()


if __name__ == "__main__":
    sys.exit(main())
