"""
CS2-MATCH-ID-BRIDGE — populate cs2_match_id_bridge.

Joins bo3gg matches (cs2_results, the settled-outcomes universe sneak peeks
care about) to HLTV matches (cs2_hltv_matches) using a tiered fuzzy match:

  1. exact      — lowercased team1/team2 equal on both sides       conf=1.00
  2. norm_team  — suffix-stripped + alias-normalised team names     conf=0.90
  3. fuzzy      — rapidfuzz token_set_ratio on both teams           conf=0.5 + min/200

Candidates restricted to ±36h of kickoff (bo3gg and HLTV drift by up to a
day for delayed/rescheduled matches, plus tz quirks). Bidirectional team
pairing (team1↔team1, team2↔team2 OR team1↔team2, team2↔team1) — we pick
whichever ordering scores higher. Accept best candidate per bo3gg match
when confidence ≥ 0.6. When multiple candidates tie on confidence the
smallest time-drift wins.

Placeholder-timestamp handling: the HLTV backfill stored ~90% of matches
at a handful of "fetch time" placeholder timestamps (2026-06-11 16:30Z is
the largest bucket). For HLTV rows in those buckets the time component
is meaningless, so we ignore the ±36h window and rely entirely on the
team-name match. To keep 1:1 mapping when a pair recurs across events,
each placeholder-bucket HLTV row is greedy-assigned to at most one bo3gg
match (chronological bo3gg order, ascending hltv_match_id within a pair).

Re-runnable: ON CONFLICT (bo3gg_id, hltv_match_id) DO UPDATE.
"""
from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python3 scripts/esports/cs2_match_id_bridge_populate.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2.extras  # noqa: E402
from workers.api_clients.db import get_conn, execute_query  # noqa: E402

# ── fuzzy backend ──────────────────────────────────────────────────────────
try:
    from rapidfuzz import fuzz as _rf_fuzz
    def _fuzz_score(a: str, b: str) -> float:
        return float(_rf_fuzz.token_set_ratio(a, b))
    _FUZZ_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover — fallback only
    from difflib import SequenceMatcher
    def _fuzz_score(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0
    _FUZZ_BACKEND = "difflib"


# ── normalisation ──────────────────────────────────────────────────────────
# Trailing org-suffix words that don't change identity.
# Order matters — multi-word phrases first.
_SUFFIX_WORDS = [
    "esports.net",
    "e-sports",
    "esports",
    "esport",
    "gaming",
    "academy",
    "clan",
    "club",
    "team",
    "pro",
    "fe",            # "<org> fe" women's lineup tag — keep? often present
                     # on both sides if both are women. Drop is safer.
]

# Known alias pairs (lowercased, normalised). After we normalise both sides we
# unify these to a single canonical token so they collide on dict lookup.
_ALIAS_MAP = {
    "faze clan": "faze",
    "team spirit": "spirit",
    "spirit academy": "spirit_academy",
    "team vitality": "vitality",
    "team liquid": "liquid",
    "team falcons": "falcons",
    "team heretics": "heretics",
    "team aurora": "aurora",
    "natus vincere": "navi",
    "natus vincere junior": "navi_junior",
    "navi junior": "navi_junior",
    "ninjas in pyjamas": "nip",
    "g2 esports": "g2",
    "mouz nxt": "mouz_nxt",
    "mouz": "mouz",
    "mousesports": "mouz",
    "fnatic rising": "fnatic_rising",
    "1win team": "1win",
    "9 pandas": "9pandas",
    "ninepandas": "9pandas",
    "saw esports": "saw",
    "the mongolz": "mongolz",
    "mongolz": "mongolz",
    "ence academy": "ence_academy",
    "ence": "ence",
    "heroic academy": "heroic_academy",
    "complexity gaming": "complexity",
    "ex-betera": "betera",
    "ex-anonymo": "anonymo",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _strip_suffixes(s: str) -> str:
    # Repeatedly remove a trailing suffix word until no more matches.
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIX_WORDS:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
                break
    return s


def _strip_prefix_team(s: str) -> str:
    # "Team X" → "X" (only if remainder is non-trivial)
    if s.startswith("team ") and len(s) > 6:
        return s[5:]
    return s


def normalise_team(name: str) -> str:
    """Lower, trim, drop common suffixes/prefixes, apply alias map, collapse non-alnum."""
    if not name:
        return ""
    s = name.lower().strip()
    # First pass: alias map on the raw lowercased form.
    if s in _ALIAS_MAP:
        s = _ALIAS_MAP[s]
    s = _strip_prefix_team(s)
    s = _strip_suffixes(s)
    # Second pass: alias map on stripped form.
    if s in _ALIAS_MAP:
        s = _ALIAS_MAP[s]
    # Final: collapse non-alnum to nothing for resilience to dots/hyphens.
    s = _NON_ALNUM.sub("", s)
    return s


# ── candidate scoring ──────────────────────────────────────────────────────

def _exact_pair(b1: str, b2: str, h1: str, h2: str) -> bool:
    b1l, b2l, h1l, h2l = b1.lower(), b2.lower(), h1.lower(), h2.lower()
    return (b1l == h1l and b2l == h2l) or (b1l == h2l and b2l == h1l)


def _norm_pair(b1n: str, b2n: str, h1n: str, h2n: str) -> bool:
    if not (b1n and b2n and h1n and h2n):
        return False
    return (b1n == h1n and b2n == h2n) or (b1n == h2n and b2n == h1n)


def _fuzzy_pair_score(b1: str, b2: str, h1: str, h2: str) -> tuple[float, float]:
    """Return (best_avg, best_min) across the two pairings."""
    s_a1 = _fuzz_score(b1, h1)
    s_a2 = _fuzz_score(b2, h2)
    s_b1 = _fuzz_score(b1, h2)
    s_b2 = _fuzz_score(b2, h1)
    avg_a = (s_a1 + s_a2) / 2.0
    min_a = min(s_a1, s_a2)
    avg_b = (s_b1 + s_b2) / 2.0
    min_b = min(s_b1, s_b2)
    if avg_a >= avg_b:
        return avg_a, min_a
    return avg_b, min_b


# ── main ───────────────────────────────────────────────────────────────────

# bo3gg vs HLTV kickoffs drift by up to ~24h in practice — likely a mix of
# timezone normalisation and matches that get rescheduled. ±36h captures the
# real overlap while still ruling out coincidental name reuse across events.
WINDOW = timedelta(hours=36)
WINDOW_SEC = int(WINDOW.total_seconds())
FUZZ_MIN_BOTH = 70.0
ACCEPT_THRESHOLD = 0.6


def load_bo3gg() -> list[dict]:
    rows = execute_query(
        "SELECT bo3gg_id, team1, team2, kickoff_time "
        "FROM cs2_results "
        "WHERE kickoff_time IS NOT NULL "
        "  AND team1 IS NOT NULL AND team2 IS NOT NULL"
    )
    out = []
    for r in rows:
        out.append({
            "bo3gg_id": str(r["bo3gg_id"]),
            "team1": r["team1"],
            "team2": r["team2"],
            "team1_n": normalise_team(r["team1"]),
            "team2_n": normalise_team(r["team2"]),
            "kickoff": r["kickoff_time"],
        })
    return out


def load_hltv() -> list[dict]:
    rows = execute_query(
        "SELECT hltv_match_id, team1_name, team2_name, match_date "
        "FROM cs2_hltv_matches "
        "WHERE match_date IS NOT NULL "
        "  AND team1_name IS NOT NULL AND team2_name IS NOT NULL"
    )
    out = []
    for r in rows:
        out.append({
            "hltv_match_id": int(r["hltv_match_id"]),
            "team1": r["team1_name"],
            "team2": r["team2_name"],
            "team1_n": normalise_team(r["team1_name"]),
            "team2_n": normalise_team(r["team2_name"]),
            "kickoff": r["match_date"],
        })
    return out


# Placeholder timestamps from the HLTV match-details backfill: the scraper
# couldn't parse a real date on ~90% of pages and defaulted to fetch-time,
# clustering ~26k matches into a handful of identical timestamps. Inside
# these buckets the time component is meaningless — we ignore the time
# window entirely and rely on the team-name match alone.
PLACEHOLDER_BUCKET_MIN_COUNT = 100


def detect_placeholder_timestamps(hltv: list[dict]) -> set[int]:
    """Any HLTV timestamp shared by >=PLACEHOLDER_BUCKET_MIN_COUNT matches is a
    placeholder. Returns a set of unix-second timestamps."""
    counts: dict[int, int] = defaultdict(int)
    for h in hltv:
        counts[int(h["kickoff"].timestamp())] += 1
    return {ts for ts, c in counts.items() if c >= PLACEHOLDER_BUCKET_MIN_COUNT}


def build_time_index(hltv: list[dict],
                     placeholder_ts: set[int]) -> dict[int, list[int]]:
    """Bucket REAL-timestamp HLTV matches by hour for fast time-window lookup.
    Placeholder-dated rows are excluded — they're matched separately by name."""
    idx: dict[int, list[int]] = defaultdict(list)
    for i, h in enumerate(hltv):
        ts = int(h["kickoff"].timestamp())
        if ts in placeholder_ts:
            continue
        bucket = ts // 3600
        idx[bucket].append(i)
    return idx


def build_name_index(hltv: list[dict],
                     placeholder_ts: set[int]) -> tuple[dict, dict]:
    """Two indexes over PLACEHOLDER-dated HLTV rows keyed by frozenset of team
    names — one exact-lowercase, one normalised. Each value is a list of HLTV
    row indices sorted by hltv_match_id ascending (so greedy assignment to
    chronological bo3gg matches is stable)."""
    exact: dict[frozenset, list[int]] = defaultdict(list)
    norm: dict[frozenset, list[int]] = defaultdict(list)
    for i, h in enumerate(hltv):
        ts = int(h["kickoff"].timestamp())
        if ts not in placeholder_ts:
            continue
        ek = frozenset({h["team1"].lower(), h["team2"].lower()})
        nk = frozenset({h["team1_n"], h["team2_n"]}) if h["team1_n"] and h["team2_n"] else None
        exact[ek].append(i)
        if nk:
            norm[nk].append(i)
    # Sort each list by hltv_match_id (ascending) for stable greedy assignment.
    for d in (exact, norm):
        for k in d:
            d[k].sort(key=lambda i: hltv[i]["hltv_match_id"])
    return exact, norm


def candidates_for(bo: dict, hltv: list[dict],
                   time_idx: dict[int, list[int]]) -> list[int]:
    ts = int(bo["kickoff"].timestamp())
    base_bucket = ts // 3600
    # ±36h → sweep ±37 hour-buckets to be safe at boundaries.
    span = (WINDOW_SEC // 3600) + 1
    out: list[int] = []
    for b in range(base_bucket - span, base_bucket + span + 1):
        for i in time_idx.get(b, ()):
            if abs(int(hltv[i]["kickoff"].timestamp()) - ts) <= WINDOW_SEC:
                out.append(i)
    return out


def score_match(bo: dict, h: dict) -> tuple[str, float, float | None, int] | None:
    """Return (joined_by, confidence, team_score_avg, time_drift_sec) or None.
    Caller must pass a real-timestamp HLTV row — drift is meaningful here."""
    drift = abs(int(h["kickoff"].timestamp()) - int(bo["kickoff"].timestamp()))

    # exact (lowercased originals)
    if _exact_pair(bo["team1"], bo["team2"], h["team1"], h["team2"]):
        return "exact", 1.0, 100.0, drift

    # normalised
    if _norm_pair(bo["team1_n"], bo["team2_n"], h["team1_n"], h["team2_n"]):
        return "norm_team", 0.9, 100.0, drift

    # fuzzy
    avg, mn = _fuzzy_pair_score(bo["team1"], bo["team2"], h["team1"], h["team2"])
    if mn >= FUZZ_MIN_BOTH:
        conf = 0.5 + mn / 200.0   # mn=70→0.85, mn=100→1.0
        return "fuzzy", round(conf, 4), round(avg, 2), drift

    return None


def main() -> None:
    t0 = time.time()
    print(f"[bridge] fuzzy backend: {_FUZZ_BACKEND}")
    print("[bridge] loading bo3gg + HLTV …")
    bo3gg = load_bo3gg()
    hltv = load_hltv()
    print(f"[bridge] bo3gg={len(bo3gg):,}  hltv={len(hltv):,}")

    placeholder_ts = detect_placeholder_timestamps(hltv)
    n_placeholder = sum(1 for h in hltv if int(h["kickoff"].timestamp()) in placeholder_ts)
    print(f"[bridge] detected {len(placeholder_ts)} placeholder-timestamp buckets "
          f"covering {n_placeholder:,}/{len(hltv):,} HLTV rows")

    time_idx = build_time_index(hltv, placeholder_ts)
    name_idx_exact, name_idx_norm = build_name_index(hltv, placeholder_ts)

    # Process bo3gg in chronological order so greedy assignment to
    # placeholder-bucket HLTV rows (sorted by hltv_match_id ascending) is stable
    # and roughly tracks event chronology.
    bo3gg_sorted = sorted(bo3gg, key=lambda b: b["kickoff"])

    rows_to_write: list[tuple] = []
    counts = {"exact": 0, "norm_team": 0, "fuzzy": 0, "unmatched": 0}
    unmatched_samples: list[dict] = []
    low_conf_samples: list[dict] = []

    # Track which placeholder-bucket HLTV rows we've already assigned so
    # a popular pair like "MOUZ vs FaZe" doesn't collapse 14 bo3gg matches
    # into one HLTV id.
    used_placeholder_hltv: set[int] = set()

    for n, bo in enumerate(bo3gg_sorted, 1):
        # PASS 1 — score against real-timestamp HLTV within ±36h.
        cands = candidates_for(bo, hltv, time_idx)
        best_rank: tuple | None = None
        best: tuple | None = None  # (conf, joined_by, tsa, drift, hltv_idx)
        for ci in cands:
            scored = score_match(bo, hltv[ci])
            if scored is None:
                continue
            joined_by, conf, tsa, drift = scored
            # rank key: higher conf, then smaller drift, then higher team_score_avg.
            rank = (conf, -drift, tsa or 0.0)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (conf, joined_by, tsa, drift, ci)

        # PASS 2 — if no real-time hit, fall back to placeholder buckets by name.
        # Drift is reported as NULL (unknown true time). Confidence drops one
        # notch vs. real-time match to flag the uncertainty.
        if best is None or best[0] < ACCEPT_THRESHOLD:
            placeholder_best: tuple | None = None
            ek = frozenset({bo["team1"].lower(), bo["team2"].lower()})
            nk = (frozenset({bo["team1_n"], bo["team2_n"]})
                  if bo["team1_n"] and bo["team2_n"] else None)
            # Exact-name candidates first.
            for ci in name_idx_exact.get(ek, ()):
                if hltv[ci]["hltv_match_id"] in used_placeholder_hltv:
                    continue
                placeholder_best = (0.8, "exact", 100.0, None, ci)
                break
            if placeholder_best is None and nk is not None:
                for ci in name_idx_norm.get(nk, ()):
                    if hltv[ci]["hltv_match_id"] in used_placeholder_hltv:
                        continue
                    placeholder_best = (0.7, "norm_team", 100.0, None, ci)
                    break
            if placeholder_best is not None:
                best = placeholder_best
                used_placeholder_hltv.add(hltv[placeholder_best[4]]["hltv_match_id"])

        if best is None or best[0] < ACCEPT_THRESHOLD:
            counts["unmatched"] += 1
            if len(unmatched_samples) < 5:
                unmatched_samples.append({
                    "team1": bo["team1"], "team2": bo["team2"],
                    "kickoff_date": bo["kickoff"].date().isoformat(),
                })
            continue

        conf, joined_by, tsa, drift, ci = best
        h = hltv[ci]
        counts[joined_by] += 1
        rows_to_write.append((
            bo["bo3gg_id"],
            h["hltv_match_id"],
            conf,
            joined_by,
            tsa,
            drift,
        ))
        if 0.6 <= conf < 0.75 and len(low_conf_samples) < 20:
            low_conf_samples.append({
                "bo3gg_id": bo["bo3gg_id"],
                "bo3gg_teams": f"{bo['team1']} vs {bo['team2']}",
                "hltv_teams": f"{h['team1']} vs {h['team2']}",
                "joined_by": joined_by,
                "confidence": conf,
                "team_score_avg": tsa,
                "drift_sec": drift,
                "kickoff_date": bo["kickoff"].date().isoformat(),
            })

        if n % 2000 == 0:
            print(f"[bridge] scored {n:,}/{len(bo3gg_sorted):,}")

    # Replace prior rows for any bo3gg we just rebridged — the PK is
    # (bo3gg_id, hltv_match_id) which allows multiple rows per bo3gg, but each
    # populate run should produce a clean 1:1 picture. Delete-then-insert
    # bounded to the bo3gg ids we touched so manual overrides on other rows
    # (joined_by='manual') survive.
    touched_bo3gg = [r[0] for r in rows_to_write]
    print(f"[bridge] writing {len(rows_to_write):,} bridge rows …")
    sql_insert = (
        "INSERT INTO cs2_match_id_bridge "
        "(bo3gg_id, hltv_match_id, confidence, joined_by, team_score_avg, time_drift_sec) "
        "VALUES %s "
        "ON CONFLICT (bo3gg_id, hltv_match_id) DO UPDATE SET "
        "  confidence = EXCLUDED.confidence, "
        "  joined_by = EXCLUDED.joined_by, "
        "  team_score_avg = EXCLUDED.team_score_avg, "
        "  time_drift_sec = EXCLUDED.time_drift_sec"
    )
    if rows_to_write:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Wipe non-manual existing rows for the bo3gg ids we just rebridged.
                cur.execute(
                    "DELETE FROM cs2_match_id_bridge "
                    "WHERE bo3gg_id = ANY(%s) AND joined_by <> 'manual'",
                    (touched_bo3gg,),
                )
                psycopg2.extras.execute_values(
                    cur, sql_insert, rows_to_write, page_size=1000
                )
                conn.commit()

    total = len(bo3gg)
    bridged = total - counts["unmatched"]
    coverage = (bridged / total * 100.0) if total else 0.0

    print()
    print("=" * 60)
    print("CS2-MATCH-ID-BRIDGE — populate summary")
    print("=" * 60)
    print(f"total bo3gg matches : {total:,}")
    print(f"bridged             : {bridged:,}")
    print(f"coverage            : {coverage:.2f}%")
    print(f"  exact             : {counts['exact']:,}")
    print(f"  norm_team         : {counts['norm_team']:,}")
    print(f"  fuzzy             : {counts['fuzzy']:,}")
    print(f"  unmatched         : {counts['unmatched']:,}")
    print(f"elapsed             : {time.time() - t0:.1f}s")

    print()
    print("── 5 sample unmatched bo3gg rows ─────────────────────────")
    for u in unmatched_samples:
        print(f"  {u['kickoff_date']}  {u['team1']!r} vs {u['team2']!r}")

    print()
    print("── 20 sample low-confidence (0.6-0.75) joins ────────────")
    low_conf_samples.sort(key=lambda r: r["confidence"])
    for r in low_conf_samples:
        print(
            f"  conf={r['confidence']:.3f} {r['joined_by']:<9} "
            f"avg={r['team_score_avg']} drift={r['drift_sec']}s "
            f"{r['kickoff_date']}  bo3gg={r['bo3gg_teams']!r}  "
            f"hltv={r['hltv_teams']!r}"
        )


if __name__ == "__main__":
    main()
