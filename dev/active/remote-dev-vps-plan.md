# Remote dev on the VPS + daemon consolidation — research & plan

**Status:** research / design only. Nothing implemented. Written 2026-09-16.
**Question asked:** how powerful is the Hetzner box, can we run Claude Code there
and drive it from a phone, and can *all* our daemons move there?

---

## 1. What the box actually is

Measured live 2026-09-16, not from memory:

| | |
|---|---|
| Provider / location | Hetzner vServer, **Helsinki, FI** (AS24940), IP 204.168.199.8 |
| CPU | AMD EPYC-Genoa, **8 vCPU**, 1 thread/core, AVX-512 |
| RAM | **15.98 GB** total — 6.10 GB available, **1.64 GB of 2 GB swap in use** |
| Disk | 305 GB NVMe, 126 GB used (**44%**), 163 GB free |
| Uptime | 70 days |
| Load avg | **0.44 / 0.85 / 1.30** → ~5–15% CPU |

Plan shape (8 vCPU / 16 GB / 305 GB) is consistent with a CPX41-class Hetzner
Cloud plan. Note Hetzner raised CCX/CPX prices 2.1×–2.7× effective 15 Jun 2026
for **new orders or rescaling**, so a resize re-prices the whole instance at the
new rate — that is a real cost cliff, not a small delta.

### Who lives on it already

It is a **four-tenant box**, not an OddsIntel box:

| Tenant | Processes | Resident |
|---|---|---|
| Postgres 17 (shared by all) | `shared_buffers=4GB`, `max_connections=100` | ~9.5 GB cgroup |
| CrossRank web (Next.js) | `crossrank-web.service` | 2.2 GB |
| OddsIntel scheduler | `oddsintel-scheduler.service`, 99 tasks | 894 MB |
| FlareSolverr (VPS copy) | `oi_local_flaresolverr` docker | 617 MB |
| OddsIntel web (pm2) | `pm2-root` | 386 MB |
| netdata, uptime-kuma, box-ranking, 2× PostgREST, GitHub Actions runner | | ~400 MB |

### Verdict

**CPU: plenty.** 8 EPYC cores running at ~10%. Claude Code is I/O- and
network-bound, not CPU-bound; it will not move that needle.

**RAM: this is the constraint, and it is already stressed.** 1.6 GB of 2 GB swap
is in use *before* we add anything. A Claude Code session is roughly 0.4–1.5 GB
resident depending on context size; server mode defaults to **capacity 32**.
Two or three concurrent sessions on this box will push Postgres pages out to
swap, and Postgres on swap is how a "mystery slow pipeline" incident starts.

**Disk: fine.** 163 GB free, and a repo + node_modules is single-digit GB.

---

## 2. Remote control: which mechanism

Three options exist. They are not close.

### ✅ Recommended — official Claude Code Remote Control (server mode)

`claude remote-control` runs a persistent server in a tmux session on the VPS.
The phone (Claude iOS/Android app) or any browser at claude.ai/code attaches to
it. Session state, filesystem, MCP servers and project config all stay on the
VPS; the phone is a window, not a second brain.

Why it wins here:

- **Outbound HTTPS only. No inbound port.** Nothing new to firewall, nothing new
  to get scanned. On a box that holds the production Postgres for three products
  and a real-money placement path, that property alone decides it.
- **Available on all plans.** No tier gate.
- **`--spawn worktree`** gives each on-demand session its own git worktree. On a
  box where `/opt/odds-intel-engine` is the live deploy target, this is
  load-bearing: it stops a phone session from editing the checkout that
  `systemctl restart oddsintel-scheduler` deploys from.
- **`--permission-mode`** is set at the server, and permission prompts render on
  the phone — so the approval gate survives the move to mobile.

Sketch:

```bash
# on the VPS, in tmux, from a NON-deploy checkout
tmux new -s cc
cd /opt/dev/odds-intel-engine        # a separate clone, NOT /opt/odds-intel-engine
claude remote-control \
  --name "oddsintel" \
  --spawn worktree \
  --capacity 3                       # NOT the default 32 — see RAM above
```

Then `Ctrl-b d` to detach, and the server survives your SSH dropping.

Constraints to respect (from the official docs):
- `ANTHROPIC_BASE_URL` must be unset or point at `api.anthropic.com`.
- `DISABLE_TELEMETRY` / `DO_NOT_TRACK` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
  / `DISABLE_GROWTHBOOK` each **disable Remote Control** — check `settings.json`
  and the shell env on the box before blaming the network.
- Must run `claude` once in the project dir to accept the workspace-trust dialog.
  Trust is never saved for `$HOME`, so always start from a project directory.
- Headless login: `claude` → `/login` prints a URL; open it on the phone or
  laptop, paste the code back. No browser needed on the VPS.

### ❌ Not recommended — Telegram / Discord bridge (OpenACP and similar)

Self-hosted bridges that pipe a chat channel into the Claude Code CLI do exist
and work. They are the wrong choice for *this* box:

- They need a bot token that, in practice, carries **write access to a repo that
  deploys to production on push to main**. That is a new credential with
  real-money blast radius, held by third-party code.
- They typically flatten or auto-approve permission prompts, because a chat
  channel is a poor UI for them. That deletes the gate.
- Remote Control now covers the same use case first-party, so the bridge buys
  nothing but risk.

**But keep Telegram for what it is already good at.** You already alert to
Telegram (deploy drift, settle recon, Coolbet heal commands). That is
*notification and narrow command*, which is exactly right. The rule:
**Telegram tells you something happened; Remote Control is how you act on it.**
Do not merge the two.

### ⚪ Also available — Claude Code on the web (cloud sandbox)

Runs in Anthropic's own sandbox, not your box. Useful for a throwaway
"read this repo and explain X" from the phone. Useless for anything needing the
VPS Postgres, the CDP-Chrome, or the live pipeline — no access to any of it.

---

## 3. Can all the daemons move? — **No. And the reason is not compute.**

The blocker is that two of our books require a **residential Estonian-consumer
IP and a real, human-established, logged-in browser session**. Hetzner Helsinki
is a datacenter IP in the wrong country. This is already proven, not theoretical:
the runbook records a **silent Coolbet outage 2026-06-26 → 2026-07-03** caused by
exactly this.

### Tier A — already VPS-side or moves cleanly

| Job | Why it moves |
|---|---|
| The whole `oddsintel-scheduler` (99 jobs) | already there |
| Epicbet odds ingest | anonymous REST, no auth, no Imperva — already VPS-side by design |
| `com.oddsintel.inplay-collector` | Epicbet-only (`BOOK = "Epicbet"`) → **moves as-is** |
| Epicbet third of `near-kickoff-capture` | same → **splittable out and moved** |
| `com.oddsintel.vps-postgres-tunnel` | becomes unnecessary *for VPS-side work* (still needed while you also work from the Mac) |

### Tier B — cannot move without solving the IP/fingerprint problem

| Job | Hard dependency |
|---|---|
| `coolbet-odds-snapshot` | Imperva blocks Hetzner IP **+ Linux Chrome fingerprint** |
| `coolbet-ui-placer` | CDP-Chrome, real money, logged-in session |
| `coolbet-feed-watchdog` | mixed — the DB-judging half could move, but `ensure_session_live` (JWT heal) needs CDP-Chrome |
| `unibet-site-odds` | raw CDP on the operator's **established** logged-in tab. Three alternative transports were tested live 2026-09-09 and all failed: fresh/background CDP tab → DataDome 500/204; FlareSolverr → Kindred API → HTTP 400 |
| `cdp-watch`, `coolbet-cdp-selfheal`, `flaresolverr-keepalive` | exist *only* to babysit the Mac's Chrome and FS |
| Coolbet + Unibet thirds of `near-kickoff-capture` | inherit the above |

Note the asymmetry: Unibet is the harder one. Coolbet at least has a documented
HTTP path; Unibet-Site works **only** off a tab a human established.

### Tier C — the Estonian-IP question (REVISED 2026-09-16 after a live check)

**First, correcting the premise.** Nothing in this repo has ever faked an IP.
`grep` for `x-forwarded-for`, `cf-connecting-ip`, `x-real-ip`, `spoof`, `geoip`
across `workers/` and `scripts/` returns nothing, and `scripts/smoke_test.py`
carries an assertion titled *"User-Agent must be honest — no spoofing browser
UA"*. What we actually did was different, and the difference decides this.

**What the Epicbet fix really was (EPICBET-403-FROM-VPS-2026-08-29).** Cloudflare
403'd every Epicbet call from the VPS — silently, for six days, while the job
reported success. The fix was to route the calls through **FlareSolverr from the
same Hetzner IP**. No IP change at all. Cookie harvesting explicitly did *not*
work: FS earns a `cf_clearance`, but replaying it from plain `requests` still
403s, because **Cloudflare binds clearance to the TLS fingerprint**. Giving it a
real browser was enough; the IP was never the issue. Verified on the box: 200,
552 categories.

**Pinnacle's guest API is the third case, and we DID use it** —
`scripts/pinnacle_movement_research.py` (PINNACLE-WEEKEND-EXPERIMENT 2026-06-05)
and again for the `AF-PINNACLE-NOT-PINNACLE-2026-09-14` paired test. It hits
`https://guest.api.arcadia.pinnacle.com` with **plain `urllib`, no key, no auth**
— and its own hard constraints read `USER_AGENT — honest identification, no
spoofing` and `NO_PROXY — single-IP, no rotation, no evasion logic`. So no IP was
faked there either; the endpoint is genuinely public. It is *Mac-only* because
the VPS is blocked.

**Measured live 2026-09-16 — and the Epicbet trick does NOT transfer.** Tested
both transports from the VPS:

```
plain curl   → HTTP 403, 4,547B, 42ms
               <title>Attention Required! | Cloudflare</title>
               "Sorry, you have been blocked"          ← error 1020, a WAF RULE
FlareSolverr → status: error
               "Cloudflare has blocked this request.
                Probably your IP is banned for this site."
```

**This is the whole distinction, and it is worth internalising:**

| Wall | Book | What it is | FS from VPS |
|---|---|---|---|
| CF **managed challenge** ("Just a moment") | Epicbet | JS to execute; clearance bound to TLS fingerprint | ✅ solved — 200, 552 categories |
| CF **WAF rule 1020** ("you have been blocked") | Pinnacle | IP/ASN denylist. **There is no challenge to solve.** | ❌ explicit "your IP is banned" |
| **Imperva** | Coolbet | IP + visitor id | ❌ refused even through FS |
| **DataDome** | Unibet | session provenance (human-established tab) | ❌ 3 transports failed 2026-09-09 |

A challenge is solvable by being a real browser. A firewall rule is not solvable
by anything except a different IP. FlareSolverr can only ever fix the first kind
— which is exactly why it rescued Epicbet and does nothing for the other three.

**So the Epicbet trick does not transfer to Coolbet — and that was already
tested.** `epicbet_explorer.py:265` says it outright: *"Imperva refuses the
datacenter IP even through FS, which is why the Coolbet reader had to move to
Mac launchd."*

---

#### 🔑 The new finding: the "Linux Chrome fingerprint" half of our own docs is wrong

`COOLBET_RUNBOOK.md:23` and `WORKFLOWS.md:147` both say Imperva blocks the
**"Hetzner IP + Linux Chrome fingerprint"**. Checked live 2026-09-16:

```
$ docker exec oi_local_flaresolverr uname -a
Linux ... 6.12.76-linuxkit ... aarch64 GNU/Linux
$ docker exec oi_local_flaresolverr chromium --version
Chromium 148.0.7778.178 built on Debian GNU/Linux 12 (bookworm)
```

**The Mac's working Coolbet odds reader is already Linux Chromium in Docker.**
It is not macOS Chrome. The runbook even says so in §2: *"FlareSolverr is a
different browser in Docker with its own fingerprint; the operator's Chrome is a
different client entirely"* — and that Docker Linux browser sweeps 33k rows a
pass through Imperva without complaint.

So between the working Mac-FS and the blocked VPS-FS there are only two
differences: **the egress IP** (residential EE vs Hetzner datacenter FI), and
CPU arch (aarch64 vs x86_64, which no bot-wall gates on). **The IP is almost
certainly the entire blocker for the odds reader.** That reframes this from a
vague hope into a sharp, falsifiable experiment.

⚠️ Two production docs assert the wrong reason. They should be corrected —
flagged, not yet done.

---

#### C1 — WireGuard egress through the home connection ✅ recommended

Stand up a WireGuard endpoint at home (router, a ~€60 Raspberry Pi, or the Mac),
and give the VPS a **policy route so that only Coolbet/Unibet traffic** exits via
home. Everything else — Postgres, the 99 scheduler jobs, Claude Code, all other
books — keeps using the fast Hetzner path.

Why this is the good one:
- The egress IP is **genuinely the operator's own residential Estonian IP** — the
  exact one Imperva already accepts today. Not a lookalike.
- **No third party ever sees the session or the real-money path.**
- **No terms-of-service problem.** It is the operator's own home connection,
  their own account, an EMTA-licensed Estonian book, and they are physically in
  Estonia. Nothing is being misrepresented; the traffic genuinely originates
  where it claims to.
- The thing that must stay always-on at home shrinks from "the MacBook, lid open,
  running nine launchd jobs" to "a Pi that forwards packets".

Realistic reach — note the Pinnacle test above **widened this**:
- ✅ `coolbet-odds-snapshot` (FS-based HTTP) — the hypothesis directly covers it
- ✅ Coolbet third of `near-kickoff-capture` (plain requests + harvested cookies)
- ✅ **Pinnacle guest API — a new and possibly the most valuable one.** Its block
  is purely IP (error 1020, no challenge), so home egress should clear it
  outright. Today the fresh-Pinnacle probe can only ever be a Mac research
  script; on the VPS it becomes a *schedulable feed*. That matters because
  `AF-PINNACLE-NOT-PINNACLE-2026-09-14` measured AF's "Pinnacle" as **+0.81pp
  wider overround** than real Pinnacle (n=92, 95% CI [+0.38, +1.02]), pure lag
  from AF's 3-hourly refresh — and real Pinnacle is the anchor every sharp edge
  and every published CLV number is computed against.
- ❓ `unibet-site-odds` — **IP alone will not fix this.** DataDome refused a
  fresh/background CDP tab (500/204) *from the residential IP already*. Its gate
  is session provenance — a tab a human established — not geography.
- ❌ `coolbet-ui-placer` — the real-money path needs CDP-Chrome holding the
  operator's logged-in profile, and the `reese84` token is **TLS-bound** to that
  client. Moving it is a much bigger question than routing. **Leave it home.**

Test it cheaply before building anything: bring up WireGuard, policy-route the
VPS FlareSolverr's egress, and run one `foCategory` call. It answers in minutes,
and a negative result is as valuable as a positive one.

#### C2 — commercial residential/mobile proxy with an EE exit ⚠️ not recommended

FlareSolverr accepts a per-session `proxy`, so it is a small code change (~€10–50/mo).
But against C1 it is worse on every axis that matters here:
- A **shared residential proxy IP carries worse reputation than your own line**,
  and ours is already sensitive — 143 failed authenticated probes in 12h is on
  record as having fed the wall.
- `visid_incap_*` is long-lived (**observed ~8-month expiry**) and the flag
  attaches to the *visitor*, not to current traffic. A proxy pool that hands you
  a previously-flagged exit inherits its history, and you cannot see that coming.
- **A third party sits in the path of the real-money account.**
- **Bookmaker T&Cs commonly prohibit proxies outright, regardless of intent.**
  Unlike C1, here you genuinely would be routing through infrastructure that is
  not yours — an account-closure and balance-confiscation risk on the one account
  that matters. That is a business risk, not a technical one, and it is the
  owner's call — but it is the reason to prefer C1.

### Recommended target architecture

```
  Hetzner VPS (Helsinki)                    Home / residential EE
  ──────────────────────                    ─────────────────────
  Postgres 17 (all tenants)                 CDP-Chrome :9222 (logged in)
  oddsintel-scheduler (99 jobs)             FlareSolverr docker
  + inplay-collector        ← moved         coolbet-odds-snapshot
  + near-kickoff (Epicbet)  ← split         coolbet-ui-placer  (real money)
  PostgREST ×2, nginx, pm2 web              coolbet-feed-watchdog
  ★ claude remote-control (tmux)            unibet-site-odds
                                            cdp-watch / selfheal / fs-keepalive
        ▲                                   near-kickoff (Coolbet + Unibet)
        │ phone / browser                              │
        └── claude.ai/code ────────────────────────────┘
```

**The honest headline: ~60% of the Mac-side job count can move; the real-money
placement path and the two Estonian book feeds cannot, and should not be forced.**

---

## 4. Phased plan (nothing here is started)

**Phase 0 — make room (do first, it gates everything).**
Decide the RAM question before installing anything. Options:
(a) tune `shared_buffers` down from 4 GB and raise `--capacity` cautiously;
(b) move CrossRank web (2.2 GB) off this box;
(c) resize — but re-prices the whole instance at post-June-2026 rates.
*Do not skip this.* Adding Claude Code to a box already 1.6 GB into swap is how
you get a "mystery slow pipeline" week.

**Phase 1 — Remote Control, read-only-ish.**
Separate dev clone at `/opt/dev/odds-intel-engine` (never the deploy checkout).
`claude remote-control --spawn worktree --capacity 3` under tmux, ideally a
systemd user unit so it survives reboot. Verify from the phone. No daemon moves
yet. Low risk, immediately useful, fully reversible.

**Phase 2 — move Tier A.**
`inplay-collector` → a scheduler job. Split `near-kickoff-capture` into an
Epicbet job (VPS) and a Coolbet+Unibet job (Mac). Both need a smoke test and a
`WORKFLOWS.md` update in the same commit; `docs/COOLBET_RUNBOOK.md` Mac-jobs
table and the Mac-side inventory both go stale the moment this lands — the
ripple-check rule applies.

**Phase 3 — test the WireGuard-to-home hypothesis (C1). Cheap, do it early.**
The 2026-09-16 finding makes this a one-afternoon experiment, not a project:
bring up WireGuard at home, policy-route only the VPS FlareSolverr's egress
through it, run one Coolbet `foCategory` call from the VPS.
Test **Pinnacle's guest API through the same tunnel in the same sitting** — it
is one plain `curl`, it needs no session or cookies, and its block is known to be
pure IP, so it is the cleanest possible probe of whether the tunnel works at all.
- **200 + categories** → the IP was the whole blocker. `coolbet-odds-snapshot`
  and the Coolbet near-kickoff third move to the VPS, the fresh-Pinnacle probe
  graduates from Mac research script to a schedulable anchor feed, and the Mac's
  job list drops to the CDP-bound ones.
- **Still walled** → the fingerprint claim survives after all, we have *measured*
  it rather than inherited it, and C2/proxy is not worth trying either.
Either way, correct `COOLBET_RUNBOOK.md:23` and `WORKFLOWS.md:147` with what the
test actually shows. **The real-money UI placer stays home regardless** — its
`reese84` token is TLS-bound to the operator's own Chrome.

### Direction tags

| Phase | Direction | How |
|---|---|---|
| 0, 1 | **neither** — operator ergonomics | Enables work from the phone. Does not change a pick or a stake. Be honest that it is infrastructure for *us*, not for the product. |
| 2 | **🤖👥 BOTH** | 🤖 OWN: in-play + near-kickoff capture stops depending on the laptop being open, so closing-price coverage stops having holes. 👥 PICKS: same coverage feeds the published CLV numbers. |
| 3 (C1 test) | **🤖👥 BOTH** | 🤖 OWN: if the odds reader moves to the VPS, Coolbet prices stop depending on the laptop being open — that is the price basis every real stake is sized from. 👥 PICKS: the same feed is what makes the published Coolbet-anchored numbers complete rather than full of laptop-shaped holes. |

---

## 5. Open questions for the owner

1. **RAM:** tune, evict CrossRank, or pay the resize? This gates Phase 1.
2. **Is there anything always-on at home to terminate WireGuard?** A router that
   supports it, a Pi, or the Mac staying plugged in. This is the only hardware
   question, and it is small.
3. **Do we accept a third party in the real-money path?** If C1 fails and the
   proxy route (C2) is the only option left, that is an owner decision on
   T&C/account risk — not an implementation detail.
4. **Permission mode on a phone-driven session** touching a repo that
   auto-deploys on push to main — worktree isolation plus default (prompting)
   mode is the conservative default. Anything looser needs a deliberate yes.
