Parent row: **#131 BACKLOG-AUDIT-2026-09-24** in PRIORITY_QUEUE.md.

# Backlog audit — context (2026-09-24)

Owner request: merge every parallel session's list into PRIORITY_QUEUE.md, audit every
open row against reality, merge duplicates, close done/stale with evidence, then
prioritise the whole list with estimates and present one table.

## Method
- Inventory: ~56 numbered open rows + ~25 unnumbered legacy table rows; the feeds
  session's private list (#1–#34, open: 14b, T-lag, 15–23, 27–31, 34); the #119 (E)
  handover a–g (`dev/active/sharp-anchor-v2-btts-ah-research/HANDOVER.md` §5).
- Five read-only audit agents, one per slice (A feeds/data, B picks/product/security,
  C model/analysis, D bots/placement/in-play, E stray backlogs outside the master).
  Common brief: scratchpad `audit/COMMON.md`; verdicts KEEP / CLOSE-DONE / CLOSE-STALE /
  MERGE-INTO / OWNER-DECISION, each with evidence.
- Then: apply closures in place (status → ✅/⛔ + one-line reason), merge notes on the
  surviving row, new numbered rows for session items that are real new work, and ONE
  ranked table near the top ("Open work — ranked").

## Constraints found
- Smoke `TASK-NUMBERS-STABLE`: each `**#NNN` may appear ONCE in the file, and any line
  whose first status mark is ⬜ / 🔄 In Progress must carry a `**#NNN`. So the ranked
  table uses plain `#NNN` and text statuses (open / in progress / blocked), never ⬜/🔄.
- Smoke `SINGLE-MASTER-TASK-LIST`: no ⬜ / `- [ ]` outside PRIORITY_QUEUE.md except dev/,
  runbooks, sentinel-marked docs.
- Other sessions share the checkout: commit with a temp GIT_INDEX_FILE, only own files.
  The "In-game odds improvement" session was asked to route queue edits through us
  while this runs (it did: #130 recorded verbatim in 89fec747).

## State
- #131 claimed (1146864c). Agents launched. Next: merge agent outputs → apply → table.
