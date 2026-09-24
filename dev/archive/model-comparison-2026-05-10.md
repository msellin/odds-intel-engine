# Model comparison — held-out 2026-04-26 → 2026-05-09

Test slice: **6,542 finished matches** with MFV row + actual score.


## 1x2_home

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,542 | 0.3430 | 0.1045 | 0.871 | 0.1263 |
| v11_pinnacle | 6,542 | 0.3587 | 0.1039 | 0.894 | 0.1513 |
| v9a_202425 (DB) | 1,759 | 0.7596 | 0.2670 | 0.567 | 0.1294 |

## 1x2_draw

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,542 | 0.4083 | 0.1269 | 0.838 | 0.1848 |
| v11_pinnacle | 6,542 | 0.3894 | 0.1128 | 0.879 | 0.1624 |
| v9a_202425 (DB) | 1,759 | 0.6288 | 0.2188 | 0.707 | 0.1309 |

## 1x2_away

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,542 | 0.2689 | 0.0778 | 0.904 | 0.0940 |
| v11_pinnacle | 6,542 | 0.3003 | 0.0806 | 0.922 | 0.1312 |
| v9a_202425 (DB) | 1,759 | 0.7481 | 0.2158 | 0.711 | 0.1090 |

## over_25

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,542 | 0.6488 | 0.2256 | 0.640 | 0.1335 |
| v11_pinnacle | 6,542 | 0.6282 | 0.2190 | 0.658 | 0.0428 |
| v9a_202425 (DB) | 0 | — | — | — | — |

## btts_yes

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,542 | 0.9209 | 0.3126 | 0.565 | 0.2502 |
| v11_pinnacle | 6,542 | 0.6868 | 0.2469 | 0.553 | 0.0854 |
| v9a_202425 (DB) | 1,164 | 0.6971 | 0.2513 | 0.517 | 0.0491 |

**Caveat — leakage risk**: bundles trained on the same MFV may have already seen many matches in the test window. Numbers are upper-bound unless each candidate is retrained with an explicit `--cutoff <date>` arg first. v9a_202425 was trained on Kaggle (not MFV), so its baseline IS clean held-out — use it as the reference for whether retraining beats production.
