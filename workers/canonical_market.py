"""Canonical market/selection vocabulary — the ONE source of truth for the
spelling mess documented in docs/BETTING_ARCHITECTURE.md §9.

COOLBET-PICK-TABLE-AUDIT Stage 1 (2026-09-09). The same logical bet is written
three different ways across the pipeline:

  * `simulated_bets` (generation)      market='o/u'          selection='over 2.5'
  * `shadow_bets` / `odds_snapshots`   market='over_under_25' selection='over'
  * floor dicts / grade helpers        keyed 'o/u'

This module centralises the two conversions that used to be duplicated inline in
`coolbet_placer._canon_market` and `coolbet_model_ou_shadow._convert`. It is
BEHAVIOUR-PRESERVING: it reproduces those two functions exactly (asserted by the
smoke test CANONICAL-MARKET-VOCAB), so wiring the callers to it changes nothing —
it only removes the duplication and gives every future writer/reader one helper.

Two distinct axes, do not confuse them:
  1. `market_family(market)` — the FLOOR-KEY family ('1x2','o/u','asian_handicap',
     'draw_no_bet', …). Collapses the whole over/under ladder to 'o/u'. This is
     what `_MIN_EDGE_BY_MARKET` / `_MIN_ODDS_BY_MARKET` / grade helpers key on.
  2. `ou_selection_to_storage(selection)` — the LINE ENCODING: turns the pipeline's
     `'over 2.5'` into the storage pair `('over_under_25','over')`. Only whole/half
     lines 2.5 and 3.5 are supported (the placeable O/U ladder); others → None.
"""
from __future__ import annotations

import re

# --- axis 1: market family (floor key) -------------------------------------
# The canonical family keys used by the per-market floor dicts and grade bands.
MARKET_FAMILIES = ("1x2", "o/u", "asian_handicap", "draw_no_bet", "btts", "double_chance")


def market_family(market: str | None) -> str | None:
    """Normalise a market string to its canonical floor-key family.

    Reproduces `coolbet_placer._canon_market` exactly: the over/under ladder
    (`over_under_25`, `over_under_35`, `ou`) collapses to `o/u`; everything else
    is lower-cased and passed through. `None`/'' pass through unchanged.
    """
    if not market:
        return market
    m = str(market).lower()
    if m.startswith("over_under") or m == "ou":
        return "o/u"
    return m


# --- axis 2: O/U line encoding (pipeline 'over 2.5' <-> storage 'over_under_25') ---
_OU_SEL_RE = re.compile(r"^\s*(over|under)\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
# The placeable O/U ladder. Extend here (one place) if a new line becomes placeable.
_OU_SUPPORTED_LINES = {"2.5": "over_under_25", "3.5": "over_under_35"}


def ou_selection_to_storage(selection: str | None) -> tuple[str, str] | None:
    """`'over 2.5'` -> `('over_under_25', 'over')`; unsupported/unparseable -> None.

    Reproduces `coolbet_model_ou_shadow._convert` exactly.
    """
    m = _OU_SEL_RE.match(selection or "")
    if not m:
        return None
    side = m.group(1).lower()
    market = _OU_SUPPORTED_LINES.get(m.group(2))
    if market is None:
        return None
    return market, side
