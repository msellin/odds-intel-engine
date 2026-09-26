Parent: PRIORITY_QUEUE.md #191 OWN-BOT-LINEUP-AND-ADMIN-BOTS-CLEANUP-2026-09-26 — sub-item (a2), "page" brainstorm.
DESIGN ONLY: no code was changed. The line-up itself (which bots sit in each block) comes from the audit (a) and the
owner's line-by-line approval (a3). Where this page names bots, the names are examples, not a decision.

# /admin/bots in three blocks: PICKS · OWN · (Instruments & history)

## 0. The problem in one paragraph

Today the page groups bots by how they are BUILT: Forward test, Sharp triggers, Sharp generators, Model · paper,
Model · simulated, In-play, Settings unknown (`FAMILY_ORDER` in `bot-board-model.ts`). The owner does not think in
build families. He thinks in two questions:

1. **PICKS**: *what are my customers getting, and is it any good?* He already understands this, because /performance
   answers it.
2. **OWN**: *where should my own euros go, and is that working?* Nothing on the page answers this today. The answer
   is spread over "Sharp triggers" (one bot per book), "Model · paper" and a real-money card that lists placers,
   not strategies.

Everything else on the page is scaffolding: controls, reference arms and old experiments. It should be out of the
way.

The page therefore asks two questions, in that order. The build family moves into the bot's detail sheet
(Settings tab). It stays useful there for engineers and stops being the page's organising principle.

## 1. Page skeleton

```
┌─ Bots ─────────────────────────────────────────────── [Activity] [How to read] [⋯] ┐
│ Bots | Real money                                    (section tabs — unchanged)   │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ANSWER STRIP (3 plain sentences, same component as every admin page)              │
│  • Customers: 6 bots public — 1 ACTIVE, 4 TESTING, 1 VIP. Next promotion: …       │
│  • Own money: 1 OWN bot live on paper; none beats the market yet (too early).     │
│  • Needs you: 2 items  → (opens the issue list, same as today's bell)             │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ① PICKS — what customers get                         [Picks channel: ON ▾]        │
│    ACTIVE · TESTING · VIP · In development                                        │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ② OWN — where our money goes                         [Kill switch] [Armed: NO]    │
│    market × method grid, then one row per OWN bot                                │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ③ Instruments (collapsed, one line each)  ·  Retired (27) →                       │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* No KPI strip of six cards. The answer strip replaces it: three sentences, not six numbers.
* The block controls live in the header of the block they govern. The Publishing card moves into the PICKS header,
  and the kill switch and armed state move into the OWN header. They are **still two different controls in two
  different places** (I10 holds), but now each sits next to the bots it affects.
* One search box stays, top-right of the page, across all blocks. The facet chips go (see §7).

## 2. Block ① PICKS — what customers get

**Membership:** exactly the /performance line-up. That means every non-retired bot whose status puts it on
/performance (`bot_distribution.on_performance`: TESTING or ACTIVE, VIP included), **plus** the published
forward-test arms. No hand list: see §8 on `PUBLISHED_ARM_BOTS`.

**Sub-groups and their order:** the same four as /performance, with the same titles, so that reading one page
teaches the other:

| Sub-group | Rule (= /performance `groupOf`) | Title shown |
|---|---|---|
| ACTIVE | status `active`, ≥ 5 settled, not VIP | **Active — counts in the headline totals** |
| TESTING | status `testing`, ≥ 5 settled, not VIP | **Testing — own record, not in the headline** |
| VIP | `vip = true` (a public status underneath), ≥ 5 settled | **VIP — paid channel, public once settled** |
| In development | < 5 settled, any of the above | **In development — fewer than 5 settled** (muted) |

EXPERIMENTAL bots whose job is to become a customer pick (for example `bot_consensus_d_v1`) are not on /performance,
so they are not rows here. They get **one footer line** under the block: `3 experimental pick bots — not public →`.
The link opens them in the same table shape. Without this line they would silently fall into "Instruments".

### Wireframe

```
① PICKS — what customers get                                   Picks channel [ON]
   Same bots, same figures as /performance. Figures at €10 flat, best price on all books.

   ACTIVE — counts in the headline totals
   Bot                                    Settled  W / L   ROI     P&L €   CLV (n)        Verdict             Next step            Last pick
   ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   Match result · sharp line              112      51/61   +4.1%   +€46    +3.9% (98)     ▲ Beats the market  —                    2 h ago
     1X2 · SHARP · all books · EV ≥ 3% · ≤ 8% ceiling
       ⓘ the CLV here is against the Pinnacle close — for a Pinnacle-triggered bot it partly
         repeats the trigger (§85). Independent check: +1.2% [−0.0, +2.7] → Can't tell yet

   TESTING — own record, not in the headline
   Consensus · standard (C)               41       19/22   −2.0%   −€8     +2.9% (37)     ◆ Can't tell yet    Promote at 50 · 41   5 h ago
     1X2 + O/U 2.5 · CONSENSUS · all books · grade C
   …

   VIP — paid channel, public once settled
   1X2 NEW+ EV5 ⭐                         …

   In development — fewer than 5 settled   (muted rows)

   3 experimental pick bots — not public →
```

### Columns (PICKS)

| # | Label | Source | Notes |
|---|---|---|---|
| 1 | **Bot** | `bots.display_name` + config line (§5) underneath | Same display name as /performance |
| 2 | **Settled** | `bot_performance.settled` | |
| 3 | **W / L** | `won` / `lost` | |
| 4 | **ROI** | `roi_public` | Flat, best price on ALL publishable books: the /performance figure, unchanged |
| 5 | **P&L €** | `pnl_units_public × 10` | "€10 flat" in the block subtitle |
| 6 | **CLV (n)** | `clv_public`, `clv_n` | Sharp-anchor close: the /performance figure |
| 7 | **Verdict** | §4, on `clv_public` | Same four words as OWN |
| 8 | **Next step** | status rule | `Promote at 50 · 41` (TESTING); `Review flag` (#155); `—` (ACTIVE) |
| 9 | **Last pick** | `last_pick_at` | ⚠ icon when silent > 7 days |
| 10 | ⋯ | row menu | Change status (audited) · Open on /performance · Open detail |

**No € switch in PICKS.** A PICKS bot never stakes our money. It prices off the best book in the world, which we
cannot reach. If a PICKS strategy also deserves our money, that is a **separate OWN bot** on the same method, priced
at the Estonian books. It is not a switch on the PICKS row. This one rule removes the whole "Real-money capable"
confusion from this block.

**No /picks switch either.** Since #155 the status decides distribution, and `show_on_picks` is derived from it by
a trigger. The row shows the status. Changing it goes through ⋯ → Change status, using the same audited
`admin_set_control` route as today.

**The circularity caveat.** For a SHARP- or CONSENSUS-anchored PICKS bot, the public CLV flatters the bot, because
the close it is judged against is its own trigger (§85 mirror trap). §88 makes it worse: that close is often about
2 hours stale. /performance keeps the public figure; that decision was already taken in #159. The admin row adds a
single ⓘ sub-line that gives the independent check (§3) and its verdict. **Owner decision D1:** should the PICKS
verdict chip use the independent figure for sharp- and consensus-anchored bots? The recommendation is **no** for
now. The chip stays on the public figure, so that /admin and /performance agree, and the ⓘ line shows the honest
version. Revisit after #188.

## 3. Block ② OWN — where our money goes

**Membership:** bots with `track = 'own'` (§8). Each is ONE bot per **market × method**, never one per book. It
prices at the best **Estonian-reachable** book (today Coolbet, Unibet-Site, Epicbet and Tonybet; Paf, Ninja and
Optibet join via #101) and records the book on every pick. The per-book view lives inside the bot (the "By book"
tab), because #172's only positive lines were per-book. That is evidence to look at, not a reason to have six bots.

**Judged on independent CLV at the executed price.** The executed price is the pick-time Estonian price
(`odds_own`, `own_basis = 'pick_time_snapshot'`, mig 467). It is compared against the **independent close**: the
≥ 5-book consensus with **Pinnacle AND the bet book excluded** (`leg_clv_sharp.clv_cons`, `cons_status = 'ok'`).
The reasons are §85 and §88:

* Against the Pinnacle close, a Pinnacle-triggered bot's CLV ≈ its trigger edge **by construction**, so it shows
  a positive number whatever the pick was worth.
* Against the bet book's own close, CLV ≈ −margin by construction.
* AF's "Pinnacle close" last changed a median 120 min before kick-off. Our 30-min fetch stamp hides that.
* The independent consensus uses neither Pinnacle nor our book, so it is the only one of the three that can say no.

### Wireframe

```
② OWN — where our money goes                         Kill switch [Running]  Real money [Not armed]
   One bot per market × method. Priced at the best Estonian book (Coolbet · Unibet · Epicbet · Tonybet).
   Judged on independent CLV: our price vs the closing price of 5+ other books (not Pinnacle, not our book).

   At a glance                SHARP                      MODEL
                     ┌──────────────────────────┬──────────────────────────┐
   1X2               │ ⧗ Too early · 12/30      │ ▼ Loses the market       │
   O/U 2.5           │ — no bot (proposed)      │ ◆ Can't tell yet         │
   Asian handicap    │ — no bot (proposed)      │ —                        │
                     └──────────────────────────┴──────────────────────────┘
   (click a cell = scroll to its row; "proposed" cells come from the approved line-up, not a hand list)

   Bot                              Verdict            Indep. CLV            Judged      ROI at our price  Picks 7d  Last pick   Real money
   ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
   OWN · 1X2 · sharp                ⧗ Too early 12/30  +2.1%  [−3.0, +7.2]   12 of 31    −6.0% (grey)      31        40 min ago  Paper   [€ ○]
     1X2 · SHARP · best Estonian book · EV ≥ 3% vs v2 anchor · last 3 h · ≥ 3 books confirm
   OWN · 1X2 · model                ▼ Loses the market −4.8% [−7.9, −1.7]    96 of 210   −11.2% (grey)     4         1 d ago     Locked  [🔒]
     1X2 · MODEL · Coolbet · edge ≥ 10% · odds ≥ 2.80 · home underdogs
```

### Columns (OWN)

| # | Label | Source | Notes |
|---|---|---|---|
| 1 | **Bot** | display name `OWN · <market> · <method>` + config line (§5) | The name IS the cell in the grid |
| 2 | **Verdict** | §4, on independent CLV, **Holm across the OWN block** | Block is a family (#172 used Holm; keep it) |
| 3 | **Indep. CLV** | mean + 95% range of `clv_indep_own` | Header tooltip: "our executed price vs 5+ other books' close; Pinnacle and our book excluded" |
| 4 | **Judged** | `n_indep` of `settled` | **Coverage is shown, never hidden**: 35–41% today (#150). "12 of 31" says the verdict rests on 12 |
| 5 | **ROI at our price** | `roi_own` | Grey below 300 settled (today's rule). Never drives the verdict (#182: ±10pp noise at n 190) |
| 6 | **Picks 7d** | `picks_7d` | |
| 7 | **Last pick** | `last_pick_at` | ⚠ silent > 7 d |
| 8 | **Real money** | `coolbet_placer_bots` + ladder | One word, `Paper` / `Eligible` / `Staking` / `Locked`, plus the existing € switch |
| 9 | ⋯ | row menu | Open detail · By book · Rule history · Pause |

The detail sheet for an OWN bot opens on **By book**: independent CLV, n and ROI per bet book. It also shows
**Guards**: v2 anchor, market split (match-wide), de-vig robustness (Shin and power), 60-min freshness. That is the
list the context doc says lives on the OWN bot. Each guard is shown as how many picks it blocked in 7 days, from the
funnel view (mig 456).

**What "Real money" can say.** `Staking` in red appears only when the whole ladder is green: armed, not paused,
bot eligible and placer alive. That is today's CAN STAKE logic, moved onto the row. The €X totals stay on the
**Real money** section tab. That tab is the money ledger; this block is the strategy verdict.

## 4. The ONE verdict vocabulary

Four words, used in both blocks, the /admin Overview inbox and the bot sheet. Each block's column header names its
yardstick. The words stay the same.

| Chip | Exact label | Rule | Plain-language tooltip |
|---|---|---|---|
| ▲ green | **Beats the market** | n ≥ 30 judged AND the 95% range is entirely above 0 (OWN: Holm-adjusted) | "Our prices were better than where the market closed, by more than luck explains." |
| ▼ red | **Loses the market** | n ≥ 30 AND the 95% range is entirely below 0 | "Our prices were worse than the close. This bot is paying the market." |
| ◆ amber | **Can't tell yet** | n ≥ 30, the range crosses 0 | "Could be skill, could be luck. Needs more picks." |
| ⧗ grey | **Too early · n/30** | n < 30 judged | "Fewer than 30 picks with a usable close. No verdict." |

These replace today's labels (`Beats close`, `Loses to close`, `Inconclusive`, `Too early`, `No CLV`). Changes:

* **"close" becomes "the market".** The owner does not have to know what a closing line is to read the chip.
* **"Inconclusive" becomes "Can't tell yet".** The current page already uses that phrase in its facet.
* **`No CLV` is dropped.** In-play betting was retired on 2026-08-21, and no row on the page needs it. The in-play
  rig, if kept, is an instrument, and instruments carry no verdict (§6).
* **|t| ≥ 2 becomes "95% range excludes 0"**, which is the same test for a normal mean. The owner reads it off the
  range column instead of a t number. `t` moves to the sheet.
* **Silent, locked and review flag are states, not verdicts.** They appear as a small icon next to Last pick or
  Real money and never replace the chip.
* **Junk control:** its comparison (`≈ junk control`) moves out of the row and into the forward-test bot's sheet.
  It is a pre-registered detail of one test, not a page-wide concept.

One file defines these: `verdict.ts` (a new, pure module; today it is `VERDICT_UI` in `bot-row.tsx` plus
`VERDICT_FACET` in `bots-board.tsx`, which are two copies).

## 5. A bot's config in one plain line

Template, fixed order, generated from structured fields. No free text:

```
<MARKET> · <METHOD> · <BOOKS> · <FLOOR> [· <WINDOW>] [· <FILTER>]
```

| Slot | Values | From |
|---|---|---|
| MARKET | `1X2`, `O/U 2.5`, `Asian handicap`, `BTTS`, `1X2 + O/U 2.5` | `market_key` (new, canonical) |
| METHOD | `SHARP` (Pinnacle + exchange anchor), `MODEL` (our model), `CONSENSUS` (5+ books) | registry `anchor` |
| BOOKS | `best Estonian book`, `Coolbet`, `all books` | `bot_config.books` collapsed: the Estonian set means "best Estonian book"; the full publishable set means "all books" |
| FLOOR | `EV ≥ 3%`, `edge ≥ 10%`, `odds ≥ 2.80` | `edge_floor` + unit, `odds_floor` (the existing `floorShort`/`fmtOddsBand`) |
| WINDOW | `last 3 h`, `≥ 45 min before` | `window` (new) |
| FILTER | `home underdogs`, `grade C` | `filter_label` (new, ≤ 3 words, registry-owned) |

Examples:

* `1X2 · SHARP · best Estonian book · EV ≥ 3% · last 3 h`
* `O/U 2.5 · MODEL · Coolbet · edge ≥ 8% · odds ≥ 1.80`
* `1X2 + O/U 2.5 · CONSENSUS · all books · grade C`

The existing `identityLine()` already concatenates market · books · floor · odds band. This design extends it with
METHOD, WINDOW and FILTER, and makes METHOD always the second word, because the method is the owner's mental split.
The long `one_liner` prose stays in the sheet's Settings tab.

## 6. Block ③ Instruments & history (minimal)

Another agent is arguing the case for Instruments, so the page design only reserves the smallest possible slot.

```
③ Instruments — reference arms that measure something; never published, never staked      [show 3 ▾]
   Junk control            the forward test's noise floor                   writing · 2 h ago
   Unified gate 1X2        flat-10% model-edge hypothesis                   writing · 1 d ago
   In-play slow-state rig  in-play price lag (no closing line)              silent 9 d  ⚠

Retired (27) →
```

* **Collapsed by default.** One line each: name · what it measures (≤ 8 words) · alive or silent. No verdict, no ROI
  and no switches. An instrument answers a question for another bot. If it deserves a verdict, it is a PICKS or
  OWN bot.
* **If the owner rejects the block** (the fallback): instruments go under the same link as retired bots, as
  `Retired & instruments (30) →`, and block ③ disappears. Nothing else on the page changes. That is the test of a
  minimal design.
* **Retired bots are reached** through `Retired (N) →`, which is today's `?view=retired` tab turned into a link.
  It opens a flat list grouped by the track the bot had (PICKS / OWN / instrument / pre-track). Each row shows name,
  retired date, **why** (`bots.retired_reason`, already in `RetiredInfo`), and final figures on its block's
  yardstick. Retired PICKS bots also stay in /performance "work done" (#157), which is unchanged.

## 7. What to delete from the current page

| Delete | Where | Why |
|---|---|---|
| The seven family sections + `FAMILY_ORDER` / `FAMILY_INFO` / `FamilyIcon` / accents | `bot-board-model.ts`, `bot-row.tsx` | The build-family grouping is the mess; family moves to the sheet's Settings tab as one line |
| "Family" facet chip, "Verdict" facet chip, quick views `On /picks`, `Real-money capable` | `bots-board.tsx`, `board-toolbar.tsx` | The blocks ARE the filter. Keep search and the `Bot issues` quick view (as the answer strip link) |
| Sort menu (7 keys) | `board-toolbar.tsx`, `bot-sort.ts` | Fixed order: sub-group, then verdict, then n. Column-header sort only where cheap |
| Fleet KPI strip (6 cards: Kill switch, Real money, Active bots, Verdicts, Picks · 7 days, Bot issues) | `fleet-strip.tsx` | Replaced by the answer strip + block headers |
| Publishing card and Real-money card as standalone cards | `fleet-controls-card.tsx`, `real-money-card.tsx` | Moved into the PICKS and OWN block headers (the components are reused, not rewritten) |
| Capability icons (Published / Telegram / Real-money capable / Real money ON) | `bot-row.tsx` `CapIcons` | Published and Telegram follow from the status (#155). Money is its own OWN column |
| Per-row `/picks` switch | `bot-controls-cell.tsx` | Status decides distribution. Use ⋯ → Change status |
| Forest bar on a shared −8 … +8 scale and the 12-week strip in the ROW | `bot-viz.tsx` usage in `bot-row.tsx` | Kept in the sheet. The row shows the number + range in text (one idea per cell) |
| Junk-control dashed line and `≈ junk control` on every row | `bot-row.tsx` `ControlLine`, `controlLineFor` | Belongs to the forward test's own sheet |
| MC-CLV anywhere on the board, and its How-to-read paragraph | `how-to-read.tsx`, `METRIC_*` | Admissible on no block. It stays in the sheet's "Other metrics" |
| `Settings unknown` section | `FAMILY_INFO.unknown` | Becomes an issue item ("<bot>: settings missing from the nightly export"). A track-less bot fails smoke (§9) |
| `In-play` section and the `No CLV` verdict | `FAMILY_INFO.inplay`, `VERDICT_UI.noclv` | No in-play betting since 2026-08-21 |
| How-to-read panel: 8 bullets | `how-to-read.tsx` | Replaced by 4 lines: the two yardsticks, the four verdict words, "€ = one of six gates", "grey ROI = too few picks" |

What is **not** deleted: the bot sheet (tabs), the ledger loader, controls-context / arming / confirm dialogs, the
Activity sheet, the `Real money` section tab, the issue list and review flags, and the URL-state approach
(`?bot=&tab=`). They carry over unchanged.

## 8. Data shape

### 8.1 Registry: where the grouping comes from

The grouping must come from `workers/registry/bot_registry.py`, which is the single source that already has a drift
test. It must not come from a hand list on the web.

```python
# bot_registry.py — new fields on BotSpec (all required for an active bot; drift-tested)
TRACK_PICKS = "picks"            # a customer-picks strategy (public or on its way: EXPERIMENTAL pick bots too)
TRACK_OWN = "own"                # tests where OUR money goes: one per market × method, Estonian books, shadow_bets
TRACK_INSTRUMENT = "instrument"  # measures something for another bot; no verdict, never published, never staked

@dataclass(frozen=True)
class BotSpec:
    ...
    track: str                 # TRACK_*
    market_key: str            # '1x2' | 'ou25' | 'ah' | 'btts' | 'multi' — canonical, for the OWN grid
    window: str | None = None  # 'last 3 h', '>= 45 min' — plain words, for the config line
    filter_label: str | None = None  # '≤ 3 words', e.g. 'home underdogs', 'grade C'
```

* **Method** is not a new field. It is `anchor` (`sharp` / `model` / `consensus`). The OWN grid's columns are
  `SHARP` and `MODEL`, and CONSENSUS appears only if an OWN consensus bot is ever approved.
* **Track vs status.** Track says what a bot is FOR. Status (`bots.maturity_label` + `vip`) says how far a PICKS
  bot has got. Only `track='picks'` bots have a meaningful public status. An OWN bot is always EXPERIMENTAL on the
  status axis (never public), and that is enforced by smoke, not by the UI.
* **`family` stays** in the registry and in `bot_config` as the engineering fact (which writer, which ledger). It is
  demoted from the page's grouping to the Settings tab.

### 8.2 `bot_config`: one migration, four columns

```sql
ALTER TABLE bot_config
  ADD COLUMN track        text CHECK (track IN ('picks','own','instrument')),
  ADD COLUMN market_key   text,
  ADD COLUMN window_label text,
  ADD COLUMN filter_label text;
```

`scripts/export_bot_config.py` writes these from the registry (`_COLS` + `_row`). A bot with no registry spec gets
`track = NULL` and becomes a "settings missing" issue, which replaces the `unknown` family.

Fix while there: `bot_ah_sharp_v1` is missing from the export (tasks.md "quick fix"). With a required `track`, the
drift test catches this class of bug.

### 8.3 Independent CLV: one definition, in the ledger view

This is new, and it follows the §86 rule: one shared computation plus a parity test.

* SQL function `indep_clv(odds numeric, cons_status text, p_close_cons numeric)` returns `odds × p_close_cons − 1`
  when `cons_status = 'ok'`, else NULL. It follows the `anchor_clv()` pattern from mig 454, and nobody re-types the
  CASE.
* `bot_ledger` gains `clv_indep_own = indep_clv(odds_own, c.cons_status, c.p_close_cons)` and
  `clv_indep_public = indep_clv(odds_public, …)`. Legs with `own_basis = 'our_books_unverified'` get NULL. A price
  we cannot prove we could have taken does not get judged.
* `bot_performance` gains `clv_indep_own`, `clv_indep_own_sd`, `n_indep_own` and the same three for `_public`.
  `bot_scoreboard` projects them (se, t, lo95, hi95).
* New private view `bot_indep_by_book` (bot, bet book → n, mean, sd, roi_own) for the OWN sheet's By book tab.
* Holm is applied **in the web model** over the OWN block's rows at render time. It is a pure function in
  `verdict.ts`, since the family is "the OWN rows on this page". Holm stored in SQL would go stale whenever the
  line-up changed.

### 8.4 One read for the board

`loadBotBoard()` keeps its current reads and adds `bot_config.track/market_key/window_label/filter_label`, the new
indep columns and `bot_distribution` (status, vip, label). The page groups like this:

```ts
block = cfg.track === 'own' ? 'own'
      : cfg.track === 'instrument' ? 'instrument'
      : cfg.track === 'picks' && onPerformance(dist) ? 'picks'
      : cfg.track === 'picks' ? 'picks_experimental'   // the footer line
      : 'issue';                                        // no track = settings missing
sub  = block === 'picks' ? performanceGroupOf(row) : null;  // SAME function /performance uses
```

**The PICKS line-up must be one function shared with /performance.** Today /performance builds its list from
`botsDB` filtered by status, **plus a hand-typed `PUBLISHED_ARM_BOTS` array** in `performance/page.tsx` (5
forward-test arms). This design moves the list into `src/lib/performance-lineup.ts` (`getPerformanceLineup()` +
`performanceGroupOf()`), used by both pages. The arm list should come from the registry
(`family = forward_test AND track = picks`) or from `bot_config.published`, not from a literal. This is a
prerequisite for the page (step d), not a separate task.

## 9. Smoke tests to pin

| ID | Pins | How |
|---|---|---|
| `ADMIN-BOTS-THREE-BLOCKS` | The board groups by `track`, not by family | Source: `bots-board.tsx` has no `FAMILY_ORDER`; the grouping reads `cfg.track` |
| `PICKS-BLOCK-EQUALS-PERFORMANCE` | Same bots, same sub-groups on both pages | Source: both pages import `getPerformanceLineup` / `performanceGroupOf`; no `PUBLISHED_ARM_BOTS` literal remains |
| `REGISTRY-TRACK-COMPLETE` | Every active BotSpec has `track` + `market_key`; every active `bot_config` row has non-null `track` | Registry import + DB (like `SYSTEM-MAP-REGISTRY-NOT-DRIFTED`) |
| `OWN-TRACK-SHAPE` | `track='own'` ⇒ ledger `shadow_bets`, books ⊆ the Estonian set, status never public, **at most one active bot per (market_key, anchor)** | Registry + `bot_config` + `bot_distribution` |
| `OWN-JUDGED-ON-INDEPENDENT-CLV` | The OWN verdict reads `clv_indep_own*` only, never `clv_anchor*` / `clv_public` / `clv_mc` | Source inspection of `verdict.ts` / OWN row |
| `INDEP-CLV-ONE-DEFINITION` | `clv_indep_*` is computed only via `indep_clv()`; the `bot_performance` aggregate equals a direct recompute from `leg_clv_sharp` on a sample | Migration grep + parity query (like `ONE-ROI-CLV-PARITY`) |
| `ONE-VERDICT-VOCAB` | The four labels are defined once in `verdict.ts` and imported by /admin/bots, /admin Overview and the sheet; old strings (`Beats close`, `Inconclusive`, `No CLV`) are gone | Source grep |
| `CONFIG-LINE-FROM-FIELDS` | The config line is built by `identityLine()` from structured fields; METHOD is the second slot | Unit-style source check on a fixture row |

Retire or adjust the smoke tests that pin what is deleted (family sections, `ControlLine` on rows, capability
icons). Grep smoke_test.py for `FAMILY_ORDER`, `VERDICT_UI` and `CapIcons` when building. The CLAUDE.md admin
section says there are about 65 smoke pins on admin URLs, and those URLs do not change.

## 10. Owner decisions this design needs

| # | Decision | Recommendation |
|---|---|---|
| D1 | PICKS verdict chip: public CLV (agrees with /performance) or independent CLV (honest for sharp/consensus bots)? | Public on the chip + independent on the ⓘ line; revisit after #188 |
| D2 | Keep block ③ Instruments, or fold it into `Retired & instruments →`? | Owner's call after the instruments brainstorm; the page works either way |
| D3 | OWN verdict with Holm across the block (stricter) or raw 95% range? | Holm: the block is a family of tests, and #172 already used it |
| D4 | Does an EXPERIMENTAL pick bot show as a footer line in PICKS, or live under Instruments? | Footer line in PICKS; it is a picks candidate, not an instrument |

## 11. Build order (for step d, after a3 approval)

1. Registry fields + export + migration (8.1, 8.2). No UI change; smoke `REGISTRY-TRACK-COMPLETE`.
2. `indep_clv()` + ledger/performance columns (8.3). Parity smoke.
3. `performance-lineup.ts` shared by both pages (8.4). /performance must render identically, so diff it before and
   after.
4. `verdict.ts` + `configLine()`; then the three blocks; then the deletions (§7) in the same web commit.
5. Docs: CLAUDE.md admin paragraph, `docs/SYSTEM_MAP.md` (track column), `ANALYSIS_GOTCHAS` §85 pointer to
   `indep_clv()`.
