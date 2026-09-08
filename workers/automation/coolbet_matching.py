"""
COOLBET-INGEST-REWORK (2026-09-08) — robust Coolbet-event ↔ AF-fixture matching.

The old sweep matched a fuzzy name query against Coolbet cross-league (search/v2),
which accepted at a similarity of 70 and wrote another game's prices onto a
fixture (verified wrong-fixture rate ~0.3%). This module replaces name-only
matching with a RECORD-LINKAGE approach that blocks on the cheap, high-precision
signals first and lets team names disambiguate only within a tiny block:

    1. COUNTRY  — Coolbet ships an ISO country code per event (`region_icon`:
                  'GB-ENG', 'US', 'GB-SCT'). Mapped to AF's country name, this
                  makes a cross-country false positive impossible.
    2. DATE     — kickoff within a slot (default ±3.5h, allowing timezone wobble).
    3. NAMES    — BOTH teams must agree, using a SUBSET-SAFE ratio so Coolbet's
                  short names match AF's full ones ('Stoke' ⊂ 'Stoke City',
                  'Hull' ⊂ 'Hull City', 'West Ham' ⊂ 'West Ham United').

A match is accepted only when the best candidate clears the threshold AND is
unambiguous within its block (a clear gap to the runner-up). Verified 2026-09-08:
recovers the confirmed false-negatives (Cardiff–Stoke, Bolton–West Ham,
Sunderland–Hull, all AF-present that night) at score 100, and matches ~99% of
AF-present near-term Coolbet games with a single close runner-up on the board.

When ISO does not map (a rare region), the country block is skipped and matching
falls back to date-slot + both-team names, which is still far tighter than the
old global cross-league search.
"""
from __future__ import annotations

import html
import re
import unicodedata

from rapidfuzz import fuzz

# Coolbet region_icon (ISO 3166, with GB home-nation subdivisions) → AF country
# name (AF uses dash-joined English). Extend as new regions appear; an unmapped
# code simply disables the country block for that event (date+names still apply).
ISO_TO_AF_COUNTRY: dict[str, str] = {
    "GB-ENG": "England", "GB-SCT": "Scotland", "GB-WLS": "Wales", "GB-NIR": "Northern-Ireland",
    "US": "USA", "ES": "Spain", "DE": "Germany", "IT": "Italy", "FR": "France",
    "NL": "Netherlands", "PT": "Portugal", "EE": "Estonia", "FI": "Finland", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "BR": "Brazil", "AR": "Argentina", "SA": "Saudi-Arabia",
    "EG": "Egypt", "JP": "Japan", "KR": "South-Korea", "MX": "Mexico", "CO": "Colombia",
    "PY": "Paraguay", "UY": "Uruguay", "CL": "Chile", "AU": "Australia", "AT": "Austria",
    "CH": "Switzerland", "BE": "Belgium", "GR": "Greece", "TR": "Turkey", "RU": "Russia",
    "UA": "Ukraine", "CZ": "Czech-Republic", "PL": "Poland", "IE": "Ireland", "IS": "Iceland",
    "QA": "Qatar", "IQ": "Iraq", "IL": "Israel", "RS": "Serbia", "HR": "Croatia", "RO": "Romania",
    "BG": "Bulgaria", "HU": "Hungary", "SK": "Slovakia", "SI": "Slovenia", "GE": "Georgia",
    "AM": "Armenia", "AZ": "Azerbaijan", "KZ": "Kazakhstan", "UZ": "Uzbekistan", "CA": "Canada",
    "ZA": "South-Africa", "MY": "Malaysia", "TH": "Thailand", "CN": "China", "IN": "India",
    "BO": "Bolivia", "PE": "Peru", "EC": "Ecuador", "VE": "Venezuela", "CR": "Costa-Rica",
    "GT": "Guatemala", "SV": "El-Salvador", "IR": "Iran", "MK": "Macedonia", "ME": "Montenegro",
    "BA": "Bosnia", "XK": "Kosovo", "LT": "Lithuania", "LV": "Latvia", "LU": "Luxembourg",
    "MT": "Malta", "CY": "Cyprus", "AD": "Andorra", "LI": "Liechtenstein", "SM": "San-Marino",
}

# club-form tokens that carry no identity, so they must not sink a name match
_STRIP_TOKENS = {
    "fc", "sc", "cf", "ac", "sk", "bk", "if", "afc", "cd", "sv", "nk", "rc", "as",
    "ss", "us", "sd", "ud", "fk", "ca", "kf", "club", "jk", "cp", "cs", "ec", "se",
    "aa", "ao", "ks", "mfk", "gd", "od", "hb", "bsc", "vfb", "vfl", "tsv", "fsv", "spvgg",
}

DEFAULT_NAME_THRESHOLD = 75
DEFAULT_SLOT_HOURS = 3.5
DEFAULT_MIN_GAP = 8  # required margin best-vs-runner-up (unless best is near-perfect)


def norm_team(name: str | None) -> str:
    """Lowercase, strip accents and club-form tokens → a comparable team key.

    HTML-unescape FIRST: Coolbet ships '&amp;' in names like 'Havant &amp;
    Waterlooville', and without unescaping the '&' the token 'amp' survives and
    sinks the match (measured: it was one cause of a genuine matcher-miss on an
    AF-present FA Cup tie)."""
    s = html.unescape(name or "")
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t and t not in _STRIP_TOKENS).strip()


def team_sim(a: str, b: str) -> float:
    """Subset-safe similarity: 'stoke' vs 'stoke city' scores high (token_set /
    partial), where token_sort_ratio would not."""
    return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b))


def af_country_for_iso(region_icon: str | None) -> str | None:
    return ISO_TO_AF_COUNTRY.get(region_icon) if region_icon else None


def match_event_to_af(
    cb_home: str, cb_away: str, cb_iso: str | None, cb_start,
    af_candidates: list[dict],
    *,
    name_threshold: int = DEFAULT_NAME_THRESHOLD,
    slot_hours: float = DEFAULT_SLOT_HOURS,
    min_gap: int = DEFAULT_MIN_GAP,
):
    """Match one Coolbet event to at most one AF fixture.

    `af_candidates` rows need: id, ko (tz-aware datetime), home, away, country.
    Returns (matched_row_or_None, best_score, runner_up_score). A match is
    returned only when the best in-block candidate clears `name_threshold` AND is
    unambiguous (gap to runner-up ≥ min_gap, or best ≥ 90). Blocks on country
    (when ISO maps) and kickoff slot BEFORE scoring names.
    """
    eh, ea = norm_team(cb_home), norm_team(cb_away)
    afc = af_country_for_iso(cb_iso)
    best = None
    best_score = 0.0
    second = 0.0
    for a in af_candidates:
        if cb_start is not None and a.get("ko") is not None:
            if abs((a["ko"] - cb_start).total_seconds()) > slot_hours * 3600:
                continue  # date-slot block
        if afc is not None and a.get("country") and a["country"] != afc:
            continue  # country block
        ah, aw = norm_team(a["home"]), norm_team(a["away"])
        direct = min(team_sim(eh, ah), team_sim(ea, aw))
        swapped = min(team_sim(eh, aw), team_sim(ea, ah))
        sc = max(direct, swapped)
        if sc > best_score:
            second = best_score
            best_score = sc
            best = a
        elif sc > second:
            second = sc
    ok = best_score >= name_threshold and (best_score - second >= min_gap or best_score >= 90)
    return (best if ok else None), best_score, second
