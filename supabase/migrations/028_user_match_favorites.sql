-- Per-match star/follow: users can follow individual games
CREATE TABLE IF NOT EXISTS user_match_favorites (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    match_id   uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, match_id)
);

-- Index for fast lookup by user
CREATE INDEX idx_umf_user ON user_match_favorites (user_id);

-- RLS: users can only see/manage their own favorites
ALTER TABLE user_match_favorites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users read own match favorites"
    ON user_match_favorites FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users insert own match favorites"
    ON user_match_favorites FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users delete own match favorites"
    ON user_match_favorites FOR DELETE
    USING (auth.uid() = user_id);
