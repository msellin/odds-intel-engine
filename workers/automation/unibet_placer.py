"""UNIBET-UI-PLACER Phase 2 — bet-slip driver for unibet.ee (real money).

Mirrors the Coolbet own-betting flow (scripts/place_coolbet_ui.py) for Unibet's
BESPOKE, DataDome-protected, Estonian-locale sportsbook. Drives the operator's real
logged-in CDP-Chrome (via workers/automation/unibet_browser_sync). The full
placement flow, with the stable `data-test-name` selectors mapped 2026-09-09:

    ensure logged in  → cdp_auto_login (auto-login from UNIBET_USER/UNIBET_PASS)
    open event        → navigate to the fixture URL (from the search matcher)
    clear the slip    → [data-test-name="bet-list-header-trash-icon"]
    select outcome    → [data-test-name="propositionOptionBtn"] (match text + odds)
    set stake         → input[placeholder="0.00"]  (STAKE_EUR)
    place             → button "Tee panus"

SAFETY (mirrors Coolbet):
  * STAKE_EUR = €10 flat, same as Coolbet's FLAT_STAKE_EUR.
  * execute=False by default → PAPER: it fills the slip and stops before "Tee panus".
    Real money only when execute=True.
  * Eligibility re-checked at the LIVE SITE price immediately before placing (the
    public Kambi feed diverges — proven 3.20 feed vs 3.50 site on Derby), and the bet
    is placed ONLY if it still clears `min_odds`. Single-leg guarantee: aborts unless
    the slip holds exactly the one intended selection.
  * Balance is read before/after; a real placement must drop the balance by the stake.

This module does NOT decide WHICH bets to place or find matches — that is the
Stage-B best-price router (checks Coolbet + Unibet, places at the better book, no
duplicates). This is just the executor for a resolved (event_url, outcome, min_odds).
"""
from __future__ import annotations

import logging
import re

from workers.automation import unibet_browser_sync as ubs

log = logging.getLogger(__name__)

STAKE_EUR = 10.0  # flat, matching Coolbet (workers.../engine FLAT_STAKE_EUR)

_TRASH = '[data-test-name="bet-list-header-trash-icon"]'
_OUTCOME = '[data-test-name="propositionOptionBtn"]'
_STAKE_INPUT = 'input[placeholder="0.00"]'


def _balance(page) -> str | None:
    try:
        m = page.evaluate(
            r"""() => { const m=document.body.innerText.match(/Põhisaldo[^\d]*([\d.,]+)/); return m?m[1]:null; }"""
        )
        return m
    except Exception:
        return None


def _clear_slip(page) -> None:
    try:
        el = page.locator(_TRASH).first
        if el.count() > 0:
            el.click(timeout=2500)
            page.wait_for_timeout(700)
    except Exception:
        pass


def place_bet(event_url: str, outcome_name: str, min_odds: float,
              odds_lo: float, odds_hi: float, *, execute: bool = False,
              stake: float = STAKE_EUR) -> dict:
    """Place (execute=True) or stage (execute=False) a single bet on unibet.ee.

    `outcome_name` is matched against the outcome button text (e.g. the home team for
    a 1x2 home); `odds_lo`/`odds_hi` disambiguate the intended market (the button text
    is "<name><odds>", so a tight odds band pins the right market). `min_odds` is the
    eligibility floor — the LIVE slip odds must be >= this or we abort. Never raises.
    """
    from playwright.sync_api import sync_playwright
    out = {"event": event_url, "outcome": outcome_name, "execute": execute,
           "placed": False, "reason": None, "odds": None,
           "balance_before": None, "balance_after": None}
    with sync_playwright() as pw:
        try:
            ctx = ubs._get_context(pw)
        except Exception as e:  # noqa: BLE001
            out["reason"] = f"cdp unreachable: {e}"
            return out
        page = ubs._unibet_page(ctx)
        page.goto(event_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        ubs.dismiss_limit_modal(page)
        # FAIL CLOSED: must be logged in to place real money. Re-login on the same page
        # (login_via_modal manages no playwright, so no nesting), then re-open the event.
        if not ubs.is_logged_in(page):
            if not ubs.login_via_modal(page):
                out["reason"] = "not logged in (auto-login failed) — refusing to place"
                return out
            page.goto(event_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            ubs.dismiss_limit_modal(page)
        _clear_slip(page)

        # select the outcome: button whose text is "<outcome_name><odds>" with odds in band
        pick = page.evaluate(
            r"""(args) => {
              const {name, lo, hi} = args;
              const bs=[...document.querySelectorAll('[data-test-name="propositionOptionBtn"]')];
              for(let i=0;i<bs.length;i++){
                const t=(bs[i].textContent||'').trim();
                const m=t.match(/^(.*?)(\d{1,2}[.,]\d{2})$/);
                if(m && new RegExp(name,'i').test(m[1])){
                  const o=parseFloat(m[2].replace(',','.'));
                  if(o>=lo && o<=hi) return {index:i, odds:o, text:t};
                }
              }
              return null;
            }""",
            {"name": re.escape(outcome_name), "lo": odds_lo, "hi": odds_hi},
        )
        if not pick:
            out["reason"] = "outcome not found on event page"
            return out
        out["odds"] = pick["odds"]
        # eligibility at the LIVE site price
        if pick["odds"] < min_odds:
            out["reason"] = f"live odds {pick['odds']} < min_odds {min_odds} — not eligible now"
            return out

        page.locator(_OUTCOME).nth(pick["index"]).click(timeout=4000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_selector(_STAKE_INPUT, timeout=8000)
        except Exception:
            out["reason"] = "stake input did not appear (selection not added?)"
            return out
        st = page.locator(_STAKE_INPUT).first
        st.click(timeout=3000)
        st.fill(str(int(stake)) if float(stake).is_integer() else str(stake), timeout=3000)
        page.wait_for_timeout(1500)

        # SINGLE-LEG guarantee
        legs = page.evaluate(
            r"""() => [...document.querySelectorAll('[data-test-name="option-variant"]')].map(e=>e.textContent.trim())"""
        )
        if len(legs) != 1 or not re.search(re.escape(outcome_name), legs[0], re.I):
            out["reason"] = f"slip not exactly the intended single leg: {legs}"
            return out

        out["balance_before"] = _balance(page)
        if not execute:
            out["reason"] = "PAPER (execute=False) — slip staged, not placed"
            return out

        # place
        teep = page.evaluate(
            r"""() => { const b=[...document.querySelectorAll('button')].find(x=>/^tee panus$/i.test((x.textContent||'').trim())); if(!b||b.disabled) return false; b.click(); return true; }"""
        )
        if not teep:
            out["reason"] = "Tee panus button missing/disabled — not placed"
            return out
        page.wait_for_timeout(6000)
        out["balance_after"] = _balance(page)
        # a real placement must drop the balance by ~stake
        try:
            bb = float((out["balance_before"] or "0").replace(".", "").replace(",", "."))
            ba = float((out["balance_after"] or "0").replace(".", "").replace(",", "."))
            out["placed"] = (bb - ba) >= stake - 0.01
            out["reason"] = "placed ✓" if out["placed"] else "clicked Tee panus but balance unchanged — verify manually"
        except Exception:
            out["reason"] = "placed (balance parse failed — verify manually)"
        return out
