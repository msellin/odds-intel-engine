# WC Best Site — Running Context

## State (2026-06-04 ~10:00 UTC)

- ✅ V2 schedule + group cards shipped (commit `ade5877` + `205f690`).
- ✅ Bar legend / numbers / AI-team-name pill / favourite-bolding shipped.
- ✅ Smoke green: WC-PROB-DISPLAY-SHARED, WC-GROUPCARD-VITALITY-V2, WC-SCHEDULE-VITALITY-V2.
- ❌ **Brazil v Morocco showing 22.5%/27%/50% (Morocco favoured)** — model bug confirmed; market consensus is Brazil 55-69% favoured.
- ❌ Root cause: `team_elo_international` has Morocco at 1940 (2nd in world!) and Brazil at 1759. Real-world ELOs: Brazil 1763, Morocco 1757 (eloratings.net).
- ✅ ELO walk script (`scripts/compute_international_elo.py`) is structurally fine; just needs better starting seeds instead of 1500-for-everyone.

## Key files / paths

| File | Purpose |
|------|---------|
| `scripts/compute_international_elo.py` | ELO walk over all intl matches. Needs INITIAL_ELO_SEEDS dict. |
| `workers/model/national_team_predictor.py` | Phase-3 model that produces 1X2 from ELO. |
| `scripts/write_national_team_predictions.py` | Writes predictions to DB (`source='national_team_v1'`). |
| `src/lib/world-cup.ts` | FE prediction merger (now accepts `1x2_home/draw/away`). |
| `src/components/wc-prob-display.tsx` | Shared `ProbBar/ProbNumbersRow/AiPickPill/favouriteClass`. |
| `src/components/wc-schedule.tsx`, `wc-group-card.tsx` | Both use the shared primitives. |

## Wave 1 brief (in flight)

| Code | Agent | Files | Status |
|------|-------|-------|--------|
| A1 | foreground | `scripts/compute_international_elo.py` + seed table | active |
| A2 | bg agent | `workers/jobs/wc_roster_strength.py` + mig 176 | spawned |
| A3 | bg agent | `workers/jobs/wc_market_consensus.py` + mig 177 | spawned |
| D2 | bg agent | `workers/jobs/wc_live_xg.py` + scheduler edit | spawned |
| E2 | bg agent | `src/app/(app)/world-cup/teams/[name]/page.tsx` | spawned |
| G1 | bg agent | `src/app/(app)/world-cup/bracket/page.tsx` | spawned |

## Notes for future sessions

- Both repos public on GitHub; push directly to main; no feature branches.
- Smoke runs in CI; locally use `--filter` for the test in question only.
- `odds-intel-web` runs on Vercel; commits to main auto-deploy.
- `odds-intel-engine` worker is on Railway; scheduler.py is the main entrypoint.
