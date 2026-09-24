# Pinnacle weekend line-movement analysis — 2026-06-10

**Source:** `dev/active/pinnacle-movement-2026-06-05_to_07.csv`
**Analyzer:** `scripts/analyze_pinnacle_movement.py` (stdlib-only, deterministic)

## Coverage

- Matches captured: **54**
- Total snapshots: **275**
- Snapshots per match (median / max): **4.0 / 11**

## Drift distribution (full-window max abs per match, pp)

- n = **45** matches with ≥2 snapshots
- mean: **1.92pp**
- median: **0.64pp**
- p75: **2.03pp**
- p90: **6.88pp**
- max: **14.20pp**

## Matches with material drift (≥1.0pp implied prob shift)

| Window | matched | total | % |
|---|---|---|---|
| Full window (first → last snap) | 17 | 45 | 37.8% |
| **Final hour (T-60 → last)** | **11** | **28** | **39.3%** |
| Final 15min (T-15 → last) | 7 | 34 | 20.6% |

## DECISION CRITERIA (frozen pre-data)

Threshold metric: **% of matches with ≥1.0pp implied-prob max-abs shift in the final hour (T-60 → close).**

- **STRONG case** (≥20%): material late drift is common enough that AF's 3h refresh cycle is missing meaningful movement on weekends. Build a weekend-only Pinnacle direct-poll job.
- **MARGINAL** (10%-20%): mixed evidence. File a more targeted follow-up — likely scoped to a specific league or kickoff window where drift concentrates.
- **NEGATIVE** (<10%): late drift is rare. AF 3h cycle is fine; don't burn engineering on weekend Pinnacle polling. Close the question.

### Verdict: **STRONG**

39.3% of matches with a T-60 snapshot showed ≥1pp implied-prob movement in the final hour. Recommend building a weekend-only Pinnacle direct-poll job (≤30 req/h, scoped to top-N leagues by drift concentration — see league table below).

## Per-league drift summary (≥3 matches captured)

| League | n | mean max-abs (pp) | median max-abs (pp) | max drift (pp) |
|---|---|---|---|---|
| USL League One Cup | 3 | 8.31 | 6.93 | 14.20 |
| MLS Next Pro | 3 | 5.14 | 6.76 | 8.48 |
| J2/J3 League | 3 | 2.20 | 2.84 | 3.18 |
| Friendlies | 6 | 0.86 | 0.56 | 2.06 |
| Victoria NPL 2 | 3 | 0.74 | 0.74 | 0.83 |
| USL League Two | 6 | 0.67 | 0.35 | 2.00 |
| Segunda División | 4 | 0.22 | 0.09 | 0.69 |

## Top 5 single-match movers (full-window max-abs)

| Match | League | n_snaps | first T-KO | last T-KO | max-abs (pp) |
|---|---|---|---|---|---|
| Indy Eleven vs Forward Madison | USL League One Cup | 5 | 88min | 2min | 14.20 |
| Crown Legacy vs Philadelphia Union II | MLS Next Pro | 10 | 196min | 2min | 8.48 |
| Vancouver FC vs Atlético Ottawa | Canadian Premier League | 11 | 238min | 16min | 8.39 |
| Charleston Battery vs Pittsburgh Riverhounds | USL League One Cup | 5 | 88min | 2min | 6.93 |
| Hai Phong vs Nam Dinh | V.League 1 | 9 | 172min | 4min | 6.85 |

---

## Reproducibility

- Analyzer: `scripts/analyze_pinnacle_movement.py`
- Source CSV: `dev/active/pinnacle-movement-2026-06-05_to_07.csv`
- Material-shift threshold: `1.0pp` (frozen pre-data)
- Decision thresholds: strong ≥20.0%, marginal ≥10.0%, negative <10.0%
- Run command: `python3 scripts/analyze_pinnacle_movement.py`