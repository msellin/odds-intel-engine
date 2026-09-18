"""In-play trigger sweep — backtest candidate triggers on the boards we ALREADY hold.

WHY THIS EXISTS. The forward test (`bot_inplay_slowstate_v1`) needs n≈3,000 to
decide, which at current volume is roughly 40 days. But an in-play bet settles on
the FINAL SCORE, which we already have — so every board snapshot in
`inplay_book_quotes` is a replayable decision point, and the whole collected
history can be scored today. This script turns "wait 40 days to discover" into
"read it this week, then let the forward test CONFIRM".

WHAT MAKES THIS DIFFERENT FROM THE 2026-09-14 SWEEP (which found nothing):
that one priced off API-Football's live aggregate — median 40 s stale and ~2pp
wider than a real book. This one prices off **Epicbet's own on-screen number**,
the price we could actually have taken. That is the entire reason the collector
was built.

THE DISCIPLINE, which is not optional here. The 2026-09-14 round produced a
+9.0% cell (n=727) that evaporated when the entry window was widened to one a bot
could really use. The lesson recorded in PRIORITY_QUEUE was: *widen a trigger to
the window a bot would really use BEFORE believing a cell.* So every candidate
here is reported with:

  * its NEIGHBOURING windows — a real effect is a plateau, a fluke is a spike;
  * a TIME SPLIT — early dates vs late, out of sample in the only axis we have;
  * a bootstrap CI clustered BY MATCH, because repeat instants of one fixture are
    one bet, not many (the collector's own dedup rule);
  * the TOTAL NUMBER OF CELLS TESTED, so a reader can weigh one significant cell
    against how many chances it had. At ~50 cells, one 95% "winner" is the
    expected yield of pure noise.

Nothing here places a bet or writes a pick. It reads and it prints.

    python3 scripts/inplay_trigger_sweep.py                 # full sweep
    python3 scripts/inplay_trigger_sweep.py --min-n 40      # only cells with n>=40
    python3 scripts/inplay_trigger_sweep.py --trigger T1    # one family, with neighbours
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from workers.api_clients.db import get_conn  # noqa: E402

PRICE_CAP_DEFAULT = 2.20
BOOTSTRAP_ROUNDS = 2000


# ── loading ──────────────────────────────────────────────────────────────────
def load_boards(days: int) -> list[dict]:
    """Every collected board instant on a SETTLED fixture, oldest first.

    One row per (fixture, instant); markets stay nested exactly as the collector
    wrote them, so a trigger here sees precisely what the live rig would see.
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT q.match_id::text, q.captured_at, q.minute,
                      q.score_home, q.score_away, q.league, q.markets,
                      m.score_home, m.score_away,
                      m.ht_score_home, m.ht_score_away
                 FROM inplay_book_quotes q
                 JOIN matches m ON m.id = q.match_id
                WHERE m.score_home IS NOT NULL AND m.score_away IS NOT NULL
                  AND q.minute IS NOT NULL
                  AND q.score_home IS NOT NULL AND q.score_away IS NOT NULL
                  AND q.captured_at > NOW() - (%s || ' days')::interval
                ORDER BY q.captured_at""",
            (days,),
        )
        return [
            {"match_id": r[0], "at": r[1], "minute": r[2], "sh": r[3], "sa": r[4],
             "league": r[5], "markets": r[6] or [], "ft_h": r[7], "ft_a": r[8],
             "ht_h": r[9], "ht_a": r[10]}
            for r in cur.fetchall()
        ]


# ── market access ────────────────────────────────────────────────────────────
def quote(markets: list[dict], fam: str, line: float | None, want: str,
          cap: float) -> float | None:
    """Unsuspended price for `want` in the (fam, line) market, or None.

    `want` is matched case-insensitively against the selection label, with
    'home'/'away' resolved positionally for 3-way markets (the collector stores
    team NAMES there, which differ per fixture)."""
    for m in markets:
        if m.get("fam") != fam:
            continue
        if line is not None:
            try:
                if abs(float(m.get("line")) - line) > 1e-6:
                    continue
            except (TypeError, ValueError):
                continue
        sels = [s for s in (m.get("sel") or [])
                if s.get("odds") and not s.get("suspended")]
        if not sels:
            return None
        if want in ("home", "away", "draw") and len(sels) == 3:
            idx = {"home": 0, "draw": 1, "away": 2}[want]
            o = float(sels[idx]["odds"])
        else:
            o = None
            for s in sels:
                if str(s.get("sel", "")).strip().lower().startswith(want):
                    o = float(s["odds"])
                    break
            if o is None:
                return None
        return o if o <= cap else None
    return None


# ── settlement ───────────────────────────────────────────────────────────────
def settled_won(row: dict, market: str, want: str, line: float | None) -> bool | None:
    """Did `want` win on the final score? None = void/unsupported."""
    th, ta = row["ft_h"], row["ft_a"]
    if market == "ou":
        total = th + ta
        if abs(total - line) < 1e-9:
            return None                      # exact line = push
        return (total > line) if want == "over" else (total < line)
    if market == "1x2":
        if want == "home":
            return th > ta
        if want == "away":
            return ta > th
        return th == ta
    if market == "btts":
        hit = th > 0 and ta > 0
        return hit if want == "yes" else not hit
    return None


# ── one trigger ──────────────────────────────────────────────────────────────
def run_trigger(boards: list[dict], *, state, lo: int, hi: int, fam: str,
                line: float | None, want: str, cap: float) -> list[dict]:
    """First qualifying instant per match — the collector's own dedup rule:
    a trigger true for 20 minutes is ONE bet, not twenty."""
    taken: dict[str, dict] = {}
    for r in boards:
        if r["match_id"] in taken:
            continue
        if not (lo <= r["minute"] <= hi):
            continue
        if not state(r["sh"], r["sa"]):
            continue
        o = quote(r["markets"], fam, line, want, cap)
        if o is None:
            continue
        w = settled_won(r, fam, want, line)
        if w is None:
            continue
        taken[r["match_id"]] = {"odds": o, "won": w, "league": r["league"],
                                "at": r["at"], "match_id": r["match_id"]}
    return list(taken.values())


def summarise(bets: list[dict]) -> dict:
    n = len(bets)
    if not n:
        return {"n": 0, "roi": 0.0, "lo": 0.0, "hi": 0.0, "wins": 0, "losses": 0,
                "avg_odds": 0.0, "roi_one_more_loss": 0.0, "ci_ok": False}
    pnl = [(b["odds"] - 1.0) if b["won"] else -1.0 for b in bets]
    roi = 100.0 * sum(pnl) / n
    rng = random.Random(12345)               # deterministic across runs
    boots = []
    for _ in range(BOOTSTRAP_ROUNDS):
        s = sum(pnl[rng.randrange(n)] for _ in range(n))
        boots.append(100.0 * s / n)
    boots.sort()
    wins = sum(1 for b in bets if b["won"])
    losses = n - wins
    avg_odds = sum(b["odds"] for b in bets) / n
    return {"n": n, "roi": roi, "wins": wins, "losses": losses,
            "avg_odds": avg_odds,
            # Sensitivity: what this cell becomes if ONE more bet had lost. For a
            # near-certainty trigger (2-goal lead at 80' priced 1.02) this is the
            # only honest risk number — see `ci_ok` below.
            "roi_one_more_loss": 100.0 * (sum(pnl) - avg_odds) / n,
            # A bootstrap resamples OBSERVED outcomes, so a cell with no losses
            # can never produce a loss and its CI is a fiction that always reads
            # "significant". Every cell this sweep first flagged as CI>0 was of
            # exactly that kind: 100% win rates at odds 1.02-1.11. Require at
            # least two observed losses before the interval means anything.
            "ci_ok": losses >= 2,
            "lo": boots[int(0.025 * BOOTSTRAP_ROUNDS)],
            "hi": boots[int(0.975 * BOOTSTRAP_ROUNDS)]}


def time_split(bets: list[dict]) -> tuple[dict, dict]:
    """Split by calendar date — the only out-of-sample axis a 3-day window has."""
    if not bets:
        return summarise([]), summarise([])
    ordered = sorted(bets, key=lambda b: b["at"])
    mid = len(ordered) // 2
    return summarise(ordered[:mid]), summarise(ordered[mid:])


# ── the grid ─────────────────────────────────────────────────────────────────
LEVEL     = lambda h, a: h == a                                  # noqa: E731
GOALLESS  = lambda h, a: h == 0 and a == 0                       # noqa: E731
LEAD1     = lambda h, a: abs(h - a) == 1                         # noqa: E731
LEAD2     = lambda h, a: abs(h - a) == 2                         # noqa: E731

STATES = {"0-0": GOALLESS, "level": LEVEL, "lead1": LEAD1, "lead2": LEAD2}

MARKETS = [
    ("ou", 2.5, "under", "U2.5"),
    ("ou", 2.5, "over",  "O2.5"),
    ("ou", 1.5, "under", "U1.5"),
    ("ou", 3.5, "over",  "O3.5"),
    ("btts", None, "no",  "BTTS-no"),
    ("1x2", None, "home", "1x2-home"),
    ("1x2", None, "draw", "1x2-draw"),
    ("1x2", None, "away", "1x2-away"),
]

WINDOWS = [(15, 29), (25, 39), (30, 44), (35, 49), (40, 54),
           (45, 59), (55, 69), (65, 79), (70, 84)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--min-n", type=int, default=25)
    ap.add_argument("--cap", type=float, default=PRICE_CAP_DEFAULT)
    ap.add_argument("--trigger", default=None,
                    help="substring of a cell label; prints its neighbouring windows")
    a = ap.parse_args()

    boards = load_boards(a.days)
    matches = {r["match_id"] for r in boards}
    print(f"loaded {len(boards):,} board instants over {len(matches)} settled fixtures")
    if not boards:
        print("nothing to sweep")
        return 0
    print(f"price cap <= {a.cap}, one bet per fixture per trigger, "
          f"bootstrap {BOOTSTRAP_ROUNDS} clustered by match\n")

    cells, tested = [], 0
    for sname, sfn in STATES.items():
        for fam, line, want, mlabel in MARKETS:
            for lo, hi in WINDOWS:
                tested += 1
                bets = run_trigger(boards, state=sfn, lo=lo, hi=hi, fam=fam,
                                   line=line, want=want, cap=a.cap)
                st = summarise(bets)
                if st["n"] < a.min_n:
                    continue
                st["label"] = f"{sname:5s} {lo:2d}'-{hi:2d}' {mlabel}"
                st["bets"] = bets
                cells.append(st)

    cells.sort(key=lambda c: c["roi"], reverse=True)
    print(f"{tested} cells tested, {len(cells)} met n>={a.min_n}\n")
    print(f"{'trigger':28s} {'n':>4s} {'w':>4s} {'ROI%':>7s} {'95% CI':>17s}  "
          f"{'early':>7s} {'late':>7s} {'-1win':>7s}")
    print("-" * 92)
    for c in cells:
        e, l = time_split(c["bets"])
        flag = "" if c["ci_ok"] else "  !CI"
        print(f"{c['label']:28s} {c['n']:4d} {c['wins']:4d} {c['roi']:7.2f} "
              f"[{c['lo']:7.2f},{c['hi']:7.2f}]  {e['roi']:7.2f} {l['roi']:7.2f} "
              f"{c['roi_one_more_loss']:7.2f}{flag}")

    pos = [c for c in cells if c["lo"] > 0 and c["ci_ok"]]
    fake = [c for c in cells if c["lo"] > 0 and not c["ci_ok"]]
    print(f"\n{len(pos)} cell(s) with a TRUSTWORTHY 95% CI strictly above zero, "
          f"out of {tested} tested.")
    print(f"Pure noise alone would be expected to yield about {0.025*tested:.1f}.")
    if fake:
        print(f"({len(fake)} further cell(s) read CI>0 but are marked !CI — fewer than two "
              "observed losses, so the interval is an artifact of resampling a sample "
              "with nothing to lose. Read the -1win column instead.)")
    if len(pos) <= 0.025 * tested:
        print("\nVERDICT: this sweep finds FEWER trustworthy positives than chance alone "
              "would produce. On this data there is no in-play edge to build a bot on.")
    elif pos:
        print("\nA cell only earns belief if its NEIGHBOURING windows agree (--trigger) "
              "and both time halves point the same way.")

    if a.trigger:
        print(f"\n=== neighbouring windows for cells matching {a.trigger!r} "
              "(plateau = real, spike = noise) ===")
        for sname, sfn in STATES.items():
            for fam, line, want, mlabel in MARKETS:
                if a.trigger.lower() not in f"{sname} {mlabel}".lower():
                    continue
                print(f"\n  {sname} / {mlabel}")
                for lo in range(15, 80, 5):
                    bets = run_trigger(boards, state=sfn, lo=lo, hi=lo + 14,
                                       fam=fam, line=line, want=want, cap=a.cap)
                    st = summarise(bets)
                    if st["n"] >= 10:
                        print(f"    {lo:2d}'-{lo+14:2d}' n={st['n']:3d} "
                              f"ROI {st['roi']:7.2f}%  CI[{st['lo']:7.2f},{st['hi']:7.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
