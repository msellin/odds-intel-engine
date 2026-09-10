# USE-COLLECTED-MARKETS — build plan (autonomous stretch 2026-09-10)

Goal: model the markets we already COLLECT prices for but never used, as sharp-anchor
(de-vig Pinnacle line-shop) paper shadow bots. Template = corners_paper_bot / the new
team_total_paper_bot. Floor 0 → accumulate → multi-dim sweep later.

## Viable markets (Pinnacle-anchored + settleable) — verified 2026-09-10
| market | anchor (Pinnacle 7d) | settlement | status |
|---|---|---|---|
| team_total (full match) | 2330 matches | final score (no gap) | ✅ DONE — bot_team_total_paper_shadow_v1 (mig 327), 38 picks live |
| 1x2_1h (first-half result) | 1033 matches | HT score — VERIFY availability | ⏳ next |
| over_under_1h (first-half totals) | 353 matches | HT score — VERIFY availability | ⏳ after |

## Decision points (search docs when unsure)
- 1H bots settle from the HALF-TIME score. team_total used the FINAL score (always present).
  MUST verify HT-score coverage before building 1H bots — if limited (like corners), gate
  them; if broad, build clean. (Check match_stats HT fields / a HT score column.)

## Hard markets (deferred — settlement/anchor problems)
- cards, corners_handicap, corners_home/away → corners-class settlement gap + no Pinnacle
  anchor for cards. Low priority (corners already reads -16%).

## Progress log
- team_total bot: built + tested (scanned 606, 38 picks, 1 settled won) + registry + SYSTEM_MAP
  + scheduler + smoke TEAM-TOTAL-PAPER-BOT. Committed 020da95.
- capability matrix: docs/DATA_SOURCE_CAPABILITY_MATRIX.md.
- NEXT: verify HT-score settlement → 1x2_1h bot → over_under_1h bot → follow-up task.
