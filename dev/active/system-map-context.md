# SYSTEM MAP — context / decisions

## The two edges (the core confusion)
- MODEL edge = cal_prob − 1/book_odds. Anchor = our calibrated model. Floor 13%/8%
  (big: model noisier than market). Used by coolbet model bots + /picks + model triggers.
- SHARP edge = P_sharp − 1/book_odds. Anchor = Shin-de-vigged Pinnacle. Floor ~3%
  (small: Pinnacle near-true). Used by value/line-shop bots, sharp triggers, placer default.

## Registry design
- Pure-data Python (`BOTS: list[BotSpec]`). Fields: name, family, market, anchor,
  edge_floor, odds_floor, real_money, paper, one_liner, twin (optional).
- Helpers: `placeable_names()`, `by_name()`, `active_names()`.
- Floors reference coolbet_placer values where applicable (drift test asserts ==).

## Key decisions
- PLACEABLE_BOTS NOT re-derived from registry (real-money safety) — only asserted equal.
- Sharp trigger floors: edge 3% (both markets), odds floors kept = model twin (2.80/1.80)
  so only the ANCHOR + edge-floor differ → clean twin comparison. Paper, owner-adjustable.
- SYSTEM_MAP.md is the index; other docs become linked deep-dives.

## Next steps if interrupted
See tasks.md checkboxes. Registry first (everything reads it), then sharp-floor re-run,
then map, then drift test, then web cards, then visual, then CLAUDE rule + commit.
