# OddsIntel — Data Sources

> Last updated: 2026-04-28 — Migration complete. All T1–T13 endpoints integrated.

---

## Current Stack

| Source | Role | Status |
|--------|------|--------|
| **API-Football Ultra** ($29/mo) | PRIMARY — all structured data | ✅ Active |
| Kambi API (free) | Supplementary odds, 41 leagues | ✅ Active |
| Sofascore API (free) | xG post-match only; fallback fixture source | Reduced |
| ESPN (free) | Settlement result backup | ✅ Active (backup) |
| BetExplorer (free) | Gap league odds (phasing out) | To be removed |

**What API-Football covers:** fixtures, 13-bookmaker odds, live scores, lineups, injuries, standings, H2H, match events, player stats, team stats, transfers. 1,236 leagues.

**What it doesn't cover:** xG (use Sofascore), weather (use Open-Meteo free).

---

## Daily Request Budget (API-Football Ultra — 75K/day limit)

| Operation | Calls/day | Pipeline |
|-----------|-----------|----------|
| Fixtures | ~5 | Morning |
| Pre-match odds (T1 + odds) | ~400 | Morning + every 2h |
| Predictions (T1) | ~130 | Morning |
| Team stats (T2) | ~80 | Morning |
| Injuries (T3) | ~7 | Morning |
| Standings (T9) | ~40 | Morning |
| H2H (T10) | ~130 | Morning |
| Live fixtures + stats (T6) | ~120 | Live tracker every 5min |
| Live odds (T5) | ~120 | Live tracker every 5min |
| Lineups (T7) | ~120 | Live tracker pre-KO |
| Events (T8) | ~120 | Live tracker + settlement |
| Post-match stats (T4) | ~120 | Settlement |
| Player stats (T12) | ~120 | Settlement |
| **Total** | **~1,512** | **2% of 75K limit** |

Remaining headroom: ~73,500 req/day for historical backfill or new features.

---

## Integrated Endpoints (T1–T13)

| Task | Endpoint | Pipeline | Status |
|------|----------|----------|--------|
| T1 | `/predictions` | Morning | ✅ Done |
| T2 | `/teams/statistics` | Morning | ✅ Done |
| T3 | `/injuries` (batched 20/call) | Morning | ✅ Done |
| T4 | `/fixtures/statistics?half=1/2` | Settlement | ✅ Done |
| T5 | `/odds/live` | Live tracker | ✅ Done |
| T6 | `/fixtures?live=all` | Live tracker | ✅ Done |
| T7 | `/fixtures/lineups` | Live tracker (pre-KO) | ✅ Done |
| T8 | `/fixtures/events` | Live tracker + settlement | ✅ Done |
| T9 | `/standings` | Morning | ✅ Done |
| T10 | `/fixtures/headtohead` | Morning | ✅ Done |
| T11 | `/sidelined` | Backfill script | ✅ Done |
| T12 | `/fixtures/players` | Settlement | ✅ Done |
| T13 | `/transfers` | Backfill (opt-in `--transfers`) | ✅ Done |

---

## Remaining Cleanup

- [ ] Remove `betexplorer_odds.py` once AF odds coverage confirmed across gap leagues
- [ ] Remove `compute_team_form_from_db()` / `update_team_form_cache()` — now superseded by T2
- [ ] Simplify `news_checker.py` to skip injury detection — injuries now structured via T3
- [ ] Evaluate API-Football Pro ($19/mo, 7.5K req/day) after 4–6 weeks once we know which leagues are profitable
