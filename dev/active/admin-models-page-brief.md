Parent row: PRIORITY_QUEUE.md #153 ADMIN-MODELS-PAGE-2026-09-25. Also input for the unified bot model
refactor (#140, dev/active/unified-bot-model-HANDOVER.md). Forwarded by the owner 2026-09-25 from
another agent session, stored verbatim below. Not started — do not claim it from here without the row.

## What matters for the bot refactor (read this first)

- **simulated_bets has no rule_version.** Picks only carry `model_version`: NEW+ bots write
  'r1x2_comb_v1', O/U model bots 'ou_comb_v1', sharp O/U bots 'ou_sharp_v1'. A rule change
  (threshold, gate, odds range) is invisible in the ledger today. The refactor's schema
  (unified-bot-model-phase5-schema-draft.md) should decide whether a pick carries a rule/config
  version — #153 is told NOT to redesign bot_ledger/bot_scoreboard and to write this up as a proposal.
- **Where a bot's rules live today:** BOTS_CONFIG `prob_source` / `ou_prob_source` in
  workers/jobs/daily_pipeline_v2.py, plus standalone jobs (ou_sharp_outlier.py, ou35_model_shadow.py,
  pick_generator.py, pick_triggers.py). scripts/export_bot_config.py + table `bot_config` (migration 410)
  already resolve most of it per bot with file:line sources — the refactor should build on that, not parse
  code a second time.
- **Probability sources in production:** `predictions` source='ensemble' (old 1X2 + O/U;
  'ensemble_shadow' for candidates since migration 419), `rating_1x2_predictions` ('r1x2_d8plus_v1',
  'r1x2_comb_v1' + combiner_1x2_params), `ou_model_predictions` ('ou_comb_v1'; p_comb = model,
  p_over = served = Pinnacle where priced else combined; + combiner_ou_params), and market anchors
  (Pinnacle de-vigged, multi-book consensus).
- **Two edge units are in use:** probability points vs EV (p×odds−1). A unified bot definition has to
  name which one a threshold is in.
- **Visibility today** is spread over bots.show_on_performance (public / VIP / testing), hide_pending
  (hidden), vip_exclude, and coolbet_placer_bots (real-money capability).
- Holdout reference (08-31..09-24, log-loss): 1X2 old 1.0711 / 1.144 served blend, NEW ~1.005,
  NEW+ 0.9763; O/U 1.5/2.5/3.5 old 0.5934/0.7086/0.6961, combined 0.5655/0.6738/0.6428,
  Pinnacle 0.5612/0.6731/0.6422.

## The brief, verbatim

Task: PRIORITY_QUEUE.md row #153 ADMIN-MODELS-PAGE-2026-09-25 in /Users/margussellin/www/odds-intel-engine
(+ web repo ../odds-intel-web). Read CLAUDE.md first (task lifecycle, shared checkouts, admin shell,
anon least-privilege), then docs/SYSTEM_MAP.md and workers/registry/bot_registry.py. Claim the row
(🔄 In Progress) before writing code.

GOAL — a superadmin page /admin/models in the shared admin shell (admin/layout.tsx, components in
src/components/oi/: Panel, StatCard, StatusBadge, DataTable, ChartCard) that answers three questions:
1. Which MODELS exist in production, and how good is each one right now?
2. Which BOT uses which model, with which rule, and how is it doing?
3. When did a bot's model or rule last change?

1) MODELS — one row per probability source in production:
   - 1X2 old ensemble: `predictions` source='ensemble', markets 1x2_home/draw/away, per model_version
     (shadow/candidate rows are source='ensemble_shadow' since migration 419)
   - 1X2 NEW (ratings only): `rating_1x2_predictions` model_version 'r1x2_d8plus_v1'
   - 1X2 NEW+ (combined): `rating_1x2_predictions` model_version 'r1x2_comb_v1' (+ combiner_1x2_params)
   - O/U old ensemble: `predictions` source='ensemble', markets over15/over25/over35
   - O/U combined: `ou_model_predictions` model_version 'ou_comb_v1' (p_comb = the model,
     p_over = SERVED = Pinnacle where priced else combined) (+ combiner_ou_params)
   - Market anchors used as "models" by some bots: Pinnacle de-vigged, multi-book consensus.
   Per model, per market/line, on SETTLED matches, only predictions made before kickoff: n, log-loss,
   Brier, base-rate log-loss, Pinnacle log-loss on the same rows (the honest benchmark), rolling
   7/30-day and since launch, plus last fit / last write time. Reference numbers on the 08-31..09-24
   holdout: 1X2 old 1.0711 / 1.144 (served blend), NEW ~1.005, NEW+ 0.9763; O/U old 0.5934/0.7086/0.6961
   (1.5/2.5/3.5), combined 0.5655/0.6738/0.6428, Pinnacle 0.5612/0.6731/0.6422. Compute these in the
   ENGINE as a daily job into a private table (no heavy SQL in the web request).

2) BOT → MODEL MAP — every active bot: display name, market, probability source (BOTS_CONFIG
   prob_source / ou_prob_source in workers/jobs/daily_pipeline_v2.py; standalone jobs such as
   workers/jobs/ou_sharp_outlier.py, ou35_model_shadow.py, pick_generator.py, pick_triggers.py),
   edge unit (probability points vs EV = p×odds−1) and thresholds, odds range, min_prob, gates
   (vip_exclude, require_pinnacle, early-hours, one_per_match), books, visibility (public /
   VIP / testing via bots.show_on_performance / hidden via hide_pending), maturity, real-money
   capability (coolbet_placer_bots), and live CLV (de-vigged Pinnacle close) + ROI + n from
   bot_ledger / bot_scoreboard. REUSE scripts/export_bot_config.py and the `bot_config` table
   (migration 410): it already resolves most of this per bot with file:line sources — extend it
   rather than parse code twice. Clicking a model filters to its bots; clicking a bot opens the
   existing bot sheet on /admin/bots.

3) CONFIG HISTORY — when a bot's model or rule changed. simulated_bets carries no rule_version;
   the usable signals are simulated_bets.model_version per pick (NEW+ bots write 'r1x2_comb_v1',
   O/U model bots 'ou_comb_v1', sharp O/U bots 'ou_sharp_v1'), git history of BOTS_CONFIG, and
   control_changes. Minimum: per bot, the distinct model_versions on its picks with first/last date
   and n. If you find this needs a proper rule_version on simulated_bets, write that up on the row
   as a proposal — don't redesign bot_ledger/bot_scoreboard (#139's contract) here.

Rules: superadmin only; server-side service client; new tables private (REVOKE anon/authenticated,
no *_public view, smoke ANON-LEAST-PRIVILEGE untouched); add the page to the admin nav, ⌘K search
and the CLAUDE.md admin list; a smoke test for the engine job and one for the page; update
SYSTEM_MAP.md / WORKFLOWS.md (new job) / MODEL_WHITEPAPER.md (how model accuracy is measured).
Shared checkouts: other sessions have uncommitted edits in both repos (often scripts/smoke_test.py,
PRIORITY_QUEUE.md, admin pages) — stage only your own hunks, never git stash, verify
`git diff --cached` before each commit. Push to main when green (web UI changes are pushed
immediately; the owner tests on the deployed site).
Don't change any bot's rules or model on this task — it's a read-only view. If the page shows
something wrong (e.g. a bot labelled with a model it doesn't use), file it on the row.
