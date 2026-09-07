"""
COOLBET-CROSS-BOOK-SANITY-GUARD (2026-09-07).

The Coolbet fuzzy matcher accepts on name-similarity alone and has no cross-book
price check, so a wrong-fixture match writes another game's prices into
odds_snapshots. Verified 2026-09-07: ~0.3% of co-priced matches carry inverted
favourites vs Pinnacle (a different game's odds), including mainstream leagues,
and those rows feed best-price / edge / recommended_bookmaker on real-money
fixtures.

This is the price-side safety net the fuzzy fix (league corroboration) does not
provide: it re-derives, per match, whether Coolbet's 1x2 favourite is INVERTED
vs Pinnacle's by more than a threshold — the exact signature of a wrong-fixture
match — and alerts with the offending matches. It does NOT delete rows (a false
positive must never silently drop a legitimate price); it surfaces them for a
human to pull, and counts them so the fuzzy-match regression is visible.

Detect + alert only. Never raises. Modelled on coolbet_odds_freshness.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# implied-probability gap on the SAME 1x2 selection that flags a wrong fixture.
# 0.30 (30pp) is well beyond any honest Coolbet-vs-Pinnacle margin difference;
# the verified corruptions sat at 40-80pp. Env-tunable.
import os
DIVERGENCE_PP = float(os.getenv("COOLBET_SANITY_DIVERGENCE_PP", "0.30"))
# only bother when both books show a CLEAR favourite (odds gap), so a genuinely
# even match near 50/50 can't trip the inversion test on noise.
MIN_FAV_MARGIN = float(os.getenv("COOLBET_SANITY_MIN_FAV_MARGIN", "0.5"))
ALERT_DEDUP_HOURS = int(os.getenv("COOLBET_SANITY_ALERT_DEDUP_HOURS", "6"))
LOOKBACK_HOURS = int(os.getenv("COOLBET_SANITY_LOOKBACK_HOURS", "6"))
_PIPELINE = "coolbet-price-sanity"


def _find_suspects() -> list[dict]:
    """Latest Coolbet vs Pinnacle 1x2 per match (recent window); return the ones
    whose favourite is inverted with both clear, or any selection diverges by
    more than DIVERGENCE_PP. Read-only."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        WITH cb AS (
          SELECT DISTINCT ON (match_id, selection) match_id, selection, odds
            FROM odds_snapshots
           WHERE bookmaker='Coolbet' AND market='1x2'
             AND "timestamp" >= now() - (%s || ' hours')::interval
           ORDER BY match_id, selection, "timestamp" DESC),
        pin AS (
          SELECT DISTINCT ON (match_id, selection) match_id, selection, odds
            FROM odds_snapshots
           WHERE bookmaker='Pinnacle' AND market='1x2'
             AND "timestamp" >= now() - (%s || ' hours')::interval
           ORDER BY match_id, selection, "timestamp" DESC)
        SELECT cb.match_id::text AS match_id,
               max(CASE WHEN cb.selection='home' THEN cb.odds END) ch,
               max(CASE WHEN cb.selection='away' THEN cb.odds END) ca,
               max(CASE WHEN pin.selection='home' THEN pin.odds END) ph,
               max(CASE WHEN pin.selection='away' THEN pin.odds END) pa
          FROM cb JOIN pin ON cb.match_id = pin.match_id AND cb.selection = pin.selection
         GROUP BY cb.match_id
        """,
        (str(LOOKBACK_HOURS), str(LOOKBACK_HOURS)),
    )
    suspects = []
    for r in rows:
        if not all([r.get("ch"), r.get("ca"), r.get("ph"), r.get("pa")]):
            continue
        ch, ca = float(r["ch"]), float(r["ca"])
        ph, pa = float(r["ph"]), float(r["pa"])
        inverted = (ch < ca) != (ph < pa) and abs(ch - ca) > MIN_FAV_MARGIN and abs(ph - pa) > MIN_FAV_MARGIN
        gap_home = abs(1.0 / ch - 1.0 / ph)
        if inverted or gap_home > DIVERGENCE_PP:
            suspects.append({
                "match_id": r["match_id"],
                "cb": f"H{ch}/A{ca}", "pin": f"H{ph}/A{pa}",
                "inverted": inverted, "home_gap_pp": round(gap_home * 100, 1),
            })
    return suspects


def _format_alert(suspects: list[dict]) -> str:
    lines = [
        "🚨 <b>Coolbet wrong-fixture prices detected</b>",
        "",
        f"{len(suspects)} match(es) have a Coolbet 1x2 that inverts Pinnacle's "
        "favourite — the signature of a fuzzy-match false positive writing "
        "another game's odds. These feed best-price / edge / recommended_bookmaker.",
        "",
    ]
    for s in suspects[:10]:
        tag = "FAV-INVERTED" if s["inverted"] else f"{s['home_gap_pp']}pp gap"
        lines.append(f"  • match {s['match_id'][:8]}  Coolbet {s['cb']}  vs  "
                     f"Pinnacle {s['pin']}  [{tag}]")
    if len(suspects) > 10:
        lines.append(f"  … and {len(suspects) - 10} more")
    lines += [
        "",
        "These rows were NOT auto-deleted. Investigate the match_ids and pull the "
        "bad Coolbet snapshots; the root cause is COOLBET-FUZZY-MATCH cross-league "
        "matching.",
    ]
    return "\n".join(lines)


def run_coolbet_price_sanity_check(*, dry_run: bool = False) -> dict:
    """Main entry. Never raises."""
    counters = {"suspects": 0, "alert_sent": False, "dedup_skipped": False}
    try:
        from workers.api_clients.db import get_conn
        from workers.notify.telegram import send_telegram

        suspects = _find_suspects()
        counters["suspects"] = len(suspects)
        if suspects:
            log.warning("coolbet price-sanity: %d wrong-fixture suspects: %s",
                        len(suspects), [s["match_id"][:8] for s in suspects[:10]])
        if not suspects:
            return counters

        now = datetime.now(timezone.utc)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT last_alert_at FROM pipeline_health_state "
                            "WHERE pipeline_name=%s", (_PIPELINE,))
                row = cur.fetchone()
                last_alert = row[0] if row else None
                if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                    counters["dedup_skipped"] = True
                    return counters
                if dry_run:
                    counters["alert_sent"] = True
                    return counters
                tg = send_telegram(
                    _format_alert(suspects),
                    dedup_key="coolbet-price-sanity",
                    dedup_window_s=ALERT_DEDUP_HOURS * 3600,
                )
                if tg is not None:
                    counters["alert_sent"] = True
                    cur.execute(
                        """INSERT INTO pipeline_health_state
                               (pipeline_name, last_alert_at, last_alert_reason, updated_at)
                           VALUES (%s, %s, %s, now())
                           ON CONFLICT (pipeline_name) DO UPDATE
                              SET last_alert_at=EXCLUDED.last_alert_at,
                                  last_alert_reason=EXCLUDED.last_alert_reason,
                                  updated_at=now()""",
                        (_PIPELINE, now, f"{len(suspects)} wrong-fixture suspects"),
                    )
                    conn.commit()
    except Exception as e:
        log.warning("coolbet price-sanity check raised (non-fatal): %s", e)
    return counters


def main() -> int:
    import json
    print(json.dumps(run_coolbet_price_sanity_check(dry_run="--dry-run" in os.sys.argv),
                     default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
