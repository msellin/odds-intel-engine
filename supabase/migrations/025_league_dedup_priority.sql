-- ============================================================
-- Migration 025: League Deduplication + Priority Column
-- ============================================================
-- Problem: Kambi odds scraper creates leagues with different names/countries
-- than API-Football, causing duplicate leagues with fragmented match data.
-- Fix: Merge Kambi leagues into their AF counterparts, add priority for sorting.

BEGIN;

-- ============================================================
-- 1. ADD PRIORITY COLUMN
-- ============================================================
-- Priority controls league sort order on the frontend.
-- Lower = more important. NULL = default (sorted alphabetically after priority leagues).
-- 1 = "Featured today" (continental cups, marquee matchdays — set dynamically)
-- 10 = Top domestic leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1)
-- 20 = Major secondary leagues (Championship, Serie B, 2. Bundesliga, etc.)
-- 30 = Other notable leagues (Eredivisie, Liga Portugal, MLS, etc.)
-- NULL = Everything else (sorted alphabetically)

ALTER TABLE leagues ADD COLUMN IF NOT EXISTS priority smallint DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_leagues_priority ON leagues (priority) WHERE priority IS NOT NULL;

COMMENT ON COLUMN leagues.priority IS
  'Sort priority for frontend display. Lower = higher on page. NULL = alphabetical after priority leagues. Set dynamically for featured matchdays.';

-- ============================================================
-- 2. MERGE KAMBI DUPLICATE LEAGUES INTO AF COUNTERPARTS
-- ============================================================
-- For each pair: move matches from Kambi league → AF league, then delete Kambi league.
-- Only the matches table has league_id FK. Teams table league_id is a dummy placeholder.

-- === CONTINENTAL CUPS (high visibility) ===

-- Europa League → UEFA Europa League (af_id=3)
UPDATE matches SET league_id = '627937cc-6c56-482d-a769-a8d83c9fcacf' WHERE league_id = 'cbeee5b4-2425-4458-9572-8cfb6ba589b8';
DELETE FROM leagues WHERE id = 'cbeee5b4-2425-4458-9572-8cfb6ba589b8';

-- Conference League → UEFA Europa Conference League (af_id=848)
UPDATE matches SET league_id = 'c94f1f6a-dfd1-4545-9ae3-21893aab7a8a' WHERE league_id = 'b654d623-256f-4369-9215-81ee308200ae';
DELETE FROM leagues WHERE id = 'b654d623-256f-4369-9215-81ee308200ae';

-- Champions League → UEFA Champions League (af_id=2)
UPDATE matches SET league_id = '1bb28d8a-8b00-436b-aef7-7ba9c86dcc3f' WHERE league_id = '04f5028b-eb30-4f6f-a7bd-2c49c74b60f0';
DELETE FROM leagues WHERE id = '04f5028b-eb30-4f6f-a7bd-2c49c74b60f0';

-- Copa Libertadores → CONMEBOL Libertadores (af_id=13)
UPDATE matches SET league_id = '4bd09038-37ed-4946-abab-0c60c28aaf47' WHERE league_id = '2fc60047-e3f4-420a-a3c0-81e6d7b339d2';
DELETE FROM leagues WHERE id = '2fc60047-e3f4-420a-a3c0-81e6d7b339d2';

-- Copa Sudamericana → CONMEBOL Sudamericana (af_id=11)
UPDATE matches SET league_id = 'c7483064-f6f4-44d8-b708-8bff85bcdaf1' WHERE league_id = 'e5d68dfe-a862-4c3f-914a-41e19af8b92f';
DELETE FROM leagues WHERE id = 'e5d68dfe-a862-4c3f-914a-41e19af8b92f';

-- CONCACAF Champions Cup → CONCACAF Champions League (af_id=16)
UPDATE matches SET league_id = '8d31c7e0-da67-4d69-bc81-cd489c4064a7' WHERE league_id = '6eebabe2-b409-4124-a195-061e90eb89df';
DELETE FROM leagues WHERE id = '6eebabe2-b409-4124-a195-061e90eb89df';

-- Europa Cup (W) → UEFA Europa Cup - Women (af_id=1191)
UPDATE matches SET league_id = '005091b9-30f5-4a90-9a88-786a819263d8' WHERE league_id = '10ebb986-d037-4c38-b498-0f915cadbb7c';
DELETE FROM leagues WHERE id = '10ebb986-d037-4c38-b498-0f915cadbb7c';

-- === EUROPE ===

-- The Championship → Championship (England, af_id=40)
UPDATE matches SET league_id = '01ba89d6-1b7b-4ee7-95e9-adf8cd617afc' WHERE league_id = '78aa2ed4-983d-4afc-a7f3-b4b13c60eac9';
DELETE FROM leagues WHERE id = '78aa2ed4-983d-4afc-a7f3-b4b13c60eac9';

-- Superligaen → Superliga (Denmark, af_id=119)
UPDATE matches SET league_id = '977b3b8c-d0e9-47cf-ab2c-a51432ba4e52' WHERE league_id = 'dae3e2a4-b5ed-4796-a53c-a0d415fe8706';
DELETE FROM leagues WHERE id = 'dae3e2a4-b5ed-4796-a53c-a0d415fe8706';

-- 1st Division (Denmark) → 1. Division (Denmark, af_id=120)
UPDATE matches SET league_id = 'b50a4b1a-3fb4-4192-be40-25edb18d19ba' WHERE league_id = '6e03015e-fc5a-4cd5-a377-7d4267cb7e75';
DELETE FROM leagues WHERE id = '6e03015e-fc5a-4cd5-a377-7d4267cb7e75';

-- Ligat Ha'Al → Ligat Ha'al (Israel, af_id=383) — case difference
UPDATE matches SET league_id = 'dbb04bd2-6966-47c9-88e6-2b745acb4851' WHERE league_id = '8db7a1f0-91c3-4add-bdff-998ab012fbaf';
DELETE FROM leagues WHERE id = '8db7a1f0-91c3-4add-bdff-998ab012fbaf';

-- Championnat National → National 1 (France, af_id=63)
UPDATE matches SET league_id = '693ccabd-af82-45f7-8849-f0b438bbdeb0' WHERE league_id = '0ea07b18-1dc0-476a-ba6e-724ecbf553d0';
DELETE FROM leagues WHERE id = '0ea07b18-1dc0-476a-ba6e-724ecbf553d0';

-- National 2 Group C → National 2 - Group C (France, af_id=69)
UPDATE matches SET league_id = 'fe74882f-c4c2-4177-b347-63f1d8550633' WHERE league_id = '8d8e66d9-6f9e-4047-aa9d-7ca885e1e0ef';
DELETE FROM leagues WHERE id = '8d8e66d9-6f9e-4047-aa9d-7ca885e1e0ef';

-- SNL 2 → 2. SNL (Slovenia, af_id=374)
UPDATE matches SET league_id = '0016b5d8-bc77-419e-ad8f-67caf9fb05eb' WHERE league_id = 'f881aefc-c771-4f4d-8611-793fabdfa2b8';
DELETE FROM leagues WHERE id = 'f881aefc-c771-4f4d-8611-793fabdfa2b8';

-- MSFL → 3. liga - MSFL (Czech Republic, af_id=349)
UPDATE matches SET league_id = 'f9ee4751-343e-4bae-8c92-2082bd9c3664' WHERE league_id = '4b7036dc-6d29-4785-bb7a-cca520c38382';
DELETE FROM leagues WHERE id = '4b7036dc-6d29-4785-bb7a-cca520c38382';

-- 1st Division (Cyprus) → 1. Division (Cyprus, af_id=318)
UPDATE matches SET league_id = '395710c0-a5d1-4a5b-a85a-0f70b78a142f' WHERE league_id = 'eaa20f2e-148d-401e-b5d1-419dc31efacc';
DELETE FROM leagues WHERE id = 'eaa20f2e-148d-401e-b5d1-419dc31efacc';

-- Premier League (Azerbaijan) → Premyer Liqa (Azerbaijan, af_id=419)
UPDATE matches SET league_id = '2b153e8c-a38e-4fcf-8b16-9cb79fc543ca' WHERE league_id = '07adaf51-1afe-48df-ba4e-aa14e2682dee';
DELETE FROM leagues WHERE id = '07adaf51-1afe-48df-ba4e-aa14e2682dee';

-- Toppserien (W) → Toppserien (Norway, af_id=725)
UPDATE matches SET league_id = '04bc2b02-1557-4318-882f-24c408b12775' WHERE league_id = 'd2db35a4-9730-4f9b-a8dc-accd49b2be72';
DELETE FROM leagues WHERE id = 'd2db35a4-9730-4f9b-a8dc-accd49b2be72';

-- 1. HNL League → HNL (Croatia, af_id=210)
UPDATE matches SET league_id = '41c7b3e0-637e-4b3a-8d64-4d832ea413b7' WHERE league_id = '1ad8a522-ab52-4790-b1fb-7bcdfc463ddc';
DELETE FROM leagues WHERE id = '1ad8a522-ab52-4790-b1fb-7bcdfc463ddc';

-- NB 2 → NB II (Hungary, af_id=272)
UPDATE matches SET league_id = '32a10fab-f488-4c3c-8b75-d65b0f06dc4f' WHERE league_id = 'dfd7dd49-0e2f-447d-b01c-39147b2f2d33';
DELETE FROM leagues WHERE id = 'dfd7dd49-0e2f-447d-b01c-39147b2f2d33';

-- NB 1 → NB I (Hungary, af_id=271)
UPDATE matches SET league_id = '735015f9-c9fb-4e90-8c5d-069e59539f3a' WHERE league_id = 'baed353e-0424-4e32-951d-56ca2204532e';
DELETE FROM leagues WHERE id = 'baed353e-0424-4e32-951d-56ca2204532e';

-- Lotto Super League (W) → Super League Women (Belgium, af_id=146)
UPDATE matches SET league_id = '65dceb6c-af0e-42b9-b505-822ef0532afd' WHERE league_id = 'b9f98420-0d89-4a4c-9bad-0d9d64282a30';
DELETE FROM leagues WHERE id = 'b9f98420-0d89-4a4c-9bad-0d9d64282a30';

-- Pro League U21 → Reserve Pro League (Belgium, af_id=518)
UPDATE matches SET league_id = 'ece495be-f977-49f2-bb2c-571e1011c01e' WHERE league_id = 'dd4e3908-9fc2-4aeb-8f39-47db9076bcbb';
DELETE FROM leagues WHERE id = 'dd4e3908-9fc2-4aeb-8f39-47db9076bcbb';

-- Liga Revelacao U23 → Liga Revelação U23 (Portugal, af_id=701)
UPDATE matches SET league_id = '22037b4d-dfb0-4e71-9138-bf3fd9e2661e' WHERE league_id = 'f8beb367-4816-4b68-9653-b58702d74555';
DELETE FROM leagues WHERE id = 'f8beb367-4816-4b68-9653-b58702d74555';

-- Botola → Botola Pro (Morocco, af_id=200)
UPDATE matches SET league_id = 'eb59525f-b737-47b9-81a1-d081f55ae919' WHERE league_id = '8736522e-c2b6-43d2-aaa9-9f3e6583bbf5';
DELETE FROM leagues WHERE id = '8736522e-c2b6-43d2-aaa9-9f3e6583bbf5';

-- Super League (W) → FA WSL (England, af_id=44)
UPDATE matches SET league_id = 'fdd0d136-600f-4b97-bdc4-e8e1c5253ce6' WHERE league_id = 'e0cc08e2-0da7-4ef1-8674-a9e001171325';
DELETE FROM leagues WHERE id = 'e0cc08e2-0da7-4ef1-8674-a9e001171325';

-- Premier League International Cup → Premier League International Cup (World, af_id=1039)
UPDATE matches SET league_id = '4ab3eff3-d461-48b1-b95a-7e72c5f61c98' WHERE league_id = 'b1983cba-fc3f-4abd-9b71-2f49d448a822';
DELETE FROM leagues WHERE id = 'b1983cba-fc3f-4abd-9b71-2f49d448a822';

-- Southern League Premier Division South → Non League Premier - Southern South (England, af_id=60)
UPDATE matches SET league_id = '0cbe73de-301c-45ab-b5da-227e5cf51dcb' WHERE league_id = 'ede49257-9528-4e33-a71f-5730ab56ba86';
DELETE FROM leagues WHERE id = 'ede49257-9528-4e33-a71f-5730ab56ba86';

-- Regional League East → Regionalliga - Ost (Austria, af_id=221)
UPDATE matches SET league_id = '6a28a19b-2c37-4512-9ab9-51add7e053b3' WHERE league_id = '8b1b1eed-0ecd-4b03-b531-4597f1e2f515';
DELETE FROM leagues WHERE id = '8b1b1eed-0ecd-4b03-b531-4597f1e2f515';

-- Cup → Federation Cup (Bangladesh, af_id=811)
UPDATE matches SET league_id = '93b639b2-1ece-46bc-897f-ac5a5305a877' WHERE league_id = '6ba6b985-5ed7-4ff6-b122-98395a162428';
DELETE FROM leagues WHERE id = '6ba6b985-5ed7-4ff6-b122-98395a162428';

-- === SPAIN (Tercera/Segunda RFEF groups) ===

-- Tercera RFEF 11 → Tercera División RFEF - Group 11 (af_id=449)
UPDATE matches SET league_id = 'aa62cdaf-394a-4695-84b4-7662ec0fcbac' WHERE league_id = '7da7d7bb-c153-4b3c-9d64-6cc2486ae29c';
DELETE FROM leagues WHERE id = '7da7d7bb-c153-4b3c-9d64-6cc2486ae29c';

-- Tercera RFEF 8 → Tercera División RFEF - Group 8 (af_id=446)
UPDATE matches SET league_id = '3355330e-c4ea-42b7-af36-6e13f2a24d24' WHERE league_id = 'd800067e-84bd-4143-a04c-63725c82760b';
DELETE FROM leagues WHERE id = 'd800067e-84bd-4143-a04c-63725c82760b';

-- Tercera RFEF 3 → Tercera División RFEF - Group 3 (af_id=441)
UPDATE matches SET league_id = '1ae10fde-9987-46d6-9ed0-7824fb317e55' WHERE league_id = '930b2979-b01e-4f77-94a9-03592f9c582e';
DELETE FROM leagues WHERE id = '930b2979-b01e-4f77-94a9-03592f9c582e';

-- Tercera RFEF 1 → Tercera División RFEF - Group 1 (af_id=439)
UPDATE matches SET league_id = 'fabbbf43-d8bd-4193-99fb-f8b624034f42' WHERE league_id = '2c7c06e9-d0d2-4dd6-a91a-28eb311c25db';
DELETE FROM leagues WHERE id = '2c7c06e9-d0d2-4dd6-a91a-28eb311c25db';

-- Segunda RFEF 2 → Segunda División RFEF - Group 2 (af_id=876)
UPDATE matches SET league_id = 'da411ba7-eaa9-4613-9f5c-805d15c6c783' WHERE league_id = 'c0944328-ef42-427b-a002-7d9cd4a79e62';
DELETE FROM leagues WHERE id = 'c0944328-ef42-427b-a002-7d9cd4a79e62';

-- Primera RFEF 2 → Primera División RFEF - Group 2 (af_id=436)
UPDATE matches SET league_id = '5c816839-dac4-490b-a2ab-2341d7d5116c' WHERE league_id = '771a0941-0663-4343-975a-56f0e02c5cfb';
DELETE FROM leagues WHERE id = '771a0941-0663-4343-975a-56f0e02c5cfb';

-- === SWEDEN (Division 2 regional) ===

-- Division 2 VG → Division 2 - Västra Götaland (af_id=596)
UPDATE matches SET league_id = '732608d0-fd6f-47a0-b9e9-5537e8d7d0d0' WHERE league_id = 'e64f5581-0f39-44b3-a192-1ec025159956';
DELETE FROM leagues WHERE id = 'e64f5581-0f39-44b3-a192-1ec025159956';

-- Division 2 SG → Division 2 - Södra Svealand (af_id=595)
UPDATE matches SET league_id = '2eeeb001-0a33-4247-a74f-0e84bb96506e' WHERE league_id = 'c35604c9-ce48-43a8-afb0-b8f5261f3cfa';
DELETE FROM leagues WHERE id = 'c35604c9-ce48-43a8-afb0-b8f5261f3cfa';

-- Division 2 NG → Division 2 - Norra Götaland (af_id=592)
UPDATE matches SET league_id = 'ec6f743e-7a26-431d-a688-a3ec6e2d94f6' WHERE league_id = 'f68a87a7-488c-4909-8f86-e1b1a4592ea7';
DELETE FROM leagues WHERE id = 'f68a87a7-488c-4909-8f86-e1b1a4592ea7';

-- === AMERICAS ===

-- Reserves Leagues U23 → Reserve League (Argentina, af_id=906)
UPDATE matches SET league_id = 'a1e97c06-e5e3-4982-932f-163a1c340f43' WHERE league_id = '2995b5bb-c390-4390-929b-dc55155e2066';
DELETE FROM leagues WHERE id = '2995b5bb-c390-4390-929b-dc55155e2066';

-- Campeonato Brasileiro U20 → Brasileiro U20 A (af_id=740)
UPDATE matches SET league_id = 'a2984559-0095-4e53-9a22-fb85e8451e49' WHERE league_id = 'ba6401da-6ab4-4820-9f65-bcd298285ae1';
DELETE FROM leagues WHERE id = 'ba6401da-6ab4-4820-9f65-bcd298285ae1';

-- Campeonato Brasileiro B U20 → Brasileiro U20 B (af_id=1183)
UPDATE matches SET league_id = '6eae7a19-df94-4889-8cc6-bee75b83b33a' WHERE league_id = '05452aca-46c7-40fd-8652-ff76881d0d4e';
DELETE FROM leagues WHERE id = '05452aca-46c7-40fd-8652-ff76881d0d4e';

-- Campeonato Brasileiro (W) → Brasileiro Women (af_id=74)
UPDATE matches SET league_id = '745be8e6-eb9d-492f-9322-04113f20a414' WHERE league_id = '59596118-5b58-472d-9145-9854f2a4db64';
DELETE FROM leagues WHERE id = '59596118-5b58-472d-9145-9854f2a4db64';

-- Copa Sul - Sudeste → Copa Sul-Sudeste (af_id=1224)
UPDATE matches SET league_id = '22e796dd-de07-427f-86c1-79d1dec08319' WHERE league_id = '464c368c-7661-4493-a4a1-f23f55ede3fc';
DELETE FROM leagues WHERE id = '464c368c-7661-4493-a4a1-f23f55ede3fc';

-- Paulista A2 → Paulista - A2 (af_id=476)
UPDATE matches SET league_id = '0bc444b5-468c-4247-92a7-e47079ad4a92' WHERE league_id = '46883357-829c-45f8-b8a3-7a2f5faf6271';
DELETE FROM leagues WHERE id = '46883357-829c-45f8-b8a3-7a2f5faf6271';

-- Cearense 2 → Cearense - 2 (af_id=620)
UPDATE matches SET league_id = '2542a744-e91a-4cb3-8c82-884f827cfecf' WHERE league_id = 'a7e6c904-7a1f-492c-9d9e-9e4980ddcd3f';
DELETE FROM leagues WHERE id = 'a7e6c904-7a1f-492c-9d9e-9e4980ddcd3f';

-- Copa Paraense U20 → Paraense U20 (af_id=1157)
UPDATE matches SET league_id = 'bfff7f86-559a-4032-b768-9dfe00100c53' WHERE league_id = '572a2b35-d2db-45be-a711-30732ea9c941';
DELETE FROM leagues WHERE id = '572a2b35-d2db-45be-a711-30732ea9c941';

-- Serie B (Ecuador) → Liga Pro Serie B (Ecuador, af_id=243)
UPDATE matches SET league_id = 'ad269eb4-70dc-49d4-bbf7-9805e643ac9d' WHERE league_id = '9e7188dd-fbf0-46c7-a224-130fee6599cb';
DELETE FROM leagues WHERE id = '9e7188dd-fbf0-46c7-a224-130fee6599cb';

-- Liga Nacional Guatemala → Liga Nacional (Guatemala, af_id=339)
UPDATE matches SET league_id = '47163753-f29d-4292-8251-b864489afbb3' WHERE league_id = '0be1e8db-b4a2-4983-9055-f5bb8a86303a';
DELETE FROM leagues WHERE id = '0be1e8db-b4a2-4983-9055-f5bb8a86303a';

-- NWSL (W) → NWSL Women (USA, af_id=254)
UPDATE matches SET league_id = '12cb7d9d-8a2a-4aa0-89ab-2a97f44fbc04' WHERE league_id = '93e05134-a598-4de5-a61c-8648ee857ff5';
DELETE FROM leagues WHERE id = '93e05134-a598-4de5-a61c-8648ee857ff5';

-- Liga MX Femenil (W) → Liga MX Femenil (Mexico, af_id=673)
UPDATE matches SET league_id = 'b6cba5c7-793d-400e-8bf5-814bb1ca520e' WHERE league_id = 'f5dcc19c-a0a7-4320-a158-b2645c39b6f8';
DELETE FROM leagues WHERE id = 'f5dcc19c-a0a7-4320-a158-b2645c39b6f8';

-- Liga Femenina Profesional → Liga Femenina (Colombia, af_id=712)
UPDATE matches SET league_id = '0ff73122-094b-4ead-aa6d-4bbf83c77bf7' WHERE league_id = 'b4788e73-4da5-463a-8058-6cd4caaaa645';
DELETE FROM leagues WHERE id = 'b4788e73-4da5-463a-8058-6cd4caaaa645';

-- Liga Nacional Honduras → Liga Nacional (Honduras, af_id=234)
UPDATE matches SET league_id = '6ed9c517-b1c6-4f96-b4c1-e5e615a2c247' WHERE league_id = 'f75c1489-e002-4827-a576-50e7627120cc';
DELETE FROM leagues WHERE id = 'f75c1489-e002-4827-a576-50e7627120cc';

-- Liga de Ascenso (Costa Rica) → Liga de Ascenso (Costa-Rica, af_id=163)
UPDATE matches SET league_id = '8544b1ed-dac9-4891-9eee-e78b7dfae37f' WHERE league_id = '12090079-a9b8-45f1-98c9-5a16e47eb663';
DELETE FROM leagues WHERE id = '12090079-a9b8-45f1-98c9-5a16e47eb663';

-- Segunda Division → Segunda División (Peru, af_id=282)
UPDATE matches SET league_id = 'fd4f7be2-fe3e-404a-aa4a-f8dcf3ad7018' WHERE league_id = 'd4a1cde7-870d-4b8b-a644-2e901a757318';
DELETE FROM leagues WHERE id = 'd4a1cde7-870d-4b8b-a644-2e901a757318';

-- Liga 1 → Primera División (Peru, af_id=281)
UPDATE matches SET league_id = '6902384d-5da7-4a41-96bf-bf0543c7aaf2' WHERE league_id = '68cc0f71-93ff-471b-b57a-1a482c785965';
DELETE FROM leagues WHERE id = '68cc0f71-93ff-471b-b57a-1a482c785965';

-- Primera Division Nicaragua → Primera Division (Nicaragua, af_id=396)
UPDATE matches SET league_id = '19f5b26e-da79-47d0-8875-7309550ecede' WHERE league_id = 'b96b2b74-9e32-4f8b-800f-2d9d6af4c385';
DELETE FROM leagues WHERE id = 'b96b2b74-9e32-4f8b-800f-2d9d6af4c385';

-- === ASIA / MIDDLE EAST ===

-- Professional League → Pro League (Saudi-Arabia, af_id=307)
UPDATE matches SET league_id = 'ea2f6741-908e-4cff-8f7d-3d3c46a00691' WHERE league_id = '993ced56-2cc6-4873-8413-b52aaaf5adce';
DELETE FROM leagues WHERE id = '993ced56-2cc6-4873-8413-b52aaaf5adce';

-- Division 1 (Saudi Arabia) → Division 1 (Saudi-Arabia, af_id=308)
UPDATE matches SET league_id = '1a59a517-cf8a-4234-8bf6-8ade5460d111' WHERE league_id = 'c0017c82-b7b4-4c65-8980-8bcd87a1808c';
DELETE FROM leagues WHERE id = 'c0017c82-b7b4-4c65-8980-8bcd87a1808c';

-- Division 1 (UAE) → Division 1 (United-Arab-Emirates, af_id=303)
UPDATE matches SET league_id = '72f740c3-833c-4629-8e1d-75a9826588c0' WHERE league_id = '1fd4e262-d6a5-4d55-a066-ad0cd25d862f';
DELETE FROM leagues WHERE id = '1fd4e262-d6a5-4d55-a066-ad0cd25d862f';

-- Qatar Stars League → Stars League (Qatar, af_id=305)
UPDATE matches SET league_id = 'ecff894a-9a57-46e7-bf03-d332c185b504' WHERE league_id = 'bcdd92b6-54d3-4731-aa61-69c9ef0c171d';
DELETE FROM leagues WHERE id = 'bcdd92b6-54d3-4731-aa61-69c9ef0c171d';

-- Premier League (Iraq) → Iraqi League (Iraq, af_id=542)
UPDATE matches SET league_id = '32be5c36-2098-4441-9e62-b3d65a490270' WHERE league_id = '928976b0-f585-4d2b-8d7e-824bbaa55aa9';
DELETE FROM leagues WHERE id = '928976b0-f585-4d2b-8d7e-824bbaa55aa9';

-- Premier League (Hong Kong) → Premier League (Hong-Kong, af_id=380)
UPDATE matches SET league_id = '4a4515bf-85f1-4206-a13b-db1a626049af' WHERE league_id = '7ef50900-3904-401d-bf1c-7b2c5813d632';
DELETE FROM leagues WHERE id = '7ef50900-3904-401d-bf1c-7b2c5813d632';

-- Premier League (Jordan) → League (Jordan, af_id=387)
UPDATE matches SET league_id = '5c9b02e1-7bd3-45f8-9bac-ae3485a39540' WHERE league_id = '07a0ca34-ba35-472a-b82d-7dae51c8a25f';
DELETE FROM leagues WHERE id = '07a0ca34-ba35-472a-b82d-7dae51c8a25f';

-- K-League Women → WK-League (South Korea, af_id=660)
UPDATE matches SET league_id = 'f9f9810c-2587-4b4b-8f09-d0c5ed5b619d' WHERE league_id = 'c65cade7-acbb-4b90-90ef-3ed4ecb1747e';
DELETE FROM leagues WHERE id = 'c65cade7-acbb-4b90-90ef-3ed4ecb1747e';

-- === JAPAN ===

-- J2/J3 100 Year Vision League → J2/J3 League (af_id=99)
UPDATE matches SET league_id = '3022f063-b707-4725-8a34-2e90b1f8b361' WHERE league_id = '14bc3711-332e-4fc2-82dc-758297b7b5d7';
DELETE FROM leagues WHERE id = '14bc3711-332e-4fc2-82dc-758297b7b5d7';

-- J1 100 Year Vision League → J1 League (af_id=98)
UPDATE matches SET league_id = 'e4226c0b-2845-422d-9e10-53d3209af661' WHERE league_id = '83ec5155-9d96-483e-874b-de5c25fbaa85';
DELETE FROM leagues WHERE id = '83ec5155-9d96-483e-874b-de5c25fbaa85';

-- Coppa Primavera U20 → Coppa Italia Primavera (Italy, af_id=704)
UPDATE matches SET league_id = '50e45b52-a095-4a81-a187-fe4438dcff1d' WHERE league_id = '7b2f1d15-5e0a-4079-a3ec-4b04d1200358';
DELETE FROM leagues WHERE id = '7b2f1d15-5e0a-4079-a3ec-4b04d1200358';

-- === AFRICA ===

-- Premier League (Ethiopia) ← Premier League (W) (Ethiopia)
UPDATE matches SET league_id = 'cbe6d173-9d8d-4d83-b2ce-16890babf6d0' WHERE league_id = '3076fee3-9205-4a45-98ee-98f1cbb4fbfb';
DELETE FROM leagues WHERE id = '3076fee3-9205-4a45-98ee-98f1cbb4fbfb';

-- === NORWAY: OBOS-ligaen duplicates ===
-- OBOS-ligaen (Kambi) → OBOS-ligaen (AF) — merge if both exist
UPDATE matches SET league_id = '04936edb-baf3-4e95-b8f4-8b9e6c2dd5a5'
WHERE league_id = '241ae5f9-1afd-466c-95cd-adb2d8a0a923'
  AND EXISTS (SELECT 1 FROM leagues WHERE id = '04936edb-baf3-4e95-b8f4-8b9e6c2dd5a5');
DELETE FROM leagues WHERE id = '241ae5f9-1afd-466c-95cd-adb2d8a0a923'
  AND EXISTS (SELECT 1 FROM leagues WHERE id = '04936edb-baf3-4e95-b8f4-8b9e6c2dd5a5');

-- === ESTONIA: Esiliiga duplicates ===
UPDATE matches SET league_id = 'b25ad12d-fb30-4393-9779-395952b1b79d'
WHERE league_id = '194fbe02-2011-4dd9-9493-19b3a84dc534'
  AND EXISTS (SELECT 1 FROM leagues WHERE id = 'b25ad12d-fb30-4393-9779-395952b1b79d');
DELETE FROM leagues WHERE id = '194fbe02-2011-4dd9-9493-19b3a84dc534'
  AND EXISTS (SELECT 1 FROM leagues WHERE id = 'b25ad12d-fb30-4393-9779-395952b1b79d');

-- ============================================================
-- 3. ENSURE show_on_frontend IS SET ON MERGED AF LEAGUES
-- ============================================================
-- Some of these were only set on the now-deleted Kambi league.
-- Set it on the AF league that now holds all matches.

UPDATE leagues SET show_on_frontend = true
WHERE id IN (
  '627937cc-6c56-482d-a769-a8d83c9fcacf',  -- UEFA Europa League
  'c94f1f6a-dfd1-4545-9ae3-21893aab7a8a',  -- UEFA Europa Conference League
  '1bb28d8a-8b00-436b-aef7-7ba9c86dcc3f',  -- UEFA Champions League
  '4bd09038-37ed-4946-abab-0c60c28aaf47',  -- CONMEBOL Libertadores
  'c7483064-f6f4-44d8-b708-8bff85bcdaf1',  -- CONMEBOL Sudamericana
  '8d31c7e0-da67-4d69-bc81-cd489c4064a7',  -- CONCACAF Champions League
  'eb59525f-b737-47b9-81a1-d081f55ae919',  -- Botola Pro (Morocco)
  'ea2f6741-908e-4cff-8f7d-3d3c46a00691',  -- Pro League (Saudi Arabia)
  '977b3b8c-d0e9-47cf-ab2c-a51432ba4e52',  -- Superliga (Denmark)
  '01ba89d6-1b7b-4ee7-95e9-adf8cd617afc',  -- Championship (England)
  'e4226c0b-2845-422d-9e10-53d3209af661',  -- J1 League (Japan)
  '3022f063-b707-4725-8a34-2e90b1f8b361',  -- J2/J3 League (Japan)
  '12cb7d9d-8a2a-4aa0-89ab-2a97f44fbc04'   -- NWSL Women (USA)
);

-- ============================================================
-- 4. SET LEAGUE PRIORITIES
-- ============================================================
-- Priority 10: Big 5 domestic top flights + continental cups
-- Priority 20: Major secondary European leagues + big non-European top flights
-- Priority 30: Other notable leagues
-- NULL: everything else (sorted alphabetically after priority leagues)

-- Continental cups: priority 10
UPDATE leagues SET priority = 10
WHERE api_football_id IN (2, 3, 848, 13, 11, 16, 480, 531);
-- 2=UCL, 3=UEL, 848=UECL, 13=Libertadores, 11=Sudamericana, 16=CONCACAF CL, 480=Euro, 531=Euro Qual

-- Big 5 top flights: priority 10
UPDATE leagues SET priority = 10
WHERE api_football_id IN (39, 140, 135, 78, 61);
-- 39=EPL, 140=La Liga, 135=Serie A, 78=Bundesliga, 61=Ligue 1

-- Major second tier + notable top flights: priority 20
UPDATE leagues SET priority = 20
WHERE api_football_id IN (
  40,   -- Championship (England)
  141,  -- La Liga 2 (Spain)
  136,  -- Serie B (Italy)
  79,   -- 2. Bundesliga (Germany)
  62,   -- Ligue 2 (France)
  88,   -- Eredivisie (Netherlands)
  94,   -- Primeira Liga (Portugal)
  144,  -- Jupiler Pro League (Belgium)
  203,  -- Super Lig (Turkey)
  253,  -- MLS (USA)
  262,  -- Liga MX (Mexico)
  71,   -- Serie A (Brazil)
  128,  -- Liga Profesional (Argentina)
  307,  -- Pro League (Saudi Arabia)
  98,   -- J1 League (Japan)
  292   -- K League 1 (South Korea)
);

-- Other notable leagues: priority 30
UPDATE leagues SET priority = 30
WHERE api_football_id IN (
  119,  -- Superliga (Denmark)
  113,  -- Allsvenskan (Sweden)
  103,  -- Eliteserien (Norway)
  106,  -- Ekstraklasa (Poland)
  218,  -- Bundesliga (Austria)
  207,  -- Super League (Switzerland)
  179,  -- Premiership (Scotland)
  197,  -- Super League 1 (Greece)
  169,  -- Super League (China)
  254,  -- NWSL Women (USA)
  383,  -- Ligat Ha'al (Israel)
  200,  -- Botola Pro (Morocco)
  233,  -- Premier League (Egypt)
  332,  -- Premier League (Ukraine)
  286,  -- Super Liga (Serbia)
  72,   -- Serie B (Brazil)
  73,   -- Copa Do Brasil (Brazil)
  188,  -- Veikkausliiga (Finland)
  210,  -- HNL (Croatia)
  271   -- NB I (Hungary)
);

-- ============================================================
-- 5. CLEAN UP: Delete Kambi leagues with 0 matches and no AF ID
-- ============================================================
-- These are orphan Kambi-created leagues that never got any AF data.
-- Only delete if they have no matches AND no teams pointing to them.
-- (teams.league_id is a dummy FK set by ensure_team() for placeholder grouping)
DELETE FROM leagues
WHERE api_football_id IS NULL
  AND id NOT IN (SELECT DISTINCT league_id FROM matches WHERE league_id IS NOT NULL)
  AND id NOT IN (SELECT DISTINCT league_id FROM teams WHERE league_id IS NOT NULL);

COMMIT;
