# COOLBET-UI-PLACER — plan

**Filed:** 2026-08-27 · **Goal:** place real bets at Coolbet by driving its UI,
wired to OddsIntel picks, with the operator in the loop for money-committing steps.

## Why a second placer

`coolbet_placer.py` posts to `/s/bets/bets` and depends on Imperva clearance for
every call. Stale cookies returned HTTP 403 and killed the odds feed for 80h
(COOLBET-FEED-WATCHDOG). The operator's real Chrome renders the same pages
natively — Imperva already trusts it — so a UI path has **no cookie-freshness
dependency**. Slower and DOM-fragile; that is the trade. Use it as the resilient
fallback and for supervised placement, not as a replacement.

## Phases

| # | Phase | Status |
|---|---|---|
| 1 | Recover a live session from a cold start | ✅ done — 2 bugs fixed, no SMS needed |
| 2 | Map the UI (search → match → market → betslip) | ✅ done — verified live |
| 3 | Build `coolbet_ui_placer.py`, stage-only by default | ✅ done |
| 4 | One supervised real placement | ⛔ blocked — account balance €0.00 |
| 5 | Decide which bots to automate | ✅ analysed — prematch first, not in-play |
| 6 | Wire picks → UI placement with caps | ⬜ after phase 4 |

## Risks

* **SMS is the hard ceiling on autonomy.** The `reese84` marker survives session
  lapses, but when it expires Coolbet demands SMS and the code goes to the
  operator's phone. No implementation removes this.
* **DOM fragility.** Coolbet's markup is the contract. Selectors are centralised
  at the top of the module so a redesign is a one-place fix, and the smoke test
  pins them.
* **Silent wrong-side bets.** `button-odds-<id>` keys the market, not the
  outcome. Only `1x2` is implemented; OU/AH raise rather than guess.
* **Latency vs in-play.** ~15–20s per bet through the UI. Fine prematch,
  marginal in-play. In-play belongs on the API path.
* **Real money.** `execute=False` by default; `placement_paused` gates the
  execute branch; the operator fires the first live placement.
