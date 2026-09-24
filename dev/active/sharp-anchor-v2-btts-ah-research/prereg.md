# Pre-registration — sharp-anchor feasibility scan, BTTS + AH (written 2026-09-24 before outcomes)

Data: odds_snapshots rows in [KO-7h, KO] for fixtures kicking off in the last 60 days; AH full/half lines only.
Soft books: Coolbet, Epicbet, Unibet-Site, Tonybet. Anchors: AF-Pinnacle (Shin), consensus = mean of per-book Shin
over >=5 AF books (Marathonbet, 1xBet, William Hill, BetVictor, Betano, Betfair[sportsbook], Bet365, Superbet, 10Bet,
888Sport, SBO, Dafabet; never Pinnacle, never the four soft books), Betfair EXCHANGE (exchange_quotes).

Two regimes (retention §59/§64):
* CLOSE (60 d): soft book's last pre-KO quote vs anchor's last complete pre-KO market; anchor close <=60 min before KO,
  |soft ts - anchor ts| <= 60 min. Edge == CLV by construction here, so only coverage/frequency + ROI(secondary).
* DECISION (intact window only): soft book's latest quote in [KO-6h, KO-1h]; anchor = latest complete market within
  +-60 min of it (publisher ALIGN_MIN). Edge = soft_odds * P_anchor - 1. CLV = soft_odds * P_anchor_close - 1 with the
  close strictly later than the decision anchor and <= 60 min before KO.

Dedup: one leg per (fixture, book, market) — the highest-edge flagged leg (a bot places one bet per market).
Outlier guard applied BEFORE flagging: soft_odds / anchor_fair_odds - 1 <= 0.25 (1x2/BTTS §9) — reported both with and without.

Primary test: one-sided t on mean CLV > 0 (DECISION regime), per cell.
Family F (m = 51): BTTS x {Coolbet, Epicbet, Unibet-Site, Tonybet} x {consensus, exchange} x {2,3,5 pct}  = 24
                   AH   x {Coolbet, Epicbet, Tonybet} x {Pinnacle, consensus, exchange} x {2,3,5 pct}     = 27
Unibet-Site AH is not collected (#010) — excluded by construction. Untestable cells (n<10 or no data) enter Holm with p=1.
Benchmark family B (m = 24, separate Holm): 1x2 x 4 books x {Pinnacle, consensus} x {2,3,5 pct}.
Expected outcome (stated in advance): BTTS/AH frequencies of >3pct edges comparable to or lower than 1x2 at the scraped
books; CLV near 0 at fresh quotes; the tail dominated by stale/board artifacts (as #015 found for AH). No cell expected to
survive Holm at the available n.
Worth-a-forward-test rule: n_flagged(3pct) >= ~1/day and power to detect +2pct CLV (sd ~0.09 => n>=~160) within ~8 weeks.
