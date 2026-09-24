# Match Detail Redesign — Plan

## Product principle
OddsIntel computes strong intelligence and presents it as raw material. Fix: lead with the call, grade confidence, 3-5 reasons in plain language, raw depth one tap down.

## Build order

### Phase 1 — Data bugs (trust killers)
1. Merge `MarketImpliedProbabilities` + `ModelMarketUsers` into one unified card — eliminates the 49% vs 45% contradiction
2. Fix delta label: "pp" not "%"
3. Remove `MatchPickButton` (duplicates header odds, adds nothing)
4. Hide `CommunityVote` when no votes (already has logic, just verify)
5. Fix "-2 points above drop zone" minus sign in `MatchDetailFree`

### Phase 2 — Verdict card (highest priority feature)
6. Add `getMatchValueBet(matchId)` to engine-data.ts — queries simulated_bets for today's top bet for this match
7. Create `MatchVerdictCard` component — two states: green (value found) + amber (no value/fairly priced)
8. Wire into match-detail page.tsx between header separator and tabs

### Phase 3 — Signal depth fixes
9. Signal Timeline → Elite-only collapsible toggle (hide by default in IntelProContent)
10. Add "Signal Groups" header legend (dot color meaning: green=supports value, amber=caution, red=erodes value)

### Phase 4 — CONTEXT tab fixes
11. `ContextProContent` season stats: when one team's data missing, show single message not empty column
12. `CommunityVote`: hide entirely until hasVotes (or hide "Users" row until votes)
13. League table: verify both teams shown (in MatchDetailFree)

### Phase 5 — ODDS tab and MATCH tab
14. ODDS tab: add overround callout when margin < 0 (negative overround = line-shopping value)
15. MATCH tab: add injury header summary (N out + N doubtful per team) above player list
16. Grade badge: add `title` tooltip with definition

## Key decisions
- Grade B tooltip: "Coverage grade — reflects data depth and bookmaker count for this league. A=13 books+rich history, D=limited coverage."
- Model probs source: use `publicMatch.modelHome/Draw/Away` for consistency (already fetched, eliminates N+1)
- Verdict card scope: show on all matches; "no value" state avoids "volatile everywhere" credibility erosion
- Verdict card sticky: not sticky (avoids vertical space fight with tab bar on mobile)
- Explicit stance: show "value on Draw · +16pp" not just descriptive (product is already explicit with bots)

## Risks
- `getMatchValueBet` must use service role (same as getTodayBets) — RLS blocks anon key on simulated_bets
- `publicMatch.modelHome` vs `getModelMarketUsers` diverge because they may pull different prediction rows. Fix by removing `getModelMarketUsers` and using publicMatch values as the single source.
- Negative overround callout: only show when `(1/home + 1/draw + 1/away) < 1`
