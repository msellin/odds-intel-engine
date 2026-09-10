# MARKET-VOCAB-CANONICAL — Phase 2 (full canonicalization) — PLAN

**Goal (owner, 2026-09-10):** every market/selection string in the DB uses ONE canonical
vocabulary, enforced by a test. Kills the join-breaking spelling drift (`1X2`/`1x2`,
`O/U`+`over 2.5` / `over_under_25`+`over`, `BTTS`/`btts`) for both OWN placement and the
customer-facing /performance + /picks numbers.

Phase 1 (DONE, committed 46aae53/4cf4a81): `workers/canonical_market.py` = the ONE
vocabulary — `Market`/`Selection` str-enums + `normalize(market, selection)` covering 100%
of the bet universe (incl parametric `*_ou_<line>`, AH line-in-selection, combo). Smoke
`MARKET-VOCAB-ENFORCED` fails on rogue/new stored vocab or re-declared enums. Read-only
eval `scripts/executable_shadow_eval.py`.

## Blast-radius map (established 2026-09-10, read-only survey + DB audit)

**Tables needing backfill (bot-written only):**
- `shadow_bets` ~21k non-canonical + (now 0 unrecognised)
- `simulated_bets` ~1.5k
- `real_bets` ~0.5k
- `odds_snapshots` — **already 100% canonical, DO NOT TOUCH** (905k "unrecognised" are exotic AF
  markets we never bet — out of scope).

**Transformation rules (via `canonical_market.normalize`):**
- `1X2`→`1x2` (selection home/draw/away already fine)
- `O/U`/`o/u` + `over 2.5` → market `over_under_25` + selection `over` (line moves market-side)
- `BTTS`→`btts`, `double_chance` fine
- **AH: market `asian_handicap` already canonical; selection `home -1.5` carries the LINE and
  there is NO line column — PRESERVE the selection, do NOT strip to `home`.**
- **combo: passthrough (family combo, no canonical market).**
- predictions vocab `1x2_home/draw/away` is a SEPARATE namespace — DO NOT migrate.

**Engine readers — low risk:** `settlement.py` already tolerates the mix (its own dispatch at
:337/:1232/:2797-2825 handles `1x2`/`over_under`/`o/u`/`over 2.5`) — it has a DUPLICATE O/U
normalizer to retire in favour of canonical_market. Grading will not break.

**Engine writers — the source of non-canonical:** primarily `workers/jobs/daily_pipeline_v2.py`
(builds bet_data as `1X2`/`O/U`/`over 2.5` → simulated_bets + shadow_bets). Plus `real_bets`
writers (`coolbet_placer.py`, `coolbet_ui_placer.py`). odds_snapshots writers already canonical.

**Frontend (odds-intel-web) — the DOMINANT risk, ~30 sites, several PUBLIC + silent-fail:**
- `lib/upcoming-picks.ts:121` `PRE_MATCH_MARKETS` → `.in("market",…)` → public /picks + `/api/v1/track-record`.
- `lib/engine-data.ts:3412` `CALIBRATED_PUBLIC_MARKETS` → public /performance cohort.
- `lib/engine-data.ts:3844` `PublishedPickMarket` + `byMarket[r.market]` bucketing (unmatched silently dropped).
- Bridges that `return null` silently: `lib/engine-data.ts:1757 _mapPaperToSnapshotKey()`, `lib/real-money-tier.ts:71 calibrationKey()`.
- NOT lowercased (break on case alone): `engine-data.ts:3051`, `app/picks/page.tsx:42`.
- Label maps (4, duplicated, no central map): `app/picks/page.tsx:41 formatMarket`, `components/place-bet-table.tsx:99 fmtSelShort`, `admin/shadow-bots/page.tsx:1780` + `[bot]/page.tsx:621`.
- Forward-compat helpers already list BOTH spellings → survive canonicalization.
- Frontend deploys to prod on push (pm2) — changes are live immediately.


## DECISION 2026-09-10: Option 2 — canonicalize ALL THREE bot tables (the complete, cleaner solution)
Owner asked "which is correct / more complete". simulated_bets is the last place the O/U line lives
in the SELECTION ('over 2.5'); everywhere else it's in the MARKET ('over_under_25'). Option 1 (leave
simulated_bets) makes that split permanent (normalize-on-read forever). Option 2 removes it: ONE
encoding everywhere, and the O/U mirror becomes a straight passthrough like the 1x2 mirror (drop
_convert). Cost = it changes real-money O/U generation → GATE on a before/after pick-equivalence check
that bot_coolbet_ou_model_v1 produces the SAME picks. AH selection keeps its line (no line column) —
'one encoding' applies to the O/U family, AH stays line-in-selection by necessity.

## Staged sequence (readers-first — nothing breaks mid-flight)

1. **Frontend shared normalizer** — new `src/lib/market-vocab.ts` mirroring canonical_market
   (`normalizeMarket`, `marketLabel`). Route the 5 high-risk filters/bridges + 4 label fns
   through it so they accept BOTH legacy and canonical. ADDITIVE — breaks nothing. Deploy.
2. **Engine readers** — route exact-match readers through `canonical_market.normalize`; retire
   settlement's duplicate O/U normalizer. Verify grading unchanged (settlement golden fixture).
3. **Engine writers** — `daily_pipeline_v2` + placers emit canonical via the enums. New rows
   are canonical from here. Verify a fresh pipeline run writes canonical.
4. **Backfill migration** — `supabase/migrations/NNN_canonicalize_bet_vocab.sql` rewrites
   shadow_bets/simulated_bets/real_bets to canonical (1X2→1x2, O/U→over_under_NN + selection,
   BTTS→btts). AH/combo untouched. Idempotent. Verify counts before/after; verify /picks +
   /performance still populate.
5. **Strict test** — flip `MARKET-VOCAB-ENFORCED` to assert the DB contains ONLY canonical
   values for these families (no `1X2`, no `O/U`, no `over 2.5`).

Each stage = its own commit + verification. Do NOT do 3/4 before 1/2 land and are verified.

## Risks / guards
- Public /picks + /performance silently empty if a filter matches only the old spelling and the
  data becomes canonical → step 1 (frontend tolerant) MUST land + deploy before step 4 backfill.
- AH line loss if selection stripped → preserve AH selection.
- predictions `1x2_home` namespace is NOT this vocab → exclude everywhere.
- settlement golden fixture (`scripts/fixtures/settlement_golden.json`) must still pass after step 2.
