# OWN implementation — CONTEXT

**Last updated: 2026-09-15 (evening).** Phases 0, 1a, 1b, 2, 3 and 6 are CODE-COMPLETE
and pushed (engine `6fdc37fa`, `a104b0d3`, this commit; web `c8fdade`…`173e56a`, deployed).
Found during Phase 0: `ROUTER_ALLOW_REAL` was SET in `.env` (router ran in real mode);
`real_bets.simulated_bet_id` FK swallowed every confirmed placement's ledger row since
09-13 (fixed: `shadow_bet_id`, mig 354). Remaining: Phase 5 cull; verifier agents;
owner actions (Phase 2 accounts + T&Cs; arm nothing).

## Key files

| area | file |
|---|---|
| audit + corrections | `docs/OWN_STRATEGY_AUDIT_2026_09_15.md` |
| plan / tasks | `dev/active/own-implementation-plan.md`, `-tasks.md` |
| pause flags | `workers/automation/coolbet_state.py` (`is_placement_paused` :310, `is_daemons_paused` :422, `is_publishing_paused` — mig 353, stays fail-open) |
| UI placer | `scripts/place_coolbet_ui.py` (`main` :980, `effective_allowlist` :134, caps :219), `workers/automation/coolbet_ui_placer.py` (`stage_bet`, late pause read :1607) |
| router | `workers/automation/best_price_router.py` (`ROUTER_ALLOW_REAL` :410, iterates `PLACEABLE_BOTS` :426) |
| Unibet executor | `workers/automation/unibet_placer.py` (no pause read) |
| API placer + VPS drain | `workers/automation/coolbet_placer.py` (`place_all_bets` :1930, pause :1952, `place_bet_by_id` :2704, hardcoded `execute=False` :2754/:2761); `workers/scheduler.py:2616` (`IntervalTrigger(seconds=10)`) |
| launchd | `local/launchd/com.oddsintel.coolbet-ui-placer.plist`, `com.oddsintel.best-price-router.plist` — both LOADED on the Mac as of 2026-09-15 |
| settlement | `workers/jobs/settlement.py` (`_settle_real_bets_for_matches` :1481, direct-book CLV :1369) |
| in-play collector | `workers/jobs/inplay_epicbet_collector.py` (Mac, plain requests); `workers/automation/coolbet_inplay.py` (Coolbet in-play API, read-only parts reusable) |
| own-book writers | `workers/automation/coolbet_explorer.py`, `workers/scrapers/epicbet_explorer.py`, `workers/automation/unibet_odds_feed.py` |
| retention | `prune_old_simple` (see ANALYSIS_GOTCHAS §59) |
| de-vig | `workers/model/devig.py` (`shin_devig`, `devig`) |
| sharp-tight prereg | `dev/active/own-sharp-tight-preregistration.md` |
| in-play discovery | `dev/active/inplay-strategy-discovery-context.md` (oufid.jsonl result still unread) |

## Decisions made

- **Owner requirement 2026-09-15:** every OWN pick visible on `/admin/shadow-bots` from its first row; nothing OWN on `/picks` or `/performance`. Verified structurally (shadow_bets_unique upcoming section; customer surfaces read other tables; maturity CHECK constraint). Encoded as the "Visibility invariant" in the plan + smoke `OWN-BOTS-OFF-CUSTOMER-SURFACES`.

- OWN direction continues only as bounded measurement builds (1a, 1b) plus promos (2);
  expected value stated as ≤ 0; ceiling €1–7k/yr.
- Phase 0 is mandatory regardless of any strategy decision.
- `publishing_paused` (mig 353) is deliberately NOT part of the placement gate.
- Replace `ROUTER_ALLOW_REAL` env var with DB `real_money_armed` (mig 354).
- In-play target book is **Coolbet first** (tighter in-play margin, has a placer),
  Epicbet second; collector runs on the Mac.
- In-play primary metric is hit-rate minus de-vigged book implied prob, not ROI, not CLV.
- Migrations shipped today: 354 (real_money_armed + real_bets.shadow_bet_id), 355 (shadow mc-CLV + freshness + views), 356 (promo), 357 (in-play board + bots). Next free: **358**.

## Corrections to earlier advice (2026-09-15)

- **"Log manual bets via `/admin/place`" was WRONG for shadow picks.** `/admin/place`
  reads `simulated_bets`; OWN picks live in `shadow_bets`. Today there is NO path from
  a shadow pick to `real_bets` except the paused UI placer's Coolbet account
  reconciliation. Phase 6's `place-action` closes this. Until then a hand-placed OWN
  bet is only captured when the UI placer next runs a verified pass.
- `/admin/shadow-bots` status pill (Promote/Retire) uses Pinnacle CLV t≥1.65 at
  n≥100 — NOT the pre-registered margin-corrected own-book CLV at n≥300. Ignore the
  pill until Phase 6.

## Owner decisions pending (⚖️)

1. ~~Unload the two `--execute` plists~~ DONE 2026-09-15 (moved to `~/Library/LaunchAgents/paused/`).
2. ~~Go/no-go on Phase 1b~~ built per the owner's "finish everything" instruction; Epicbet only, Coolbet in-play filed.
3. Open accounts at Olybet / Optibet / Betsafe / Paf / Tonybet / bet365.ee for Phase 2 and enter the T&Cs with `scripts/promo_ev.py add-terms`.

## Scope

- OWN only. PICKS items (audit §6, publisher time) are queue rows for a PICKS agent, not in this plan.

## Open questions

- `oufid.jsonl` O/U fidelity result — never read; gates Phase 1b's trigger set.
- Coolbet in-play endpoint viability from the Mac under current Imperva posture.
- Whether Coolbet's slip exposes a max-stake field (Phase 3 logging).

## Next steps

Start Phase 0.A (`placement_gate.py`) → 0.B call sites → 0.F tests → 0.D ledger repair
→ 0.E status → docs ripple → commit. Then ask owner for 0.C.
