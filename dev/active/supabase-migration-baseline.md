# Supabase baseline (snapshot 2026-07-09 00:07 UTC)

Reference numbers taken before migration. Use for parity verification after dump/restore.

## Database size

- **Total `public` schema size: 18 GB** (18,838,768,787 bytes)

## Top 30 tables

| Table | Size | Rows (n_live_tup) |
|---|---|---|
| odds_snapshots | 10103 MB | 20,673,171 |
| match_signals | 4821 MB | 16,961,470 |
| team_transfers | 664 MB | 1,046,155 |
| match_events | 532 MB | 1,413,556 |
| predictions | 275 MB | 557,937 |
| match_player_stats | 266 MB | 212,422 |
| live_match_snapshots | 265 MB | 838,047 |
| matches | 205 MB | 144,723 |
| league_standings | 145 MB | 111,605 |
| cs2_hltv_player_match_stats | 139 MB | 644,850 |
| cs2_leetify_player_match_stats | 110 MB | 75,288 |
| odds_snapshots_quarantined | 64 MB | 342,369 |
| team_elo_daily | 41 MB | 160,612 |
| team_form_cache | 38 MB | 134,157 |
| dashboard_cache | 37 MB | 4,531 |
| cs2_hltv_match_veto | 25 MB | 205,294 |
| pipeline_runs | 23 MB | 74,073 |
| match_feature_vectors | 22 MB | 64,212 |
| shadow_bets | 19 MB | 40,791 |
| team_season_stats | 18 MB | 14,629 |
| matches_dupe_quarantined | 14 MB | 3,177 |
| cs2_predictions | 13 MB | 36,799 |
| match_stats | 12 MB | 51,456 |
| cs2_hltv_match_maps | 10184 kB | 64,437 |
| team_coaches | 9912 kB | 55,288 |
| cs2_hltv_matches | 8800 kB | 29,360 |
| published_picks | 6872 kB | 10,277 |
| cs2_pandascore_matches | 6528 kB | 15,832 |
| match_injuries | 5392 kB | 4,825 |
| cs2_hltv_match_queue | 5048 kB | 30,998 |

## Post-cutover parity check

Run the same query against VPS `oddsintel` and diff row counts. Snapshot is `n_live_tup` (statistics estimate) — for authoritative parity use `SELECT COUNT(*)` on each table. Tolerable delta: <10 rows on active tables (ingestion may have leaked a few writes into pre-cutover window).
