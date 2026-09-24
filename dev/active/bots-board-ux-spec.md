Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# /admin/bots — UX audit + buildable design spec (2026-09-24)

Scope: `odds-intel-web/src/app/(app)/admin/bots/{page.tsx,bots-board.tsx}` + `src/lib/bot-board.ts`.
Audited on the local fixture preview (`http://localhost:3055/admin/bots`, `.dev-fixtures/bot-board.json`,
89 scoreboard rows / 21 active / 88 retired) at 1440×900 and 375×812.
Owner verdict: *"it looks ugly. it's like a book with no images and just small text."*

Builder: implement §2–§13. Reviewer: screenshot-check against §14 (acceptance checklist).
No new npm dependencies — everything below is Tailwind + inline SVG + `lucide-react` (already
installed). recharts 3 is installed but is **not** needed; the forest bar and strip are ~40 lines of SVG
each and render server-side-friendly with no hydration cost.

---

## 1. Audit — what is wrong today

Measured, not guessed (DOM + screenshots):

| # | Problem | Evidence |
|---|---|---|
| A1 | **The answer is off-screen.** At 1440 px the table is **2,248 px wide in a 1,214 px box**. Verdict, metric, ROI, settled, last pick and capabilities all sit in the hidden right half. The first screen shows Bot / Markets / Books / Floor — the four least important columns. | `table.scrollWidth=2248`, wrapper `1214` |
| A2 | **Scrolling right loses the row identity.** Once you scroll to the verdict, the bot name column and the family header text are scrolled away — you see "negative" next to nothing. No sticky first column. | screenshot after `scrollLeft=1100` |
| A3 | **Mobile is unusable.** At 375 px only the bot name and a truncated "over_under_2…" are visible; the verdict is ~1,800 px to the right. | mobile screenshot |
| A4 | **Floor column repeats machine text on every row**: `0.03 (multiplicative P x odds - 1)` ×6, and for `bot_v10_1x2` a 120-char tier string `T1 1x2_fav 0.08 1x2_long 0.12 ou 0.08; T2 …` that alone pushes the table wider than the screen. | fixture `edge_floor` |
| A5 | **`*` as a books value** (7 of 21 active bots) — meaningless without the source string, which is only a hover title. | `books: ["*"]` |
| A6 | **No visuals at all.** The single most important fact — mean ± CI vs zero, and vs the −3.0% junk-control floor — is rendered as `mc-CLV -2.9% ± 1.4%` + an 11 px grey line `t -4.13 · n 62`. You cannot compare 21 bots by eye. | MetricCell |
| A7 | **The noise floor is never shown.** `control_junk_anchor` is −3.04% ± 0.31% (n 571, t −19). `bot_sharp_1x2_v1` is −2.88% ± 1.37% and wears a red **"negative (t ≤ −2)"** chip — but it is statistically **indistinguishable from the junk control** (Δ +0.16 pp, t≈0.2). That is the real reading of the forward test and the page never says it. | fixture |
| A8 | **Four verdict states collapse to two looks.** "no evidence yet" is used both for n=4 (too early) and for n=257, t=1.09 (enough data, inconclusive). Operators cannot see which bots are *close* to readable. 7 of 21 active bots are n<30, 7 are inconclusive at n≥30. | verdictOf() |
| A9 | **Tiny grey text everywhere.** Secondary lines are `text-[11px] text-muted-foreground` (oklch 0.63 on 0.07 bg). Family blurbs are one long 12 px grey sentence per section. Chips are `text-[11px] leading-none` with 1 px borders — "Collecting" appears on all 21 rows and carries zero information. | classes |
| A10 | **What matters is hidden, what doesn't is shown.** Hidden: an "active" bot that has been **silent 11 days** (`bot_coolbet_ou_model_v1`, `writing_7d=false`) looks identical to a live one; picks/7d (2,429 fleet-wide) is only in the drawer; the rule version is 11 px grey. Shown needlessly: "Collecting" chip on every row, the raw floor formula, the `+N pending` sub-line under Settled. | fixture |
| A11 | **Family sections are weak.** The family header is a `bg-muted/20` table row with a run-on sentence; the Control family is a whole section for one bot that is really a *reference line*, not a peer. Family order puts model_sim between control and model_shadow. | FAMILY_ORDER |
| A12 | **Fleet status is a sentence, not a status.** "Placement: paused · Real money: off · 21 active · 21 collecting · 5 published · 0 real-money enabled" in one 14 px line. Real money ARMED would be one red word in that line. | FleetStatus |
| A13 | **Drawer is a config dump.** 16 key/value lines, then a 3-column gate table with raw source paths (`scripts/publish_picks_forward_test.py:37`) in every row, `books_excluded` as a JSON array string. Odds range reads `any – 4`. Record section is 4 run-on grey lines. | drawer screenshot |
| A14 | **Recent picks table shows a UUID prefix as "Match"** (`e41e29b8`) and a **Pin-CLV column that is always "—"** for forward-test / shadow-only bots. In-play picks show CLV columns although in-play is never judged on CLV. | drawer |
| A15 | **Retired tab is a flat 88-row list**, 20 of them "no ledger rows" (never fired). No grouping, no "had data" filter, reasons clipped to 2 lines of grey. | retired screenshot |
| A16 | Page intro paragraph (3 lines of grey prose) above the fleet status pushes the content down; it explains methodology the page itself should make obvious. | page.tsx |

Style reference points on the site that *do* work (reuse them): the `/performance` hero
(`performance-hero.tsx`: mono uppercase `tracking-[0.2em]` micro-labels, big numbers, a
`gap-px` tile strip on `bg-white/[0.08]`), the method pills in `performance-leaderboard.tsx:142`
(model = sky, sharp = violet, consensus = teal), and the bordered `bg-card rounded-xl` panels.

---

## 2. Page structure (desktop ≥ 1024 px)

`max-w-7xl` stays. Order top → bottom:

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ ← Admin                                                                               │
│ Bots                                              [ⓘ How to read this]  data 15:27 UTC │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ FLEET STRIP (6 tiles, gap-px)                                                         │
│ ┌────────────┬────────────┬────────────┬──────────────────┬───────────┬─────────────┐ │
│ │PLACEMENT   │REAL MONEY  │ACTIVE BOTS │VERDICTS          │PICKS · 7D │NEEDS A LOOK │ │
│ │⏸ Paused    │● Off       │20 +control │▇▇▇▇▇▇▇▇▇▇▇▇▇     │2,429      │1 silent bot │ │
│ │placer idle │0 enabled · │5 published │0 beat · 4 lose · │across 20  │coolbet O/U  │ │
│ │            │2 capable   │5 telegram  │7 inconcl · 7 early│bots      │model · 11 d │ │
│ └────────────┴────────────┴────────────┴──────────────────┴───────────┴─────────────┘ │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ [Active 21] [Retired 88]            filter: (All) (Published) (Real-money capable)   │
│                                      sort: family ▾                                   │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ ▌FORWARD TEST  · pre-registered, public on /picks · judged on mc-CLV       5 bots    │
│ ┌ reference ───────────────────────────────────────────────────────────────────────┐ │
│ │ Junk control   −3.0% ±0.3  n 571   ── this dashed line appears in every plot ──  │ │
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
│ BOT                 VERDICT        mc-CLV (95% CI)  −10%   ┊−3  0      +5%   12 WEEKS   N / ROI       LAST   CAPS     │
│ Sharp-line · 1×2    ● Loses        −2.9% ±1.4       ├────●──┊──┤│          ▂▅▇▆▃▅    62 · +7.5%    6 h    ◉ ✈      │
│ sharp_edge_v4 ·     ≈ junk control  t −4.1                     │                                     50/7d          │
│ 1×2 · any book                                                                                                      │
│ Sharp-line · O/U    ○ Too early    −2.9% ±2.0       (faded bar)        22/30 ███████░░   22 · −13.8%   2 h    ◉ ✈      │
│ ...                                                                                                                  │
│ ▌SHARP TRIGGERS · one per book, fire above the Pinnacle line · mc-CLV      5 bots    │
│ ...                                                                                   │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

Remove the intro paragraph (A16). Replace it with a `How to read this` button (lucide `Info`)
that toggles a small panel (≤ 5 bullet lines, §9 copy). Right-aligned: "data as of" = max
`bot_config.exported_at` rendered `HH:MM UTC` (relative in title).

### 2.1 Family order and names

`forward_test → sharp_trigger → sharp_generator → model_shadow → model_sim → inplay → unknown`.
**`control` is not a section.** `control_junk_anchor` renders as the *reference strip* at the top
of the Forward-test section (and its dashed line appears on every mc-CLV plot on the page). It is
still clickable → drawer.

| family | Section title | One-line subtitle (≤ 70 chars, `text-sm text-muted-foreground`) | Accent (left bar + icon) |
|---|---|---|---|
| forward_test | Forward test | Pre-registered, public on /picks. Never re-scored. | teal-400 · `FlaskConical` |
| sharp_trigger | Sharp triggers | One per book — fire when the book beats Pinnacle. | violet-400 · `Crosshair` |
| sharp_generator | Sharp generators | Any placeable book above the de-vigged Pinnacle line. | violet-400 · `Radar` |
| model_shadow | Model · paper | Our model, priced at books we can bet. | sky-400 · `Brain` |
| model_sim | Model · simulated | Our model, best accessible price (behind /performance). | sky-400 · `Brain` |
| inplay | In-play | Picks during the match. No closing line → no CLV. | amber-400 · `Timer` |
| unknown | Unresolved config | The export could not describe these — fix export_bot_config.py. | amber-500 · `AlertTriangle` |

Section header: `flex items-center gap-2` — 3 px coloured left bar (`border-l-[3px]`), icon 16 px,
title `text-base font-semibold`, subtitle, and right-aligned `"{n} bots · judged on {metric pill}"`.
Metric pill: `rounded-full border px-2 py-0.5 text-[11px] font-mono uppercase` —
`MC-CLV` / `PIN-CLV` / `NO CLV`. Hover title = the long `METRIC_LABEL`.

Within a family, sort by **verdict group then |t| desc**: Beats → Loses → Inconclusive → Too early →
Silent (writing_7d=false) last. (Current sort is last_pick_at, which puts noise first.)

---

## 3. Fleet strip (KPI tiles)

One `section.overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.08]` containing
`grid grid-cols-2 gap-px md:grid-cols-3 lg:grid-cols-6`; each tile `bg-card px-4 py-3`.
Tile anatomy (copy the `/performance` Metric pattern):
label `font-mono text-[11px] uppercase tracking-widest text-muted-foreground`,
value `text-2xl font-semibold tabular-nums`, sub `text-xs text-muted-foreground` (≥ 12 px — never 11).

| Tile | Value | Sub | States |
|---|---|---|---|
| **Placement** | `⏸ Paused` / `▶ Running` / `? Unknown` | "placer idle" / "placer live" | paused = sky-400 text + `PauseCircle`; running = amber-400 + `PlayCircle`; unknown = muted + `HelpCircle` |
| **Real money** | `Off` / `ARMED` / `Unknown` | `"{place_enabled} enabled · {place_capable} capable"` | **ARMED: whole tile `bg-red-500/10 ring-1 ring-red-500/50`, value red-400 + `ShieldAlert`**, must be impossible to miss. Off = muted + `ShieldOff`. |
| **Active bots** | `20` + `+ control` suffix in muted text | `"{publish} published · {telegram} on Telegram"` | — |
| **Verdicts** | stacked bar (below) | legend text `"0 beat · 4 lose · 7 inconclusive · 7 too early · 2 no CLV"` (today's fixture) | counts over active bots **excluding the control** |
| **Picks · 7d** | `sum(picks_7d)` formatted `2,429` | `"across {count picks_7d>0} bots"` | — |
| **Needs a look** | count of issues | first issue text, `+N more` opens a popover list | 0 → value `All clear` in emerald-400 + `CheckCircle2`; ≥1 → amber-400 + `AlertTriangle` |

Verdict stacked bar: `h-2.5 w-full rounded-full overflow-hidden flex` with one segment per verdict,
width ∝ count, colours from §5 verdict tokens, 1 px `bg-background` gap between segments. Each
segment has a `title`. The text legend underneath is mandatory (not colour-only).

"Needs a look" rules (computed client-side from existing fields — no new data):
1. active bot with `writing_7d === false` → "`{display}` silent · last pick {ago}"
2. `family === "unknown"` → "`{name}` has no resolvable config"
3. `place_enabled && fleet_placement_paused === false && verdict !== 'beats'` → "`{name}` stakes real money without a positive verdict" (red)
4. `clv_outlier_n > 0` → *not* an issue (shown in drawer only).
5. scoreboard / config / capabilities read error → "`{view}` unreadable".

---

## 4. Bot row (desktop) — the core visual

Use a CSS grid per row (not `<table>`), so it can reflow to a card on mobile without a second
component. Desktop template (fits in 1,216 px content width with no horizontal scroll):

```
grid-cols-[minmax(220px,1.4fr)_132px_minmax(260px,1.6fr)_120px_112px_72px_76px]
  Bot            Verdict   CLV forest bar            12 weeks  N · ROI   Last  Caps
```

Row: `px-4 py-3 border-t border-border hover:bg-accent/40 cursor-pointer focus-visible:ring-2
ring-ring rounded-none`, rendered as a `<button>` or `role="button" tabIndex=0` with Enter/Space → open
drawer. Column header row `font-mono text-[11px] uppercase tracking-widest text-muted-foreground`,
`sticky top-0 bg-background/95 backdrop-blur z-10` per section.

### 4.1 Bot cell
- Line 1: display name `text-sm font-medium text-foreground` (fallback: humanised bot_name, see §7).
- Line 2 (`text-xs text-muted-foreground`, **not mono, not 11 px**): the **identity summary**:
  `"{markets} · {books} · {floor}"`, e.g. `1×2 · any book · edge ≥ 3%`, `O/U 3.5 · Coolbet · edge ≥ 8%`,
  `1×2 · Coolbet, Unibet · edge ≥ 10% · odds ≥ 2.80`. Truncate with `truncate` + full string in title.
- Line 3 only when present: rule-version pill for pre-registered bots —
  `rounded bg-teal-500/10 text-teal-300 font-mono text-[11px] px-1.5` → `v4 · 2026-09-15` (§7),
  plus `+8 earlier picks not pooled` in muted text.
- bot_name (`bot_sharp_1x2_v1`) moves to the drawer header and to the row's `title`. It is not shown
  in the row (it doubled every row's height).

### 4.2 Verdict cell (5 states, icon + word + colour — never colour alone)

| state | rule | label | icon (lucide) | token |
|---|---|---|---|---|
| beats | n ≥ 30 and t ≥ 2 | **Beats close** | `TrendingUp` | emerald-400 text, `bg-emerald-500/10 border-emerald-500/40` |
| loses | n ≥ 30 and t ≤ −2 | **Loses to close** | `TrendingDown` | red-400, `bg-red-500/10 border-red-500/40` |
| inconclusive | n ≥ 30 and \|t\| < 2 | **Inconclusive** | `Minus` | amber-300, `bg-amber-500/10 border-amber-500/30` |
| early | n < 30 or t null | **Too early** + progress | `Hourglass` | muted-foreground, `bg-muted/40 border-border` |
| noclv | metric = lift | **No CLV** | `Timer` | muted-foreground, dashed border |

Chip: `inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium`.
Under the chip, `text-xs tabular-nums text-muted-foreground`: `t −4.13` (beats/loses/inconclusive).
For **early**: a 64 × 4 px progress bar `n/30` (fill muted-foreground/60) + `22 / 30` text. For n = 0:
`0 / 30 · none settled yet`.

**Forward-test only — the control comparison line** under the verdict, `text-xs`:
Δ = mean − control_mean; se_Δ = √(se² + se_c²); t_Δ = Δ / se_Δ.
- |t_Δ| < 2 → `≈ junk control` in amber-300 with `Equal` icon (this is the honest headline for
  `bot_sharp_1x2_v1` today: −2.9% vs −3.0%, t_Δ ≈ 0.2).
- t_Δ ≥ 2 → `above junk control` emerald; t_Δ ≤ −2 → `below junk control` red.
- Only when the bot has n ≥ 30 and the control has a mean. Only for the `forward_test` family
  (the control is drawn from the forward-test pool; comparing other ledgers to it is an owner decision
  — see §15 Q1). Everything here derives from `bot_scoreboard` fields already loaded.

### 4.3 CLV forest bar (the "image")
Inline SVG, width 100% of the cell, height 28 px, `role="img"` with an `aria-label` like
`"mc-CLV −2.9%, 95% interval −4.2% to −1.5%, n 62"`.

- **Shared x-domain per metric across the whole page** so rows are comparable: default
  **−12% … +12%** (fraction −0.12 … 0.12). Ticks at −10, −5, 0, +5, +10 drawn only in the column
  header (a 16 px mini-axis SVG, labels `text-[10px] fill-muted-foreground`).
- Zero line: 1 px solid `stroke-foreground/40`, full height.
- **Junk-control line** (mc-CLV plots only): 1 px dashed `stroke-amber-400/70`
  (`strokeDasharray="3 3"`) at control_mean, with a 1 px-tall band of `fill-amber-400/10` for the
  control's own 95% CI. Header shows a tiny legend: `┊ junk control −3.0%`.
- CI whisker: horizontal line from mean − 1.96·se to mean + 1.96·se, 2 px, with 6 px end caps.
  Point: circle r = 4.
- Colour = verdict token (emerald / red / amber / muted). **early** → whisker and dot at 40% opacity
  and a dashed whisker, so an n = 4 interval of ±9% does not shout.
- Clamp: if an end falls outside the domain, draw it to the edge with a 5 px arrowhead and put the
  real value in the aria-label/title (e.g. `bot_consensus_b_v1` ±9.2%).
- To the left of the bar (inside the same cell, `w-[76px] text-right`): mean as
  `text-sm font-semibold tabular-nums` coloured by sign (≥0 emerald-400, <0 red-400 — but muted if
  early), and `±1.4` in `text-xs text-muted-foreground`.
- **mean null / n = 0** → no SVG; text `no settled CLV yet` (muted, italic off).
- **In-play (noclv)** → no SVG; show `hit {won/(won+lost)}% · ROI {roi}` and the tag
  `No closing line — judged on lift (not computed yet)` in `text-xs text-muted-foreground`.
  Never render CLV for in-play, even though the fixture carries `clv_mc_mean = −43.7%` for
  `bot_inplay_slowstate_v1` (that number is meaningless and must not appear anywhere on the page).
- Pin-CLV families (model_sim) use the same bar without the junk-control line; the metric pill in the
  section header says `PIN-CLV`.

### 4.4 "12 weeks" activity strip — needs one new loader (§8)
Inline SVG 112 × 28. 12 bars, one per ISO week (oldest left). Bar height ∝ settled picks that week
(scale: per-row max, min visible height 2 px for weeks with ≥ 1 pick; empty week = a 1 px baseline tick).
Bar colour = sign of that week's mean admissible metric:
emerald-500/80 if mean > 0, red-500/80 if < 0, `muted-foreground/40` if the week has < 5 CLV picks
or the bot is in-play. So the strip shows **activity and direction at once**, and small weeks cannot
flash red/green. Title per bar: `"w/c 15 Sep · 50 picks · mc-CLV −3.1% (n 48)"`.
Under the strip `text-xs text-muted-foreground`: `{picks_7d}/7d`.
Until the loader exists: render the strip placeholder as `{picks_7d} picks · 7d` text only (no fake bars).

### 4.5 N · ROI cell
`text-sm tabular-nums`: `62` settled (foreground) · `+7.5%` ROI coloured by sign, muted when
settled < 30. Second line `text-xs text-muted-foreground`: `+2 pending` only if pending > 0.
Title: "ROI on a flat 1-unit stake per pick — comparable across bots, not their real staking."

### 4.6 Last pick cell
`6 h` / `25 min` / `2 d` (§7). Colour: foreground if < 24 h, amber-300 if 24 h – 7 d,
**red-400 + `AlertCircle` if > 7 d on an active bot** (e.g. `bot_coolbet_ou_model_v1`, 11 d).
Title = full UTC timestamp.

### 4.7 Capability icons (Caps cell)
Drop "Collecting" from the row — every active bot collects; show it only when it is **false** (as a red
"Not collecting" chip). Show the other capabilities as 20 px round icon badges, max 4, `gap-1`:

| cap | icon | on style | off |
|---|---|---|---|
| publish | `Globe` | `bg-teal-500/15 text-teal-300 ring-1 ring-teal-500/30` | not rendered |
| telegram | `Send` | `bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/30` | not rendered |
| real-money capable | `Wallet` | `bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30` | not rendered |
| real-money ON | `Banknote` | `bg-red-500/20 text-red-300 ring-2 ring-red-500/60` (+ `animate-pulse` only when fleet is armed and not paused) | not rendered |

Each icon has `aria-label` and a `title` (e.g. "Published on /picks and the public track record").
None on → a single `–` (muted) with title "Collecting only (paper)". A legend line under the filter
bar explains the four icons once (icon + word), so meaning is never colour/icon-only.

---

## 5. Colour tokens (existing theme only)

From `globals.css` + Tailwind palette already used on the site:
- surfaces: `bg-background`, `bg-card`, `bg-muted/40`, borders `border-border`, tile strip `bg-white/[0.08]` + `border-white/[0.06]`
- text: `text-foreground`, `text-muted-foreground` (never below 12 px for content text; 11 px allowed only for mono uppercase micro-labels and pills)
- verdict: emerald-400/500 (= `--color-positive` #22c55e family), red-400/500 (= `--color-negative`), amber-300/400 (= `--color-warning`), muted
- method accents (as in performance-leaderboard.tsx:142): model = sky, sharp = violet, consensus/forward = teal, in-play = amber
- junk control reference: amber-400/70 dashed
Contrast: emerald-400, red-400, amber-300, sky-300, teal-300, violet-300 on `bg-card` (oklch 0.12) all
exceed 4.5:1; do **not** use the -500 shades for text on tinted chip backgrounds, and do not use
`text-muted-foreground` on `bg-muted` for anything the operator must read.

---

## 6. Mobile (< 768 px)

No horizontal scroll anywhere (acceptance: `document.documentElement.scrollWidth === innerWidth`).
Fleet strip → `grid-cols-2` (Needs-a-look tile spans 2 when non-empty). Filters become a horizontal
pill row that wraps. Each bot row becomes a card (same grid element, `md:` breakpoint switches template):

```
┌─────────────────────────────────────────┐
│ Sharp-line · 1×2              ◉ ✈   6 h │
│ 1×2 · any book · edge ≥ 3%              │
│ [↘ Loses to close]  ≈ junk control      │
│ −2.9% ±1.4 ├────●──┊──┤│      n 62      │   ← forest bar full width
│ ▂▅▇▆▃▅▂▁▃▅▆▇  50/7d        ROI +7.5%    │
└─────────────────────────────────────────┘
```
Card: `rounded-lg border border-border bg-card p-3 space-y-2`, `gap-3` between cards; section
headers stay (title + metric pill; subtitle wraps). Drawer on mobile = full-screen sheet from the
bottom (`inset-0`, rounded top corners, sticky header with a 44 px close button).

---

## 7. Formatting rules (human-readable; implement as pure helpers in `bot-board-format.ts`)

| Field | Raw example | Render |
|---|---|---|
| markets | `1x2`, `over_under_25`, `over_under_35`, `o/u`, `btts` | `1×2`, `O/U 2.5`, `O/U 3.5`, `O/U`, `BTTS`; join with ` + ` ; `['o/u','over_under_25','over_under_35']` → `O/U 2.5 + 3.5` (drop the bare `o/u` when specific lines exist) |
| books `["*"]` | + books_source "every book except EXCLUDED_BOOKS …" | row: `any book`; drawer: `Any book except Max, Avg, Betfair Exchange, BetWin, Betfred, Unibet-Kambi, Unibet` — taken from the gate named `books_excluded*` if present; else `Any book (see source)` |
| books list | `Coolbet, Unibet-Site` | `Coolbet, Unibet` (strip `-Site`); `api-football-live` → `API-Football live feed` |
| edge_floor numeric-ish | `0.03 (multiplicative P x odds - 1)` / `0.08` | row: `edge ≥ 3%`; drawer: `Edge ≥ 3% — model probability × odds must exceed 1.03` |
| edge_floor selection-aware | `selection-aware: home at odds>=2.8 -> 0.1, else 0.13` | row: `edge ≥ 10–13%`; drawer: two bullet lines `Home at odds ≥ 2.80: edge ≥ 10%` / `Otherwise: edge ≥ 13%` |
| edge_floor tiered | `T1 1x2_fav 0.08 1x2_long 0.12 ou 0.08; T2 …` | row: `edge ≥ 3–12% by tier`; drawer: a small 4 × 3 table (rows T1–T4, cols Favourite / Long shot / O/U) in % |
| edge_floor null | (in-play) | row: omit the floor segment; drawer: `No edge floor — trigger-based` |
| unparseable floor | anything else | show raw string in drawer, row shows `custom floor` |
| odds band | min 1.2 max 1.6 / null–4.0 / 2.8–null / 1.01–null | `odds 1.20–1.60` / `odds ≤ 4.00` / `odds ≥ 2.80` / (1.01 is "no floor" → omit) / both null → omit |
| percentages | fractions | `+2.5%` 1 dp with explicit sign; CI half-width `±1.4` (no % sign, pp implied, 1 dp); ROI `+7.5%` |
| t | −4.127 | `t −4.1` (1 dp, true minus sign U+2212) |
| counts | 2429 | `2,429` (`toLocaleString('en-GB')`) |
| relative time | minutes | `just now` < 1 min · `25 min` < 90 min · `6 h` < 36 h · `2 d` < 14 d · then `12 Sep`; title shows `2026-09-24 09:35 UTC` |
| rule_version | `sharp_edge_v4_2026_09_15` | pill `v4 · 15 Sep`; title shows raw string |
| display name fallback | `bot_trigger_1x2_sharp_tight_v1` | strip `bot_` and `_v\d+`, `_`→space, known tokens (`1x2`→`1×2`, `ou`→`O/U`) → `Trigger 1×2 sharp tight` |
| gate names | `min_edge_multiplicative`, `book_quote_max_age_min` | a label map (`Min edge`, `Max quote age`, `Max anchor overround`…); unknown → sentence-case the snake_case |
| gate values | `["shin","additive","power"]`, `0.08`, `45` | arrays → comma list; fractions < 1 on edge/overround gates → `%`; `*_min` → `45 min`; `*_h` → `48 h`; objects → compact key: value list |
| prob_source / anchor | `Shin-de-vigged Pinnacle` | shown verbatim in drawer; anchor → method pill (model / sharp / consensus / junk / none) |

---

## 8. Detail drawer

Right sheet `max-w-2xl` (was 3xl), `bg-background border-l`, sticky header, body `space-y-6`.

```
┌────────────────────────────────────────────────────────┐
│ [SHARP] Sharp-line picks — 1×2                   ✕     │  sticky header
│ bot_sharp_1x2_v1 · Forward test · v4 · 15 Sep          │
│ ◉ Published  ✈ Telegram  – Real money: not capable    │  capability row (icon+word)
├────────────────────────────────────────────────────────┤
│ ┌ VERDICT ───────────┬ mc-CLV ──────────┬ ROI · FLAT ┐ │  3 mini tiles
│ │ ↘ Loses to close   │ −2.9% ±1.4       │ +7.5%      │ │
│ │ ≈ junk control     │ t −4.1 · n 62    │ 62 settled │ │
│ └────────────────────┴──────────────────┴────────────┘ │
│ forest bar, full width, 40 px, with axis ticks          │
│ 12-week strip, full width, 56 px, with week labels      │
│ Record: 64 picks · 25 W / 37 L · 0 void · 2 pending     │
│         first pick 15 Sep · last 6 h ago · 50 in 7 d    │
│ (secondary, collapsed) Other metrics: Pin-CLV …, raw CLV│
├────────────────────────────────────────────────────────┤
│ WHAT IT BETS                                           │
│  Description (1–2 lines, text-sm)                      │
│  ┌ Markets ┐ ┌ Books ────────────┐ ┌ Price ─────────┐  │  3 "fact cards"
│  │ 1×2     │ │ Any book except 7 │ │ Edge ≥ 3%      │  │
│  │         │ │ (list on expand)  │ │ Odds ≤ 4.00    │  │
│  └─────────┘ └───────────────────┘ └────────────────┘  │
│  Probability: Shin-de-vigged Pinnacle · anchor: sharp   │
│  Runs: :05/:35 by publish_picks_forward_test            │
│  ▸ Gates (9)          — collapsed <details>             │
│     Min edge ........ 3%          [source ▸]            │
│     Max odds ........ 4.00                              │
│  ▸ Sources & export   — collapsed: ledger, writer job,  │
│     edge_floor_source, books_source, exported_at        │
├────────────────────────────────────────────────────────┤
│ RECENT PICKS (30)                         [open all ↗]  │
│ 24 Sep 18:45  Arsenal – Chelsea   1×2 Home  1.84 Epicbet  ● pending   —   │
│ 21 Sep 17:30  …                   1×2 Away  3.65 Coolbet  ✓ won      −4.0% │
└────────────────────────────────────────────────────────┘
```

Rules:
- Header method pill from `anchor` (model sky / sharp violet / consensus teal / junk amber / none muted).
- Capability row repeats §4.7 icons **with words**; plus, when relevant, `Real money: capable, OFF`
  or red `Real money: ON`; fleet state appended (`fleet paused`).
- Mini tiles reuse the §3 tile style at `text-xl`.
- "Other metrics" `<details>` shows the non-admissible numbers (e.g. Pin-CLV +10.0% t 11.4 for the
  sharp triggers) with an explicit line: *"Not used for the verdict — this family is judged on mc-CLV."*
  Never shown for in-play.
- `outliers excluded` and `maturity_label` go in the Record line as small text.
- Gates: `<details>` closed by default, 2-column `grid-cols-[1fr_auto]` label/value list; each row's
  source is a `text-[11px] font-mono` line revealed on the row's own expand (or `title`), **not** a
  third column. Duplicates of the fact cards (`max_odds`, `odds_band`, `min_edge_multiplicative`,
  `books_excluded`, `rule_version`) are hidden from the gate list — they are already shown above.
- Recent picks: `Match` shows **team names** (`Home – Away`, needs §9 data item 2); fallback
  `match 8-char id` muted. Result = icon + word (`✓ won` emerald, `✗ lost` red, `● pending` muted,
  `○ void` muted). CLV column shows **only the admissible metric** for the family (one column, header
  `mc-CLV` or `Pin-CLV`); in-play shows no CLV column and adds `min'` if available. Odds 2 dp.
  Rows older than the scored rule_version (forward test) are shown with a `v3` pill and 50% opacity.
- Mobile: full-height bottom sheet; tables become stacked 2-line list items.

---

## 9. Data needed (builder adds loaders + extends `scripts/dump_bot_board_fixture.py`)

Everything in §2–§8 renders from the current fixture **except** these two items:

**D1 — weekly series for the 12-week strip.** New read `bot_weekly` (loader `loadBotWeekly()` in
`src/lib/bot-board.ts`, fixture key `"weekly"`: `Record<bot_name, WeeklyRow[]>`):

```sql
SELECT l.bot_name,
       date_trunc('week', l.pick_time)                                  AS week,
       count(*)                                                          AS picks,
       count(*) FILTER (WHERE l.result IN ('won','lost'))                AS settled,
       count(l.clv_mc)       FILTER (WHERE abs(l.clv_mc) <= 1)           AS clv_mc_n,
       avg(l.clv_mc)         FILTER (WHERE abs(l.clv_mc) <= 1)           AS clv_mc_mean,
       count(l.clv_pinnacle) FILTER (WHERE abs(l.clv_pinnacle) <= 1)     AS clv_pin_n,
       avg(l.clv_pinnacle)   FILTER (WHERE abs(l.clv_pinnacle) <= 1)     AS clv_pin_mean,
       sum(l.pnl_unit)                                                   AS pnl_unit
  FROM bot_ledger l
  LEFT JOIN bot_scoreboard s USING (bot_name)
 WHERE l.pick_time >= date_trunc('week', now()) - interval '11 weeks'
   -- pre-registered bots: current rule_version only, same as the scoreboard (never pooled)
   AND (s.scored_rule_version IS NULL OR l.rule_version = s.scored_rule_version)
 GROUP BY 1, 2
 ORDER BY 1, 2;
```
Same |clv| ≤ 1 outlier guard as the scoreboard (ANALYSIS_GOTCHAS §9). Web side: one query over the
view with the service client (admin-only; not anon). If ops prefer a view, `bot_weekly` can be
migration 411 with the identical body — either is fine; the page must tolerate its absence
(`{rows:[], error}` → strip falls back to text, §4.4).

**D2 — team names for recent picks.** Extend the ledger read (fixture `ledger` rows + `/api/admin/bot-ledger`)
with `home_team`, `away_team` via `matches m JOIN teams ht ON ht.id = m.home_team_id JOIN teams at ON
at.id = m.away_team_id` (builder: verify the teams name column). Do not change the `bot_ledger`
view contract itself — join in the read, or add the columns to the view in a separate reviewed migration.

No other new data. The junk-control comparison, verdict states, silence detection and all formatting
derive from fields already in `bot_scoreboard` / `bot_config` / `bot_capabilities`.

### "How to read this" copy (≤ 5 bullets)
- Each bot is judged on one number for its family — mc-CLV, Pinnacle CLV, or (in-play) nothing yet.
- The bar is the 95% range. Left of zero = we priced worse than the close.
- Dashed amber line = a deliberately junk-anchored bot. A bot on that line is not showing skill.
- No verdict below 30 measured picks. ROI is a flat 1-unit stake, for comparison only.
- Pre-registered bots are scored on their current rule version only.

---

## 10. Empty / unknown / error states

| Case | Render |
|---|---|
| views not deployed (scoreboard error) | keep today's amber banner, but as a card with `AlertTriangle`; fleet strip tiles show `—`; no rows |
| config read failed | rows render; identity line = `config unavailable` (amber-300); drawer config section shows the error |
| capabilities read failed / no fleet row | Placement + Real-money tiles show `? Unknown` (never "off"); caps cell `?` with title |
| bot has no config row | identity line `no config — family unknown`, row lands in Unresolved section |
| metric n = 0 | verdict **Too early** `0 / 30`, no forest bar, text `no settled CLV yet` |
| se null (n = 1) | dot only, no whisker, verdict Too early |
| control missing | no dashed line, no ≈ line, header legend omitted (never draw a line at 0 as a stand-in) |
| weekly loader missing | strip → `{picks_7d} picks · 7d` text |
| drawer ledger loading / error / empty | skeleton rows (3 × `h-4 bg-muted/40 animate-pulse`) / amber error line / `No picks yet` |
| retired bot with no ledger rows | collapsed into a single line "20 retired bots never produced a pick ▸" (expandable list) |

## 11. Retired tab

Group by retirement month (`September 2026 (14)` …) as `<details open>` for the latest month, closed
for older. Default filter **"Had picks"** (hides the 20 zero-pick retirees behind the collapsed line
in §10). Each retired row = same grid as §4 with Verdict + forest bar + N·ROI kept (the "final
verdict"), **Retired date** in place of Last pick, and the **reason** as a `text-sm` foreground line
clamped to 2 lines with an inline `more` toggle (not a hover title). Badge `still collecting`
(`writing_7d`) as a muted outline chip with `Activity` icon. Family accent bar on the left of each row
so a retired sharp generator and a retired model bot are distinguishable at a glance.

## 12. Accessibility

- Every verdict/capability is icon + word; colour is redundant, never the only carrier.
- Forest bar and strip have `role="img"` + a full-sentence `aria-label`; values also exist as text in the row.
- Rows are keyboard-reachable (`tabIndex=0`, Enter/Space opens, visible `focus-visible:ring-2 ring-ring`); drawer traps focus, returns focus to the row on close, Esc closes (exists).
- Minimum content text 12 px; mono micro-labels 11 px only. All text colours in §5 ≥ 4.5:1 on `bg-card`.
- `prefers-reduced-motion`: no pulse on the real-money icon.
- Hit targets ≥ 40 px on mobile (tabs, filters, close button).

## 13. Honesty rules (must hold; reviewer checks each)

1. No verdict word other than **Too early** when the admissible n < 30. The forest bar for n < 30 is faded + dashed.
2. In-play shows **no CLV anywhere** (row, strip colour, drawer, pick table) — only hit rate / ROI and "judged on lift (not computed yet)".
3. Pre-registered bots show their scored rule version (pill) and "earlier picks not pooled"; the weekly strip and pick table respect the same version filter.
4. The verdict reads only the family's admissible metric; other metrics live in the collapsed "Other metrics" block with the "not used for the verdict" line.
5. The junk-control line is labelled wherever drawn; the ≈-control readout appears only for forward_test.
6. Unknown fleet state is shown as Unknown, never as Off.

## 14. Acceptance checklist (reviewer screenshots)

- [ ] 1440×900: first screen shows fleet strip + the full Forward-test section with **verdict, forest bar, N·ROI, last, caps visible without horizontal scroll** (`scrollWidth === clientWidth` for every section).
- [ ] 375×812: cards, no horizontal scroll, verdict + bar visible on each card.
- [ ] Junk-control dashed line at −3.0% visible on every mc-CLV bar; `bot_sharp_1x2_v1` reads "Loses to close · ≈ junk control".
- [ ] `bot_consensus_b_v1` (n 4): "Too early 4 / 30", faded bar clamped with arrowheads.
- [ ] `bot_coolbet_ou_model_v1`: red last-pick "11 d" and listed in "Needs a look".
- [ ] In-play rows: no CLV number anywhere (search the DOM for "43.").
- [ ] No row contains `multiplicative P x odds`, a bare `*`, or `over_under_25`.
- [ ] `bot_v10_1x2` floor reads `edge ≥ 3–12% by tier` in the row and a T1–T4 table in the drawer.
- [ ] Verdicts tile on today's fixture: "0 beat · 4 lose · 7 inconclusive · 7 too early · 2 no CLV" (20 bots — the control is excluded; including it would make 5 lose).
- [ ] Drawer: 3 mini tiles, 3 fact cards, gates collapsed, recent picks with team names (or id fallback), one CLV column.
- [ ] Retired tab grouped by month; zero-pick retirees collapsed into one line.
- [ ] Tab through the board: every row focusable, Enter opens drawer, Esc returns focus.

## 15. Open questions for the owner (do not block the build)

- **Q1** Should the ≈-junk-control comparison extend to every mc-CLV family (shadow model bots, sharp triggers)? Mechanically trivial; statistically the control is drawn from the forward-test pool, so it is shown only for forward_test until decided.
- **Q2** Should a large-n Inconclusive (e.g. `bot_unibet_trigger_sharp_1x2_v1`, mc-CLV −0.9% ±2.6 at n 170, while its non-admissible Pin-CLV reads +8.4% t 8.7) get a distinct "large n, still zero" cue? Proposed: no — keep five states; the Pin-CLV stays in the drawer's "Other metrics".
