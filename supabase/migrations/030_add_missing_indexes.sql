-- Add missing indexes identified by Supabase index analyzer
CREATE INDEX IF NOT EXISTS idx_teams_name ON public.teams USING btree (name);
CREATE INDEX IF NOT EXISTS idx_league_standings_team_name ON public.league_standings USING btree (team_name);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_bookmaker ON public.odds_snapshots USING btree (bookmaker);
CREATE INDEX IF NOT EXISTS idx_leagues_name ON public.leagues USING btree (name);
