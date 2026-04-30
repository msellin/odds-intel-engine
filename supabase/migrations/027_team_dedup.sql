-- ============================================================
-- Migration 027: Merge Duplicate Teams (Kambi → AF canonical)
-- ============================================================
-- Problem: Kambi odds scraper creates teams with slightly different
-- names than API-Football (accents, punctuation, spacing), causing
-- duplicate match rows for the same real-world fixture.
-- Fix: Merge all Kambi-created duplicate teams into their AF counterparts.
-- Tables with team FK: matches, lineups, players, manager_tenures,
-- team_elo_daily, team_form_cache.

BEGIN;

-- Académico Viseu U23 (Portugal) -> Academico Viseu U23 (Portugal)
UPDATE matches SET home_team_id = 'fa6363ef-7efa-4246-a6e3-37bc0bb031e7' WHERE home_team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
UPDATE matches SET away_team_id = 'fa6363ef-7efa-4246-a6e3-37bc0bb031e7' WHERE away_team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
UPDATE lineups SET team_id = 'fa6363ef-7efa-4246-a6e3-37bc0bb031e7' WHERE team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
UPDATE players SET team_id = 'fa6363ef-7efa-4246-a6e3-37bc0bb031e7' WHERE team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
UPDATE manager_tenures SET team_id = 'fa6363ef-7efa-4246-a6e3-37bc0bb031e7' WHERE team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
DELETE FROM team_elo_daily WHERE team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
DELETE FROM team_form_cache WHERE team_id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';
DELETE FROM teams WHERE id = '35424d53-ce6f-4baa-b92e-d88a2b559d3d';

-- Al Ahli Jeddah (Saudi Arabia) -> Al-Ahli Jeddah (Saudi-Arabia)
UPDATE matches SET home_team_id = 'ee38af74-6944-4474-b2cc-f15ae9b7dc9b' WHERE home_team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
UPDATE matches SET away_team_id = 'ee38af74-6944-4474-b2cc-f15ae9b7dc9b' WHERE away_team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
UPDATE lineups SET team_id = 'ee38af74-6944-4474-b2cc-f15ae9b7dc9b' WHERE team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
UPDATE players SET team_id = 'ee38af74-6944-4474-b2cc-f15ae9b7dc9b' WHERE team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
UPDATE manager_tenures SET team_id = 'ee38af74-6944-4474-b2cc-f15ae9b7dc9b' WHERE team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
DELETE FROM team_elo_daily WHERE team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
DELETE FROM team_form_cache WHERE team_id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';
DELETE FROM teams WHERE id = '9c2e94d6-c3a5-491d-8603-615ecc8b2e32';

-- Al Fateh (Saudi Arabia) -> Al-Fateh (Saudi-Arabia)
UPDATE matches SET home_team_id = 'fa54be98-0cb5-49e5-b0ea-1da4dc55ef8f' WHERE home_team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
UPDATE matches SET away_team_id = 'fa54be98-0cb5-49e5-b0ea-1da4dc55ef8f' WHERE away_team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
UPDATE lineups SET team_id = 'fa54be98-0cb5-49e5-b0ea-1da4dc55ef8f' WHERE team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
UPDATE players SET team_id = 'fa54be98-0cb5-49e5-b0ea-1da4dc55ef8f' WHERE team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
UPDATE manager_tenures SET team_id = 'fa54be98-0cb5-49e5-b0ea-1da4dc55ef8f' WHERE team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
DELETE FROM team_elo_daily WHERE team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
DELETE FROM team_form_cache WHERE team_id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';
DELETE FROM teams WHERE id = '43109ee7-53b3-4a30-89cb-c30bac60a8fe';

-- Al-Kholood (Saudi Arabia) -> Al Kholood (Saudi-Arabia)
UPDATE matches SET home_team_id = 'fdc7e721-7e56-4e42-85a1-581e90b1803f' WHERE home_team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
UPDATE matches SET away_team_id = 'fdc7e721-7e56-4e42-85a1-581e90b1803f' WHERE away_team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
UPDATE lineups SET team_id = 'fdc7e721-7e56-4e42-85a1-581e90b1803f' WHERE team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
UPDATE players SET team_id = 'fdc7e721-7e56-4e42-85a1-581e90b1803f' WHERE team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
UPDATE manager_tenures SET team_id = 'fdc7e721-7e56-4e42-85a1-581e90b1803f' WHERE team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
DELETE FROM team_elo_daily WHERE team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
DELETE FROM team_form_cache WHERE team_id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';
DELETE FROM teams WHERE id = '3cf27069-2aac-4b9e-83d1-d85ba1e7539d';

-- Al-Khor (Qatar) -> Al Khor (Qatar)
UPDATE matches SET home_team_id = 'd64b4ef2-5f57-4840-a5db-49bc41260e07' WHERE home_team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
UPDATE matches SET away_team_id = 'd64b4ef2-5f57-4840-a5db-49bc41260e07' WHERE away_team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
UPDATE lineups SET team_id = 'd64b4ef2-5f57-4840-a5db-49bc41260e07' WHERE team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
UPDATE players SET team_id = 'd64b4ef2-5f57-4840-a5db-49bc41260e07' WHERE team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
UPDATE manager_tenures SET team_id = 'd64b4ef2-5f57-4840-a5db-49bc41260e07' WHERE team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
DELETE FROM team_elo_daily WHERE team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
DELETE FROM team_form_cache WHERE team_id = '859fb3be-d15a-499e-a24b-457ae46becd2';
DELETE FROM teams WHERE id = '859fb3be-d15a-499e-a24b-457ae46becd2';

-- Al-Markhiya (Qatar) -> Al Markhiya (Qatar)
UPDATE matches SET home_team_id = '3b1ee90e-5b22-4634-9237-27214e0c8b96' WHERE home_team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
UPDATE matches SET away_team_id = '3b1ee90e-5b22-4634-9237-27214e0c8b96' WHERE away_team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
UPDATE lineups SET team_id = '3b1ee90e-5b22-4634-9237-27214e0c8b96' WHERE team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
UPDATE players SET team_id = '3b1ee90e-5b22-4634-9237-27214e0c8b96' WHERE team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
UPDATE manager_tenures SET team_id = '3b1ee90e-5b22-4634-9237-27214e0c8b96' WHERE team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
DELETE FROM team_elo_daily WHERE team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
DELETE FROM team_form_cache WHERE team_id = 'b3a2e968-2d5e-444a-8443-f247e309952c';
DELETE FROM teams WHERE id = 'b3a2e968-2d5e-444a-8443-f247e309952c';

-- Al Nassr (Saudi Arabia) -> Al-Nassr (Saudi-Arabia)
UPDATE matches SET home_team_id = 'c5bbbeec-3db1-42b5-b929-4406eba23e8b' WHERE home_team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
UPDATE matches SET away_team_id = 'c5bbbeec-3db1-42b5-b929-4406eba23e8b' WHERE away_team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
UPDATE lineups SET team_id = 'c5bbbeec-3db1-42b5-b929-4406eba23e8b' WHERE team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
UPDATE players SET team_id = 'c5bbbeec-3db1-42b5-b929-4406eba23e8b' WHERE team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
UPDATE manager_tenures SET team_id = 'c5bbbeec-3db1-42b5-b929-4406eba23e8b' WHERE team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
DELETE FROM team_elo_daily WHERE team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
DELETE FROM team_form_cache WHERE team_id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';
DELETE FROM teams WHERE id = '1aa09ba0-6020-4c0c-9e0c-c00066ee7a20';

-- Argentinos JRS (Argentina) -> Argentinos Jrs (Argentina)
UPDATE matches SET home_team_id = '47f1a2f8-3425-49af-8278-0cb817cc04b1' WHERE home_team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
UPDATE matches SET away_team_id = '47f1a2f8-3425-49af-8278-0cb817cc04b1' WHERE away_team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
UPDATE lineups SET team_id = '47f1a2f8-3425-49af-8278-0cb817cc04b1' WHERE team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
UPDATE players SET team_id = '47f1a2f8-3425-49af-8278-0cb817cc04b1' WHERE team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
UPDATE manager_tenures SET team_id = '47f1a2f8-3425-49af-8278-0cb817cc04b1' WHERE team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
DELETE FROM team_elo_daily WHERE team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
DELETE FROM team_form_cache WHERE team_id = '38938e9d-d420-4f62-b7f1-23ca8a294353';
DELETE FROM teams WHERE id = '38938e9d-d420-4f62-b7f1-23ca8a294353';

-- Arsenal W (England) -> Arsenal (W) (England)
UPDATE matches SET home_team_id = 'cdd01f3d-e199-497e-a3f8-83a8a8e79c66' WHERE home_team_id = '01e68385-95c9-43af-a171-3e9407498932';
UPDATE matches SET away_team_id = 'cdd01f3d-e199-497e-a3f8-83a8a8e79c66' WHERE away_team_id = '01e68385-95c9-43af-a171-3e9407498932';
UPDATE lineups SET team_id = 'cdd01f3d-e199-497e-a3f8-83a8a8e79c66' WHERE team_id = '01e68385-95c9-43af-a171-3e9407498932';
UPDATE players SET team_id = 'cdd01f3d-e199-497e-a3f8-83a8a8e79c66' WHERE team_id = '01e68385-95c9-43af-a171-3e9407498932';
UPDATE manager_tenures SET team_id = 'cdd01f3d-e199-497e-a3f8-83a8a8e79c66' WHERE team_id = '01e68385-95c9-43af-a171-3e9407498932';
DELETE FROM team_elo_daily WHERE team_id = '01e68385-95c9-43af-a171-3e9407498932';
DELETE FROM team_form_cache WHERE team_id = '01e68385-95c9-43af-a171-3e9407498932';
DELETE FROM teams WHERE id = '01e68385-95c9-43af-a171-3e9407498932';

-- Atlético-GO U20 (Brazil) -> Atlético GO U20 (Brazil)
UPDATE matches SET home_team_id = '630998b1-d5ff-4c8a-ace9-e3e1cbec9164' WHERE home_team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
UPDATE matches SET away_team_id = '630998b1-d5ff-4c8a-ace9-e3e1cbec9164' WHERE away_team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
UPDATE lineups SET team_id = '630998b1-d5ff-4c8a-ace9-e3e1cbec9164' WHERE team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
UPDATE players SET team_id = '630998b1-d5ff-4c8a-ace9-e3e1cbec9164' WHERE team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
UPDATE manager_tenures SET team_id = '630998b1-d5ff-4c8a-ace9-e3e1cbec9164' WHERE team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
DELETE FROM team_elo_daily WHERE team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
DELETE FROM team_form_cache WHERE team_id = '82b6623b-4dea-41c5-b14a-d826719317d4';
DELETE FROM teams WHERE id = '82b6623b-4dea-41c5-b14a-d826719317d4';

-- Atlético Mineiro W (Brazil) -> Atlético Mineiro (W) (Brazil)
UPDATE matches SET home_team_id = '74c54af8-a3c6-4513-9f01-0b7d0de596ef' WHERE home_team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
UPDATE matches SET away_team_id = '74c54af8-a3c6-4513-9f01-0b7d0de596ef' WHERE away_team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
UPDATE lineups SET team_id = '74c54af8-a3c6-4513-9f01-0b7d0de596ef' WHERE team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
UPDATE players SET team_id = '74c54af8-a3c6-4513-9f01-0b7d0de596ef' WHERE team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
UPDATE manager_tenures SET team_id = '74c54af8-a3c6-4513-9f01-0b7d0de596ef' WHERE team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
DELETE FROM team_elo_daily WHERE team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
DELETE FROM team_form_cache WHERE team_id = '733dac4e-ee95-4629-8187-9131df2cb4ae';
DELETE FROM teams WHERE id = '733dac4e-ee95-4629-8187-9131df2cb4ae';

-- Bahia W (Brazil) -> Bahia (W) (Brazil)
UPDATE matches SET home_team_id = '8fc5b2af-6875-4c6a-9811-fb444241714c' WHERE home_team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
UPDATE matches SET away_team_id = '8fc5b2af-6875-4c6a-9811-fb444241714c' WHERE away_team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
UPDATE lineups SET team_id = '8fc5b2af-6875-4c6a-9811-fb444241714c' WHERE team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
UPDATE players SET team_id = '8fc5b2af-6875-4c6a-9811-fb444241714c' WHERE team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
UPDATE manager_tenures SET team_id = '8fc5b2af-6875-4c6a-9811-fb444241714c' WHERE team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
DELETE FROM team_elo_daily WHERE team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
DELETE FROM team_form_cache WHERE team_id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';
DELETE FROM teams WHERE id = 'a40ef23d-28c3-42c0-8928-92437b74cefb';

-- Bayern Munich W (Germany) -> Bayern Munich (W) (Germany)
UPDATE matches SET home_team_id = '19d373d0-6d24-48e5-abb3-c50fc1aa94e3' WHERE home_team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
UPDATE matches SET away_team_id = '19d373d0-6d24-48e5-abb3-c50fc1aa94e3' WHERE away_team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
UPDATE lineups SET team_id = '19d373d0-6d24-48e5-abb3-c50fc1aa94e3' WHERE team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
UPDATE players SET team_id = '19d373d0-6d24-48e5-abb3-c50fc1aa94e3' WHERE team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
UPDATE manager_tenures SET team_id = '19d373d0-6d24-48e5-abb3-c50fc1aa94e3' WHERE team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
DELETE FROM team_elo_daily WHERE team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
DELETE FROM team_form_cache WHERE team_id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';
DELETE FROM teams WHERE id = '2d3a9c29-030d-4c23-a924-10dd8e0f1b63';

-- Brann W (Norway) -> Brann (W) (Norway)
UPDATE matches SET home_team_id = '5a070838-ea9d-4502-8c29-ae6d9a22b40e' WHERE home_team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
UPDATE matches SET away_team_id = '5a070838-ea9d-4502-8c29-ae6d9a22b40e' WHERE away_team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
UPDATE lineups SET team_id = '5a070838-ea9d-4502-8c29-ae6d9a22b40e' WHERE team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
UPDATE players SET team_id = '5a070838-ea9d-4502-8c29-ae6d9a22b40e' WHERE team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
UPDATE manager_tenures SET team_id = '5a070838-ea9d-4502-8c29-ae6d9a22b40e' WHERE team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
DELETE FROM team_elo_daily WHERE team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
DELETE FROM team_form_cache WHERE team_id = '3d6e1360-415f-430f-a911-2904c8316d76';
DELETE FROM teams WHERE id = '3d6e1360-415f-430f-a911-2904c8316d76';

-- Brinje-Grosuplje (Slovenia) -> Brinje Grosuplje (Slovenia)
UPDATE matches SET home_team_id = 'd89fdde2-3c98-46d9-909e-250ad81a9abe' WHERE home_team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
UPDATE matches SET away_team_id = 'd89fdde2-3c98-46d9-909e-250ad81a9abe' WHERE away_team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
UPDATE lineups SET team_id = 'd89fdde2-3c98-46d9-909e-250ad81a9abe' WHERE team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
UPDATE players SET team_id = 'd89fdde2-3c98-46d9-909e-250ad81a9abe' WHERE team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
UPDATE manager_tenures SET team_id = 'd89fdde2-3c98-46d9-909e-250ad81a9abe' WHERE team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
DELETE FROM team_elo_daily WHERE team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
DELETE FROM team_form_cache WHERE team_id = '884e6e8a-0460-49d3-a792-05190988ae06';
DELETE FROM teams WHERE id = '884e6e8a-0460-49d3-a792-05190988ae06';

-- Chapecoense-SC (Brazil) -> Chapecoense-sc (Brazil)
UPDATE matches SET home_team_id = '51b499b9-5cd9-4f5d-8ea4-c1c9f96efcfb' WHERE home_team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
UPDATE matches SET away_team_id = '51b499b9-5cd9-4f5d-8ea4-c1c9f96efcfb' WHERE away_team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
UPDATE lineups SET team_id = '51b499b9-5cd9-4f5d-8ea4-c1c9f96efcfb' WHERE team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
UPDATE players SET team_id = '51b499b9-5cd9-4f5d-8ea4-c1c9f96efcfb' WHERE team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
UPDATE manager_tenures SET team_id = '51b499b9-5cd9-4f5d-8ea4-c1c9f96efcfb' WHERE team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
DELETE FROM team_elo_daily WHERE team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
DELETE FROM team_form_cache WHERE team_id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';
DELETE FROM teams WHERE id = '4a179a02-9e67-4e98-8618-b5f8292b29ec';

-- Châteauroux (France) -> Chateauroux (France)
UPDATE matches SET home_team_id = 'e52a3b52-c6b7-40f7-bd74-711f4e878544' WHERE home_team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
UPDATE matches SET away_team_id = 'e52a3b52-c6b7-40f7-bd74-711f4e878544' WHERE away_team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
UPDATE lineups SET team_id = 'e52a3b52-c6b7-40f7-bd74-711f4e878544' WHERE team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
UPDATE players SET team_id = 'e52a3b52-c6b7-40f7-bd74-711f4e878544' WHERE team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
UPDATE manager_tenures SET team_id = 'e52a3b52-c6b7-40f7-bd74-711f4e878544' WHERE team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
DELETE FROM team_elo_daily WHERE team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
DELETE FROM team_form_cache WHERE team_id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';
DELETE FROM teams WHERE id = '9cbeb2e9-6a4e-44e8-9290-c132b3e89b72';

-- Chicago Red Stars W (USA) -> Chicago Red Stars (W) (USA)
UPDATE matches SET home_team_id = '9a8768bb-3cf8-4e06-9fdf-fe8c9906680e' WHERE home_team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
UPDATE matches SET away_team_id = '9a8768bb-3cf8-4e06-9fdf-fe8c9906680e' WHERE away_team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
UPDATE lineups SET team_id = '9a8768bb-3cf8-4e06-9fdf-fe8c9906680e' WHERE team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
UPDATE players SET team_id = '9a8768bb-3cf8-4e06-9fdf-fe8c9906680e' WHERE team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
UPDATE manager_tenures SET team_id = '9a8768bb-3cf8-4e06-9fdf-fe8c9906680e' WHERE team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
DELETE FROM team_elo_daily WHERE team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
DELETE FROM team_form_cache WHERE team_id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';
DELETE FROM teams WHERE id = 'cc85f24d-bc9d-4c83-9abe-c5ada8e2c7ec';

-- Cobán Imperial (Guatemala) -> Coban Imperial (Guatemala)
UPDATE matches SET home_team_id = '2de5fe85-9535-4b5f-bc70-d171482da565' WHERE home_team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
UPDATE matches SET away_team_id = '2de5fe85-9535-4b5f-bc70-d171482da565' WHERE away_team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
UPDATE lineups SET team_id = '2de5fe85-9535-4b5f-bc70-d171482da565' WHERE team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
UPDATE players SET team_id = '2de5fe85-9535-4b5f-bc70-d171482da565' WHERE team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
UPDATE manager_tenures SET team_id = '2de5fe85-9535-4b5f-bc70-d171482da565' WHERE team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
DELETE FROM team_elo_daily WHERE team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
DELETE FROM team_form_cache WHERE team_id = 'dfab2bb2-7714-4273-8826-c091fba06608';
DELETE FROM teams WHERE id = 'dfab2bb2-7714-4273-8826-c091fba06608';

-- CODM Meknès (Morocco) -> CODM Meknes (Morocco)
UPDATE matches SET home_team_id = '14d372c1-326d-40dc-ba1b-185bed3382fa' WHERE home_team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
UPDATE matches SET away_team_id = '14d372c1-326d-40dc-ba1b-185bed3382fa' WHERE away_team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
UPDATE lineups SET team_id = '14d372c1-326d-40dc-ba1b-185bed3382fa' WHERE team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
UPDATE players SET team_id = '14d372c1-326d-40dc-ba1b-185bed3382fa' WHERE team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
UPDATE manager_tenures SET team_id = '14d372c1-326d-40dc-ba1b-185bed3382fa' WHERE team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
DELETE FROM team_elo_daily WHERE team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
DELETE FROM team_form_cache WHERE team_id = '03f8808f-887b-4277-aaa5-b6f8c572319b';
DELETE FROM teams WHERE id = '03f8808f-887b-4277-aaa5-b6f8c572319b';

-- Cruz Azul W (Mexico) -> Cruz Azul (W) (Mexico)
UPDATE matches SET home_team_id = '8100d6d2-46ee-4fd6-8fd3-a32c62be1cf5' WHERE home_team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
UPDATE matches SET away_team_id = '8100d6d2-46ee-4fd6-8fd3-a32c62be1cf5' WHERE away_team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
UPDATE lineups SET team_id = '8100d6d2-46ee-4fd6-8fd3-a32c62be1cf5' WHERE team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
UPDATE players SET team_id = '8100d6d2-46ee-4fd6-8fd3-a32c62be1cf5' WHERE team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
UPDATE manager_tenures SET team_id = '8100d6d2-46ee-4fd6-8fd3-a32c62be1cf5' WHERE team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
DELETE FROM team_elo_daily WHERE team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
DELETE FROM team_form_cache WHERE team_id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';
DELETE FROM teams WHERE id = '13ea95bf-d4ab-4804-b0fd-1b159fa0d4db';

-- El-Entag El-Harby (Egypt) -> El Entag EL Harby (Egypt)
UPDATE matches SET home_team_id = '411b1274-4c8f-4223-9a81-878124af21f2' WHERE home_team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
UPDATE matches SET away_team_id = '411b1274-4c8f-4223-9a81-878124af21f2' WHERE away_team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
UPDATE lineups SET team_id = '411b1274-4c8f-4223-9a81-878124af21f2' WHERE team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
UPDATE players SET team_id = '411b1274-4c8f-4223-9a81-878124af21f2' WHERE team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
UPDATE manager_tenures SET team_id = '411b1274-4c8f-4223-9a81-878124af21f2' WHERE team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
DELETE FROM team_elo_daily WHERE team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
DELETE FROM team_form_cache WHERE team_id = 'd4d2d953-e705-48fb-8047-331003543d9f';
DELETE FROM teams WHERE id = 'd4d2d953-e705-48fb-8047-331003543d9f';

-- FK Liepāja (Latvia) -> FK Liepaja (Latvia)
UPDATE matches SET home_team_id = '05fab950-a45f-47f6-87f0-6ea9b994b112' WHERE home_team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
UPDATE matches SET away_team_id = '05fab950-a45f-47f6-87f0-6ea9b994b112' WHERE away_team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
UPDATE lineups SET team_id = '05fab950-a45f-47f6-87f0-6ea9b994b112' WHERE team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
UPDATE players SET team_id = '05fab950-a45f-47f6-87f0-6ea9b994b112' WHERE team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
UPDATE manager_tenures SET team_id = '05fab950-a45f-47f6-87f0-6ea9b994b112' WHERE team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
DELETE FROM team_elo_daily WHERE team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
DELETE FROM team_form_cache WHERE team_id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';
DELETE FROM teams WHERE id = '62b09a9a-1ab1-4a39-8bbf-bce71d2c5d33';

-- Ghazl El-Mehalla (Egypt) -> Ghazl El Mehalla (Egypt)
UPDATE matches SET home_team_id = 'e2403571-130f-4d0e-864c-789fafd6f76c' WHERE home_team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
UPDATE matches SET away_team_id = 'e2403571-130f-4d0e-864c-789fafd6f76c' WHERE away_team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
UPDATE lineups SET team_id = 'e2403571-130f-4d0e-864c-789fafd6f76c' WHERE team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
UPDATE players SET team_id = 'e2403571-130f-4d0e-864c-789fafd6f76c' WHERE team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
UPDATE manager_tenures SET team_id = 'e2403571-130f-4d0e-864c-789fafd6f76c' WHERE team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
DELETE FROM team_elo_daily WHERE team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
DELETE FROM team_form_cache WHERE team_id = '18613ff1-95a8-4844-879a-23fc8730707a';
DELETE FROM teams WHERE id = '18613ff1-95a8-4844-879a-23fc8730707a';

-- Gremio W (Brazil) -> Gremio (W) (Brazil)
UPDATE matches SET home_team_id = '005d30b4-540a-473c-8a8b-6c4b5f504c51' WHERE home_team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
UPDATE matches SET away_team_id = '005d30b4-540a-473c-8a8b-6c4b5f504c51' WHERE away_team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
UPDATE lineups SET team_id = '005d30b4-540a-473c-8a8b-6c4b5f504c51' WHERE team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
UPDATE players SET team_id = '005d30b4-540a-473c-8a8b-6c4b5f504c51' WHERE team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
UPDATE manager_tenures SET team_id = '005d30b4-540a-473c-8a8b-6c4b5f504c51' WHERE team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
DELETE FROM team_elo_daily WHERE team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
DELETE FROM team_form_cache WHERE team_id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';
DELETE FROM teams WHERE id = '5b66bcb1-bc27-4409-beed-9bd49581f9d8';

-- Grobiņa (Latvia) -> Grobina (Latvia)
UPDATE matches SET home_team_id = '2fbc0200-3656-4d0e-ac3f-117815665604' WHERE home_team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
UPDATE matches SET away_team_id = '2fbc0200-3656-4d0e-ac3f-117815665604' WHERE away_team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
UPDATE lineups SET team_id = '2fbc0200-3656-4d0e-ac3f-117815665604' WHERE team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
UPDATE players SET team_id = '2fbc0200-3656-4d0e-ac3f-117815665604' WHERE team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
UPDATE manager_tenures SET team_id = '2fbc0200-3656-4d0e-ac3f-117815665604' WHERE team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
DELETE FROM team_elo_daily WHERE team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
DELETE FROM team_form_cache WHERE team_id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';
DELETE FROM teams WHERE id = '5ce88d81-ef94-433a-a101-25b8fbeeeacf';

-- Gżira United (Malta) -> Gzira United (Malta)
UPDATE matches SET home_team_id = 'd3ef7526-2cd7-43ed-a45f-3a244e217d3d' WHERE home_team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
UPDATE matches SET away_team_id = 'd3ef7526-2cd7-43ed-a45f-3a244e217d3d' WHERE away_team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
UPDATE lineups SET team_id = 'd3ef7526-2cd7-43ed-a45f-3a244e217d3d' WHERE team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
UPDATE players SET team_id = 'd3ef7526-2cd7-43ed-a45f-3a244e217d3d' WHERE team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
UPDATE manager_tenures SET team_id = 'd3ef7526-2cd7-43ed-a45f-3a244e217d3d' WHERE team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
DELETE FROM team_elo_daily WHERE team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
DELETE FROM team_form_cache WHERE team_id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';
DELETE FROM teams WHERE id = 'ea2ab78e-7112-4069-8009-cecbe11dc49e';

-- Haugesund W (Norway) -> Haugesund (W) (Norway)
UPDATE matches SET home_team_id = '84851fc2-e477-4df0-80e1-e9490444174a' WHERE home_team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
UPDATE matches SET away_team_id = '84851fc2-e477-4df0-80e1-e9490444174a' WHERE away_team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
UPDATE lineups SET team_id = '84851fc2-e477-4df0-80e1-e9490444174a' WHERE team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
UPDATE players SET team_id = '84851fc2-e477-4df0-80e1-e9490444174a' WHERE team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
UPDATE manager_tenures SET team_id = '84851fc2-e477-4df0-80e1-e9490444174a' WHERE team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
DELETE FROM team_elo_daily WHERE team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
DELETE FROM team_form_cache WHERE team_id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';
DELETE FROM teams WHERE id = 'a9c7d73c-963a-4096-a3be-32b880cdcc8d';

-- Huima/Urho (Finland) -> Huima / Urho (Finland)
UPDATE matches SET home_team_id = 'e928ad84-5fe6-4186-9dbb-6d5e64532fd2' WHERE home_team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
UPDATE matches SET away_team_id = 'e928ad84-5fe6-4186-9dbb-6d5e64532fd2' WHERE away_team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
UPDATE lineups SET team_id = 'e928ad84-5fe6-4186-9dbb-6d5e64532fd2' WHERE team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
UPDATE players SET team_id = 'e928ad84-5fe6-4186-9dbb-6d5e64532fd2' WHERE team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
UPDATE manager_tenures SET team_id = 'e928ad84-5fe6-4186-9dbb-6d5e64532fd2' WHERE team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
DELETE FROM team_elo_daily WHERE team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
DELETE FROM team_form_cache WHERE team_id = '03a23d35-033c-40a7-b361-89b14b07e26d';
DELETE FROM teams WHERE id = '03a23d35-033c-40a7-b361-89b14b07e26d';

-- Huracán (Argentina) -> Huracan (Argentina)
UPDATE matches SET home_team_id = '55b25744-5960-4570-8b6b-0280908e18d0' WHERE home_team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
UPDATE matches SET away_team_id = '55b25744-5960-4570-8b6b-0280908e18d0' WHERE away_team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
UPDATE lineups SET team_id = '55b25744-5960-4570-8b6b-0280908e18d0' WHERE team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
UPDATE players SET team_id = '55b25744-5960-4570-8b6b-0280908e18d0' WHERE team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
UPDATE manager_tenures SET team_id = '55b25744-5960-4570-8b6b-0280908e18d0' WHERE team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
DELETE FROM team_elo_daily WHERE team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
DELETE FROM team_form_cache WHERE team_id = '3428b10f-dbd1-449c-be5d-1859120d79c0';
DELETE FROM teams WHERE id = '3428b10f-dbd1-449c-be5d-1859120d79c0';

-- IFK Norrköping (Sweden) -> IFK Norrkoping (Sweden)
UPDATE matches SET home_team_id = '95370914-fb31-497f-b699-92646dad9776' WHERE home_team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
UPDATE matches SET away_team_id = '95370914-fb31-497f-b699-92646dad9776' WHERE away_team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
UPDATE lineups SET team_id = '95370914-fb31-497f-b699-92646dad9776' WHERE team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
UPDATE players SET team_id = '95370914-fb31-497f-b699-92646dad9776' WHERE team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
UPDATE manager_tenures SET team_id = '95370914-fb31-497f-b699-92646dad9776' WHERE team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
DELETE FROM team_elo_daily WHERE team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
DELETE FROM team_form_cache WHERE team_id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';
DELETE FROM teams WHERE id = 'a27ee08b-7e05-4cb8-97c5-edef72466825';

-- Júbilo Iwata (Japan) -> Jubilo Iwata (Japan)
UPDATE matches SET home_team_id = '92163fd1-0da9-4cff-bbd7-3cce88815021' WHERE home_team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
UPDATE matches SET away_team_id = '92163fd1-0da9-4cff-bbd7-3cce88815021' WHERE away_team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
UPDATE lineups SET team_id = '92163fd1-0da9-4cff-bbd7-3cce88815021' WHERE team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
UPDATE players SET team_id = '92163fd1-0da9-4cff-bbd7-3cce88815021' WHERE team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
UPDATE manager_tenures SET team_id = '92163fd1-0da9-4cff-bbd7-3cce88815021' WHERE team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
DELETE FROM team_elo_daily WHERE team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
DELETE FROM team_form_cache WHERE team_id = 'ce943863-e7ef-4565-8837-b481b7bd337a';
DELETE FROM teams WHERE id = 'ce943863-e7ef-4565-8837-b481b7bd337a';

-- Lausanne-Sport II (Switzerland) -> Lausanne Sport II (Switzerland)
UPDATE matches SET home_team_id = 'e72ef1e4-7445-4d79-bc99-692a164bb404' WHERE home_team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
UPDATE matches SET away_team_id = 'e72ef1e4-7445-4d79-bc99-692a164bb404' WHERE away_team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
UPDATE lineups SET team_id = 'e72ef1e4-7445-4d79-bc99-692a164bb404' WHERE team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
UPDATE players SET team_id = 'e72ef1e4-7445-4d79-bc99-692a164bb404' WHERE team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
UPDATE manager_tenures SET team_id = 'e72ef1e4-7445-4d79-bc99-692a164bb404' WHERE team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
DELETE FROM team_elo_daily WHERE team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
DELETE FROM team_form_cache WHERE team_id = '0791527c-eb37-4aab-92e3-32b2514f1f96';
DELETE FROM teams WHERE id = '0791527c-eb37-4aab-92e3-32b2514f1f96';

-- Leganés (Spain) -> Leganes (Spain)
UPDATE matches SET home_team_id = '9a4bdf63-36e2-459a-b94f-2fa79959c8e0' WHERE home_team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
UPDATE matches SET away_team_id = '9a4bdf63-36e2-459a-b94f-2fa79959c8e0' WHERE away_team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
UPDATE lineups SET team_id = '9a4bdf63-36e2-459a-b94f-2fa79959c8e0' WHERE team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
UPDATE players SET team_id = '9a4bdf63-36e2-459a-b94f-2fa79959c8e0' WHERE team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
UPDATE manager_tenures SET team_id = '9a4bdf63-36e2-459a-b94f-2fa79959c8e0' WHERE team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
DELETE FROM team_elo_daily WHERE team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
DELETE FROM team_form_cache WHERE team_id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';
DELETE FROM teams WHERE id = 'ec8d63e6-6729-4ccd-ad2e-a13284b61a2b';

-- Leixões U23 (Portugal) -> Leixoes U23 (Portugal)
UPDATE matches SET home_team_id = '2c99690c-dd2b-4a91-9e94-9b80755d1086' WHERE home_team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
UPDATE matches SET away_team_id = '2c99690c-dd2b-4a91-9e94-9b80755d1086' WHERE away_team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
UPDATE lineups SET team_id = '2c99690c-dd2b-4a91-9e94-9b80755d1086' WHERE team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
UPDATE players SET team_id = '2c99690c-dd2b-4a91-9e94-9b80755d1086' WHERE team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
UPDATE manager_tenures SET team_id = '2c99690c-dd2b-4a91-9e94-9b80755d1086' WHERE team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
DELETE FROM team_elo_daily WHERE team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
DELETE FROM team_form_cache WHERE team_id = 'f245a144-4b8f-448a-94fb-e1550feffd41';
DELETE FROM teams WHERE id = 'f245a144-4b8f-448a-94fb-e1550feffd41';

-- North Carolina Courage W (USA) -> North Carolina Courage (W) (USA)
UPDATE matches SET home_team_id = '6f90e359-af68-45b5-b217-2755415d5cc2' WHERE home_team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
UPDATE matches SET away_team_id = '6f90e359-af68-45b5-b217-2755415d5cc2' WHERE away_team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
UPDATE lineups SET team_id = '6f90e359-af68-45b5-b217-2755415d5cc2' WHERE team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
UPDATE players SET team_id = '6f90e359-af68-45b5-b217-2755415d5cc2' WHERE team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
UPDATE manager_tenures SET team_id = '6f90e359-af68-45b5-b217-2755415d5cc2' WHERE team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
DELETE FROM team_elo_daily WHERE team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
DELETE FROM team_form_cache WHERE team_id = 'b73662cd-091b-454f-8179-936417f89d1e';
DELETE FROM teams WHERE id = 'b73662cd-091b-454f-8179-936417f89d1e';

-- Olympique Dcheïra (Morocco) -> Olympique Dcheira (Morocco)
UPDATE matches SET home_team_id = '370c0449-f1f5-4927-85ba-a3378d492afc' WHERE home_team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
UPDATE matches SET away_team_id = '370c0449-f1f5-4927-85ba-a3378d492afc' WHERE away_team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
UPDATE lineups SET team_id = '370c0449-f1f5-4927-85ba-a3378d492afc' WHERE team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
UPDATE players SET team_id = '370c0449-f1f5-4927-85ba-a3378d492afc' WHERE team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
UPDATE manager_tenures SET team_id = '370c0449-f1f5-4927-85ba-a3378d492afc' WHERE team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
DELETE FROM team_elo_daily WHERE team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
DELETE FROM team_form_cache WHERE team_id = '547cdd32-9d6f-42f5-b035-d14c996c6257';
DELETE FROM teams WHERE id = '547cdd32-9d6f-42f5-b035-d14c996c6257';

-- Once Caldas W (Colombia) -> Once Caldas (W) (Colombia)
UPDATE matches SET home_team_id = '906bac9f-99d1-4899-bb7b-f916ae6088e6' WHERE home_team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
UPDATE matches SET away_team_id = '906bac9f-99d1-4899-bb7b-f916ae6088e6' WHERE away_team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
UPDATE lineups SET team_id = '906bac9f-99d1-4899-bb7b-f916ae6088e6' WHERE team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
UPDATE players SET team_id = '906bac9f-99d1-4899-bb7b-f916ae6088e6' WHERE team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
UPDATE manager_tenures SET team_id = '906bac9f-99d1-4899-bb7b-f916ae6088e6' WHERE team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
DELETE FROM team_elo_daily WHERE team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
DELETE FROM team_form_cache WHERE team_id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';
DELETE FROM teams WHERE id = 'c83ed1ca-ae8d-4ce0-aa37-41fd4da18623';

-- Operário-PR (Brazil) -> Operario-PR (Brazil)
UPDATE matches SET home_team_id = '1119f2d8-b08b-434d-95bb-12904b2bec51' WHERE home_team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
UPDATE matches SET away_team_id = '1119f2d8-b08b-434d-95bb-12904b2bec51' WHERE away_team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
UPDATE lineups SET team_id = '1119f2d8-b08b-434d-95bb-12904b2bec51' WHERE team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
UPDATE players SET team_id = '1119f2d8-b08b-434d-95bb-12904b2bec51' WHERE team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
UPDATE manager_tenures SET team_id = '1119f2d8-b08b-434d-95bb-12904b2bec51' WHERE team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
DELETE FROM team_elo_daily WHERE team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
DELETE FROM team_form_cache WHERE team_id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';
DELETE FROM teams WHERE id = 'bdf7974f-a600-4a59-ac37-b1c144b1c9da';

-- PEPO (Finland) -> Pepo (Finland)
UPDATE matches SET home_team_id = '831a8806-39cc-4820-b147-bcb95ceb6563' WHERE home_team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
UPDATE matches SET away_team_id = '831a8806-39cc-4820-b147-bcb95ceb6563' WHERE away_team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
UPDATE lineups SET team_id = '831a8806-39cc-4820-b147-bcb95ceb6563' WHERE team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
UPDATE players SET team_id = '831a8806-39cc-4820-b147-bcb95ceb6563' WHERE team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
UPDATE manager_tenures SET team_id = '831a8806-39cc-4820-b147-bcb95ceb6563' WHERE team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
DELETE FROM team_elo_daily WHERE team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
DELETE FROM team_form_cache WHERE team_id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';
DELETE FROM teams WHERE id = '4e234aba-8399-4d25-92f7-2b9a22b5b7a0';

-- Qarabağ (Azerbaijan) -> Qarabag (Azerbaijan)
UPDATE matches SET home_team_id = '814dbfb4-cdbc-4279-b887-93f446515c94' WHERE home_team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
UPDATE matches SET away_team_id = '814dbfb4-cdbc-4279-b887-93f446515c94' WHERE away_team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
UPDATE lineups SET team_id = '814dbfb4-cdbc-4279-b887-93f446515c94' WHERE team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
UPDATE players SET team_id = '814dbfb4-cdbc-4279-b887-93f446515c94' WHERE team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
UPDATE manager_tenures SET team_id = '814dbfb4-cdbc-4279-b887-93f446515c94' WHERE team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
DELETE FROM team_elo_daily WHERE team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
DELETE FROM team_form_cache WHERE team_id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';
DELETE FROM teams WHERE id = '611a461b-82bd-4ccc-9e2c-1f80a4859efa';

-- RoPS (Finland) -> Rops (Finland)
UPDATE matches SET home_team_id = '392d2125-5137-4e8d-80f1-859f9740476e' WHERE home_team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
UPDATE matches SET away_team_id = '392d2125-5137-4e8d-80f1-859f9740476e' WHERE away_team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
UPDATE lineups SET team_id = '392d2125-5137-4e8d-80f1-859f9740476e' WHERE team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
UPDATE players SET team_id = '392d2125-5137-4e8d-80f1-859f9740476e' WHERE team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
UPDATE manager_tenures SET team_id = '392d2125-5137-4e8d-80f1-859f9740476e' WHERE team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
DELETE FROM team_elo_daily WHERE team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
DELETE FROM team_form_cache WHERE team_id = 'd075e01e-1e01-4767-9cd5-873671baae9c';
DELETE FROM teams WHERE id = 'd075e01e-1e01-4767-9cd5-873671baae9c';

-- Sampaio Correa-RJ (Brazil) -> Sampaio Corrêa RJ (Brazil)
UPDATE matches SET home_team_id = 'c1cf2076-7051-4f0f-9e4a-10aa3558f4e0' WHERE home_team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
UPDATE matches SET away_team_id = 'c1cf2076-7051-4f0f-9e4a-10aa3558f4e0' WHERE away_team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
UPDATE lineups SET team_id = 'c1cf2076-7051-4f0f-9e4a-10aa3558f4e0' WHERE team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
UPDATE players SET team_id = 'c1cf2076-7051-4f0f-9e4a-10aa3558f4e0' WHERE team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
UPDATE manager_tenures SET team_id = 'c1cf2076-7051-4f0f-9e4a-10aa3558f4e0' WHERE team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
DELETE FROM team_elo_daily WHERE team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
DELETE FROM team_form_cache WHERE team_id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';
DELETE FROM teams WHERE id = '10b0d9d8-27c2-4221-8938-9e4565e91bc3';

-- Shimizu S-Pulse (Japan) -> Shimizu S-pulse (Japan)
UPDATE matches SET home_team_id = '7f3beff7-5e41-41fc-bb52-6c525bcd8779' WHERE home_team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
UPDATE matches SET away_team_id = '7f3beff7-5e41-41fc-bb52-6c525bcd8779' WHERE away_team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
UPDATE lineups SET team_id = '7f3beff7-5e41-41fc-bb52-6c525bcd8779' WHERE team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
UPDATE players SET team_id = '7f3beff7-5e41-41fc-bb52-6c525bcd8779' WHERE team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
UPDATE manager_tenures SET team_id = '7f3beff7-5e41-41fc-bb52-6c525bcd8779' WHERE team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
DELETE FROM team_elo_daily WHERE team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
DELETE FROM team_form_cache WHERE team_id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';
DELETE FROM teams WHERE id = '2f74cf4d-42a0-4431-9626-e11bca7f86fb';

-- Şimal (Azerbaijan) -> Simal (Azerbaijan)
UPDATE matches SET home_team_id = '89fc1588-0440-4dac-b3d8-b4722d80c3f3' WHERE home_team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
UPDATE matches SET away_team_id = '89fc1588-0440-4dac-b3d8-b4722d80c3f3' WHERE away_team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
UPDATE lineups SET team_id = '89fc1588-0440-4dac-b3d8-b4722d80c3f3' WHERE team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
UPDATE players SET team_id = '89fc1588-0440-4dac-b3d8-b4722d80c3f3' WHERE team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
UPDATE manager_tenures SET team_id = '89fc1588-0440-4dac-b3d8-b4722d80c3f3' WHERE team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
DELETE FROM team_elo_daily WHERE team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
DELETE FROM team_form_cache WHERE team_id = '93913e2a-ef88-4765-ae62-b8d66644419d';
DELETE FROM teams WHERE id = '93913e2a-ef88-4765-ae62-b8d66644419d';

-- Stabæk W (Norway) -> Stabæk (W) (Norway)
UPDATE matches SET home_team_id = '438f795f-8f77-458d-b5f9-b93c8a9abe54' WHERE home_team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
UPDATE matches SET away_team_id = '438f795f-8f77-458d-b5f9-b93c8a9abe54' WHERE away_team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
UPDATE lineups SET team_id = '438f795f-8f77-458d-b5f9-b93c8a9abe54' WHERE team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
UPDATE players SET team_id = '438f795f-8f77-458d-b5f9-b93c8a9abe54' WHERE team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
UPDATE manager_tenures SET team_id = '438f795f-8f77-458d-b5f9-b93c8a9abe54' WHERE team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
DELETE FROM team_elo_daily WHERE team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
DELETE FROM team_form_cache WHERE team_id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';
DELETE FROM teams WHERE id = '1452abbc-fdb1-4a02-a3af-cc9b81e32bc9';

-- Taian Tiankuang (China) -> Tai'an Tiankuang (China)
UPDATE matches SET home_team_id = '28a49a7f-5845-4db4-a32e-59f74923775a' WHERE home_team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
UPDATE matches SET away_team_id = '28a49a7f-5845-4db4-a32e-59f74923775a' WHERE away_team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
UPDATE lineups SET team_id = '28a49a7f-5845-4db4-a32e-59f74923775a' WHERE team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
UPDATE players SET team_id = '28a49a7f-5845-4db4-a32e-59f74923775a' WHERE team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
UPDATE manager_tenures SET team_id = '28a49a7f-5845-4db4-a32e-59f74923775a' WHERE team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
DELETE FROM team_elo_daily WHERE team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
DELETE FROM team_form_cache WHERE team_id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';
DELETE FROM teams WHERE id = '8f1ab39d-04bf-42d0-8c30-9dae2aa7c0df';

-- Vålerenga W (Norway) -> Vålerenga (W) (Norway)
UPDATE matches SET home_team_id = '659da3b3-5b94-41ef-97d7-491bb98b03ed' WHERE home_team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
UPDATE matches SET away_team_id = '659da3b3-5b94-41ef-97d7-491bb98b03ed' WHERE away_team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
UPDATE lineups SET team_id = '659da3b3-5b94-41ef-97d7-491bb98b03ed' WHERE team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
UPDATE players SET team_id = '659da3b3-5b94-41ef-97d7-491bb98b03ed' WHERE team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
UPDATE manager_tenures SET team_id = '659da3b3-5b94-41ef-97d7-491bb98b03ed' WHERE team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
DELETE FROM team_elo_daily WHERE team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
DELETE FROM team_form_cache WHERE team_id = '1d441636-620f-4fe4-be0c-e88a899893e1';
DELETE FROM teams WHERE id = '1d441636-620f-4fe4-be0c-e88a899893e1';

-- Vitkovice (Czech Republic) -> Vítkovice (Czech-Republic)
UPDATE matches SET home_team_id = '143cd76d-2794-4aa7-9794-fa5bfb79e753' WHERE home_team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
UPDATE matches SET away_team_id = '143cd76d-2794-4aa7-9794-fa5bfb79e753' WHERE away_team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
UPDATE lineups SET team_id = '143cd76d-2794-4aa7-9794-fa5bfb79e753' WHERE team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
UPDATE players SET team_id = '143cd76d-2794-4aa7-9794-fa5bfb79e753' WHERE team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
UPDATE manager_tenures SET team_id = '143cd76d-2794-4aa7-9794-fa5bfb79e753' WHERE team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
DELETE FROM team_elo_daily WHERE team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
DELETE FROM team_form_cache WHERE team_id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';
DELETE FROM teams WHERE id = '75a6d976-b80b-468f-8a9e-c171e68b74a3';

-- Vsetin (Czech Republic) -> Vsetín (Czech-Republic)
UPDATE matches SET home_team_id = '8b7a4c71-bfbc-4bdc-87a5-c551fa247d66' WHERE home_team_id = '75add024-3605-4366-8fcf-93fccd60b551';
UPDATE matches SET away_team_id = '8b7a4c71-bfbc-4bdc-87a5-c551fa247d66' WHERE away_team_id = '75add024-3605-4366-8fcf-93fccd60b551';
UPDATE lineups SET team_id = '8b7a4c71-bfbc-4bdc-87a5-c551fa247d66' WHERE team_id = '75add024-3605-4366-8fcf-93fccd60b551';
UPDATE players SET team_id = '8b7a4c71-bfbc-4bdc-87a5-c551fa247d66' WHERE team_id = '75add024-3605-4366-8fcf-93fccd60b551';
UPDATE manager_tenures SET team_id = '8b7a4c71-bfbc-4bdc-87a5-c551fa247d66' WHERE team_id = '75add024-3605-4366-8fcf-93fccd60b551';
DELETE FROM team_elo_daily WHERE team_id = '75add024-3605-4366-8fcf-93fccd60b551';
DELETE FROM team_form_cache WHERE team_id = '75add024-3605-4366-8fcf-93fccd60b551';
DELETE FROM teams WHERE id = '75add024-3605-4366-8fcf-93fccd60b551';

-- V-Varen Nagasaki (Japan) -> V-varen Nagasaki (Japan)
UPDATE matches SET home_team_id = 'ef78f25b-4e51-434e-b3ff-766fb18106a4' WHERE home_team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
UPDATE matches SET away_team_id = 'ef78f25b-4e51-434e-b3ff-766fb18106a4' WHERE away_team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
UPDATE lineups SET team_id = 'ef78f25b-4e51-434e-b3ff-766fb18106a4' WHERE team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
UPDATE players SET team_id = 'ef78f25b-4e51-434e-b3ff-766fb18106a4' WHERE team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
UPDATE manager_tenures SET team_id = 'ef78f25b-4e51-434e-b3ff-766fb18106a4' WHERE team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
DELETE FROM team_elo_daily WHERE team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
DELETE FROM team_form_cache WHERE team_id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';
DELETE FROM teams WHERE id = 'e15b2638-4d33-4968-98d2-7d89221cbac5';

-- Washington Spirit W (USA) -> Washington Spirit (W) (USA)
UPDATE matches SET home_team_id = '07bae305-7553-4119-9f5c-014198a6f6fc' WHERE home_team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
UPDATE matches SET away_team_id = '07bae305-7553-4119-9f5c-014198a6f6fc' WHERE away_team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
UPDATE lineups SET team_id = '07bae305-7553-4119-9f5c-014198a6f6fc' WHERE team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
UPDATE players SET team_id = '07bae305-7553-4119-9f5c-014198a6f6fc' WHERE team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
UPDATE manager_tenures SET team_id = '07bae305-7553-4119-9f5c-014198a6f6fc' WHERE team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
DELETE FROM team_elo_daily WHERE team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
DELETE FROM team_form_cache WHERE team_id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';
DELETE FROM teams WHERE id = 'c2ab971b-e55b-471e-b04f-cc9980f6d148';

-- Werder Bremen W (Germany) -> Werder Bremen (W) (Germany)
UPDATE matches SET home_team_id = '748409dd-1316-4733-aee6-1b23046bc2a8' WHERE home_team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
UPDATE matches SET away_team_id = '748409dd-1316-4733-aee6-1b23046bc2a8' WHERE away_team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
UPDATE lineups SET team_id = '748409dd-1316-4733-aee6-1b23046bc2a8' WHERE team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
UPDATE players SET team_id = '748409dd-1316-4733-aee6-1b23046bc2a8' WHERE team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
UPDATE manager_tenures SET team_id = '748409dd-1316-4733-aee6-1b23046bc2a8' WHERE team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
DELETE FROM team_elo_daily WHERE team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
DELETE FROM team_form_cache WHERE team_id = '21be594f-eccd-4654-818d-7b0424913c4d';
DELETE FROM teams WHERE id = '21be594f-eccd-4654-818d-7b0424913c4d';

-- Weston-super-Mare (England) -> Weston Super Mare (England)
UPDATE matches SET home_team_id = '58620523-eb02-4895-827c-e7414e7746cd' WHERE home_team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
UPDATE matches SET away_team_id = '58620523-eb02-4895-827c-e7414e7746cd' WHERE away_team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
UPDATE lineups SET team_id = '58620523-eb02-4895-827c-e7414e7746cd' WHERE team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
UPDATE players SET team_id = '58620523-eb02-4895-827c-e7414e7746cd' WHERE team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
UPDATE manager_tenures SET team_id = '58620523-eb02-4895-827c-e7414e7746cd' WHERE team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
DELETE FROM team_elo_daily WHERE team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
DELETE FROM team_form_cache WHERE team_id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';
DELETE FROM teams WHERE id = '7e49a429-e3a7-4970-9d5f-59827e9174f4';

-- Yokohama F. Marinos (Japan) -> Yokohama F Marinos (Japan)
UPDATE matches SET home_team_id = '54c2ac42-02f3-4823-ab07-ef622fcd6f9b' WHERE home_team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
UPDATE matches SET away_team_id = '54c2ac42-02f3-4823-ab07-ef622fcd6f9b' WHERE away_team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
UPDATE lineups SET team_id = '54c2ac42-02f3-4823-ab07-ef622fcd6f9b' WHERE team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
UPDATE players SET team_id = '54c2ac42-02f3-4823-ab07-ef622fcd6f9b' WHERE team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
UPDATE manager_tenures SET team_id = '54c2ac42-02f3-4823-ab07-ef622fcd6f9b' WHERE team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
DELETE FROM team_elo_daily WHERE team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
DELETE FROM team_form_cache WHERE team_id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';
DELETE FROM teams WHERE id = 'b2bc2012-b7c3-4bf3-9c6d-dd3df6981693';

-- ============================================================
-- Clean up duplicate matches created by the team merge.
-- After merging teams, some matches now have identical
-- (home_team_id, away_team_id, date) — keep the one with the
-- most data (has score, has odds_snapshots, or earlier created_at).
-- ============================================================
DELETE FROM matches m
WHERE EXISTS (
  SELECT 1 FROM matches m2
  WHERE m2.home_team_id = m.home_team_id
    AND m2.away_team_id = m.away_team_id
    AND DATE(m2.date) = DATE(m.date)
    AND m2.id != m.id
    AND (
      -- Keep the one with a real status (finished > live > scheduled)
      CASE m2.status WHEN 'finished' THEN 3 WHEN 'live' THEN 2 ELSE 1 END >
      CASE m.status  WHEN 'finished' THEN 3 WHEN 'live' THEN 2 ELSE 1 END
      OR (
        CASE m2.status WHEN 'finished' THEN 3 WHEN 'live' THEN 2 ELSE 1 END =
        CASE m.status  WHEN 'finished' THEN 3 WHEN 'live' THEN 2 ELSE 1 END
        AND m2.id < m.id  -- tiebreaker: keep lexicographically smaller UUID
      )
    )
);

COMMIT;
-- Total team merges: 55
