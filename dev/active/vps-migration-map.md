# VPS migration map — what moves, what doesn't, and how to unblock what doesn't

**Tested live 2026-09-16** from both hosts with `scripts/ops/egress_probe.py`.
Nothing was installed or left running on the VPS; probe artifacts removed, the
only FlareSolverr session there (`coolbet_prod`) predates this work.

> **Why measured rather than read.** The repo carried three inherited claims that
> were each wrong in part and each cost days: *"Epicbet hits no bot-protection"*
> (six silent days), *"Imperva blocks the Hetzner IP **+ Linux Chrome
> fingerprint**"* (the Mac's own working reader IS Linux Chromium), and
> *"Pinnacle guest API — Mac-only"* (it is blocked from the Mac too, see §3).

---

## 1. The measurement

```
HOST  Mac   egress 95.153.51.90     (Telia EE, residential)
HOST  VPS   egress 204.168.199.8    (Hetzner, Helsinki FI, datacenter)

BOOK             WALL                       MAC direct   MAC+FS   VPS direct   VPS+FS
Epicbet          Cloudflare challenge       OK           n/a      CF-CHALLENGE  ✅ OK
Coolbet          Imperva                    IMPERVA¹     IMPERVA¹ IMPERVA      IMPERVA
Pinnacle-guest   CF WAF rule 1020           TIMEOUT²     ERROR    CF-WAF-RULE   ERROR
Unibet-Site      DataDome                   OK           n/a      ✅ OK         n/a
API-Football     none (control)             HTTP-403³    OK       HTTP-403³     OK
```

¹ **The Coolbet row does not discriminate, and must not be read as evidence.**
The probe makes a *bare, unseeded* FS call. The production path seeds Imperva
cookies harvested from CDP-Chrome into a warm named session (`IMPERVA-SEED-FS`,
`coolbet_session.py:203`) and does warmup navigations first. Unseeded fails from
**both** hosts, so this test says nothing about VPS-vs-Mac for Coolbet. The
evidence for the Coolbet IP block is the 2026-06-26 → 07-03 silent outage and
`epicbet_explorer.py:265`, not this row.

² Not transient — 3/3 attempts, and `www.pinnacle.com` itself also times out
while DNS resolves fine (195.80.107.145). See §3.

³ Expected: no API key sent. Proves host reachability, which is the point.

### The one distinction that explains everything

| Wall type | Example | Beatable by | Beatable by |
|---|---|---|---|
| **Challenge** (JS to execute) | Cloudflare "Just a moment" — Epicbet | ✅ a real browser (FlareSolverr) | — |
| **Firewall rule / reputation** | CF 1020, Imperva | ❌ | ✅ a different egress IP |
| **Session provenance** | DataDome — Unibet | ❌ | ❌ — needs a human-established tab |

FlareSolverr fixes exactly one of these three. That is why it rescued Epicbet
from the VPS and does nothing for Coolbet, Pinnacle or Unibet.

---

## 2. Job-by-job verdict

All 9 loaded launchd jobs + 2 parked ones. Verified against `launchctl list`.

### ✅ Moves to the VPS today — no new infrastructure

| Job | Hits | Why it moves |
|---|---|---|
| `com.oddsintel.inplay-collector` | Epicbet live board | Epicbet-only (`BOOK = "Epicbet"`). VPS+FS returns OK — **tested**. Becomes a scheduler job. |
| `near-kickoff-capture` — **Epicbet third only** | Epicbet | Same. Split the job three ways; this third goes. |
| `coolbet-feed-watchdog` — **DB-judging half** | nothing (reads DB) | Makes no Coolbet HTTP calls at all; it judges staleness from Postgres, which is *on* the VPS. The JWT session-keep half stays — see below. |
| `flaresolverr-keepalive` | local FS | The VPS already runs its own FS; a systemd timer replaces the plist. Trivial. |
| `vps-postgres-tunnel` | — | **Deleted, not moved.** It exists only so the Mac can reach VPS Postgres. Work running on the VPS needs no tunnel. |

**That is ~40% of the job count with zero new infrastructure and no new risk.**
It is worth doing on its own merits, independent of everything below.

### 🔧 Blocked on egress IP — one fix unblocks all of these

| Job | Hits | Exact blocker |
|---|---|---|
| `coolbet-odds-snapshot` | Coolbet fo-tree + sidebets via FS | Imperva refuses the Hetzner IP even through FS |
| `near-kickoff-capture` — **Coolbet third** | Coolbet, plain requests + harvested cookies | same |

**Solution: give the VPS a residential Estonian egress.** See §4.

### 🔒 Blocked on something an IP cannot fix

| Job | Hits | Blocker | Realistic? |
|---|---|---|---|
| `unibet-site-odds` | unibet.ee SPA `contest-page` via raw CDP | **Not IP** — the VPS reaches unibet.ee fine (**tested OK**). DataDome gates on a *human-established logged-in tab*; a fresh/background CDP tab got 500/204 **from the residential IP already** (2026-09-09). | Maybe — see §5 |
| `coolbet-feed-watchdog` — **session-keep half** | CDP-Chrome `ensure_session_live` | Needs the operator's real logged-in Chrome | Follows Chrome |
| `cdp-watch`, `coolbet-cdp-selfheal` | CDP-Chrome health | They exist *only* to babysit that Chrome | Follows Chrome |
| `paused/coolbet-ui-placer` | Coolbet UI, **real money** | `reese84` is **TLS-bound** to the operator's own Chrome; a replayed token is blackholed, not 403'd | **Leave it home** |
| `paused/best-price-router` | Coolbet + Unibet placement | Inherits both placers' constraints | Follows the placers |

---

## 3. Finding: the Pinnacle guest API is broken from BOTH hosts

Last turn I called this the biggest prize for a home tunnel. **That was wrong,
and the direction is inverted.**

```
Mac (Estonia)   → www.pinnacle.com          TIMEOUT 12s  (DNS resolves fine)
                → guest.api.arcadia...      TIMEOUT 12s
VPS (Finland)   → guest.api.arcadia...      HTTP 403, Cloudflare error 1020
```

A silent timeout on *every* Pinnacle host from an Estonian residential line,
with DNS resolving, is **EMTA ISP-level blocking** — Pinnacle is not
EMTA-licensed and Estonian ISPs block it (`BETTING_ARCHITECTURE.md:53`,
`OWN_PATH_VERDICT_2026_09_14.md:133`).

So the two hosts are blocked for **opposite** reasons, and the consequence is
the inverse of what I said:

> **Routing the VPS through the Estonian home line would make Pinnacle *less*
> reachable, not more** — it would inherit the EMTA block. Pinnacle needs a
> **non-Estonian, non-datacenter** egress, which is a third thing entirely and
> not what §4 builds.

Also note: `AF-PINNACLE-NOT-PINNACLE-2026-09-14` ran this probe successfully
from the Mac two days ago (n=92). It does not run today. **Either the EMTA block
is new or something else changed — that is a live regression, and any conclusion
depending on refreshing that comparison is currently un-reproducible.**

---

## 4. Solution for the IP-blocked jobs: WireGuard egress through home

Terminate WireGuard on something always-on at home (router, a ~€60 Pi, or the
Mac) and **policy-route only Coolbet traffic** out through it. Everything else —
Postgres, the 99 scheduler jobs, Epicbet, AF — keeps the fast Hetzner path.

Why this and not a commercial proxy:
- The egress is **the operator's own residential Estonian IP** — the exact one
  Imperva accepts today, not a lookalike.
- **No third party in the path** of an account that holds real money.
- **No T&C problem.** Own connection, own account, EMTA-licensed book, operator
  physically in Estonia. Nothing is misrepresented.
- A commercial EE residential proxy is worse on every axis here: shared IPs carry
  worse reputation than your own line, `visid_incap_*` flags stick to a *visitor*
  for ~8 months so you can silently inherit someone's history, and most
  bookmaker T&Cs prohibit proxies outright regardless of intent.

### Test it in 5 minutes before building anything

SSH can do a reverse SOCKS forward — no WireGuard, no hardware, nothing
installed. **Run from the Mac** (a sandbox classifier blocked me from opening it):

```bash
ssh -N -R 1080 root@204.168.199.8
```

Then, in another terminal, from the VPS — traffic exits via the Mac's Estonian IP:

```bash
ssh root@204.168.199.8 'curl -s --socks5-hostname localhost:1080 -o /dev/null -w "%{http_code}\n" https://api.ipify.org && curl -s --socks5-hostname localhost:1080 "https://www.coolbet.com/s/sbgate/category/fo-tree/et?country=EE" | head -c 300'
```

- **Estonian IP echoed + JSON categories** → the IP was the whole blocker.
  `coolbet-odds-snapshot` and the Coolbet near-kickoff third can move. Build the
  WireGuard version properly.
- **Imperva interstitial** → the fingerprint claim survives after all; we will
  have *measured* it instead of inheriting it, and the proxy route is not worth
  trying either.

⚠️ The bare-FS caveat from §1 applies: to make this a fair test of the production
path it should run through the seeded `coolbet_prod` FS session, not a naive
call. The quick version above is still worth running first — a plain 200 is
already decisive in the positive direction.

---

## 5. Solution for Unibet: a persistent logged-in Chrome on the VPS

This one is *not* an IP problem — **the VPS reaches unibet.ee fine (tested OK)**.
DataDome gates on tab provenance, and a fresh CDP tab fails from the residential
IP too. So the fix is not egress, it is giving the VPS a browser with the same
provenance the Mac's has:

1. Run a persistent Chrome on the VPS under Xvfb/xpra with a real, durable profile.
2. Log in **once, interactively**, over a VNC/xpra session.
3. Keep the tab alive with the **existing** anti-freeze fix — the renderer-freeze
   problem (`COOLBET-DAEMON-DEATH-RECURRING`, occluded tab → frozen renderer →
   lapsed JWT) is already solved in this repo and the fix transfers.

**Honest risks, in order:**
- **Account-security flagging.** An Estonian Unibet account logging in from a
  Finnish datacenter IP is exactly the pattern fraud systems escalate on. This is
  the real risk, and it is not technical. Combining it with the §4 WireGuard
  egress removes it — log in *through* the home tunnel so the session originates
  from the usual Estonian IP.
- DataDome may fingerprint the headless/Xvfb environment even with a real profile.
- Unverified. Unlike §4 this has no cheap decisive test; budget a real spike.

**Recommendation: do §4 first.** If the tunnel works, Unibet-on-VPS becomes much
more attractive (it inherits the correct egress) and much lower risk. Attempting
§5 alone, over a Finnish IP, is the version most likely to get an account locked.

---

## 6. What stays home no matter what

**The real-money placer.** `coolbet-ui-placer` drives the operator's own Chrome,
and `reese84` is TLS-bound to that client. Moving it means moving the logged-in
identity of the account that holds the money, for a convenience gain, onto a box
that also runs three other products and a public web server. Even if §4 and §5
both succeed, **this is the one to leave alone** — and it is already parked and
disarmed (mig 343/354) pending OWN Phase 3.

---

## 7. Order of work

| # | Do | Needs | Direction |
|---|---|---|---|
| 1 | Move the 5 zero-risk jobs (§2 ✅) | nothing | 🤖👥 BOTH — Epicbet in-play + near-kickoff stop depending on the laptop being open; that feeds both stake sizing and published CLV |
| 2 | Run the 5-minute reverse-SOCKS test (§4) | one command, from the Mac | decides everything below |
| 3 | If green: WireGuard + move the 2 Coolbet jobs | a Pi or router at home | 🤖 OWN — the Coolbet price basis every real stake is sized from stops having laptop-shaped holes |
| 4 | Only then consider Unibet-on-VPS (§5) | step 3 done first | 🤖 OWN, speculative |
| — | Log the Pinnacle regression (§3) | — | 👥 PICKS — the AF-vs-real-Pinnacle comparison is currently un-reproducible |
| — | Correct `COOLBET_RUNBOOK.md:23` + `WORKFLOWS.md:147` | after step 2 | both assert a fingerprint cause that the evidence does not support |

**Not yet in `PRIORITY_QUEUE.md`** — this is a design, and per the queue rules a
decision is not a task. Say the word and I will file steps 1–3 as one epic row
(one project, one row) plus the Pinnacle regression as its own.
