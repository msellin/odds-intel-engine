Parent row: PRIORITY_QUEUE.md #187 (AH-MARKET-BOT-FOR-CUSTOMERS-2026-09-26). Research brief: dev/active/ah-market-bot-brief.md.

# AH market bot — plan

**Goal:** a new-market bot for our users (👥 PICKS): Asian handicap picks where a publishable book's price
beats Pinnacle's de-vigged fair price on the SAME line. Every line type (whole / half / quarter), both sides.

## Phases
1. **Data + definitions** — extract pre-match AH snapshots (2026-07-01 → now; before that the history is
   ~1 snapshot/match from 4–5 books, useless for a pick-time test), final scores, a per-line fair price.
2. **Pre-registration** (`dev/active/ah-market-bot-prereg.md`) — written BEFORE the first backtest number:
   candidate rule, measures, cells, Holm, holdout, expected outcome.
3. **Backtest** — discovery (older part) → holdout (newest part), one run on the holdout.
4. **Settlement correctness** — quarter lines half-win / half-loss; ledger support only if quarters survive.
5. **Shadow bot** (EXPERIMENTAL, admin-only) on the passing configuration; owner decides TESTING / free vs VIP.
6. **Public labels** — readable AH wording (after the owner's call on distribution).

## Key decisions (recorded as made)
* Fair price = Pinnacle's own price on the same line (power de-vig, 2-way), the LATEST Pinnacle fetch at or
  before the book's quote, ≤ 60 min apart. Measured 2026-09-26: Pinnacle AH is present ≥ 60 min before
  kickoff on 95% of finished matches (8,183 / 8,597, 30 d) with ~3.5 lines 12 h+ out, 5.7 at 1–12 h, 8.1 in
  the last hour — the old "AH only in the final fetch" note is out of date.
* Edge unit = price ratio `o / o_fair − 1` (break-even-price edge; same sign as expected profit for every line
  type, including refund lines).
* PRIMARY judge = CLV vs an INDEPENDENT close: power-de-vigged consensus of ≥ 5 books on the same line,
  excluding Pinnacle AND the pick's own book (ANALYSIS_GOTCHAS §85 mirror trap: a Pinnacle-triggered pick's
  Pinnacle-close CLV ≈ the trigger edge by construction). Secondary: Pinnacle-close CLV. Tertiary: ROI with
  correct quarter/whole payoffs.

## Risks
* Stale soft quotes (a book quote older than its snapshot suggests) → require the quote and anchor ≤ 60 min apart
  and report direct books (own fetch cadence) separately.
* Sign convention differences between books (checked in phase 1).
* Correlated legs (several lines of one match) → one pick per match in the primary test.
