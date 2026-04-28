# OddsIntel — UI/UX & Data Source Research

> Combined analysis from 3 AI research passes (Gemini, ChatGPT, Claude) on 2026-04-27.
> Sites analyzed: OddsPortal, Flashscore, SofaScore, FotMob, Bet365.

---

## Consensus Patterns (All 5 Sites Agree)

### 1. Collapsible league accordions — universal
Every site uses collapsible league sections. The differentiator is what's expanded by default:
- **Flashscore/SofaScore/FotMob:** User-pinned/starred leagues expanded
- **OddsPortal/Bet365:** Popular/top leagues expanded
- **All:** Minor leagues collapsed by default

### 2. League filter — sidebar on desktop, compact on mobile
- Desktop: left sidebar with country → league tree + search
- Mobile: horizontal chips, filter sheet, or search input
- **Never** 30+ pills all visible at once

### 3. Visual hierarchy via position + expansion state
- Top leagues first (position)
- Top leagues expanded, minor collapsed (expansion)
- Subtle styling differences (bolder headers, logos) — not heavy UI
- No site uses explicit tier badges

### 4. Dense match rows — 5-7 data points per row
- OddsPortal: time, teams, 1X2 odds, bookmaker count (~6 points)
- Flashscore: time/status, teams, score, 1X2 odds (~7 points)
- FotMob: teams, time/score (~4 points, minimalist)
- Bet365: teams, time, 1X2 odds buttons, "+N markets" (~6 points)
- Key: **one row per match**, not cards. ~40px height max.

### 5. Match list → detail: full-page navigation
All five use full-page nav (not inline expansion). Bet365 does panel on desktop.
Back navigation preserves scroll position.

### 6. Mobile-first essentials
- Sticky date selector
- Bottom tab navigation (FotMob gold standard)
- Swipeable date navigation
- Touch-friendly tap targets (48px min)
- Star/pin system for favorite leagues

---

## Key Insights for OddsIntel

### What to steal:
1. **Flashscore's pin system** — "my leagues" always on top
2. **OddsPortal's density** — inline odds in every row
3. **FotMob's mobile UX** — bottom tabs, swipe dates, minimal chrome
4. **Bet365's hierarchy** — deep collapse tree, nothing shown unless requested
5. **SofaScore's smart defaults** — algorithmic prioritization over raw listing

### What to avoid:
- Showing all 200 matches expanded (our current approach)
- Card grids — take 3x vertical space vs rows
- 30+ filter pills (our current approach)
- Sparse rows with no odds (our compact rows)
- Equal visual weight for all leagues

### The core principle:
> **"Don't build a list. Build a decision engine."**
> Users don't want "200 matches." They want "Which 5 should I care about?"

---

## Recommended OddsIntel Match Row Format

```
│ 🔥 15:00  Arsenal vs Chelsea     2.10  3.40  3.25  → │
│    ↑int   ↑time   ↑teams          ↑H    ↑D    ↑A  ↑nav│
```

- Interest indicator (🔥/—)
- Kickoff time (mono font)
- Home vs Away
- H/D/A best odds (green highlight for best value)
- Chevron for navigation
- ~40px row height, 7 data points

For matches without odds:
```
│ — 15:00  Estonia Team vs Other Team                → │
```

---

## Recommended Layout Structure

```
┌─────────────────────────────────────────────────────────┐
│  Today's Matches           [All] [With Odds] [Value]    │
│  Mon 27 Apr 2026  ‹ ● ›   192 matches · 47 with odds   │
├─────────────────────────────────────────────────────────┤
│  🔍 Filter leagues...                    [Collapse All] │
├─────────────────────────────────────────────────────────┤
│  ▾ 🏴 ENGLAND / PREMIER LEAGUE            🔥 6 matches  │  ← expanded (has odds)
│  │ 15:00  Arsenal vs Chelsea   2.10 3.40 3.25  →       │
│  │ 17:30  Spurs vs Man City    3.60 3.50 1.95  →       │
│  ...                                                    │
│  ▸ 🇪🇪 ESTONIA / PREMIUM LIIGA           — 6 matches   │  ← collapsed (no odds)
│  ▸ 🇱🇻 LATVIA / VIRSLIGA                 — 7 matches   │  ← collapsed
└─────────────────────────────────────────────────────────┘
```

### Expansion rules:
- Leagues WITH odds → expanded by default
- Leagues WITHOUT odds → collapsed (show count only)
- User can toggle any section
- "Collapse All" / "Expand All" toggle
- Cuts visible rows from ~200 to ~50

### Filter:
- Replace pill strip with search input
- Type "eng" → highlights England leagues
- Future: logged-in users can pin/star leagues

---

## Implementation Priority

1. Dense table rows with inline odds (biggest impact)
2. Collapsible league sections (solves scrolling)
3. Collapse fixture-only leagues by default (instant hierarchy)
4. Replace pill filter with search input (cleaner)
5. Sticky header with date + filter (mobile)
6. Future: pinned leagues, swipeable dates

---

> **Note:** Data source research that was previously in this file has been moved to `DATA_SOURCES.md`.
> Signal display strategy is now in `SIGNAL_UX_ROADMAP.md`.
- **Limitation:** Ends 2016. Only big European leagues (no gap countries)
- **Action:** Download for sharp-vs-soft bookmaker analysis. Train model to detect which bookmaker moves signal real information vs noise. Bookmaker behavior patterns persist across eras.

### How these help us

**For historical backtesting:**
- European Soccer DB → multi-bookmaker odds enable "would we have gotten better odds shopping across 13 bookmakers?" analysis + sharp money detection training
- Footiqo → minute-interval data could backtest in-play betting strategies (e.g. "back Under 2.5 at minute 60 when xG < 1.0")
- Footiqo → independent 1xBet closing odds for gap leagues validates our existing signals

**For live predictions:**
- OddAlerts API → 20+ bookmakers real-time = better odds comparison for users + sharp money signal (when Pinnacle diverges from soft bookmakers)
- Footiqo minute-interval patterns → trained model improves live tracker value detection
- European Soccer DB bookmaker patterns → "Pinnacle moved but Bet365 didn't" = sharp action signal, applicable to live odds monitoring
