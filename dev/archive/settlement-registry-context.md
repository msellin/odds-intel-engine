# SETTLEMENT-RESOLVER-REGISTRY — context

## Goal
Replace the hardcoded if/elif in `settle_bet_result()` (workers/jobs/settlement.py)
with a market→resolver REGISTRY. Same verdicts for every existing market; add
corners (needs corner counts) and BTTS-derive; unknown/data-missing market =
SKIP LOUDLY (leave pending + alert), NEVER guess-grade.

## Ground truth established 2026-09-07
- Current unknown-market default = **won=False → result 'lost'** (NOT void).
  Verified: corners_ou_105 over AND under both return {'result':'lost','pnl':-10}.
- Real markets in bet tables (all KNOWN): 1x2, o/u, over_under_15/25/35,
  asian_handicap, btts, double_chance, draw_no_bet, combo. Only unknown rows are
  the 34 pending corners picks → changing unknown-default affects 0 settled rows.
- Callers of settle_bet_result: settlement.py lines 348 (combo leg), 1050 (CLV
  path), 1454 (void-resettle), 2746/2770 (sim settle), 2897 (shadow settle);
  one-shot scripts resettle_after_btts_fix.py, cleanup_ou_bets_unvoid_inplay.py.

## Safety plan
1. GOLDEN FIXTURE (scripts/fixtures/settlement_golden.json): exhaustive synthetic
   grid over every known market×selection×score×closing_odds, snapshot of CURRENT
   settle_bet_result output. Built BEFORE refactor.
2. Refactor to registry, keep known-market logic byte-identical.
3. Smoke SETTLEMENT-GOLDEN re-runs the grid through the new function, asserts
   identical result+pnl+clv on every row.
4. New behaviour: unknown market → result 'skip' (callers leave it pending, alert
   once). Wire the 5 settlement.py callers to treat 'skip' as "do not write".
5. Corners resolver (from stats), cards registered UNSETTLEABLE (documented).
6. Independent sub-agent review (high blast radius). Then commit.

## Status: golden fixture next

## Status 2026-09-07 (built, awaiting sub-agent review)
- settle_bet_result refactored to `_SETTLEMENT_REGISTRY` (resolvers extracted verbatim).
- Golden fixture 1,935 rows: 0 mismatches. Corners grade from stats; unknown/no-stats -> skip.
- Skip guards added at all 5 settlement.py callers + combo-leg; `_alert_unsettleable` dedup'd TG.
- `import re` added (module scope). Smoke SETTLEMENT-GOLDEN + SETTLEMENT-REGISTRY green.
- Docs: ANALYSIS_GOTCHAS §50, WORKFLOWS ⑧. Corners exclusion guard in _PENDING_SHADOW_BETS_SQL retained.
- NEXT: incorporate sub-agent findings, flip PRIORITY_QUEUE to Done, commit engine.

## DONE 2026-09-07 (sub-agent review incorporated)
Review found 2 real issues, both fixed:
- Real-combo path wrote result unguarded (real_bets.result is text) → skip would persist. Guarded (settlement.py ~1302).
- Combo legs use glued `ou15/ou25/ou35`; _parse_ou_line returned None → all O/U combo legs silently 'lost' (199 sim + 30 real). Fixed _parse_ou_line + OU predicate to parse ouNN → combos settle correctly forward. Historical re-grade filed COMBO-OU-LEG-RESETTLE (owner-gated for real_bets).
All 7 settle_bet_result/settle_combo_bet callers guarded against writing 'skip'. Golden 0 mismatch. Committed.
