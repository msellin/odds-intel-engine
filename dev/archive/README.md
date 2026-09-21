# `dev/archive/` — finished task docs

Working docs (`<task>-plan.md`, `-context.md`, `-tasks.md`, results files) whose
parent `PRIORITY_QUEUE.md` row is closed, or whose subject no longer exists.
They are kept, not deleted: the reasoning in them is often the only record of
*why* something was built the way it was.

**They hold no open work.** Per `CLAUDE.md` § "ONE master task list", any item
still live when its parent row closes must be promoted to a `PRIORITY_QUEUE.md`
row or explicitly dropped with a reason in the commit that archives it. An
unticked `- [ ]` in here is finished-with, superseded, or abandoned — never a
backlog. If you find one that looks like real outstanding work, it belongs in
`PRIORITY_QUEUE.md`, not here.

**Moving a doc here must not leave a dangling path.** Source files, migrations
and plists sometimes cite these docs by path. Smoke test
`DEV-ARCHIVE-LEAVES-NO-DANGLING-PATH` fails if anything still points at
`dev/active/<file>` for a file that now lives here.

`dev/active/` is for docs whose work is in flight, plus the live artefacts the
running system reads and writes — competitor snapshots, launchd logs,
`cdp-lifecycle.jsonl`. Those are not task docs and must never be archived.
