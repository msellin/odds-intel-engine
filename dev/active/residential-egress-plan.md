# Residential egress for the VPS — accepted plan

**Task:** VPS-CONSOLIDATION-2026-09-16, phase 1 (the tunnel).
**Decision 2026-09-21:** tunnel first, then move the Epicbet in-play collector.
**Why first:** the VPS can only reach Epicbet through FlareSolverr (`HTTP 403
CF-CHALLENGE` on the live endpoint, measured 2026-09-21), and FS is *already*
dropping 7 of 48 pre-match Epicbet runs (15%, the `EPICBET-FS-500` pattern).
Adding ~33 in-play calls/min to that FS would degrade a working feed on a
placeable book. With a residential egress the VPS reaches Epicbet with **plain
requests, no FS at all** — exactly as the Mac does today.

Analysis this builds on: [`VPS_FEATURE_MATRIX.md`](VPS_FEATURE_MATRIX.md),
[`vps-migration-map.md`](vps-migration-map.md) §4 (the proven A/B).

---

## The honest caveat, stated first

**If the home peer is the MacBook, this does NOT remove the laptop dependency.**
It moves the *code* to the VPS while the *egress* still needs the laptop awake
and connected. That is a real but partial gain:

- ✅ Logic, scheduling, logging, deploys all consolidate onto the VPS
- ✅ Survives Mac reboots better (a service dialing out, not 9 launchd jobs)
- ✅ One place to deploy; the Mac stops being a second deployment target
- ❌ Coolbet/Epicbet egress still dies when the laptop closes

**The full win needs an always-on device at home** — a ~€60 Raspberry Pi, or a
router with WireGuard support. The design below makes that a **config swap, not a
rebuild**: same peer config, same proxy, different hardware.

**Recommendation: build it Mac-first.** It proves the whole chain end-to-end with
no purchase and no waiting, and the Pi becomes a 20-minute swap once it arrives.

---

## Architecture: WireGuard link + SOCKS proxy on the home peer

```
   VPS (Hetzner FI, 204.168.199.8)              HOME (behind NAT, 95.153.51.90)
   ─────────────────────────────                ──────────────────────────────
   wg0  10.8.0.1/24   (server, UDP 51820)  <──  wg0 10.8.0.2  (client, dials OUT,
        │                                        PersistentKeepalive=25)
        │                                            │
   collectors ──socks5://10.8.0.2:1080 ─────────────►│ SOCKS5 proxy bound to wg0
                                                     │
                                                     └──► Epicbet / Coolbet
                                                          via the home ISP
   everything else (AF, Postgres, web, Pinnacle-via-AF)
        └──► straight out the Hetzner path, untouched
```

**Home dials out** because it is behind NAT — no home port-forwarding needed, and
nothing at home has to be reachable from the internet.

### Why a SOCKS proxy and NOT kernel policy routing

The obvious design is `ip rule` + fwmark. **Rejected, for two concrete reasons:**

1. **It cannot select what we need.** All 82 scheduler jobs run in **one process
   as root**, so `--uid-owner` and cgroup marking cannot separate "the Epicbet
   job" from "the API-Football job". The alternative — routing by destination
   prefix — is fragile for Cloudflare/Imperva-fronted hosts whose IPs move.
2. **Blast radius.** A wrong `ip rule` on a box serving a production DB for three
   products, a public website and a PostgREST API can take the whole box off the
   network, including the SSH we would need to fix it.

The proxy selects **per request, by construction**. `requests` takes `proxies=`,
FlareSolverr takes a `proxy` field, and both are already used in this repo. **No
`ip rule`, no fwmark, no iptables NAT on the VPS** — if the tunnel dies, the
affected calls fail and *nothing else on the box changes*. That property is worth
more here than elegance.

### Failure behaviour (designed, not hoped for)

| If | Then |
|---|---|
| Tunnel down | Coolbet/Epicbet calls fail fast and log it. AF, Postgres, web, settlement all unaffected |
| Laptop closes | Same as above — degrades exactly the two feeds, nothing else |
| Proxy down but tunnel up | Same |
| **Never** | A silent fallback to the Hetzner IP. A call that cannot use the residential egress must **fail loudly**, not quietly collect from the wrong identity — that is how `EPICBET-403-FROM-VPS` reported success for six days |

---

## Progress

| Phase | Status |
|---|---|
| 1 — VPS WireGuard server | ✅ **done 2026-09-22.** wg0 10.8.0.1/24, UDP 51820, `ufw allow`. Default route verified identical at every step; SSH never interrupted; no NAT/forwarding/ip-rule. |
| 2 — home peer (Mac) | ✅ **done.** utun4 @ 10.8.0.2, microsocks on 10.8.0.2:1080. Handshake 12ms RTT, 1.5 MB carried, **0 errors 0 drops**. |
| 3 — verify | ✅ **done.** VPS egress through proxy = 84.50.188.194 (home line). `egress_probe --proxy`: **Epicbet CF-CHALLENGE→OK, Pinnacle-guest CF-WAF-RULE→OK**. Mac default route still `192.168.1.1 via en0`, only `10.8/24` via tunnel — **no full-tunnel leak**. |
| 4 — wire Epicbet | ✅ **done.** `OI_RESIDENTIAL_PROXY` in `inplay_epicbet_collector`, inert when unset. Deployed `5cd19fc3`, PySocks 1.7.1 on the VPS. **Transport parity confirmed:** VPS-via-tunnel and Mac-direct returned the *same 4 fixture ids, same 17 markets, identical prices* (Philippines 1.77 / Draw 3.0 / Kuwait 5.0). Latency 320ms vs 100ms per fixture — irrelevant at 45s cadence. |
| 5 — cutover | ✅ **done 2026-09-22 07:21 UTC.** Mac job booted out + plist parked; VPS `oddsintel-inplay-collector.service` started. **Continuous rows across the cutover minute, 0 duplicate groups, longest gap 46s (= the 45s cadence, i.e. no gap).** Same 4 fixtures, same 4/8-rows-per-minute pattern as the Mac. VPS load fell to 1.49. |

## Steps


> **Accuracy correction 2026-09-22.** An earlier note here and in the first commit
> message said "no `net.ipv4.ip_forward`". That describes what *we added*, but it
> reads as a claim about the box and the box does not match it: `ip_forward` is
> already `1` and two `MASQUERADE` rules exist (`172.17.0.0/16`, `172.18.0.0/16`).
> **Those are Docker's**, pre-existing and unrelated. What is true: nothing in
> iptables references `wg0` or `10.8.0.0/24`, and this work added no forwarding or
> NAT rule of its own. Do not "tidy away" the Docker rules — PostgREST and
> FlareSolverr need them.

### Phase 1 — VPS WireGuard server (blast radius: low, but do it carefully)
1. `apt install wireguard-tools` (kernel module already present, 7.0.0-27)
2. Generate server keypair, `wg0` = 10.8.0.1/24, listen UDP 51820
3. `ufw allow 51820/udp` — **SSH on 22/tcp stays allowed throughout**; verify
   before and after with a second live SSH session held open
4. `systemctl enable --now wg-quick@wg0`
5. **No** IP forwarding, **no** NAT, **no** `ip rule` on the VPS

### Phase 2 — home peer (Mac first)
6. `brew install wireguard-tools microsocks`
7. Client config: peer = VPS public key, endpoint 204.168.199.8:51820,
   `AllowedIPs = 10.8.0.0/24` (**only the tunnel subnet** — this must NOT become
   a full-tunnel VPN that routes the Mac's own traffic through Hetzner),
   `PersistentKeepalive = 25`
8. `microsocks` bound to **10.8.0.2:1080 only** (never 0.0.0.0)
9. launchd unit so it survives logout/reboot

### Phase 3 — verify before wiring anything to it
10. From the VPS: `curl --socks5-hostname 10.8.0.2:1080 https://api.ipify.org`
    → must print **95.153.51.90**, not 204.168.199.8
11. Re-run `scripts/ops/egress_probe.py` on the VPS through the proxy — Epicbet
    and Coolbet rows must flip to OK
12. Confirm the Mac's own default route is **unchanged** (no full-tunnel leak)

### Phase 4 — wire up Epicbet, still not cutting over
13. Add an `EPICBET_PROXY` / `OI_RESIDENTIAL_PROXY` env knob honoured by the
    Epicbet client, defaulting to unset (no behaviour change when absent)
14. Run the in-play collector on the VPS **dry-run, writing nothing**, and
    compare its board against the Mac's for the same minute

### Phase 5 — cutover (the double-write guard)
> `inplay_book_quotes` has **NO unique constraint** — only a PK on an
> autoincrement id. Two collectors would silently double-write, and the paper
> bots would double-count into `shadow_bets` against a 3,000-pick target.
> **Pausing the Mac job first is mandatory, not a nicety.**

15. `launchctl bootout` the Mac `inplay-collector`; confirm it is gone
16. Start the VPS collector; confirm heartbeat and row flow
17. Validate: row cadence continuous across the cutover, no duplicate
    `(book_event_id, captured_at)` pairs, `shadow_bets` pick rate unchanged

---

## Acceptance

- `egress_probe.py` on the VPS through the proxy: Epicbet + Coolbet → OK
- The Mac's default route unchanged (no full-tunnel leak)
- In-play rows continuous across cutover, **zero duplicates**
- Pre-match `epicbet_odds_snapshot` failure rate **not worse** than today's 15%
- SSH to the VPS never interrupted

## Explicitly NOT in this phase

Coolbet cutover, Unibet-on-VPS, Claude Code on the VPS, the CrossRank/RAM
decision, and the real-money placer — which stays on the Mac regardless.
