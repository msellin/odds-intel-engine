# SYSTEM MAP — tasks

- [x] 1. `workers/registry/bot_registry.py` — 13 active bots, structured facts + helpers
- [x] 2. Sharp floors: `_SHARP_MIN_EDGE_BY_MARKET`/`_ODDS` in pick_triggers.py (3% edge, 2.80/1.80 odds); re-run Stage A/B; verify sharp bots fire
- [x] 3. `docs/SYSTEM_MAP.md` — two edges, families table, %-glossary, gate stack, links
- [x] 4. Smoke test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` (floors/active-set/placeable/map-mentions)
- [x] 5. Web: per-card anchor/floor/paper-or-real badge (mirrored TS lookup) + keep grouping/retired-collapsed
- [x] 6. Visual one-pager artifact (two edges + families + %-glossary)
- [x] 7. CLAUDE.md rule + PRIORITY_QUEUE row + BETTING_GATE_DECISIONS sharp note
- [x] 8. Commit engine+web+docs; verify web build

## Ground truth (2026-09-09)
- 13 active bots (DB). Real-money placeable: bot_coolbet_1x2_model_v1, bot_coolbet_ou_model_v1.
- coolbet_placer_bots: BOTH model bots ui_place_enabled=TRUE (held only by global placement_paused/daemons_paused). note text says "OFF" — stale, drift to flag.
- Floors: 1x2 edge .13 odds 2.80; o/u edge .08 odds 1.80 (coolbet_placer). Sharp default 3%.
- Trigger bots present: model 1x2/ou + sharp 1x2/ou. Sharp fire 0 at model floors (max sharp edge today +6.6%/+4.8%).
