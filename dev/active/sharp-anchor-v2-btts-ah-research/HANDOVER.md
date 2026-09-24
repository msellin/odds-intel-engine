Parent row: **#119 SHARP-ANCHOR-V2** (step E — new AH/BTTS PICKS bots). Handover written 2026-09-24.

# Handover — BTTS & Asian handicap research for #119 (E)

**From:** the session "In-game odds improvement betting strategy" (owner asked: *can we
make markets beyond 1x2 work — BTTS or AH — by modelling or by sharp anchor?*).
**To:** the session holding #119. **Owner's instruction:** merge this into your task list.

Four read-only agents ran (BTTS literature, AH literature, sharp-anchor data scan, engineering
audit). Nothing was edited, committed or written to the DB. Full reports sit beside this file:

| file | what |
|---|---|
| `research_btts.md` | BTTS literature + derived-Pinnacle BTTS + contamination probe |
| `research_ah.md` | AH literature + line-sign check + derived wide-line pricing + Holm family |
| `sharp_scan.md` + `prereg.md` + `family_out.txt` | sharp-anchor scan BTTS/AH vs 1x2 benchmark, pre-registered, Holm |
| `scripts/` | the analysis scripts. ⚠️ they reference scratchpad paths and intermediate CSV/PKL (not copied, ~250 MB) — re-run to regenerate |
| engineering audit | inline below (it was not saved as a file) |

---

## 1. Verdicts

| | Model route | Sharp-anchor route |
|---|---|---|
| **BTTS** | ❌ **Do not build.** A Poisson/Dixon-Coles price derived from Pinnacle 1x2 + O/U 2.5 (ρ≈−0.10) tracks the soft consensus at r=0.987, mean gap 0.89pp; adding it to consensus gains +0.00006 nats (t=1.0, n=8,970 held-out). Nothing left for a market-free model. Expected α = 0. | ❌ **~0.** After stripping broken rows (gap ≤8pp, quote ≤180 min): Coolbet +0.4% ROI (n=362, t=0.06), Epicbet −13% (n=149), Unibet-Site negative. T−2h CLV −1.3% to −1.6% (n 6–13/book). Flags fire ~4× less often than 1x2. |
| **AH** | ❌ **Do not build.** Best-documented efficient market (Hegarty & Whelan 2024 RBF, 2025 IJF — no FLB, implied probs unbiased). Expected α = 0. | ⚠️ **Undecidable, leaning 0.** Holm family (±1+ lines, EV ≥2%, 3 anchors × 2 books): nothing passes; best Epicbet +9.8% CI [−4,+24] n=755, adj p=0.50. The only honest time-aligned check (last 7 days): Epicbet wide lines +5.6% EV at decision → **+0.1% vs Pinnacle close** (n=29) — Pinnacle moved TO the soft price, i.e. anchor error, not a stale book. |

**⚠️ The 1x2 benchmark does not pass either.** At the scraped books (Coolbet/Epicbet/Unibet-Site),
T−2h consensus-anchored 1x2 CLV is +0.5% to +1.8% (t ≤1.74, n 45–60/book); 24-cell family,
min Holm-adjusted p = **0.915**. Consistent with #024's +0.58%. "1x2 works" is not established at
our executable books — owner has been told; worth its own row (see §4).

## 2. Findings that change how (E) must be built — if built at all

1. **AH de-vig must be POWER, not proportional.** Proportional de-vig of Pinnacle AH overstates
   "home fav covers −1/−1.5" by 1.8pp (z=−3.0, stable); power is calibrated both sides (|z|<1.2).
   Proportional manufactures edges exactly where `bot_ah_home_fav` lost. (research_ah.md)
2. **No pre-close AH anchor exists.** AF writes the AH ladder almost only in the final pre-KO
   fetch (Pinnacle: ~43k rows in last 30 min vs ~2k/h before). At T−2h only 28/69/11 fixtures.
   AF-Pinnacle AH median overround at close 5.5%, ~80% of lines >4% → fails our own sharpness gate.
   So (E) for AH needs either ≥2 weeks of exchange AH ladder or polling AF AH at T−2h.
3. **Sign convention verified**: `handicap_line` = HOME line on both selections at all 13 AF books,
   Coolbet/Epicbet/Tonybet and the exchange (paired corr 0.96–0.996; flipped 22–53pp off). Not
   checked: the selection strings in `shadow_bets` (no line column; line lives in the string).
4. **Coolbet has no 0/±0.5 AH lines** — only ±1 and wider (the lines that carry info beyond 1x2).
   Betfair carries 52% of Coolbet's lines, 93% Epicbet's, 90% Tonybet's on overlapping fixtures.
5. **Pinnacle has zero BTTS rows.** Anchor options: derived-from-Pinnacle-1x2+O/U (works, see §1),
   Pinnacle correct score (`CAPTURE_EXACT_SCORE` off, 0 rows), exchange (median €54 matched ~10h
   pre-KO, ~9/28 events ≥€1k, 22.5% of rows liquid — too thin for volume).
6. **Design-doc correction** (`dev/active/per-market-feature-sets-design.md`, BTTS head):
   `clean_sheet_pct`/`failed_to_score_pct` "96.87% fill" is ROW fill of season-aggregate
   snapshots fetched mostly May 2026; only **7.6%** of matches have a pre-KO snapshot → LEAKS.
   Derive walk-forward from `matches` scores instead.

## 3. Data-quality findings (independent of whether E is built)

- **Coolbet BTTS/AH pre-2026-09-18 (market-collision bug)** = 87–89% of Coolbet's BTTS edge tail
  and 60–85% of its AH tail at close. After the fix >25%-edge prices fell 6.2%→0.36% (AH),
  2.6%→0.12% (BTTS). Any backtest spanning 09-18 must cut there.
- **Coolbet AH within 30 min of KO: 6.8% of quotes >15pp off Pinnacle** — in-play or
  wrong-kickoff rows. Unguarded they fake a "+37% EV" tail. Needs a root cause + a ≤10pp agreement
  guard in any AH rule.
- **Coolbet BTTS-yes quoted at 11.0 / 4.5 pre-KO vs ~50% consensus** (in-play or another match's
  board) — 29 Coolbet + 40 Epicbet fixtures >15pp off consensus; the naive scan read +27–58% ROI
  at t≈4 from these (§67 trap). **Same family as #120 — `mirror_guard` skips BTTS and AH.**
- Epicbet BTTS tail 32–48% flagged wrong-board / inverted fav (25% after fix); Unibet-Site 19–27%.
- 309 of 12,982 (fixture, soft book) pairs have a 1x2 board >0.12 off the ≥4-book median
  (Epicbet 140, Coolbet 129, Unibet-Site 39). `data_quality_findings` was empty at scan time.
- Soft-book last quote is a median **18 min older** than the anchor at close — edge-at-close is
  mostly staleness, so close-to-close replays overstate.
- Retention keeps ~3 rows/series after 7 days → only ~8 days of full intraday history; T−2h
  backtests are impossible further back.

## 4. Engineering audit (stage-by-stage, file:line as found 2026-09-24)

| stage | BTTS | AH | evidence | effort |
|---|---|---|---|---|
| collection | ✅ | ✅ exc. Unibet-Site (#010) | `api_football.py:1152` BTTS, `:1192` AH; Coolbet `coolbet_explorer.py:383-385,829-866`; Epicbet `epicbet_explorer.py:794,800-820`; Tonybet `tonybet_feed.py:22,232-235`; Betfair `betfair_exchange_feed.py:48,139-161` | — |
| sharp edge | 🟡 consensus only | ❌ | `anchor.py:65-78` (BTTS de-vig set), `:218` `load_sets` ignores `handicap_line`; triggers 1x2/O/U only `pick_triggers.py:79-80,110,130`; publisher `publish_picks_forward_test.py:331` | BTTS ~½d · AH ~1–1.5d (line-keyed resolver, POWER de-vig) |
| model | 🟡 `btts.pkl` retired (−12.8%, n=427, `coolbet_placer.py:98-107`) | 🟡 Poisson/DC AH pricer `daily_pipeline_v2.py:1432`; `ah_xgb` discredited | not needed for sharp bot |
| bots | ❌ 6 BTTS bots retired (`bot_btts_all` −6.4% n=252; CLV +1.3% raw → −6.35% margin-corrected) | ❌ 3 AH bots ~−6% ROI on 1,860 deduped shadow bets; Pinnacle CLV empty on every AH row | copy `workers/jobs/team_total_paper_bot.py` | ~½d each |
| grading | ✅ `settlement.py:345` | 🟡 whole=push, half OK; **quarter lines graded strictly** `settlement.py:367-398` | filter quarters or fix ~½d |
| CLV | 🟡 consensus CLV (`clv_sharp.py:198`) | 🟡 raw close at matching line only (`settlement.py:819-857`); de-vig excludes AH `:976-979`; `clv_sharp.py:21-25,224` | AH de-vig CLV ~½d |
| placement | ❌ floor `None` (`coolbet_placer.py:94-107`); UI placer 1x2/O-U only (`coolbet_ui_placer.py:701-744`) | ❌ floor 5%, not fold-robust; UI label collision with team names | BTTS ~½d · AH 1–2d |
| publishing | 🟡 `/picks` allows BTTS (`upcoming-picks.ts:184`) | ❌ excluded (`engine-data.ts:1416,1491`) | BTTS ~2h · AH ~½d |

Note: shadow promotion is gated on Pinnacle CLV, which BTTS can never have → promotion rule would
need redefining around derived-Pinnacle or exchange CLV.

## 5. Proposed task-list changes (for you to merge — owner's call on priorities)

Every row needs Direction + estimate per CLAUDE.md. Suggested:

| # | item | direction | est | suggestion |
|---|---|---|---|---|
| a | **Close the model route for BTTS and AH** as negative results (literature + measured), with the design-doc correction in §2.6 | 🤖👥 BOTH — stops build effort both sides | ~1h | close in #119/#089 notes; add ANALYSIS_GOTCHAS entries: AH power de-vig; team_season_stats leakage; BTTS derivable from 1x2+O/U |
| b | **Extend the #120 board guard to BTTS and AH** + a near-KO (≤30 min) AH agreement guard; root-cause the 6.8% Coolbet near-KO AH rows | 🤖👥 BOTH — 🤖 shadow bets the owner copies with money; 👥 same boards reach /picks | ~½d | fold into **#120** |
| c | **Derived-Pinnacle BTTS anchor** (DC fit to Pinnacle 1x2+O/U, ρ≈−0.10 or per-league walk-forward) for `clv_pinnacle` on BTTS — ends "BTTS unmeasurable" | 👥 PICKS — honest BTTS numbers; 🤖 none | ~½d | a sub-step of #119 (replaces "BTTS bot" in E) |
| d | **(E) AH shadow bot — ONLY Epicbet ±1+ lines**, same-line Pinnacle/exchange anchor, POWER de-vig, anchor ≤15 min older than soft, ≤10pp agreement guard, no soft quotes <30 min pre-KO, CLV vs same-line close, ≤4 pre-registered Holm cells. Pre-registered expectation: CLV 0 ± 2pp. Gated on a pre-close anchor (≥2 wk exchange AH ladder or AF AH polled at T−2h) | 🤖 OWN only (Coolbet ±1+ too thin at ~3/day) | ~2d + 3–4 wk shadow (n≈110) | owner said AH may still go to Telegram readers (👥) — note the conflict: expected α≈0 |
| e | **Drop BTTS bot from (E)** — no volume (0.75–1.75 flags/day/book), negative CLV | — | — | record in the row |
| f | **Re-examine the "1x2 works" premise at executable books** — 24-cell family min adj p 0.915 | 🤖👥 BOTH — underpins every market expansion | ~½d | new row; owner flagged it as P1 |
| g | `exchange_quotes` retention + re-test exchange BTTS/AH sharpness in 4–6 weeks | 🤖👥 BOTH | — | already open in #119 (D) |

Owner has seen the summary and asked for this handover; they did not yet choose among a–g.
