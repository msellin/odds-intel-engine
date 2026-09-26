Parent row: PRIORITY_QUEUE.md #186 (AH-MARKET-BOT-FOR-CUSTOMERS-2026-09-26). Findings brief — holds no open work; the work lives on the row.

# Asian handicap as our third market — research brief (2026-09-26)

**Goal (owner 2026-09-26):** a new-market bot for OUR USERS (👥 PICKS) — free /picks + public Telegram, or a
VIP-tier bot. Priced over **every publishable (global) book**, NOT the Estonian ones: "can we bet it in
Estonia" is irrelevant here. Coolbet has no usable AH (no quarter lines, and no 0/±0.5). If Tonybet / Optibet /
others later show OWN value, that is a SEPARATE row, not this one.

Why AH was chosen over corners / team totals / BTTS / cards / 1H / double chance: three parallel read-only
tracks on 2026-09-26 — our own stored research, the literature, and a DB coverage inventory.

## 1. What our own research already measured

* **The recipe that worked for market #2 (O/U) was NOT a stats model** — six model-only O/U tests ended at
  α = 0 (`dev/active/market2-model-plan.md:24`). What worked: a soft book's price beating Pinnacle's
  de-vigged fair price, strongest on EARLY quotes (≥ 12 h: +7.47% Aug n=383 / +6.93% Sep n=459 CLV,
  `market2-model-plan.md:125-134`) → the O/U EARLY VIP bot. AH should reuse that recipe.
* **Model-free consensus-outlier scan** (08-31..09-24, Shin, Holm across markets; `market2-model-plan.md:142-146`,
  raw `data/models/_research/market2/scan_table.csv`, gitignored): at EV ≥ 5%
  AH 0.0 **+10.1%** CLV vs Pinnacle close [7.7, 12.6] n 98 (late, after 09-17: +11.1% n 18);
  AH +0.5 / −0.5 +10.5% / +8.8% (n 40 / 56); DNB (= AH 0) +10.2% n 114 but late only +2.0% n 12.
  Caveats: Bet365 ≈ 36% of picks; AF opening quotes' takeability unproven; the scan's earlier slice used
  stale opening rows, so "late" is the honest slice; whole lines were conditioned on no push.
* **Model route is dead:** market AUC 0.70–0.75 vs our model 0.55 on 74k obs (`docs/MARKET_DATA_MAP.md:23`);
  FLOOR-GRID 0 robust cells at every line (n 1,573); retired AH shadow bots ≈ −6% ROI. Do not build an AH
  goal model.
* **Sharp route on ±1+ lines was undecidable** (nothing passed Holm; Epicbet +9.8% CI [−4, +24]; time-aligned
  +0.1%) — `dev/active/sharp-anchor-v2-btts-ah-research/HANDOVER.md:27`. The blocker was the anchor timing (§3).
* **Soft-book AH margin 7.09%** — the most expensive market on the board; Pinnacle's is the lowest of any market.

## 2. Literature (research-before-train, CLAUDE.md)

* **Structural shape:** goal DIFFERENCE — Karlis & Ntzoufras 2003 (bivariate Poisson, diagonal inflation) and
  2009 (Skellam / zero-inflated Skellam for the difference). AH 0 = P(H)/(P(H)+P(A)); −0.5 = P(H). Lines 0 and
  ±0.5 carry no information beyond 1X2.
* **Hegarty & Whelan, IJF 2025 ("A tale of two markets")**: 1X2 odds show a strong favourite–longshot bias; AH
  odds on the same matches are EFFICIENT. → AH at Pinnacle is the right FAIR PRICE; the edge (if any) is soft
  books deviating from it, not a model.
* **Hegarty & Whelan, Rev. Behavioral Finance 2024**: realised loss rates differ by LINE TYPE — ≈ 4.16% half
  lines, ≈ 3.6% quarter lines, ≈ 3.24% whole lines; in Pinnacle-only samples (43,235 and 24,138 matches) half
  lines lose ≈ 1.8–1.9 pp more than whole lines, predictably (odds sit near 1.92 whatever the refund
  structure). → a line-choice rule to test, and a reason to report results PER LINE TYPE.
* **Constantinou, JSA 2022** (13 EPL seasons): AH "shares the inefficiencies" of 1X2 but gave lower profit than
  1X2, inconsistent by season. **Grant et al. 2018**: AH↔soft-1X2 arbitrage existed — the softness sat in 1X2.
* **Expected outcome, stated before any run:** a model edge ≈ 0 (efficient market); a soft-book-outlier edge
  of the O/U kind is plausible but unproven at the honest (late, fresh-anchor) slice. A family of many lines ×
  books needs a family-wise correction (Holm), per ANALYSIS_GOTCHAS §47.

## 3. Data we hold (DB inventory 2026-09-26, 30 days pre-match)

* **Asian handicap:** ≈ 9.9k matches / 30 d; Pinnacle on 9.2k; **history since 2023-07** (≈ 35k matches, 29.5k with
  Pinnacle) — the only non-1X2 market with years of history. Settles from the FT score (100%).
* Books pricing it: Pinnacle, Bet365 and the other API-Football books; **Epicbet 6.3k** (full ladder incl.
  quarters, 43% quarter rows), **Tonybet 1.4k** (since 09-23, ~50% quarters), Coolbet 7.7k (**|L| ≥ 1 full/half
  lines only — no quarters, no 0/±0.5**; irrelevant for this row), Unibet-Site none. Betfair Exchange carries AH
  since 2026-09-24 (`exchange_quotes`).
* **Line format:** `market` = AH, the line in `handicap_line` (not in the market name). Check every book's sign
  convention (home-perspective vs selection-perspective) before pooling.
* **Margins (14-d median, last pre-match price per side):** AF "Pinnacle" 6.5% (⚠️ the AF feed labelled
  Pinnacle is NOT tight — 9.1% on 1X2 vs real Pinnacle 2–3%; always de-vig, never trust raw), Epicbet 7.0%,
  Coolbet 8.0%, Tonybet 9.2%.
* **Anchor timing — the known blocker:** AF writes the Pinnacle AH LADDER almost only in the final pre-kickoff
  fetch (`sharp-anchor-v2-btts-ah-research/HANDOVER.md:39-42`) → no pre-close AH anchor for ±1+ lines. Lines 0
  and ±0.5 can be derived EXACTLY from Pinnacle 1X2 (fresh pre-close). Quarter lines = the average of the two
  neighbouring half/whole lines' outcomes. The exchange (since 09-24) is a second anchor candidate.

## 4. Known defects to fix before any record counts

* `settlement.py:394-398` grades quarter lines as a full win/loss — must be half-win / half-loss (and whole-line
  pushes as void).
* CLV must be push-aware on whole lines and split-aware on quarter lines; AH CLV cannot use a fixed rung
  (ANALYSIS_GOTCHAS §16).
* De-vig AH with **power**, not proportional (proportional overstates home favourites by 1.8 pp).
* The public formatters (`coolbet_signaler._format_market_public`, the forward-test `PICK_LABEL`) have no AH
  labels ("Arsenal −0.75" and what a quarter line means must be readable to a casual bettor).
