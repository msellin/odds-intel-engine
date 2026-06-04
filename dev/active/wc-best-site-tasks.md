# WC Best Site — Task Checklist

## Wave 1

- [ ] **A1** — ELO seed table (~50 nations from eloratings.net snapshot) + re-run `compute_international_elo.py` + re-run `write_national_team_predictions.py`. Verify Brazil v Morocco lands close to market.
- [ ] **A2** — Roster-strength scraper (clubelo + transfermarkt squad values) → `team_roster_strength` table.
- [ ] **A3** — Market-consensus scraper (free sources only — eloratings.net WC page + Betfair Exchange / Forebet) → `wc_market_consensus` table.
- [ ] **D2** — Live xG ingestion job for WC matches (extends existing API-Football live tracker).
- [ ] **E2** — Per-nation team pages at `/world-cup/teams/[name]`.
- [ ] **G1** — Apply prob-display primitives (bar + numbers + AI pill) to bracket page matchup cards.

## Wave 2

- [ ] **A4** — Bayesian blender (own model + market consensus → final blended prob per fixture).
- [ ] **A5** — Lineup-aware refresh at T-60min using API-Football lineups.
- [ ] **E1** — `/world-cup/who-can-win` Monte Carlo tournament simulation page.
- [ ] **D1** — WP curve extended to WC matches (international ELO baseline).
- [ ] **C1** — `/world-cup/predictions-record` honest tracking page.
- [ ] **F1** — SEO indexing for team + group pages.

## Wave 3

- [ ] **B1** + **B4** — Model card component + market-disagreement callout on match detail.
- [ ] **B2** + **B3** — Score predictions table + key-player chip.
- [ ] **B5** — AI preview Gemini prompt enhanced to reference actual model numbers.
- [ ] **D3** + **D4** — WP curve event annotations + next-10-min goal prob.
- [ ] **C2** + **C3** — CLV dashboard + model leaderboard vs Opta + market.
- [ ] **E3** + **E4** — Auto-generated analytical posts.

## Wave 4

- [ ] **F2** — Twitter auto-post on match resolution.
- [ ] **F3** — Telegram alerts for top-edge picks.
- [ ] **F4** — Daily preview email via existing digest infra.
- [ ] **F5** — Per-match prediction OG image.
- [ ] **G2** + **G3** — Mobile responsiveness + loading skeletons.
- [ ] **G4** — Final visual + copy polish.

## Done

- [x] ELO bug diagnosis (2026-06-04) — Morocco inflated, Brazil underrated, market inverted. Filed.
- [x] V2 visual upgrade for schedule + group cards (2026-06-04).
- [x] Smoke fixes (INPLAY-BAYESIAN, INPLAY-LAMBDA-STATE, MFV-MAY6-TIMEOUT) (2026-06-04).
