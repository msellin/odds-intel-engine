"""#168a VERIFY-QUEUE — background verification of post-deploy checks.

WHY. A task used to end with its agent WAITING: for migrate.yml, for the next :05/:35
betting refresh, for a new bot's first picks. The owner's rule (2026-09-25): "quality and
validation, but we don't want to keep waiting — validation in the background or in bulk".
So a task hands its checks off by committing them, and this job runs them when they are due.

WHERE CHECKS COME FROM
  1. ``ops/verify/<task>.yml`` — one file per task (no shared-file contention):

        task: "#152"
        checks:
          - name: first_picks
            sql: SELECT count(*) FROM simulated_bets WHERE model_version = 'ou_comb_v1'
            expect: ">= 1"            # == != >= <= > <  number | 'text' | true | false | null | not null
            mode: eventually          # now (default): one-shot; alert on mismatch, retry until pass
                                      # eventually: keep retrying, alert only at expiry
                                      # invariant: re-run EVERY cycle until expiry, alert on any
                                      #   break; passes (terminal) at expiry if it held
            run_after:                # all given keys must hold (optional)
              migration: "443"        #   filename prefix present in _schema_migrations
              time: 2026-09-25T15:30:00Z
              job: betting_refresh    #   a pipeline_runs row of this job completed ...
              since: 2026-09-25T15:30:00Z   # ... after this instant
            expires: 2026-09-26T16:00:00Z   # required

     ``cmd:`` (argv list or string, run from the repo root, 60 s timeout) may replace
     ``sql:``; its value is stdout stripped, or the exit code with ``value: exit_code``.

  2. A ``-- verify: <boolean SQL expression>`` line in any migration applied in the last
     7 days — expected true, due as soon as the migration is applied, expires 48 h later.
     migrate.yml runs the same lines straight after apply (CI log only — the repo has no
     Telegram secrets, so this job is what alerts).

HOW THEY RUN. On a dedicated connection opened with ``default_transaction_read_only=on``
and a 20 s ``statement_timeout`` — Postgres itself refuses a write — and only a single
SELECT/WITH statement is accepted. State lives in ``verify_results`` (migration 447): a
passed or expired check is never re-run, an edited check (new spec hash) starts over. The
spec files stay in git; nothing is committed from the VPS checkout (it is a deploy target).

ALERTS go to the OPERATOR chat (``send_telegram``) once per status transition into
failed / error / expired — never for a pass, never repeated for the same state.

Local dry run (no writes, no Telegram):  python3 -m workers.jobs.verify_queue --dry-run
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
VERIFY_DIR = ROOT / "ops" / "verify"
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
STATEMENT_TIMEOUT_MS = 20_000
CMD_TIMEOUT_S = 60
MIGRATION_LOOKBACK = timedelta(days=7)
MIGRATION_EXPIRY = timedelta(hours=48)
TERMINAL = ("passed", "expired")
MODES = ("now", "eventually", "invariant")
ALERT_STATUSES = ("failed", "error", "expired")

_EXPECT_RE = re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")
_MIG_VERIFY_RE = re.compile(r"^--\s*verify:\s*(.+?)\s*$", re.MULTILINE)


# ─── spec parsing ────────────────────────────────────────────────────────────────

def _ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_expect(expect) -> tuple[str, object]:
    """'>= 1' -> ('>=', 1.0); 'true' -> ('==', True); 'not null' -> ('!=', None)."""
    if isinstance(expect, bool):
        return "==", expect
    if isinstance(expect, (int, float)):
        return "==", float(expect)
    s = str(expect).strip()
    low = s.lower()
    if low in ("true", "false"):
        return "==", low == "true"
    if low == "null":
        return "==", None
    if low == "not null":
        return "!=", None
    m = _EXPECT_RE.match(s)
    if not m:
        raise ValueError(f"bad expect {expect!r}")
    op, lit = m.groups()
    if lit.lower() in ("true", "false"):
        return op, lit.lower() == "true"
    if lit.lower() == "null":
        return op, None
    if len(lit) >= 2 and lit[0] == lit[-1] and lit[0] in "'\"":
        return op, lit[1:-1]
    return op, float(lit)


def evaluate(value, expect) -> bool:
    op, want = parse_expect(expect)
    if want is None or isinstance(want, bool):
        if op not in ("==", "!="):
            raise ValueError(f"{op} makes no sense with {want!r}")
        if isinstance(want, bool) and value is not None and not isinstance(value, bool):
            value = str(value).strip().lower() in ("t", "true", "1")
        eq = (value is None) if want is None else (value == want)
        return eq if op == "==" else not eq
    if value is None:
        return False
    if isinstance(want, float):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return False
    else:
        value = str(value)
    return {"==": value == want, "!=": value != want, ">=": value >= want,
            "<=": value <= want, ">": value > want, "<": value < want}[op]


def _check_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("one statement only")
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise ValueError("sql must start with SELECT or WITH")
    return s


def load_yaml_specs(verify_dir: Path = VERIFY_DIR) -> list[dict]:
    import yaml
    out: list[dict] = []
    for path in sorted(glob.glob(str(verify_dir / "*.yml"))):
        rel = os.path.relpath(path, ROOT)
        doc = yaml.safe_load(Path(path).read_text()) or {}
        task = str(doc.get("task") or Path(path).stem)
        for c in doc.get("checks") or []:
            name = c.get("name")
            if not name:
                raise ValueError(f"{rel}: every check needs a name")
            if bool(c.get("sql")) == bool(c.get("cmd")):
                raise ValueError(f"{rel}::{name}: exactly one of sql / cmd")
            if c.get("sql"):
                _check_sql(c["sql"])
            parse_expect(c["expect"])
            mode = c.get("mode", "now")
            if mode not in MODES:
                raise ValueError(f"{rel}::{name}: mode must be one of {MODES}")
            if not c.get("expires"):
                raise ValueError(f"{rel}::{name}: expires is required")
            ra = c.get("run_after") or {}
            if bool(ra.get("job")) != bool(ra.get("since")):
                raise ValueError(f"{rel}::{name}: run_after job and since go together")
            out.append({
                "check_id": f"{rel}::{name}", "task": task, "source": rel, "name": name,
                "sql": c.get("sql"), "cmd": c.get("cmd"), "cmd_value": c.get("value", "stdout"),
                "expect": str(c["expect"]), "mode": mode, "run_after": ra,
                "expires": _ts(c["expires"]), "note": c.get("note"),
            })
    return out


def load_migration_specs(applied: dict[str, datetime],
                         now: datetime, migrations_dir: Path = MIGRATIONS_DIR) -> list[dict]:
    out: list[dict] = []
    for path in sorted(glob.glob(str(migrations_dir / "*.sql"))):
        fn = os.path.basename(path)
        at = applied.get(fn)
        if at is None or now - at > MIGRATION_LOOKBACK:
            continue
        for i, expr in enumerate(_MIG_VERIFY_RE.findall(Path(path).read_text()), 1):
            out.append({
                "check_id": f"supabase/migrations/{fn}::verify{i}", "task": fn.split("_")[0],
                "source": f"supabase/migrations/{fn}", "name": f"verify{i}",
                "sql": f"SELECT ({expr.rstrip(';')})::boolean", "cmd": None,
                "cmd_value": "stdout", "expect": "true", "mode": "now",
                "run_after": {"migration": fn}, "expires": at + MIGRATION_EXPIRY, "note": None,
            })
    return out


def spec_hash(spec: dict) -> str:
    keys = ("sql", "cmd", "cmd_value", "expect", "mode", "run_after", "expires")
    blob = json.dumps({k: spec.get(k) for k in keys}, default=str, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# ─── due / run ───────────────────────────────────────────────────────────────────

def is_due(spec: dict, now: datetime, applied: dict[str, datetime],
           job_done_since) -> tuple[bool, str]:
    ra = spec.get("run_after") or {}
    if ra.get("migration"):
        pre = str(ra["migration"])
        if not any(fn == pre or fn.startswith(pre) for fn in applied):
            return False, f"migration {pre} not applied"
    if ra.get("time") and now < _ts(ra["time"]):
        return False, f"before {ra['time']}"
    if ra.get("job") and not job_done_since(ra["job"], _ts(ra["since"])):
        return False, f"no completed {ra['job']} since {ra['since']}"
    return True, ""


def _ro_connect():
    import psycopg2
    return psycopg2.connect(
        os.environ["DATABASE_URL"], connect_timeout=10,
        options=(f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS} "
                 "-c idle_in_transaction_session_timeout=60000"),
        application_name="verify_queue",
    )


def run_sql(conn, sql: str):
    try:
        with conn.cursor() as cur:
            cur.execute(_check_sql(sql))
            row = cur.fetchone()
            return None if row is None else row[0]
    finally:
        conn.rollback()


def run_cmd(cmd, value_kind: str = "stdout"):
    argv = shlex.split(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=CMD_TIMEOUT_S)
    return p.returncode if value_kind == "exit_code" else p.stdout.strip()


def _load_state() -> dict[str, dict]:
    from workers.api_clients.db import execute_query
    return {r["check_id"]: r for r in execute_query("SELECT * FROM verify_results")}


def _applied_migrations() -> dict[str, datetime]:
    from workers.api_clients.db import execute_query
    return {r["filename"]: r["applied_at"]
            for r in execute_query("SELECT filename, applied_at FROM _schema_migrations")}


def _job_done_since(job: str, since: datetime) -> bool:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT 1 FROM pipeline_runs WHERE job_name = %s AND status = 'completed' "
        "AND completed_at > %s LIMIT 1", (job, since))
    return bool(rows)


def decide(spec: dict, prev: dict | None, now: datetime, due: bool, why: str,
           runner) -> dict:
    """Pure state transition for one check. Returns the new row (without DB fields)."""
    h = spec_hash(spec)
    if prev and prev.get("spec_hash") != h:
        prev = None  # edited check starts over
    row = {
        "status": (prev or {}).get("status", "waiting"),
        "last_value": (prev or {}).get("last_value"), "detail": (prev or {}).get("detail"),
        "runs": (prev or {}).get("runs") or 0, "first_run_at": (prev or {}).get("first_run_at"),
        "last_run_at": (prev or {}).get("last_run_at"), "passed_at": (prev or {}).get("passed_at"),
        "alerted_status": (prev or {}).get("alerted_status"),
        "alerted_at": (prev or {}).get("alerted_at"), "spec_hash": h, "ran": False,
    }
    if row["status"] in TERMINAL:
        return row
    if due:
        row["ran"] = True
        row["runs"] += 1
        row["first_run_at"] = row["first_run_at"] or now
        row["last_run_at"] = now
        try:
            value = runner(spec)
            row["last_value"] = None if value is None else str(value)[:500]
            ok = evaluate(value, spec["expect"])
            row["detail"] = None
            if ok and spec["mode"] != "invariant":
                row["status"], row["passed_at"] = "passed", now
                row["alerted_status"] = None
                return row
            if ok:  # invariant held this cycle — keep watching until expiry
                row["status"], row["alerted_status"] = "holding", None
            else:
                row["status"] = "pending" if spec["mode"] == "eventually" else "failed"
        except Exception as e:  # noqa: BLE001 — one bad check never stops the queue
            row["status"], row["detail"] = "error", f"{type(e).__name__}: {e}"[:500]
    else:
        if row["status"] == "waiting":
            row["detail"] = why
    if spec["expires"] and now >= spec["expires"]:
        if not due and row["runs"] == 0:
            row["detail"] = f"never became due ({why})"
        if row["status"] == "holding":
            row["status"], row["passed_at"] = "passed", now  # held for its whole window
        else:
            row["status"] = "expired"
    return row


def run_verify_queue(dry_run: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    applied = _applied_migrations()
    specs = load_yaml_specs() + load_migration_specs(applied, now)
    try:
        state = _load_state()
    except Exception:  # noqa: BLE001 — only tolerated for a dry run before migration 447
        if not dry_run:
            raise
        state = {}
    conn = None
    counts = {"checks": len(specs), "stored": 0, "passed": 0, "alerts": 0}
    alerts: list[str] = []

    def runner(spec):
        nonlocal conn
        if spec.get("cmd"):
            return run_cmd(spec["cmd"], spec.get("cmd_value", "stdout"))
        if conn is None:
            conn = _ro_connect()
        return run_sql(conn, spec["sql"])

    try:
        for spec in specs:
            prev = state.get(spec["check_id"])
            if prev and prev.get("status") in TERMINAL and prev.get("spec_hash") == spec_hash(spec):
                continue
            due, why = is_due(spec, now, applied, _job_done_since)
            row = decide(spec, prev, now, due, why, runner)
            counts["stored"] += int(row["ran"])
            counts["passed"] += int(row["status"] == "passed" and row["ran"])
            if row["status"] in ALERT_STATUSES and row["alerted_status"] != row["status"]:
                alerts.append(f"• <b>{row['status'].upper()}</b> {spec['check_id']} "
                              f"(task {spec['task']}) — expect {spec['expect']}, got "
                              f"{row['last_value']!s}" + (f" — {row['detail']}" if row['detail'] else ""))
                row["alerted_status"], row["alerted_at"] = row["status"], now
            if dry_run:
                print(f"{row['status']:8} {spec['check_id']}  value={row['last_value']}  {row['detail'] or ''}")
                continue
            _upsert(spec, row, now)
    finally:
        if conn is not None:
            conn.close()

    counts["alerts"] = len(alerts)
    if alerts and not dry_run:
        from workers.notify.telegram import send_telegram
        send_telegram("🔎 <b>Verify queue</b> — " + f"{len(alerts)} check(s) need a look\n"
                      + "\n".join(alerts))
    elif alerts:
        print("WOULD ALERT:\n" + "\n".join(alerts))
    return counts


def _upsert(spec: dict, row: dict, now: datetime) -> None:
    from workers.api_clients.db import execute_write
    execute_write(
        """INSERT INTO verify_results (check_id, task, source, name, spec_hash, mode, expect,
               status, last_value, detail, runs, first_run_at, last_run_at, passed_at,
               expires_at, alerted_status, alerted_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (check_id) DO UPDATE SET task=EXCLUDED.task, source=EXCLUDED.source,
               name=EXCLUDED.name, spec_hash=EXCLUDED.spec_hash, mode=EXCLUDED.mode,
               expect=EXCLUDED.expect, status=EXCLUDED.status, last_value=EXCLUDED.last_value,
               detail=EXCLUDED.detail, runs=EXCLUDED.runs, first_run_at=EXCLUDED.first_run_at,
               last_run_at=EXCLUDED.last_run_at, passed_at=EXCLUDED.passed_at,
               expires_at=EXCLUDED.expires_at, alerted_status=EXCLUDED.alerted_status,
               alerted_at=EXCLUDED.alerted_at, updated_at=EXCLUDED.updated_at""",
        (spec["check_id"], spec["task"], spec["source"], spec["name"], row["spec_hash"],
         spec["mode"], spec["expect"], row["status"], row["last_value"], row["detail"],
         row["runs"], row["first_run_at"], row["last_run_at"], row["passed_at"],
         spec["expires"], row["alerted_status"], row["alerted_at"], now))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="run due checks, print, write nothing")
    ap.add_argument("--validate", action="store_true", help="only parse ops/verify/*.yml")
    a = ap.parse_args()
    if a.validate:
        for s in load_yaml_specs():
            print("ok", s["check_id"])
    else:
        print(run_verify_queue(dry_run=a.dry_run))
