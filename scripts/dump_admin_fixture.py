"""Per-page snapshots for the LOCAL admin design preview (#139, 2026-09-24).

Companion of dump_bot_board_fixture.py (which owns /admin/bots + /admin Overview). Each other
admin page has ONE module scripts/admin_fixtures/<name>.py with `def snapshot(rows) -> dict`
(`rows(sql, params)` runs a read-only query and returns JSON-able dicts); the web page reads it
in the dev preview through src/lib/admin-fixture.ts readAdminFixture("<name>"). One file per page
so pages can be built independently.

    python3 scripts/dump_admin_fixture.py --page feeds
    python3 scripts/dump_admin_fixture.py --all

Writes odds-intel-web/.dev-fixtures/admin-<name>.json (gitignored). Reads the production DB
read-only over the engine's direct connection. Keep each page's SQL next to the page it serves —
the same shapes the page's loader reads, so the preview renders what production will.
"""
import argparse
import datetime as dt
import decimal
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

_OUT = Path(__file__).resolve().parent.parent.parent / "odds-intel-web" / ".dev-fixtures"


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)


def rows(sql, params=None):
    return [{k: _plain(v) for k, v in r.items()} for r in execute_query(sql, params)]


def _pages() -> dict:
    import importlib
    d = Path(__file__).parent / "admin_fixtures"
    out = {}
    for f in sorted(d.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        out[f.stem] = importlib.import_module(f"scripts.admin_fixtures.{f.stem}").snapshot
    return out


def main() -> None:
    PAGES = _pages()
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", choices=sorted(PAGES) or None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    names = sorted(PAGES) if a.all else [a.page]
    _OUT.mkdir(parents=True, exist_ok=True)
    for n in names:
        if not n:
            ap.error("--page or --all")
        p = _OUT / f"admin-{n}.json"
        p.write_text(json.dumps(PAGES[n](rows)))
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
