Parent: the Block D rows in `PRIORITY_QUEUE.md` (topic block BOTS-GATES-AND-SHARP-ANCHOR). This file holds ORDER and reasoning only — every task is a queue row; status lives there.

# Block D — implementation order after the research handover (2026-09-23)

Owner: *"apply queue changes, show me full task list … in the order of how we should implement those tasks"*.
Inputs: `data-modelling-agent-handover-2026-09-23.md` (derived from `value-betting-research-handover.md`,
~120 sources), cross-checked against everything measured on 2026-09-23.

## What the handover changes

**Measure before building.** The published picks are judged on CLV against their OWN soft book's
close (7.8-11% margin). The research standard is the de-vigged SHARP close, which proves or kills an
edge in ~135-150 bets instead of ~13,500. Rejected candidates are discarded, so no floor question can
be tested. And five α = 0 results say a better forecaster is unlikely to help — the model route left is
DECORRELATION (#090), not more features. Hence: funnel → sharp-close CLV → decorrelation, with feature
backfills paused.

## Where the handover was corrected (checked 2026-09-23)

1. **Grade B as a de-vig artefact — overstated.** B's evidence is REALISED profit at the taken odds
   (external 2015-16: 696 picks, win rate 0.764 vs 0.697 implied; ours +17.8% and +14.7%). No de-vig
   method changes that. The de-vig bake-off (#106) can tighten selection; it cannot undo B.
2. **"AH is unbiased" (T7) — verify first.** The IJF paper used average-of-books closing odds; the same
   authors' Pinnacle paper finds AH not uniform (half-goal lines dearer). 0/±0.5 lines add nothing over
   1x2. `pinnacle_ah_line` holds the line only.
3. **T2 does not need a direct Pinnacle feed first.** 120 of 120 settled published legs have a Pinnacle
   quote within 60 min of kickoff.
4. **T1 is partly built.** `picks_board` already stores the sharp arm's candidate board (1,267 rows).

## Revised order — 2026-09-24 (owner: "move 089 on top")

Steps 1-4 of the order below are done (#082, #024, #085 correctness half, #081/#088/#089 D/E), and
#090 (a) and (b) are closed (a: real but ±0.4pp — too small to trade; b: not achievable through AF).
#113 (anchor widening) was closed by another session. What is left, in order:

| step | row | what | est | direction |
|---|---|---|---|---|
| 1 ✅ 2026-09-24 | #089 | DONE — effect reproduces vs max odds, zero vs Pinnacle; no shots O/U head. Was: faithful Wheatcroft shots-rating replication on football-data.co.uk history — separates "our version was unfaithful" from "the effect is gone" | 1 d | BOTH |
| 2 ✅ 2026-09-24 | #111 | DONE — AF publishes xG 1-4 d late; nightly late-fill + backfill; HT-stats full-match fallback fixed and 1,002 rows repaired. Was: AF xG stopped arriving ~2026-08-31 — diagnose | 1-2 h | BOTH |
| 3 | #106 | de-vig bake-off per market + worst-method gate | 1 d | PICKS |
| 4 | #096 | grade the sharp arm (needs #106 and the AF-fed price check) | ½ d | PICKS |
| 5 | #014 | fair 1x2 from Pinnacle AH + totals (verify the claim first) | 2-3 d | PICKS |
| 6 | #062 | consensus as a log-odds pool, weights fitted to the close | 1 d | PICKS |
| 7 | #090 (c) | decorrelation + Benter blend — demoted, capped by the margin | 2-3 d | OWN |
| 8 | #085 | deletion half (keep `pinnacle_ah_line`) | ½ d | OWN |
| 9 | #071 | dated odds-band re-check | 2 h | PICKS |

Background, decided by date: #103 (Epicbet 1H, 14 d / 60 verified picks), grade B re-test at n ≥ 150.
Parked: #080, #086 (lineups arrive after kickoff — the fetch must move to T-60..T-30 first).
Owner decisions: #077 retire `bot_v10_ou`, #087 delete ~1.6 GB, #070 channel message, Pinnacle API email.

The table below is the 2026-09-23 order, kept for its reasoning.

## The order (rows, not a second backlog)

| step | row | what | est | direction |
|---|---|---|---|---|
| 1 | #082 | persist the candidate funnel (extend `picks_board`) | 1 d | BOTH |
| 2 | #024 | `clv_sharp` for every leg + segment table + OWN tail re-test + best-time curve | 2-3 d | BOTH |
| 3 | #085 | correctness half: the two diverging signal computations | 1 d | OWN |
| 4 | #081/#088 close-out, then #089 arms D/E | backfills verified; shots-fed O/U arms | ½ d | BOTH |
| 5 | #090 | decorrelated market-free model + Benter logit blend, judged on `clv_sharp` | 2-3 d | OWN |
| 6 | #106 | de-vig bake-off per market + worst-method gate (new row) | 1 d | PICKS |
| 7 | #014 | fair 1x2 from Pinnacle AH + totals (verify claim first) | 2-3 d | PICKS |
| 8 | #062 | consensus as a log-odds pool, weights fitted to the close | 1 d | PICKS |

Running in the background, decided by date: #103 (Epicbet 1H live check, 14 d / 60 picks), grade B
live re-test at n ≥ 150 (from #098).

Parked until #090 reports: #080, #086. Deferred: #028. Deprioritised: #025. Closed by merge: #029.
Unchanged, any time: #030, #022, #071, #037, #018, #096 (unblocked by #024 + #106), #091, #101.

## Owner decisions (not rows)

- **Direct Pinnacle feed (handover T9)** — the domain is DNS-blocked in Estonia and the guest API is
  unofficial (Pinnacle's public API closed 2025-07-23); may need a legal read. Nothing above needs it.
- **#077** — retire `bot_v10_ou` (negative in all 5 months and 7 versions).
- **#087** — delete ~1.6 GB of unread data.
- **#070** — tell the channel why pick volume jumped.

## Declined from the research pack (recorded so nobody re-derives them)

Kelly variants (no edge to size), exchanges / prediction markets as inputs or venues (DNS-blocked,
unlicensed, taxed), LLM forecasting, speed/latency edges, lower-league soft-line hunting, esports,
rebuilding the Pinnacle limits gate (rejected 2026-09-17), parlays, reselling feed data.
