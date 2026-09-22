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

## Steps

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
