# ACCA-LEG-SHADOW-EVAL — 2026-06-03

## Context

The acca bot (`bot_acca_coolbet` + 4 variants) uses looser filters than the
singles bots so it can land enough matches to build accumulators on Coolbet's
~130-league pool. When ACCA-LEG-SHADOW shipped 2026-05-25 we started logging
every acca leg as a hypothetical single bet in `shadow_bets` under
`bot_acca_leg_shadow`, with the hypothesis: **do the wider acca filters catch
+EV singles that our production singles bots are missing?**

Gate was 30 settled legs / target 2026-07-15. As of 2026-06-03 we have **59 settled**
— gate exceeded almost 2× ahead of schedule, so the eval ran early.

## Headline numbers

```
bot_acca_leg_shadow (last 60d, settled only):
  N = 59 | staked $590 | PnL +$52.17 | ROI +8.84% | hit-rate 64.4%
```

ROI +8.84% on N=59 isn't statistically airtight (SE ≈ ±18pp) but the
**per-market breakdown** is what matters — see below.

## Per-market comparison vs production singles

| Market | Selection | Shadow N | Shadow ROI | Production bot | Prod N | Prod ROI | Delta | Verdict |
|---|---|---|---|---|---|---|---|---|
| btts | no | 9 | **+32.89%** | bot_btts_all (no) | 64 | -1.81% | +34.7pp | Small N but suggests wider filters catch +EV |
| btts | no | — | — | bot_btts_conservative (no) | 10 | +2.76% | +30.1pp | Conservative variant agrees direction |
| btts | yes | 17 | **+9.18%** | bot_btts_all (yes) | **106** | **-7.04%** | **+16.2pp** | **Strong signal — large production loss, shadow beats** |
| btts | yes | — | — | bot_btts_conservative (yes) | 33 | +3.08% | +6.1pp | Both positive, shadow higher |
| over_under_15 | over | 7 | +36.96% | (none) | — | — | — | Unaddressed market — worth exploring |
| over_under_25 | over | 12 | **+4.00%** | bot_ou25_global (over 2.5) | 27 | -11.50% | +15.5pp | Shadow beats — widen candidate |
| over_under_25 | under | 7 | -6.29% | bot_ou25_global (under 2.5) | **41** | **+21.53%** | -27.8pp | **Production singles much better — DO NOT widen** |
| over_under_35 | over | 2 | -2.50% | bot_ou35_attacking (over 3.5) | 15 | -74.31% | +71.8pp | Both losing but prod is catastrophic; sample too small |
| over_under_35 | under | 5 | -37.60% | bot_ou35_attacking (under 3.5) | 18 | -10.53% | -27.1pp | Both losing, shadow worse |

## Decision tree application (per PRIORITY_QUEUE decision rule)

The original eval rule: *"(a) shadow ROI ≥ +3% sustained and beats the matching singles' ROI → widen one singles bot's filters experimentally"*

### Strongest case: `bot_btts_all` (yes)

- Production cohort: N=106 (the largest single-bot/single-selection cohort in
  the comparison), ROI -7.04%, total PnL ≈ -$50 over 60d
- Shadow cohort: N=17, ROI +9.18%
- Delta: +16.2pp with a meaningful production sample
- Annualised production drag at current sizing: roughly -$300/year if
  unaddressed
- **Recommendation**: file `BOT-BTTS-ALL-WIDEN-EXPERIMENT` — clone the bot as
  `bot_btts_all_v2` with one filter loosened at a time (probably min_edge or
  the alignment veto), run in shadow for 4 weeks, compare against production
  before swapping.

### Second case: `bot_ou25_global` (over 2.5)

- Production: N=27, ROI -11.50%
- Shadow: N=12, ROI +4.00%
- Smaller production sample than BTTS but same directional signal.
- **Recommendation**: piggyback on the BTTS experiment — clone
  `bot_ou25_global` as a v2 with the same single-filter loosening pattern.

### DO NOT widen: `bot_ou25_global` (under 2.5)

- Production: N=41, ROI **+21.53%** — already strong.
- Shadow: N=7, ROI -6.29%.
- The wider acca filters HURT here. Production tightness is correct.
- Implication: any widening experiment must be selection-aware (over vs under
  are different beasts in OU markets).

### Unaddressed market: OU 1.5 over

- Shadow N=7 at +36.96% ROI on a market no production bot covers.
- Too small to act on but worth filing `BOT-OU15-OVER-EXPLORE` as a P2.
- Caveat: OU 1.5 is a high-favorite line (typical odds ~1.20-1.30). Flat
  stake makes the ROI look big; Kelly sizing would shrink it. Re-check after
  N≥30.

## What's NOT worth widening

- OU 3.5 — both shadow and production lose. Separate problem (the model
  has known calibration issues at extreme totals). Don't conflate.
- DC, AH — bot_acca_leg_shadow doesn't have meaningful sample in these
  markets after 60d.

## Followups filed

- `BOT-BTTS-ALL-WIDEN-EXPERIMENT` (P1, ~3h) — clone + shadow experiment
- `BOT-OU25-OVER-WIDEN-EXPERIMENT` (P2, ~2h) — piggyback on above
- `BOT-OU15-OVER-EXPLORE` (P2, defer until N≥30) — new bot exploration
- `ACCA-LEG-SHADOW-EVAL-REPEAT` (P3, target 2026-09-01) — re-run with 4x more
  data; some sub-cohort N=2-9 today need more time to stabilise

## Reproduction

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import sys; sys.path.insert(0, '.')
from workers.api_clients.db import execute_query
# shadow:
r = execute_query('''SELECT market, selection, COUNT(*) AS n,
    SUM(stake)::numeric(10,2) AS staked, SUM(pnl)::numeric(10,2) AS pnl,
    ROUND((SUM(pnl) / NULLIF(SUM(stake), 0) * 100)::numeric, 2) AS roi
FROM shadow_bets
WHERE bot_id=(SELECT id FROM bots WHERE name='bot_acca_leg_shadow')
  AND result::text IN ('won','lost')
  AND created_at >= NOW() - INTERVAL '60 days'
GROUP BY market, selection ORDER BY market, selection''')
for x in r: print(x)
"
```
