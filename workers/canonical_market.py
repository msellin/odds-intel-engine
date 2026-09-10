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
from enum import Enum

# ===========================================================================
# UNIFIED CONSTANTS — import these; do not hardcode the strings.
# ---------------------------------------------------------------------------
# The same logical bet was historically written 4 ways (1X2/1x2, O/U+'over 2.5'
# / over_under_25+'over'). These enums are the ONE spelling. They subclass str,
# so `Market.OVER_UNDER_25 == "over_under_25"` is True — existing string
# comparisons keep working, and new code references the constant instead of a
# literal. The smoke test MARKET-VOCAB-ENFORCED fails if (a) any value stored in
# the DB can't be normalised to these, or (b) a vocab-critical module stops
# importing this module (i.e. re-hardcodes the vocabulary). `normalize()` is the
# single mapper every reader/writer should route through.
# ===========================================================================


class Market(str, Enum):
    """Canonical STORAGE market strings (what `odds_snapshots.market` holds)."""
    ONE_X_TWO = "1x2"
    OVER_UNDER_05 = "over_under_05"
    OVER_UNDER_15 = "over_under_15"
    OVER_UNDER_25 = "over_under_25"
    OVER_UNDER_35 = "over_under_35"
    OVER_UNDER_45 = "over_under_45"
    BTTS = "btts"
    DOUBLE_CHANCE = "double_chance"
    ASIAN_HANDICAP = "asian_handicap"
    DRAW_NO_BET = "draw_no_bet"


class Selection(str, Enum):
    """Canonical STORAGE selection strings, per family."""
    HOME = "home"
    DRAW = "draw"
    AWAY = "away"
    OVER = "over"
    UNDER = "under"
    YES = "yes"
    NO = "no"
    DC_1X = "1x"
    DC_12 = "12"
    DC_X2 = "x2"


# --- axis 1: market family (floor key) -------------------------------------
# The canonical family keys used by the per-market floor dicts and grade bands.
MARKET_FAMILIES = ("1x2", "o/u", "asian_handicap", "draw_no_bet", "btts", "double_chance",
                   "corners_ou", "combo")


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


# --- the ONE normaliser: any observed (market, selection) -> canonical -----
_OU_MARKET_RE = re.compile(r"^over_under_(\d{2,3})$")
# Any parametric over/under family that encodes the line in the market name:
# corners_ou_95, corners_home_ou_75, corners_1h_ou_45, cards_ou_30, cards_away_ou_20…
# Captured family stem keeps the '_ou' suffix (e.g. 'corners_home_ou').
_GENERIC_OU_RE = re.compile(r"^([a-z0-9]+(?:_[a-z0-9]+)*_ou)_(\d{2,3})$")
# team goal totals: team_total_home_15 / team_total_1h_away_05 (side + line, over/under).
_TEAM_TOTAL_RE = re.compile(r"^team_total(?:_1h)?_(?:home|away)_(?P<line>\d{2,3})$")
_ONE_X_TWO_SEL = {"1": "home", "x": "draw", "2": "away",
                  "home": "home", "draw": "draw", "away": "away"}


def _digits_to_line(d: str) -> float:
    """'25'->2.5, '05'->0.5, '105'->10.5."""
    return int(d) / 10.0


def _line_to_ou_market(line: float) -> str:
    """2.5 -> 'over_under_25', 0.5 -> 'over_under_05', 10.5 -> 'over_under_105'."""
    return "over_under_" + str(int(round(line * 10))).zfill(2)


def normalize(market: str | None, selection: str | None) -> dict | None:
    """The single mapper. Turns ANY observed (market, selection) spelling into the
    canonical form, so `1X2`/`home`, `1x2`/`home`, `O/U`/`over 2.5` and
    `over_under_25`/`over` all collapse to one thing. Returns
    `{family, market, selection, line}` (all canonical, `market`/`selection` are the
    Market/Selection enum *values*), or **None** if the input is outside the known
    vocabulary — which is exactly what the enforcement test treats as a failure.
    """
    if not market:
        return None
    m = str(market).strip().lower()
    s = ("" if selection is None else str(selection)).strip().lower()

    # 1X2 — full match, and the first-half variant (1x2_1h) modelled by
    # bot_1h_1x2_paper_shadow_v1 (USE-COLLECTED-MARKETS). Same home/draw/away
    # selections; a distinct family so its floors/grades track separately.
    if m in ("1x2", "1x2_1h"):
        sel = _ONE_X_TWO_SEL.get(s)
        return {"family": m, "market": m, "selection": sel, "line": None} if sel else None

    # Over/Under total goals — the line may live in the MARKET (over_under_25) or in
    # the SELECTION ('over 2.5'), depending on which table wrote it.
    line = None
    side = None
    om = _OU_MARKET_RE.match(m)
    if om:
        line = _digits_to_line(om.group(1))
        if s in ("over", "under"):
            side = s
        else:
            ms = _OU_SEL_RE.match(selection or "")
            side = ms.group(1).lower() if ms else None
    elif m in ("o/u", "ou", "over_under"):
        ms = _OU_SEL_RE.match(selection or "")
        if ms:
            side = ms.group(1).lower()
            line = float(ms.group(2))
        elif s in ("over", "under"):
            side = s  # line not encoded anywhere → leave None
    if side in ("over", "under"):
        return {"family": "o/u",
                "market": _line_to_ou_market(line) if line is not None else None,
                "selection": side, "line": line}

    # parametric over/under families with the line in the market name
    # (corners_ou_95 -> 9.5; corners_home_ou_75; corners_1h_ou_45; cards_ou_30; …)
    cm = _GENERIC_OU_RE.match(m)
    if cm:
        return {"family": cm.group(1), "market": m, "selection": s,
                "line": _digits_to_line(cm.group(2))} if s in ("over", "under") else None

    # team goal totals — team_total_home_15 / team_total_away_25 (and the 1H
    # variant team_total_1h_home_05). side+line in the market, over/under selection.
    # Modelled by bot_team_total_paper_shadow_v1 (USE-COLLECTED-MARKETS).
    tt = _TEAM_TOTAL_RE.match(m)
    if tt:
        return {"family": "team_total", "market": m, "selection": s,
                "line": _digits_to_line(tt.group("line"))} if s in ("over", "under") else None

    if m == "btts":
        return {"family": "btts", "market": Market.BTTS.value, "selection": s,
                "line": None} if s in ("yes", "no") else None
    if m == "double_chance":
        return {"family": "double_chance", "market": Market.DOUBLE_CHANCE.value,
                "selection": s, "line": None} if s in ("1x", "12", "x2") else None
    if m == "asian_handicap":
        # selection carries the handicap line: 'home -1.5', 'away +0.5', or bare 'home'
        am = re.match(r"^(home|away)\s*([+-]?\d+(?:\.\d+)?)?$", s)
        if am:
            return {"family": "asian_handicap", "market": Market.ASIAN_HANDICAP.value,
                    "selection": am.group(1),
                    "line": float(am.group(2)) if am.group(2) not in (None, "") else None}
        return None
    if m == "draw_no_bet":
        return {"family": "draw_no_bet", "market": Market.DRAW_NO_BET.value,
                "selection": s, "line": None} if s in ("home", "away") else None
    if m == "combo":
        # accumulator/combo bets — no single market/selection; passthrough the label.
        return {"family": "combo", "market": "combo", "selection": s, "line": None} if s else None
    return None


def is_canonical(market: str | None, selection: str | None) -> bool:
    """True iff the input is recognised by `normalize`. The enforcement test uses
    this over the DB's distinct (market, selection) values."""
    return normalize(market, selection) is not None


# Families whose LINE lives in the selection (there is no line column), so the
# selection must be preserved verbatim on write rather than reduced to home/away.
_LINE_IN_SELECTION_FAMILIES = ("asian_handicap", "combo")


def canonicalize_for_storage(market: str | None, selection: str | None) -> tuple:
    """The write-side canonicaliser (MARKET-VOCAB-CANONICAL Phase 2). Returns the
    (market, selection) pair to STORE, in the one canonical spelling.

    Rules, chosen to be non-destructive:
      * Unrecognised vocab passes through UNCHANGED — we never corrupt a value we
        don't understand (e.g. an exotic AF market, a predictions key).
      * O/U with no resolvable line passes through unchanged (can't safely rewrite).
      * asian_handicap / combo keep their ORIGINAL selection (the line/label lives in
        the selection; there is no line column) — only the market is canonicalised
        (already 'asian_handicap'/'combo', so effectively a no-op).
      * Everything else (1x2, o/u with a line, btts, double_chance, draw_no_bet,
        parametric *_ou) is rewritten to the canonical market + selection.

    Idempotent: canonical input returns unchanged. Apply at every bet-table write.
    """
    c = normalize(market, selection)
    if c is None or not c.get("market"):
        return market, selection
    if c["family"] in _LINE_IN_SELECTION_FAMILIES:
        return c["market"], selection
    return c["market"], c["selection"]
