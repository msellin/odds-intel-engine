"""
OddsIntel — Team Name Mapping
Maps team names between different sources (Kambi, football-data.co.uk, Sofascore).

This is one of the most important files — wrong mapping = missed bets.
"""

# Kambi name → football-data.co.uk name
# Add mappings as we discover mismatches
KAMBI_TO_FOOTBALL_DATA = {
    # England
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Tottenham Hotspur": "Tottenham",
    "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest": "Nott'm Forest",
    "West Ham United": "West Ham",
    "Sheffield United": "Sheffield United",
    "Brighton & Hove Albion": "Brighton",
    "Leicester City": "Leicester",
    "AFC Bournemouth": "Bournemouth",
    "Leeds United": "Leeds",
    "Ipswich Town": "Ipswich",
    "Luton Town": "Luton",
    "Sheffield Wednesday": "Sheffield Weds",
    "Queens Park Rangers": "QPR",
    "West Bromwich Albion": "West Brom",
    "Birmingham City": "Birmingham",
    "Blackburn Rovers": "Blackburn",
    "Bolton Wanderers": "Bolton",
    "Stoke City": "Stoke",
    "Swansea City": "Swansea",
    "Hull City": "Hull",
    "Cardiff City": "Cardiff",
    "Wigan Athletic": "Wigan",
    "Huddersfield Town": "Huddersfield",
    "Preston North End": "Preston",
    "Coventry City": "Coventry",
    "Plymouth Argyle": "Plymouth",
    "Bristol City": "Bristol City",
    "Millwall FC": "Millwall",
    "Norwich City": "Norwich",
    "Sunderland AFC": "Sunderland",
    "Burnley FC": "Burnley",
    "Derby County": "Derby",
    "Oxford United": "Oxford",
    "Portsmouth FC": "Portsmouth",
    "Blackpool FC": "Blackpool",

    # Spain
    "Espanyol": "Espanol",
    "UD Las Palmas": "Las Palmas",
    "Rayo Vallecano": "Vallecano",
    "Athletic Bilbao": "Ath Bilbao",
    "Atletico Madrid": "Ath Madrid",
    "Real Betis": "Betis",
    "Celta de Vigo": "Celta",
    "Deportivo Alaves": "Alaves",
    "Real Sociedad": "Sociedad",
    "Real Oviedo": "Oviedo",
    "Cadiz CF": "Cadiz",

    # Italy
    "AC Milan": "Milan",
    "Inter Milan": "Inter",
    "AS Roma": "Roma",
    "SSC Napoli": "Napoli",
    "SS Lazio": "Lazio",
    "Hellas Verona": "Verona",
    "US Sassuolo": "Sassuolo",
    "Genoa CFC": "Genoa",
    "Torino FC": "Torino",
    "ACF Fiorentina": "Fiorentina",
    "US Lecce": "Lecce",
    "Empoli FC": "Empoli",
    "Frosinone Calcio": "Frosinone",
    "Salernitana": "Salernitana",

    # Germany
    "Bayern Munich": "Bayern Munich",
    "Borussia Dortmund": "Dortmund",
    "Bayer Leverkusen": "Leverkusen",
    "RB Leipzig": "RB Leipzig",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "Borussia Mönchengladbach": "M'gladbach",
    "VfB Stuttgart": "Stuttgart",
    "VfL Wolfsburg": "Wolfsburg",
    "SC Freiburg": "Freiburg",
    "TSG 1899 Hoffenheim": "Hoffenheim",
    "1. FC Union Berlin": "Union Berlin",
    "1. FC Köln": "FC Koln",
    "FC Augsburg": "Augsburg",
    "SV Werder Bremen": "Werder Bremen",
    "1. FC Heidenheim": "Heidenheim",
    "SV Darmstadt 98": "Darmstadt",

    # France
    "Paris Saint-Germain": "Paris SG",
    "Olympique Marseille": "Marseille",
    "Olympique Lyon": "Lyon",
    "AS Monaco": "Monaco",
    "LOSC Lille": "Lille",
    "OGC Nice": "Nice",
    "RC Lens": "Lens",
    "Stade Rennais": "Rennes",
    "RC Strasbourg Alsace": "Strasbourg",
    "Stade de Reims": "Reims",
    "FC Nantes": "Nantes",
    "Toulouse FC": "Toulouse",
    "Montpellier HSC": "Montpellier",
    "FC Lorient": "Lorient",
    "Clermont Foot 63": "Clermont",
    "Le Havre AC": "Le Havre",
    "FC Metz": "Metz",
    "Stade Brestois 29": "Brest",

    # Netherlands
    "Ajax Amsterdam": "Ajax",
    "PSV Eindhoven": "PSV",
    "Feyenoord Rotterdam": "Feyenoord",
    "AZ Alkmaar": "AZ",
    "FC Twente": "Twente",
    "FC Utrecht": "Utrecht",
    "SC Heerenveen": "Heerenveen",
    "Vitesse Arnhem": "Vitesse",
    "Sparta Rotterdam": "Sparta Rotterdam",

    # Turkey
    "Galatasaray SK": "Galatasaray",
    "Fenerbahçe SK": "Fenerbahce",
    "Beşiktaş": "Besiktas",
    "Trabzonspor": "Trabzonspor",
    "Konyaspor": "Konyaspor",
    "Alanyaspor": "Alanyaspor",
    "Fatih Karagümrük": "Karagumruk",
    "Kasımpaşa SK": "Kasimpasa",
    "Antalyaspor": "Antalyaspor",
    "Sivasspor": "Sivasspor",
    "Samsunspor": "Samsunspor",

    # Portugal
    "Sporting CP": "Sp Lisbon",
    "SL Benfica": "Benfica",
    "FC Porto": "Porto",
    "SC Braga": "Braga",
    "Gil Vicente FC": "Gil Vicente",
    "Casa Pia AC": "Casa Pia",

    # Scotland
    "Celtic FC": "Celtic",
    "Rangers FC": "Rangers",
    "Aberdeen FC": "Aberdeen",
    "Heart of Midlothian": "Hearts",
    "Hibernian FC": "Hibernian",

    # Belgium
    "Club Brugge KV": "Club Brugge",
    "RSC Anderlecht": "Anderlecht",
    "KRC Genk": "Genk",
    "Royal Antwerp FC": "Antwerp",
    "Standard Liège": "Standard",

    # Greece
    "Olympiacos Piraeus": "Olympiakos",
    "Panathinaikos FC": "Panathinaikos",
    "PAOK Thessaloniki": "PAOK",
    "AEK Athens": "AEK",
}

# Reverse map
FOOTBALL_DATA_TO_KAMBI = {v: k for k, v in KAMBI_TO_FOOTBALL_DATA.items()}


def normalize_team_name(name: str, source: str = "kambi") -> str:
    """
    Normalize a team name to match our historical data (football-data.co.uk format).
    """
    if source == "kambi":
        return KAMBI_TO_FOOTBALL_DATA.get(name, name)
    return name


def fuzzy_match_team(name: str, known_teams: set, threshold: int = 5) -> str | None:
    """
    Try to fuzzy-match a team name against known teams.
    Uses simple prefix matching.
    """
    name_lower = name.lower()

    # Exact match
    if name in known_teams:
        return name

    # Check mapping
    mapped = KAMBI_TO_FOOTBALL_DATA.get(name)
    if mapped and mapped in known_teams:
        return mapped

    # Prefix match (first N chars)
    for n in [10, 8, 6, 5]:
        prefix = name_lower[:n]
        matches = [t for t in known_teams if t.lower().startswith(prefix)]
        if len(matches) == 1:
            return matches[0]

    # Contains match
    for t in known_teams:
        if name_lower[:5] in t.lower() or t.lower()[:5] in name_lower:
            return t

    return None
