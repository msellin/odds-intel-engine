"""
COOLBET-UI-PLACER (2026-08-27) — place bets by driving Coolbet's own UI.

Why a second placer when `coolbet_placer.py` already posts to /s/bets/bets:
the API path depends on Imperva clearance for every call (search/v2 returns
403 the moment the harvested cookies go stale, which is what killed the feed
for 80h in COOLBET-FEED-WATCHDOG). The operator's real Chrome renders the
same pages natively — Imperva already trusts it — so the UI path has no
cookie-freshness dependency at all. It is slower and DOM-fragile, which is
the trade: use it as the resilient fallback and for supervised placement.

Attaches to the CDP-Chrome the operator already runs
(`local/launch_chrome_for_sync.sh`, port 9222). It never spawns its own
browser and never handles credentials — `coolbet_browser_sync.cdp_auto_login`
owns login, reading COOLBET_USER/COOLBET_PASS from .env itself.

DOM map verified live against the FK Partizan v Getafe page 2026-08-27:

    search box      input[name="sportSearch"]
    result link     a[href^="/et/sport/match/<id>"]
    odds button     button[data-test="button-odds-<marketId>"]
    stake field     input[name="yourStake<marketId>"]
    place button    button[data-test="button-place-bet"]

Note `button-odds-<id>` keys the MARKET, not the outcome — 1X2 home, draw and
away all carry the same id, and Over/Under share one too. The outcome is
identified by the label text in the button's parent. Every market also
renders twice (main block + sticky summary), so all locators take .first.

Safety: `place()` is the only function that commits money and it is never
reached unless the caller passes execute=True AND the placement kill switch
(`coolbet_session_state.placement_paused`) is clear. Default is stage-only.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from rapidfuzz import fuzz

load_dotenv()

log = logging.getLogger(__name__)

CDP_URL = os.getenv("COOLBET_CHROME_CDP_URL", "http://localhost:9222")
SPORT_PAGE = "https://www.coolbet.com/et/sport"

# Selectors — single source of truth so the smoke test can assert on them
# and a Coolbet redesign is a one-place fix.
SEL_SEARCH = 'input[name="sportSearch"]'
SEL_ODDS_BTN = 'button[data-test^="button-odds-"]'
SEL_PLACE_BTN = 'button[data-test="button-place-bet"]'
SEL_STAKE = 'input[name="yourStake{market_id}"]'

# Estonian market/outcome vocabulary as rendered on the match page.
DRAW_LABELS = ("viik",)
OVER_PREFIXES = ("üle", "ule", "over")
UNDER_PREFIXES = ("alla", "under")

_MATCH_HREF_RE = re.compile(r"/et/sport/match/(\d+)")


class UiPlacerError(RuntimeError):
    """Raised when the UI path cannot complete a step it must complete."""


@dataclass
class UiEvent:
    """One search result from Coolbet's own search dropdown."""
    match_id: str
    href: str
    text: str
    home: str = ""
    away: str = ""
    start_text: str = ""


@dataclass
class UiOutcome:
    """One clickable price on the match page."""
    market_id: str
    label: str
    odds: float


@dataclass
class SlipState:
    """What the betslip shows after a selection + stake are staged."""
    selection: str
    odds: float | None
    potential_return: float | None
    place_enabled: bool
    message: str = ""
    raw: str = ""


# ── browser attach ────────────────────────────────────────────────────────────


def attach(pw):
    """Attach to the operator's CDP-Chrome and return (browser, page).

    Prefers an existing coolbet.com tab. Chrome with ZERO pages cannot be
    attached to at all — Playwright's connect_over_cdp calls
    Browser.setDownloadBehavior, which fails with "Browser context management
    is not supported" when no context exists. The caller is responsible for
    ensuring a tab is open (coolbet_browser_sync opens one via /json/new);
    we surface that as a clear error rather than a protocol trace.
    """
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=15000)
    except Exception as e:
        raise UiPlacerError(
            f"cannot attach to CDP-Chrome at {CDP_URL}: {e}. "
            "Start it with ./local/launch_chrome_for_sync.sh and make sure at "
            "least one tab is open (a pageless Chrome cannot be attached to)."
        ) from e
    if not browser.contexts:
        raise UiPlacerError("CDP-Chrome has no browser context — open a tab first.")
    ctx = browser.contexts[0]
    for pg in ctx.pages:
        if "coolbet.com" in pg.url:
            return browser, pg
    return browser, ctx.new_page()


def is_logged_in(page) -> bool:
    """True when the page shows an authenticated session.

    Checks for the login form rather than for a balance widget: a €0.00
    balance is a perfectly valid logged-in state (it is exactly the state
    the operator's account was in on 2026-08-27), so keying on a balance
    value would misreport a funded-but-empty account as logged out.
    """
    return not page.evaluate(
        """() => !!document.querySelector("input[type='password'], input[name='password']")"""
    )


# ── search ────────────────────────────────────────────────────────────────────


def search_events(page, query: str, *, timeout_ms: int = 15000) -> list[UiEvent]:
    """Type `query` into Coolbet's own search box and read the dropdown."""
    if SPORT_PAGE.split("/et/")[0] not in page.url or "/sport" not in page.url:
        page.goto(SPORT_PAGE, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2000)
    page.wait_for_selector(SEL_SEARCH, timeout=timeout_ms)
    page.click(SEL_SEARCH)
    page.fill(SEL_SEARCH, "")
    page.type(SEL_SEARCH, query, delay=80)
    page.wait_for_timeout(3500)

    rows = page.evaluate(
        """() => [...document.querySelectorAll('a[href*="/sport/match/"]')].map(a => ({
              href: a.getAttribute('href'),
              text: (a.innerText||'').replace(/\\s+/g,' ').trim()
           })).filter(r => r.text)"""
    )
    out: list[UiEvent] = []
    seen: set[str] = set()
    for r in rows:
        m = _MATCH_HREF_RE.search(r["href"] or "")
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        home, away = _split_teams(r["text"])
        out.append(
            UiEvent(match_id=m.group(1), href=r["href"], text=r["text"],
                    home=home, away=away, start_text=_start_text(r["text"]))
        )
    return out


def _split_teams(text: str) -> tuple[str, str]:
    """Pull 'A - B' out of a search-result line.

    Coolbet renders results as '<home> - <away> <date> <time> <sport/league>'.
    Outright markets ('... 2026-2027(Team @ Winner)') have no ' - ' and fall
    through to ('',''), which the squad/fuzzy guards then reject.
    """
    head = text.split(" - ")
    if len(head) < 2:
        return "", ""
    home = head[0].strip()
    rest = " - ".join(head[1:])
    away = re.split(r"\s+\d{1,2}\s+\w{3}\b|\s+\d{1,2}:\d{2}\b", rest)[0].strip()
    return home, away


def _start_text(text: str) -> str:
    m = re.search(r"\d{1,2}\s+\w{3},?\s+\d{1,2}:\d{2}", text)
    return m.group(0) if m else ""


# ── matching ──────────────────────────────────────────────────────────────────


def pick_event(
    events: list[UiEvent],
    home: str,
    away: str,
    kickoff: datetime | None = None,
    *,
    min_score: float = 80.0,
) -> UiEvent | None:
    """Choose the search result that is our fixture, or None.

    COOLBET-SQUAD-GUARD (2026-08-27): reuses `_squad_tag` from the Epicbet
    explorer so a reserve/youth side can never match a first team. This is
    the class of false match that produced a fake +87% edge on Epicbet
    (Rosario Central Res. vs Rosario Central, partial_ratio 100). Coolbet is
    the venue real money goes to, so the guard belongs here first.
    """
    from workers.automation.epicbet_explorer import _squad_tag

    best: tuple[float, UiEvent] | None = None
    for ev in events:
        if not ev.home or not ev.away:
            continue
        if _squad_tag(home) != _squad_tag(ev.home):
            continue
        if _squad_tag(away) != _squad_tag(ev.away):
            continue
        # Score both sides and take the WORSE one, so a strong home match
        # cannot paper over a wrong away side (same rule as the API matcher).
        score = min(
            fuzz.token_set_ratio(home.lower(), ev.home.lower()),
            fuzz.token_set_ratio(away.lower(), ev.away.lower()),
        )
        if score < min_score:
            continue
        if kickoff and ev.start_text and not _start_plausible(ev.start_text, kickoff):
            continue
        if best is None or score > best[0]:
            best = (score, ev)
    return best[1] if best else None


def _start_plausible(start_text: str, kickoff: datetime, *, tol_h: int = 30) -> bool:
    """Cheap same-fixture date check on the rendered 'DD mmm HH:MM' string.

    Coolbet renders local Estonian time (UTC+2/+3) with no year and a
    localised month name, so rather than parse it we compare day-of-month
    against the kickoff's own local day, allowing the neighbouring days to
    absorb both the timezone offset and any month-name mismatch.
    """
    m = re.match(r"(\d{1,2})", start_text.strip())
    if not m:
        return True
    day = int(m.group(1))
    ko = kickoff.astimezone(timezone.utc)
    ok_days = {
        (ko + timedelta(hours=off)).day for off in (-tol_h, 0, tol_h)
    }
    return day in ok_days


# ── match page ────────────────────────────────────────────────────────────────


def open_event(page, ev: UiEvent, *, timeout_ms: int = 15000) -> None:
    """Click through to the match page from the search dropdown."""
    link = page.locator(f'a[href="{ev.href}"]').first
    link.click(timeout=timeout_ms)
    page.wait_for_timeout(4000)
    if ev.match_id not in page.url:
        raise UiPlacerError(f"navigation did not land on match {ev.match_id}: {page.url}")


def read_outcomes(page) -> list[UiOutcome]:
    """Read every clickable price on the open match page."""
    rows = page.evaluate(
        """() => [...document.querySelectorAll('button[data-test^="button-odds-"]')].map(b => ({
             market: b.getAttribute('data-test').replace('button-odds-',''),
             odds: (b.innerText||'').replace(/\\s+/g,' ').trim(),
             label: (b.parentElement?.innerText||'').replace(/\\s+/g,' ').trim()
           }))"""
    )
    out: list[UiOutcome] = []
    for r in rows:
        try:
            odds = float((r["odds"] or "").replace(",", "."))
        except ValueError:
            continue
        label = (r["label"] or "").replace(r["odds"] or "", "").strip()
        if not label:
            # Sticky-summary duplicate carries the price but no team name;
            # the main block already supplied a labelled copy.
            continue
        out.append(UiOutcome(market_id=r["market"], label=label, odds=odds))
    return out


def find_outcome(
    outcomes: list[UiOutcome], market: str, selection: str, home: str, away: str
) -> UiOutcome | None:
    """Resolve our (market, selection) vocabulary onto a rendered price.

    Only 1x2 is supported today — that is what the UI path has been verified
    against end to end. OU and AH need line matching against the Estonian
    'Üle/Alla X.X' labels and are deliberately left unimplemented rather than
    guessed at, because a wrong line is a silently wrong bet
    ([[feedback_odds_quality_recurring]]).
    """
    if market != "1x2":
        raise UiPlacerError(
            f"market {market!r} not supported on the UI path yet — only 1x2 is verified"
        )
    want = {"home": home, "away": away, "draw": None}.get(selection)
    for o in outcomes:
        low = o.label.lower()
        if selection == "draw":
            if any(d in low for d in DRAW_LABELS):
                return o
            continue
        if want and fuzz.token_set_ratio(want.lower(), low) >= 85:
            # Reject handicap/total variants that embed the team name
            # ('FK Partizan +1.0') — those are different markets.
            if re.search(r"[+\-]\d|\d\.\d", low):
                continue
            return o
    return None


# ── betslip ───────────────────────────────────────────────────────────────────


def select_outcome(page, outcome: UiOutcome, *, timeout_ms: int = 10000) -> None:
    """Click a price, adding it to the betslip. Reversible; commits nothing."""
    btn = (
        page.locator(f'button[data-test="button-odds-{outcome.market_id}"]')
        .filter(has_text=f"{outcome.odds:.2f}".replace(".", ","))
        .or_(
            page.locator(f'button[data-test="button-odds-{outcome.market_id}"]')
            .filter(has_text=f"{outcome.odds:.2f}")
        )
        .first
    )
    btn.click(timeout=timeout_ms)
    page.wait_for_timeout(2500)


def set_stake(page, market_id: str, stake: float, *, timeout_ms: int = 10000) -> float:
    """Type the stake and return what the field actually holds afterwards.

    The stake field is backed by Coolbet's own on-screen keypad and is a
    React-controlled input, so a fill() that appears to succeed can be
    silently dropped. We read the value back and the caller compares — never
    assume the stake stuck ([[feedback_silent_failures]]).
    """
    sel = SEL_STAKE.format(market_id=market_id)
    page.wait_for_selector(sel, timeout=timeout_ms)
    page.fill(sel, "")
    page.type(sel, f"{stake:.2f}", delay=80)
    page.wait_for_timeout(2000)
    raw = page.input_value(sel) or "0"
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return 0.0


def read_slip(page) -> SlipState:
    """Read the staged betslip: selection, price, return, and whether it can be placed."""
    data = page.evaluate(
        """() => {
             const pb = document.querySelector('button[data-test="button-place-bet"]');
             if (!pb) return null;
             let box = pb;
             for (let i=0;i<6&&box;i++) box = box.parentElement;
             return {
               disabled: pb.disabled || pb.getAttribute('aria-disabled') === 'true',
               text: (box ? box.innerText : '').replace(/\\s+/g,' ').trim()
             };
           }"""
    )
    if not data:
        raise UiPlacerError("betslip not present — no selection staged?")
    text = data["text"]
    win = re.search(r"(\d+[.,]\d{2})\s*€", text)
    odds = re.search(r"(\d+[.,]\d{2})\s+L[õo]pptulemus", text)
    return SlipState(
        selection=text[:120],
        odds=float(odds.group(1).replace(",", ".")) if odds else None,
        potential_return=float(win.group(1).replace(",", ".")) if win else None,
        place_enabled=not data["disabled"],
        message=_slip_message(text),
        raw=text,
    )


def _slip_message(text: str) -> str:
    """Surface the blocking reason Coolbet renders inside the slip, if any."""
    for needle, msg in (
        ("ületab vaba saldot", "insufficient balance"),
        ("muutunud", "odds changed"),
        ("suletud", "market closed"),
    ):
        if needle in text.lower():
            return msg
    return ""


def clear_stake(page, market_id: str) -> None:
    """Blank the stake field — used to leave the slip uncommitted after staging."""
    try:
        page.fill(SEL_STAKE.format(market_id=market_id), "")
    except Exception as e:  # slip may already be gone
        log.debug("clear_stake no-op: %s", e)


def place(page, *, timeout_ms: int = 20000) -> SlipState:
    """Click TEE PANUS. THIS COMMITS REAL MONEY.

    Never called unless the caller passed execute=True and the kill switch is
    clear — both are enforced in `stage_bet`, not here, so this stays a dumb
    primitive that is trivially greppable.
    """
    page.locator(SEL_PLACE_BTN).first.click(timeout=timeout_ms)
    page.wait_for_timeout(5000)
    try:
        return read_slip(page)
    except UiPlacerError:
        # Slip clears on a successful placement — that is the success shape.
        return SlipState(selection="", odds=None, potential_return=None,
                         place_enabled=False, message="slip cleared after placement")


# ── orchestration ─────────────────────────────────────────────────────────────


@dataclass
class StageResult:
    ok: bool
    reason: str = ""
    event: UiEvent | None = None
    outcome: UiOutcome | None = None
    slip: SlipState | None = None
    stake_applied: float = 0.0
    placed: bool = False
    notes: list[str] = field(default_factory=list)


def stage_bet(
    page,
    bet: dict,
    stake: float,
    *,
    execute: bool = False,
    max_odds_drop_pct: float = 5.0,
) -> StageResult:
    """Drive one qualified pick through the UI up to (optionally) placement.

    Steps: search → match → open → read prices → resolve outcome → odds-drift
    check → select → stake (verified) → read slip → optionally place.

    `execute=False` (default) stops with the bet staged and the stake cleared,
    which is a complete no-op against the account.
    """
    home, away = bet["home_team"], bet["away_team"]
    notes: list[str] = []

    if not is_logged_in(page):
        return StageResult(False, "not logged in — run coolbet_browser_sync --cdp-auto-login")

    events = search_events(page, home)
    if not events:
        return StageResult(False, f"no search results for {home!r}")

    ev = pick_event(events, home, away, bet.get("match_date"))
    if ev is None:
        return StageResult(False, f"no confident match for {home} v {away} in {len(events)} results")

    open_event(page, ev)
    outcomes = read_outcomes(page)
    if not outcomes:
        return StageResult(False, "no prices rendered on match page", event=ev)

    outcome = find_outcome(outcomes, bet["market"], bet["selection"], home, away)
    if outcome is None:
        return StageResult(False, f"{bet['market']}/{bet['selection']} not found on page", event=ev)

    # Odds drift — the pick was qualified against a captured price. Refuse to
    # stake into a materially worse one; edge is the whole reason we are here.
    captured = bet.get("odds") or bet.get("captured_odds")
    if captured:
        drop = (float(captured) - outcome.odds) / float(captured) * 100.0
        if drop > max_odds_drop_pct:
            return StageResult(
                False,
                f"odds dropped {drop:.1f}% ({captured} → {outcome.odds}), limit {max_odds_drop_pct}%",
                event=ev, outcome=outcome,
            )
        notes.append(f"odds {captured} → {outcome.odds} ({drop:+.1f}%)")

    select_outcome(page, outcome)
    applied = set_stake(page, outcome.market_id, stake)
    if abs(applied - stake) > 0.005:
        clear_stake(page, outcome.market_id)
        return StageResult(
            False, f"stake did not stick: wanted {stake:.2f}, field holds {applied:.2f}",
            event=ev, outcome=outcome,
        )

    slip = read_slip(page)
    notes.append(f"slip: {slip.selection[:60]}")

    if not execute:
        clear_stake(page, outcome.market_id)
        return StageResult(True, "staged (not placed)", ev, outcome, slip, applied, False, notes)

    from workers.automation.coolbet_state import is_placement_paused
    paused, why = is_placement_paused()
    if paused:
        clear_stake(page, outcome.market_id)
        return StageResult(False, f"placement_paused: {why or 'no reason given'}",
                           ev, outcome, slip, applied, False, notes)

    if not slip.place_enabled:
        clear_stake(page, outcome.market_id)
        return StageResult(False, f"place button disabled ({slip.message or 'unknown'})",
                           ev, outcome, slip, applied, False, notes)

    after = place(page)
    return StageResult(True, "placed", ev, outcome, after, applied, True, notes)
