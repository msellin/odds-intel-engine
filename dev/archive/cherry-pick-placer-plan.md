# Cherry-pick placer — real-money bets restricted to mature bots

**Status**: planning · **Owner**: Margus / Claude · **Target**: ship code 2026-06-02 to 2026-06-04, flip gate ON after Phase 3.5 closes 2026-06-07

## Problem

All bots fire into `simulated_bets`, all of them get picked up by `coolbet_placer --record` and create `real_bets` rows. That makes the real_bets cohort a noisy mix of validated and unvalidated strategies. The same bot bleed dragging the paper page (e.g. `bot_lower_1x2`, the consolidated DC bots before retirement) was also poisoning the `real_bets` cohort that Phase 3.5 / Phase 4 decisions rely on.

We want a separation:

- **Paper firing keeps full breadth** — every bot writes to `simulated_bets`. Performance page stays honest ("every bet logged"), training volume stays high.
- **Real-money path is curated** — `coolbet_placer` only records bets from bots that pass an explicit maturity bar.

## Design

### Gate

`bots.maturity_label` already exists (values seen in DB: `testing`, `beta`, `active`, `calibrated`, `experimental`). Use it directly — semantically it already means "how much do we trust this bot". No new column.

New env var `COOLBET_RECORD_ALLOWED_MATURITY` — comma-separated list, default `calibrated`. Empty / unset / `*` means no filter (broad cohort, today's behaviour).

### Where the filter lives

Three loaders, all in the same gate pattern:

| Loader | File | What it returns |
|---|---|---|
| `load_qualified_bets()` | `workers/automation/coolbet_placer.py:187` | singles |
| `load_qualified_combo_bets()` | `workers/automation/coolbet_placer.py:379` | combos |
| `load_qualified_inplay_bets()` | `workers/jobs/inplay_bot.py` (search) | inplay |

Each already JOINs `bots b ON b.id = sb.bot_id`. Add `AND b.maturity_label = ANY(%s)` with the parsed env var as a list parameter.

**Bypass for admin manual placement**: `bet_id_filter` parameter already bypasses date/edge/dedup filters (MANUAL-PLACE 2026-05-29). The maturity gate must also be bypassed when `bet_id_filter` is set — admin is explicitly authorising that one bet by ID.

### Promotion criteria (written rule, manual gate)

Today `maturity_label` gets set by ad-hoc decisions. Before the gate flips to `calibrated`, agree on a single written rule. Proposal:

> A bot is promoted to `calibrated` when ALL of these hold over a 14-day rolling window:
> - n ≥ 150 settled bets
> - ROI ≥ 0% (preferably ≥ 2%)
> - avg CLV ≥ 0% (positive line agreement with sharp books)
> - hit rate within 3pp of model-predicted hit rate (calibration sanity check)
>
> Demotion: if any of (ROI, CLV) drops below -2% on n ≥ 50 bets over 7 days, drop back to `active`.

This is a manual operator decision — the data is surfaced, the human flips the label. Automating promotion is out of scope for this plan.

### Admin surface

Add `/admin/promotion-candidates` to odds-intel-web:
- Lists every bot not currently `calibrated`
- Columns: name, current maturity, n_settled_30d, roi_30d, avg_clv_30d, hit_rate_vs_predicted, status (✅ eligible / ⏳ thin data / ❌ failing)
- One-click "Promote to calibrated" button → writes `maturity_label='calibrated'` (admin-only)

This avoids the gate-flip moving the cherry-picking bias from "all bots" to "implicit operator gut feel".

## Phases & milestones

### Phase 1 — Ship the code, gate disabled (2026-06-02 → 06-04)

Code lands but `COOLBET_RECORD_ALLOWED_MATURITY` is unset / `*`, so behaviour is unchanged. Zero risk to Phase 3.5 verdict.

- Add env var read + parse helper in `coolbet_placer.py`
- Thread maturity filter into all three loaders
- Bypass when `bet_id_filter` is set
- Smoke tests covering: (a) no env var = no filter, (b) `calibrated` = filter applied, (c) admin bypass works

### Phase 2 — Admin surface + promotion criteria written into docs (2026-06-05 → 06-06)

- `scripts/promotion_candidates.py` — CLI that prints the 30-day rolling stats per bot
- `/admin/promotion-candidates` page in odds-intel-web
- Promotion rule documented in `docs/PROMOTION_RULES.md` (new file) and referenced from `CLAUDE.md`

### Phase 3 — Flip the gate (2026-06-08, day after Phase 3.5 closes)

- Read Phase 3.5 / Phase 4 verdict on 2026-06-07
- Promote the bots that meet the new criteria (likely: `bot_v10_all`, the v2 family if they hold)
- Flip `COOLBET_RECORD_ALLOWED_MATURITY=calibrated` on Railway
- Monitor for 7 days; iterate the allowed set if too few bets place

## Risks

| Risk | Mitigation |
|---|---|
| Flipping the gate during Phase 3.5 contaminates the verdict | Phase 1 ships with gate OFF. Flip lives in Phase 3, after 2026-06-07. |
| Too few bots pass criteria → placer goes silent on real money | Default allowed list can be `active,calibrated` initially; tighten to `calibrated` only once we have ≥ 4 calibrated bots. |
| Inplay bots use the same gate but their CLV column is sparse → criteria don't apply cleanly | Inplay calibration check uses ROI + hit-rate-vs-pseudo-prob instead of CLV. Document this separately. |
| Combo bots have no meaningful CLV (multi-leg) | Combo gate uses straight per-leg model probabilities aggregated. Plan: leave combos OUT of the calibrated set for now; combo placer continues with current rules unless explicitly added later. |
| Performance page still shows the paper-only ROI as headline → new visitors think "system is losing" | Out of scope for this plan but related — covered in PERFORMANCE-PAGE-ATTRACTIVENESS (separate task). |

## What this is NOT

- Not changing what bots fire. Every bot keeps firing paper.
- Not automating promotion. Operator decides.
- Not changing performance-page logic. (Separate task — see below.)
- Not adding tier-level filtering. Maturity is the only axis.

## Open questions

1. **Should inplay bets be eligible for real money at all?** Inplay has higher operational friction (Coolbet live odds latency, score-odds inconsistency). Inplay paper ROI is +41% but real-money placement is harder. Lean: include `inplay_o`, `inplay_p_v2`, `inplay_c` only — the 3 with ≥ 30 bets and positive ROI. Defer to Phase 3.
2. **Should combo bots ever be flipped to calibrated?** Combo bleed is structural (variance + correlated legs). Lean: leave combos in `experimental` permanently, never auto-place real money on combos.
3. **What happens to manual `/admin/place` flow?** It already uses `bet_id_filter` which bypasses the gate. Admin is the override mechanism. No change needed.

## Acceptance

- Phase 1 done when: env var works, all three loaders gate correctly, admin bypass preserved, smoke tests pass, code shipped with default `*` (no behaviour change).
- Phase 2 done when: `/admin/promotion-candidates` lives, promotion rule written, at least 2 bots manually promoted to `calibrated`.
- Phase 3 done when: env flipped, 7-day monitoring report shows real_bets cohort ROI > paper cohort ROI by ≥ 2pp.
