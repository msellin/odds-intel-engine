"""
INPLAY-STAKE-5 retroactive normalization — multiply historical inplay bets'
stake (1.0 → 5.0) and pnl (× 5) so the /performance headline ROI reflects
inplay strategies at their real go-forward weight without waiting weeks for
new bets to dilute the €1 history.

Affects bots named `inplay_a` through `inplay_q` (see workers/jobs/inplay_bot.py
INPLAY_BOTS). Pre-match bots untouched.

Steps:
  1. Idempotency guard — abort if any settled inplay bet already has stake > 1.0
     (means the script ran before).
  2. Snapshot affected rows to `simulated_bets_pre_inplay_normalize_2026_05_17`
     (audit trail; created in `--apply` mode).
  3. UPDATE simulated_bets SET
       stake = stake * 5,
       pnl   = CASE WHEN result IN ('won','lost') THEN pnl * 5 ELSE pnl END
     WHERE bot_id is an inplay bot
  4. Recompute simulated_bets.bankroll_after per inplay bot:
       starting_bankroll + running_sum(pnl) over (order by created_at, id)
  5. Recompute bots.current_bankroll = starting_bankroll + sum(pnl)
  6. Trigger settlement.write_dashboard_cache so /performance picks up the new
     headline numbers within minutes (otherwise it waits for the next
     scheduled 30-min refresh).

Run:
  python scripts/normalize_inplay_stake_to_5.py            (dry-run, default)
  python scripts/normalize_inplay_stake_to_5.py --apply    (execute)
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

SNAPSHOT_TABLE = "simulated_bets_pre_inplay_normalize_2026_05_17"
MULTIPLIER = 5.0


def scan() -> tuple[list[dict], list[dict]]:
    """Return (per-bot summary, list of inplay bot ids)."""
    summary = execute_query(
        """
        SELECT b.id::text AS bot_id, b.name, b.starting_bankroll, b.current_bankroll,
               COUNT(sb.id)                                               AS total_bets,
               COUNT(sb.id) FILTER (WHERE sb.result = 'won')              AS won,
               COUNT(sb.id) FILTER (WHERE sb.result = 'lost')             AS lost,
               COUNT(sb.id) FILTER (WHERE sb.result = 'void')             AS void,
               COUNT(sb.id) FILTER (WHERE sb.result NOT IN
                   ('won','lost','void') OR sb.result IS NULL)            AS pending,
               COALESCE(SUM(sb.stake), 0)                                 AS total_stake,
               COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN
                   ('won','lost')), 0)                                    AS total_pnl,
               COALESCE(MAX(sb.stake), 0)                                 AS max_stake
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        WHERE b.name LIKE 'inplay%%'
        GROUP BY b.id, b.name, b.starting_bankroll, b.current_bankroll
        ORDER BY b.name
        """,
        [],
    )
    bot_ids = [r["bot_id"] for r in summary if r["total_bets"]]
    return summary or [], bot_ids


def already_normalized(summary: list[dict]) -> bool:
    """If any inplay bot has a settled bet stake > 1.0, the script ran before."""
    for r in summary:
        if float(r.get("max_stake") or 0) > 1.0:
            return True
    return False


def render_summary(summary: list[dict]):
    print(f"{'bot':<15} {'bets':>5} {'won':>4} {'lost':>5} {'void':>5} {'pend':>5} "
          f"{'stake':>9} {'pnl':>8} {'bankroll':>9}")
    print("-" * 86)
    tot = {"total_bets": 0, "won": 0, "lost": 0, "void": 0, "pending": 0,
           "total_stake": 0.0, "total_pnl": 0.0}
    for r in summary:
        n = int(r["total_bets"] or 0)
        print(f"{r['name']:<15} {n:>5d} {int(r['won'] or 0):>4d} "
              f"{int(r['lost'] or 0):>5d} {int(r['void'] or 0):>5d} "
              f"{int(r['pending'] or 0):>5d} "
              f"{float(r['total_stake'] or 0):>9.2f} {float(r['total_pnl'] or 0):>+8.2f} "
              f"{float(r['current_bankroll'] or 0):>9.2f}")
        for k in tot:
            tot[k] += float(r[k] or 0) if k in ("total_stake", "total_pnl") else int(r[k] or 0)
    print("-" * 86)
    print(f"{'TOTAL':<15} {tot['total_bets']:>5d} {tot['won']:>4d} {tot['lost']:>5d} "
          f"{tot['void']:>5d} {tot['pending']:>5d} "
          f"{tot['total_stake']:>9.2f} {tot['total_pnl']:>+8.2f}")
    print()
    print(f"After {MULTIPLIER:.0f}× normalization:")
    print(f"  Total stake: {tot['total_stake']:.2f} → {tot['total_stake'] * MULTIPLIER:.2f}")
    print(f"  Total pnl:   {tot['total_pnl']:+.2f} → {tot['total_pnl'] * MULTIPLIER:+.2f}")


def apply_normalization(bot_ids: list[str]):
    """Multiply stake (all) and pnl (settled only) by MULTIPLIER, then recompute
    bankroll_after + bots.current_bankroll for each affected bot.

    Pulls starting_bankroll from the bots table (no hardcoded 1000)."""
    if not bot_ids:
        print("No inplay bots with bets — nothing to do.")
        return

    # 1. Snapshot for audit
    execute_write(
        f"""
        CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} AS
        SELECT * FROM simulated_bets WHERE bot_id = ANY(%s::uuid[]);
        """,
        [bot_ids],
    )
    snap_count = execute_query(f"SELECT COUNT(*) AS n FROM {SNAPSHOT_TABLE}", [])[0]["n"]
    print(f"  Snapshot: {snap_count} rows saved to {SNAPSHOT_TABLE}")

    # 2. Multiply stake and pnl
    affected = execute_write(
        """
        UPDATE simulated_bets
        SET stake = stake * %s,
            pnl   = CASE WHEN result IN ('won','lost') THEN pnl * %s ELSE pnl END
        WHERE bot_id = ANY(%s::uuid[])
        """,
        [MULTIPLIER, MULTIPLIER, bot_ids],
    )
    print(f"  Multiplied stake (and pnl on settled rows) for {affected} bets")

    # 3. Recompute bankroll_after per bot — running sum of pnl in pick_time order
    for bot_id in bot_ids:
        execute_write(
            """
            WITH ranked AS (
              SELECT sb.id,
                     SUM(COALESCE(sb.pnl, 0)) OVER (
                       ORDER BY sb.created_at, sb.id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                     ) AS running_pnl
              FROM simulated_bets sb
              WHERE sb.bot_id = %s::uuid
            )
            UPDATE simulated_bets sb
            SET bankroll_after = (
              SELECT b.starting_bankroll + ranked.running_pnl
              FROM ranked
              JOIN bots b ON b.id = sb.bot_id
              WHERE ranked.id = sb.id
            )
            WHERE sb.bot_id = %s::uuid
            """,
            [bot_id, bot_id],
        )

    # 4. Recompute current_bankroll per bot
    for bot_id in bot_ids:
        execute_write(
            """
            UPDATE bots
            SET current_bankroll = starting_bankroll + COALESCE((
              SELECT SUM(pnl) FROM simulated_bets
              WHERE bot_id = %s::uuid AND pnl IS NOT NULL
            ), 0)
            WHERE id = %s::uuid
            """,
            [bot_id, bot_id],
        )
    print(f"  Recomputed bankroll_after + current_bankroll for {len(bot_ids)} inplay bots")

    # 5. Refresh dashboard_cache so /performance reflects the new picture
    try:
        from workers.jobs.settlement import write_dashboard_cache
        write_dashboard_cache()
    except Exception as e:
        print(f"  [yellow]write_dashboard_cache failed (non-critical, next refresh covers it): {e}[/yellow]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    p.add_argument(
        "--force", action="store_true",
        help="Skip the idempotency guard (re-normalization will compound — only use if you know why)"
    )
    args = p.parse_args()

    mode = "[APPLY]" if args.apply else "[DRY RUN]"
    print(f"{mode} INPLAY-STAKE-5 normalization (1.0 → {MULTIPLIER:.1f})\n")

    summary, bot_ids = scan()
    if not summary:
        print("No inplay bots found.")
        return

    render_summary(summary)

    if already_normalized(summary) and not args.force:
        print()
        print("[ABORT] At least one inplay bet has stake > 1.0 — the script has "
              "already been applied. Re-running would compound the multiplier. "
              "Use --force to override (rarely correct).")
        sys.exit(1)

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to execute.")
        return

    print("\nApplying...")
    apply_normalization(bot_ids)
    print("\nDone.")


if __name__ == "__main__":
    main()
