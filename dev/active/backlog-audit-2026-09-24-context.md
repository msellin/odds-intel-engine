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

## Execution run (owner: "work the list top-down, verify every task with another agent")
Table is sorted in IMPLEMENTATION order (prerequisites first). Every task below was
independently reviewed by a separate agent; review findings were fixed and re-committed.
- #072 anon lockdown — mig 404/405 + superuser revokes; review clean. ✅
- #129 PICKS outlier anchor = publishable books; review found OWN leak → `_own_outlier_ok`
  in pick_generator; 1xBet+Marathonbet counted once. ✅
- #034 19 jobs inside _run_job; review: pause claim was wrong (corrected). ✅
- #136 coverage RPC 335 s → 0.03 s (index built by hand, mig 406). ✅
- #016 checkpoint: slope verdict ~09-29; new item (5) Pinnacle 1H share halved from 09-21.
- #055 Telegram dead ends + /performance upsell removed. ✅
- #001 matchers orientation-strict; review found decoy defect → refuse when best fit is
  reversed, ties broken on exact name similarity. ✅ (follow-up under review)
- #112 T-lag was a measurement window; aliases; Unibet World routing (+43 matches/sweep);
  UEFA club category; records_count; longshot identity check (under review). Open: captcha
  iframes, docstrings.
- #123 single-market BTTS/AH guard (direct books only); near-KO 10 pp deferred. ✅
- #002 → owner decision: void Gremio, Borac, Plopeni, Sudtirol (not Mannucci).
- #022 atomic manual bet logging (mig 407), editable price/stake. ✅
- #132 part 1 Coolbet DNB (under review). Open: Unibet AH, Epicbet lines, 2H markets.
- #101 Optibet probe: /et/groups works; /upcoming cut after ~4 s; boards by ids — not shipped.
- #107 C part 1 coverage-drop amber (under review).
Next in order: #121 now-parts (book personalities, opening-price CLV), then #018.
Owner decisions pending: #002 voids, #133, #127, #049, #057, ROUTER_ALLOW_REAL on the Mac, DMARC.
