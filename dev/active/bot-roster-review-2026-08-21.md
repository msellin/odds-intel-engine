# Bot Roster Review — 2026-08-21

**Trigger:** user flagged that we have ~10 active bots + 4 beta bots that are negative — should we retire some? Also: what would retirement do to the hero numbers?

**Method:** queried live DB for all non-retired bots + 60d and 30d ROI/CLV per bot, plus per-bot hero-cohort impact (retire → recompute hero ROI/CLV/n). Data pulled 2026-08-21.

**Output:** proposal only. No retirements executed.

---

## Current hero baseline

Before any changes:
> **n=1,208 · ROI +10.37% · CLV +9.64% · PnL €+751**
> (`getCalibratedHeadlineStats` — maturity IN calibrated/beta/active, market IN 1x2/o/u/btts, since 2026-05-04, settled only)

---

## Full roster (43 non-retired bots)

Not the ~10 the user mentioned — the DB has **43 rows with `retired_at IS NULL`**. Grouping:

- **12 CS2 bots** — all zero volume in 60d (CS2_ENABLED=false env kills them at the scheduler); marked active in DB but functionally dead. Roster clutter.
- **~5 leagues-starved bots** — `bot_dnb_specialist`, `bot_opt_away_british/europe`, `bot_opt_ou_british`, `inplay_q` — 0 bets in 60d, summer-league gap.
- **~15 with real recent activity** — the useful list.
- **6 experimental** — shadow bots not in hero cohort (fine as-is).

Full per-bot 60d + 30d table (only bots with any 60d activity):

```
BOT                         MAT           60d n  60d ROI%  60d CLV%   30d n  30d ROI%  30d CLV%
bot_conservative            active            3   -35.91    +41.31       0      —         —
bot_greek_turkish           active            1  -100.00    -29.56       1  -100.00    -29.56
bot_opt_home_lower          active           11   -25.66    +13.76       7   +21.01    +16.87
inplay_c                    active           18   -27.63       —        11   -69.09       —
inplay_o                    active            3   +91.73       —         1  +187.60       —
bot_btts_all                beta             22   +19.01     +4.82      22   +19.01     +4.82
bot_high_roi_global_v2      beta             14   +10.49    +24.94       6   -80.10    +18.14
bot_ou_specialist           beta              2   -36.79     -0.26       1  -100.00     +0.00
bot_proven_leagues_v2       beta              7   +22.12     +9.06       6   +42.70     +8.92
bot_summer_specialist       beta             13   -53.66    +14.48      11   -58.54    +13.09
inplay_a                    beta              2    -8.80       —         1   +82.40       —
inplay_b                    beta              9   -18.87       —         7   -19.63       —
inplay_btts_dryspell_v1     beta              4   +25.00       —         2  -100.00       —
inplay_btts_press_v1        beta             70   -13.36       —        49   -22.62       —
inplay_d                    beta              2  -100.00       —         1  -100.00       —
inplay_g                    beta              2   +12.50       —         1  -100.00       —
inplay_h                    beta              3   +30.80       —         2    +5.00       —
inplay_m                    beta             18   +54.73       —         8   +78.78       —
bot_1x2_specialist          calibrated        1  +149.94     +6.84       1  +149.94     +6.84
bot_v10_all                 calibrated      127   +22.16    +24.66      67   +43.38    +25.79
inplay_l                    calibrated       67    +9.89       —        38    +1.71       —
bot_btts_v2                 experimental      3  +117.68     +7.32       2  +122.42     +4.76
```

(Bots at 0 60d bets omitted — see "zero-volume" section below.)

---

## Retirement candidates (ordered by confidence)

### Definite retire — clearly dead / consistent losing

**1. `bot_greek_turkish` (active)** — 1 bet in 60d (−100% ROI, −30% CLV). Not just thin — the single settled bet was a loss on a negative-CLV pick, meaning the model was on the wrong side of a sharp move. Filed as active but the volume trickle shows it's not finding picks.
- **Hero impact of retiring**: n 1208 → 1203 (−5), ROI +10.37% → **+10.91%** (+0.54pp), CLV +9.64% → +9.97% (+0.33pp).

**2. `inplay_c` (active)** — 18 bets in 60d at −27.63% ROI. 30d trend WORSE: 11 bets at −69.09%. This is the "getting worse" pattern that killed inplay_e (retired 2026-07-31 for same reason — fixture-mix shift after European season ended).
- **Hero impact**: n 1208 → 1136 (−72), ROI +10.37% → **+11.30%** (+0.93pp), CLV unchanged.

**3. `inplay_btts_press_v1` (beta)** — 70 bets in 60d at −13.36%, 49 bets in 30d at −22.62%. Highest-volume losing bot. Getting worse.
- **Hero impact**: n 1208 → 1117 (−91), ROI +10.37% → **+11.37%** (+1.00pp).

**4. `inplay_d` (beta)** — 2 bets in 60d, both −100% ROI. Effectively dead like inplay_e.
- **Hero impact**: n 1208 → 1202 (−6), ROI −0.04pp (essentially neutral).

**5. `bot_ou_specialist` (beta)** — 2 bets in 60d (−36.79%), 1 bet in 30d (−100%). Near-zombie, negative CLV (−0.26%). Volume trickle + no signal.
- **Hero impact**: n 1208 → 1199 (−9), ROI +10.37% → +10.81% (+0.44pp), CLV +0.09pp.

**Combined estimated hero impact if all 5 retired** (deltas are not strictly additive but the direction holds): **ROI +10.37% → ~+12.5-13%**, CLV +9.64% → ~+10%, n 1208 → ~1030, PnL €751 → ~€830 (kill the losing bets, and the ROI% on the remaining base is higher).

### CS2 cleanup — 12 bots that are already functionally retired

The `CS2_ENABLED=false` env at scheduler level (CS2-DISABLE-2026-07-31) means every CS2 bot has been silent for 3 weeks. They're `is_active=true` in DB but the scheduler never fires them. Cleanup:

- `bot_cs2_a1m_specialist_v1`, `bot_cs2_aggressive_v1`, `bot_cs2_clean_sweep_v1`, `bot_cs2_dog_v1`, `bot_cs2_fav_v1`, `bot_cs2_hltv_v1`, `bot_cs2_map1_winner_v1`, `bot_cs2_total_maps_v1`, `bot_cs2_v7`, `bot_cs2_v8`, `bot_cs2_value_v1`

Retire them all (`is_active=false`, `retired_at=NOW()`, `maturity_label='retired'`, `retired_reason='CS2-DISABLE cleanup 2026-08-21'`). Zero hero impact (CS2 not in soccer markets), but the leaderboard funnel line will read cleaner: "60 tested" → "72 tested · 47 retired" (more honest reflection of what we've tried and killed).

### Watch — thin sample, negative recent, but has some signal or expected to improve

**`bot_summer_specialist` (beta)** — 60d −53% ROI (n=13), 30d −58% (n=11). But **CLV is strongly positive +14.48% 60d** — the model IS beating the closing line, it's variance-hurting. Filed already: BOT-SUMMER-SPECIALIST re-review at n≥30 (~2026-09-15). **Keep for now.** If still negative + CLV drops below +5% by then, retire.

**`inplay_b` (beta)** — n=9, −18.87% ROI. Too thin to conclude. Watch.

**`bot_high_roi_global_v2` (beta)** — 60d +10.49% (n=14), 30d −80.10% (n=6). Recent losing streak, but sample is tiny. CLV is +18% positive throughout. Keep — one of the few beta bots with positive 60d ROI.

**`bot_conservative` (active)** — 60d −35% on n=3 (30d n=0). Thin. Not enough volume to judge either way; historical hero contribution positive. Watch.

### Summer-starved (0 volume, expected to unblock at Aug 15+ season restart)

- `bot_dnb_specialist` — already diagnosed as summer-starved in DNB-ZOMBIE-DIAGNOSIS. Auto-unblock post Aug 15. Watch for volume return.
- `bot_opt_away_british`, `bot_opt_away_europe`, `bot_opt_ou_british` — similar pattern.
- `inplay_q` — 0 bets, no historical context.

**Don't retire these** yet — the European Aug 15 season restart is 5-10 days old; give them 2-3 weeks of restart data before deciding.

### Winners — keep

- **`bot_v10_all`** (calibrated) — 60d +22.16% ROI n=127, 30d +43.38% n=67. The workhorse.
- **`inplay_l`** (calibrated) — 60d +9.89% n=67. Solid.
- **`bot_1x2_specialist`** (calibrated) — thin (n=1) but positive lifetime.
- **`bot_btts_all`** (beta) — 60d +19.01%. The dedicated BTTS bot.
- **`bot_high_roi_global_v2`** (beta) — see above.
- **`bot_proven_leagues_v2`** (beta) — 60d +22% n=7.
- **`bot_opt_home_lower`** (active) — 60d −25% but 30d +21%. Recovering; keep.
- **`inplay_o`, `inplay_h`, `inplay_m`, `inplay_a`** — positive or thin-positive.

---

## Recommended action plan

**Phase 1 (immediate, quick DB update):**
- Retire 5 bots: `bot_greek_turkish`, `inplay_c`, `inplay_btts_press_v1`, `inplay_d`, `bot_ou_specialist`.
- CS2 cleanup: retire the 11 CS2 `is_active=true` rows (already functionally dead).
- Estimated hero movement: ROI +10.37% → ~+12.5%, CLV +9.64% → ~+10%, PnL €751 → ~€830.
- **How to ship**: single migration file (`supabase/migrations/NNN_bot_retirements_2026_08_21.sql`) with 16 `UPDATE bots SET is_active=false, retired_at=NOW(), maturity_label='retired', retired_reason='...' WHERE name IN (...)` — one row per bot with a specific reason. GitHub Actions auto-applies on merge.

**Phase 2 (watch, decide by ~2026-09-15):**
- `bot_summer_specialist` — n hits ≥30, re-check ROI + CLV.
- `bot_dnb_specialist` + summer-starved bots — check if Aug 15 restart brings volume back.
- `inplay_b`, `bot_conservative` — watch for either recovery or further data.

**Phase 3 (structural change, optional):**
- Add an "auto-retire" cron: any bot with n≥20 AND 60d ROI ≤ −10% AND 60d CLV ≤ +2% for 3 consecutive weekly checks gets auto-retired with a Telegram alert. Would have caught inplay_btts_press_v1 and inplay_c automatically.

---

## Hero-impact math — for the record

You asked: if we retire bots, do the hero numbers change? **Yes, and here's the full ranking of hero-impact if each bot were retired individually** (highest ROI improvement first):

```
IF WE RETIRED THIS BOT       n_left    ROI%     CLV%       Δroi    Δclv
bot_btts_all                  1016  +13.21%  +11.22%     +2.84   +1.59  ⚠ 60d +19%, keep despite hero-impact
inplay_btts_press_v1          1117  +11.37%   +9.64%     +1.00    +0    ✅ RETIRE
inplay_c                      1136  +11.30%   +9.64%     +0.93    +0    ✅ RETIRE
bot_summer_specialist         1195  +11.06%   +9.56%     +0.69   -0.08  ⏳ watch
bot_greek_turkish             1203  +10.91%   +9.97%     +0.54   +0.33  ✅ RETIRE
bot_ou_specialist             1199  +10.81%   +9.72%     +0.44   +0.09  ✅ RETIRE
inplay_h                      1204  +10.41%   +9.56%     +0.03   -0.08
bot_opt_home_lower            1152  +10.50%   +9.88%     +0.13   +0.24
```

**Important caveat on `bot_btts_all`**: retiring it would give the biggest single hero boost (+2.84pp ROI, +1.59pp CLV). But its **recent 60d ROI is +19%** and CLV +4.8% — it's a bot that had a bad early period and has recovered. Retiring based only on hero-drag would kill a currently-profitable strategy. **Don't retire on hero math alone** — use it as a signal, then check recent trend before pulling the trigger.

The Phase 1 candidates above all satisfy BOTH criteria: hero-drag AND currently-losing 60d/30d.

---

## Actual before/after simulation (2026-08-21, applied against live DB in memory)

Running the retirement list through the live hero query and cache-rebuild logic without touching prod produced:

**Hero cohort (`getCalibratedHeadlineStats`)**

| | BEFORE | AFTER (16 retired) | Δ |
|---|---:|---:|---:|
| n | 1,208 | **1,025** | −183 |
| ROI | +10.37% | **+13.54%** | **+3.17 pp** |
| CLV mean | +9.64% | +10.06% | +0.42 pp |
| CLV-beat rate | 80.7% | 81.2% | +0.5 pp |
| PnL total | €+751 | **€+852** | **+€101** |
| **Last 30d ROI** | +9.92% | **+22.58%** | **+12.66 pp** ← biggest recent-window impact |
| Last 30d PnL | €+145 | €+258 | +€113 |

**Leaderboard funnel**

| | BEFORE | AFTER |
|---|---:|---:|
| Non-retired non-experimental non-in-play non-CS2 soccer | 14 | 12 |
| Retired | 35 | 51 |
| Total tested (excl. experimental) | 49 | 63 |

**90d cumulative PnL curve endpoint**: €+491 → €+550 (+€59) — the chart's trailing peak moves up modestly.

**Hero per-market after retirement**:
| Market | n | ROI | CLV | PnL |
|---|---:|---:|---:|---:|
| 1x2 | 476 | +21.54% | +13.75% | €+603 |
| o/u | 353 | +12.78% | +6.21% | €+276 |
| btts | 196 | −2.06% | +4.51% | €−27 |

(BTTS is now the drag — bot_btts_all's historical early-period losses are still counted in the all-time cohort. Its recent 60d is +19% so the drag reverses over time; don't retire on this alone.)

---

## Deploying

Migration file: `supabase/migrations/273_bot_cleanup_2026_08_21.sql` — 5 UPDATEs for the losing bots + 1 batch UPDATE for the 11 CS2 bots. Idempotent (running twice is a no-op).

**Two paths to apply, choose one:**

1. **CI path** — push to main and let `.github/workflows/migrate.yml` handle it (as documented in CLAUDE.md).
2. **SSH path** — apply directly on VPS if the CI path is broken post-SUPABASE-TO-VPS:
   ```bash
   scp supabase/migrations/273_bot_cleanup_2026_08_21.sql root@204.168.199.8:/tmp/
   ssh root@204.168.199.8 'psql -U oddsintel -d oddsintel -f /tmp/273_bot_cleanup_2026_08_21.sql'
   ```

**After applying — force fresh dashboard_cache (or wait for 21:00 UTC settlement):**
```bash
ssh root@204.168.199.8 'cd /opt/oddsintel && sudo -u oddsintel .venv/bin/python -c "from workers.jobs.settlement import write_dashboard_cache; write_dashboard_cache()"'
```

**Immediate vs delayed effects:**
- **Immediate (< 10 min, on next Next.js ISR revalidate):** hero ROI/CLV, bot leaderboard active list, history table cohort filter.
- **After `write_dashboard_cache()`:** cumulative PnL 30d/90d chart curve, per-bot breakdown in the leaderboard cached data.
- **Automatic at next 21:00 UTC settlement:** the above happens for free.

**Rollback**: `UPDATE bots SET is_active=true, retired_at=NULL, maturity_label='<previous>' WHERE name IN (...)`. Full previous maturity values are in the bots table history — capture them from a fresh `pg_dump bots` before running the migration if you want a bulletproof revert path.

