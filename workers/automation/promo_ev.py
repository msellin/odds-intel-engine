"""PROMO-EV (2026-09-15, OWN Phase 2) — expected value of a bookmaker promotion
under its stated terms, against a Shin-de-vigged consensus fair price.

Nothing here predicts football. It prices a subsidy. The fair probability is
the average of the Shin-de-vigged probabilities of every reference book quoting
the full market on that fixture (Pinnacle plus the tight AF books plus our own
three, minus the book running the promotion, which is never part of the
consensus it is measured against). That is the one quantity this codebase
computes well; it is also what makes the terms matter:

  * ODDS BOOST on the PROFIT part (the usual retail form): a fair 2.00 boosted
    +50% pays 1 + 1.0×1.5 = 2.50 → EV = p×(2.5−1) − (1−p) = +25% at p=0.5.
    A boost on the FULL decimal odds ("2.00 → 3.00") is +50%.
  * FREE BET, stake NOT returned (SNR — the default at every EMTA book we have
    seen): a €10 token on fair odds o returns €10×(o−1) with probability 1/o,
    so the expected cash is €10×(o−1)/o = €10×(1 − 1/o) → 50% of face at 2.0,
    75% at 4.0, approaching face value as odds lengthen. The classic reason to
    put free bets on longshots — and the reason min-odds terms exist.
  * ACCA INSURANCE (refund if exactly one leg loses, usually as a free bet):
    EV = Σ over legs of P(all others win and this one loses) × refund_value
    − vig on the acca itself. Positive only when the legs are near-fair and the
    refund is counted at its FREE-BET value (~70% of face), not face.
  * DEPOSIT BONUS with rollover: each turnover cycle at a ~7% margin costs
    ~7% × rollover × bonus. A 100% bonus with 3× rollover on the bonus is
    ~−21% of the bonus before it can be withdrawn — usually negative. Priced
    here so the ledger says so before anyone deposits.

Every function takes the terms explicitly. `min_odds` and `max_stake_eur` are
enforced: an offer whose min_odds pushes the bet onto the long side is priced
at that longer, worse-vigged price, not at the price you wanted.

No exchange is EMTA-licensed, so nothing can be hedged: every EV here is an
expectation over a variance-bearing bet. The ledger records that variance next
to the EV so the monthly review compares like with like.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

REFERENCE_BOOKS = ("Pinnacle", "Marathonbet", "1xBet", "10Bet", "Bet365", "Betano",
                   "William Hill", "BetVictor", "Coolbet", "Epicbet", "Unibet-Site")

_MARKET_SIDES = {
    "1x2": ("home", "draw", "away"),
    "btts": ("yes", "no"),
}


def market_sides(market: str) -> tuple[str, ...] | None:
    m = (market or "").lower()
    if m in _MARKET_SIDES:
        return _MARKET_SIDES[m]
    if m.startswith("over_under") or m.startswith("team_total") or m.startswith("corners_ou"):
        return ("over", "under")
    return None


@dataclass
class FairPrice:
    prob: float
    books_used: int
    per_book: dict = field(default_factory=dict)

    @property
    def fair_odds(self) -> float:
        return 1.0 / self.prob if self.prob > 0 else float("inf")


def consensus_fair_prob(match_id: str, market: str, selection: str, *,
                        exclude_book: str | None = None,
                        books: tuple[str, ...] = REFERENCE_BOOKS,
                        max_age_hours: float = 6.0, min_books: int = 4) -> FairPrice | None:
    """Shin-de-vig each reference book's LATEST pre-kickoff full market on this
    fixture and average the probabilities of `selection`. Books older than
    `max_age_hours` (relative to the newest quote) are dropped — a 12-hour-old
    quote is not an opinion about the current market. Returns None if fewer
    than `min_books` (default 4, per the plan) have a complete, fresh market."""
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig
    sides = market_sides(market)
    if not sides:
        return None
    sel = (selection or "").lower()
    if sel not in sides:
        return None
    rows = execute_query(
        """SELECT DISTINCT ON (o.bookmaker, o.selection)
                  o.bookmaker, o.selection, o.odds::float AS odds, o.timestamp
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.match_id = %s AND o.market = %s AND o.bookmaker = ANY(%s)
              AND o.timestamp <= m.date AND COALESCE(o.is_live, false) = false
            ORDER BY o.bookmaker, o.selection, o.timestamp DESC""",
        (match_id, market, list(b for b in books if b != exclude_book)),
    ) or []
    by_book: dict[str, dict] = {}
    newest = None
    for r in rows:
        by_book.setdefault(r["bookmaker"], {})[r["selection"].lower()] = (float(r["odds"]), r["timestamp"])
        if newest is None or r["timestamp"] > newest:
            newest = r["timestamp"]
    probs: dict[str, float] = {}
    for book, q in by_book.items():
        if not all(s in q for s in sides):
            continue
        ts = min(q[s][1] for s in sides)
        if newest is not None and (newest - ts).total_seconds() > max_age_hours * 3600:
            continue
        p = devig([q[s][0] for s in sides])
        if not p:
            continue
        probs[book] = p[sides.index(sel)]
    if len(probs) < min_books:
        return None
    return FairPrice(prob=sum(probs.values()) / len(probs), books_used=len(probs), per_book=probs)


# ── the formulas ─────────────────────────────────────────────────────────────
def ev_straight(fair_p: float, odds: float, stake: float) -> float:
    """EV of a plain bet at decimal `odds` with fair win probability `fair_p`."""
    return stake * (fair_p * (odds - 1.0) - (1.0 - fair_p))


def ev_odds_boost(fair_p: float, odds: float, stake: float, boost_pct: float, *,
                  applies_to: str = "profit", min_odds: float | None = None,
                  max_stake_eur: float | None = None) -> tuple[float, str]:
    """EV of an odds boost. `boost_pct` = 50 means +50%. `applies_to='profit'`
    boosts the winnings (1 + (o−1)×1.5); 'odds' boosts the full decimal price."""
    if min_odds is not None and odds < min_odds:
        return 0.0, f"refused: odds {odds:.2f} below promo min_odds {min_odds:.2f}"
    s = min(stake, max_stake_eur) if max_stake_eur else stake
    f = 1.0 + boost_pct / 100.0
    boosted = (1.0 + (odds - 1.0) * f) if applies_to == "profit" else odds * f
    ev = ev_straight(fair_p, boosted, s)
    return ev, (f"boost {boost_pct:+.0f}% on {applies_to}: {odds:.2f} -> {boosted:.2f}, "
                f"stake {s:.2f}, fair p {fair_p:.3f} (fair {1/fair_p:.2f}), EV {ev:+.2f}")


def ev_free_bet(fair_p: float, odds: float, face_eur: float, *, stake_returned: bool = False,
                min_odds: float | None = None) -> tuple[float, str]:
    """Expected CASH from a free-bet token of `face_eur` placed at `odds`.
    SNR (stake not returned): win pays face×(o−1). Stake-returned: face×o."""
    if min_odds is not None and odds < min_odds:
        return 0.0, f"refused: odds {odds:.2f} below promo min_odds {min_odds:.2f}"
    payout = face_eur * (odds if stake_returned else odds - 1.0)
    ev = fair_p * payout
    pct = ev / face_eur if face_eur else 0.0
    return ev, (f"free bet {face_eur:.2f} @ {odds:.2f} ({'SR' if stake_returned else 'SNR'}), "
                f"fair p {fair_p:.3f}: expected cash {ev:.2f} = {pct:.0%} of face")


def free_bet_value_ratio(fair_p: float, odds: float) -> float:
    """Fraction of a SNR token's face value it converts to in expectation at a
    FAIR price: (o−1)/o = 1 − p. Rises with odds — the case for longshots."""
    return fair_p * (odds - 1.0)


def ev_acca_insurance(leg_fair_p: list[float], leg_odds: list[float], stake: float, *,
                      refund_eur: float, refund_cash: bool = False,
                      refund_free_bet_value: float = 0.70, min_legs: int | None = None,
                      max_stake_eur: float | None = None) -> tuple[float, str]:
    """EV of an accumulator with 'money back if exactly one leg loses'.
    The refund is worth `refund_free_bet_value` × face unless paid in cash."""
    n = len(leg_fair_p)
    if n == 0 or n != len(leg_odds):
        return 0.0, "refused: legs mismatch"
    if min_legs is not None and n < min_legs:
        return 0.0, f"refused: {n} legs below promo min_legs {min_legs}"
    s = min(stake, max_stake_eur) if max_stake_eur else stake
    acca_odds = math.prod(leg_odds)
    p_all = math.prod(leg_fair_p)
    # exactly one leg loses
    p_one_loses = sum((1.0 - leg_fair_p[i]) * math.prod(leg_fair_p[j] for j in range(n) if j != i)
                      for i in range(n))
    refund_value = min(refund_eur, s) * (1.0 if refund_cash else refund_free_bet_value)
    ev = p_all * s * (acca_odds - 1.0) - (1.0 - p_all) * s + p_one_loses * refund_value
    return ev, (f"acca {n} legs @ {acca_odds:.2f}, P(all) {p_all:.3f}, P(exactly one loses) "
                f"{p_one_loses:.3f}, refund value {refund_value:.2f}, stake {s:.2f}, EV {ev:+.2f}")


def ev_deposit_bonus(bonus_eur: float, rollover_x: float, margin: float = 0.07,
                     deposit_eur: float | None = None, rollover_base: str = "deposit_plus_bonus",
                     min_odds_margin: float | None = None) -> tuple[float, str]:
    """EV of a deposit bonus under REAL terms. The verifier (2026-09-15) caught
    the first version pricing 100%/3x at +79 while its own docstring said such
    offers are usually negative: it rolled over the BONUS only and ignored that
    Estonian books roll over deposit+bonus at 5–10x, often at min odds where
    the effective margin is higher. Without the deposit base the number is not
    computable — REFUSE rather than print a flattering guess."""
    if deposit_eur is None or rollover_x is None:
        return 0.0, ("refused: deposit_bonus needs deposit_eur and rollover_x from the T&Cs "
                     "(rollover usually applies to deposit+bonus) — not modelled without them")
    base = (deposit_eur + bonus_eur) if rollover_base == "deposit_plus_bonus" else bonus_eur
    m = min_odds_margin if min_odds_margin is not None else margin
    cost = base * rollover_x * m
    ev = bonus_eur - cost
    return ev, (f"bonus {bonus_eur:.2f} on deposit {deposit_eur:.2f}; rollover {rollover_x:.0f}x on "
                f"{rollover_base} = {base * rollover_x:.0f} turnover at {m:.1%} margin costs {cost:.2f}: EV {ev:+.2f}")


def ev_for_terms(terms: dict, *, fair_p: float, odds: float, stake: float,
                 leg_fair_p: list[float] | None = None, leg_odds: list[float] | None = None) -> tuple[float, str]:
    """Dispatch on a `promo_terms` row (dict). Returns (ev_eur, explanation)."""
    t = terms.get("promo_type")
    if t == "odds_boost":
        return ev_odds_boost(fair_p, odds, stake, float(terms.get("boost_pct") or 0),
                             applies_to=terms.get("boost_applies_to") or "profit",
                             min_odds=_f(terms.get("min_odds")), max_stake_eur=_f(terms.get("max_stake_eur")))
    if t == "free_bet":
        return ev_free_bet(fair_p, odds, float(terms.get("face_value_eur") or stake),
                           stake_returned=bool(terms.get("stake_returned")), min_odds=_f(terms.get("min_odds")))
    if t == "acca_insurance":
        return ev_acca_insurance(leg_fair_p or [], leg_odds or [], stake,
                                 refund_eur=float(terms.get("refund_eur") or stake),
                                 refund_cash=bool(terms.get("refund_cash")),
                                 min_legs=terms.get("min_legs"), max_stake_eur=_f(terms.get("max_stake_eur")))
    if t == "deposit_bonus":
        return ev_deposit_bonus(float(terms.get("face_value_eur") or 0), terms.get("rollover_x"),
                                deposit_eur=_f(terms.get("deposit_eur")))
    return ev_straight(fair_p, odds, stake), "plain bet (no promo mechanics recognised)"


def _f(v):
    return float(v) if v is not None else None
