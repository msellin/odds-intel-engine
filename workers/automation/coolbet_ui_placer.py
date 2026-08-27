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


def dismiss_overlays(page, *, tries: int = 3) -> bool:
    """Close any modal/backdrop sitting over the page. Returns True if clear.

    A leftover MUI modal backdrop (`div.MuiBackdrop-root`) covers the entire
    viewport, so every subsequent click fails Playwright's actionability check
    with a bare timeout that says nothing about the cause. Two full 15-pick
    runs were lost to this before `elementFromPoint` on the search box came
    back as the backdrop rather than the input.

    Escape first (the documented dismissal), then a click on the backdrop
    itself for modals that ignore it.
    """
    for _ in range(tries):
        present = page.evaluate(
            "() => !!document.querySelector('div.MuiBackdrop-root')"
        )
        if not present:
            return True
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
        if not page.evaluate("() => !!document.querySelector('div.MuiBackdrop-root')"):
            return True
        try:
            page.locator("div.MuiBackdrop-root").first.click(timeout=2000)
        except Exception as e:
            log.debug("backdrop click failed: %s", e)
        page.wait_for_timeout(700)
    return not page.evaluate("() => !!document.querySelector('div.MuiBackdrop-root')")


def search_events(page, query: str, *, timeout_ms: int = 15000) -> list[UiEvent]:
    """Type `query` into Coolbet's own search box and read the dropdown."""
    # Gate on the search box actually being present, not on the URL looking
    # right. A match page's URL contains '/sport' and passes any URL check
    # while the header search can still be absent mid-render — which stalled a
    # whole 15-pick run on Page.click timeouts.
    dismiss_overlays(page)
    # Gate on the search box being VISIBLE, not merely present. On a match page
    # the header search stays in the DOM but collapsed, so a querySelector check
    # passes and every click then times out waiting for actionability — that
    # stalled two full 15-pick runs.
    if not page.locator(SEL_SEARCH).first.is_visible():
        page.goto(SPORT_PAGE, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2000)
    page.wait_for_selector(SEL_SEARCH, timeout=timeout_ms)
    # .first everywhere: Coolbet renders the header search more than once
    # (desktop + responsive), and page.click() is strict — it fails with
    # "locator resolved to N elements" rather than picking one, which stalled
    # an entire 15-pick run on 30s timeouts.
    box = page.locator(SEL_SEARCH).first
    box.click(timeout=timeout_ms)
    box.fill("")
    box.type(query, delay=80)
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
    from workers.automation.coolbet_placer import _ascii, _team_aliases
    from workers.automation.epicbet_explorer import _squad_tag

    def _best_score(ours: str, theirs: str) -> float:
        # Fold diacritics before scoring — Coolbet lists 'Centro Atlético
        # Fénix' where our DB has 'Fenix', and an unfolded 'é' drops the score
        # below threshold on an otherwise exact token match. Aliases cover the
        # cases folding cannot: 'Austria Vienna' vs 'FK Austria Wien' is a
        # translation, not a spelling.
        t = _ascii(theirs)
        return max(fuzz.token_set_ratio(_ascii(a), t) for a in _team_aliases(ours))

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
        score = min(_best_score(home, ev.home), _best_score(away, ev.away))
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


def _wanted_line(market: str) -> float | None:
    """Extract the total from our market vocabulary: over_under_25 -> 2.5."""
    m = re.fullmatch(r"over_under_(\d{2,3})", market)
    if not m:
        return None
    digits = m.group(1)
    return float(f"{digits[:-1]}.{digits[-1]}")


def _label_line(label: str) -> float | None:
    """Extract the total Coolbet rendered: 'Üle Ü 2.5' -> 2.5.

    Coolbet writes the side twice — the word then its initial ('Üle Ü 2.5',
    'Alla A 1.73') — so the number is taken from the LAST numeric token that
    looks like a line, not the first thing that parses as a float.
    """
    nums = re.findall(r"\d+(?:[.,]\d+)?", label)
    for tok in nums:
        val = float(tok.replace(",", "."))
        # Lines are .0/.25/.5/.75 steps and realistically below 10; odds are
        # >1 with two decimals. Require a genuine line-shaped step.
        if val < 10 and abs(val * 4 - round(val * 4)) < 1e-9:
            return val
    return None


def _ou_side(label: str) -> str | None:
    """'over' / 'under' / None from an Estonian or English OU label."""
    low = label.strip().lower()
    if low.startswith(OVER_PREFIXES):
        return "over"
    if low.startswith(UNDER_PREFIXES):
        return "under"
    return None


def find_outcome(
    outcomes: list[UiOutcome], market: str, selection: str, home: str, away: str
) -> UiOutcome | None:
    """Resolve our (market, selection) vocabulary onto a rendered price.

    Supports 1x2 and over_under_XX. Asian handicap is NOT supported — Coolbet
    renders AH as team-name-plus-handicap ('FK Partizan +1.0'), which collides
    with the 1X2 label space, and quarter lines do not exist there at all
    ([[project_coolbet_limitations]]). It raises rather than guesses, because a
    wrong line is a silently wrong bet ([[feedback_odds_quality_recurring]]).
    """
    if market == "1x2":
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

    line = _wanted_line(market)
    if line is not None:
        if selection not in ("over", "under"):
            raise UiPlacerError(f"selection {selection!r} invalid for {market!r}")
        for o in outcomes:
            if _ou_side(o.label) != selection:
                continue
            got = _label_line(o.label)
            # Exact line only. 'Over 2.5' and 'Over 3.5' are different bets and
            # a near-miss here is how you silently back the wrong total.
            if got is not None and abs(got - line) < 1e-9:
                return o
        return None

    raise UiPlacerError(
        f"market {market!r} not supported on the UI path — only 1x2 and over_under_XX"
    )


def read_ou_grid(page) -> list[UiOutcome]:
    """Read the full Over/Under ladder from the 'Väravate arv (Üle/Alla)' card.

    The card is a grid: a left column of line labels (1.5, 2, 2.5, 3 …) and two
    price columns, Üle (over) then Alla (under). The line lives in its own cell,
    NOT inside the button, which is why a naive `read_outcomes` sees bare prices
    with nothing saying which total they belong to.

    The row is recovered geometrically — label and its two prices share a
    horizontal band — because the DOM gives no per-row container to walk up to
    and the class names are hashed CSS modules. Column order (lower x = Üle)
    decides the side.

    Do NOT read totals off the header strip at the top of the match page: those
    are quick-bet shortcuts showing a single line, and that is what made the
    first attempt at OU miss every non-main total.

    Integrity guard: the two prices on a row must share a market id (Coolbet
    keys one id per line). A row that fails that is dropped rather than
    guessed at — a mis-paired row is a silently wrong total.
    """
    raw = page.evaluate(
        r"""() => {
              const cards = [...document.querySelectorAll('div')].filter(d =>
                /Väravate arv/.test(d.innerText || '') &&
                d.querySelectorAll('button[data-test^="button-odds-"]').length >= 6);
              if (!cards.length) return null;
              const card = cards[cards.length - 1];   // innermost matching card
              const mid = e => {
                const r = e.getBoundingClientRect();
                return { x: Math.round(r.x), y: Math.round(r.y + r.height / 2) };
              };
              const labels = [...card.querySelectorAll('*')]
                .filter(e => e.children.length === 0 &&
                             /^\d+(\.\d)?$/.test((e.innerText || '').trim()))
                .map(e => ({ t: (e.innerText || '').trim(), ...mid(e) }));
              const btns = [...card.querySelectorAll('button[data-test^="button-odds-"]')]
                .map(e => ({ odds: (e.innerText || '').trim(),
                             mkt: e.getAttribute('data-test').replace('button-odds-', ''),
                             ...mid(e) }));
              return { labels, btns };
           }"""
    )
    if not raw or not raw.get("labels"):
        return []

    out: list[UiOutcome] = []
    for lab in raw["labels"]:
        try:
            line = float(lab["t"])
        except ValueError:
            continue
        # Same horizontal band as the label, and to its right.
        row = sorted(
            (b for b in raw["btns"] if abs(b["y"] - lab["y"]) <= 6 and b["x"] > lab["x"]),
            key=lambda b: b["x"],
        )
        if len(row) != 2:
            log.debug("OU row for line %s has %d prices — skipped", line, len(row))
            continue
        if row[0]["mkt"] != row[1]["mkt"]:
            log.warning(
                "OU row %s pairs different market ids (%s/%s) — skipped",
                line, row[0]["mkt"], row[1]["mkt"],
            )
            continue
        for side, cell in (("Üle", row[0]), ("Alla", row[1])):
            try:
                odds = float(cell["odds"].replace(",", "."))
            except ValueError:
                continue
            out.append(UiOutcome(market_id=cell["mkt"], label=f"{side} {line:g}", odds=odds))
    return out


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
    # The stake field is named yourStake<id>, but that id is NOT always the
    # market id we clicked — it varies by market type. Only one single is in
    # the slip at a time, so match on the name PREFIX and take the first.
    sel = SEL_STAKE.format(market_id=market_id)
    if page.locator(sel).count() == 0:
        sel = 'input[name^="yourStake"]'
    page.wait_for_selector(sel, timeout=timeout_ms)
    field = page.locator(sel).first
    field.fill("")
    field.type(f"{stake:.2f}", delay=80)
    page.wait_for_timeout(2000)
    raw = field.input_value() or "0"
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return 0.0


def slip_ticket_count(page) -> int:
    """How many selections the betslip holds, from its own 'N Pilet' counter."""
    txt = page.evaluate("() => document.body.innerText || ''")
    m = re.search(r"(\d+)\s*Pilet", txt)
    return int(m.group(1)) if m else 0


def empty_slip(page, *, tries: int = 8) -> int:
    """Empty the betslip. Returns the ticket count left behind (0 = clean).

    Each selection card carries a small trash <svg> with no label, id or
    data-test — so it is found by SHAPE and CONTEXT: a ~16px icon whose
    enclosing div also contains an odds-looking number ('Viik 3.35'). That is
    the only stable handle Coolbet gives; class names are hashed CSS modules.

    This matters for periodic running: selections survive navigation, so a slip
    left dirty by an earlier pass blocks every pick on the next one.
    """
    for _ in range(tries):
        n = slip_ticket_count(page)
        if n == 0:
            return 0
        # Real Playwright click, not a dispatched MouseEvent: React ignores
        # synthetic events on these icons (the same trap as the OU line tabs).
        svgs = page.locator("svg")
        clicked = False
        for i in range(min(svgs.count(), 200)):
            el = svgs.nth(i)
            try:
                box = el.bounding_box()
                if not box or box["width"] > 26 or box["width"] < 6:
                    continue
                near = el.evaluate(
                    r"""el => {
                          const d = el.closest('div');
                          return (d ? d.innerText : '').replace(/\s+/g, ' ').trim();
                       }"""
                )
                if not re.search(r"\d+[.,]\d{2}", near or ""):
                    continue
                el.click(timeout=3000, force=True)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            break
        page.wait_for_timeout(1200)
    return slip_ticket_count(page)


def deselect_outcome(page, outcome: UiOutcome, *, timeout_ms: int = 8000) -> None:
    """Re-click a price to take it back OUT of the betslip.

    Coolbet's odds buttons toggle. Blanking the stake is NOT enough — the
    selection stays in the slip and the next pick's selection lands on top of
    it, so the slip accumulates. Observed live on 2026-08-27: after three
    staged picks the slip read '2 Pilet' and the third pick's slip text showed
    the SECOND pick's selection. Placing from that state bets the wrong thing.
    """
    try:
        select_outcome(page, outcome, timeout_ms=timeout_ms)
    except Exception as e:
        log.warning("could not deselect %s: %s", outcome.label, e)


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
        sel = SEL_STAKE.format(market_id=market_id)
        if page.locator(sel).count() == 0:
            sel = 'input[name^="yourStake"]'
        page.locator(sel).first.fill("")
    except Exception as e:  # slip may already be gone
        log.debug("clear_stake no-op: %s", e)


def read_balance(page) -> float | None:
    """Account balance in EUR, or None if it cannot be read.

    Used as the ONLY reliable confirmation that a bet was accepted: Coolbet's
    UI gives the placer no ticket id, and the betslip clears on both success
    and several failure modes.
    """
    val = page.evaluate(
        r"""() => {
              const cands = [...document.querySelectorAll('*')]
                .filter(e => e.children.length === 0 && /€/.test(e.innerText || ''))
                .map(e => (e.innerText || '').trim())
                .filter(t => /^[\d\s .,]+€$/.test(t));
              return cands.length ? cands[0] : null;
           }"""
    )
    if not val:
        return None
    cleaned = (val.replace("€", "").replace("\u00a0", "").replace(" ", "")
                  .replace(".", "").replace(",", "."))
    try:
        return float(cleaned)
    except ValueError:
        return None


def place(page, *, timeout_ms: int = 20000) -> SlipState | None:
    """Click TEE PANUS, then the confirmation step. THIS COMMITS REAL MONEY.

    Coolbet uses a TWO-STEP confirm: the first click swaps the slip into a
    confirmation state and a second control actually submits. Getting this
    wrong is what produced a false 'placed' record on 2026-08-27 — the first
    click landed, the slip changed, read_slip raised, and the old code called
    that success while the balance never moved.

    The confirm control is matched on several plausible shapes because it can
    only be observed mid-placement. A wrong guess is SAFE: the caller confirms
    by balance delta, so an unclicked confirm reports "not confirmed" rather
    than claiming a bet that does not exist.

    Never called unless the caller passed execute=True, the kill switch is
    clear, and the slip holds exactly one selection.
    """
    page.locator(SEL_PLACE_BTN).first.click(timeout=timeout_ms)
    page.wait_for_timeout(2500)

    # Second step. Try the same button again first — Coolbet re-labels the
    # primary action in place — then explicit confirm wordings.
    for attempt in (
        lambda: page.locator(SEL_PLACE_BTN).first,
        lambda: page.get_by_role("button", name=re.compile(r"kinnita|confirm", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile(r"n[õo]ustu|accept", re.I)).first,
        lambda: page.locator('button:has-text("TEE PANUS")').first,
    ):
        try:
            loc = attempt()
            if loc.count() == 0 or not loc.is_visible():
                continue
            loc.click(timeout=6000)
            page.wait_for_timeout(3000)
            break
        except Exception as e:
            log.debug("confirm candidate failed: %s", e)
            continue

    page.wait_for_timeout(2500)
    try:
        return read_slip(page)
    except UiPlacerError:
        # Slip gone. That is CONSISTENT with success but is not evidence of it —
        # the caller must still confirm via balance.
        return None


# ── audit trail ───────────────────────────────────────────────────────────────


def record_attempt(
    bet: dict,
    *,
    outcome: str,
    stage: str,
    reason: str = "",
    ev: "UiEvent | None" = None,
    ui_outcome: "UiOutcome | None" = None,
    slip: "SlipState | None" = None,
    stake_requested: float | None = None,
    stake_applied: float | None = None,
    execute_mode: bool = False,
    ticket_id: str | None = None,
    real_bet_id: str | None = None,
) -> str | None:
    """Write one row to coolbet_placement_attempts — ALWAYS, whatever happened.

    A placer that quietly places nothing looks identical to one with nothing to
    place unless the misses are recorded too ([[feedback_silent_failures]]).
    `coolbet_odds` stays NULL exactly when we never reached a price, and
    `reason` says why rather than leaving a hole.

    Never raises: an audit-write failure must not abort a placement run, and it
    certainly must not mask the outcome we were trying to record.
    """
    # execute_write_returning, NOT execute_query: execute_query never commits,
    # so the INSERT returned a fresh id and then vanished on connection
    # release — an audit write that reports success and stores nothing.
    from workers.api_clients.db import execute_write_returning

    captured = bet.get("odds_at_pick") or bet.get("odds") or bet.get("captured_odds")
    cb_odds = ui_outcome.odds if ui_outcome else None
    drift = None
    if captured and cb_odds:
        drift = (float(captured) - cb_odds) / float(captured) * 100.0
    try:
        rows = execute_write_returning(
            """INSERT INTO coolbet_placement_attempts
                 (bot_id, bot_name, shadow_bet_id, simulated_bet_id, match_id,
                  home_team, away_team, kickoff, market, selection,
                  captured_odds, coolbet_odds, odds_drift_pct,
                  stake_requested, stake_applied,
                  coolbet_match_id, coolbet_market_id,
                  outcome, stage, reason, slip_text, ticket_id, real_bet_id,
                  execute_mode)
               VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s, %s,%s,
                       %s,%s, %s,%s,%s,%s,%s,%s, %s)
               RETURNING id::text""",
            (
                bet.get("bot_id"), bet.get("bot_name"),
                bet.get("shadow_bet_id") or bet.get("id"),
                bet.get("simulated_bet_id"), bet.get("match_id"),
                bet.get("home_team"), bet.get("away_team"), bet.get("match_date"),
                bet.get("market"), bet.get("selection"),
                captured, cb_odds, drift,
                stake_requested, stake_applied,
                ev.match_id if ev else None,
                ui_outcome.market_id if ui_outcome else None,
                outcome, stage, reason or None,
                slip.raw[:2000] if slip else None,
                ticket_id, real_bet_id, execute_mode,
            ),
        )
        return rows[0]["id"] if rows else None
    except Exception as e:
        log.error("could not record placement attempt (%s/%s): %s", outcome, stage, e)
        return None


def min_odds_for(bet: dict, threshold: float) -> float | None:
    """Break-even price for this pick: (1 + threshold) / model probability.

    Same formula the /picks page and the shadow-bots admin use. Below this the
    edge is gone and the bet is negative EV, so it is the authoritative gate —
    a generic "odds dropped less than X pct" check is not equivalent, because a
    pick can be under its floor at pick time without having drifted at all.
    """
    prob = bet.get("model_probability") or bet.get("calibrated_prob")
    if not prob:
        return None
    prob = float(prob)
    return (1.0 + threshold) / prob if prob > 0 else None


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
    edge_threshold: float = 0.03,
    max_odds_drop_pct: float = 100.0,
) -> StageResult:
    """Drive one qualified pick through the UI up to (optionally) placement.

    Steps: search → match → open → read prices → resolve outcome → min-odds
    gate → select → stake (verified by read-back) → read slip → optionally
    place. EVERY exit writes a coolbet_placement_attempts row, including the
    ones that never reached a price.

    `execute=False` (default) stops with the bet staged and the stake cleared,
    which is a complete no-op against the account.

    The primary price gate is min-odds — (1 + edge_threshold) / model
    probability — not a drift percentage. A pick can sit below its break-even
    price without having drifted at all, and taking it is negative EV by the
    bot's own criterion.
    """
    home, away = bet["home_team"], bet["away_team"]
    notes: list[str] = []

    def _fail(stage: str, reason: str, ev=None, oc=None, slip=None,
              applied: float | None = None) -> StageResult:
        record_attempt(bet, outcome="rejected", stage=stage, reason=reason,
                       ev=ev, ui_outcome=oc, slip=slip,
                       stake_requested=stake, stake_applied=applied,
                       execute_mode=execute)
        return StageResult(False, reason, ev, oc, slip, applied or 0.0, False, notes)

    if not is_logged_in(page):
        return _fail("login", "not logged in — run coolbet_browser_sync --cdp-auto-login")

    # Never work against a dirty slip. Selections left by an earlier run cannot
    # be told apart from ours once staged, and placing from a multi-selection
    # slip bets something we never chose.
    dirty = slip_ticket_count(page)
    if dirty:
        dirty = empty_slip(page)
    if dirty:
        return _fail("login", f"betslip is not empty ({dirty} selection(s)) — clear it first")

    try:
        events = search_events(page, home)
    except Exception as e:
        return _fail("search", f"search failed: {type(e).__name__}: {str(e)[:120]}")
    if not events:
        return _fail("search", f"no search results for {home!r}")

    ev = pick_event(events, home, away, bet.get("match_date"))
    if ev is None:
        return _fail("match", f"no confident match for {home} v {away} in {len(events)} results")

    try:
        open_event(page, ev)
    except Exception as e:
        return _fail("open", f"could not open match page: {str(e)[:120]}", ev)

    # Totals come from the 'Väravate arv (Üle/Alla)' card, which carries the
    # whole ladder. The header strip at the top of the page shows one line only
    # (quick bets) — reading totals from there misses every non-main line.
    if _wanted_line(bet["market"]) is not None:
        outcomes = read_ou_grid(page)
        if not outcomes:
            return _fail("price", "OU ladder not found on match page", ev)
    else:
        outcomes = read_outcomes(page)
        if not outcomes:
            return _fail("price", "no prices rendered on match page", ev)

    try:
        outcome = find_outcome(outcomes, bet["market"], bet["selection"], home, away)
    except UiPlacerError as e:
        return _fail("outcome", str(e), ev)
    if outcome is None:
        return _fail("outcome", f"{bet['market']}/{bet['selection']} not offered on this page", ev)

    # ── price gates ──────────────────────────────────────────────────────────
    floor = min_odds_for(bet, edge_threshold)
    if floor is not None and outcome.odds < floor:
        return _fail(
            "drift",
            f"below min odds: {outcome.odds} < {floor:.2f} "
            f"(break-even at {edge_threshold:.0%} edge)",
            ev, outcome,
        )
    if floor is not None:
        notes.append(f"min_odds={floor:.2f} coolbet={outcome.odds}")

    captured = bet.get("odds_at_pick") or bet.get("odds") or bet.get("captured_odds")
    if captured:
        drop = (float(captured) - outcome.odds) / float(captured) * 100.0
        if drop > max_odds_drop_pct:
            return _fail("drift", f"odds dropped {drop:.1f}pct ({captured} → {outcome.odds})",
                         ev, outcome)
        notes.append(f"odds {captured} → {outcome.odds} ({drop:+.1f}pct)")

    # ── stake ────────────────────────────────────────────────────────────────
    # Any UI step can raise (timeouts, re-renders). An exception here must be
    # RECORDED as a failed attempt, not escape and abort the whole run — one
    # unhandled stake timeout killed a 15-pick run mid-way.
    try:
        select_outcome(page, outcome)
        applied = set_stake(page, outcome.market_id, stake)
    except Exception as e:
        try:
            clear_stake(page, outcome.market_id)
        except Exception:
            pass
        return _fail("stake", f"{type(e).__name__}: {str(e)[:140]}", ev, outcome)
    if abs(applied - stake) > 0.005:
        clear_stake(page, outcome.market_id)
        return _fail("stake", f"stake did not stick: wanted {stake:.2f}, field holds {applied:.2f}",
                     ev, outcome, applied=applied)

    slip = read_slip(page)
    notes.append(f"slip: {slip.selection[:60]}")

    if not execute:
        clear_stake(page, outcome.market_id)
        deselect_outcome(page, outcome)
        record_attempt(bet, outcome="staged", stage="slip",
                       reason="staged only (execute=False)", ev=ev, ui_outcome=outcome,
                       slip=slip, stake_requested=stake, stake_applied=applied,
                       execute_mode=False)
        return StageResult(True, "staged (not placed)", ev, outcome, slip, applied, False, notes)

    from workers.automation.coolbet_state import is_placement_paused
    paused, why = is_placement_paused()
    if paused:
        clear_stake(page, outcome.market_id)
        return _fail("place", f"placement_paused: {why or 'no reason given'}",
                     ev, outcome, slip, applied)

    if not slip.place_enabled:
        clear_stake(page, outcome.market_id)
        deselect_outcome(page, outcome)
        return _fail("place", f"place button disabled ({slip.message or 'unknown'})",
                     ev, outcome, slip, applied)

    # Final safety before money moves: the slip must hold exactly our one bet.
    n = slip_ticket_count(page)
    if n != 1:
        clear_stake(page, outcome.market_id)
        deselect_outcome(page, outcome)
        return _fail("place", f"refusing to place — slip holds {n} selections, expected 1",
                     ev, outcome, slip, applied)

    # CONFIRM BY EVIDENCE, never by absence of an exception. On 2026-08-27 a
    # run recorded outcome='placed' for FK Jablonec draw @ 3.50 while the
    # balance never moved off EUR 250.00 — the click had not placed anything.
    # The UI gives no ticket id and the slip clears on success AND on several
    # failures, so the balance delta is the only trustworthy signal.
    balance_before = read_balance(page)
    after = place(page)
    balance_after = read_balance(page)
    if balance_before is None or balance_after is None:
        clear_stake(page, outcome.market_id)
        return _fail("place", "cannot read balance — refusing to claim a placement "
                              "that cannot be confirmed", ev, outcome, after, applied)
    moved = balance_before - balance_after
    if abs(moved - applied) > 0.01:
        return _fail(
            "place",
            f"placement NOT confirmed: balance {balance_before:.2f} -> "
            f"{balance_after:.2f} (moved {moved:.2f}, expected {applied:.2f})",
            ev, outcome, after, applied,
        )
    notes.append(f"balance {balance_before:.2f} -> {balance_after:.2f}")
    real_bet_id = None
    try:
        from workers.api_clients.supabase_client import store_real_bet
        real_bet_id = store_real_bet(
            match_id=str(bet["match_id"]), market=bet["market"], selection=bet["selection"],
            bookmaker="Coolbet", captured_odds=float(captured) if captured else outcome.odds,
            actual_odds=outcome.odds, stake=applied, bot_id=str(bet["bot_id"]) if bet.get("bot_id") else None,
            notes=f"ui-placer edge_threshold={edge_threshold:.2%}",
        )
    except Exception as e:
        # The bet is already placed at Coolbet at this point. Losing the
        # real_bets row must not look like a failed placement.
        log.error("placed but could not write real_bets: %s", e)
        notes.append(f"real_bets write failed: {str(e)[:80]}")
    record_attempt(bet, outcome="placed", stage="place", reason="",
                   ev=ev, ui_outcome=outcome, slip=after,
                   stake_requested=stake, stake_applied=applied,
                   execute_mode=True, real_bet_id=real_bet_id)
    return StageResult(True, "placed", ev, outcome, after, applied, True, notes)
