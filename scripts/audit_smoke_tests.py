#!/usr/bin/env python3
"""SMOKE-SUITE-AUDIT — classify every test in scripts/smoke_test.py by whether
the thing it guards still exists.

Why this exists: the suite is 762 tests in one 27K-line file and has not passed
in 100 CI runs. Failures are only half the problem — a test that asserts a
*source string* keeps passing forever after the feature is deleted, so dead
tests inflate the pass count and make the gate look healthier than it is.

Evidence, not name-matching: for each test we pull every referenced module,
file path, DB table and migration number out of its body, then check whether
each still exists on disk / in the schema. A test is only called dead when its
*code* references are gone, never because its name mentions a dead product.

Usage:
    python3 scripts/audit_smoke_tests.py                 # full report
    python3 scripts/audit_smoke_tests.py --status-json X  # join CI results
    python3 scripts/audit_smoke_tests.py --csv out.csv    # machine-readable
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "scripts" / "smoke_test.py"

# ---------------------------------------------------------------- parsing

def parse_tests(src: str) -> list[dict]:
    """Split the suite into per-test blocks using the AST, so a decorator
    string containing '@test(' or a stray quote cannot desync a regex scan."""
    tree = ast.parse(src)
    lines = src.splitlines()
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "test":
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    name = dec.args[0].value
        if name is None:
            continue
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        end = node.end_lineno or start
        out.append({
            "name": name,
            "func": node.name,
            "line": start,
            "body": "\n".join(lines[start - 1:end]),
            "nlines": end - start + 1,
        })
    return out


# ------------------------------------------------------------- references

# `from workers.jobs import settlement` must resolve workers.jobs.settlement,
# not just the package — the package always exists and would hide a deleted
# module. Capture the imported names too.
MOD_RE   = re.compile(r'from\s+(workers[.\w]*)\s+import\s+([\w,\s]+)|import\s+(workers[.\w]+)')
# Paths must be quoted string LITERALS. The earlier pattern let a match run
# from inside a docstring to the docstring's own closing quote, inventing
# paths like "workers/automation/coolbet_session.py) reads six". Exclude
# quotes, whitespace and closing brackets, and require a file extension so
# prose fragments cannot qualify.
PATH_RE  = re.compile(r'["\']((?:workers|scripts|supabase|data|ledger)/[^"\'\s)（]+?\.\w{1,4})["\']')
OPEN_RE  = re.compile(r'open\(\s*["\']([^"\'\s)]+\.\w{1,4})["\']')
TABLE_RE = re.compile(r'\b(?:from|join|into|update|table_name\s*=\s*)\s+["\']?([a-z_][a-z0-9_]{3,})\b', re.I)
MIG_RE   = re.compile(r'migration\s+(\d{2,3})', re.I)


def references(body: str) -> dict:
    mods: set[str] = set()
    for m in MOD_RE.finditer(body):
        pkg, names, plain = m.group(1), m.group(2), m.group(3)
        if plain:
            # `import workers.utils.kuma.push` where push is a FUNCTION would
            # be reported as a missing module. Fall back to the parent package
            # before calling anything dead.
            if module_exists(plain):
                mods.add(plain)
            elif "." in plain and module_exists(plain.rsplit(".", 1)[0]):
                mods.add(plain.rsplit(".", 1)[0])
            else:
                mods.add(plain)
            continue
        if not pkg:
            continue
        mods.add(pkg)
        # `from workers.jobs import settlement, email_digest` -> resolve each
        # submodule. Anything that is not a module on disk (a function or a
        # constant) simply will not match a file and is dropped below.
        for nm in re.split(r'[,\s]+', (names or "").strip()):
            if nm and nm.isidentifier():
                cand = f"{pkg}.{nm}"
                if module_exists(cand):
                    mods.add(cand)
    paths = set(PATH_RE.findall(body)) | set(OPEN_RE.findall(body))
    # A path the test asserts is ABSENT is not a dead reference — its absence
    # is the assertion. RLS-MISSING-TABLES checks the old duplicate migration
    # was removed, so "missing" is the pass condition.
    absent = set()
    for p in paths:
        esc = re.escape(p)
        if re.search(rf'assert\s+not\s+[^\n]*{esc}', body) or \
           re.search(rf'{esc}[^\n]*\n[^\n]*assert\s+not\b', body) or \
           re.search(rf'assert\s+not\s+\w+\.exists', body) and p in body:
            absent.add(p)
    paths -= absent
    migs = set(MIG_RE.findall(body))
    return {"modules": sorted(mods), "paths": sorted(paths),
            "migrations": sorted(migs), "asserted_absent": sorted(absent)}


def module_exists(dotted: str) -> bool:
    """True if the dotted name maps to a module file OR a package directory.
    workers/jobs and workers/api_clients are namespace packages with no
    __init__.py, so requiring one reports every live module as missing."""
    rel = dotted.replace(".", "/")
    return (ROOT / f"{rel}.py").exists() or (ROOT / rel).is_dir()


def path_exists(p: str) -> bool:
    if "*" in p:
        try:
            return any(ROOT.glob(p))
        except Exception:
            return True          # unresolvable glob: do not call it dead
    return (ROOT / p).exists()


def migration_exists(num: str) -> bool:
    mig_dir = ROOT / "supabase" / "migrations"
    if not mig_dir.is_dir():
        return True              # cannot verify -> never call it dead
    n = num.zfill(3)
    return any(f.name.startswith(n) for f in mig_dir.iterdir())


# ------------------------------------------------------------ classifying

# A test whose only assertions grep a source file for a literal keeps passing
# after the feature is deleted. Flag those separately -- they are the ones
# that quietly inflate the pass count.
SOURCE_READ_RE = re.compile(r'\b(?:read_text|getsource|open\s*\()')


def _source_only(body: str) -> bool:
    """True when the test reads a source file and EVERY assertion is a
    substring check against that text — i.e. it verifies that some code is
    still *written*, never that it *works*. These keep passing after the
    feature is deleted, so they are the ones inflating the pass count.

    A test that also calls the thing under test (an assert containing a call)
    is exercising behaviour and does not count, even if it greps too.
    """
    if not SOURCE_READ_RE.search(body):
        return False
    asserts = re.findall(r'^\s*assert\s+(.+)$', body, re.M)
    if not asserts:
        return False
    for a in asserts:
        # An assertion that invokes something — foo(...) — is behavioural.
        # `in src` / `not in src` / string comparisons are not.
        stripped = re.sub(r'\b(?:len|str|int|float|set|sorted|any|all)\s*\(', '', a)
        if re.search(r'\w\s*\(', stripped):
            return False
    return True


# Ranking WEAKEN by line number is useless — 194 of them is a backlog, not a
# task list. What matters is blast radius: a source-grep guarding real-money
# placement is a different problem from one guarding a doc string. Weight by
# what the test's own name says it protects.
STAKES = [
    (5, re.compile(r'real.?money|real_bets|kill.?switch|execute|place(ment)?|stake|'
                   r'bankroll|payout|pause', re.I)),
    (4, re.compile(r'settle|void|clv|slippage|edge|threshold|veto|guard|cap|gate', re.I)),
    (3, re.compile(r'calibrat|model|predict|ensemble|blend|elo|prob', re.I)),
    (2, re.compile(r'schedul|cron|job|pipeline|migration|rls|auth|tier', re.I)),
]


def stakes_of(name: str) -> int:
    for score, pat in STAKES:
        if pat.search(name):
            return score
    return 1


def classify(t: dict, status: str | None) -> tuple[str, str]:
    refs = t["refs"]
    dead_mods  = [m for m in refs["modules"] if not module_exists(m)]
    dead_paths = [p for p in refs["paths"] if not path_exists(p)]
    dead_migs  = [m for m in refs["migrations"] if not migration_exists(m)]

    if dead_mods or dead_paths:
        why = []
        if dead_mods:  why.append("missing module(s): " + ", ".join(dead_mods))
        if dead_paths: why.append("missing path(s): " + ", ".join(dead_paths))
        return "DELETE", "; ".join(why)

    if dead_migs:
        return "REVIEW", f"references missing migration(s): {', '.join(dead_migs)}"

    if status == "fail":
        return "FIX", "failing in CI"
    if status == "skip":
        return "REVIEW", "skipped in CI - may be hiding a failure"

    if _source_only(t["body"]):
        return "WEAKEN", "asserts source strings only - would pass after deletion"

    return "KEEP", "references resolve; passing"


# ----------------------------------------------------------------- report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status-json", help='JSON map {"test name": "pass|fail|skip"}')
    ap.add_argument("--csv", help="write per-test rows here")
    ap.add_argument("--verdict", help="only show this verdict")
    ap.add_argument("--stakes", action="store_true",
                    help="rank WEAKEN by blast radius instead of line order")
    args = ap.parse_args()

    src = SUITE.read_text(encoding="utf-8")
    tests = parse_tests(src)
    status = {}
    if args.status_json:
        status = json.loads(Path(args.status_json).read_text())

    for t in tests:
        t["refs"] = references(t["body"])
        t["verdict"], t["why"] = classify(t, status.get(t["name"]))
        t["stakes"] = stakes_of(t["name"])

    order = ["DELETE", "FIX", "REVIEW", "WEAKEN", "KEEP"]
    counts = {v: 0 for v in order}
    for t in tests:
        counts[t["verdict"]] += 1

    print(f"suite: {len(tests)} tests, {len(src.splitlines()):,} lines")
    if status:
        print(f"CI status joined for {sum(1 for t in tests if t['name'] in status)} tests")
    print()
    for v in order:
        print(f"  {v:8} {counts[v]:>4}")
    print()

    if args.stakes:
        weak = sorted((t for t in tests if t["verdict"] == "WEAKEN"),
                      key=lambda t: (-t["stakes"], t["line"]))
        LABEL = {5: "5 REAL MONEY", 4: "4 settlement/edge", 3: "3 model",
                 2: "2 infra", 1: "1 other"}
        from collections import Counter
        dist = Counter(t["stakes"] for t in weak)
        print("WEAKEN by blast radius:")
        for k in sorted(dist, reverse=True):
            print(f"  {LABEL[k]:20} {dist[k]:>4}")
        print()
        cur = None
        for t in weak:
            if t["stakes"] != cur:
                cur = t["stakes"]
                print(f"--- {LABEL[cur]} " + "-" * 40)
            print(f"  L{t['line']:<6} {t['name'][:76]}")
        return

    for v in order:
        if args.verdict and v != args.verdict:
            continue
        rows = [t for t in tests if t["verdict"] == v]
        if not rows or v == "KEEP":
            continue
        print(f"--- {v} ({len(rows)}) " + "-" * 40)
        for t in rows:
            print(f"  L{t['line']:<6} {t['name'][:74]}")
            print(f"          {t['why'][:110]}")
        print()

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["verdict", "stakes", "line", "nlines", "name", "why",
                        "modules", "paths", "migrations", "ci_status"])
            for t in sorted(tests, key=lambda x: (order.index(x["verdict"]), x["line"])):
                w.writerow([t["verdict"], t["stakes"], t["line"], t["nlines"], t["name"], t["why"],
                            "|".join(t["refs"]["modules"]), "|".join(t["refs"]["paths"]),
                            "|".join(t["refs"]["migrations"]), status.get(t["name"], "")])
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
