# VAL-POST-MORTEM — Review of 14 days of LLM post-mortems

**Date:** 2026-05-24
**Source:** `model_evaluations WHERE market='post_mortem'` (14 rows, 2026-04-28 → 2026-05-24)
**Source script:** `scripts/val_post_mortem.py`

## Headline numbers

- **60 loss classifications** parsed across 14 rows (avg 4.3 / day, range 1–9).
- **All 14 rows parsed**: 9 via strict JSON, 5 via regex fallback (LLM occasionally emits truncated / double-encoded JSON — own-goal in the writer, not the LLM).
- Category mix:

  | Category | n | share |
  |---|---:|---:|
  | MODEL_ERROR | 25 | 41.7% |
  | VARIANCE | 22 | 36.7% |
  | INFORMATION_GAP | 12 | 20.0% |
  | TIMING | 1 | 1.7% |

- Market × Category (most signal):

  | Market | MODEL_ERROR | VARIANCE | INFORMATION_GAP | Total |
  |---|---:|---:|---:|---:|
  | 1X2 | 11 | 14 | 2 | 27 |
  | BTTS | 4 | 3 | 2 | 9 |
  | OU-under | 6 | 2 | 0 | 8 |
  | OU-over | 3 | 1 | 0 | 4 |
  | ? (sniff failed) | 1 | 2 | 8 | 12 |

## Findings

### F1. High-conviction OU-under (>75% model prob) is the most reliable money-loser

Of 8 OU-under losses where the LLM categorised the cause, **6 are MODEL_ERROR** (75%). The pattern is consistent: model assigns 77–91% confidence to Under 2.5, match scores 3–6 goals.

Specific examples:
- 2026-05-11 Gil Vicente vs Arouca: model 90.6% under → final had 4 goals
- 2026-05-13 Motherwell vs Celtic: model 77.8% under → 5 goals
- 2026-05-14 DC United vs Chicago Fire: model 77.4% under → 4 goals
- 2026-05-15 Aston Villa vs Liverpool: model 86.6% under → 6 goals
- 2026-05-18 Sport Recife vs CRB: model 91.8% under → 3 goals

This isn't variance — variance would have a mix of close misses and blowouts. Six of six are blowouts. Two plausible causes:
- **Poisson variance too tight at extreme rates**: when both `λ_home + λ_away` are very low (e.g., 1.6 expected goals total), Poisson under-rates the fat right tail. Real football has more "anything can happen" variance than independent Poisson goals suggest.
- **Selection bias in training**: low-scoring matches are over-represented in our targets when both teams are defensive sides, but those matchups also generate the highest book vig — the model learns "low totals are common" without learning "low totals are also where bookmakers pad the margin most."

**Action proposal**: cap calibrated probability for OU-under at ~0.75 in the bot's `min_prob` / Kelly logic, OR mark these picks for shadow-only until we have a 2-feature Platt fit (CAL-PLATT BTTS hits in ~2 weeks per the threshold table). Decision deferred — see *Recommendation* below.

### F2. INFORMATION_GAP clusters in Scottish 2nd-tier and US lower divisions

11 of 12 INFORMATION_GAP losses are matches where pre-KO CLV was −80% to −98%. The recurring league set:

- Scottish Championship (Dundee Utd, Dunfermline, Partick, Stockport, Stevenage, Rangers, Hibernian)
- Welsh/English non-league (Stockport vs Stevenage)
- MLS / USL (FC Cincinnati vs Inter Miami — "Messi factor")
- Latvian higher tier (Super Nova vs Tukums)

The pattern is "market moves hard against us between bet placement and kickoff" — typically a lineup announcement or fitness news that we miss. Two of these duplicate exactly (Dunfermline vs Partick, Dijon vs Orleans) — same match, two bets logged with identical −87% / −0.043 drift, suggesting either a refresh-cohort dedup leak OR two bots picking the same side.

**Action proposal**: re-examine these leagues' CLV distribution in `aln1_analysis.py` style — if Scottish Championship and similar consistently show negative CLV for our picks, either deprioritise the cohort or add a "wait for confirmed lineups" gate.

### F3. Calibration sometimes flips the prediction direction by 20+ pp

The LLM flagged several cases where Stage-1 shrinkage changed the model probability so much it crossed the implied-prob threshold:

- 2026-05-10 Sényő Carnifex vs Tiszafuredi VSE: raw 10.0% → calibrated 39.6%
- 2026-05-12 Osasuna vs Atletico Madrid: raw 26.5% → calibrated 46.4%
- 2026-05-12 Samger vs Real de Banjul: raw 10.0% → calibrated 42.3%

These are mostly tier 3-4 (lower-tier) matches where `CALIBRATION_ALPHA` defaults toward the market anchor (T3=0.50, T4=0.65). When raw model says "10%" and market says "40%", the shrunk value lands at 25-30% — and the model then picks the bet based on the shrunk prob. **If the raw prob and shrunk prob disagree by >15pp, we're effectively betting the market's opinion, not the model's**, but with the model's noisy 5%-bump-for-LOW-alignment threshold.

**Action proposal**: log raw vs calibrated prob to `simulated_bets` (currently only `calibrated_prob` is stored) so we can retroactively quantify how often this happens. Then decide whether to add a "raw-vs-cal divergence" filter to the bot funnel.

### F4. LLM hallucinates structural "bugs"

The 2026-05-18 post-mortem flagged "Bogota FC vs Real Cartagena: model assigned contradictory and non-normalized probabilities (55.0% home win, 46.7% away win), leading to placing conflicting bets — fundamental flaw in probability generation or bet selection logic." Verified in DB: zero same-bot home+away conflicts since 2026-05-06. What actually happened was `inplay_o` picked Home @5.00 and `inplay_p` picked Away @4.33 — two **different** in-play bots with different strategies, working as designed.

**Action proposal**: prompt the LLM post-mortem with explicit context that bots are an independent portfolio. Otherwise these false-positive "bug reports" will eat reviewer time and risk over-corrective edits.

### F5. Data-quality: post-mortem `notes` JSON shape drifts

5 of 14 rows needed regex fallback to parse — the LLM emits double-encoded JSON or string-truncation. Not blocking VAL-POST-MORTEM today but it'll bite us if a downstream tool (e.g. a meta-tuner) tries to consume these programmatically.

**Action proposal**: lock the post-mortem writer's output to a strict schema (validate JSON before insert, retry with simpler prompt on failure). Cheap fix, ~30 min.

## Recommendations (ranked)

| # | Item | Effort | Priority |
|---|---|---|---|
| 1 | **OU-UNDER-CAP** — investigate F1. Pull all v14 OU-under bets with model_prob > 0.75 since 5/6, compute actual hit rate vs predicted hit rate. If miscalibrated, cap min_prob at 0.75 for OU-under bots or wait for 2-feature Platt | 1 h | P1 |
| 2 | **POST-MORTEM-SCHEMA** — fix F5: lock JSON shape, retry on validation failure | 30 min | P2 |
| 3 | **POST-MORTEM-CONTEXT** — fix F4: improve LLM prompt with bot-portfolio context | 15 min | P2 |
| 4 | **CALIB-DIVERGENCE-LOG** — fix F3: store `raw_model_probability` alongside `calibrated_prob` on `simulated_bets` so we can audit divergence retroactively | 1 h | P3 |
| 5 | **INFO-GAP-LEAGUE-AUDIT** — fix F2: per-league CLV distribution; deprioritise consistently-sharp leagues OR add lineup-confirmation gate | 2 h | P2 (can batch with NEWS-LINEUP-VALIDATE) |

## Not actionable

- VARIANCE losses (22 of 60): expected behaviour, no signal to act on.
- TIMING (1 of 60): too rare to act on, watch over the next 14 days.

## Re-run

```bash
python3 scripts/val_post_mortem.py
```

Outputs the same aggregations against current data. Refresh this doc whenever the category counts shift meaningfully.
