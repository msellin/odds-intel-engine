Parent: PRIORITY_QUEUE.md #113

Key files: workers/utils/anchor.py (resolver), scripts/anchor_consensus_composition.py (measurement),
dev/active/anchor-widening-plan.md (findings + design + consumer inventory summary).
Decisions: Pinnacle tight(≤4%)+fresh(≤60 min) first; else consensus ≥5 books (Pinnacle counts as a member
when wide); Coolbet never a member (measured worse); own book excluded; skins deduped; thin 3–4 only on request.
Live check 2026-09-23 20:45 UTC: most no-anchor fixtures were AF quotes 273 min old → fixed the refresh.
Next: validation study (closing-line), then clv_sharp fallback.
