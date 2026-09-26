-- #192 COOLBET-INPLAY-PRICES-IN-PREMATCH (2026-09-26).
-- odds_snapshots is the PRE-MATCH table for the direct books (in-play quotes live in
-- inplay_book_quotes / book_live_stats). The Coolbet board sweep reused a cached category
-- listing (#142 LISTING-REUSE / PASS-PACING) whose events still read OPEN after kickoff and
-- wrote their LIVE markets as pre-match; Epicbet had the same shape earlier (09-02..09-23).
-- The writers' old `abs(mtk) <= 15` closing window stamped many of them is_closing.
-- CLV was NOT affected: every settlement closing read bounds `timestamp <= m.date`.
-- Code fix in the same commit (supabase_client.after_kickoff + pre-KO closing window +
-- the sweep's clock check). This moves the already-written rows out, reversibly
-- (original_id kept), with the same rule the writer now applies:
--   * the book's own clock said after kickoff (minutes_to_kickoff < 0), or
--   * written more than 15 min after OUR kickoff.
WITH bad AS (
  SELECT o.id
    FROM odds_snapshots o
    JOIN matches m ON m.id = o.match_id
   WHERE o.bookmaker IN ('Coolbet', 'Epicbet', 'Tonybet', 'Unibet-Site', 'Optibet')
     AND (o.minutes_to_kickoff < 0 OR o.timestamp > m.date + interval '15 minutes')
), moved AS (
  INSERT INTO odds_snapshots_quarantined
    (match_id, bookmaker, market, selection, odds, timestamp, is_closing,
     minutes_to_kickoff, is_live, handicap_line, is_opening, original_id,
     quarantine_reason, quarantined_at)
  SELECT o.match_id, o.bookmaker, o.market, o.selection, o.odds, o.timestamp, o.is_closing,
         o.minutes_to_kickoff, o.is_live, o.handicap_line, o.is_opening, o.id,
         '#192 2026-09-26: written after kickoff (in-play price in the pre-match table)', now()
    FROM odds_snapshots o JOIN bad USING (id)
  RETURNING original_id
)
DELETE FROM odds_snapshots WHERE id IN (SELECT original_id FROM moved);
