# Unibet-Site on the VPS — what's actually needed

**Tested 2026-09-16.** Nothing was installed on the VPS; a throwaway
`--network host` FlareSolverr container ran the probes and was removed.

---

## 1. The blocker is NOT what the docs imply

`WORKFLOWS.md` and the migration map both file `unibet-site-odds` under
"DataDome blocks us". **Measured, that is wrong.** A real Chromium running *on the
VPS* was pointed at `https://www.unibet.ee/betting/odds`:

```
=== NOPROXY  (Hetzner 204.168.199.8, datacenter FI) ===
   nav #1:  HTTP 200   2,444,620 bytes   -> REAL SPA ✅   cookies=2   datadome_cookie=no
   nav #2:  HTTP 200   2,444,620 bytes   -> REAL SPA ✅   cookies=11  datadome_cookie=YES

=== TUNNEL   (95.153.51.90, Telia EE residential) ===
   nav #1:  HTTP 200   2,425,224 bytes   -> REAL SPA ✅   cookies=2   datadome_cookie=no
   nav #2:  HTTP 200   1,446,864 bytes   -> REAL SPA ✅   cookies=9   datadome_cookie=YES
```

**DataDome served the full 2.4 MB SPA to the datacenter IP and issued a
`datadome` cookie on the second navigation.** So none of these is the obstacle:

| Suspected blocker | Verdict |
|---|---|
| IP reputation / datacenter IP | ❌ not a blocker — datacenter loads the full SPA |
| DataDome bot-protection | ❌ not a blocker — issues an identity cookie |
| Linux / containerised Chromium fingerprint | ❌ not a blocker — this *was* Linux Chromium in Docker |

This matches the repo's own Phase-0 matrix, which already said it and was being
misread — `unibet-ui-placer-plan.md:141`: *"FlareSolverr → the Kindred API ❌ HTTP
400 — **FS passes DataDome**, but a bare browser GET lacks the SPA's required XHR
headers/params."* The ✗ was never DataDome. It was the request shape.

## 2. What the blocker actually is

The `contest-page` prices call **must be issued by the SPA itself**. Every way of
making that request *for* the SPA fails, and each fails differently:

| Transport | Result | Why |
|---|---|---|
| Raw-CDP read of the SPA's own `contest-page` response, established tab | ✅ 200, true prices | rides the SPA's real XHR — DataDome token + headers + params |
| Fresh / background CDP tab | ❌ 500/204 | the SPA never fully initialises |
| Injected `fetch()` from the established page | ❌ CORS "Failed to fetch" | Kindred sends no ACAO for injected XHR |
| FlareSolverr → Kindred API directly | ❌ HTTP 400 "Bad request" | a bare GET lacks the SPA's headers/params |

**So the requirement is architectural, not adversarial: a real, persistent,
logged-in browser where the SPA runs and fires its own XHRs, with CDP attached to
read the response bodies.** That is reproducible anywhere — including the VPS.

## 3. Why this is cheaper than it looks: the code already supports it

`unibet_browser_sync.py:CDP_URL` reads an environment variable:

```python
CDP_URL = os.getenv("UNIBET_CHROME_CDP_URL", "http://localhost:9222")
```

And login is **already automated** — `cdp_auto_login` / `login_via_modal` drive
the header modal with `UNIBET_USER` / `UNIBET_PASS` from `.env`. The docstring's
"the operator's REAL logged-in CDP-Chrome" describes *where it runs today*, not a
human-in-the-loop requirement.

**So the transport needs no code change.** Point that env var at a Chrome running
on the VPS and the existing `unibet_odds_feed` raw-CDP capture works unmodified.

## 4. What has to be provided

| # | Need | Notes |
|---|---|---|
| 1 | **Chromium + Xvfb on the VPS** | Currently **none installed** — verified: no chromium, google-chrome, Xvfb, x11vnc. This is the only real install. |
| 2 | **Persistent `--user-data-dir`** | The profile carries the login and the `datadome` cookie across restarts. Losing it means re-login, not failure. |
| 3 | `--remote-debugging-port=9222`, bound to **127.0.0.1 only** | Never expose CDP publicly. Anyone who reaches it owns a logged-in bookmaker session. |
| 4 | **Login** | Automated path exists. First run does it; the profile persists it. A VNC/x11vnc session is a *fallback* for a captcha or a 2FA prompt, not the normal path. |
| 5 | **Session-keep** | The Coolbet renderer-freeze fix (`COOLBET-DAEMON-DEATH-RECURRING`: occluded tab → frozen renderer → lapsed session) applies identically. Reuse it, don't re-derive it. |
| 6 | **Navigation rate limit** | ~8 rapid probe navigations tripped DataDome behavioural throttling (contest-page began 500-ing). The feed is already low-volume by design — candidate fixtures only, not a board sweep. **Keep that.** |
| 7 | **systemd unit + Xvfb display** | Same shape as `oddsintel-scheduler.service`. |
| 8 | **~0.4–1 GB RAM** | Box has 5.9 GB available but is already 1.6 GB into swap. **Gated on the Phase-0 RAM decision** — do not add Chrome to a swapping box. |

## 5. The one genuine risk: account security, not bot-protection

Reachability does **not** need the tunnel — the datacenter IP works. But:

> **An Estonian Unibet account logging in from a Finnish datacenter IP is exactly
> the pattern account-security systems escalate on.** DataDome passing says
> nothing about this; they are different systems with different owners. A locked
> account is a far worse outcome than a job that stays on the Mac.

**Recommendation: route the Unibet Chrome through the WireGuard tunnel anyway**,
even though DataDome does not require it — so the session originates from the
operator's usual Estonian IP, which is what the account has always seen. The same
tunnel §4 of the migration map builds for Coolbet covers this at no extra cost.

This inverts the earlier ordering advice: Unibet is *technically* easier than
expected, but should still follow the tunnel, for a reason that has nothing to do
with bot-walls.

## 6. Scope boundary — this is the READER only

`unibet_odds_feed` is **read-only**: it navigates and reads response bodies, and
never selects an outcome, sets a stake, or places. `unibet_placer` is separate
and out of scope here.

**The real-money placers stay on the Mac** regardless of what happens with this.
Moving a price reader is a reversible operational change; moving the logged-in
identity that places money is not.

## 7. Proposed order

| # | Step | Gate |
|---|---|---|
| 0 | Resolve the RAM question | blocks any Chrome on this box |
| 1 | WireGuard tunnel (migration map §4) | needed by Coolbet anyway; here it is an account-safety measure |
| 2 | Install Chromium + Xvfb, systemd unit, persistent profile, CDP on loopback | the only new install |
| 3 | First login through the tunnel, confirm profile persists a restart | — |
| 4 | Point `UNIBET_CHROME_CDP_URL` at it, run `unibet_odds_feed --bulk --days 2` **dry-run**, diff prices against the Mac's rows for the same fixtures | **this is the acceptance test** — same prices or it has not worked |
| 5 | Register as a scheduler job, retire the launchd plist, update `WORKFLOWS.md` + the Mac-jobs table | ripple-check rule |

**Estimate: half a day**, most of it step 2. Materially cheaper than the "budget a
real spike, unverified" assessment in the earlier migration map — because the
thing assumed to be a security wall turned out to be a request-shape problem, and
the transport code is already env-var driven.

## 8. Correction to earlier docs

`dev/active/vps-migration-map.md` §5 and `vps-inventory-table.md` both say Unibet
is blocked because "DataDome gates on a human-established tab" and call it
unverified/speculative. **Superseded by §1 above:** DataDome admits the VPS, the
gate is the SPA-issued XHR, and the automated login path already exists. Those
sections should be updated when this lands.

---

## 9. Measured 2026-09-23 — LOGIN hits a DataDome captcha from the Zone exit

Steps 2 is built (not yet in the repo): `oddsintel-unibet-chrome.service` runs real
Google Chrome headful under Xvfb, persistent profile `/opt/oddsintel/unibet-chrome-profile`,
CDP on `127.0.0.1:9222`, proxied via `socks5://127.0.0.1:1081` (verified exit
217.146.76.113, AS49604 Zone Media, Tallinn). The SPA loads, prices render
logged-out, and a `datadome` cookie is issued — §1 still holds for **reading the page**.

**Login does not.** `cdp_auto_login` returns rc=5. Instrumented: after the modal
submit the page is replaced by a DataDome **slider captcha** ("Vajalik kinnitamine"),
citing *"automatiseeritud (bot) tegevus teie võrgus (IP 217.146.76.113)"*. The creds
match the Mac's `.env` (hash-compared). So §1's "DataDome is not the obstacle" was
true for page load and **false for the login POST** — the login endpoint is scored
far stricter, and the Zone egress IP is flagged there.

The agent must not solve captchas. Options (owner's call):

| Option | Cost | Risk |
|---|---|---|
| A. Operator solves the slider once over VNC (x11vnc on loopback + SSH tunnel); profile persists the session | ~20 min | Every future re-login (session expiry) may challenge again → feed silently stale until a human slides it |
| B. Exit via the operator's home line (WireGuard, migration map §4) — the IP the account has always logged in from | the tunnel work | Depends on the home router; the thing we were moving away from |
| C. Test whether the feed needs login at all (logged-out `sportsbff` lobby/contest-page) | ~30 min | If yes, the login problem disappears for the READER |
| D. Leave Unibet on the Mac | 0 | Mac stays load-bearing |

Two failed login attempts were made; do not script repeated retries — that is the
behavioural signal DataDome escalates on.

## 10. 2026-09-23 — option C: logged-OUT reading works; shipped that way

Ran `_async_run_bulk(days=1, limit=60)` on the VPS against the logged-out tab,
with `store_book_odds_snapshots` swapped for a capture: 60 DB fixtures → 7
countries swept → 13 matched → 162 rows, 21 fetches, **0 blocks**. Diffed against
the Mac's latest `Unibet-Site` rows (written ~25 min earlier): 138/162 identical;
**11 of 13 fixtures identical on every row**, the other two moved in paired
over/under steps (a line move, not a transport fault). So the prices are public
and login is needed only to place. The VPS reader therefore never logs in
(`run_bulk(login=False)`), which also keeps it off the DataDome captcha path.
