"""UNIBET-KAMBI-ODDS — direct Unibet prices from the public Kambi offering API.

Unibet is a Kambi operator, as is Coolbet — which is why `coolbet_placer` already
parses "Kambi criterion labels". The offering API is **public, unauthenticated
and has no bot protection**, so this needs none of the Imperva/FlareSolverr
machinery `coolbet_session.py` carries (1,154 lines) or the Cloudflare fallback
in `epicbet_explorer.py`. It is plain JSON over HTTPS.

    list:  /offering/v2018/ub/listView/football.json?lang=et_EE&market=EE
    event: /offering/v2018/ub/betoffer/event/{id}.json?lang=et_EE&market=EE

Measured 2026-09-04 from the EE market: **708 football events across 186
leagues**, of which **524 pre-match** and ~71 virtual (Cyber Live Arena / CLA,
excluded here). Every one of 8 sampled pre-match fixtures carried an O/U 2.5
line, including obscure ones (Paulista U20, Campionato Primavera 2, Kõrgliiga).

WHY IT WRITES `Unibet-Kambi` AND NOT `Unibet`
---------------------------------------------
We already receive Unibet prices through API-Football under `Unibet`, and that
feed has never been checked against the real site. `BET365-EXECUTION-AUDIT-
2026-08-21` found exactly that failure mode: AF-fed Bet365 odds were
systematically inflated, giving CLV +10% against ROI -10%, and Bet365 was
removed from the placeable set for it.

Writing under a separate bookmaker name keeps the two feeds side by side so the
question can be settled with a query instead of an assumption. Merging them
would destroy the only evidence that could answer it. Once the comparison is
done, the loser should be dropped — not silently averaged.

GOTCHAS THE API IMPOSES
-----------------------
* **Odds and lines are milli-units.** `odds: 5750` is 5.75; `line: 4500` is 4.5.
  Storing them raw would put 5750.0 into `odds_snapshots`.
* **Match on `outcome["type"]` AND `criterion.englishLabel`. The type alone is
  NOT enough** — this rule, as originally written, caused a production incident.
  With `lang=et_EE` the localised `criterion.label` comes back Estonian
  ("Üle"/"Alla", "Normaalaeg", "Jah/Ei"), so it must not be matched on. But the
  outcome types are reused across completely different offers: `OT_ONE/CROSS/TWO`
  also appears on 3-Way Handicap, 2nd Half, First Goal, Next Goal and every
  Interval Winner window; `OT_OVER/OT_UNDER` also appears on Total Corners,
  Asian Total and per-team totals. Keying on type alone therefore wrote half-time
  and team-total prices into full-match `1x2` and `over_under_XX`.
  `criterion.englishLabel` is the correct discriminator: it is English regardless
  of the `lang` parameter, so it is both language-stable AND sufficient. See
  KAMBI-CRITERION-CONTAMINATION below.
* **`listView` only carries the MAIN total line**, which is often not 2.5 — the
  per-event endpoint is required for the full ladder.
* **Live events carry in-play-adjusted lines.** A first pass at this sampled
  six fixtures that had all kicked off and concluded Unibet barely offers
  O/U 2.5 on lower leagues. That was wrong: they were live. Pre-match only.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

_BASE = os.getenv("UNIBET_KAMBI_BASE",
                  "https://eu-offering-api.kambicdn.com/offering/v2018/ub")
_MARKET = os.getenv("UNIBET_KAMBI_MARKET", "EE")
_LANG = os.getenv("UNIBET_KAMBI_LANG", "et_EE")
_BOOKMAKER = "Unibet-Kambi"

# Virtual/simulated football. Real fixtures only — these have no counterpart in
# our `matches` table and would burn fuzzy-match attempts.
# KAMBI-VIRTUAL-MARKERS-2026-09-05: "esports battle" / "e-spordi lahing" were
# missing, so 49 of 465 pre-match candidates carried the esoccer player-handle
# signature ("Trabzonspor (Dicca) v Besiktas (chevare)"). Those fuzzy-match to the
# REAL fixture of the same name and write virtual prices onto a real match.
_VIRTUAL_MARKERS = ("cyber live arena", "cla ", "cla world cup", "esoccer",
                    "e-soccer", "virtual", "esports battle", "e-spordi lahing",
                    "esports", "e-sport")

_TIMEOUT = 25
_SLEEP_S = float(os.getenv("UNIBET_KAMBI_SLEEP_S", "0.12"))


def _milli(v) -> float | None:
    """Kambi sends odds and lines multiplied by 1000."""
    try:
        return float(v) / 1000.0
    except (TypeError, ValueError):
        return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": os.getenv(
            "UNIBET_KAMBI_UA",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    })
    return s


def fetch_football_list(sess: requests.Session) -> list[dict]:
    """Every football event Unibet currently offers on this market."""
    r = sess.get(f"{_BASE}/listView/football.json",
                 params={"lang": _LANG, "market": _MARKET, "useCombined": "true"},
                 timeout=_TIMEOUT)
    r.raise_for_status()
    return (r.json() or {}).get("events") or []


def fetch_event_betoffers(sess: requests.Session, event_id: int) -> list[dict]:
    r = sess.get(f"{_BASE}/betoffer/event/{event_id}.json",
                 params={"lang": _LANG, "market": _MARKET}, timeout=_TIMEOUT)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return (r.json() or {}).get("betOffers") or []


def is_virtual(group: str | None) -> bool:
    g = (group or "").lower()
    return any(mark in g for mark in _VIRTUAL_MARKERS)


def shape_candidates(events: list[dict], *, min_lead_minutes: int = 5) -> list[dict]:
    """Pre-match, non-virtual events shaped for `fuzzy_match_event`."""
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for e in events:
        ev = e.get("event") or {}
        if is_virtual(ev.get("group")):
            continue
        start = ev.get("start")
        try:
            st = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        # Pre-match only. A live event's totals are in-play adjusted and are not
        # comparable to the pre-kickoff price everything else in odds_snapshots
        # records.
        if st <= now + timedelta(minutes=min_lead_minutes):
            continue
        name = ev.get("name") or ""
        if " - " not in name:
            continue
        home, away = [p.strip() for p in name.split(" - ", 1)]
        out.append({"home": home, "away": away, "start": start,
                    "raw": {"event_id": ev.get("id"), "group": ev.get("group")}})
    return out


# KAMBI-CRITERION-CONTAMINATION-2026-09-05 — the outcome TYPE does not identify
# the market; the PERIOD and SUBJECT live in the criterion.
#
# parse_betoffers originally keyed only on `outcome["type"]`, following the
# module docstring's "types are language-stable, labels are not" rule. That rule
# is right about language and wrong about sufficiency: Kambi reuses the same
# outcome types across completely different offers. Measured on 25 live events,
# type-only matching swept these into our full-match vocabulary:
#
#   -> stored as `1x2`:   3-Way Handicap, 2nd Half, First Goal, Next Goal (2/3/5),
#                         Interval Winner - 45:00-89:59 (nine variants), Most Corners
#   -> stored as `o/u`:   Total Corners, Asian Total, Total Corners by <Team>,
#                         Total Goals by <Team>  (per-team totals, ~30 variants)
#
# Result in production: 51.0% of Unibet-Kambi (match, market, selection, line,
# timestamp) groups held MORE THAN ONE price, worst case 29 distinct values for
# one selection at one instant. Every other book measured 0.0-0.2%. Because a
# best-price selector takes the MAXIMUM, and half-time / team-total lines are
# systematically longer than the full-match price, `Unibet-Kambi` became the #1
# `recommended_bookmaker` (403 of 1,015 picks in 3 days) at prices that do not
# exist for that market. The operator places real money on those signals.
#
# The fix keeps the language-stability principle but applies it to the right
# field: `criterion.englishLabel` is English regardless of the `lang` parameter.
# It is an exact-match WHITELIST and it FAILS CLOSED — an offer whose criterion
# is missing or unrecognised is skipped, not guessed at. A new Kambi market
# silently entering our feed is exactly what went wrong here.
_CRITERION_1X2 = "full time"
_CRITERION_TOTALS = "total goals"
_CRITERION_BTTS = "both teams to score"


def _criterion_key(offer: dict) -> str:
    """Language-stable criterion label, lowercased. Empty string if absent."""
    crit = offer.get("criterion") or {}
    # englishLabel is present regardless of the `lang` query parameter; `label`
    # is localised and must never be used for matching.
    return str(crit.get("englishLabel") or "").strip().lower()


def parse_betoffers(offers: list[dict]) -> list[tuple[str, str, float, float | None]]:
    """(market, selection, odds, handicap_line) in this repo's shared vocabulary.

    Keyed on `outcome["type"]` AND `criterion.englishLabel` — the type alone
    does not identify the market. See KAMBI-CRITERION-CONTAMINATION above.
    """
    rows: list[tuple[str, str, float, float | None]] = []
    for o in offers:
        outs = o.get("outcomes") or []
        if not outs:
            continue
        types = {str(x.get("type") or "") for x in outs}
        crit_en = _criterion_key(o)

        # 1X2 — exactly the three-way FULL TIME result offer. Without the
        # criterion check this also matched 3-Way Handicap, 2nd Half, First
        # Goal, Next Goal and every Interval Winner window.
        if {"OT_ONE", "OT_CROSS", "OT_TWO"} <= types and crit_en == _CRITERION_1X2:
            for x in outs:
                sel = {"OT_ONE": "home", "OT_CROSS": "draw",
                       "OT_TWO": "away"}.get(str(x.get("type")))
                odds = _milli(x.get("odds"))
                if sel and odds and odds > 1:
                    rows.append(("1x2", sel, odds, None))
            continue

        # Totals. Kambi ships Asian totals (quarter lines) through the same
        # OT_OVER/OT_UNDER shape; only whole/half lines map onto our
        # `over_under_XX` vocabulary, so quarter lines are skipped rather than
        # rounded into a line we do not actually hold.
        # Totals — MATCH total goals only. Without the criterion check this also
        # matched Total Corners, Asian Total, and per-team totals ("Total Goals
        # by <Team>"), all of which price far longer than the match total.
        if ("OT_OVER" in types and "OT_UNDER" in types
                and crit_en == _CRITERION_TOTALS):
            line = _milli(outs[0].get("line"))
            if line is None:
                continue
            if abs(line * 2 - round(line * 2)) > 1e-9:   # e.g. 2.25, 2.75
                continue
            tag = f"over_under_{str(line).replace('.', '').rstrip('0') or '0'}"
            # 2.5 -> "25", 3.0 -> "30", 0.5 -> "05"
            tag = "over_under_" + (f"{line:.1f}".replace(".", "").zfill(2))
            for x in outs:
                sel = {"OT_OVER": "over", "OT_UNDER": "under"}.get(str(x.get("type")))
                odds = _milli(x.get("odds"))
                if sel and odds and odds > 1:
                    rows.append((tag, sel, odds, None))
            continue

        # BTTS — exact criterion match. The previous substring test on the
        # LOCALISED label ("jah"/"both"/"mõlemad") would accept any yes/no offer
        # whose Estonian label happened to contain "jah".
        if types <= {"OT_YES", "OT_NO"} and types and crit_en == _CRITERION_BTTS:
            for x in outs:
                sel = {"OT_YES": "yes", "OT_NO": "no"}.get(str(x.get("type")))
                odds = _milli(x.get("odds"))
                if sel and odds and odds > 1:
                    rows.append(("btts", sel, odds, None))
    return rows


def run_bulk(days: int = 2, dry_run: bool = False,
             limit: int | None = None) -> dict:
    """Snapshot Unibet prices for DB fixtures kicking off within `days`.

    Returns counters so the scheduler log and the smoke test can assert on them.
    """
    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import store_book_odds_snapshots
    from workers.automation.coolbet_placer import fuzzy_match_event

    counters = {"kambi_events": 0, "prematch": 0, "db_matches": 0,
                "matched": 0, "stored": 0, "errors": 0}
    sess = _session()
    events = fetch_football_list(sess)
    counters["kambi_events"] = len(events)
    candidates = shape_candidates(events)
    counters["prematch"] = len(candidates)
    if not candidates:
        return counters

    matches = execute_query(
        """SELECT m.id, m.date, ht.name AS home, at2.name AS away
             FROM matches m
             LEFT JOIN teams ht ON ht.id = m.home_team_id
             LEFT JOIN teams at2 ON at2.id = m.away_team_id
            WHERE m.date > now() AND m.date < now() + (%s || ' days')::interval
              AND m.date_disputed_at IS NULL
            ORDER BY m.date""",
        (str(days),),
    )
    if limit:
        matches = matches[:limit]
    counters["db_matches"] = len(matches)

    for m in matches:
        if not m.get("home") or not m.get("away"):
            continue
        ev = fuzzy_match_event(m["home"], m["away"], candidates,
                               m.get("date"), str(m["id"]))
        if not ev:
            continue
        counters["matched"] += 1
        eid = (ev.get("raw") or {}).get("event_id")
        if not eid:
            continue
        try:
            offers = fetch_event_betoffers(sess, int(eid))
        except Exception as e:                       # noqa: BLE001
            counters["errors"] += 1
            log.warning("unibet-kambi betoffer fetch failed for %s: %s", eid, e)
            continue
        rows = parse_betoffers(offers)
        if rows and not dry_run:
            mins = int((m["date"] - datetime.now(timezone.utc)).total_seconds() // 60)
            counters["stored"] += store_book_odds_snapshots(
                _BOOKMAKER, str(m["id"]), rows, minutes_to_kickoff=mins)
        elif rows:
            counters["stored"] += len(rows)
        time.sleep(_SLEEP_S)
    return counters
