# #191 synthesis — every bot outside /performance, one verdict each + the OWN line-up (2026-09-26)

Sources: `own-bot-lineup-audit-part1.md` (sharp), `-part2.md` (model / in-play / arms), `-brainstorm-strategy.md`,
`-brainstorm-instruments.md`, `-brainstorm-page.md`. Independent CLV = pick-time Estonian price vs a ≥5-book close
without Pinnacle and the bet book. **Nothing below shows an independent edge whose CI clears 0 at Estonian prices**
(one O/U slice n 18 excepted). OWNER APPROVES EACH LINE before anything is built or retired.

| # | Bot | What it is | Independent CLV (n) | Proposed |
|---|---|---|---|---|
| 1 | bot_coolbet_trigger_sharp_1x2_v1 | 1X2 sharp, Pinnacle-only, Coolbet | +2.7% [−0.5,+5.7] (96); last 3 h +6.5% | FOLD → OWN 1X2·SHARP (history kept) |
| 2 | bot_unibet_trigger_sharp_1x2_v1 | same, Unibet | +2.6% [−0.7,+5.8] (108); last 3 h +7.1% | FOLD → OWN 1X2·SHARP |
| 3 | bot_trigger_1x2_sharp_v1 | same, best of Coolbet/Unibet | +1.1% (62); last 3 h +8.1%, >3 h −5.7% | FOLD → OWN 1X2·SHARP |
| 4 | bot_unibet_trigger_sharp_ou_v1 | O/U sharp, Unibet | +4.4% [+0.1,+9.2] (18) | FOLD → new OWN O/U·SHARP |
| 5 | bot_trigger_ou_sharp_v1 | O/U sharp, best of 2 | −2.7% (16) | FOLD → new OWN O/U·SHARP |
| 6 | bot_coolbet_trigger_sharp_ou_v1 | O/U sharp, Coolbet (100% duplicate) | −3.6% (24) | RETIRE |
| 7 | bot_trigger_1x2_sharp_tight_v1 | pre-registered instrument | own-book mc-CLV −3.7% (272) = its retire cell | RETIRE (at its readout) |
| 8 | bot_sharp_aligned_v1 | forward-test twin (fetch-time freshness) | −2.5% (5); gate meaningless per §88 | RETIRE |
| 9 | bot_ou_sharp_2anchor_v1 | O/U sharp + consensus, global books | +3.0% at its global price; −0.4% at ours | KEEP as PICKS test |
| 10 | bot_own_1x2_v1 | OWN 1X2·SHARP, v2 anchor, all guards, last 3 h | live since 09-26 | KEEP (OWN) |
| 11 | bot_coolbet_1x2_model_v1 | 1X2 home model, stale model, ⊂ bot_v10_1x2 | −0.9% (6) | FOLD → new OWN 1X2·MODEL (NEW+) |
| 12 | bot_coolbet_ou_model_v1 | O/U model, calibrator bug, silent since 09-13 | ROI −35.7% | RETIRE (#133) |
| 13 | bot_unified_gate_1x2_paper_v1 | flat 10% model gate | −14.1% [−16.9,−11.0] (241) | RETIRE (#171) |
| 14 | bot_rating_1x2_v1 | superseded rating model | −7.8% (4); ROI −64% | RETIRE |
| 15 | bot_combined_1x2_v1 | NEW+ copy of a /perf bot | — (2) | RETIRE (duplicate) |
| 16 | bot_consensus_d_v1 | consensus grade D, never sent | −1.1% (35) | RETIRE (keep grade D as a data slice) |
| 17 | bot_consensus_pinconf_v1 | consensus twin, gate rejected 0 of 77 | +4.5% (19, all non-Estonian) | RETIRE (comparison reads 0 by construction) |
| 18 | bot_ah_sharp_v1 | Asian handicap, global books (#187) | not scored yet | KEEP as PICKS test |
| 19 | bot_inplay_slowstate_v1 + _afctl_v1 | in-play rig + control | lift −0.84pp | KEEP until the n=1,000 stop (~3 d), then STOP |
| 20 | control_junk_anchor | random leg — the forward test's null | −2.2% (534), as it should | KEEP as the reference LINE, not a bot row |

## Proposed OWN line-up (paper; the book is recorded per pick; built fresh, not inherited)
1. **OWN 1X2 · SHARP** = `bot_own_1x2_v1` (live). The only rule that survived a holdout (+5.7%, n 134).
2. **OWN O/U 2.5 · SHARP** (new) — same rule on O/U 2.5; thin evidence, ~1–5 picks/day; close as "no volume" if < 30 picks in 4 weeks.
3. **OWN 1X2 · MODEL** (new) — NEW+ edge ≥ 3% at the best Estonian price AND the v2 anchor not negative, odds 1.30–3.50;
   pre-registered question: does model agreement rescue the > 3 h sharp picks that revert? Retire if it only duplicates #1.
   First cheap backtest (2 days, 25 min before KO): NEW+ alone +0.5% (56), NEW+ AND anchor +2.9% (32), pure rating −7.7% (86).
4. **Not started:** O/U · MODEL (ou_comb_v1 IS Pinnacle where Pinnacle prices; −2.4%), AH / BTTS / corners / cards at our books.

## Page (#191 d) — agreed shape
PICKS (the /performance line-up, public figures) · OWN (market × method grid, judged on independent CLV) · no Instruments
block: junk control stays the dashed reference line; in-play and "tight" show as one footer line until their readouts;
retired bots behind the existing "Retired" tab. One verdict vocabulary: Beats the market · Loses the market · Can't tell yet · Too early n/30.

## Gaps found (to fix inside #191)
- The exchange close should be the independent judge (418 finished matches already have a quote ≤ 30 min before KO).
- NEW+ / ou_comb_v1 predictions are overwritten every 30 min (sometimes after KO) — an append-only log is needed before
  any early-window MODEL backtest.
- `lineups_fetched_at` is overwritten after the match — record first-seen.
- "Placeable" shows true for 7 sharp bots whose placement floor is an empty band (1X2 min edge 0.10–0.13 > max 0.08).
- bot_consensus_d_v1's admin record is frozen at 10 (never-sent picks don't count).
