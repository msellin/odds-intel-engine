"""
Rewrite the COMP_FALLBACK block in odds-intel-web/src/app/page.tsx from
the current ledger/comparison_*.json values.

Runs weekly from .github/workflows/competitor_audits_weekly.yml after the
audit JSONs are refreshed. Keeps the hardcoded fallback in sync with the
live values so that when the GitHub raw fetch fails at request time
(rare, but real), the landing degrades to numbers that are at most one
week old — not the initial-commit values from months ago.

Usage:
    python3 scripts/update_frontend_comp_fallback.py \\
        --web-repo /path/to/odds-intel-web

Exits 0 with no-op message if:
    - The web repo path doesn't exist (workflow may skip checkout when
      the cross-repo PAT is not set — graceful degradation)
    - The page.tsx already matches the current audit values

Exits 0 with "updated" message when it rewrote page.tsx. The caller
(workflow) is responsible for committing + pushing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


LEDGER_KEYS = ["winnerodds", "signalodds", "deepbetting", "tipstrr", "forebet"]


def load_audit(engine_root: Path, key: str) -> dict[str, float | int]:
    """Read one comparison_<key>.json and return the four numbers that
    feed COMP_FALLBACK. Raises on missing keys — a malformed audit JSON
    is a bug we want to surface loudly, not silently paper over."""
    p = engine_root / "ledger" / f"comparison_{key}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    their = data["their_stats"]
    ours = data["our_stats_same_window"]
    return {
        "theirN": int(their["n"]),
        "theirRoi": float(their["roi_pct"]),
        "ourN": int(ours["n"]),
        "ourRoi": float(ours["roi_pct"]),
    }


def format_block(values: dict[str, dict[str, float | int]]) -> str:
    """Render the COMP_FALLBACK dict as a TypeScript object literal,
    padded so numeric columns line up like the hand-written original."""
    # Column widths tuned to the existing hand-authored padding.
    lines = []
    for key in LEDGER_KEYS:
        v = values[key]
        # theirN and ourN as integers, ROIs to 2dp (matches audit output).
        lines.append(
            f"  {key + ':':<13}"
            f"{{ theirN: {v['theirN']:>4}, "
            f"theirRoi: {v['theirRoi']:>5.2f}, "
            f"ourN: {v['ourN']:>4}, "
            f"ourRoi: {v['ourRoi']:>5.2f} }},"
        )
    return "\n".join(lines)


def rewrite_page(page_path: Path, new_block: str, today: str) -> bool:
    """Rewrite the COMP_FALLBACK dict in page.tsx in place. Returns True
    if the file changed, False if it was already up to date."""
    src = page_path.read_text(encoding="utf-8")

    # Match the whole const declaration incl. body — anchors on the exact
    # comment marker + the closing "};" so we don't overrun into
    # subsequent code.
    pattern = re.compile(
        r"(// LAST-REFRESH: )\d{4}-\d{2}-\d{2}( audit snapshot\.\n"
        r"const COMP_FALLBACK: Record<string, "
        r"\{ theirN: number; theirRoi: number; ourN: number; ourRoi: number \}> = \{\n)"
        r"(.*?)"
        r"(\n\};)",
        re.DOTALL,
    )
    m = pattern.search(src)
    if not m:
        raise SystemExit(
            "page.tsx does not match the expected COMP_FALLBACK pattern. "
            "The landing must keep the LAST-REFRESH marker + typed dict "
            "declaration intact — see /Users/margussellin/www/odds-intel-web/"
            "src/app/page.tsx for the reference shape."
        )

    replacement = f"{m.group(1)}{today}{m.group(2)}{new_block}{m.group(4)}"
    if replacement == m.group(0):
        return False
    new_src = src[: m.start()] + replacement + src[m.end() :]
    page_path.write_text(new_src, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--web-repo",
        required=True,
        help="Path to the odds-intel-web checkout (workflow-provided).",
    )
    ap.add_argument(
        "--engine-repo",
        default=str(Path(__file__).resolve().parent.parent),
        help="Path to the odds-intel-engine checkout (default: this repo).",
    )
    args = ap.parse_args()

    engine = Path(args.engine_repo).resolve()
    web = Path(args.web_repo).resolve()

    if not web.exists():
        # Workflow reached this step without a checked-out web repo —
        # typically means FRONTEND_REPO_TOKEN is not configured. Log
        # and exit 0 so the outer audit run stays green.
        print(
            f"[fallback-refresh] {web} does not exist — skipping (cross-repo "
            "PAT likely not configured). Audit JSONs are still committed to "
            "the engine repo; the landing keeps using them directly via the "
            "GitHub raw fetch."
        )
        return 0

    page = web / "src" / "app" / "page.tsx"
    if not page.exists():
        print(f"[fallback-refresh] {page} not found — skipping.")
        return 0

    values: dict[str, dict[str, float | int]] = {}
    for key in LEDGER_KEYS:
        try:
            values[key] = load_audit(engine, key)
        except (OSError, KeyError, ValueError) as exc:
            print(
                f"[fallback-refresh] Could not load comparison_{key}.json: "
                f"{exc}. Aborting rewrite so we don't publish a half-refreshed "
                "fallback block."
            )
            return 1

    new_block = format_block(values)
    today = date.today().isoformat()
    changed = rewrite_page(page, new_block, today)
    if changed:
        print(f"[fallback-refresh] page.tsx COMP_FALLBACK refreshed ({today}).")
    else:
        print("[fallback-refresh] page.tsx already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
