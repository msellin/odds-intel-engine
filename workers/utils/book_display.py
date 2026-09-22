"""Bookmaker names as a READER should see them.

WHY THIS EXISTS. Owner, 2026-09-22, reading a Telegram pick: *"we should name it
Unibet, not Unibet-Site...everywhere. its weird with that Site suffix."* Correct —
`-Site` is an internal distinction and means nothing to a customer.

⚠️ DISPLAY ONLY. NEVER RENAME THE KEY. Three distinct Unibet feeds exist in
`odds_snapshots` and two of them carry data:

    Unibet         1,228,791 rows   last 2026-09-12   API-Football feed, dead
    Unibet-Site      750,729 rows   last TODAY        our direct scrape, LIVE
    Unibet-Kambi     109,785 rows   last 2026-09-15   retired (KAMBI-FEED-DIVERGENCE)

Renaming `Unibet-Site` to `Unibet` in the data would merge our live feed into 1.2M
rows of a different, dead one — and `Unibet-Kambi` is the book whose stored prices
read up to +23.5% HIGHER than the site actually offered, which is why it was
excluded in the first place. Collapsing these identities destroys the ability to
tell them apart, and every CLV figure, every `recommended_bookmaker`, every join
in `real_bets`/`shadow_bets` keys on the raw string.

So: the ledger keeps `Unibet-Site`; only the rendered string changes. Same rule as
[[#069]] — a display name that is not the primary key.
"""
from __future__ import annotations

# Only add an entry where the internal key would actively confuse a reader.
# A book whose key is already its name needs nothing here.
_DISPLAY: dict[str, str] = {
    "Unibet-Site": "Unibet",
}


def display_book(name: str | None) -> str:
    """The name to show a customer. Unknown keys pass through unchanged."""
    if not name:
        return ""
    return _DISPLAY.get(name, name)
