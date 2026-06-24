# OddsIntel — Public Track-Record Ledger

This folder holds **daily, byte-identical, GitHub-signed snapshots** of every
settled calibrated pre-match bet placed by the OddsIntel football model.

## Verification mechanic

1. The live API at `https://oddsintel.app/api/v1/track-record` serves the
   same data straight from the production database (calibrated bots only,
   pre-match markets only, no Asian Handicap, settled bets only).
2. Once per day (22:45 UTC) a GitHub Action runs
   `scripts/export_track_record_snapshot.py`, writes a deterministic JSON
   file to `ledger/YYYY-MM-DD.json`, updates `latest.json` and `index.json`,
   then commits the result as `github-actions[bot]`. The commit signature
   is verified by GitHub — anyone can check the commit history and confirm
   the snapshot existed at that date.
3. `index.json` stores the SHA-256 of each daily file so any future edit
   would be visible in git history AND would break the recorded hash.

## What's in each daily file

```jsonc
{
  "snapshot": { "date": "...", "generated_at_utc": "...", "scope": "..." },
  "summary": {
    "since": "2026-05-04",            // calibrated-tier launch
    "total_bets": 624,
    "stake_total": 3586.18,
    "pnl_total": 343.25,
    "roi_pct": 9.5715,
    "median_clv_pin_pct": 2.10,       // robust — see note below
    "clv_pin_coverage_pct": 97.27,    // % of bets with a Pinnacle close
    "clv_pin_beat_pct": 56.01,        // % of picks with CLV>0
    "scope": "calibrated bots, pre-match markets (1x2, OU 2.5, BTTS), settled only"
  },
  "bets": [
    {
      "id": "...",
      "match_id": "...",                   // UUID stable across re-settle
      "kickoff_utc": "2026-05-04T14:30:00+00:00",
      "league": "Liga I",
      "country": "Romania",
      "market": "1x2",
      "selection": "home",
      "placed_odds": 3.35,
      "bookmaker": null,                   // recommended_bookmaker if set
      "placed_at_utc": "2026-05-04T04:27:01Z",
      "closing_odds": 3.15,
      "clv_any_pct": 6.35,                 // vs any-book close
      "clv_pin_pct": 5.35,                 // vs Pinnacle close
      "stake": 5.0,
      "pnl": -5.0,
      "result": "lost",
      "score": "2-2",
      "bot": "bot_v10_all"
    },
    ...
  ]
}
```

## How to verify each bet yourself

Take any row and:

1. Cross-reference `match_id` + `kickoff_utc` against
   ESPN / Flashscore / Pinnacle archive — find the same fixture.
2. Confirm the final `score` against that public source.
3. Apply the bet definition (`market` + `selection`) to the score to
   derive the result yourself — won/lost/void.
4. Check `placed_at_utc < kickoff_utc` to confirm the bet was logged
   pre-kickoff (no after-the-fact editing).

## Why median CLV not mean?

Some historical "closing" Pinnacle snapshots in our `odds_snapshots` table
are vintage — captured hours or even days before kickoff because Pinnacle
didn't publish odds late, or because the `is_closing` flag was set on a
sub-optimal snap. Those produce outlier CLV values (±50%+) that wildly
swing the mean.

Median is robust to this. As of the
[CLOSING-LINE-COVERAGE](https://github.com/msellin/odds-intel-engine/commit/f5c0c94)
fix landing 2026-06-24, every imminent match (T-15 → T+5) now gets a
fresh per-fixture Pinnacle snap every 5 minutes, so the noise tail will
decay over the coming weeks and the mean will become trustworthy again.
Until then: **median is the publishable number.**

## Schema stability

The bet-row schema is intentionally narrow — no model-internal scores,
no calibrated probabilities, no signal weights. That's by design: this
ledger is for external verification of outcomes, not for replicating
the model. If you want the inputs, train your own model on public data
or read the [open-source engine](https://github.com/msellin/odds-intel-engine).

If we have to evolve the schema, additive-only changes will land in a
new field; we will not silently rename or remove fields.
