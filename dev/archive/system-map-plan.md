# SYSTEM MAP — keep the betting system understandable as it evolves

**Problem (owner, 2026-09-09):** every time we touch bots / modelling / edge floors
the system gets *harder* to understand. Knowledge is scattered across
COOLBET_OWN_BETTING.md, BETTING_GATE_DECISIONS.md, BOOK_AGNOSTIC_EDGE_ENGINE.md and
code comments; no single place is *required* to stay true, and nothing catches
drift. The same word "edge" means two different things (model edge vs sharp edge).

**Fix (owner approved all three):**
1. **One canonical map + a registry** — `workers/registry/bot_registry.py` is the
   structured single source of truth for every active bot (family, market, anchor,
   edge floor, odds floor, real-money?, one-liner). `docs/SYSTEM_MAP.md` is the
   human map (two edges, families, %-glossary, gate stack) built on it.
2. **A drift test** — `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` in smoke_test.py fails CI
   when the registry disagrees with the code/DB: floors == coolbet_placer floors,
   registry active set == bots table active set, real_money set == PLACEABLE_BOTS,
   every registry bot named in SYSTEM_MAP.md.
3. **Code consolidation** — registry is the one place to read bot facts. Safety
   set PLACEABLE_BOTS stays hardcoded in place (defense-in-depth for real money)
   but is *checked* against the registry by the drift test. Web cards read a small
   mirrored TS lookup so each card shows anchor/floor/paper-or-real plainly.
4. **Visual one-pager** — a rendered reference (artifact) of the two edges + bot
   families + %-glossary.
5. **CLAUDE.md rule** — any change to a bot/edge-def/floor/model-version updates
   SYSTEM_MAP.md + registry in the same commit; the drift test enforces it.

**In-flight, folded in:** the sharp-anchor trigger bots (built this session) fire 0
because they reused the MODEL floors (13%/8%) on a SHARP edge. Give them their own
sharp floors (3% edge, same 2.80/1.80 odds floors as the twin) so they actually
fire and can be compared. Document the sharp floors in SYSTEM_MAP + gate-decisions.

## Risks
- Touching real-money adjacent code (PLACEABLE_BOTS). Mitigation: do NOT change how
  PLACEABLE_BOTS is computed; keep it hardcoded, only ADD a drift assertion.
- Cross-repo (engine Python registry vs web TS). Mitigation: registry is Python;
  web keeps a tiny mirrored lookup, checked names-only by the drift test.
- Over-documentation making it worse. Mitigation: SYSTEM_MAP is the INDEX; existing
  docs get subordinated (linked as deep-dives), not duplicated.
