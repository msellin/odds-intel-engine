# OddsIntel — Next Steps (Updated 2026-04-27)

## Data We Now Have

| Dataset | Matches | Leagues | Odds? | Date Range |
|---------|---------|---------|-------|------------|
| football-data.co.uk | 133K | 18 | Yes (5-6 bookmakers) | 2005-2025 |
| schochastics | 1.3M | 207 | No (results only) | 1888-2025 |
| Beat the Bookie (Kaggle) | 479K | 818 | Yes (16+ bookmakers) | 2005-2015 |
| **Combined** | **1.3M+** | **800+** | **Partial** | **1888-2025** |

Plus live data:
- Sofascore: 467 matches/day (fixtures + results, free)
- Kambi (Unibet/Paf): 117 matches/day with odds (free)

## Immediate TODO

### 1. Backtest with Beat the Bookie (479K matches, 818 leagues, WITH odds)
- Merge with global ELO ratings
- Run our model across ALL 818 leagues
- Find which leagues are consistently profitable
- This is the most comprehensive backtest possible

### 2. Expand Live Match Coverage (100 → 400+)
- Currently: Kambi gives 117 matches with odds
- Need: Additional free odds sources
- Options:
  - The Odds API free tier (500 calls/month, covers many leagues)
  - OddsPortal scraping (covers everything, engineering effort)
  - Sofascore has odds for some matches too (check API)
  - Betclic API (if accessible from Estonia)
- Sofascore already gives us 467 fixtures — we just need odds for them

### 3. Fix Live Pipeline Issues
- Team name mapping (82% matched, need 95%+)
- Odds storage column mismatch (captured_at vs created_at)
- Predictions not being stored (0 rows in predictions table)
- Settlement pipeline needs testing

### 4. Deploy Daily Pipeline
- Add Supabase secrets to GitHub repo settings
- Test GitHub Actions workflow
- Verify it runs unattended

### 5. Continue Model Improvement
- Global ELO is 68.7% accurate but calibration still off by 13-15%
- Ligue 1 (+21.6%), Scottish Premiership (+15.1%), Serie B (+3.1%) look promising
- Need to test if these patterns hold in Beat the Bookie dataset (independent validation)

## Key Insight from Today

The product needs to cover ALL matches, not just 18 leagues. The data exists — we just need to:
1. Process it (global ELO gives us predictions for any team in the world)
2. Get odds (Kambi + The Odds API + OddsPortal covers most)
3. Show the best value opportunities across ALL leagues

The frontend can show "Top 10 Value Bets Today" across ALL 400+ matches, not just Premier League.
