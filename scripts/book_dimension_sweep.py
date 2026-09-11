"""BOOK-DIMENSION-SWEEP — where does each of our three bettable books price best?

Owner question (2026-09-11): take every game the three books all quote, and find
which dimensions — market, league, country, tier, odds band — each book wins.

Universe: every (fixture, market, selection, handicap_line) series quoted by ALL
THREE of Coolbet / Unibet-Site / Epicbet. Books that don't all quote it can't be
compared on it (§33 — never compare books measured on different samples).

Method notes (docs/ANALYSIS_GOTCHAS.md):
  §25  cross-book comparison must match on handicap_line — BUT only where the line
       is a real dimension. NEW TRAP found running this (2026-09-11): for every
       `over_under_*` market the line is redundant (it is already in the market
       name) and the four books DISAGREE on whether to store it — Coolbet and
       Pinnacle write handicap_line NULL, Epicbet and Unibet-Site write 2.5.
       Each book is internally consistent, so nothing looks broken; a key that
       includes handicap_line simply returns ZERO cross-book O/U pairs between
       the two camps and the comparison silently becomes 1x2-only. Live code is
       unaffected — it carries the line in the market name and never joins on it
       (checked: pick_triggers keys on (match, selection) per fixed market;
       settlement's handicap_line equality is AH-only) — so this is an ANALYSIS
       trap. Key on handicap_line for asian_handicap / *_handicap only.
  §30  one price per series per book = LATEST row at or before kickoff, never MAX
  §34  drop any quote >6h behind the freshest peer ON THE SAME FIXTURE; a dead
       series stays "newest" forever and reads as perfectly fresh
  §59  before 2026-09-11 retention left our books ONE row per series and no
       openings, so the window is deliberately short — recent + upcoming only
  §6   no literal percent sign inside a SQL string (psycopg2 reads it as a param)
  §31  league comes from matches.league_id, never teams.league_id

Two complementary measures, because they answer different questions:
  BEST-PRICE RATE  — how often this book is the one you'd take. What a
                     line-shopper feels.
  OVERROUND        — sum of implied probabilities across a complete market
                     (2-way O/U or BTTS, 3-way 1x2). Lower = systematically
                     more generous, independent of which side you happened to
                     back. What tells you if a book is actually cheap or just
                     noisy.
"""
import os, sys
from collections import defaultdict
import psycopg2
from dotenv import load_dotenv

load_dotenv()
# argv[2] = "cb-eb" runs the two-book comparison instead. It is worth having:
# Unibet-Site quotes only FIVE markets (1x2 + four O/U lines), so the three-book
# intersection is structurally capped at those and cannot say anything about the
# other ~110 markets Coolbet and Epicbet both price.
PAIR = len(sys.argv) > 2 and sys.argv[2] == "cb-eb"
BOOKS = ["Coolbet", "Epicbet"] if PAIR else ["Coolbet", "Unibet-Site", "Epicbet"]
SHORT = {"Coolbet": "CB", "Unibet-Site": "UB", "Epicbet": "EB"}


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def paired_t(diffs):
    """Paired t on the per-series price difference, in pct of the pair mean.

    §60 — compute the POWER before reporting a difference. These gaps are
    fractions of a percent on n in the low hundreds; without this every re-run
    on a new day produces a new 'answer' and none of them mean anything.
    """
    n = len(diffs)
    if n < 8:
        return None
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    if var <= 0:
        return (mean, float("inf"), n)
    return (mean, mean / ((var / n) ** 0.5), n)
STALE_H = 6.0
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 7

con = psycopg2.connect(os.environ["DATABASE_URL"])
cur = con.cursor()
cur.execute(f"""
    SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.line_key, o.bookmaker)
           o.match_id, o.market, o.selection, o.line_key,
           o.bookmaker, o.odds, o.timestamp,
           l.name, l.country, l.tier, m.date
      FROM (SELECT *,
                   -- the line is a real dimension ONLY where it is not already
                   -- encoded in the market name; see the §25 note above
                   CASE WHEN market LIKE '%%handicap%%' OR market = 'asian_handicap'
                        THEN COALESCE(handicap_line, -999) ELSE -999 END AS line_key
              FROM odds_snapshots) o
      JOIN matches m  ON m.id = o.match_id
      LEFT JOIN leagues l ON l.id = m.league_id
     WHERE o.bookmaker = ANY(%s)
       AND o.is_live = false
       AND o.timestamp <= m.date
       AND m.date > now() - interval '{DAYS_BACK} days'
       AND m.date < now() + interval '48 hours'
     ORDER BY o.match_id, o.market, o.selection, o.line_key,
              o.bookmaker, o.timestamp DESC
""", (BOOKS,))

series = defaultdict(dict)          # (match, market, selection, line) -> book -> (odds, ts)
meta = {}
for mid, mk, sel, line, book, odds, ts, lname, country, tier, ko in cur.fetchall():
    k = (mid, mk, sel, float(line))
    series[k][book] = (float(odds), ts)
    meta[k] = dict(league=lname or "?", country=country or "?", tier=tier, ko=ko, match=mid)

fresh_ts = defaultdict(lambda: None)
for k, q in series.items():
    fresh_ts[k[0]] = max([fresh_ts[k[0]]] + [v[1] for v in q.values()]) if fresh_ts[k[0]] else max(v[1] for v in q.values())

rows, dropped_stale, dropped_outlier = [], 0, 0
for k, q in series.items():
    if len(q) < len(BOOKS):
        continue
    lag = {b: (fresh_ts[k[0]] - q[b][1]).total_seconds() / 3600 for b in BOOKS}
    if max(lag.values()) > STALE_H:
        dropped_stale += 1
        continue
    spread = max(q[b][0] for b in BOOKS) / min(q[b][0] for b in BOOKS)
    if spread > 1.25:
        dropped_outlier += 1
        continue
    rows.append(dict(key=k, odds={b: q[b][0] for b in BOOKS}, **meta[k]))

W = 84
def rule(c="─"): print(c * W)

def family(mk):
    if mk.startswith("over_under_1h"): return "O/U 1st half"
    if mk.startswith("over_under"):    return f"O/U {mk.split('_')[-1][0]}.{mk.split('_')[-1][1:] or '5'}"
    if mk.startswith("team_total"):    return "team totals"
    if mk.startswith("corners"):       return "corners"
    if mk.startswith("cards"):         return "cards"
    if mk in ("1x2", "1X2"):           return "1x2"
    if mk.endswith("_1h"):             return f"{mk[:-3]} 1st half"
    return mk

def report(title, keyfn, data, min_n=40, top=14):
    buckets = defaultdict(list)
    for r in data:
        buckets[keyfn(r)].append(r)
    big = {k: v for k, v in buckets.items() if len(v) >= min_n}
    if not big:
        print(f"  (nothing with n >= {min_n})"); return
    print()
    print(f"{title}")
    rule()
    print(f"{'':<24}{'n':>6}   {'best-price rate':^{8*len(BOOKS)}}   "
          f"top-2 paired gap  (SIG = |t| >= 3, a Bonferroni-safe bar for ~18 buckets)")
    print(f"{'':<24}{'':>6}   " + " ".join(f"{SHORT[b]:>7}" for b in BOOKS))
    out = []
    for name, rs in big.items():
        wins = {b: 0 for b in BOOKS}
        edge = {b: 0.0 for b in BOOKS}
        for r in rs:
            top_o = max(r["odds"].values())
            for b in BOOKS:
                if r["odds"][b] >= top_o - 1e-9:
                    wins[b] += 1
                avg = sum(r["odds"].values()) / len(BOOKS)
                edge[b] += 100 * (r["odds"][b] - avg) / avg
        n = len(rs)
        out.append((n, name, {b: 100 * wins[b] / n for b in BOOKS},
                    {b: median([100 * (r["odds"][b] - sum(r["odds"].values())/len(BOOKS))
                                / (sum(r["odds"].values())/len(BOOKS)) for r in rs])
                     for b in BOOKS}, rs))
    for n, name, w, e, rs in sorted(out, key=lambda x: -x[0])[:top]:
        # Leader = the book you would actually take most often. Ranking by
        # "median vs the book average" instead produced ties at +0.00 and then
        # labelled the WRONG book as leader against a negative t — the metric
        # you rank on has to be the metric you report.
        order = sorted(BOOKS, key=lambda b: -w[b])
        lead, runner = order[0], order[1]
        gaps = [100 * (r["odds"][lead] - r["odds"][runner]) /
                ((r["odds"][lead] + r["odds"][runner]) / 2) for r in rs]
        t = paired_t(gaps)
        sig = "" if t is None else (
            f"  {SHORT[lead]} vs {SHORT[runner]}: med {median(gaps):+5.2f}pc "
            f"mean {t[0]:+5.2f}pc  t={t[1]:+5.1f}"
            f"{'  SIG' if abs(t[1]) >= 3.0 else '  ns '}")
        print(f"{str(name)[:23]:<24}{n:>6}   "
              + " ".join(f"{w[b]:>5.0f}pc" for b in BOOKS) + sig)


print()
rule("═")
print(f"  BOOK-DIMENSION SWEEP — {' vs '.join(BOOKS)}")
print(f"  kickoffs from {DAYS_BACK}d ago to +48h · all {len(BOOKS)} books quote the "
      f"same market+selection+line")
print(f"  {len(rows):,} comparable price series across "
      f"{len({r['match'] for r in rows}):,} fixtures "
      f"({dropped_stale:,} dropped stale >{STALE_H:.0f}h behind peers; "
      f"{dropped_outlier:,} dropped as >25 pct apart — a gap that size is a line "
      f"mismatch or a bad quote, not a price, and a few of them own any mean, §9)")
rule("═")

# ── overall ──
wins = {b: 0 for b in BOOKS}; edge = {b: 0.0 for b in BOOKS}
for r in rows:
    top_o = max(r["odds"].values()); avg = sum(r["odds"].values()) / len(BOOKS)
    for b in BOOKS:
        if r["odds"][b] >= top_o - 1e-9: wins[b] += 1
        edge[b] += 100 * (r["odds"][b] - avg) / avg
n = len(rows)
print()
print("OVERALL")
rule()
for b in sorted(BOOKS, key=lambda x: -wins[x]):
    print(f"  {b:<13} best price on {wins[b]:>6,} / {n:,} series ({100*wins[b]/n:5.1f} pct)"
          f"   mean price {edge[b]/n:+5.2f} pct vs the 3-book average")

report("BY MARKET", lambda r: family(r["key"][1]), rows, min_n=60, top=18)
report("BY COUNTRY", lambda r: r["country"], rows, min_n=60)
report("BY LEAGUE", lambda r: f"{r['country'][:9]} {r['league']}", rows, min_n=50)
report("BY LEAGUE TIER", lambda r: f"tier {r['tier']}" if r["tier"] else "tier ?", rows, min_n=60)

def band(r):
    o = sum(r["odds"].values()) / len(BOOKS)
    if o < 1.5:  return "1  odds < 1.50"
    if o < 2.0:  return "2  1.50-2.00"
    if o < 3.0:  return "3  2.00-3.00"
    if o < 5.0:  return "4  3.00-5.00"
    return          "5  odds 5.00+"
report("BY ODDS BAND (3-book average price)", band, rows, min_n=60)
print()

# ── the slice the real-money bots actually bet ────────────────────────────────
# §47's lesson in its market form: the 3.00-5.00 odds band looked like a Coolbet
# win on the 3-book (1x2-only) universe and an Epicbet win on the 2-book (all-
# market) one. Both are true; they are different markets. What matters for money
# is the exact gate the placer runs, so measure THAT, not a band average.
print()
print("THE BETS WE ACTUALLY PLACE — the placer's own gates")
rule()
for label, pred in (
    ("1x2 home, odds >= 2.80  (bot_coolbet_1x2_model_v1)",
     lambda r: r["key"][1].lower() == "1x2" and r["key"][2].lower() == "home"
               and sum(r["odds"].values()) / len(BOOKS) >= 2.80),
    ("O/U 2.5 either side, odds >= 1.80  (bot_coolbet_ou_model_v1)",
     lambda r: r["key"][1] == "over_under_25"
               and sum(r["odds"].values()) / len(BOOKS) >= 1.80),
):
    rs = [r for r in rows if pred(r)]
    if len(rs) < 20:
        print(f"  {label}: n={len(rs)} — too few to say anything"); continue
    w = {b: sum(1 for r in rs
                if r["odds"][b] >= max(r["odds"].values()) - 1e-9) for b in BOOKS}
    order = sorted(BOOKS, key=lambda b: -w[b])
    lead, runner = order[0], order[1]
    gaps = [100 * (r["odds"][lead] - r["odds"][runner]) /
            ((r["odds"][lead] + r["odds"][runner]) / 2) for r in rs]
    m, t, n = paired_t(gaps)
    print(f"  {label}")
    print(f"    n={n:,}   " + "  ".join(f"{SHORT[b]} best {100*w[b]/n:.0f} pct" for b in BOOKS))
    print(f"    {SHORT[lead]} vs {SHORT[runner]}: median {median(gaps):+.2f} pct, "
          f"mean {m:+.2f} pct, t={t:+.1f} -> "
          f"{'REAL' if abs(t) >= 3.0 else 'not distinguishable from noise'}")
