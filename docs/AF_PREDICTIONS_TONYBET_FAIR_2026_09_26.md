# API-Football predictions and Tonybet's fair probabilities — are they any use? ([[#144]], 2026-09-26)

**Read before** using `matches.af_prediction` or `book_fair_probs` as a probability, a fallback or a public
benchmark. Measured with the forward scorecard `workers/jobs/model_accuracy.py` (the same code that feeds
/admin/models): settled matches, 30 days to 2026-09-26, log-loss (lower is better), "guessing" = the
window's own outcome rates, Pinnacle = its de-vigged latest pre-kickoff price on the SAME matches.

## Numbers

| Source | Market | n | log-loss | guessing | on Pinnacle's matches: source / Pinnacle (n) |
|---|---|---|---|---|---|
| API-Football `/predictions` | 1X2 | 13,397 | **1.527** | 1.058 | 1.434 / 0.982 (7,151) |
| Tonybet fair (Sportradar) | 1X2 | 344 | **0.930** | 1.043 | 0.935 / 0.922 (252) |
| Tonybet fair | O/U 1.5 | 329 | 0.577 | 0.558 | 0.553 / 0.545 (134) |
| Tonybet fair | O/U 2.5 | 340 | 0.706 | 0.691 | 0.715 / 0.717 (252) |
| Tonybet fair | O/U 3.5 | 339 | 0.636 | 0.641 | 0.663 / 0.661 (190) |
| (reference) Pinnacle | 1X2 | 8,203 | 0.982 | 1.070 | — |
| (reference) NEW+ `r1x2_comb_v1` | 1X2 | 267 | 0.945 | 1.045 | 0.955 / 0.953 (194) |

⚠️ `matches.af_prediction` has no fetch timestamp, so "written before kickoff" cannot be enforced for it (it is
fetched in the pre-match enrichment; a late refetch cannot be ruled out). That can only flatter it — and it is
still far worse than guessing. Tonybet rows are latest-only; `updated_at < kickoff` keeps them pre-match.

## Verdicts

1. **Tier C picks priced off AF since 2026-09-14:** 8 picks, all O/U from `bot_v10_ou_comb_v1` (priced by its
   own combined O/U model, not AF). **No 1X2 pick was priced off the AF fallback** in the window — exposure 0.
   Verdict: **don't use** AF as the Tier C 1X2 fallback; with zero current exposure the removal is a tidy-up,
   filed as [[#180]], not an emergency.
2. **AF vs Pinnacle / consensus / current models:** AF is **worse than guessing** by 0.47 log-loss on 13,397
   matches (and 0.45 worse than Pinnacle on the 7,151 it shares). #141 round 3b's finding (AF adds information
   only where no book prices a match) is the most it can be; forward, it loses everywhere. **Don't use.**
3. **"Our picks vs API-Football" as a public benchmark:** beating a source that is worse than guessing proves
   nothing and would read as a claim it is not. **Don't use** — benchmark against Pinnacle / the consensus.
4. **Tonybet fair as an anchor:** 1X2 within 0.013 of Pinnacle on the same matches (0.935 vs 0.922) and better
   than every model we run; O/U level with Pinnacle on 2.5, slightly behind on 1.5 / 3.5. **Promising as a
   fallback fair price where Pinnacle is absent** — but n = 252–344 is too small for a pre-registered verdict.
   Already scheduled as #154 idea 3 (pre-register at ≥ 2,000 fixtures, ~mid-October); no new row.
5. **Tonybet price above its own fair:** **never** — 0 of ~14,000 quotes (1X2 / O/U / BTTS); its price is the
   Sportradar fair minus an 8–12% margin (1X2 −11.1% EV on average). No value flags from its own model; it
   also means Tonybet is rarely a best price.
6. **Keep history (open / T-3h / close) in `book_fair_probs`:** recommended (cheap, and verdict 4 needs a
   time-aligned comparison at close, which latest-only rows cannot give). Owner/feeds decision — noted on #154.

Scorecard rows for both sources are now part of `model_accuracy` (/admin/models) and re-measured daily.
