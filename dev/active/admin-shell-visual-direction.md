Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# Admin shell: visual direction (2026-09-24)

**Direction in one line.** Use the **shadcn dashboard example (`dashboard-01`) and sidebar blocks (`sidebar-07`)** as the
code base: copy them into the repo (they are copied code, not a template dependency). Take the **structure** the owner's
references share (TailAdmin, Purple Admin, Staradmin, SB Admin): a grouped sidebar, a top bar, KPI cards with a trend,
interactive charts, DataTables. Render all of it in **the public site's own dark palette and type**, so the admin looks
like the same product as `/`, `/picks` and `/performance`. There is **no light content area by default**. A light
toggle is optional and low priority (§9).

Owner constraints, in order: (1) no third-party template install; build from our components plus shadcn code copied
into the repo; (2) interactive charts and tables; (3) "it needs to align with our site"; dark first.

Sidebar groups in §3 are a **draft**. The information-architecture audit (`dev/active/admin-information-architecture.md`,
in progress) decides the final groups.

---

## 1. References (saved in `dev/active/admin-refs/`, 1440 px headless captures)

| File | What to take from it |
|---|---|
| `03-shadcn-dashboard-01.png` | **The base.** Sidebar with a plain first group, a labelled second group ("Documents"), and a footer with Settings/Help/Search above the user row. Top bar has a sidebar toggle, a vertical separator and the page title. A row of 4 KPI cards, each with a label, a big number, a trend pill in the top right (`↗ +12.5%`) and a two-line footnote. An area chart card has a 3-way range toggle (3 months / 30 days / 7 days). The table has tab filters and badge counts, "Customize columns", status badges with icons, a kebab row action and "Rows per page + Page 1 of 7" paging. |
| `02-shadcn-sidebar-07.png` | Sidebar that collapses to an icon rail. It has a workspace switcher at the top (logo tile + name + sub-label + chevrons), group labels, nested items behind a chevron, and a user footer with a dropdown. The top bar holds a breadcrumb: `Section › Page`. |
| `06-tremor-dashboard.png` / `07-tremor-details-table.png` | The **dark-mode look to copy**. Near-black body. Borders, not shadows. Small tinted delta badges (`+4.4%` green, `-3.9%` red) next to the metric name. Line charts with a muted comparison series. The table has hairline row dividers, a dashed "+ Status / + Region" filter chip bar, search, Export/View buttons on the right, coloured status pills, `…` row actions and right-aligned tabular numbers. This matches how our site already looks. |
| `05-tailadmin-demo.png` | Owner's main reference, used for **structure only**: an uppercase `MENU` label, an icon on every item, a filled active item, chevrons, `NEW` badges on items, a top bar with `Search or type command… ⌘K` plus bell (with dot), theme toggle and avatar/name, KPI cards with an icon in a tinted tile and a delta pill with an arrow, and a Monthly/Quarterly/Annually segmented control with a date range. Its light theme, blue primary and soft shadows are **not** adopted (§2). |
| `01-shadcn-examples-dashboard.png`, `04-shadcn-sidebar-03.png`, `08-materio.png` | Supporting: the same shadcn dashboard at a smaller size; sidebar-03's grouped doc-style nav with a left guide line for sub-items; Materio's KPI tiles with icon tiles (its gradient/purple styling is rejected). |

Checked and not used: Horizon UI (the demo deployment is paused), Tailwind Plus application shells (login-gated), and
Linear/Vercel/Stripe (login-gated). Two lessons from their published write-ups still apply. Linear made its sidebar
*dimmer* than the content so the content leads
([Linear redesign part II](https://linear.app/now/how-we-redesigned-the-linear-ui)). Vercel's 2026 navigation moved
tabs into a collapsible sidebar with a floating mobile bar
([Vercel changelog](https://vercel.com/changelog/dashboard-navigation-redesign-rollout)).

Our current admin (local preview `localhost:3055/admin/bots`, fixture) already has a sidebar with a status block, a
KPI strip, control cards and the bot table. It reads as a set of stacked bordered boxes: no top bar, no breadcrumb,
labels and numbers in several different styles, and page headers that differ from page to page.

---

## 2. Design tokens: existing site token → admin usage

All of these already exist in `src/app/globals.css` (`:root` and `.dark` hold identical dark values; `<html>` carries
`class="dark"`). **The admin adds no new colours.** It adds four semantic aliases so the 441 hard-coded
`emerald/red/amber/sky-NNN` classes in admin code have somewhere to go.

| Site token (value) | Tailwind | Admin usage |
|---|---|---|
| `--background` oklch(0.07 0.01 260) | `bg-background` | Content area (page body) |
| `--sidebar` oklch(0.10) | `bg-sidebar` | Sidebar column. It sits one step *above* the body and stays dimmer than cards, per the Linear lesson |
| `--card` oklch(0.12) | `bg-card` | Cards, chart cards, table container, top bar (`bg-background/80 backdrop-blur` when sticky) |
| `--accent` / `--muted` oklch(0.18) | `bg-accent`, `bg-muted` | Row hover, active nav item fill, segmented-control active segment, table header row (`bg-muted/40`) |
| `--border` oklch(0.22) | `border-border` | Every card/table/sidebar divider. **Borders, never shadows**, in dark mode (Tremor, shadcn) |
| `--foreground` 0.93 / `--muted-foreground` 0.63 | `text-foreground`, `text-muted-foreground` | Values and titles / labels, footnotes, axis ticks |
| `--primary` green oklch(0.72 0.19 145) | `bg-primary`, `text-primary`, `ring-ring` | Brand accent: primary button, focus ring, active-nav left bar, chart series 1. Same green as the site CTA |
| `--destructive` oklch(0.60 0.22 25) | `bg-destructive` | Destructive buttons, the ARMED bar |
| `--chart-1…5` green / blue 250 / orange 55 / magenta 320 / cyan 200 | `var(--chart-n)` | Chart series in that order. The junk-control / baseline series always uses `--muted-foreground` dashed |
| `--color-positive` #22c55e | `text-positive`, `bg-positive/15` | **Alias as "success"**: +CLV, "beat", ok feed, trend-up pill |
| `--color-negative` #ef4444 | `text-negative`, `bg-negative/15` | **"danger"**: −CLV, "lose", failed feed, trend-down pill |
| `--color-warning` #f59e0b | `text-warning`, `bg-warning/15` | **"warning"**: stale, needs a look, Unknown state |
| *(add)* `--color-info: oklch(0.70 0.14 235)` (≈ sky-400, already used ad hoc) | `text-info`, `bg-info/15` | "Paused", informational badges |
| Site method hues: violet-300/500 sharp, teal-300/500 consensus, sky-300/500 model (`/picks`) | *(add)* `--color-method-sharp/-consensus/-model` | Bot family colour in charts ("picks per day by family") and family badges. The admin must use the **same hue per method as `/picks`** |
| `--radius` 0.5rem → `rounded-lg`; site cards use `rounded-xl` | `rounded-xl` cards, `rounded-lg` controls, `rounded-md` nav items/badges | The site's `rounded-xl border` card is the admin card |
| `--font-sans` Inter / `--font-mono` JetBrains Mono | `font-sans`, `font-mono` | Inter for everything. Mono **uppercase, tracking-wider** for eyebrow labels (the site's `font-mono text-xs uppercase tracking-[0.2em]` signature) and for numbers in tables (`tabular-nums`) |

**Add (one small `@theme inline` block, no values changed):**
```css
--color-success: var(--color-positive);
--color-danger:  var(--color-negative);
--color-info: oklch(0.70 0.14 235);
--color-method-sharp: oklch(0.72 0.16 295);      /* ≈ violet-400, as /picks */
--color-method-consensus: oklch(0.78 0.12 180);  /* ≈ teal-300 */
--color-method-model: oklch(0.75 0.13 235);      /* ≈ sky-400 */
```

**Type scale (admin):** page title `text-xl font-semibold tracking-tight`. Card title `text-sm font-medium`. Eyebrow
`font-mono text-[11px] uppercase tracking-wider text-muted-foreground`. KPI value `text-2xl font-semibold tabular-nums`
(`text-3xl` on Overview). Body/table `text-sm`. Footnote `text-xs text-muted-foreground`.
**Spacing:** page padding `p-4 lg:p-6`. Gap between sections `gap-4 lg:gap-6`. Card padding `p-4` (header `px-4 pt-4`,
table cells `px-3 py-2`).

---

## 3. Shell (the base is sidebar-07 + dashboard-01)

**Current state.** Another session has an **uncommitted** hand-built shared shell in the web repo:
`src/app/(app)/admin/layout.tsx`, `src/components/admin/{admin-shell,admin-sidebar,admin-nav,admin-status}.ts(x)`,
`src/components/public-chrome.tsx`. It already provides one sidebar for every admin page, a rail collapse saved in
localStorage, a mobile drawer, status dots and the armed bar. **Build on it; do not rebuild it.** The data
(`ADMIN_NAV`, `fleetStatus`) stays. What changes is the chrome.

**Option A (recommended): switch the internals to shadcn `sidebar.tsx`.** `npx shadcn add sidebar` for the base
registry. Its registry dependencies are button ✅, separator ✅, sheet ✅, input ✅, **tooltip ✗, skeleton ✗, use-mobile hook ✗**.
The three missing pieces are generated as files and need **no new npm package**, because `@base-ui/react` already
ships Tooltip. The `--sidebar-*` tokens it needs **already exist** in globals.css. What we gain:
- The open/rail state is saved in a **cookie**, read on the server, so the page does not flash. Today it is localStorage.
- ⌘B toggles the sidebar.
- Tooltips on rail icons.
- `SidebarGroupLabel`, `SidebarMenuBadge` and `Collapsible` sub-groups.
- A mobile Sheet.

Cost: about 700 lines of copied code plus about 100 lines of re-wiring `AdminSidebar`. Option B is to keep the hand-built
sidebar and restyle it with the classes below. That works too; the visual spec is the same.

**Sidebar spec** (`collapsible="icon"`, `variant="sidebar"`, width 15rem, rail 3rem):
- **Header:** a logo tile `size-8 rounded-lg bg-primary/15 text-primary` with the OI mark, then `OddsIntel` (`text-sm font-semibold`) over `ADMIN` (eyebrow). It is not a switcher; we have one workspace.
- **Groups (draft, pending the IA audit):**
  - *(no label)* Overview
  - `BOTS & MONEY`: Bots · Shadow bots · Real bets
  - `DATA`: Feeds · Ops
  - `OTHER SPORTS`: a collapsible group, **collapsed by default**, holding CS2 · LoL · Tennis (routes the owner does not use). Place stays out of the menu but reachable from Real bets.
- **Group label:** `SidebarGroupLabel` → `font-mono text-[11px] uppercase tracking-wider text-sidebar-foreground/50`. This is TailAdmin's `MENU`, in our eyebrow style.
- **Item:** `h-8 rounded-md px-2 gap-2.5 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground`, with a 16 px icon on every item.
  - **Active:** `bg-sidebar-accent text-sidebar-foreground font-medium` plus a 2 px `bg-primary` left bar (`before:` pseudo). This is the filled highlight in TailAdmin, drawn with our green.
- **Badges** (`SidebarMenuBadge`) are **live counts, not decoration**:
  - Bots: `needs a look` count, shown in the warning tone.
  - Feeds: fail count in the danger tone, or warn count in the warning tone.
  - Shadow bots: pending picks, in a neutral tone.
  - Badge class: `rounded-md px-1.5 text-[11px] font-medium tabular-nums bg-warning/15 text-warning`.
- **Footer:** the existing STATUS block (Placement / Real money / Picks channel, dot + word). In rail mode it becomes three stacked dots with tooltips. Below it is the user row (avatar initials `size-8 rounded-lg bg-muted`, email, `…` menu: *Back to site*, *Sign out*).

**Top bar** (`SidebarInset` header, `h-12 sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur px-4`),
left to right:
1. `SidebarTrigger` (panel icon) + `Separator orientation="vertical" h-4`.
2. **Breadcrumb:** `Admin › Bots › bot_v10_1x2`. Derived from `activeAdminItem(pathname)` plus a page-provided leaf. Current segment `text-foreground`, ancestors `text-muted-foreground hover:text-foreground`.
3. Spacer.
4. **Search / command button:** `h-8 w-64 rounded-lg border border-border bg-muted/30 text-muted-foreground text-sm`, text "Search bots, pages…" with a `⌘K` kbd chip. It opens the palette (§8).
5. **Notifications bell** with a dot when there are attention items (§8).
6. *(Optional, later)* theme toggle.
7. The **armed bar** stays a full-width `bg-destructive` strip *above* the top bar, unchanged.

**Page header** (one component, `PageHeader`, used by every page):
```tsx
<div className="flex flex-wrap items-end justify-between gap-3">
  <div>
    <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{eyebrow}</div>
    <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
    <p className="mt-0.5 text-xs text-muted-foreground">{meta /* “20 active · data 15:27 UTC” */}</p>
  </div>
  <div className="flex items-center gap-2">{actions /* range toggle, Activity, How to read, … */}</div>
</div>
```
Pages drop their own `mx-auto max-w-* px-* py-*` wrappers. The shell's `main` sets `p-4 lg:p-6`, and
`max-w-[1600px]` only on text-heavy pages.

---

## 4. Shared components (named so the public pages can adopt them later)

Put them in `src/components/oi/` (product-wide), **not** `components/admin/`. Admin is the first consumer. Moving
`/performance` onto them is a possible future task and is **out of scope here**.

| Component | Spec |
|---|---|
| `Panel` (+`PanelHeader`, `PanelBody`) | `rounded-xl border border-border bg-card`. Header `flex items-center justify-between gap-3 px-4 pt-4`, title `text-sm font-medium`, description `text-xs text-muted-foreground`, actions slot on the right. It replaces ad-hoc `rounded-lg border bg-card` boxes and matches the site's `rounded-xl border border-border/50 bg-card/60` cards. Pick **one** of the two opacities (propose solid `bg-card` + `border-border`) and use it on both. |
| `SectionLabel` | The eyebrow: `font-mono text-[11px] uppercase tracking-wider text-muted-foreground`. Today it is copied into about 10 files as `LABEL`. |
| `StatCard` (KPI) | See §5. |
| `TrendPill` | `inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium tabular-nums`. Up: `bg-success/15 text-success` + `TrendingUp` 12 px. Down: `bg-danger/15 text-danger` + `TrendingDown`. Flat or too few samples: `bg-muted text-muted-foreground`. Semantics: the colour means **good for us/bad for us**, not the sign. A falling "needs a look" count is green. |
| `StatusBadge` | `rounded-md px-1.5 py-0.5 text-xs font-medium` + a `size-1.5 rounded-full` dot. Tones: success, danger, warning, info, neutral, method-*. It replaces every `DOT_CLS`/`VERDICT_BG` map. |
| `Segmented` | A range switch built on the existing `Tabs` (`@base-ui` Tabs): `inline-flex h-8 rounded-lg border border-border bg-muted/30 p-0.5`, segment `px-2.5 text-xs rounded-md`, active `bg-card text-foreground shadow-sm`. Values `7d · 30d · 90d · All` (admin data is weekly and daily, so no "Day/Week/Month" wording). |
| `ChartCard` | A `Panel` with title, description, `Segmented` and a `ChartContainer` (§6). |
| `DataTable` | See §7. |

---

## 5. KPI card (`StatCard`)

This is the dashboard-01 anatomy with TailAdmin's icon tile and Staradmin's sparkline, in our tokens:
```
┌──────────────────────────────────────────┐
│ [icon]  ACTIVE BOTS           ↗ +2 (7d)  │  icon tile size-8 rounded-lg bg-{tone}/15 text-{tone}; eyebrow; TrendPill
│ 20                     ▁▂▃▅▆▇ sparkline  │  text-2xl font-semibold tabular-nums; 80×28 recharts <Area>, no axes
│ 5 published · 5 on Telegram              │  text-xs text-muted-foreground
│ View bots →                              │  optional footer link text-xs text-primary (SB Admin's “View details →”)
└──────────────────────────────────────────┘
```
- Container: `Panel` + `p-4`, whole card clickable when it has `href` (`hover:bg-accent/40 transition-colors`, focus ring). In a strip, use `grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3`. Separate cards (dashboard-01 style), **not** the current joined `gap-px` strip.
- **Sparkline** uses the same series the detail chart shows, with the last 12 weeks from `bot_weekly`. Stroke `var(--chart-1)`, fill a gradient `stop-opacity .25 → 0`, `isAnimationActive={false}`.
- **Unknown stays "Unknown"** (honesty rule): value `—` + `StatusBadge tone=warning "Unknown"`, no TrendPill.
- Gradient-filled KPI cards (Purple Admin) are **not** used. The single exception is the existing whole-card red state for **Real money ARMED** (`border-destructive bg-destructive/10`).

---

## 6. Charts: recharts 3.8.1 (already in package.json) + shadcn `chart.tsx`

`npx shadcn add chart` copies `components/ui/chart.tsx` (`ChartContainer`, `ChartTooltip(Content)`,
`ChartLegend(Content)`, a config that maps series → label + `var(--chart-n)`). It uses **recharts, which we already
have**, so there is no new dependency. ApexCharts (TailAdmin's library) is **not** added, because recharts already
covers everything below.

**Interaction standard (every `ChartCard`):**
- **Tooltip** on hover with a crosshair (`cursor={{ stroke: "var(--border)" }}`). Tooltip body is `rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-lg`, with values `tabular-nums` and a coloured 8 px square per series.
- **Legend toggles series.** Keep a `hidden: Set<string>` state, have `ChartLegendContent` items call `onClick` to toggle, and dim a hidden item to `opacity-40 line-through`.
- **Range switch** via `Segmented` (7d/30d/90d/All) in the card header. It filters client-side when the page already loaded 90d. Otherwise it goes in a `?range=` search param, read server-side.
- **Style:**
  - Grid: `CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3"`.
  - Axes: `tickLine={false} axisLine={false}`, ticks `fill: var(--muted-foreground) fontSize 11`.
  - Areas: `type="monotone" strokeWidth={2}` + `<linearGradient>` fill `.3 → 0`.
  - Bars: `radius={[3,3,0,0]}`.
  - Zero line: `ReferenceLine y={0} stroke="var(--muted-foreground)" strokeOpacity={.4}`.
  - Height `h-[240px]` (card) / `h-[320px]` (Overview hero).
- A chart **never** shows a smoothed line through fewer than 5 samples per bucket. Those buckets render as gaps (`connectNulls={false}`), the same rule the WeeklyStrip already uses.

**First four charts for our data:**

| Chart | Page | Type | Source | Data status |
|---|---|---|---|---|
| **CLV over time per bot, with the junk-control line** | Bots drawer + Bots page "Compare" card | Line (one bot or up to 4 selected) + a dashed `--muted-foreground` junk-control series + zero line | `bot_weekly.clv_mc_mean` (n ≥ 5 per week) per `bot_name` | Bot series ready. **The junk-control series needs weekly data**: `bot_market_stats` is all-time per (bot, market), so the line needs the junk bot's own `bot_weekly` rows on the same market. Verify it has a `bot_weekly` row |
| **Picks per week by family** | Overview + Bots | Stacked bar, one colour per family (`--color-method-*`, then `--chart-n` for the other families) | `bot_weekly.picks` × bot family (`BotFamily`) | Ready (weekly). A daily grain would need a new view |
| **Cumulative flat-stake P/L per bot** | Bots (drawer + compare) | Area, cumulative `Σ pnl_unit` | `bot_weekly.pnl_unit` | Ready. Label it "flat 1u P/L", and do not call it ROI unless divided by settled count |
| **Feed coverage per book over time** | Feeds | Multi-line, `priced / fixtures` % per book | `FeedBookStats` holds **today + yesterday only** | **Needs a daily history view** (e.g. `feed_book_daily`). Until then, show today vs yesterday as bars |

The existing **ForestBar** (CI forest plot) and **WeeklyStrip** stay as they are **inside table rows**. They are
in-row micro-visuals, not chart cards. WeeklyStrip grows into a real recharts area chart only in the bot drawer and
Overview.

---

## 7. DataTable: `@tanstack/react-table` (new, small, headless)

Not present in package.json today. It is added because the owner asked for sort, search, paging, column visibility and
sticky headers on **every** table (Bots, Shadow picks, Real bets, Feeds, Ops). Hand-rolling that five times is the
alternative. The library is headless, so the markup stays our `components/ui/table.tsx`, following shadcn's
"data-table" pattern.

- **Toolbar** (`flex flex-wrap items-center gap-2 pb-3`):
  - Global search `Input h-8 w-56` with a search icon.
  - Facet filter chips in Tremor's dashed style: `h-8 border border-dashed border-border rounded-lg px-2.5 text-xs` with `+ Family`, `+ Verdict` and `+ Status` as a Popover checklist.
  - Right side: a `View` dropdown for column visibility, then an optional `Export CSV`.
- **Container:** `Panel` + `overflow-auto max-h-[70dvh]`.
- **Header:** `sticky top-0 z-10 bg-card` with row `bg-muted/40`, `h-9 text-xs font-medium text-muted-foreground`. Sortable headers are buttons with `ArrowUpDown` / `ArrowUp` / `ArrowDown` 12 px.
- **Rows:** `h-10 border-b border-border/60 hover:bg-accent/40`, **no zebra**, matching Tremor and shadcn dark. Numbers are `text-right font-mono tabular-nums`. Signed values are coloured via `text-success` / `text-danger` only when the sign is meaningful and n is large enough.
- **Row actions:** a kebab `DropdownMenu` in the last column (`w-10`). The primary row click opens the existing drawer/sheet.
- **Footer:** `flex items-center justify-between px-4 py-3 text-xs text-muted-foreground` holding "N of M rows", a "Rows per page" `Select` (25/50/100/All), "Page x of y" and four icon buttons. Choose paging for Shadow picks, Real bets and Ops runs. Choose **All** by default for Bots (about 20–40 rows; paging would hide the fleet).
- Sort, search, visibility and page size are saved in search params (`?sort=clv.desc&q=`) so views can be linked.

---

## 8. Top-bar patterns

- **Command palette (⌘K).** Built from the existing `Dialog` + `Input` with a filtered list (about 120 lines). **No `cmdk` dependency.** Sections: *Pages* (`ADMIN_NAV`), *Bots* (names from the board, jump to `/admin/bots?bot=…`), *Actions* (Pause placement, Pause picks channel). Actions route to the **existing confirm dialogs** and never toggle directly. Keys: ↑/↓, Enter, Esc. Style: `max-w-lg rounded-xl border bg-popover`, active row `bg-accent`.
- **Notifications** (`Popover`, bell + `size-2 rounded-full bg-danger` dot when there are fail items, `bg-warning` for warn only). Items come from **attention items we already compute**:
  - Feeds with `status` fail or warn (`getFeedStatus`, with `status_reason`).
  - Bots in "needs a look" (silent bots, stale picks: `Issue[]` in bot-board-model).
  - Placement or picks channel paused, and Unknown fleet state.

  Each item is a row with a `StatusBadge` + title + one-line reason + link. There is no read/unread persistence in v1: the list *is* the current state. It is loaded by the admin layout (one call; `getFeedStatus` + `loadFleetStatus` are already there) and passed through the shell.
- **Theme toggle:** low priority, §9.

---

## 9. Light mode (optional, low priority)

It is feasible, but it is **not** free. The tokens can carry it: add an `html.admin-light { … }` block with light
values, which beats `:root`/`.dark` on specificity. Drop the `dark` class on `<html>` while it is active, and have the
admin layout set both with an inline pre-paint script from a cookie, restored on leaving `/admin`. Classes must go on
`<html>` because Dialog/Sheet/Popover portal into `<body>`. The cost is that admin code has **441 hard-coded status
classes** (`text-red-400`, `text-amber-300`, …) and **214 hard-coded neutrals** (`text-neutral-100/500`, `white/[0.08]`),
tuned for a black background. They would fail contrast on white. The §2 semantic aliases are the migration target
either way. Do the light toggle only after pages have moved onto `success/danger/warning/info` and `Panel`/`StatusBadge`.

---

## 10. Per-page adaptation

| Page | Change so it sits in the shell |
|---|---|
| **Overview** `/admin` | Rebuild as a real dashboard. Row 1: 4 `StatCard`s (Active bots, Picks 7d, Feeds ok/warn/fail, Needs a look), each linking through. Row 2: `ChartCard` "Picks per week by family" (2/3 width) + "Attention" list panel (1/3, the same items as the bell). Row 3: the Bookmakers chip panel (existing) as a `Panel`. Drop `max-w-3xl` and the link-card grid; the sidebar now provides navigation. |
| **Bots** `/admin/bots` | `PageHeader` (eyebrow "Bots & money", title "Bots", meta line, actions Activity / How to read / …). FleetStrip becomes 6 `StatCard`s. Real money stays whole-card red when ARMED. Placement and Real money tiles scroll to the Real money card. Controls and Real money stay `Panel`s. The bot table moves to `DataTable`: sortable, family/verdict facets, search, sticky header, ForestBar + WeeklyStrip kept in-row. The drawer gains a CLV-over-time chart and a cumulative P/L chart. Remove the "Feeds bots depend on" block; the sidebar badge plus the bell cover it. |
| **Shadow bots** `/admin/shadow-bots` | SafetyStrip → a thin `StatCard` row or a status bar under the header. Replace every `text-neutral-*` with tokens. The picks table and scoreboard become `DataTable`s (the picks table uses paging). Promotions becomes a `Panel`. Remove the "← Back to ops" link (breadcrumb). `[bot]` detail: breadcrumb leaf = bot title, drop `max-w-4xl/6xl`, add the CLV chart. |
| **Feeds** `/admin/feeds` | `PageHeader` + `StatCard` row (ok/warn/fail/paused counts). FeedsBoard becomes a `DataTable` grouped by category, using `StatusBadge` for status. Add the "Coverage per book" `ChartCard` once a daily history view exists (§6). DQ findings becomes a `Panel`. |
| **Ops** `/admin/ops` | `PageHeader`; the auto-refresh indicator moves to the header actions. Its sections become `Panel`s in a 2-column grid on xl. Scheduler-runs and coverage tables become `DataTable`s. |
| **Real bets** `/admin/real-bets` | `StatCard` row (staked, P/L, CLV, count). The existing `real-bets-chart` (recharts line) is rewrapped in `ChartCard` + `chart.tsx` styling with a range switch. Its two raw `<table>`s become `DataTable`s. Link "Place" from the header actions. |
| **Place** `/admin/place` | Out of the menu; reachable from Real bets. `PageHeader` + `Panel` only. |
| **CS2 / LoL / Tennis** | They go in the collapsed "Other sports" group. Minimal pass: `PageHeader`, strip the `max-w-*` wrappers, convert raw tables to `components/ui/table` inside a `Panel`. No new charts, because the owner does not use these pages. |

---

## 11. Build order and what is added

1. Tokens (§2 block) + `components/oi/{Panel,SectionLabel,StatusBadge,TrendPill,Segmented,PageHeader}`, with no page changes. Smoke test: tokens exist and are used by the shell.
2. Shell: shadcn `sidebar` + `tooltip` + `skeleton` + `use-mobile` (copied files, no npm), rewire `AdminSidebar`/`AdminShell`, then add the top bar, breadcrumb and cookie state. **Coordinate with the session that owns the uncommitted shared shell.**
3. `StatCard` + `chart.tsx` (recharts, present) → Overview rebuild + Bots FleetStrip.
4. `@tanstack/react-table` (**the only new npm package**) → `DataTable` → Bots table, then Shadow picks, Feeds, Ops, Real bets.
5. Command palette + notifications.
6. Charts needing data work: junk-control weekly series, `feed_book_daily`. Each gets its own row if it outlives this task.
7. *(Optional)* light toggle, after the colour migration.

New dependencies: **`@tanstack/react-table` only.** Copied-in shadcn files: sidebar, tooltip, skeleton, chart,
use-mobile. Not added: a template, ApexCharts, cmdk.
