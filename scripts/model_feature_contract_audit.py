#!/usr/bin/env python3
"""MODEL-FEATURE-CONTRACT-AUDIT — does the model actually RECEIVE what it was trained on?

WHY THIS EXISTS (2026-09-16). `pinnacle_implied_home` / `_draw` / `_away` are in
the model's `feature_cols.pkl` but have no column in `match_feature_vectors` —
`daily_pipeline_v2` writes them to `match_signals`. `_build_row_from_mfv()` does
`SELECT * FROM match_feature_vectors` and zero-fills anything it cannot find, so
**every live prediction since the v10 schema has fed the model 0.0 for the three
features carrying the market's own 1x2 price** — and set their `_missing`
indicators to 1 on every match, permanently, even where the data exists.

Nothing failed. No exception, no log line, no alert. The model simply got zeros.
The owner's question after finding it was the right one: *how do we know that was
the only one?* This script answers that, and is meant to be re-run before any new
feature is added.

WHAT IT CHECKS, per bundle, over a sample of real matches, through the ACTUAL
production code path (`_build_row_from_mfv`) rather than a reimplementation:

  1. DROPPED   — for the SAME row, a source holds a value and the inference path
                 returned 0.0 anyway. This is the only true wiring bug, and it is
                 decided per row, never by aggregate.
  2. STUCK     — a `_missing` indicator that never varies. The model learned to
                 split on it; a constant tells it nothing and, when stuck at 1,
                 actively asserts "absent" about data we may hold.
  3. CONSTANT  — a real feature whose value never changes across matches.
  4. NO SOURCE — in `feature_cols` but present in neither `match_feature_vectors`
                 nor `match_signals`. Unfixable without a pipeline change.
  5. RECOVERABLE — zero-filled by the inference path, but the value IS available
                 in `match_signals`. This is the pinnacle bug's exact shape and
                 the most important row in the report.
  6. UNPOPULATED — the inference path returned 0.0 and the source is NULL for
                 those same rows. The wiring is fine; the DATA is missing. A
                 different problem with a different fix, so it is reported
                 separately and never counted as a bug.

⚠️ TWO WRONG CLASSIFIERS PRECEDED THIS ONE, both because "zero on every sampled
match" is not evidence of anything on its own:

  v1 called `line_velocity`, `league_clv_efficiency` and both `xg_overperf_*`
  DEAD. The sample is the most RECENT matches, which is exactly where a
  backfilled-later column is legitimately empty.

  v2 cross-checked against each column's non-zero count over the window — 8,060 /
  14,576 / ~1,600 rows — and still called them DEAD, because the aggregate says
  the data exists SOMEWHERE, not that it existed for THESE rows. It did not: all
  four are NULL on all 200 sampled matches.

So the verdict is now per row: a feature is DROPPED only when the source has a
value for a row and inference returned 0.0 for that same row. Everything else is
a coverage question, which matters but is not this script's subject.

Exit code 1 if any RECOVERABLE or DEAD feature is found, so it can gate CI.

Read-only.

    python3 scripts/model_feature_contract_audit.py [--bundle ...] [--sample 300]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib  # noqa: E402

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.xgboost_ensemble import _build_row_from_mfv  # noqa: E402

DEFAULT_BUNDLE = "data/models/soccer/v20260914_clean_cut0820"


def mfv_columns() -> set[str]:
    return {r["column_name"] for r in execute_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='match_feature_vectors'") or []}


def signal_names() -> set[str]:
    """Distinct signal names seen recently — the other place a feature can live."""
    return {r["signal_name"] for r in execute_query(
        "SELECT DISTINCT signal_name FROM match_signals "
        "WHERE captured_at > now() - interval '90 days'") or []}


def nonzero_counts(cols: list[str], have_mfv: set[str]) -> dict[str, int]:
    """Non-zero, non-null count per mfv column over the audit window. This is the
    cross-check that separates a BROKEN feature from a merely ABSENT one — see
    the sample warning in the module docstring."""
    real = [c for c in cols if c in have_mfv]
    if not real:
        return {}
    sel = ", ".join(f'count(*) FILTER (WHERE mfv."{c}" IS NOT NULL AND mfv."{c}" <> 0) AS c{i}'
                    for i, c in enumerate(real))
    r = execute_query(f"""SELECT {sel} FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
         WHERE m.status='finished' AND m.date > now() - interval '120 days'""")
    return {c: int(r[0][f"c{i}"]) for i, c in enumerate(real)} if r else {}


def sample_matches(n: int) -> list[str]:
    return [str(r["match_id"]) for r in execute_query(
        """SELECT mfv.match_id FROM match_feature_vectors mfv
             JOIN matches m ON m.id = mfv.match_id
            WHERE m.status='finished' AND m.date > now() - interval '120 days'
            ORDER BY m.date DESC LIMIT %s""", (n,)) or []]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE)
    ap.add_argument("--sample", type=int, default=300)
    a = ap.parse_args()

    cols = joblib.load(f"{a.bundle}/feature_cols.pkl")
    have_mfv, have_sig = mfv_columns(), signal_names()
    ids = sample_matches(a.sample)
    if not ids:
        print("no sampled matches — cannot audit")
        return 1

    # RAW SOURCE for the same rows: the mfv row itself, plus the signals that
    # live outside it. Compared per row against what inference produced.
    raw_by_id = {str(r["match_id"]): r for r in (execute_query(
        "SELECT * FROM match_feature_vectors WHERE match_id::text = ANY(%s)", (ids,)) or [])}
    sig_by_id: dict[str, dict] = defaultdict(dict)
    for r in (execute_query(
            """SELECT DISTINCT ON (match_id, signal_name) match_id, signal_name, signal_value
                 FROM match_signals WHERE match_id::text = ANY(%s)
                ORDER BY match_id, signal_name, captured_at DESC""", (ids,)) or []):
        sig_by_id[str(r["match_id"])][r["signal_name"]] = r["signal_value"]

    vals: dict[str, list] = defaultdict(list)
    dropped_rows: dict[str, int] = defaultdict(int)
    unpop_rows: dict[str, int] = defaultdict(int)
    built = 0
    for mid in ids:
        row = _build_row_from_mfv(mid, cols, tier=1)
        if row is None:
            continue
        built += 1
        raw, sigs = raw_by_id.get(mid, {}), sig_by_id.get(mid, {})
        for c in cols:
            got = row.get(c)
            vals[c].append(got)
            if c.endswith("_missing") or c == "tier":
                continue
            src = raw.get(c)
            if src is None:
                src = sigs.get(c)
            if src is None:
                unpop_rows[c] += 1
            elif got in (0.0, 0) and float(src) != 0.0:
                dropped_rows[c] += 1      # source HAD a value; inference sent 0.0

    print(f"\n=== MODEL FEATURE CONTRACT — {a.bundle} ===")
    print(f"{len(cols)} declared features · {built}/{len(ids)} sampled matches built a row\n")

    nz = nonzero_counts([c for c in cols if not c.endswith("_missing")], have_mfv)
    recoverable, dead, stuck, constant, nosource, sparse = [], [], [], [], [], []
    for c in cols:
        v = [x for x in vals[c] if x is not None]
        if not v:
            continue
        uniq = set(v)
        is_missing_flag = c.endswith("_missing")
        base = c[:-len("_missing")] if is_missing_flag else c

        if is_missing_flag:
            if len(uniq) == 1:
                stuck.append((c, next(iter(uniq)), base in have_mfv, base in have_sig))
            continue

        if uniq == {0.0}:
            # Zero on every sampled match. That alone does NOT mean broken — the
            # sample is the most recent matches, where a backfilled column is
            # legitimately empty. Cross-check against the column's own non-zero
            # count before calling it a bug.
            if c not in have_mfv and c in have_sig and dropped_rows.get(c, 0) > 0:
                recoverable.append((c, dropped_rows[c]))   # the pinnacle bug's exact shape
            elif c not in have_mfv and c in have_sig:
                sparse.append(c)               # signal exists in general, not for these rows
            elif c not in have_mfv and c not in have_sig:
                nosource.append(c)
            elif dropped_rows.get(c, 0) > 0:
                dead.append((c, dropped_rows[c]))   # per-row proof, not an aggregate
            else:
                sparse.append(c)                    # source NULL for these same rows
        elif len(uniq) == 1:
            constant.append((c, next(iter(uniq))))

    def show(title, items, fmt=lambda x: f"    {x}"):
        print(f"  {title} ({len(items)})")
        for it in items:
            print(fmt(it))
        if not items:
            print("    none")
        print()

    show("🔴 RECOVERABLE — zero-filled by inference, but the value EXISTS in match_signals",
         recoverable,
         lambda t: f"    {t[0]}   ({t[1]} of {built} rows had a signal value that was thrown away)"
                   f"\n        -> join it in _build_row_from_mfv")
    show("🔴 DROPPED — the SOURCE had a value for that row and inference sent 0.0 anyway", dead,
         lambda t: f"    {t[0]}   ({t[1]} of {built} sampled rows)")
    show("⚪ UNPOPULATED — inference sent 0.0 and the source is NULL for those same rows "
         "(a DATA gap, not a wiring bug)", sparse,
         lambda c: f"    {c}   (source NULL on {unpop_rows.get(c, 0)}/{built} rows)")
    show("🟠 NO SOURCE — declared by the model, present in neither mfv nor match_signals", nosource)
    show("🟠 STUCK missing-indicator — never varies, so it carries no information",
         stuck,
         lambda t: (f"    {t[0]} = {t[1]} always"
                    f"   (base in mfv: {t[2]}, base in signals: {t[3]})"
                    + ("   <- asserts ABSENT on every match" if t[1] == 1 else "")))
    show("🟡 CONSTANT real feature — same value on every match", constant,
         lambda t: f"    {t[0]} = {t[1]}")

    bad = len(recoverable) + len(dead)
    print(f"VERDICT: {bad} feature(s) the model declares but cannot read.")
    if recoverable:
        print(f"  {len(recoverable)} of them are RECOVERABLE TODAY — the data is already collected.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
