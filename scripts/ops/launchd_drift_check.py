#!/usr/bin/env python3
"""LAUNCHD-DRIFT-SEMANTIC (2026-09-11) — is what launchd RUNS what git SAYS?

Editing `local/launchd/*.plist` does not reload launchd, so the installed copy
in ~/Library/LaunchAgents keeps running. That cost 5.3h of odds outage on
2026-09-10: the repo plist was already correct, and the installed copy was a
stale July one still setting COOLBET_NO_FS=true.

WHY THIS REPLACES THE `diff -q` ONE-LINER
-----------------------------------------
The previous check byte-compared the two files. Run on 2026-09-11 it reported:

    DRIFTED  com.oddsintel.best-price-router-monitor.plist
    DRIFTED  com.oddsintel.unibet-site-odds.plist
    MISSING  com.oddsintel.coolbet-mac-daemon.plist

and all three were false alarms. The two "DRIFTED" files differed ONLY in
indentation (tabs vs two spaces, inline vs multi-line `<key>`) — launchd parses
plists as data and cannot see whitespace. The "MISSING" one was the paper
mac-daemon, deliberately retired 2026-09-10.

So 3 of 7 lines were noise. That is the real danger: a guard that cries wolf
gets skimmed, and the one line that matters — a genuinely stale installed plist
— is the one that gets skimmed past. Precision is the whole point of a guard.

This compares PARSED plists, so formatting is invisible and a real difference
(a changed env var, command, or schedule) is reported with the exact keys.

STATES
  OK          installed matches the repo semantically
  DRIFTED     real semantic difference — the offending keys are listed
  NOT-LOADED  repo plist has no installed copy (fine for a job you have not
              installed on this machine; `local/launchd/retired/` is skipped
              entirely, so a retired component never shows up here)
  BAD-PLIST   one of the two files does not parse

Exit code 1 if anything is DRIFTED or BAD-PLIST, else 0 — so it can page.
Usage:  python3 scripts/ops/launchd_drift_check.py [--verbose]
"""
from __future__ import annotations

import argparse
import pathlib
import plistlib
import sys

REPO_DIR = pathlib.Path(__file__).resolve().parents[2] / "local/launchd"
INSTALLED_DIR = pathlib.Path.home() / "Library/LaunchAgents"

# Keys that legitimately differ or carry no operational meaning.
_IGNORE_KEYS: set[str] = set()


def _load(path: pathlib.Path) -> dict | None:
    """Parse a plist, falling back to `plutil` when Python's parser is stricter
    than Apple's.

    Found 2026-09-11: com.oddsintel.coolbet-ui-placer.plist — the plist of the
    job that places REAL MONEY — had `--cdp-auto-login` inside an XML comment.
    XML forbids `--` there, so expat refused the whole file, while Apple's
    lenient CFPropertyList accepted it and launchd ran it happily for weeks.
    The comment is fixed, but the lesson generalises: the guard must never go
    BLIND on a file just because expat is fussier than launchd, least of all on
    the real-money job. So: try plistlib, then plutil, and only then give up.
    """
    try:
        with path.open("rb") as fh:
            d = plistlib.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 — try Apple's parser before calling it bad
        pass
    try:
        import subprocess
        out = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(path)],
                             capture_output=True, timeout=15)
        if out.returncode == 0 and out.stdout:
            d = plistlib.loads(out.stdout)
            return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 — no plutil (CI/Linux) or genuinely broken
        pass
    return None


def _differences(repo: dict, inst: dict) -> list[str]:
    """Semantic diff of two parsed plists. Returns human-readable key paths."""
    out: list[str] = []

    def walk(a, b, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k in _IGNORE_KEYS:
                    continue
                sub = f"{path}.{k}" if path else k
                if k not in a:
                    out.append(f"{sub}: only INSTALLED has it ({b[k]!r})")
                elif k not in b:
                    out.append(f"{sub}: only REPO has it ({a[k]!r})")
                else:
                    walk(a[k], b[k], sub)
            return
        # Lists of dicts (StartCalendarInterval) compare element-wise; order is
        # meaningful to nobody here, so compare as sorted reprs.
        if isinstance(a, list) and isinstance(b, list):
            if sorted(map(repr, a)) != sorted(map(repr, b)):
                out.append(f"{path}: REPO={a!r} vs INSTALLED={b!r}")
            return
        if a != b:
            out.append(f"{path}: REPO={a!r} vs INSTALLED={b!r}")

    walk(repo, inst, "")
    return out


def check(verbose: bool = False) -> int:
    if not REPO_DIR.is_dir():
        print(f"no repo plist dir at {REPO_DIR}")
        return 0
    # `.template` files carry a scrubbed secret/host and are never installed
    # verbatim; `retired/` is a subdirectory and is not globbed at all.
    repo_plists = sorted(p for p in REPO_DIR.glob("*.plist") if p.is_file())
    bad = 0
    for repo_path in repo_plists:
        name = repo_path.name
        inst_path = INSTALLED_DIR / name
        if not inst_path.exists():
            print(f"NOT-LOADED  {name}  (no copy in ~/Library/LaunchAgents)")
            continue
        repo_d, inst_d = _load(repo_path), _load(inst_path)
        if repo_d is None or inst_d is None:
            which = "repo" if repo_d is None else "installed"
            print(f"BAD-PLIST   {name}  ({which} copy does not parse)")
            bad += 1
            continue
        diffs = _differences(repo_d, inst_d)
        if not diffs:
            print(f"OK          {name}")
            continue
        bad += 1
        print(f"DRIFTED     {name}  ({len(diffs)} semantic difference(s))")
        for d in diffs:
            print(f"              - {d}")
        print(f"              fix: cp local/launchd/{name} ~/Library/LaunchAgents/")
        print(f"                   launchctl unload ~/Library/LaunchAgents/{name}")
        print(f"                   launchctl load   ~/Library/LaunchAgents/{name}")
    retired = sorted((REPO_DIR / "retired").glob("*.plist")) if (REPO_DIR / "retired").is_dir() else []
    if verbose and retired:
        print(f"\n(skipped {len(retired)} retired plist(s): "
              f"{', '.join(p.name for p in retired)})")
    if bad:
        print(f"\n{bad} plist(s) need attention — launchd is NOT running what git says.")
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true",
                    help="also list the retired plists that were skipped")
    sys.exit(check(verbose=ap.parse_args().verbose))
