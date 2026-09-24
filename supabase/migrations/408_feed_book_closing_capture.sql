-- 408 — #107 phase C (2026-09-24): per-book closing capture on the feeds page.
-- Of the fixtures that kicked off in the last 24 h and this book priced pre-match, how many
-- carry a price in the final 15 minutes (the close). A book whose close is hours old makes
-- own-book CLV at the book we bet at unmeasurable — Coolbet's last pre-KO quote was 239 min
-- old at p90 before it joined the near-kickoff capture; this makes that visible per book.
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS closing_priced_24h integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS closing_captured_24h integer;
