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
