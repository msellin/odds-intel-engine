Parent row: #168 (steps 1, 3, 7, 9 of the proposals below shipped as #167 on 2026-09-25)

# Dev-process speed audit (2026-09-25)

## Measured baseline

**Smoke CI** (60 runs on 2026-09-25, 10:00–15:15 UTC; data from `gh run list` and the logs):
- 53 runs finished: **16 green (30%)**, 37 red. Wall time median **7.4 min** (range 3.7–9.7). Setup takes about 70 s (pip install alone is 42 s), and the suite takes 316–468 s.
- **Red is mostly not the pushed change.** Of the 37 reds, **16 contained only failures inherited from the previous run**. Of the rest, 21 introduced at least one new failure. 135 distinct tests failed at some point in the day. The worst run had 106 failures, 81 of them `Connection refused` on port 5433: the SSH tunnel dropped. That is infra noise, not a regression.
- The recurring reds are structural, not code bugs:
  - `FLOORS-ONE-SOURCE-CROSS-LANGUAGE` (13 runs) fails because the generated web file lags the engine push.
  - `MIGRATION-EDITS-ARE-INVISIBLE` (9 runs) fails because of a race. The smoke run for `4a772552` saw migration 440 in the live DB, applied by a *newer* push `f542bfbf`, and reported it "missing from disk".
  - `RECHECK-FORWARD-TEST-*` (11 runs) is a live-data invariant, not a code check.
  - The ADMIN-* failures (4 each) are cross-repo ordering: the engine test lands before the web file it reads.
- **The critical path is 3 tests.** `BEST-PRICE-ROUTER-EXECUTE-WIRING` takes 309 s, `ROUTER-NO-ALLOWLIST-BYPASS` 262 s and `GATE-STATUS-READS-THE-SAME-ENV` 261 s. All three run `route()` against the live DB and are serialised by `_ROUTER_ENV_LOCK`. The 10 tests over 60 s account for about 1,400 s of the 2,523 s spent in the 96 tests over 5 s. The other 1,233 tests are each under 5 s.
- **Test mix** (1,329 `@test`, classified by static scan): 707 source-inspection only, 446 import and call engine code, 176 explicitly query the DB. 134 read the web repo.

**Shared checkout**
- 567 commits in 7 days (about 80 per day). `scripts/smoke_test.py` is touched by **54%** of commits and `PRIORITY_QUEUE.md` by **63%**.
- Transcripts from the last 3 days show about 30 hand-built index operations (`hash-object` / `update-index` / private `GIT_INDEX_FILE`), plus `git stash` in the shared tree (this stashes *other sessions'* work) and `sleep` loops waiting on `.git/index.lock`.
- **3 "take back swept hunks" commits today** (`99ff9d78`, `1903fe9e`, `bed9a549`). All three were in `smoke_test.py`.
- The task-number counter collided at least 5 times since 09-21 (`e7d05b0f`, `e78c587c`, `d98e2c83`, …).
- Migration prefixes already duplicate (343, 354, 355, 356, 361), and `migrate.yml` applies them fine. It keys on *filename* and applies in glob order. **So a number collision is harmless; renaming afterwards is what hurts.**
- The harness already supports worktrees (`.claude/worktrees/` exists, and the `EnterWorktree` tool is available).

**Files**
- `smoke_test.py` is 58,442 lines. Local load plus one filtered test takes **2.2 s** (parse 0.7 s), so size is *not* a speed problem. It is a **contention** problem.
- `PRIORITY_QUEUE.md` is **2.4 MB / 3,147 lines**: median row 1 KB, largest 19 KB, 383 ✅ rows = 730 KB, and about 1.5 MB of prose outside the tables. No agent can read it whole. Every claim and close rewrites it.
- `CLAUDE.md` is 36 KB (about 9k tokens) and is loaded into every session and sub-agent. Most of it is incident narrative.
- Doc checklist in practice: 2.1 `.md` files per commit (mostly the PQ row plus one owning doc). Share of the last 300 commits touching each doc: WORKFLOWS 12%, SYSTEM_MAP 8%, MODEL_WHITEPAPER 4%, ROADMAP 3%; SIGNALS and TIER_ACCESS_MATRIX are not in the top 25. The long checklist is mostly read, not used.

## Proposals (ranked by time saved ÷ effort)

| # | Proposal | Time saved | Quality risk → guard that replaces it | Effort |
|---|---|---|---|---|
| 1 | **Baseline-diff verdict.** The runner writes the set of failing test names as a JSON artifact. The job compares it with the last completed main run and prints `NEW: …` / `INHERITED: …`. **Rule:** an agent acts only on NEW failures. It is red only if NEW is non-empty. | About 10–20 min per task spent diagnosing reds that are not yours (16 of 37 reds today were inherited-only) | Inherited reds could linger → a daily Telegram list of standing reds with age, owned by whoever introduced them (first-red SHA is in the artifact) | 1–2 h |
| 2 | **Two tiers.** *Fast gate* (source + import tests, no DB, no tunnel): expected well under 60 s of test time, about 2 min wall. *Live tier* (the 176 DB tests plus router/MFV/POOL-LEAK/LEAKAGE-CANARY and live-data invariants such as RECHECK, MIGRATION-EDITS): runs on push non-blocking, plus hourly on the latest main, with baseline-diff alerts. Tag with `@test(..., live=True)`. | Gate goes from 7.4 → about 2 min, for about 80 pushes a day. Tunnel flakes (81 false fails) and the migration race stop blocking. | DB regressions are seen up to 1 h later instead of 7 min → the live tier still runs on every push, just not as the gate. Data invariants were never code gates anyway. | 3–4 h |
| 3 | **Fix the long pole.** Make the three router tests use a stub DB or a small fixture instead of a live `route()`, or take the lock only around the env write. The next longest are MFV-LIVE-BUILD at 147 s and POOL-LEAK at 110 s. | Even before #2, suite time goes from 468 → about 150–200 s | Same assertions, fixture data; keep one live smoke of `route()` in the live tier | 1–2 h |
| 4 | **Split `smoke_test.py` into `tests/smoke/*.py`** (per area, and new tests go in a new file named after the task). The runner auto-imports the directory; `--filter` is unchanged. | Ends hunk-level staging of the 54%-hot file, and with it the swept-hunk reverts (3 today) | Duplicate test names → the registry asserts unique names at import | 2–3 h, one mechanical commit done at a quiet moment |
| 5 | **Slim `PRIORITY_QUEUE.md`.** Move ✅ rows and history prose to `dev/archive/PRIORITY_QUEUE_DONE.md`. Cap a row at about 600 chars and put the details in a linked dev doc. Allocate task numbers atomically, either from GitHub issue numbers or from a claim commit that must push fast-forward before any work starts. | Every claim and close is a small edit; the file becomes readable; about 5 number collisions a week go away | `SINGLE-MASTER-TASK-LIST` stays; add a smoke test for row length and file size | 2 h |
| 6 | **One worktree per session** (after #4 and #5). Commit with plain `git add <files>`, then `pull --rebase` and push. **Blocker found:** `_web_root = engine_root.parent / "odds-intel-web"` resolves to `.claude/worktrees/odds-intel-web` inside a worktree, so the **134 web tests would silently SKIP**, a false green. Fix: an `ODDSINTEL_WEB_ROOT` env var, or resolve via `git rev-parse --git-common-dir`, and make a missing web repo *fail* locally. `.env` is probably found by dotenv walking up to the main checkout (worktrees are nested); verify. Use the main checkout's `venv/` by absolute path. | Removes all index choreography (about 30 hand-built commits in 3 days), `git stash` accidents and index-lock sleeps | Rebase conflicts become visible instead of silent sweeps, which is the desired behaviour. After #4 and #5 the hot files are append-only. | 2 h plus a CLAUDE.md rule |
| 7 | **Migrations: stop renaming.** A duplicate NNN is legal (the runner keys on filename). Rule: on a collision keep your number and only add a distinct suffix. Rename only if the migration has not been pushed. Make MIGRATION-EDITS compare against the DB **only for files at or below the commit's highest prefix**, or move it to the live tier. | Removes the 9 false reds per day and the rename rework | An ordering dependency between two same-number pending files → document it with `-- depends on:`, or move to a timestamp prefix. A timestamp prefix would need `migrate.yml` to sort numerically, because `2026…` sorts before `300_`. | 30 min |
| 8 | **Cross-repo generated files** (`engine-floors.ts`, ADMIN-* reads). Generate them in the web build from the engine checkout, or have the engine push commit the regenerated web file. Cross-repo tests go in the live tier, or skip when the web HEAD is older than the engine commit. | 13 + about 16 false reds per day | The generator remains the single source; the web build fails on a stale file | 1–2 h |
| 9 | **Agent protocol rules** (CLAUDE.md, short): (a) never block on CI; push, carry on, and check once at the end with `gh run list -L1` (cancel-in-progress now folds pushes together). (b) One push per task where possible (#164 took 4). (c) Never `git stash` in a shared tree. (d) Read `PRIORITY_QUEUE.md` and `smoke_test.py` by `grep -n` and offset, never whole. (e) Reports ≤ 25 lines. (f) Doc checklist becomes "PQ row + the doc that owns what you changed + ripple grep". Drop the 9-row table from every task. | About 20–30 min per medium task (CI waits of 4 × 7.5 min) | The drift tests (SYSTEM_MAP, floors) stay mandatory; consider generating the WORKFLOWS schedule table from `scheduler.py` (touched 24 times vs WORKFLOWS 37) | 30 min |
| 10 | **Trim `CLAUDE.md` to rules only** and move the incident narratives to `RELIABILITY_LEDGER`. Target 12 KB or less. | About 6k tokens per session and sub-agent, every time | The history is kept, just linked | 1 h |

## Recommended order
1 → 3 → 7 → 9 (same day, low risk, about 4 h in total. These clear most false reds and cut the gate to about 3 min).
Then 2 (gate about 2 min), then 5 → 4 → 6 (these remove shared-checkout friction and must land in that order), then 8, then 10.
Expected result for a #164-sized task: about 1.5 h → about 40 min, with the same assertions still running. The difference is that the DB assertions report instead of gate, and a red means *your* red.
