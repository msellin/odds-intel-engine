# Model comparison — held-out 2026-04-26 → 2026-05-09

Test slice: **6,544 finished matches** with MFV row + actual score.


## 1x2_home

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,544 | 0.3434 | 0.1047 | 0.871 | 0.1276 |
| v11_pinnacle | 6,544 | 0.3589 | 0.1040 | 0.894 | 0.1511 |
| v12_post0e | 6,544 | 0.2794 | 0.0792 | 0.896 | 0.0780 |
| v13_post0e_pin | 6,544 | 0.3454 | 0.0965 | 0.898 | 0.1407 |
| v9a_202425 (DB) | 1,759 | 0.7596 | 0.2670 | 0.567 | 0.1294 |

## 1x2_draw

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,544 | 0.4088 | 0.1271 | 0.838 | 0.1858 |
| v11_pinnacle | 6,544 | 0.3891 | 0.1127 | 0.880 | 0.1629 |
| v12_post0e | 6,544 | 0.2972 | 0.0861 | 0.890 | 0.0869 |
| v13_post0e_pin | 6,544 | 0.3858 | 0.1121 | 0.885 | 0.1648 |
| v9a_202425 (DB) | 1,759 | 0.6288 | 0.2188 | 0.707 | 0.1309 |

## 1x2_away

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,544 | 0.2697 | 0.0781 | 0.904 | 0.0950 |
| v11_pinnacle | 6,544 | 0.3005 | 0.0807 | 0.923 | 0.1317 |
| v12_post0e | 6,544 | 0.2416 | 0.0674 | 0.914 | 0.0728 |
| v13_post0e_pin | 6,544 | 0.3287 | 0.0948 | 0.907 | 0.1502 |
| v9a_202425 (DB) | 1,759 | 0.7481 | 0.2158 | 0.711 | 0.1090 |

## over_25

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,544 | 0.6489 | 0.2255 | 0.640 | 0.1356 |
| v11_pinnacle | 6,544 | 0.6278 | 0.2188 | 0.660 | 0.0436 |
| v12_post0e | 6,544 | 0.6187 | 0.2138 | 0.676 | 0.0733 |
| v13_post0e_pin | 6,544 | 0.6133 | 0.2121 | 0.665 | 0.0375 |
| v9a_202425 (DB) | 0 | — | — | — | — |

## btts_yes

| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |
|---------|--:|-----------:|--------:|---------:|------:|
| v10_pre_shadow | 6,544 | 0.9218 | 0.3125 | 0.567 | 0.2493 |
| v11_pinnacle | 6,544 | 0.6866 | 0.2468 | 0.553 | 0.0851 |
| v12_post0e | 0 | — | — | — | — |
| v13_post0e_pin | 6,544 | 0.6729 | 0.2402 | 0.570 | 0.0302 |
| v9a_202425 (DB) | 1,164 | 0.6971 | 0.2513 | 0.517 | 0.0491 |

**Caveat — leakage risk**: bundles trained on the same MFV may have already seen many matches in the test window. Numbers are upper-bound unless each candidate is retrained with an explicit `--cutoff <date>` arg first. v9a_202425 was trained on Kaggle (not MFV), so its baseline IS clean held-out — use it as the reference for whether retraining beats production.
