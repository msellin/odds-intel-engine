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
Coolbet          Imperva                    IMPERVA¹     IMPERVA¹ IMPERVA      IMPERVA¹
   └─ VPS + warm FS session through a residential-EE tunnel → REAL DATA ✅ (§4)
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

| Job | Hits | Exact blocker | Status |
|---|---|---|---|
| `coolbet-odds-snapshot` | Coolbet fo-tree + sidebets via FS | egress IP only | **✅ UNBLOCKED — proven working from the VPS through a residential tunnel, §4** |
| `near-kickoff-capture` — **Coolbet third** | Coolbet, plain requests + harvested cookies | egress IP only | ✅ same fix |

**Solution: give the VPS a residential Estonian egress — now confirmed, not
hypothesised. See §4.**

### 🔒 Blocked on something an IP cannot fix

| Job | Hits | Blocker | Realistic? |
|---|---|---|---|
| `unibet-site-odds` | unibet.ee SPA `contest-page` via raw CDP | **Not IP** — the VPS reaches unibet.ee fine (**tested OK**). DataDome gates on a *human-established logged-in tab*; a fresh/background CDP tab got 500/204 **from the residential IP already** (2026-09-09). | Maybe — see §5 |
| `coolbet-feed-watchdog` — **session-keep half** | CDP-Chrome `ensure_session_live` | Needs the operator's real logged-in Chrome | Follows Chrome |
| `cdp-watch`, `coolbet-cdp-selfheal` | CDP-Chrome health | They exist *only* to babysit that Chrome | Follows Chrome |
| `paused/coolbet-ui-placer` | Coolbet UI, **real money** | `reese84` is **TLS-bound** to the operator's own Chrome; a replayed token is blackholed, not 403'd | **Leave it home** |
| `paused/best-price-router` | Coolbet + Unibet placement | Inherits both placers' constraints | Follows the placers |

---

## 3. Pinnacle — CORRECTED 2026-09-16

An earlier pass claimed Pinnacle was blocked from both hosts. **Wrong** — that
measurement used the system resolver and hit an EMTA DNS sinkhole.

```
Telia EE resolver  → 195.80.107.145                    EMTA sinkhole → timeout
1.1.1.1 / 8.8.8.8  → 104.18.42.200, 172.64.145.56      real Cloudflare
Mac → real CF IP + SNI  →  HTTP 200, 400KB of matchups  ✅
```

The EMTA block is **DNS poisoning only**; a public resolver bypasses it. Pinnacle
works from the Mac today, which is how the n=92 paired test ran. No regression.

**Consequence:** the VPS block is Cloudflare WAF 1020 **on the datacenter IP**;
the residential IP is not CF-blocked. So the §4 tunnel plus a public resolver
should unlock Pinnacle on the VPS as well — one extra `curl` to confirm, and it
would make the fresh-Pinnacle anchor a schedulable feed rather than a Mac-only
research script.


## 4. ✅ CONFIRMED: the IP is the entire blocker — Coolbet works from the VPS

**Tested 2026-09-16 with a reverse SOCKS tunnel (`ssh -N -R 1080`), which made the
VPS egress as the operator's Estonian residential IP. Same box, same FlareSolverr,
same Linux Chromium, same warm-session shape. ONE variable: the egress IP.**

```
=== NOPROXY  (Hetzner 204.168.199.8, Helsinki datacenter) ===
    warmup nav #1      HTTP=200  bytes=1,066    -> CHALLENGE (not solved)
    warmup nav #2      HTTP=200  bytes=967      -> CHALLENGE (not solved)
    API (warm)         HTTP=200  bytes=892      -> CHALLENGE (not solved)

=== TUNNEL   (95.153.51.90, Telia EE residential) ===
    warmup nav #1      HTTP=200  bytes=6,078    -> WALL           (the known §2 wall)
    warmup nav #2      HTTP=200  bytes=963      -> CHALLENGE
    API (warm)         HTTP=200  bytes=170,237  -> REAL DATA ✅
```

Reproduced twice at exactly 170,237 bytes. Payload verified as the genuine
`fo-tree`: 30 top-level categories, keys `children/depth/fullSlug/id/name/slug`,
containing `Premier League`, `matches_count`, `Jalgpall`, `Inglismaa`.

### What this settles

1. **Imperva blocks Coolbet on the IP alone.** Not the fingerprint.
2. **`COOLBET_RUNBOOK.md:23` and `WORKFLOWS.md:147` are wrong** where they say
   *"Hetzner IP **+ Linux Chrome fingerprint**"*. The successful request above was
   made by Linux Chromium in Docker on the Hetzner box. Only the IP moved. The
   fingerprint half has never been the obstacle, and it should be struck.
3. **The warmup pattern is what earns the cookies**, and it works through a tunnel
   — a fresh visitor identity, no production cookie replay needed.

### Method notes that matter for anyone re-running this

- **A cold, unseeded FS call is challenged from BOTH IPs** (999 vs 1000 bytes,
  identical markers). So is a plain `curl` (965 vs 964). Anyone testing without a
  warm named session + warmup navigations will measure nothing and wrongly
  conclude the tunnel failed. **That was the first result this investigation got,
  and it was a false negative.**
- **Deliberately did NOT seed the production Imperva cookies from the DB.** They
  are bound to the live visitor identity; replaying them from a second IP risks
  flagging a `visid_incap_*` that lasts ~8 months and would take down the working
  Mac feed. A fresh visitor per session is the safe experiment, and it was
  sufficient.
- FlareSolverr on the VPS runs on a **bridge** network, so it cannot reach an
  `ssh -R` loopback forward. The test used a throwaway `--network host` container
  on port 8192, since removed.

### Build it properly: WireGuard, not a standing SSH tunnel

The SSH reverse tunnel proved the point but is not the production shape — it dies
with the terminal and depends on the Mac. Terminate WireGuard on something
always-on at home (router, a ~€60 Pi, or the Mac) and **policy-route only Coolbet
traffic** through it. Everything else — Postgres, the 99 scheduler jobs, Epicbet,
AF — keeps the fast Hetzner path.

Why this and not a commercial proxy:
- The egress is **the operator's own residential Estonian IP** — now proven to be
  the one Imperva accepts.
- **No third party in the path** of an account that holds real money.
- **No T&C problem.** Own connection, own account, EMTA-licensed book, operator
  physically in Estonia.
- A commercial EE residential proxy is worse on every axis: shared IPs carry worse
  reputation than your own line, `visid_incap_*` flags stick to a *visitor* for
  ~8 months so you can silently inherit someone's history, and most bookmaker T&Cs
  prohibit proxies outright regardless of intent.

**Note the FS-must-reach-the-tunnel constraint** — with WireGuard the routing is
at the kernel level so the bridge-network problem disappears, but it is exactly
the kind of detail that produces a mysterious failure if the container ends up on
a network that bypasses the policy route. Verify with the egress check first.

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
| 2 | ~~Run the reverse-SOCKS test~~ | — | ✅ **DONE 2026-09-16 — green, §4** |
| 3 | **WireGuard + move the 2 Coolbet jobs** | a Pi or router at home | 🤖 OWN — the Coolbet price basis every real stake is sized from stops having laptop-shaped holes |
| 4 | Only then consider Unibet-on-VPS (§5) | step 3 done first | 🤖 OWN, speculative |
| — | Log the Pinnacle regression (§3) | — | 👥 PICKS — the AF-vs-real-Pinnacle comparison is currently un-reproducible |
| — | **Correct `COOLBET_RUNBOOK.md:23` + `WORKFLOWS.md:147`** | ready now | both assert a "Linux Chrome fingerprint" cause **disproven** in §4 — the working request was Linux Chromium on the Hetzner box |

**Not yet in `PRIORITY_QUEUE.md`** — this is a design, and per the queue rules a
decision is not a task. Say the word and I will file steps 1–3 as one epic row
(one project, one row) plus the Pinnacle regression as its own.
