-- ============================================================================
-- LEAGUE-ORDER: Redesign league priority tiers (flat → 6-tier)
--
-- Previous system had only 3 base tiers (10, 20, 30) with CL and Big 5
-- both at priority 10, making ordering within the top tier alphabetical.
--
-- New 6-tier system (lower number = higher on page):
--   Priority 10 — Tier 1 continental: CL, World Cup, Euros, Copa America, Club WC
--   Priority 12 — Tier 2 continental: EL, ECL, Libertadores, Sudamericana,
--                 Nations League, Euro Q, AFCON, Asian Cup, CONCACAF CL
--   Priority 14 — Big 5 domestic: PL, La Liga, Serie A, Bundesliga, Ligue 1
--   Priority 20 — Strong secondary: Eredivisie, Primeira, SüperLig, MLS,
--                 Brasileirao, Saudi Pro, J-League, K-League, A-League, etc.
--   Priority 25 — Other secondary domestics (previously all at 30)
--   Priority 30 — Rest (unchanged)
--
-- FEATURED_PRIORITY (1/2) for continental cups on match days is unchanged.
-- ============================================================================

-- Tier 1 continental (keep at 10 — already there for CL/WC/Euros)
-- API-Football IDs: CL=2, WC=1, Club WC=15, Euros=480, Copa America=9
-- No change needed — these are already at 10.

-- Tier 2 continental: move from 10 → 12
-- EL=3, ECL=848, Libertadores=13, Sudamericana=11, Nations League=4,
-- Euro Q=531, AFCON=6, Asian Cup=29, CONCACAF CL=16
UPDATE leagues
SET priority = 12
WHERE api_football_id IN (3, 848, 13, 11, 4, 531, 6, 29, 16)
  AND priority = 10;

-- Big 5: move from 10 → 14
-- PL=39, La Liga=140, Serie A=135, Bundesliga=78, Ligue 1=61
UPDATE leagues
SET priority = 14
WHERE api_football_id IN (39, 140, 135, 78, 61)
  AND priority = 10;

-- Strong secondary (keep at 20 — already correct)
-- Championship=40, Segunda=141, SerieB=136, 2.Bundesliga=79, Ligue2=62,
-- Eredivisie=88, Primeira=94, Jupiler=144, SüperLig=203, MLS=253,
-- LaLiga2=262, Brasileirao=71, Saudi=128, J-League=307, K-League=98, A-League=292
-- No change needed.

-- Promote notable leagues from 30 → 25
-- Scottish Prem=179, Ekstraklasa=106, Czech Liga=345, Greek SL=197,
-- Swiss SL=207, Austrian Bundesliga=218, Danish SL=119, Swedish AL=113,
-- Norwegian Eliteserien=103, Romanian Liga1=283, Hungarian NB1=271,
-- Belgian D2=144, Serbian SL=286, Ukrainian PL=333
-- (These are real top-flight leagues that rank above pure cups/lower divisions)
UPDATE leagues
SET priority = 25
WHERE api_football_id IN (
    179,  -- Scottish Premiership
    106,  -- Polish Ekstraklasa
    345,  -- Czech First League
    197,  -- Greek Super League
    207,  -- Swiss Super League
    218,  -- Austrian Bundesliga
    119,  -- Danish Superliga
    113,  -- Allsvenskan (Sweden)
    103,  -- Eliteserien (Norway)
    283,  -- Romanian Liga 1
    271,  -- Hungarian NB I
    333,  -- Ukrainian Premier League
    210,  -- Croatian HNL
    188,  -- Bulgarian First League
    200,  -- Finnish Veikkausliiga
    233,  -- Argentinian Primera
    254,  -- Chilean Primera
    383,  -- Colombian Liga BetPlay
    332,  -- Bolivian Liga
    286,  -- Serbian Super Liga
    72,   -- Brazilian Serie B
    73,   -- Brazilian Copa do Brasil
    386   -- South African PSL
)
  AND priority = 30;
