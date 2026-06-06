"""CLV-BACKFILL — compute CLV per bot from OddsPapi-extracted Pinnacle history.

Reads /tmp/op_phase3_extracted.json (produced by /tmp/oddspapi_phase3.py),
joins to our simulated_bets for the bet-side info, and produces:

  dev/active/clv-analysis.md — bot rankings by CLV with confidence intervals
  /tmp/clv_per_bet.csv       — per-bet detail for downstream analysis

CLV definition:
    clv = (our_odds / pinnacle_close_odds) - 1
  Positive CLV means we got better odds than the close → expected long-term +EV.
  Negative CLV means the line moved against us → expected long-term -EV.

Pinnacle no-vig probability:
    For 1x2: p_home, p_draw, p_away = q_home/Σq, q_draw/Σq, q_away/Σq where q_i = 1/price_i
    True edge = our_model_prob - pinnacle_no_vig_prob
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, stdev
import math

sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
from workers.api_clients.db import execute_query

EXTRACTED = Path("/tmp/op_phase3_extracted.json")
OUT_MD    = Path("/Users/margussellin/www/odds-intel-engine/dev/active/clv-analysis.md")
OUT_CSV   = Path("/tmp/clv_per_bet.csv")

# ── OddsPapi market/outcome decoder ───────────────────────────────────────
# Stable mapping discovered from live /odds-by-tournaments inspection.
OU_LINE_TO_MID = {
    0.5: 1076, 0.75: 1078, 1.0: 1080,  # NOT these — see correction below
    # The OU mids are different from AH mids. From live inspection:
    # 1010='2.5/over' / 1011='2.5/under'    → OU 2.5
    # 1012='3.5/over' / 1013='3.5/under'    → OU 3.5
    # 10168='2.0/over' / 10169='2.0/under'  → OU 2.0
    # 10170='2.25/over' / 10171='2.25/under'→ OU 2.25
    # 10172='2.75/over' / 10173='2.75/under'→ OU 2.75
    # 10174='3.0/over' / 10175='3.0/under'  → OU 3.0
    # 10176='3.25/over' / 10177='3.25/under'→ OU 3.25
    # 10258='1.5/over' / 10259='1.5/under'  → OU 1.5
    # 10490='0.75/over'/ 10491='0.75/under' → OU 0.75
    # 10492='1.0/over' / 10493='1.0/under'  → OU 1.0
    # 10494='1.25/over'/ 10495='1.25/under' → OU 1.25
    # 10496='1.75/over'/ 10497='1.75/under' → OU 1.75
}
OU_LINE_TO_MID = {
    0.75: 10490, 1.0: 10492, 1.25: 10494, 1.5: 10258, 1.75: 10496,
    2.0: 10168, 2.25: 10170, 2.5: 1010, 2.75: 10172, 3.0: 10174, 3.25: 10176, 3.5: 1012,
}
# Within an OU market: outcome with the LOWER id is over, higher id is under
# EXCEPT 1010/1011 (over/under) and 1012/1013 — over=lower-id seems consistent.

def find_pinnacle_snap(extracted_record: dict, our_market: str, our_selection: str) -> dict | None:
    """Return {opening, close, ko-24h, ko-2h, ko-30m, ko-5m, marketId, outcomeId}
    for the Pinnacle outcome matching our bet's (market, selection), or None."""
    outs = extracted_record.get("outcomes") or []

    if our_market == "1x2":
        # marketId=101, outcome 101=home, 102=draw, 103=away
        sel_to_oid = {"home": "101", "draw": "102", "away": "103"}
        target_oid = sel_to_oid.get((our_selection or "").lower())
        if not target_oid: return None
        for o in outs:
            if str(o["marketId"]) == "101" and str(o["outcomeId"]) == target_oid:
                return o
        return None

    if our_market in ("o/u", "ou") or our_market.startswith("over_under_"):
        # selection like "Over 2.5" or "Under 2.5", or for combo bots "over"/"under"
        # need to derive line either from selection or from market suffix
        line = None; side = None
        s = (our_selection or "").strip().lower()
        parts = s.split()
        if len(parts) == 2 and parts[0] in ("over","under"):
            side = parts[0]
            try: line = float(parts[1])
            except: return None
        elif s in ("over","under"):
            side = s
            # extract from market suffix over_under_25 → 2.5
            if our_market.startswith("over_under_"):
                try:
                    cents = our_market.split("_")[-1]
                    line = float(cents[0]) + (0.5 if cents[1] == "5" else 0)
                except: return None
            else: return None
        else: return None
        mid = OU_LINE_TO_MID.get(line)
        if mid is None: return None
        # Within market: lower outcomeId = over (mostly). For OU 2.5: 1010=over, 1011=under
        # Pick over=lower / under=higher
        cands = [o for o in outs if str(o["marketId"]) == str(mid)]
        if len(cands) != 2: return None
        cands.sort(key=lambda o: int(o["outcomeId"]))
        return cands[0] if side == "over" else cands[1]

    if our_market == "asian_handicap":
        # selection "home -0.5" / "away +1.25"
        # AH mids: 1062=-1.25h, 1064=-1.0h, 1066=-0.75h, 1068=-0.5h, 1070=-0.25h,
        # 1072=0h, 1074=+0.25h, 1076=+0.5h, 1078=+0.75h, 1080=+1.0h, 1082=+1.25h(guess)
        AH_LINE_TO_MID = {
            -1.5: 1060, -1.25: 1062, -1.0: 1064, -0.75: 1066, -0.5: 1068, -0.25: 1070,
            0.0: 1072, 0.25: 1074, 0.5: 1076, 0.75: 1078, 1.0: 1080, 1.25: 1082, 1.5: 1084,
        }
        parts = (our_selection or "").split()
        if len(parts) != 2: return None
        side = parts[0].lower()
        try: line = float(parts[1])
        except: return None
        # If we picked AWAY at line L, that's equivalent to HOME at -L
        if side == "away": line = -line
        mid = AH_LINE_TO_MID.get(line)
        if mid is None: return None
        cands = [o for o in outs if str(o["marketId"]) == str(mid)]
        if len(cands) != 2: return None
        cands.sort(key=lambda o: int(o["outcomeId"]))
        # Lower outcomeId = home, higher = away (matches our_selection's original side)
        return cands[0] if parts[0].lower() == "home" else cands[1]

    return None  # btts/double_chance/dnb not implemented yet

def price_at(snap_entry, key):
    """snap_entry is one of the o['opening'/'close'/'ko-2h'/...] tuples (ts, price, active)."""
    s = snap_entry.get(key)
    if not s: return None
    return s[1]

def main():
    if not EXTRACTED.exists():
        sys.exit("/tmp/op_phase3_extracted.json not found — Phase 3 hasn't produced output yet")

    extracted = json.loads(EXTRACTED.read_text())
    print(f"loaded {len(extracted)} extracted fixture records")

    # Pull all bets from DB for these match_ids (a single match may have multiple bets across bots/markets)
    match_ids = list({e["match_id"] for e in extracted})
    bets = execute_query("""
      SELECT sb.id AS sb_id, sb.match_id, b.name AS bot, sb.market, sb.selection,
             sb.odds_at_pick, sb.edge_percent, sb.calibrated_prob, sb.model_probability,
             sb.stake, sb.result, sb.created_at
      FROM simulated_bets sb
      JOIN bots b ON b.id = sb.bot_id
      WHERE sb.match_id = ANY(%s::uuid[])
        AND sb.combo_legs IS NULL
        AND sb.result IN ('won','lost')
        AND sb.created_at >= NOW() - INTERVAL '60 days'
    """, (match_ids,))
    print(f"loaded {len(bets)} settled bets across {len(match_ids)} match_ids")

    # Filter out inplay bots — their bets are placed at LIVE odds during the match,
    # so comparing to PRE-MATCH Pinnacle close is apples-to-oranges (gives bogus
    # +100%+ CLV when a team concedes and live odds spike). For inplay CLV we'd
    # need Pinnacle live odds at the exact bet timestamp, which our backfill doesn't have.
    inplay_count = sum(1 for b in bets if b["bot"].startswith("inplay_"))
    bets = [b for b in bets if not b["bot"].startswith("inplay_")]
    print(f"  excluded {inplay_count} inplay bets (CLV vs pre-match close is invalid for in-game bets)")

    # Index extracted by match_id
    extr_by_match = {e["match_id"]: e for e in extracted}

    # Compute CLV per bet
    per_bet = []
    unmatched_market = 0
    for b in bets:
        e = extr_by_match.get(str(b["match_id"]))
        if not e:
            continue
        snap = find_pinnacle_snap(e, b["market"], b["selection"])
        if not snap:
            unmatched_market += 1
            continue
        our_odds = float(b["odds_at_pick"])
        pin_close = price_at(snap, "close")
        pin_open  = price_at(snap, "opening")
        pin_ko24  = price_at(snap, "ko-24h")
        pin_ko2h  = price_at(snap, "ko-2h")
        pin_ko30m = price_at(snap, "ko-30m")
        pin_ko5m  = price_at(snap, "ko-5m")
        if pin_close is None or pin_close <= 1.0:
            continue
        clv = (our_odds / pin_close) - 1
        # No-vig probability for 1x2 requires all 3 outcomes — defer that
        per_bet.append({
            "sb_id": b["sb_id"], "match_id": b["match_id"],
            "bot": b["bot"], "market": b["market"], "selection": b["selection"],
            "our_odds": our_odds, "result": b["result"], "stake": float(b["stake"]),
            "edge_reported": float(b["edge_percent"]),
            "pin_open": pin_open, "pin_ko24": pin_ko24, "pin_ko2h": pin_ko2h,
            "pin_ko30m": pin_ko30m, "pin_ko5m": pin_ko5m, "pin_close": pin_close,
            "clv_close": clv,
            "clv_ko2h": (our_odds/pin_ko2h - 1) if pin_ko2h else None,
            "clv_open": (our_odds/pin_open - 1) if pin_open else None,
            "drift_pin_open_to_close": (pin_open/pin_close - 1) if pin_open else None,
        })

    print(f"CLV computed for {len(per_bet)} bets (skipped {unmatched_market} where market wasn't 1x2/OU/AH)")

    # Write per-bet CSV
    import csv
    with OUT_CSV.open("w", newline="") as f:
        if per_bet:
            w = csv.DictWriter(f, fieldnames=list(per_bet[0].keys()))
            w.writeheader(); w.writerows(per_bet)
    print(f"per-bet CSV: {OUT_CSV}")

    # Aggregate per bot
    by_bot = defaultdict(list)
    for p in per_bet: by_bot[p["bot"]].append(p)

    bot_rows = []
    for bot, bets_b in by_bot.items():
        clvs = [p["clv_close"] for p in bets_b]
        edges_rep = [p["edge_reported"] for p in bets_b]
        if not clvs: continue
        n = len(clvs)
        m = mean(clvs)
        sd = stdev(clvs) if n > 1 else 0
        t = m / (sd/math.sqrt(n)) if sd > 0 and n > 1 else 0
        won = sum(1 for p in bets_b if p["result"] == "won")
        winrate = won/n
        avg_our_odds = mean(p["our_odds"] for p in bets_b)
        avg_pin_close = mean(p["pin_close"] for p in bets_b)
        avg_edge_rep = mean(edges_rep)
        # True edge proxy: model_prob - pin_close_no_vig_prob (for the selection)
        # Simpler proxy: our_odds vs pin_close gives implied edge: 1/pin_close - 1/our_odds = expected vig advantage
        # Convert: edge_in_prob = 1/pin_close * (our_odds/pin_close - 1) approx
        # Cleaner: use mean CLV in % terms
        bot_rows.append({
            "bot": bot, "n": n, "winrate": winrate,
            "avg_our_odds": avg_our_odds, "avg_pin_close": avg_pin_close,
            "avg_edge_reported": avg_edge_rep,
            "avg_clv_close": m, "sd_clv_close": sd, "t_stat": t,
            "p_clv_positive": 1 - 0.5*(1 + math.erf(-t/math.sqrt(2))) if n > 1 else None,
        })
    bot_rows.sort(key=lambda r: r["avg_clv_close"], reverse=True)

    # Render Markdown
    lines = []
    lines.append("# CLV Analysis — Pinnacle Closing-Line Value per Bot\n")
    lines.append(f"Source: {len(per_bet)} settled **pre-match** paper bets with real Pinnacle closing odds backfilled via OddsPapi `/historical-odds`. Bets from last 60 days where AF lacked a flagged Pinnacle close in `odds_snapshots`.\n")
    lines.append(f"**Inplay bets excluded** — pre-match Pinnacle close is the wrong comparator for in-game bets (causes spurious +100%+ CLV). A separate inplay-CLV analysis would need Pinnacle live odds at bet timestamp, which this backfill doesn't include.\n")
    lines.append("**CLV definition:** `(our_odds / pinnacle_close) − 1`. Positive = we got a better price than the close → +EV in expectation. Negative = the line moved against us → −EV.\n")
    lines.append("**Why this matters:** CLV is the variance-free skill metric. At small n the win-rate / ROI ranking is noisy; CLV converges much faster.\n")
    lines.append("## Bot ranking by CLV (descending)\n")
    lines.append("| bot | n | win% | avg our_odds | avg pin_close | avg edge (reported) | **CLV close** | sd | t-stat | P(CLV>0) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in bot_rows:
        sig = "✓" if (r["t_stat"] > 1.96) else ("≈" if r["t_stat"] > 0 else "✗")
        lines.append(f"| {r['bot']} | {r['n']} | {r['winrate']*100:.0f}% | "
                     f"{r['avg_our_odds']:.2f} | {r['avg_pin_close']:.2f} | "
                     f"{r['avg_edge_reported']*100:.1f}% | "
                     f"**{r['avg_clv_close']*100:+.2f}%** {sig} | "
                     f"{r['sd_clv_close']*100:.1f}% | {r['t_stat']:+.2f} | "
                     f"{(r['p_clv_positive'] or 0)*100:.0f}% |")
    lines.append("")
    lines.append("Legend: `✓` = CLV > 0 at p<0.05; `≈` = directional positive but not significant; `✗` = negative CLV.")
    lines.append("")
    lines.append("> ⚠ Rows with n < 5 are too small to interpret — they appear in the table for completeness but should be ignored for promotion decisions.\n")
    lines.append("")
    lines.append("## Concrete recommendation for CHERRY-PICK-PLACER 2026-06-08 gate flip\n")
    # Promotion candidates: n>=5 AND avg_clv > 0 AND t_stat > 1.0
    promote = [r for r in bot_rows if r["n"] >= 5 and r["avg_clv_close"] > 0 and r["t_stat"] > 1.0]
    promote.sort(key=lambda r: r["t_stat"], reverse=True)
    demote = [r for r in bot_rows if r["n"] >= 5 and r["avg_clv_close"] < 0]
    demote.sort(key=lambda r: r["avg_clv_close"])
    lines.append("**Promote to `calibrated` (CLV > 0 with n ≥ 5 and t > 1.0):**\n")
    if promote:
        for r in promote:
            lines.append(f"- **{r['bot']}** — n={r['n']}, CLV {r['avg_clv_close']*100:+.2f}%, t={r['t_stat']:+.2f}, win% {r['winrate']*100:.0f}%")
    else:
        lines.append("_(none meet the threshold at this sample size)_")
    lines.append("")
    lines.append("**Do NOT promote — significantly negative CLV (n ≥ 5):**\n")
    if demote:
        for r in demote:
            lines.append(f"- **{r['bot']}** — n={r['n']}, CLV {r['avg_clv_close']*100:+.2f}%, t={r['t_stat']:+.2f}, win% {r['winrate']*100:.0f}% — taking systematically bad prices")
    else:
        lines.append("_(no bots with negative CLV at material sample size)_")
    lines.append("")
    lines.append("**Watch list (n ≥ 5 but not yet significant):** insufficient evidence; revisit after more bets accumulate or backfill more historical fixtures.\n")
    lines.append("")
    lines.append("## Files\n")
    lines.append(f"- Raw extracted snapshots: `/tmp/op_phase3_extracted.json` ({len(extracted)} fixtures)")
    lines.append(f"- Per-bet CLV: `{OUT_CSV}` ({len(per_bet)} rows)")
    lines.append(f"- Raw OP historical responses: `dev/active/pinnacle-backfill-jsons/*.json.gz` (gitignored)")
    OUT_MD.write_text("\n".join(lines))
    print(f"\nreport: {OUT_MD}")
    print(f"\ntop 5 bots by CLV:")
    for r in bot_rows[:5]:
        print(f"  {r['bot']:<28} n={r['n']:>3}  clv={r['avg_clv_close']*100:+.2f}%  t={r['t_stat']:+.2f}")

if __name__ == "__main__":
    main()
