# COOLBET-UI-PLACER — path and findings

**Date:** 2026-08-27 · **Status:** staged a bet end to end; placement blocked on €0 balance

Working record of how a bet actually got staged at Coolbet by driving the UI,
what broke, and what the evidence says about which bets are worth automating.
Written so a new session can continue from "continue" plus this file.

---

## 1. Starting position — everything was already built, and dead

The operator's framing was "open a browser, log in, find the game, place the
bet". Reading the repo first changed the task from *build* to *commission*:

| Step | Already existed |
|---|---|
| log in | `coolbet_session.py` — JWT via `/s/auth/login`, ~30 min auto-refresh |
| find the game | `coolbet_placer.py:828` `search_coolbet_event` + `fuzzy_match_event` |
| place the bet | `coolbet_placer.py:1332` `_place_bet_api` → POST `/s/bets/bets` |
| connect to picks | `coolbet_placer.py:243` `load_qualified_bets` |

Two facts established before touching anything:

* **No bet has ever actually been POSTed.** All 837 `real_bets` rows carry
  `ticket=None`. `_place_bet_api` has never returned a real ticket, so the
  payload it builds is unproven against production.
* **The daemon was dead.** Last tick 2026-08-23 12:55 (clean SIGTERM); the tick
  before errored `Connection refused` (FlareSolverr down). JWT expired >1h,
  `placement_paused=true` from a 7-error self-pause, 1 qualifying pick waiting.

## 2. FlareSolverr is the wrong dependency on the Mac

FS exists for the **VPS**, which has no browser and no human. The Mac has both,
and `COOLBET_NO_FS=true` (`coolbet_session.py:405`) already runs plain-requests
against Imperva cookies harvested from the operator's real Chrome over CDP
(`coolbet_browser_sync.py:396`). Decision: **drop FS from this path.** Real
browser for the challenge and cookies, plain HTTPS after.

## 3. Two bugs that killed unattended login

**Bug 1 — infinite recursion in the Playwright factory.** `_sync_playwright_factory`
had `except ImportError:` calling *itself* instead of importing vanilla
playwright. `patchright` is not installed, so this raised `RecursionError`
after 982 frames. Unattended login had been dead since patchright went absent,
and it failed with a stack trace that looks nothing like "login broken".
Fixed — falls back as its docstring always claimed.

**Bug 2 — CDP needs an open tab.** With zero pages, `connect_over_cdp` dies on
`Browser.setDownloadBehavior: Browser context management is not supported`.
`cdp_auto_login` only *reuses* a Coolbet tab, it never opens one, so recovery
from a truly cold Chrome dead-ends. Worked around by opening a tab via
`/json/new` first (`extract_jwt_from_cdp(allow_open_new_tab=True)`).
**Not yet fixed in code** — see tasks.

## 4. Login worked, unattended, no SMS

```
reusing existing Coolbet tab
Filling login form… / Clicking Logi sisse…
✓ logged in — page is now at .../et/sport/recommendations
```

Verified genuine: `localStorage['cbauth']` held a well-formed JWT,
`exp` 2026-08-27T07:15:19Z (~30 min life, matching the documented cadence).

**Why no SMS:** the `reese84` Imperva marker survived in the profile even though
the session had lapsed, so Coolbet treated it as a known device. That marker is
what makes unattended recovery viable — and it will eventually expire and force
one interactive SMS. **SMS is the hard ceiling on autonomy** and no approach
removes it: the code goes to the operator's phone.

Login-form DOM verified live before running it (all three selectors still match):

* `input[type=email][name=email]` (also `data-test="input-email"`)
* `input[type=password][name=password]`
* `form button[type='submit']` → resolves to exactly **1** element

The stale comment feared the header "Logi sisse" would be clicked instead. It
cannot: the header button and all three cookie-banner buttons are `type=submit`
but sit **outside** any `<form>`. Six submit buttons on the page, the
form-scoped selector matches one. Hit-tested with `elementFromPoint` — the
cookie banner does not intercept either field or the button.

## 5. The API path is blocked; the UI path is not

`search/v2` returned **HTTP 403 (Imperva)** in NO_FS mode. Cause: NO_FS sources
cookies DB-first (max 2h old, daemon-harvested) then env; the daemon had been
dead 4 days and there are no env cookies, so it sent **zero**. Harvested six
fresh cookies from CDP-Chrome and persisted them
(`visid_incap_723517`, `incap_ses_1099_723517`, `nlbi_723517`,
`nlbi_723517_2147483392`, `reese84`, `uuid`).

Operator then directed: **use the UI, not the API.** This also sidesteps
Imperva entirely — real Chrome renders the page natively, so the UI path has
**no cookie-freshness dependency at all**. That is its main virtue; DOM
fragility is the cost.

## 6. DOM map (verified live, FK Partizan v Getafe, 2026-08-27)

| Purpose | Selector |
|---|---|
| search box | `input[name="sportSearch"]` — ph. "Otsi sündmusi, tiime või mängijaid" |
| result link | `a[href^="/et/sport/match/<id>"]` |
| odds button | `button[data-test="button-odds-<marketId>"]` |
| stake field | `input[name="yourStake<marketId>"]` |
| place button | `button[data-test="button-place-bet"]` — "TEE PANUS" |
| keep selections | `input[name="keepMySelections"]` |

**Three traps found:**

1. `button-odds-<id>` keys the **market, not the outcome**. 1X2 home/draw/away
   all share one id; Over and Under share one. The outcome is identified by the
   label text in the button's parent. Anyone treating that id as an outcome id
   will bet the wrong side.
2. **Every market renders twice** (main block + sticky summary), so all
   locators need `.first` or they are ambiguous.
3. The stake field is a **React-controlled input backed by an on-screen keypad**
   (`1 2 3 4 5 6 7 8 9 OK 0 .`). A `fill()` that appears to succeed can be
   silently dropped — always read the value back. Same shape as
   [[feedback_silent_failures]].

## 7. Bet staged successfully — blocked on funds

Pick: **FK Partizan v Getafe**, 1x2 home, KO 2026-08-27 19:00 UTC,
`bot_v10_all`, model odds 3.55, edge +14%.

Coolbet UI search matched it first hit; the page showed **22:00 local = 19:00
UTC**, confirming correct fixture identification. 1X2 market read as
FK Partizan **3.30** / Viik 3.40 / Getafe 2.20.

Betslip after selection + €1 stake:
`FK Partizan 3.30 Lõpptulemus (1X2) FK Partizan - Getafe`, possible win €3.30.

**Two blockers:**

* **Balance €0.00.** Slip renders *"Sinu panus ületab vaba saldot"* and
  `button-place-bet` is **disabled**. Operator is aware and will fund.
* **Odds drift.** Pick carried 3.55, Coolbet shows **3.30** — the pick was
  qualified against a price no longer available. This is exactly what
  `captured_odds` vs `actual_odds` exists to catch, and is now enforced by a
  `max_odds_drop_pct` guard in the new module.

Stake was cleared afterwards; nothing committed.

## 8. What shipped

`workers/automation/coolbet_ui_placer.py` — UI-driven placer:
search → fuzzy match (with squad guard) → open → read prices → resolve outcome
→ odds-drift check → select → stake (**verified by read-back**) → read slip →
optionally place.

Safety: `execute=False` by default; `place()` is unreachable unless the caller
passes `execute=True` **and** `placement_paused` is clear **and** the slip's
place button is enabled. Only `1x2` is implemented — OU/AH need Estonian line
matching (`Üle/Alla 2.5`) and are deliberately left raising rather than guessed,
because a wrong line is a silently wrong bet ([[feedback_odds_quality_recurring]]).

Carries a partial fix for **COOLBET-SQUAD-GUARD** (open): `pick_event` reuses
`_squad_tag` from `epicbet_explorer`, so a reserve/youth side cannot match a
first team on the UI path. The API path in `coolbet_placer.fuzzy_match_event`
is **still exposed**.

## 9. Which bets are worth automating — the in-play investigation

**CLV is invalid for in-play.** `clv_pinnacle_devig` compares the taken price to
Pinnacle's *pre-match close*; an in-play bet at minute 22 with a goal already
scored is a different market. The nonsense values prove it: `inplay_c` +134%,
`inplay_j` +74%, `inplay_n` +66%. So the CLV gate that makes prematch decidable
at n≈78 does not apply, and ROI needs ~17,000 bets for ±2%
(`docs/ANALYSIS_GOTCHAS.md` §8). **No in-play bot has a decisive record.**

**The odds were real — the strongest finding.** All 55 in-play picks that
reached `real_bets` were placed **at Coolbet** with `actual_odds` on every one:

| bot | n | captured → actual | slippage |
|---|---|---|---|
| inplay_l | 19 | 1.69 → 1.65 | −2.4% |
| inplay_e | 7 | 1.83 → 1.79 | −1.7% |
| inplay_i | 4 | 3.42 → 3.41 | +0.4% |
| inplay_n | 9 | 4.09 → 4.14 | +1.6% |

Gap: `recommended_bookmaker` is **NULL on all 1,246 settled in-play bets** —
outside those 55, no book attribution exists at all.

**`inplay_l` is the one the operator remembered** — 129 settled, +10.8% sim ROI,
69.8% hit, avg odds 1.62, and **+13.7% real ROI on 19 Coolbet bets**. Sim and
real agreeing that closely is meaningful.

**But it is decaying:**

| window | inplay_l | inplay_e |
|---|---|---|
| May–Jun 14 | +24.5% (n=48) | +6.6% (n=223) |
| Jun 15–Jul 14 | +11.5% (n=33) | −55.4% (n=7) |
| Jul 15+ | **−3.4%** (n=48) | −15.2% (n=17) |

`inplay_e`'s +3.39% headline is entirely its first window; retired 2026-07-31
for the documented fixture-mix shift. `inplay_l` is one step behind on the same
curve. `inplay_o`'s +186% is 22 of 25 bets in the first window at 4.26 odds —
variance. `inplay_m` is the only one trending up (+1.6 → +50.6 → +43.0), n=33.

**The UI path is too slow for in-play.** Picks land at minute **15–33**
(`inplay_l`), 25–30 (`inplay_e`). The UI path costs ~15–20s per bet. In-play
prices move on every attack; the −2.4% slippage above was a *human* placing at
leisure, not a bot racing a live market.

### Recommendation

Automate **`bot_coolbet_value_v1` (prematch) first**, not in-play:

* prices **at Coolbet by construction** — no shop-vs-place mismatch
* no timing pressure, so UI latency is irrelevant
* **CLV is valid**, so it is gateable at n≈78 rather than n≈17,000
* single-digit picks/day — the right volume for supervised first runs

In-play is phase 2, on the **API** path rather than the UI, and only after
`inplay_l` is re-validated on a fresh window. Its last 48 bets are negative.

---

## Next steps

1. Operator funds the account (payment details are the operator's to enter).
2. One supervised €0.50–1.00 placement via `stage_bet(execute=True)`.
3. Fix the cold-Chrome dead-end in `cdp_auto_login` (open a tab, don't only reuse).
4. Fresh-window read on `bot_coolbet_value_v1` before wiring picks → placement.
5. Do **not** clear `placement_paused` until the 7-error cause is understood.
