"""
League prestige weights for the smart email digest.

`leagues.tier` in the DB is league depth (1=top division, 2=second-tier, etc.) —
NOT global prestige. Premier League England and Premier League Bhutan both have
tier=1, so that column alone can't filter "leagues users care about".

This module classifies leagues into prestige buckets that drive:
  1. The digest qualification score (Σ edge × prestige_weight × kelly).
  2. The content filter (T1-T3 only — drop tier-4+ from the email body).

Buckets:
  T1 (1.0) — Big-5 European top divisions + UEFA club competitions + World Cup / Euros.
  T2 (0.7) — Other major European top divisions, top non-European leagues, Big-5 second tiers.
  T3 (0.4) — Other established top divisions (Switzerland, Austria, Greece, Russia, Ukraine, etc.).
  T4 (0.0) — Excluded entirely. Lower divisions, women's leagues, youth, low-coverage countries.

The weight=0 case is the filter: any bet/match returning 0.0 is dropped from
both the qualification score and the email content.
"""

from __future__ import annotations


# ── Big-5 European top divisions ───────────────────────────────────────────
T1_BIG5 = {
    ("Premier League", "England"),
    ("La Liga", "Spain"),
    ("Bundesliga", "Germany"),
    ("Serie A", "Italy"),
    ("Ligue 1", "France"),
}

# UEFA club + national-team showpieces
T1_UEFA_CLUB = {
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",
    "UEFA Conference League",
    "UEFA Super Cup",
    "FIFA Club World Cup",
    "World Cup",
    "World Cup - Qualification Europe",
    "World Cup - Qualification South America",
    "World Cup - Qualification Africa",
    "World Cup - Qualification Asia",
    "Euro Championship",
    "Euro Championship - Qualification",
    "UEFA Nations League",
    "Copa America",
    "Copa Libertadores",
    "Copa Sudamericana",
    "AFC Champions League",
    "FA Cup",
    "EFL Cup",
    "Copa del Rey",
    "Coppa Italia",
    "DFB Pokal",
    "Coupe de France",
}

# T2: major non-Big-5 European + top non-European leagues
T2_COUNTRIES = {
    "Portugal",
    "Netherlands",
    "Belgium",
    "Turkey",
    "Brazil",
    "Argentina",
    "Mexico",
    "USA",
    "Japan",
    "Korea-Republic",
    "South-Korea",
    "Saudi-Arabia",
    "Australia",
}

# Big-5 second tiers also count as T2
T2_BIG5_SECOND = {
    ("Championship", "England"),
    ("Bundesliga 2", "Germany"),
    ("2. Bundesliga", "Germany"),
    ("Serie B", "Italy"),
    ("La Liga 2", "Spain"),
    ("Segunda División", "Spain"),
    ("Ligue 2", "France"),
}

# T3: established but smaller top divisions
T3_COUNTRIES = {
    "Scotland",
    "Switzerland",
    "Austria",
    "Greece",
    "Denmark",
    "Norway",
    "Sweden",
    "Russia",
    "Ukraine",
    "Czech-Republic",
    "Poland",
    "Croatia",
    "Romania",
    "Serbia",
    "Israel",
    "Cyprus",
    "Finland",
    "China",
    "UAE",
    "Qatar",
    "Egypt",
    "South-Africa",
    "Morocco",
    "Tunisia",
    "Algeria",
    "Chile",
    "Colombia",
    "Uruguay",
    "Paraguay",
    "Peru",
    "Ecuador",
    "Venezuela",
    "Bolivia",
    "Canada",
    "Costa-Rica",
    "Honduras",
    "Guatemala",
    "Panama",
    "El-Salvador",
    "Indonesia",
    "Thailand",
    "Vietnam",
    "Malaysia",
    "India",
    "Iran",
    "Iraq",
    "Bulgaria",
    "Slovakia",
    "Slovenia",
    "Hungary",
    "Bosnia-and-Herzegovina",
}


def league_prestige_weight(name: str | None, country: str | None, tier: int | None) -> float:
    """
    Return the prestige weight for a league. 0.0 means "exclude from digest entirely".

    Treats name/country case-insensitively but expects API-Football's hyphenated
    multi-word countries (e.g. "South-Korea", "Czech-Republic"). Tier semantics:
    `leagues.tier` = league depth (1=top, 2=second, 3=third, etc.).
    """
    if not name:
        return 0.0
    nm = name.strip()
    co = (country or "").strip()

    # Hard exclusions — these never qualify regardless of country.
    nm_lower = nm.lower()
    if any(tag in nm_lower for tag in ("u17", "u18", "u19", "u20", "u21", "u23", "youth", "primavera", "reserves")):
        return 0.0
    if "women" in nm_lower or nm.endswith(" W") or nm.endswith(" (W)"):
        return 0.0

    if (nm, co) in T1_BIG5:
        return 1.0
    if nm in T1_UEFA_CLUB:
        return 1.0

    if (nm, co) in T2_BIG5_SECOND:
        return 0.7
    if (tier == 1 or tier is None) and co in T2_COUNTRIES:
        return 0.7

    if (tier == 1 or tier is None) and co in T3_COUNTRIES:
        return 0.4

    return 0.0


# ── SQL form ────────────────────────────────────────────────────────────────
# Matches league_prestige_weight() for use directly in queries. Returns the
# weight as a numeric column. Aliases as `prestige_weight`.

_T1_BIG5_SQL = ", ".join(f"('{n}', '{c}')" for n, c in T1_BIG5)
_T1_UEFA_SQL = ", ".join(f"'{n}'" for n in T1_UEFA_CLUB)
_T2_BIG5_SQL = ", ".join(f"('{n}', '{c}')" for n, c in T2_BIG5_SECOND)
_T2_COUNTRIES_SQL = ", ".join(f"'{c}'" for c in T2_COUNTRIES)
_T3_COUNTRIES_SQL = ", ".join(f"'{c}'" for c in T3_COUNTRIES)

_NAME_RE = "(u17|u18|u19|u20|u21|u23|youth|primavera|reserves|women)"

# Note: when this SQL is passed through psycopg2.execute() with parameters,
# any literal % must be escaped as %%. We avoid LIKE '%foo%' patterns entirely
# and use regex (~) instead so the snippet is portable to both .execute()
# and .mogrify() call sites.
PRESTIGE_WEIGHT_SQL = f"""
CASE
    WHEN lower(l.name) ~ '{_NAME_RE}' THEN 0.0
    WHEN l.name ~ '\\s\\(?W\\)?$' THEN 0.0
    WHEN (l.name, l.country) IN ({_T1_BIG5_SQL}) THEN 1.0
    WHEN l.name IN ({_T1_UEFA_SQL}) THEN 1.0
    WHEN (l.name, l.country) IN ({_T2_BIG5_SQL}) THEN 0.7
    WHEN (l.tier = 1 OR l.tier IS NULL) AND l.country IN ({_T2_COUNTRIES_SQL}) THEN 0.7
    WHEN (l.tier = 1 OR l.tier IS NULL) AND l.country IN ({_T3_COUNTRIES_SQL}) THEN 0.4
    ELSE 0.0
END
""".strip()
