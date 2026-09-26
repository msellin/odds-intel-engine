Parent row: PRIORITY_QUEUE.md #187 (AH-MARKET-BOT-FOR-CUSTOMERS-2026-09-26).

# AH sharp-outlier bot — PRE-REGISTRATION (written 2026-09-26, BEFORE any backtest number)

Everything below is fixed before `scripts/analysis/ah_market/backtest.py` produces its first result. Any
later change is an AMENDMENT section with a date and a reason; nothing above it is edited.

## Data
* `scripts/analysis/ah_market/extract.py` output: pre-match Asian-handicap quotes for FINISHED matches with
  kickoff 2026-07-01 → 2026-09-25 (≈ 27.5k matches, 2.55M price runs, 816k Pinnacle rows). Before July the AH
  history is ~1 snapshot/match from 4–5 books and cannot support a pick-time test.
* `handicap_line` is the HOME line on both rows for every book (verified 2026-09-26).

## Candidate rule (the bot)
* **Books:** every publishable book (`is_publishable_book`: all except Unibet-Kambi, Unibet (AF, dead), Max,
  Avg, Betfair Exchange, BetWin, Betfred) other than Pinnacle.
* **Decision instants:** every Pinnacle AH fetch `tp` with kickoff − tp ≥ 45 min.
* **Fair price:** Pinnacle's two prices on the same line in THAT fetch, power de-vig (2-way): find k with
  (1/o_h)^k + (1/o_a)^k = 1, q_side = (1/o_side)^k, fair odds = 1/q. A line missing a side in that fetch is skipped.
* **Book price at tp:** a book price run with first_ts ≤ tp ≤ last_ts (the price was the book's current quote
  across tp). Direct-feed books (Epicbet, Tonybet, Coolbet) are reported separately as well as pooled.
* **Edge:** EV = o × q − 1 (the price relative to the fair break-even price; same sign as expected profit for
  every line type, refund lines included).
* **A pick:** the FIRST instant at which some book's EV ∈ [3%, 15%) on a (match, side, line); price = the best
  qualifying book at that instant. EV ≥ 15% is excluded as a likely stale / wrong price (the O/U EARLY cap).
* **One pick per match** in the primary test: the match's earliest qualifying instant; ties → highest EV.

## Measures
1. **PRIMARY — independent-close CLV:** `o / o_cons − 1`, where o_cons = 1 / mean(q) over ≥ 5 books' LAST
   pre-kickoff quotes on the same line (each book's last run with last_ts ≥ kickoff − 90 min, both sides
   present, power de-vig per book), EXCLUDING Pinnacle and the pick's own book (ANALYSIS_GOTCHAS §85: a
   Pinnacle-triggered pick's Pinnacle-close CLV ≈ its trigger edge by construction). Legs without ≥ 5 books
   are reported as "no independent close" (coverage stated), never dropped silently.
2. **Secondary — Pinnacle-close CLV:** same, vs Pinnacle's last pre-kickoff fetch (≤ 90 min) on that line.
   Reported, decides nothing (mirror trap).
3. **Tertiary — ROI:** flat 1 unit, correct AH settlement: whole line push = 0; quarter line = half on each of
   the two neighbouring lines (half win / half loss).

## Cells and tests
* Cells: line type {whole (x.0), half (x.5), quarter (x.25 / x.75)} × EV band {3–5%, 5–8%, 8–15%} × timing
  {early: kickoff − tp ≥ 12 h, late: 45 min – 12 h} = 18 cells, plus one POOLED rule (all lines, EV 3–15%,
  one per match).
* **Discovery:** kickoff 2026-07-01 → 2026-08-31. One-sided test of mean primary CLV > 0 (bootstrap, B =
  10,000, seed 20260926) per cell with n ≥ 30; Holm at α = 0.05 across the cells tested.
* **Holdout (run ONCE):** kickoff 2026-09-01 → 2026-09-25, only the cells that passed discovery + the pooled
  rule; pass = mean primary CLV > 0 at one-sided p < 0.05, Holm across the carried cells. ROI reported with
  its 95% CI for every carried cell (decides nothing on its own at these n).
* A configuration is **SUPPORTED** only if it passes both discovery and holdout on the primary measure.

## Expected outcome (stated before the run)
* Pinnacle-close CLV ≈ the EV band by construction (uninformative).
* Pooled independent-close CLV: small and positive, +0.5% to +2%, stronger EARLY than late (the O/U EARLY
  pattern), stronger at direct-feed books than at API-Football books (which arrive in Pinnacle's own fetch).
* Line type: no strong prior; Hegarty & Whelan (2024) predict HALF lines carry ≈ 1.8–1.9 pp more loss than
  whole lines at equal prices, so a quarter/whole preference would not surprise.
* Most likely failure: the edge is Pinnacle's own noise — the independent close agrees with the soft book, so
  primary CLV ≈ 0.

## AMENDMENT 1 — 2026-09-26, after a VOID first discovery run (holdout still unseen)

**What happened.** The first discovery run (07-01 → 08-31) produced 364 picks but only **18** with an
independent close, and one book (Betano) supplied 223 of them. Its numbers are declared **void** and are not
used for any decision; they are recorded here only so the amendment's cause is on file. The holdout
(09-01 → 09-25) has NOT been run.

**Cause 1 — the ≥ 5-book independent close is structurally impossible for AH.** Our feed carries ~6–9
publishable books on a given AH line (1xBet, Bet365, Marathonbet, Betano, BetVictor, SBO, 10Bet, Superbet +
direct books); after excluding Pinnacle and the pick's own book, the discovery picks had 0/1/2/3/4/5/6 other
closing books on 78/15/47/98/108/17/1 legs. **Change:** the independent close needs **≥ 3** other books
(Pinnacle and the pick's own book still excluded). Coverage is reported.

**Cause 2 — lone outliers.** At the close every book has 7–25% of its AH quotes > 10% (log) away from
Pinnacle on the same line and side (per-book table in the context doc), so a single book far from everyone is
more often an error or a stale quote than value. **Change:** a pick-time **CONFIRMATION** variant is added:
at the decision instant, at least one OTHER non-Pinnacle book quotes the same side and line at EV ≥ 0
against the same Pinnacle fair price. Both variants are reported; the Holm family doubles (18 cells × 2
variants + 2 pooled rules).

**Unchanged:** everything else above, including the holdout's one-run rule and the pass criteria.

## AMENDMENT 2 — 2026-09-26, after the amended discovery run (holdout still unseen)

**What the discovery run showed.** Pooled "any" +5.3% independent-close CLV (n 224) but 223 of 364 picks at
one book, Betano. A same-fetch ladder check (last 6 days, home side, quarter line L between the same book's
L−0.25 and L+0.25 in the SAME fetch) found Betano's quarter ladder non-monotonic on **9.4%** of 11,616
triples vs 1.8% Bet365, 2.4% Pinnacle, 2.7% Marathonbet, 4.0% BetVictor, 4.1% 1xBet — i.e. a share of
Betano's quarter prices are feed errors, and "value" concentrates exactly there.

**Change — a LADDER-CONSISTENCY guard for every book (pick-time data only):** at the decision instant the
pick book's price on (side, line L) must lie between its own prices on L − 0.25 and L + 0.25 (same side, runs
live at that instant) wherever those neighbours exist; a candidate whose own ladder contradicts it is
dropped. No book is banned by name. Share of picks with no neighbour to check is reported.

Because this guard was designed after seeing discovery results, the discovery cells are **re-run with it and
re-tested with Holm**; only cells passing that re-run are carried to the holdout, which is still run ONCE.
