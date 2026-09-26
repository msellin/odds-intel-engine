# #191 context (2026-09-26)

Owner: /admin/bots is messy; /performance bots are understood; everything else is a mystery. Wants a strict OWN vs PICKS
split, OWN bots per market × method (not per book) priced at Estonian-reachable books, MODEL and SHARP both, clean up the
rest, use gathered data to get/validate ideas. Sceptical about an "instruments" block — keep only if clean and intuitive.

Known facts going in:
- #172: no bot's value survives Holm at Estonian books; per-book Coolbet/Unibet sharp triggers the only positive line (+1.7/+2.4%).
- #150: independent CLV of sharp triggers ~+2.6%, undetermined; "beats close" vs Pinnacle is circular (§85) and ~2 h stale (#188).
- #182 filter study: only picks in the LAST 3 H before kick-off hold vs the independent close (+4.9%, n 190); flat ROI −18% ±10.
- #186: Estonian books are fresher than AF's copy of the global market, not better informed.
- Guards live on the OWN board/bot: v2 anchor, market split (match-wide), de-vig robustness (Shin AND power), 60-min freshness.
- Existing OWN bot: bot_own_1x2_v1 (1X2 · SHARP · last 3 h · best Estonian book), migration 473/477.

Decisions:
- 2026-09-26 owner: OWN bots are NOT take-overs. PICKS bots can never become OWN bots (they price at global books).
  Each OWN market × method bot starts from CANDIDATE rules, is BACKTESTED re-priced at the best Estonian book at pick
  time (point-in-time), and IMPROVED (floors, timing window, #182 guards, model+sharp agreement) on discovery data,
  confirmed on holdout. MODEL bots must use the LATEST models underneath (1X2 NEW+ r1x2_comb_v1, O/U ou_comb_v1);
  SHARP bots the v2 anchor.
- Instruments block only if it is clean and intuitive (owner sceptical).
Pending: owner approval of the audit table.
Next steps: see own-bot-lineup-tasks.md.

## OWN research results (2026-09-26/27) — the four "invent, don't copy" ideas
| Idea | Verdict | Key number | File |
|---|---|---|---|
| Promo EV | **POSITIVE** — build | ~€150–500/month; Coolbet Football Combo Club alone ~€75–255 (3% top-5 margin, free bet ≈ 0.65–0.74 of face); +10% profit boosts LOSE (break-even +12–16%) | own-research-promo-ev.md |
| Local lag vs live exchange | negative | 0 fires on 456 matches; books follow the exchange within their first re-scrape (Unibet 5, Tonybet 7, Epicbet 13, Coolbet 16 min); margin > lag | own-research-local-lag-live.md |
| Cross-book arbitrage | negative | ~5 clean arbs/day, ~1.1% margin, €60–240/month at €50–200/leg; one stale leg carries 100% of the edge (palp + limiting risk) | own-research-cross-book-arbs.md |
| Derived-market inconsistency | negative | 0.24% fire rate; flagged legs close −4.8% [−8.5, −1.1] (n 45) | own-research-derived-inconsistency.md |
Side findings: Epicbet placeholder boards escape the wrong-board guard (#194); "Estonian price above the Betfair lay" (~6–21/day) is a candidate single-book signal for OWN SHARP once exchange history is longer.
Owner checks for promos: Coolbet no active sport bonus (blocks Combo Club); Unibet acca-insurance cap; Olybet "My campaigns" profit-boost challenge.
