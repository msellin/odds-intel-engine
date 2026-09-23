Parent: PRIORITY_QUEUE.md #113 ANCHOR-WIDENING-2026-09-23

# Anchor widening — plan

## Question the owner asked
"Will the consensus of any 5 books really work as an anchor?"

## Measured answer (scripts/anchor_consensus_composition.py, 120 d, 24,625 finished fixtures, 1X2, Shin, paired log-loss)

**T1 — where Pinnacle exists (19,697 fixtures): EVERY variant ties AF-Pinnacle.**
consensus_all ΔLL −0.0003 (t −0.96); panel −0.0001; random 5 books −0.0002;
**the 5 SOFTEST books −0.0001 (t −0.35)**; even 2 random books +0.0001. Same on the
1,842 fixtures where Pinnacle's quote is tight (≤4% overround, a real line): all ties.

So on outcomes, "any 5 books" is as good as our Pinnacle feed. Two honest caveats:
1. **Outcome log-loss is a blunt instrument.** Every sensible market price scores
   within ~0.0005 nats of every other; the test cannot see a 1–2% price error, and a
   1–2% error is exactly the size of the edges we act on. "Ties on outcomes" means
   "not detectably worse", not "equally precise for edge-finding".
2. It says as much about AF-Pinnacle (57% of its quotes are wide goodwill quotes,
   docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md) as about the consensus.

**T2 — where Pinnacle is missing (4,928 fixtures, 20%):**
- **51% have only 1–2 books quoting** (36% one book). No method makes an anchor
  there — the gap is a COVERAGE problem, fixed only by sweeping more books that
  price lower leagues.
- ≥5 books on only 32%; panel (≥3 of Marathonbet/1xBet/BetVictor/Betfair/William
  Hill) formable on 27%.
- Where formable, consensus_all is reasonably calibrated (bins within ±0.05, n
  150–1,500 per bin); panel/softest/random-5 tie it; random 3 slightly worse (t +3.0).

## Design (from the inventory: four consensus implementations already exist)
One resolver, `workers/utils/anchor.py::resolve_anchor(...)`, grown from
`promo_ev.consensus_fair_prob` (whitelist, freshness, complete markets, Shin), fixing
the known defects instead of copying them:
- complete set from ONE fetch per book (§62), all books within a common window of
  the newest quote (§65), leave-one-out ratio guard per book (§9/§68),
  EXCLUDE the book being priced from its own anchor, Coolbet out of the anchor set
  (the one measurably worse book).
- Returns a record: `{probs, source, n_books, books, spread, anchor_ts, max_age_min}`
  with `source` ∈ `pinnacle_tight` | `consensus` | `pinnacle_wide` | `none`.
- Order: Pinnacle tight (≤4%) AND fresh (≤60 min) → consensus ≥5 books → Pinnacle
  wide but fresh → none. A 3–4-book consensus is returned only when the caller
  asks for `min_books=3` (labelled, never for own-betting gates).
- The published live arm stays byte-identical (pre-registered); it is NOT switched.

## Phases
1. Resolver + smoke test (this session).
2. Wire DARK consumers first (they gain coverage, change nothing where Pinnacle
   exists): clv_sharp close (add a consensus-close fallback with `close_source`),
   sharp trigger windows (paper), paper bots. Each with a smoke test.
3. Validation gate before OWN gates switch: consensus vs Pinnacle as a CLOSING-LINE
   predictor per league tier, junk-anchor negative control, Holm correction.
4. Own-betting consumers (PIN-veto, anchor_sanity reference, price assembly).
5. Coverage: the 51% one/two-book fixtures need more books — feeds the next-sweep
   shortlist.

## Risks
- Publishing a CLV number off a different anchor silently changes a public figure →
  every consumer records `source`; public numbers split by source, never pooled.
- Consensus of copies (skins) = one opinion; resolver dedups known skins.
