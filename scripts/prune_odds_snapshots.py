"""
Prune odds_snapshots to prevent DB bloat.

Two modes:

  hourly (default — research phase):
    Keep one snapshot per HOUR per (match, bookmaker, market, selection) for
    finished matches, plus all is_closing=true rows.
    Max 16 rows per combination (07-22 UTC) instead of the original 2-3.
    ~8× more storage than compact, but preserves intraday shape for
    odds_timing_analysis.py to answer when odds peak during the day.
    Switch back to compact once the timing theory is validated.

  compact (post-validation):
    Keep only the opening + closing snapshot per (match, bookmaker, market,
    selection). Minimum storage. Use once timing analysis is complete and
    you no longer need intraday shape for finished matches.

Usage:
    python scripts/prune_odds_snapshots.py               # dry run, hourly mode
    python scripts/prune_odds_snapshots.py --apply       # apply, hourly mode
    python scripts/prune_odds_snapshots.py --mode compact --apply  # back to original
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from workers.api_clients.db import execute_query, get_conn


def _build_sql(mode: str, for_count: bool) -> str:
    if mode == "compact":
        # Keep first + last + is_closing + is_opening per combination.
        cte = """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection
                           ORDER BY timestamp ASC
                       ) AS rn_first,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection
                           ORDER BY timestamp DESC
                       ) AS rn_last,
                       is_closing,
                       is_opening
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[])
            )
        """
        condition = "rn_first > 1 AND rn_last > 1 AND NOT is_closing AND NOT is_opening"
    else:
        # Hourly strategy: keep first snapshot per hour per combination + is_closing + is_opening
        cte = """
            WITH hourly AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection,
                                        EXTRACT(HOUR FROM timestamp)::int
                           ORDER BY timestamp ASC
                       ) AS rn_in_hour,
                       is_closing,
                       is_opening
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[])
            )
        """
        condition = "rn_in_hour > 1 AND NOT is_closing AND NOT is_opening"

    alias = "ranked" if mode == "compact" else "hourly"

    if for_count:
        return f"{cte} SELECT COUNT(*) AS cnt FROM {alias} WHERE {condition}"
    else:
        return f"{cte} DELETE FROM odds_snapshots WHERE id IN (SELECT id FROM {alias} WHERE {condition})"


def prune(dry_run: bool = True, mode: str = "hourly") -> int:
    mode_desc = {
        "hourly": "keep 1 snapshot/hour per (match, bookmaker, market, selection) + is_closing",
        "compact": "keep first + last snapshot per (match, bookmaker, market, selection) + is_closing",
    }
    print(f"{'[DRY RUN] ' if dry_run else ''}Pruning odds_snapshots (finished matches only)")
    print(f"Mode: {mode} — {mode_desc[mode]}")
    print()

    before = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
    before_cnt = before[0]["cnt"] if before else 0
    print(f"Total rows before: {before_cnt:,}")

    finished = execute_query("""
        SELECT DISTINCT o.match_id
        FROM odds_snapshots o
        JOIN matches m ON o.match_id = m.id
        WHERE m.status = 'finished'
    """, [])
    match_ids = [r["match_id"] for r in finished]
    print(f"Finished matches with snapshots: {len(match_ids)}")

    if not match_ids:
        print("Nothing to prune.")
        return 0

    total_deleted = 0
    batch_size = 50
    delete_sql = _build_sql(mode, for_count=False)
    count_sql = _build_sql(mode, for_count=True)

    for i in range(0, len(match_ids), batch_size):
        batch = match_ids[i:i + batch_size]

        if dry_run:
            result = execute_query(count_sql, [batch])
            batch_count = result[0]["cnt"] if result else 0
            total_deleted += batch_count
        else:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(delete_sql, (batch,))
                    batch_count = cur.rowcount
                    conn.commit()
            total_deleted += batch_count

        progress = min(i + batch_size, len(match_ids))
        print(f"  Batch {progress}/{len(match_ids)}: {'would delete' if dry_run else 'deleted'} {batch_count:,} rows")

    print()
    print(f"Total {'eligible for deletion' if dry_run else 'deleted'}: {total_deleted:,}")
    if before_cnt > 0:
        print(f"Reduction: {total_deleted / before_cnt * 100:.1f}%")

    if not dry_run:
        after = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
        after_cnt = after[0]["cnt"] if after else 0
        print(f"Rows remaining: {after_cnt:,}")

    if dry_run:
        print("\nThis was a DRY RUN. Run with --apply to actually delete.")

    print("Done.")
    return total_deleted


# DB-RETENTION-VOLUME-2026-09-06. The window was hard-coded at 30 days, which
# was survivable at ~311k rows/day. The corners/cards/first-half capture
# widening on 2026-09-05 took inflow to 8,623,272 rows/day (~28x in five days),
# and at 395 bytes/row including indexes a 30-day hot window projects to
# 258.7M rows / ~102 GB. The box has 118 GB free of 301 GB. At 7 days the same
# window is ~24 GB.
#
# Intra-day tick history older than a week is read by nothing we run: CLV and
# settlement resolve against the opening/closing anchors (which this job never
# deletes), and the model's line-velocity / drift features are computed at pick
# time, not from history.
RETENTION_DAYS = int(os.getenv("ODDS_RETENTION_DAYS", "7"))

# DB-ANCHOR-GROWTH step B (2026-09-11). `prune_old_simple` only ever looked at
# status='finished', so POSTPONED fixtures kept their entire tick history
# forever — 1,910,407 non-anchor rows when measured, with nothing to expire
# them. A postponed match has no result, so it cannot be settled, cannot carry
# CLV and cannot appear in a backtest; the only thing its ticks are good for is
# the pre-postponement price path if the fixture is later replayed under the
# same id. That is worth a longer grace than a finished match, not an exemption.
POSTPONED_RETENTION_DAYS = int(os.getenv("ODDS_POSTPONED_RETENTION_DAYS", "30"))

# ODDS-INPLAY-RETENTION (2026-09-11). In-play rows cannot survive this job's
# ordinary predicate: `is_closing` is only stamped within 15 minutes of kickoff
# and the anchorless-survivor fallback below only considers rows with
# `timestamp <= m.date`, so a post-kickoff row is neither an anchor nor a
# fallback survivor. The unqualified DELETE therefore erased 100% of in-play
# price history — which went unnoticed only because the cursor was too slow to
# reach it (155,048 is_live rows still existed, 152,327 of them already inside
# the target set).
#
# That is not a policy anybody chose, and it forecloses an in-play product: no
# model could ever train on more than a 7-day window. So in-play rows are now
# DOWNSAMPLED rather than deleted — one row per minute per price series, kept
# indefinitely. At the cadences we actually run (api-football-live 45s, a
# prospective Epicbet sweep 2-3 min) that is near-lossless while bounding the
# worst case to 60 rows/hour/series. Raw pre-existing history is preserved at
# full resolution in `odds_snapshots_inplay_archive` (migration 329).
INPLAY_BUCKET = os.getenv("ODDS_INPLAY_BUCKET", "minute")


def prune_old_simple(max_matches: int = 5000, dry_run: bool = False) -> int:
    """
    Fast backlog cleaner for finished matches older than 30 days.
    No window functions — just deletes everything that isn't is_closing or is_opening.
    Safe to re-run (already-compacted matches delete 0 rows).
    Uses SET LOCAL statement_timeout per transaction to override Supabase's 1-min default.
    Called nightly at 03:00 UTC to drain the historical backlog incrementally.
    """
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set")
        return 0

    print(f"{'[DRY RUN] ' if dry_run else ''}prune_old_simple: max_matches={max_matches}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SET LOCAL statement_timeout = '10min'")
    # ── ODDS-PRUNE-CURSOR-BUG-2026-09-06 ────────────────────────────────────
    # This was `ORDER BY id LIMIT %s` on a UUID primary key, with NO predicate
    # excluding matches that are already pruned. A UUID ordering is stable and
    # arbitrary, so the query returned THE SAME 5,000 matches every single
    # night since the job shipped, and the nightly cron drained nothing.
    #
    # Measured 2026-09-06: 151,154 finished matches older than 30 days exist,
    # so the job saw 3.3% of them — always the same 3.3%. Prunable rows inside
    # that frozen window were 8,313; prunable rows across the whole backlog were
    # 26,374,272, roughly 35% of the table and ~8 GB, all of it condemned by a
    # retention policy that was agreed and implemented at the time.
    #
    # Two changes, and the EXISTS clause is the important one: ordering by date
    # alone would still re-visit already-compacted matches forever, just in a
    # different order. Excluding matches with nothing left to prune is what
    # makes the cursor actually advance.
    # ── ODDS-PRUNE-CURSOR-ORDER-2026-09-11 ──────────────────────────────────
    # `ORDER BY m.date ASC` drained the THINNEST matches first. Measured on the
    # live backlog: 7-14d old = 2,527 matches x 3,102 prunable rows each;
    # 14-30d = 4,095 x 683; 30-90d = 1,569 x 143. Oldest-first therefore spent
    # every night on the 143-row matches and never reached the 2,527 holding
    # 72% of the 10.9M recoverable rows — the 2026-09-11 run deleted 85,913
    # rows from its full 5,000-match budget (17 per match) while ~1.8M were
    # written that day. DESC puts the fat matches first, so an interrupted or
    # rate-limited run still recovers most of what is available.
    #
    # The EXISTS clause (ODDS-PRUNE-CURSOR-BUG-2026-09-06) is what makes the
    # cursor advance at all; ordering only decides how much each night is worth.
    #
    # Postponed fixtures are included on their own longer grace — see
    # POSTPONED_RETENTION_DAYS. 'cancelled' is deliberately NOT included: those
    # rows are few and a cancelled fixture never comes back, so the ordinary
    # finished-match path never sees them and they are left for a later pass.
    cur.execute("""
        SELECT m.id FROM matches m
        WHERE (
                 (m.status = 'finished'  AND m.date < NOW() - make_interval(days => %s))
              OR (m.status = 'postponed' AND m.date < NOW() - make_interval(days => %s))
              )
          AND EXISTS (
                SELECT 1 FROM odds_snapshots o
                 WHERE o.match_id = m.id
                   AND NOT COALESCE(o.is_closing, false)
                   AND NOT COALESCE(o.is_opening, false)
              )
        ORDER BY m.date DESC
        LIMIT %s
    """, (RETENTION_DAYS, POSTPONED_RETENTION_DAYS, max_matches))
    match_ids = [str(r[0]) for r in cur.fetchall()]
    conn.commit()

    print(f"  Matches fetched: {len(match_ids):,}")
    if not match_ids:
        print("  Nothing to do.")
        conn.close()
        return 0

    BATCH = 100
    total_deleted = 0

    for i in range(0, len(match_ids), BATCH):
        batch = match_ids[i:i + BATCH]
        attempt = 0
        while attempt < 5:
            try:
                cur.execute("SET LOCAL statement_timeout = '10min'")
                if not dry_run:
                    # DB-RETENTION-ANCHORLESS-2026-09-06. This used to delete
                    # every non-anchor row unconditionally, which silently
                    # destroyed the ENTIRE price history of any match that has
                    # no is_closing / is_opening row at all. That is not rare:
                    # measured across 10,664 finished matches with odds in 30
                    # days, 1,151 (10.8%) carry NO anchor of either kind, and 11
                    # of 284 bet-carrying matches are among them. For those the
                    # job was not compacting history, it was erasing it — and
                    # since settlement resolves closing odds on is_closing=TRUE,
                    # an anchorless match has no CLV either, so nothing would
                    # ever have noticed.
                    #
                    # Keep, per (market, selection, bookmaker, handicap_line),
                    # the latest PRE-KICKOFF row — a closing price in all but
                    # the flag. A match can now always be re-priced.
                    cur.execute("""
                        DELETE FROM odds_snapshots o
                        WHERE o.match_id = ANY(%s::uuid[])
                          AND NOT COALESCE(o.is_closing, false)
                          AND NOT COALESCE(o.is_opening, false)
                          -- ODDS-INPLAY-RETENTION-2026-09-11: in-play rows are
                          -- downsampled by _prune_inplay_downsample, never
                          -- deleted here. Without this clause they were ALL
                          -- deleted, because a post-kickoff row can satisfy
                          -- neither anchor flag nor the pre-kickoff fallback
                          -- below. Losing every in-play row, silently.
                          -- NB: never write a literal percent sign inside these
                          -- SQL strings. psycopg2 parses it as a parameter
                          -- placeholder, so one in a COMMENT raised
                          -- IndexError on every batch and the job reported
                          -- "0 rows" as if there were nothing to prune.
                          AND NOT COALESCE(o.is_live, false)
                          AND o.id <> (
                                SELECT k.id FROM odds_snapshots k
                                 JOIN matches m ON m.id = k.match_id
                                WHERE k.match_id  = o.match_id
                                  AND k.market    = o.market
                                  AND k.selection = o.selection
                                  AND k.bookmaker = o.bookmaker
                                  AND k.handicap_line IS NOT DISTINCT FROM o.handicap_line
                                  AND k.timestamp <= m.date
                                ORDER BY k.timestamp DESC
                                LIMIT 1
                          )
                    """, (batch,))
                    deleted = cur.rowcount
                    conn.commit()
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM odds_snapshots o
                        WHERE o.match_id = ANY(%s::uuid[])
                          AND NOT COALESCE(o.is_closing, false)
                          AND NOT COALESCE(o.is_opening, false)
                          -- ODDS-INPLAY-RETENTION-2026-09-11: in-play rows are
                          -- downsampled by _prune_inplay_downsample, never
                          -- deleted here. Without this clause they were ALL
                          -- deleted, because a post-kickoff row can satisfy
                          -- neither anchor flag nor the pre-kickoff fallback
                          -- below. Losing every in-play row, silently.
                          -- NB: never write a literal percent sign inside these
                          -- SQL strings. psycopg2 parses it as a parameter
                          -- placeholder, so one in a COMMENT raised
                          -- IndexError on every batch and the job reported
                          -- "0 rows" as if there were nothing to prune.
                          AND NOT COALESCE(o.is_live, false)
                          AND o.id <> (
                                SELECT k.id FROM odds_snapshots k
                                 JOIN matches m ON m.id = k.match_id
                                WHERE k.match_id  = o.match_id
                                  AND k.market    = o.market
                                  AND k.selection = o.selection
                                  AND k.bookmaker = o.bookmaker
                                  AND k.handicap_line IS NOT DISTINCT FROM o.handicap_line
                                  AND k.timestamp <= m.date
                                ORDER BY k.timestamp DESC
                                LIMIT 1
                          )
                    """, (batch,))
                    deleted = cur.fetchone()[0]
                    conn.rollback()
                total_deleted += deleted
                break
            except psycopg2.errors.QueryCanceled:
                conn.rollback()
                attempt += 1
                if len(batch) > 10:
                    batch = batch[:max(len(batch) // 2, 10)]
                else:
                    print(f"  batch {i} skipped after {attempt} timeouts")
                    break
            except Exception as e:
                conn.rollback()
                print(f"  batch {i} error: {type(e).__name__}: {e}")
                break

    print(f"  {'Would delete' if dry_run else 'Deleted'}: {total_deleted:,} rows from {len(match_ids):,} matches")

    # ODDS-INPLAY-RETENTION-2026-09-11 — downsample instead of delete. Runs on
    # the same match cursor so it inherits the same batching and grace period.
    inplay_deleted = _prune_inplay_downsample(cur, conn, match_ids, dry_run=dry_run)
    if inplay_deleted:
        print(f"  in-play {'would downsample' if dry_run else 'downsampled'}: "
              f"{inplay_deleted:,} sub-minute rows dropped")

    conn.close()
    return total_deleted + inplay_deleted


def _prune_inplay_downsample(cur, conn, match_ids: list[str], dry_run: bool = False) -> int:
    """Thin in-play rows to one per minute per price series. Never deletes the
    last remaining row of a series.

    In-play odds are the one class this job must NOT treat like pre-match ticks.
    A post-kickoff row can never carry `is_closing` (stamped only within 15
    minutes of kickoff) and can never be the anchorless fallback survivor (that
    clause requires `timestamp <= m.date`), so the ordinary predicate condemned
    every in-play row ever written. Nobody chose that, and it would cap any
    future in-play model's training window at 7 days.

    Keeping one row per minute per (match, bookmaker, market, selection,
    handicap_line) preserves the SHAPE of the move — which is what an in-play
    model needs — at a bounded 60 rows/hour/series. At the cadences we run it is
    near-lossless: api-football-live polls at 45s, a prospective Epicbet in-play
    sweep at 2-3 min would lose nothing at all.

    `DISTINCT ON` picks the EARLIEST row in each minute deliberately: a
    downsampled series should read as "the price as at 61'00", not as a value
    stamped at an arbitrary offset inside the minute.
    """
    if not match_ids:
        return 0

    sql = """
        DELETE FROM odds_snapshots o
        WHERE o.match_id = ANY(%s::uuid[])
          AND COALESCE(o.is_live, false)
          AND o.id NOT IN (
                SELECT DISTINCT ON (k.match_id, k.bookmaker, k.market,
                                    k.selection, k.handicap_line,
                                    date_trunc('minute', k.timestamp))
                       k.id
                  FROM odds_snapshots k
                 WHERE k.match_id = ANY(%s::uuid[])
                   AND COALESCE(k.is_live, false)
                 ORDER BY k.match_id, k.bookmaker, k.market, k.selection,
                          k.handicap_line, date_trunc('minute', k.timestamp),
                          k.timestamp ASC
          )
    """
    count_sql = sql.replace("DELETE FROM odds_snapshots o",
                            "SELECT COUNT(*) FROM odds_snapshots o", 1)

    total = 0
    BATCH = 100
    for i in range(0, len(match_ids), BATCH):
        batch = match_ids[i:i + BATCH]
        try:
            cur.execute("SET LOCAL statement_timeout = '10min'")
            if dry_run:
                cur.execute(count_sql, (batch, batch))
                total += cur.fetchone()[0]
                conn.rollback()
            else:
                cur.execute(sql, (batch, batch))
                total += cur.rowcount
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  in-play batch {i} skipped: {type(e).__name__}: {e}")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    parser.add_argument("--mode", choices=["hourly", "compact", "simple_old"], default="hourly",
                        help="hourly=keep 1/hour; compact=keep first+last; simple_old=backlog drain (>30d, no window fn)")
    parser.add_argument("--max-matches", type=int, default=5000,
                        help="Max matches to process (simple_old mode only)")
    args = parser.parse_args()
    if args.mode == "simple_old":
        prune_old_simple(max_matches=args.max_matches, dry_run=not args.apply)
    else:
        prune(dry_run=not args.apply, mode=args.mode)
