# WC Best Site — Master Plan (2026-06-04 → 2026-06-11)

> **Goal**: ship OddsIntel as the best WC2026 predictions site by kickoff (2026-06-11 19:00 UTC).
> **Strategy**: own model + market consensus + honest tracking + per-match intelligence + live experience.
> **Constraint**: free data sources only; no model rewrite during WC week (lock baseline).

## Pillars

1. **Prediction quality** — ELO reanchored from real-world reference, blended with market consensus, refreshed at T-60min on lineup news.
2. **Per-match intelligence** — Every match shows our prob, market prob, blend, and feature attribution.
3. **Honest tracking** — Live `/world-cup/predictions-record` page showing Brier / calibration / CLV vs market.
4. **Live match experience** — WP curve + live xG + goal-prob + market move during the 90 minutes.
5. **Distribution** — SEO team/group pages, Twitter auto-posts, Telegram alerts, daily email.

## Workstreams + dependencies

```
WS-A (predictions) ──► A1 → A4 → A5
                   ─► A2 ──┘
                   ─► A3 ──┘

WS-B (match intel) ────────► B1-B5 (needs A4)
WS-C (tracking)    ────────► C1-C3 (needs A4)
WS-D (live)        ────────► D1 (needs A1), D2 (independent), D3-D5
WS-E (content)     ────────► E1 (needs A4), E2-E4 (independent)
WS-F (distribution) ───────► F1-F5 (needs A4/C1/B1)
WS-G (UX polish)   ────────► G1-G4 (independent of A/B/C/D)
```

## Hard constraints

- **No paid APIs**: free sources only (eloratings.net, Betfair Exchange, fbref, clubelo, transfermarkt, Forebet).
- **Worktree isolation** for parallel agents to avoid merge hell.
- **Sequential migration numbering**: A2 → 176, A3 → 177, etc. Assigned per agent brief.
- **Smoke per task**: every commit has at least one smoke entry.
- **Frozen baseline**: A1+A4 lock by 2026-06-09; no model logic changes during the tournament.

## Wave plan (~6h calendar, 6 parallel agents)

| Wave | Agent-1 | Agent-2 | Agent-3 | Agent-4 | Agent-5 | Agent-6 |
|------|---------|---------|---------|---------|---------|---------|
| 1 | A1 ELO seed | A3 market scraper | A2 roster scraper | D2 live xG | E2 team pages | G1 bracket viz |
| 2 | A4 blender | A5 lineup refresh | E1 Monte Carlo | D1 WP curve | C1 perf-record | F1 SEO |
| 3 | B1+B4 model card | B2+B3 score+player | B5 AI preview | D3+D4 WP overlays | C2+C3 CLV dash | E3+E4 articles |
| 4 | F2 Twitter | F3 Telegram | F4 email | F5 share OG | G2 mobile | G4 polish |

## Where to look mid-flight

- `dev/active/wc-best-site-tasks.md` — checklist (mark items done as we go)
- `dev/active/wc-best-site-context.md` — running notes per wave
- `PRIORITY_QUEUE.md` — high-level entries per workstream
- Git log: every commit prefixed with workstream code (e.g. `WC-A1 — …`)
