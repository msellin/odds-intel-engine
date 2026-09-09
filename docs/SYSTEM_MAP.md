# SYSTEM MAP — the one place that explains picks, bots, and every %

**This is the index. Read this first; everything else is a deep-dive it links to.**

It exists because the betting system kept getting *harder* to understand every time
we touched a bot, a floor, or the model — the facts were scattered and no single
place was required to stay true. This is that place, and it is **machine-checked**:
the smoke test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` fails CI if the structured registry
(`workers/registry/bot_registry.py`) disagrees with the live code or DB, or if a bot
here is missing from this doc. So it cannot rot into fiction.

> **Rule (also in CLAUDE.md):** any change to a bot, an edge definition, a floor, or
> a model version updates BOTH `workers/registry/bot_registry.py` AND this file in the
> **same commit**. The drift test enforces it.

---

## 1. The word "edge" means TWO different things

This one overload caused most of the confusion. There are two edges, measured against
two different yardsticks, and **their percentages are NOT comparable**.

| | **Model edge** | **Sharp edge** |
|---|---|---|
| Formula | `cal_prob − 1/book_odds` | `P_sharp − 1/book_odds` |
| Anchor ("fair value") | our **calibrated model** | **Shin-de-vigged Pinnacle** line |
| Typical floor | **13%** (1x2) · **8%** (O/U) | **~3%** |
| Why that floor | the model is *noisier* than the market, so a small edge is mostly model error — demand a big one to filter noise | Pinnacle is *near-true*, so a 3% overlay is a **real** 3% — no need for it to be big |
| Fires… | when our model disagrees a LOT with the book | rarely — Coolbet ≈ Pinnacle, so beating it by 3%+ is uncommon |
| Known failure | at 13% it still adverse-selects longshots → −21% OOS (the trigger bot) | over-strict floor (e.g. 13%) → never fires |

**A 3% sharp edge and a 13% model edge filter to roughly the same strictness** — they
just measure against different rulers. Putting a model floor on a sharp edge (or vice
versa) is the classic mistake; the `Anchor` column in the bot tables below says which
ruler each bot uses.

`P_sharp` comes from `workers/model/devig.py` (Shin's method — removes proportionally
more margin from longshots than a naive proportional de-vig, which matters for 3-way
1x2). See `docs/BETTING_GATE_DECISIONS.md` for how the model floors were decided.

> **The two anchors are NOT fully independent.** The model *itself* ingests Pinnacle
> as input features (`pinnacle_home/draw/away_odds` + sharp-consensus in
> `workers/model/features.py`), so `cal_prob` is already sharp-**informed** — the
> sharp line is an ingredient of the model number, not a separate opinion. Consequence
> for the trigger head-to-head: where the model and sharp anchors agree, part of that
> is the model "knowing" Pinnacle; the sharp anchor's distinct value is where it
> **disagrees** with the model (Coolbet beats Pinnacle on a pick the model didn't flag).

---

## 2. Every active bot, by family

Anchor = which edge it uses (§1). "Money" = whether it can stake **real** money
(must be in `PLACEABLE_BOTS`) or is **paper**. Floors shown are the gate a pick must
clear. This table is generated from the registry — do not hand-edit rows; edit the
registry and regenerate.

### Real-money capable · Coolbet UI placer

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_coolbet_1x2_model_v1` | 1x2 | model | 13% | 2.80 | **REAL** | Places our calibrated model's 1x2 picks at Coolbet's own price when model edge ≥13% & odds ≥2.80. Real money, per-bot toggle. |
| `bot_coolbet_ou_model_v1` | O/U 2.5 | model | 8% | 1.80 | **REAL** | Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. Real money, per-bot toggle. |

### Trigger engine · model vs sharp anchor (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_1x2_v1` | 1x2 | model | 13% | 2.80 | paper | Fires when Coolbet's 1x2 price lands in the MODEL trigger window (model edge ≥13% at Coolbet's own odds). Paper. OOS backtest −21% (adverse selection). |
| `bot_coolbet_trigger_sharp_1x2_v1` | 1x2 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |
| `bot_coolbet_trigger_ou_v1` | O/U 2.5 | model | 8% | 1.80 | paper | Fires when Coolbet's O/U 2.5 price lands in the MODEL trigger window (model edge ≥8% at Coolbet's own odds). Paper. OOS backtest +4.3% not-robust. |
| `bot_coolbet_trigger_sharp_ou_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |

### Coolbet own-price paper bots

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_ou35_model_v1` | O/U 3.5 | model | 8% | 1.80 | paper | Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic calibration). Paper. +7.8% not-robust, accruing forward. |
| `bot_corners_paper_shadow_v1` | corners | sharp | 0% | — | paper | Best Betano/Unibet corners price vs de-vigged Pinnacle corners line (sharp edge ≥0%). Forward paper test on executable corners books. |

### Internal model / strategy validators (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_v10_all` | mixed | model | — | — | paper | The calibrated reference bot: v10 model across target leagues, tier-adjusted thresholds. Honestly calibrated, +11–13% — the yardstick other bots are read against. |
| `bot_1x2_specialist` | 1x2 | — | — | — | paper | 1x2 home/away value specialist with per-strategy league whitelists. Internal paper strategy validator. |
| `bot_dnb_specialist` | DNB | — | — | — | paper | Draw-no-bet home+away specialist with per-strategy league whitelists. Internal paper strategy validator. |
| `bot_high_roi_global_v2` | 1x2 | — | — | — | paper | 1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator. |
| `bot_summer_specialist` | mixed | — | — | — | paper | League-whitelist summer-season specialist. Internal paper strategy validator. |
---

## 3. What each % means on each screen

Same-looking numbers, different meaning per screen. This is the glossary.

| Where you see it | The number | What it actually is |
|---|---|---|
| **/picks** — "edge" | model edge | `cal_prob − 1/odds` at the price the pick was found. NOT gated to 13% here — the page shows all model picks; the gate is at *placement*. |
| **/picks** — "min odds" (public) | break-even | `1/cal_prob` (edge = 0). Below this the bet is −EV under our model. |
| **/picks** — "place ≥ X.XX" (admin only) | placement trigger | the odds a pick must be offered at to clear the **real-money** floor: `1/(cal_prob − edge_floor)`, floor = 13%/8%. What the placer needs to see. |
| **/shadow-bots** — bot "ROI" | realised | settled paper/real P&L at the executable price. Retired bots' losses are in the "including retired" total only. |
| **/shadow-bots** — bot "CLV" | closing-line value | edge vs the closing line — the leading indicator; ROI is noisier at low n. |
| **`value_v1` / line-shop** — "edge ≥ 3%" | sharp edge | `P_sharp − 1/odds`. A different edge from /picks (§1). |
| **pick_triggers** — `cal_prob` | anchor prob | model prob for `model_*` strategies; **de-vigged Pinnacle prob** for `sharp_*` strategies. |

---

## 4. The real-money gate stack (Coolbet own-betting)

A pick becomes a real staked bet ONLY if it clears every gate, in order. Miss any one
→ no stake. Full detail: `docs/COOLBET_OWN_BETTING.md`.

1. **Bot is placeable** — in `scripts/place_coolbet_ui.py` `PLACEABLE_BOTS`
   (currently: `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`). Structural —
   paper bots can never reach here.
2. **Per-bot real-money toggle ON** — `coolbet_placer_bots.ui_place_enabled`.
3. **Not globally paused** — `placement_paused` / `daemons_paused` both false.
4. **Per-market edge floor** — model edge ≥ `_MIN_EDGE_BY_MARKET` (13% 1x2 / 8% O/U).
5. **Per-market odds floor** — odds ≥ `_MIN_ODDS_BY_MARKET` (2.80 / 1.80).
6. **Live-edge re-check** at current price, **maturity**, **pre-match only**,
   **blast-radius** caps.

The floors in steps 4–5 live in `workers/automation/coolbet_placer.py` and are the
single source the /picks "place ≥" hint, the trigger bots, and this map all read.

---

## 5. Deep-dive docs (this map links out; it does not duplicate them)

- `docs/BETTING_GATE_DECISIONS.md` — how the model floors (13%/8%) were decided, the canonical backtest method, why runs disagreed.
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (Stage A windows + Stage B matcher), the −21% adverse-selection finding.
- `docs/COOLBET_OWN_BETTING.md` — the full own-betting flow and gate stack.
- `docs/ANALYSIS_GOTCHAS.md` — line-shop mirage (§52), single-book vs best-of-books (§55), CLV-vs-ROI variance.
- `workers/registry/bot_registry.py` — the structured source of truth this map is built on.

---
*Last verified against code+DB by `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` on every push.*
