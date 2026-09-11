"""MIRROR-BOOK-SHOOTOUT — where would today's mirror picks have priced best?

Question (owner, 2026-09-11): if we had placed every mirror-bot pick of today at
Coolbet, at Unibet and at Epicbet, and every one of them won, which book pays most?

Method, and why each choice (docs/ANALYSIS_GOTCHAS.md):
  * dedupe via `shadow_bets_unique`      (§5 — the 30-min refresh writes ~16-50 copies
                                          per pick and the duplication is outcome-correlated)
  * one price per (pick, book) = the LATEST row at or before kickoff
                                         (§30 — MAX over the raw history is a high-water
                                          mark, not an offer; §29 — execution price is the
                                          closing line)
  * lowercase both sides of the join key (§23 — shadow_bets stores '1x2' AND '1X2',
                                          odds_snapshots only '1x2'; the case split
                                          itself is load-bearing, so don't migrate it)
  * Unibet = 'Unibet-Site'               (CLAUDE.md — 'Unibet-Kambi' left the feed on
                                          2026-09-06, 38 pct of its prices read HIGHER
                                          than the site; staking on them is staking on a
                                          price that does not exist)
  * report the ALL-THREE-QUOTED subset separately (§33 — never compare books measured
                                          on different samples) AND per-book coverage
  * report each book's snapshot lag vs its peers on the same fixture
                                         (§34 — a dead feed's last row stays 'newest'
                                          forever and looks perfectly fresh)
No percent signs in SQL strings (§6).
"""
import os, sys
from collections import defaultdict
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = today only
BOOKS = ["Coolbet", "Unibet-Site", "Epicbet"]
MIRRORS = ("bot_coolbet_1x2_model_v1", "bot_coolbet_ou_model_v1")
STAKE = 10.0
# §34 — a quote this far behind its peers ON THE SAME FIXTURE is a dead series,
# not an offer. 6h is the threshold that entry landed on for the Coolbet outage.
STALE_H = 6.0

con = psycopg2.connect(os.environ["DATABASE_URL"])
cur = con.cursor()

if DAYS == 0:
    window = "m.date >= date_trunc('day', now()) AND m.date < date_trunc('day', now()) + interval '1 day'"
else:
    window = f"m.date >= now() - interval '{DAYS} days' AND m.date < now()"

cur.execute(f"""
    SELECT sbu.id, sbu.bot_name, sbu.match_id, sbu.market, sbu.selection,
           sbu.odds_at_pick_live, sbu.odds_at_pick, sbu.recommended_bookmaker,
           sbu.result, m.date,
           ht.name, at.name, l.name, l.country
      FROM shadow_bets_unique sbu
      JOIN matches m  ON m.id = sbu.match_id
      JOIN teams ht   ON ht.id = m.home_team_id
      JOIN teams at   ON at.id = m.away_team_id
      LEFT JOIN leagues l ON l.id = m.league_id
     WHERE sbu.bot_name IN {MIRRORS}
       AND {window}
     ORDER BY m.date, sbu.bot_name
""")
picks = cur.fetchall()
if not picks:
    print("no mirror picks in window"); sys.exit(0)

match_ids = [str(x) for x in {p[2] for p in picks}]

# latest quote per (match, market, selection, book) at or before kickoff
cur.execute("""
    SELECT DISTINCT ON (o.match_id, lower(o.market), lower(o.selection), o.bookmaker)
           o.match_id, lower(o.market), lower(o.selection), o.bookmaker,
           o.odds, o.timestamp
      FROM odds_snapshots o
      JOIN matches m ON m.id = o.match_id
     WHERE o.match_id = ANY(%s::uuid[])
       AND o.bookmaker = ANY(%s)
       AND o.is_live = false
       AND o.timestamp <= m.date
     ORDER BY o.match_id, lower(o.market), lower(o.selection), o.bookmaker,
              o.timestamp DESC
""", (match_ids, BOOKS))
quote = {}
for mid, mk, sel, book, odds, ts in cur.fetchall():
    quote[(mid, mk, sel, book)] = (float(odds), ts)

rows, missing = [], defaultdict(int)
for (_id, bot, mid, mk, sel, o_live, o_pick, recbook, result, ko,
     home, away, league, country) in picks:
    k = (mid, mk.lower(), sel.lower())
    q = {b: quote.get(k + (b,)) for b in BOOKS}
    for b in BOOKS:
        if q[b] is None:
            missing[b] += 1
    rows.append(dict(bot=bot, ko=ko, home=home, away=away, league=league,
                     country=country, market=mk, selection=sel, result=result,
                     taken=float(o_live or o_pick or 0), recbook=recbook, q=q,
                     lag={}))

W = 78
def rule(c="─"): print(c * W)

print()
rule("═")
import datetime as _dt
print(f"  MIRROR-BOOK SHOOTOUT — "
      f"{'kickoffs today (' + _dt.datetime.now(_dt.UTC).strftime('%Y-%m-%d') + ' UTC)' if DAYS == 0 else f'last {DAYS} days'}")
print(f"  {len(rows)} deduped picks · bot_coolbet_1x2_model_v1 + bot_coolbet_ou_model_v1")
print(f"  Assumption: EVERY pick wins. EUR {STAKE:.0f} flat per pick.")
rule("═")
print()

hdr = f"{'KICK':>5}  {'MATCH':<32} {'PICK':<12} {'CB':>13} {'UB':>13} {'EB':>13}"
print(hdr); rule()
for r in rows:
    pick = (f"{r['selection'].title()}" if r["market"].lower() in ("1x2", "1X2".lower())
            else f"{r['market'].replace('over_under_','O/U ').replace('25','2.5').replace('35','3.5')} {r['selection']}")
    fresh_ts = max(r["q"][x][1] for x in BOOKS if r["q"][x])
    cells = []
    for b in BOOKS:
        if r["q"][b] is None:
            cells.append(f"{'—':>13}")
        else:
            lag = (fresh_ts - r["q"][b][1]).total_seconds() / 3600
            r["lag"][b] = lag
            flag = " !!" if lag > STALE_H else "   "
            cells.append(f"{r['q'][b][0]:>6.2f} {lag:4.1f}h{flag}"[:13].rjust(13))
    match = f"{r['home'][:15]} v {r['away'][:14]}"
    print(f"{r['ko'].strftime('%H:%M'):>5}  {match:<32} {pick:<12} {' '.join(cells)}")
print()

# ── payout, on the subset where ALL THREE quote (§33) ──
full = [r for r in rows if all(r["q"][b] for b in BOOKS)]
print(f"ALL-THREE-QUOTED SUBSET — {len(full)} of {len(rows)} picks "
      f"(the only fair head-to-head)")
rule()
if full:
    tot = {b: sum(r["q"][b][0] for r in full) * STAKE for b in BOOKS}
    staked = len(full) * STAKE
    best_book = max(tot, key=tot.get)
    for b in sorted(BOOKS, key=lambda x: -tot[x]):
        won = sum(1 for r in full if r["q"][b][0] == max(r["q"][x][0] for x in BOOKS))
        delta = tot[b] - tot[best_book]
        print(f"  {b:<12} return EUR {tot[b]:8.2f}   profit EUR {tot[b]-staked:7.2f}   "
              f"ROI {100*(tot[b]-staked)/staked:6.2f} pct   best price on {won}/{len(full)}"
              f"{'' if b == best_book else f'   ({delta:+.2f} vs {best_book})'}")
    line_shop = sum(max(r['q'][b][0] for b in BOOKS) for r in full) * STAKE
    print(f"  {'(line-shop)':<12} return EUR {line_shop:8.2f}   profit EUR {line_shop-staked:7.2f}   "
          f"ROI {100*(line_shop-staked)/staked:6.2f} pct   <- take the best of the three each time")
    print(f"  {'(vs taken)':<12} the price the bot actually recorded: EUR "
          f"{sum(r['taken'] for r in full)*STAKE:.2f}")
print()

# ── the same table again, with stale quotes struck out (§34) ──
def payout(subset, books, label):
    if not subset:
        print(f"  {label}: no picks survive"); return
    staked = len(subset) * STAKE
    tot = {b: sum(subset_q(r, b) for r in subset) * STAKE for b in books}
    best_book = max(tot, key=tot.get)
    for b in sorted(books, key=lambda x: -tot[x]):
        print(f"  {b:<12} return EUR {tot[b]:8.2f}   profit EUR {tot[b]-staked:7.2f}   "
              f"ROI {100*(tot[b]-staked)/staked:6.2f} pct"
              f"{'   <- best' if b == best_book else f'   ({tot[b]-tot[best_book]:+.2f})'}")

def subset_q(r, b):
    return r["q"][b][0]

fresh_full = [r for r in rows
              if all(r["q"][b] and r["lag"].get(b, 99) <= STALE_H for b in BOOKS)]
print(f"FRESH-ONLY SUBSET — {len(fresh_full)} of {len(rows)} picks where all three books "
      f"quote AND no quote is >{STALE_H:.0f}h behind its peers")
rule()
payout(fresh_full, BOOKS, "fresh")
print()

# ── the comparison today's data CAN support: the books that quote EVERY pick.
# No subsetting, no coverage confound, no stale-quote rescue needed.
full_cover = [b for b in BOOKS if missing[b] == 0]
if len(full_cover) >= 2:
    print(f"FULL-SAMPLE HEAD-TO-HEAD — {', '.join(full_cover)} quote all {len(rows)} picks")
    rule()
    staked = len(rows) * STAKE
    tot = {b: sum(r["q"][b][0] for r in rows) * STAKE for b in full_cover}
    lead = max(tot, key=tot.get)
    for b in sorted(full_cover, key=lambda x: -tot[x]):
        wins = sum(1 for r in rows
                   if r["q"][b][0] == max(r["q"][x][0] for x in full_cover))
        print(f"  {b:<12} return EUR {tot[b]:8.2f}   profit EUR {tot[b]-staked:7.2f}   "
              f"ROI {100*(tot[b]-staked)/staked:6.2f} pct   best price on {wins}/{len(rows)}"
              f"{'   <- best' if b == lead else f'   ({tot[b]-tot[lead]:+.2f})'}")
    shop = sum(max(r["q"][b][0] for b in full_cover) for r in rows) * STAKE
    print(f"  {'(line-shop)':<12} return EUR {shop:8.2f}   profit EUR {shop-staked:7.2f}   "
          f"ROI {100*(shop-staked)/staked:6.2f} pct   "
          f"= EUR {shop-tot[lead]:+.2f} vs always-{lead} "
          f"({100*(shop-tot[lead])/(tot[lead]-staked):+.1f} pct more profit)")
    print()

print("COVERAGE — how many of the 9 picks each book quotes at all")
rule()
for b in BOOKS:
    have = len(rows) - missing[b]
    print(f"  {b:<12} {have}/{len(rows)} quoted"
          + (f"   ({missing[b]} missing — cannot be bet there at any price)" if missing[b] else ""))
print()

# ── §34 feed-lag check: is any book's 'latest' actually a dead feed? ──
print("FEED LAG — each book's quote age vs the freshest quote on the SAME fixture")
rule()
for b in BOOKS:
    lags = []
    for r in rows:
        if r["q"][b] is None:
            continue
        peers = [r["q"][x][1] for x in BOOKS if r["q"][x]]
        lags.append((max(peers) - r["q"][b][1]).total_seconds() / 3600)
    if lags:
        print(f"  {b:<12} median {sorted(lags)[len(lags)//2]:5.2f}h behind the "
              f"freshest peer   max {max(lags):5.2f}h")
print()
