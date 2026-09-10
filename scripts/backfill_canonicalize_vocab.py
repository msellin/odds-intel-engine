"""MARKET-VOCAB-CANONICAL Phase 2 step 4 — backfill the bet tables to one spelling.

Rewrites legacy (market, selection) to canonical in simulated_bets / shadow_bets /
real_bets, using the SAME tested helper the writers use (canonical_market.
canonicalize_for_storage) — no SQL/Python divergence. odds_snapshots is untouched
(already canonical). AH/combo selections are preserved (line lives there).

Dedup-aware: simulated_bets and shadow_bets have a UNIQUE(..., market, selection)
constraint, so a legacy row that canonicalises onto a key another row already
occupies must be DELETED (keep one), not UPDATE-collided. real_bets has no such
constraint → plain update.

Idempotent: re-running does nothing once canonical. Read-only unless --execute.

  python3 scripts/backfill_canonicalize_vocab.py            # dry-run (report only)
  python3 scripts/backfill_canonicalize_vocab.py --execute  # apply
"""
from __future__ import annotations

import argparse
import os

# unique-key prefix columns (besides market, selection) per table; [] = no unique key
_UNIQ_PREFIX = {
    "simulated_bets": ["bot_id", "match_id"],
    "shadow_bets": ["shadow_cohort", "bot_id", "match_id"],
    "real_bets": [],
}


def _load_env():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


import re as _re


def _implausible(nm):
    """True if the canonical market is a nonsense O/U line (a source typo like
    'over 25' -> over_under_250). Real goal O/U lines are <= 10.5. We must NOT
    rewrite a typo into an even weirder canonical value — skip + report it instead."""
    m = _re.match(r"^over_under_(\d{2,3})$", nm or "")
    return bool(m) and int(m.group(1)) / 10 > 10.5


def _mappings(execute_query, tbl):
    """Distinct (old_market, old_sel) -> (new_market, new_sel) where canonicalisation
    actually changes the pair. NULL selection is left alone. Returns (changes, skipped)
    where skipped are implausible mappings (source typos) left untouched."""
    from workers.canonical_market import canonicalize_for_storage
    rows = execute_query(f"SELECT DISTINCT market, selection FROM {tbl}")
    out, skipped = [], []
    for r in rows:
        m, s = r["market"], r["selection"]
        if m is None or s is None:
            continue
        nm, ns = canonicalize_for_storage(m, s)
        if (nm, ns) == (m, s):
            continue
        (skipped if _implausible(nm) else out).append((m, s, nm, ns))
    return out, skipped


def run(execute: bool):
    from workers.api_clients.db import execute_query, execute_write
    grand = {"tables": {}}
    for tbl, prefix in _UNIQ_PREFIX.items():
        maps, skipped = _mappings(execute_query, tbl)
        join = " AND ".join([f"a.{c}=b.{c}" for c in prefix])
        n_update = n_collide = 0
        details = []
        for (om, os_, nm, ns) in maps:
            # how many rows carry the legacy pair
            cnt = execute_query(
                f"SELECT count(*) n FROM {tbl} WHERE market=%s AND selection=%s", (om, os_))[0]["n"]
            coll = 0
            if prefix:  # collision = a canonical twin already exists at the same uniq-prefix
                coll = execute_query(
                    f"""SELECT count(*) n FROM {tbl} a
                         WHERE a.market=%s AND a.selection=%s
                           AND EXISTS (SELECT 1 FROM {tbl} b
                                        WHERE b.market=%s AND b.selection=%s
                                          AND b.id<>a.id AND {join})""",
                    (om, os_, nm, ns))[0]["n"]
            n_update += cnt - coll
            n_collide += coll
            details.append((om, os_, nm, ns, cnt, coll))
            if execute:
                if prefix and coll:
                    # delete the legacy dup where a canonical twin already exists
                    execute_write(
                        f"""DELETE FROM {tbl} a USING {tbl} b
                             WHERE a.market=%s AND a.selection=%s
                               AND b.market=%s AND b.selection=%s
                               AND b.id<>a.id AND {join}""",
                        (om, os_, nm, ns))
                # also collapse legacy-vs-legacy rows that map to the SAME canonical key
                # at the same prefix: keep the max(id), delete the rest, before the UPDATE.
                if prefix:
                    execute_write(
                        f"""DELETE FROM {tbl} a USING {tbl} b
                             WHERE a.market=%s AND a.selection=%s
                               AND b.market=%s AND b.selection=%s
                               AND a.id<b.id AND {join}""",
                        (om, os_, om, os_))
                execute_write(
                    f"UPDATE {tbl} SET market=%s, selection=%s WHERE market=%s AND selection=%s",
                    (nm, ns, om, os_))
        grand["tables"][tbl] = {"pairs": len(maps), "to_update": n_update,
                                "to_delete_collisions": n_collide, "details": details,
                                "skipped": skipped}
    return grand


def _verify(execute_query):
    """After execute: how many rows remain non-canonical per table (should be 0)."""
    from workers.canonical_market import canonicalize_for_storage
    out = {}
    for tbl in _UNIQ_PREFIX:
        rows = execute_query(f"SELECT DISTINCT market, selection FROM {tbl}")
        bad = 0
        for r in rows:
            m, s = r["market"], r["selection"]
            if not m or not s:
                continue
            nm, ns = canonicalize_for_storage(m, s)
            if (nm, ns) != (m, s) and not _implausible(nm):  # implausible typos are intentionally left
                bad += 1
        out[tbl] = bad
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Canonicalise bet-table market/selection (dry-run unless --execute).")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    _load_env()
    from workers.api_clients.db import execute_query
    res = run(a.execute)
    mode = "EXECUTE" if a.execute else "DRY-RUN"
    print(f"MARKET-VOCAB backfill [{mode}]")
    for tbl, d in res["tables"].items():
        print(f"\n{tbl}: {d['pairs']} legacy pair(s) → update {d['to_update']} row(s), "
              f"delete {d['to_delete_collisions']} collision-dup(s)")
        for (om, os_, nm, ns, cnt, coll) in d["details"]:
            print(f"    {om!r}/{os_!r} → {nm!r}/{ns!r}   rows={cnt} collisions={coll}")
        for (om, os_, nm, ns) in d.get("skipped", []):
            print(f"    SKIPPED (implausible source typo, left for manual review): {om!r}/{os_!r} → {nm!r}")
    if a.execute:
        v = _verify(execute_query)
        print("\nVERIFY non-canonical remaining:", v,
              "→ CLEAN" if all(x == 0 for x in v.values()) else "→ STILL DIRTY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
