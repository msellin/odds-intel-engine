# Pinnacle weekend line-movement analysis — 2026-06-10

**Source:** `dev/active/pinnacle-movement-2026-06-05.csv`
**Analyzer:** `scripts/analyze_pinnacle_movement.py` (stdlib-only, deterministic)

## Coverage

- Matches captured: **15**
- Total snapshots: **153**
- Snapshots per match (median / max): **11 / 11**

## Drift distribution (full-window max abs per match, pp)

- n = **15** matches with ≥2 snapshots
- mean: **2.50pp**
- median: **0.83pp**
- p75: **3.18pp**
- p90: **8.43pp**
- max: **8.48pp**

## Matches with material drift (≥1.0pp implied prob shift)

| Window | matched | total | % |
|---|---|---|---|
| Full window (first → last snap) | 7 | 15 | 46.7% |
| **Final hour (T-60 → last)** | **5** | **15** | **33.3%** |
| Final 15min (T-15 → last) | 1 | 5 | 20.0% |

## DECISION CRITERIA (frozen pre-data)

Threshold metric: **% of matches with ≥1.0pp implied-prob max-abs shift in the final hour (T-60 → close).**

- **STRONG case** (≥20%): material late drift is common enough that AF's 3h refresh cycle is missing meaningful movement on weekends. Build a weekend-only Pinnacle direct-poll job.
- **MARGINAL** (10%-20%): mixed evidence. File a more targeted follow-up — likely scoped to a specific league or kickoff window where drift concentrates.
- **NEGATIVE** (<10%): late drift is rare. AF 3h cycle is fine; don't burn engineering on weekend Pinnacle polling. Close the question.

### Verdict: **STRONG**

33.3% of matches with a T-60 snapshot showed ≥1pp implied-prob movement in the final hour. Recommend building a weekend-only Pinnacle direct-poll job (≤30 req/h, scoped to top-N leagues by drift concentration — see league table below).

## Per-league drift summary (≥3 matches captured)

| League | n | mean max-abs (pp) | median max-abs (pp) | max drift (pp) |
|---|---|---|---|---|
| J2/J3 League | 3 | 2.20 | 2.84 | 3.18 |
| Friendlies | 3 | 1.29 | 1.47 | 2.06 |
| Victoria NPL 2 | 3 | 0.74 | 0.74 | 0.83 |

## Top 5 single-match movers (full-window max-abs)

| Match | League | n_snaps | first T-KO | last T-KO | max-abs (pp) |
|---|---|---|---|---|---|
| Crown Legacy vs Philadelphia Union II | MLS Next Pro | 10 | 196min | 2min | 8.48 |
| Vancouver FC vs Atlético Ottawa | Canadian Premier League | 11 | 238min | 16min | 8.39 |
| Vancouver Whitecaps II vs Portland Timbers II | MLS Next Pro | 11 | 237min | 18min | 6.76 |
| Matsumoto Yamaga vs Nara Club | J2/J3 League | 11 | 236min | 15min | 3.18 |
| Yokohama FC vs Renofa Yamaguchi | J2/J3 League | 11 | 236min | 15min | 2.84 |

---

## Reproducibility

- Analyzer: `scripts/analyze_pinnacle_movement.py`
- Source CSV: `dev/active/pinnacle-movement-2026-06-05.csv`
- Material-shift threshold: `1.0pp` (frozen pre-data)
- Decision thresholds: strong ≥20.0%, marginal ≥10.0%, negative <10.0%
- Run command: `python3 scripts/analyze_pinnacle_movement.py`