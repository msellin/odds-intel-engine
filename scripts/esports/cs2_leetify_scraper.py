"""
CS2 Leetify scraper — ingests per-player-per-match stats from the Leetify
public API. First independent signal source after the HLTV-detail v10-v19
feature space turned out empty.

Source: https://api-public.cs-prod.leetify.com (auth via `_leetify_key` header).

Endpoints used:
  GET /v3/profile?steam64_id=X            — profile + recent_matches/teammates
  GET /v3/profile/matches?steam64_id=X    — last 100 matches per Steam64 (FULL
                                            per-player stats for all 10 players
                                            in each match — one call gets 1000
                                            player-rows)
  GET /v2/matches/{leetify_id}            — match details by UUID
  GET /v2/matches/hltv/{full_filename}    — match details by HLTV filename

Cross-references HLTV match IDs natively: when `data_source='hltv'`, the
`data_source_match_id` is a string like `"2394212-bc-game-vs-pain-m2-anubis.dem"`.
We parse the leading digits as `hltv_match_id` for direct bridging to v8.

Politeness: 1 req/s default. 429 backoff. Failures don't crash the bootstrap —
they log and continue so a single bad player can't kill the run.

IMPORTANT: /v3/profile/matches?steam64_id=X only returns THAT PLAYER's stats
per match (1-element stats array). To get all 10 players per match we must
hit /v2/matches/{id} separately. The bootstrap does that as a "fill" pass
over the unique leetify_match_ids it just wrote, so we end up with the full
10-player payload for each match.

CLI:
  --bootstrap-seeds N      — Walk N seeds from KNOWN_SEEDS. Also follows
                              recent_teammates one hop per seed so we
                              accumulate the bridge faster.
  --match-id <uuid>        — One-off match fetch via /v2/matches/{id}.
  --full-match <ds-id>     — One-off match fetch via /v2/matches/hltv/{filename}.
  --fill-matches N         — Take N existing leetify_match_ids that have
                              <10 player rows and refetch via /v2/matches.

Writes:
  cs2_player_id_bridge                — hltv_player_id ↔ steam64_id
  cs2_leetify_player_match_stats      — per-player-per-match Leetify stats
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# Load env if present (.env may be locked from agents but is readable at runtime).
try:
    from dotenv import dotenv_values
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for k, v in dotenv_values(env_path).items():
            os.environ.setdefault(k, v)
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, get_conn  # noqa: E402

import psycopg2.extras  # noqa: E402


LEETIFY_API_KEY = os.getenv(
    "LEETIFY_API_KEY",
    "b707b687-1224-4b9a-b072-fa85073adc8b",  # inline default — see task notes.
)
LEETIFY_BASE = "https://api-public.cs-prod.leetify.com"

RATE_LIMIT_SECONDS = 1.0  # be polite — Leetify suggests 1 req/s.


# ── Hardcoded seed Steam64 IDs for top CS2 pros ──────────────────────────
# Public profile IDs; no PII. Used to bootstrap cs2_player_id_bridge before
# we have any HLTV-id ↔ Steam64-id linkage.
KNOWN_SEEDS: dict[str, str] = {
    "s1mple":     "76561198034202275",
    "donk":       "76561199063238565",
    "ZywOo":      "76561198146578464",
    "m0NESY":     "76561198872330765",
    "Aleksib":    "76561198013243326",
    "b1t":        "76561198280858203",
    "device":     "76561197992472622",
    "dupreeh":    "76561197983956651",
    "XANTARES":   "76561198028816539",
    "magixx":     "76561198855772989",
    "Twistzz":    "76561198072023918",
    "ropz":       "76561198044045107",
    "rain":       "76561198044215962",
    "broky":      "76561198218941619",
    "frozen":     "76561198091709284",
    "jks":        "76561198013222640",
    "stavn":      "76561198033041316",
    "jabbi":      "76561198194732116",
    "TeSeS":      "76561198052813822",
    "blameF":     "76561198091811071",
    "NiKo":       "76561197999004010",
    "huNter-":    "76561198069149303",
    "sh1ro":      "76561198297083093",
    "Hobbit":     "76561198039365613",
    "chopper":    "76561198166378230",
    "Boombl4":    "76561198106583994",
    "electroNic": "76561198044849243",
    "Perfecto":   "76561198081831824",
    "FalleN":     "76561197960690195",
    "yuurih":     "76561198194973922",
}


# ── HTTP plumbing ─────────────────────────────────────────────────────────
_last_call: list[float] = [0.0]


def _polite_sleep():
    """Block until at least RATE_LIMIT_SECONDS has passed since the previous call."""
    now = time.monotonic()
    wait = RATE_LIMIT_SECONDS - (now - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def fetch_json(url: str, *, retries: int = 3) -> dict | list | None:
    """GET url with the Leetify auth header. Returns parsed JSON or None on
    persistent failure. Handles 429 with exponential backoff."""
    for attempt in range(retries):
        _polite_sleep()
        req = urllib.request.Request(
            url, headers={"_leetify_key": LEETIFY_API_KEY, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                backoff = 2.0 * (2 ** attempt)
                print(f"  [429] backing off {backoff:.1f}s on {url}")
                time.sleep(backoff)
                continue
            if e.code in (401, 403):
                print(f"  [{e.code}] auth failed on {url}")
                return None
            if e.code == 404:
                print(f"  [404] not found: {url}")
                return None
            print(f"  [{e.code}] {url}: {e}")
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  [warn] {url}: {e}")
            time.sleep(1.0 * (attempt + 1))
    return None


# ── HLTV id parsing ───────────────────────────────────────────────────────
_HLTV_ID_RE = re.compile(r"^(\d+)-")


def parse_hltv_match_id(data_source_match_id: str | None) -> int | None:
    """Pull the leading digits from a Leetify HLTV match id, e.g.
    "2394212-bc-game-vs-pain-m2-anubis.dem" -> 2394212. Returns None for
    non-HLTV sources."""
    if not data_source_match_id:
        return None
    m = _HLTV_ID_RE.match(data_source_match_id)
    return int(m.group(1)) if m else None


# ── Bridge resolution ─────────────────────────────────────────────────────
def resolve_hltv_player_id(nickname: str) -> int | None:
    """Look up hltv_player_id by exact-nickname match against
    cs2_hltv_player_match_stats.nickname (case-insensitive). Returns None
    if no unique match. Used to populate joined_by='leetify_profile' rows
    in cs2_player_id_bridge."""
    if not nickname:
        return None
    rows = execute_query(
        """SELECT DISTINCT hltv_player_id
           FROM cs2_hltv_player_match_stats
           WHERE LOWER(nickname) = LOWER(%s)
             AND hltv_player_id IS NOT NULL
           LIMIT 2""",
        (nickname,),
    )
    if len(rows) == 1:
        return int(rows[0]["hltv_player_id"])
    return None


# ── Stat field map ────────────────────────────────────────────────────────
# Per-player stats schema in Leetify match payloads — pulled from a probe of
# /v2/matches/{id}.stats[] and /v3/profile/matches[*].stats[]. Both endpoints
# return the same 66-field schema. We split into "first-class columns" (used
# in CS2_LEETIFY_COLUMNS) plus the raw JSON dump for future feature work.

# Column order MUST match CS2_LEETIFY_COLUMNS below — bulk upsert tuples are
# positional.
CS2_LEETIFY_COLUMNS = [
    "leetify_match_id", "hltv_match_id", "data_source", "data_source_match_id",
    "map_name", "finished_at", "steam64_id", "nickname", "team_number",
    "leetify_rating", "ct_leetify_rating", "t_leetify_rating",
    "preaim", "reaction_time",
    "accuracy", "accuracy_head", "spray_accuracy",
    "counter_strafing_good_shots_ratio",
    "trade_kill_attempts_percentage", "trade_kills_success_percentage",
    "trade_kill_opportunities_per_round", "traded_deaths_success_percentage",
    "multi1k", "multi2k", "multi3k", "multi4k", "multi5k",
    "flashbang_thrown", "flashbang_hit_foe", "flashbang_leading_to_kill",
    "he_thrown", "molotov_thrown", "smoke_thrown",
    "utility_on_death_avg",
    "total_kills", "total_deaths", "total_assists", "total_damage",
    "rounds_count", "rounds_won", "rounds_survived",
    "kd_ratio", "dpr", "mvps",
    "raw_stats",
]


def _num(v):
    """Coerce to float|None — Leetify sometimes returns int 0/1 for ratios."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_player_row(*, leetify_match_id: str, match_meta: dict,
                     player_stats: dict) -> tuple | None:
    """Convert a single per-player stats dict (one element of match.stats[])
    into a positional tuple for execute_values bulk upsert."""
    steam64 = player_stats.get("steam64_id")
    if not steam64:
        return None

    data_source = match_meta.get("data_source") or "unknown"
    data_source_match_id = match_meta.get("data_source_match_id")
    hltv_id = (parse_hltv_match_id(data_source_match_id)
               if data_source == "hltv" else None)

    # Leetify's API key spelling is `counter_strafing_shots_good_ratio` (note
    # field order — "shots_good_ratio" not "good_shots_ratio"). The DB column
    # was named "counter_strafing_good_shots_ratio" in the task spec; we keep
    # the DB name but read from the actual JSON key.
    counter_strafe = player_stats.get("counter_strafing_shots_good_ratio")

    finished_at_raw = match_meta.get("finished_at")
    # Leetify returns ISO 8601 like "2026-05-21T08:57:33.000Z". psycopg2 can
    # accept the string directly for a TIMESTAMPTZ column.
    finished_at = finished_at_raw

    return (
        leetify_match_id,
        hltv_id,
        data_source,
        data_source_match_id,
        match_meta.get("map_name"),
        finished_at,
        steam64,
        player_stats.get("name"),                          # 'name' → DB column 'nickname'
        _int(player_stats.get("initial_team_number")),
        _num(player_stats.get("leetify_rating")),
        _num(player_stats.get("ct_leetify_rating")),
        _num(player_stats.get("t_leetify_rating")),
        _num(player_stats.get("preaim")),
        _num(player_stats.get("reaction_time")),
        _num(player_stats.get("accuracy")),
        _num(player_stats.get("accuracy_head")),
        _num(player_stats.get("spray_accuracy")),
        _num(counter_strafe),
        _num(player_stats.get("trade_kill_attempts_percentage")),
        _num(player_stats.get("trade_kills_success_percentage")),
        _num(player_stats.get("trade_kill_opportunities_per_round")),
        _num(player_stats.get("traded_deaths_success_percentage")),
        _int(player_stats.get("multi1k")),
        _int(player_stats.get("multi2k")),
        _int(player_stats.get("multi3k")),
        _int(player_stats.get("multi4k")),
        _int(player_stats.get("multi5k")),
        _int(player_stats.get("flashbang_thrown")),
        _int(player_stats.get("flashbang_hit_foe")),
        _int(player_stats.get("flashbang_leading_to_kill")),
        _int(player_stats.get("he_thrown")),
        _int(player_stats.get("molotov_thrown")),
        _int(player_stats.get("smoke_thrown")),
        _num(player_stats.get("utility_on_death_avg")),
        _int(player_stats.get("total_kills")),
        _int(player_stats.get("total_deaths")),
        _int(player_stats.get("total_assists")),
        _int(player_stats.get("total_damage")),
        _int(player_stats.get("rounds_count")),
        _int(player_stats.get("rounds_won")),
        _int(player_stats.get("rounds_survived")),
        _num(player_stats.get("kd_ratio")),
        _num(player_stats.get("dpr")),
        _int(player_stats.get("mvps")),
        json.dumps(player_stats),                          # raw_stats jsonb
    )


# ── Bulk upserts ──────────────────────────────────────────────────────────
def bulk_upsert_player_match_stats(rows: list[tuple]) -> int:
    """Upsert into cs2_leetify_player_match_stats keyed on
    (leetify_match_id, steam64_id). On conflict, refresh everything except
    the PK + scraped_at."""
    if not rows:
        return 0

    cols = ", ".join(CS2_LEETIFY_COLUMNS)
    update_cols = [c for c in CS2_LEETIFY_COLUMNS
                   if c not in ("leetify_match_id", "steam64_id")]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = (f"INSERT INTO cs2_leetify_player_match_stats ({cols}) "
           f"VALUES %s "
           f"ON CONFLICT (leetify_match_id, steam64_id) DO UPDATE SET {updates}")
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows, page_size=200)
            conn.commit()
            return cur.rowcount


def upsert_bridge(steam64_id: str, *, nickname: str | None,
                  hltv_player_id: int | None, joined_by: str) -> bool:
    """Upsert a single row into cs2_player_id_bridge. Returns True if a new
    row was inserted (heuristic: rowcount=1 on INSERT-no-conflict path)."""
    sql = """
        INSERT INTO cs2_player_id_bridge
            (steam64_id, hltv_player_id, nickname, joined_by, confidence)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (steam64_id) DO UPDATE SET
            hltv_player_id = COALESCE(EXCLUDED.hltv_player_id, cs2_player_id_bridge.hltv_player_id),
            nickname       = COALESCE(EXCLUDED.nickname,       cs2_player_id_bridge.nickname),
            joined_by      = EXCLUDED.joined_by,
            confidence     = EXCLUDED.confidence
    """
    # We can't reliably detect "was this an insert" from ON CONFLICT DO UPDATE
    # rowcount, so we count rows-before vs rows-after up the stack.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (steam64_id, hltv_player_id, nickname,
                              joined_by, 1.0))
            conn.commit()
    return True


def bridge_has(steam64_id: str) -> bool:
    rows = execute_query(
        "SELECT 1 FROM cs2_player_id_bridge WHERE steam64_id = %s",
        (steam64_id,),
    )
    return bool(rows)


# ── Match-payload writer ──────────────────────────────────────────────────
def write_match_payload(match_meta: dict) -> tuple[int, int]:
    """Given a single match payload (from /v2/matches/{id} or as one element
    of /v3/profile/matches), upsert all per-player rows. Returns
    (rows_written, hltv_match_id|None)."""
    leetify_match_id = match_meta.get("id")
    if not leetify_match_id:
        return 0, None
    try:
        # Sanity check it parses as UUID — the DB column is UUID.
        uuid.UUID(leetify_match_id)
    except (ValueError, TypeError):
        return 0, None
    stats = match_meta.get("stats") or []
    rows = []
    for ps in stats:
        row = build_player_row(
            leetify_match_id=leetify_match_id,
            match_meta=match_meta,
            player_stats=ps,
        )
        if row is not None:
            rows.append(row)
    if not rows:
        return 0, None
    bulk_upsert_player_match_stats(rows)
    return len(rows), parse_hltv_match_id(match_meta.get("data_source_match_id"))


# ── Bootstrap ─────────────────────────────────────────────────────────────
def bootstrap(n_seeds: int) -> dict:
    """Walk the first n_seeds entries of KNOWN_SEEDS in declared order.

    For each seed:
      1. fetch /v3/profile?steam64_id=X — upsert bridge.
      2. fetch /v3/profile/matches?steam64_id=X — write every match (1 call =
         up to 100 matches × 10 players = 1000 player-rows).
      3. enqueue recent_teammates (top 5 by recent_matches_count) for the
         second pass (the original seed pool is already large enough that
         we don't recurse — just one teammate hop per seed).

    Returns a stats dict for the final report.
    """
    seeds_done = 0
    matches_written = 0
    hltv_matches: set[int] = set()
    new_bridge_rows = 0
    teammate_steam64s: set[str] = set()

    seed_items = list(KNOWN_SEEDS.items())[:n_seeds]
    print(f"\n--- bootstrap: {len(seed_items)} seeds ---")
    for nickname, steam64 in seed_items:
        print(f"[seed] {nickname} ({steam64})")
        prof = fetch_json(f"{LEETIFY_BASE}/v3/profile?steam64_id={steam64}")
        if not prof:
            print("  profile fetch failed; skipping")
            continue

        prof_name = prof.get("name") or nickname
        existed = bridge_has(steam64)
        hltv_id = resolve_hltv_player_id(prof_name)
        upsert_bridge(
            steam64,
            nickname=prof_name,
            hltv_player_id=hltv_id,
            joined_by="leetify_profile",
        )
        if not existed:
            new_bridge_rows += 1
        print(f"  bridge: {prof_name} steam64={steam64} "
              f"hltv_player_id={hltv_id} (new_row={not existed})")

        # Capture teammates for a second pass after seeds run.
        for tm in (prof.get("recent_teammates") or [])[:5]:
            sid = tm.get("steam64_id")
            if sid and sid not in KNOWN_SEEDS.values():
                teammate_steam64s.add(sid)

        # Pull last 100 matches with full per-player stats inline.
        matches = fetch_json(
            f"{LEETIFY_BASE}/v3/profile/matches?steam64_id={steam64}"
        )
        if not isinstance(matches, list):
            print("  profile/matches fetch returned non-list; skipping")
            seeds_done += 1
            continue

        local_match_rows = 0
        for m in matches:
            n_rows, hltv_id = write_match_payload(m)
            local_match_rows += n_rows
            if hltv_id is not None:
                hltv_matches.add(hltv_id)
        matches_written += len(matches)
        print(f"  wrote {local_match_rows} player-rows across {len(matches)} matches")
        seeds_done += 1

    # ── Teammate hop: one /v3/profile call per teammate to seed the bridge.
    # We DON'T pull /v3/profile/matches for teammates — that would be N×100
    # match writes and blow past the "directional sneak peek" goal. Just
    # bridge them so future runs can target them by Steam64.
    print(f"\n--- teammate bridge hop: {len(teammate_steam64s)} unique teammates ---")
    for sid in sorted(teammate_steam64s):
        prof = fetch_json(f"{LEETIFY_BASE}/v3/profile?steam64_id={sid}")
        if not prof:
            continue
        nick = prof.get("name")
        hltv_id = resolve_hltv_player_id(nick) if nick else None
        existed = bridge_has(sid)
        upsert_bridge(sid, nickname=nick, hltv_player_id=hltv_id,
                      joined_by="leetify_profile")
        if not existed:
            new_bridge_rows += 1

    return {
        "seeds_done": seeds_done,
        "matches_written": matches_written,
        "hltv_bridged_matches": len(hltv_matches),
        "new_bridge_rows": new_bridge_rows,
        "teammates_seen": len(teammate_steam64s),
    }


def fill_matches(max_matches: int) -> int:
    """For up to max_matches existing leetify_match_ids that currently have
    <10 player rows, refetch via /v2/matches/{id} so we capture the other 9
    players. /v3/profile/matches only returns the seed player's row.

    Targets HLTV-bridged matches first since those are the ones we'll use
    for the sneak peek; falls back to any data_source after that.
    """
    rows = execute_query(
        """
        SELECT leetify_match_id, n_players
        FROM (
          SELECT leetify_match_id, COUNT(*) AS n_players,
                 BOOL_OR(data_source = 'hltv') AS is_hltv
          FROM cs2_leetify_player_match_stats
          GROUP BY leetify_match_id
        ) q
        WHERE n_players < 10
        ORDER BY is_hltv DESC, leetify_match_id
        LIMIT %s
        """,
        (max_matches,),
    )
    print(f"\n--- fill_matches: {len(rows)} candidates (<10 players each) ---")
    total_rows = 0
    refetched = 0
    for r in rows:
        lid = str(r["leetify_match_id"])
        md = fetch_json(f"{LEETIFY_BASE}/v2/matches/{lid}")
        if not md:
            continue
        n_rows, _ = write_match_payload(md)
        if n_rows >= 5:
            refetched += 1
        total_rows += n_rows
    print(f"  refetched {refetched} matches reaching ≥5 player rows, "
          f"{total_rows} player-rows upserted")
    return total_rows


# ── One-off entry points ──────────────────────────────────────────────────
def fetch_match_by_uuid(leetify_uuid: str) -> int:
    """Fetch a single match by Leetify UUID and upsert all players. Returns
    rows written."""
    md = fetch_json(f"{LEETIFY_BASE}/v2/matches/{leetify_uuid}")
    if not md:
        print(f"  no payload for {leetify_uuid}")
        return 0
    n_rows, _ = write_match_payload(md)
    print(f"  wrote {n_rows} player-rows for match {leetify_uuid}")
    return n_rows


def fetch_match_by_hltv(full_data_source_id: str) -> int:
    """Fetch a single match by full HLTV filename (e.g.
    "2394212-bc-game-vs-pain-m2-anubis.dem") and upsert all players."""
    url = (f"{LEETIFY_BASE}/v2/matches/hltv/"
           f"{urllib.parse.quote(full_data_source_id, safe='')}")
    md = fetch_json(url)
    if not md:
        print(f"  no payload for {full_data_source_id}")
        return 0
    n_rows, hltv_id = write_match_payload(md)
    print(f"  wrote {n_rows} player-rows for HLTV id {hltv_id} ({full_data_source_id})")
    return n_rows


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bootstrap-seeds", type=int,
                   help="Walk N seeds from KNOWN_SEEDS")
    g.add_argument("--match-id", type=str,
                   help="One-off fetch via /v2/matches/{leetify_uuid}")
    g.add_argument("--full-match", type=str,
                   help="One-off fetch via /v2/matches/hltv/{full_filename}")
    g.add_argument("--fill-matches", type=int,
                   help="Refetch N existing matches with <10 players via /v2/matches")
    args = ap.parse_args()

    started_at = time.monotonic()

    if args.bootstrap_seeds is not None:
        n = min(args.bootstrap_seeds, len(KNOWN_SEEDS))
        stats = bootstrap(n)

        # Densify: profile/matches only returned the seed's own stats per
        # match — refetch each HLTV-bridged match via /v2/matches/{id} to
        # get all 10 players. Cap at 150 so we don't blow past the
        # "directional sneak peek" budget.
        fill_matches(150)

        # Final report — also pull a fresh count to confirm DB write.
        total_pms = execute_query(
            "SELECT COUNT(*) AS n FROM cs2_leetify_player_match_stats"
        )[0]["n"]
        total_bridge = execute_query(
            "SELECT COUNT(*) AS n FROM cs2_player_id_bridge"
        )[0]["n"]
        hltv_bridged = execute_query(
            "SELECT COUNT(DISTINCT hltv_match_id) AS n "
            "FROM cs2_leetify_player_match_stats "
            "WHERE hltv_match_id IS NOT NULL"
        )[0]["n"]

        elapsed = time.monotonic() - started_at
        print()
        print("=" * 60)
        print(f"BOOTSTRAP COMPLETE ({elapsed:.1f}s)")
        print("=" * 60)
        print(f"  seeds fetched:                 {stats['seeds_done']}")
        print(f"  teammates seen (bridge only):  {stats['teammates_seen']}")
        print(f"  Leetify matches walked:        {stats['matches_written']}")
        print(f"  HLTV-bridged matches (unique): {hltv_bridged}")
        print(f"  new bridge rows this run:      {stats['new_bridge_rows']}")
        print(f"  total cs2_player_id_bridge:    {total_bridge}")
        print(f"  total cs2_leetify_pms rows:    {total_pms}")
        return

    if args.match_id:
        fetch_match_by_uuid(args.match_id)
        return

    if args.full_match:
        fetch_match_by_hltv(args.full_match)
        return

    if args.fill_matches is not None:
        fill_matches(args.fill_matches)
        return


if __name__ == "__main__":
    main()
