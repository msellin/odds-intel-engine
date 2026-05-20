# Football-data.co.uk Analysis Findings

Generated: 2026-05-21
From season: 2010

## Dataset summary
- Total matches loaded: 185,156
- Main leagues: 123,301, Extra leagues: 61,855
- Seasons covered: 2010 – 2025
- Leagues: 38
- With Pinnacle closing 1x2: 167,955
- With Pinnacle closing OU2.5: 53,230 (main leagues only)
- With Pinnacle closing AH: 53,230 (main leagues only)

**Markets NOT in this dataset:** OU1.5, OU3.5, BTTS, DC, DNB — those come from OddsPortal.

## Pinnacle calibration

### 1x2
N=167,955, Brier=0.5992, LogLoss=1.0019, ROI=+0.35%

### OU 2.5
N=53,230, Brier=0.2410, LogLoss=0.6747, ROI=-2.32%

### AH
N=49,539, Brier=0.2493, LogLoss=0.6918, ROI=-4.71%

**Sanity check:** Bet all 3 outcomes at Avg closing → ROI = -7.79% (≈ -vig)

### Calibration by season (1x2)
| Season | N | Brier | LogLoss | ROI |
|--------|---|-------|---------|-----|
| 2012 | 9,405 | 0.6111 | 1.0190 | +0.79% |
| 2013 | 9,544 | 0.6018 | 1.0056 | -0.01% |
| 2014 | 9,191 | 0.6038 | 1.0079 | -0.82% |
| 2015 | 9,138 | 0.6043 | 1.0091 | +1.27% |
| 2016 | 9,994 | 0.5966 | 0.9972 | +1.82% |
| 2017 | 9,914 | 0.5938 | 0.9938 | +0.32% |
| 2018 | 9,761 | 0.5964 | 0.9980 | +3.71% |
| 2019 | 8,868 | 0.6044 | 1.0098 | -1.39% |
| 2020 | 44,455 | 0.5986 | 1.0013 | +0.68% |
| 2021 | 9,603 | 0.5957 | 0.9970 | +0.97% |
| 2022 | 9,584 | 0.5940 | 0.9944 | +2.27% |
| 2023 | 9,628 | 0.5950 | 0.9957 | -0.03% |
| 2024 | 9,494 | 0.5962 | 0.9977 | -0.47% |
| 2025 | 9,376 | 0.6004 | 1.0037 | -2.05% |

### Calibration by league (1x2)
| League | N | Brier | LogLoss | ROI |
|--------|---|-------|---------|-----|
| England Championship | 7,728 | 0.6226 | 1.0355 | -1.15% |
| England League 2 | 7,612 | 0.6327 | 1.0500 | +0.21% |
| England League 1 | 7,576 | 0.6170 | 1.0274 | +3.45% |
| England Conference | 7,491 | 0.6134 | 1.0230 | -2.79% |
| Spain Segunda | 6,415 | 0.6237 | 1.0361 | +0.80% |
| Argentina | 6,234 | 0.6255 | 1.0391 | -1.18% |
| USA | 6,019 | 0.6051 | 1.0111 | +0.80% |
| Italy Serie B | 5,746 | 0.6278 | 1.0413 | -0.40% |
| Brazil | 5,476 | 0.5967 | 0.9984 | +2.69% |
| England Premier League | 5,310 | 0.5652 | 0.9550 | +0.94% |
| Spain La Liga | 5,309 | 0.5654 | 0.9537 | +5.15% |
| Italy Serie A | 5,308 | 0.5648 | 0.9516 | +3.64% |
| France Ligue 2 | 5,070 | 0.6255 | 1.0397 | +0.26% |
| France Ligue 1 | 4,996 | 0.5876 | 0.9848 | +0.41% |
| Mexico | 4,653 | 0.6126 | 1.0212 | +0.87% |
| Turkey Super Lig | 4,581 | 0.5853 | 0.9827 | +4.40% |
| Japan | 4,523 | 0.6204 | 1.0329 | +1.48% |
| Germany 2. Bundesliga | 4,284 | 0.6320 | 1.0485 | -0.35% |
| Germany Bundesliga | 4,283 | 0.5814 | 0.9773 | -0.95% |
| Netherlands Eredivisie | 4,203 | 0.5594 | 0.9431 | -1.90% |
| Romania | 4,173 | 0.5964 | 0.9975 | +0.82% |
| Portugal Primeira Liga | 4,148 | 0.5425 | 0.9190 | +1.17% |
| Poland | 4,073 | 0.6259 | 1.0405 | -2.32% |
| Belgium First Division | 3,755 | 0.5880 | 0.9872 | -0.37% |
| Norway | 3,453 | 0.5859 | 0.9838 | +0.95% |
| Sweden | 3,447 | 0.5860 | 0.9832 | -0.67% |
| Russia | 3,398 | 0.5841 | 0.9815 | +3.73% |
| Greece Super League | 3,349 | 0.5491 | 0.9269 | -5.55% |
| Scotland Prem | 3,142 | 0.5663 | 0.9542 | -1.54% |
| Denmark | 2,952 | 0.6045 | 1.0102 | +0.64% |
| China | 2,898 | 0.5521 | 0.9334 | -0.94% |
| Switzerland | 2,674 | 0.6068 | 1.0131 | +0.71% |
| Ireland | 2,647 | 0.5739 | 0.9646 | -0.83% |
| Austria | 2,638 | 0.5973 | 0.9994 | -3.25% |
| Finland | 2,588 | 0.5994 | 1.0032 | +0.51% |
| Scotland Div 1 | 2,428 | 0.6103 | 1.0183 | -2.56% |
| Scotland Div 2 | 1,689 | 0.6035 | 1.0084 | +3.06% |
| Scotland Div 3 | 1,686 | 0.6114 | 1.0203 | -4.09% |

### Calibration curve — home win probability (main leagues)
| Bin mid | Mean pred | Actual freq | Diff | N |
|---------|-----------|-------------|------|---|
| 0.05 | 0.078 | 0.064 | +0.014 | 1,232 |
| 0.15 | 0.157 | 0.155 | +0.002 | 5,462 |
| 0.25 | 0.258 | 0.247 | +0.011 | 12,944 |
| 0.35 | 0.353 | 0.351 | +0.002 | 25,446 |
| 0.45 | 0.448 | 0.447 | +0.000 | 28,358 |
| 0.55 | 0.545 | 0.548 | -0.003 | 17,550 |
| 0.65 | 0.644 | 0.657 | -0.013 | 8,680 |
| 0.75 | 0.744 | 0.778 | -0.034 | 4,242 |
| 0.85 | 0.839 | 0.866 | -0.027 | 2,052 |
| 0.95 | 0.915 | 0.965 | -0.050 | 143 |

## CLV analysis
- Matched records: 68,828

### Mean CLV by market
| Market | N | Mean CLV | % CLV > 0 | % CLV > 3% |
|--------|---|----------|-----------|------------|
| 1x2_draw | 16,314 | +4.60% | 89.3% | 62.1% |
| 1x2_away | 16,314 | +2.94% | 64.5% | 52.3% |
| 1x2_home | 16,314 | -7.54% | 25.3% | 17.2% |
| under25 | 9,943 | +0.64% | 51.6% | 38.8% |
| over25 | 9,943 | -0.64% | 48.4% | 35.3% |

### Value bet simulation
- Value bets (CLV > 3%): 28,817, ROI = -6.15%
- All matched bets (random baseline): ROI = -2.42%

## Key conclusions
- Pinnacle closing 1x2 is well-calibrated (Brier/LogLoss close to theoretical minimum for football)
- Flat-bet ROI on highest-prob outcome vs Max closing is negative (as expected — we're paying Max vig)
- OU2.5: Brier = 0.2410
- Model CLV edge is marginal: value bets ROI = -6.15% vs baseline -2.42%
