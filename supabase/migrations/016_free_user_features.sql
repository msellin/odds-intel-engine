-- ============================================================================
-- Free user features: favorites, picks tracker, saved matches, match notes,
-- community votes, daily teaser unlocks
-- ============================================================================

-- 1. Add favorite_teams to profiles
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS favorite_teams text[] DEFAULT '{}';

-- 2. User picks / bet tracker
CREATE TABLE IF NOT EXISTS user_picks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  selection text NOT NULL CHECK (selection IN ('home', 'draw', 'away')),
  odds numeric(8,4),
  stake numeric(10,2),
  result text CHECK (result IN ('pending', 'won', 'lost', 'void')),
  created_at timestamptz DEFAULT now(),
  resolved_at timestamptz,
  UNIQUE(user_id, match_id)
);

CREATE INDEX IF NOT EXISTS idx_user_picks_user ON user_picks(user_id);
CREATE INDEX IF NOT EXISTS idx_user_picks_match ON user_picks(match_id);

-- RLS for user_picks
ALTER TABLE user_picks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own picks"
  ON user_picks FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own picks"
  ON user_picks FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own picks"
  ON user_picks FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own picks"
  ON user_picks FOR DELETE
  USING (auth.uid() = user_id);

-- 3. Saved matches / watchlist
CREATE TABLE IF NOT EXISTS saved_matches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  saved_at timestamptz DEFAULT now(),
  UNIQUE(user_id, match_id)
);

ALTER TABLE saved_matches ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own saved matches"
  ON saved_matches FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own saved matches"
  ON saved_matches FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own saved matches"
  ON saved_matches FOR DELETE USING (auth.uid() = user_id);

-- 4. Match notes
CREATE TABLE IF NOT EXISTS match_notes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  note_text text NOT NULL DEFAULT '',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(user_id, match_id)
);

ALTER TABLE match_notes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own notes"
  ON match_notes FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own notes"
  ON match_notes FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own notes"
  ON match_notes FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own notes"
  ON match_notes FOR DELETE USING (auth.uid() = user_id);

-- 5. Community sentiment votes
CREATE TABLE IF NOT EXISTS match_votes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  vote text NOT NULL CHECK (vote IN ('home', 'draw', 'away')),
  created_at timestamptz DEFAULT now(),
  UNIQUE(user_id, match_id)
);

ALTER TABLE match_votes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read all votes"
  ON match_votes FOR SELECT USING (true);

CREATE POLICY "Users can insert own vote"
  ON match_votes FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own vote"
  ON match_votes FOR UPDATE USING (auth.uid() = user_id);

-- 6. Daily teaser unlocks
CREATE TABLE IF NOT EXISTS daily_unlocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  unlock_date date NOT NULL DEFAULT CURRENT_DATE,
  match_id uuid REFERENCES matches(id) ON DELETE SET NULL,
  feature text NOT NULL DEFAULT 'value_bet',
  created_at timestamptz DEFAULT now(),
  UNIQUE(user_id, unlock_date)
);

ALTER TABLE daily_unlocks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own unlocks"
  ON daily_unlocks FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own unlocks"
  ON daily_unlocks FOR INSERT WITH CHECK (auth.uid() = user_id);
