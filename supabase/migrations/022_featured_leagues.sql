-- Add show_on_frontend flag to control which leagues appear on the website.
-- Engine pipelines continue analyzing ALL leagues; this only filters the UI.
-- Flip to true/false anytime to add/remove leagues without code changes.

ALTER TABLE leagues ADD COLUMN IF NOT EXISTS show_on_frontend boolean NOT NULL DEFAULT false;

-- Set featured leagues: top flights, major 2nd divisions, continental cups.
-- Criteria: recognizable to bettors, good bookmaker coverage, dense data.

UPDATE leagues SET show_on_frontend = true
WHERE id IN (
  -- === ENGLAND ===
  '78aa2ed4-983d-4afc-a7f3-b4b13c60eac9',  -- England / The Championship
  '01ba89d6-1b7b-4ee7-95e9-adf8cd617afc',  -- England / Championship
  '81ade57e-f389-4374-8674-08a5ae3b7b18',  -- England / FA Cup
  '24146838-6c73-4c4a-ad5c-51402f6e57b7',  -- England / National League

  -- === SPAIN ===
  '41c36ff6-aaef-43ce-a5a6-4d1aa1dcef32',  -- Spain / Copa del Rey
  '8f7b46bf-0753-4f52-b365-6f313001ba8b',  -- Spain / La Liga 2

  -- === ITALY ===
  '81156b2d-bc31-4c38-9f28-873a5158b873',  -- Italy / Serie B

  -- === FRANCE ===
  '0877d3ca-76d6-4723-a34d-ed85606078fb',  -- France / Coupe de France

  -- === SCANDINAVIA ===
  '977b3b8c-d0e9-47cf-ab2c-a51432ba4e52',  -- Denmark / Superliga
  'dae3e2a4-b5ed-4796-a53c-a0d415fe8706',  -- Denmark / Superligaen
  'db440d96-9150-4717-8f18-e77512896e5d',  -- Sweden / Allsvenskan
  'b1eb2f6a-77d3-412a-9c67-5c8818dd6bd2',  -- Sweden / Superettan
  '554a5bf1-c4c1-4e95-92b1-c7d85866e7ef',  -- Norway / Eliteserien
  '241ae5f9-1afd-466c-95cd-adb2d8a0a923',  -- Norway / OBOS-ligaen

  -- === OTHER EUROPE ===
  '75f4f1d0-4441-4094-b07c-c1ec093856fa',  -- Ukraine / Premier League
  'cfe61322-06e6-460f-a421-4634b4224016',  -- Serbia / Super Liga
  'c44f4af7-ad82-4455-94ed-0f68734db163',  -- Scotland / Championship
  '9779c368-ddcb-4c83-8603-0d395bfaeb00',  -- Portugal / Liga 2

  -- === AMERICAS ===
  '6f74e12b-57a9-4139-bc22-d0ccaa483186',  -- USA / US Open Cup
  '85666955-2586-4073-86d5-9dd34d760f98',  -- USA / USL Championship
  '12cb7d9d-8a2a-4aa0-89ab-2a97f44fbc04',  -- USA / NWSL Women
  '179d1e47-6bba-4c77-980e-473755f38750',  -- Mexico / Liga de Expansión MX
  '4c354ebd-15d1-4b2b-8a01-4580615130be',  -- Mexico / Liga de Expansion MX
  '68682f46-2caf-4cb3-a087-548a38bce0a1',  -- Brazil / Brasileirao Serie B
  '716c7210-147f-499c-81f6-8ac1c076e080',  -- Brazil / Brasileirao Serie C
  '95b21726-709a-4f61-8f75-f3b8ccd9bca1',  -- Argentina / Copa de la Liga Profesional
  'e77ab582-6f07-4ffc-876b-f0e4fdb4f485',  -- Colombia / Primera A

  -- === ASIA ===
  'e4226c0b-2845-422d-9e10-53d3209af661',  -- Japan / J1 League
  '3022f063-b707-4725-8a34-2e90b1f8b361',  -- Japan / J2/J3 League
  '83ec5155-9d96-483e-874b-de5c25fbaa85',  -- Japan / J1 100 Year Vision League
  '14bc3711-332e-4fc2-82dc-758297b7b5d7',  -- Japan / J2/J3 100 Year Vision League
  'ea2f6741-908e-4cff-8f7d-3d3c46a00691',  -- Saudi-Arabia / Pro League
  '993ced56-2cc6-4873-8413-b52aaaf5adce',  -- Saudi Arabia / Professional League
  'c336f599-9667-48ae-94c0-35cd10bfb816',  -- Australia / A-League

  -- === AFRICA ===
  'f7fee3ac-88bc-4d69-a151-41b0f8b4d2d9',  -- Egypt / Premier League
  '8736522e-c2b6-43d2-aaa9-9f3e6583bbf5',  -- Morocco / Botola
  '8e4a16c9-9e68-4adc-af91-a60fbff3f9cc',  -- Tunisia / Ligue 1

  -- === CONTINENTAL CUPS ===
  '04f5028b-eb30-4f6f-a7bd-2c49c74b60f0',  -- Unknown / Champions League
  'cbeee5b4-2425-4458-9572-8cfb6ba589b8',  -- Unknown / Europa League
  'b654d623-256f-4369-9215-81ee308200ae',  -- Unknown / Conference League
  '6eebabe2-b409-4124-a195-061e90eb89df',  -- Unknown / CONCACAF Champions Cup
  '4bd09038-37ed-4946-abab-0c60c28aaf47',  -- World / CONMEBOL Libertadores
  'c7483064-f6f4-44d8-b708-8bff85bcdaf1',  -- World / CONMEBOL Sudamericana
  '8d31c7e0-da67-4d69-bc81-cd489c4064a7',  -- World / CONCACAF Champions League
  '6de5c36a-cc78-4f23-9edb-06e46dda027f',  -- World / CAF Champions League
  'ff82ac05-84e7-47d0-a82d-f9ec80329423',  -- World / Euro Championship
  '27b90961-5a78-4bf1-a8e6-ef0cff535116'   -- World / Euro Championship - Qualification
);

-- Also catch any top-flight leagues by name pattern that we may have missed above.
-- These are leagues where the name strongly implies top-tier domestic football.
UPDATE leagues SET show_on_frontend = true
WHERE show_on_frontend = false
  AND (
    -- Exact top-flight league names (excluding reserves/youth/women/cups already handled)
    (country = 'England' AND name IN ('Premier League'))
    OR (country = 'Spain' AND name IN ('La Liga'))
    OR (country = 'Germany' AND name IN ('Bundesliga', '2. Bundesliga', 'DFB Pokal'))
    OR (country = 'Italy' AND name IN ('Serie A', 'Coppa Italia'))
    OR (country = 'France' AND name IN ('Ligue 1', 'Ligue 2'))
    OR (country = 'Netherlands' AND name IN ('Eredivisie', 'Eerste Divisie', 'KNVB Beker'))
    OR (country = 'Portugal' AND name IN ('Primeira Liga', 'Liga Portugal'))
    OR (country = 'Belgium' AND name IN ('Jupiler Pro League', 'Pro League', 'Cup'))
    OR (country = 'Turkey' AND name IN ('Super Lig', 'Süper Lig'))
    OR (country = 'Scotland' AND name IN ('Premiership', 'FA Cup', 'League Cup'))
    OR (country = 'Greece' AND name IN ('Super League', 'Super League 1'))
    OR (country = 'Switzerland' AND name IN ('Super League'))
    OR (country = 'Austria' AND name IN ('Bundesliga', 'Cup'))
    OR (country = 'Poland' AND name IN ('Ekstraklasa'))
    OR (country = 'Czech Republic' AND name IN ('First League', 'Czech Liga'))
    OR (country = 'Czech-Republic' AND name IN ('First League', 'Czech Liga'))
    OR (country = 'Romania' AND name IN ('Liga I', 'SuperLiga'))
    OR (country = 'Croatia' AND name IN ('HNL'))
    OR (country = 'Hungary' AND name IN ('NB I', 'OTP Bank Liga'))
    OR (country = 'Finland' AND name IN ('Veikkausliiga'))
    OR (country = 'USA' AND name IN ('MLS'))
    OR (country = 'Mexico' AND name IN ('Liga MX'))
    OR (country = 'Brazil' AND name IN ('Serie A'))
    OR (country = 'Argentina' AND name IN ('Liga Profesional'))
    OR (country = 'China' AND name IN ('Super League'))
    OR (country = 'South-Korea' AND name IN ('K League 1', 'K-League'))
    OR (country = 'India' AND name IN ('ISL', 'Indian Super League'))
  );

-- Index for fast filtering
CREATE INDEX IF NOT EXISTS idx_leagues_show_on_frontend
  ON leagues (id) WHERE show_on_frontend = true;

COMMENT ON COLUMN leagues.show_on_frontend IS
  'Controls visibility on the website. Engine pipelines analyze all leagues regardless. Flip this to add/remove leagues from the UI.';
