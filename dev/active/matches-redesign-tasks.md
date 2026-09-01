# Matches Page Redesign — Task List
_Created 2026-05-29. Combined from 3 agents + codebase review._

## Architecture Decisions (locked)

- **Discovery vs decision**: Matches = browse all fixtures with value hints. Value Bets = ranked picks with stake + book. Keep both.
- **Edge % is exclusive to Value Bets page.** Matches page shows a binary ⚡ value chip — no number. Clean separation.
- **No "place bet" CTA on match rows.** Stays on match detail only.
- **"no value — in line with model"**: Live tab + starred matches only. Not on every row everywhere.
- **Sub-line signals stay free for all** (already free — don't regress). Value chip also free.
- **"Has value" filter + "By value" sort**: free for all (discovery, not intelligence).
- **A/B/C grade filter → killed.** Replaced by "Has value" + "Top leagues" chips.
- **Naming**: "value bets" everywhere. Kill "value opportunities."

## Open Decisions Resolved

| Question | Decision |
|---|---|
| Value Bets vs Matches redundant? | Keep both — different jobs |
| Best book + CTA on match rows? | No — stays on match detail |
| "no value" label scope? | Live tab + starred matches only |
| Sub-line cap? | 1 per row, most impactful. Never "Volatile odds market" alone. |
| Grade filter? | Kill A/B/C. Replace with Has value + Top leagues |
| Naming? | "value bets" everywhere |

## Key Data Notes

- **Value bet overlay**: fetch `getTodayBets()` in parallel in `MatchListContent`. Build `Map<matchId, {valueCount, topSelection, topMarket}>`. No N+1 — one query, build a Map.
- **Model-favored team (bold)**: use `predictedHome > predictedAway` for upcoming. Score-based for live/finished (already working).
- **"Has value" filter**: computed from value bets map on client — no extra DB query.
- **"Volatile odds market" teaser**: suppress when it fires as the sole teaser. The other teasers (absences, form, importance diff) are genuinely specific — show those.
- **Live momentum cues** ("goal 26'"): `LiveSnapshot` only has score+minute, no events. Cannot template reliably. Defer to Phase 5.

---

## Phase 1 — Data Layer + Controls Cleanup (2–3h)
_No new components. Safe to ship any time._

- [ ] **Fetch value bets on matches page**: In `MatchListContent` (`page.tsx`), add `getTodayBets()` to the `Promise.all`. Build `Map<matchId, {valueCount: number, topSelection: string, topMarket: string}>` server-side. Pass as `valueBets: Record<string, ValueInfo>` to `MatchesClient`.
- [ ] **Kill A/B/C grade filter**: Remove grade filter buttons, `gradeFilter` state, `gradeFiltered` memo, and all dependent logic from `matches-client.tsx`. Remove `GRADE_STYLES` and `dataGrade` badge from `league-accordion.tsx` desktop column header.
- [ ] **Add "Has value" filter chip**: In secondary filter row (after My Games). Filters to league groups where ≥1 match has a value bet. Available to all tiers.
- [ ] **Add "Top leagues" filter chip**: Filters to `leaguePriority != null` matches (already in the data). Replaces grade A filter conceptually.
- [ ] **Naming fix**: Replace all instances of "value opportunities" → "value bets" across the frontend (promo banner, any other copy).
- [ ] **"Volatile odds market" suppression**: In `MatchRow`, suppress it when it's the only item in `match.teasers`. Show nothing — silence is the default negative signal.
- [ ] **My Games active fill**: Give the My Games chip a filled amber background when active (currently just border changes — make it more visible).
- [ ] **Timezone label**: Add a single "Times in your timezone" caption somewhere visible on the page (below the date tabs or below the status tabs).

---

## Phase 2 — Row Redesign (4–5h)
_Edit `league-accordion.tsx`. Applies to both mobile and desktop._

### League Header
- [ ] **"N value" badge**: Replace plain `{matches.length}` badge with:
  - Green pill "N value" when league has value bets
  - Plain muted count when no value bets in league
  - (Requires `valueBets` prop propagated from page → MatchesClient → LeagueAccordion)

### MatchRow — Value Chip
- [ ] **⚡ value chip for 1x2**: When match has a value bet on home/draw/away, show `⚡ Home`, `⚡ Draw`, or `⚡ Away` chip inline with team names (green, small). No edge %.
- [ ] **⚡ value chip for other markets**: For DC/OU/AH value bets, show `⚡ O2.5`, `⚡ DC Home`, etc. No cell highlight (odds columns are 1x2 only).

### MatchRow — Odds Cell
- [ ] **Highlight value cell**: For 1x2 value bets, add subtle green border + background to the matching odds cell (home/draw/away). Subtle — just enough to direct the eye.
- [ ] **Movement arrow inline**: For the highlighted cell, move the `TrendingUp/Down` arrow from corner overlay to inline below the odds number (↘ = drifting our way = good signal). Keep corner icon for non-highlighted cells.

### MatchRow — Team Names
- [ ] **Bold model-favored team**: For upcoming matches, bold the team where `predictedHome > predictedAway` (or away if away > home). Existing score-based bold stays for live/finished.

### MatchRow — Signal Sub-line
- [ ] **Color-coded signal line**: Replace grey italic teasers with colored inline line + icon:
  - Injuries/absences/dead-rubber → red icon (`UserX`) + red-muted text
  - Form decline → amber icon (`TrendingDown`) + amber-muted text
  - Market/odds volatility → blue icon (`Activity`) + blue-muted text
- [ ] **Cap at 1 teaser**: Show only the first item in `match.teasers` (most impactful). Never show "Volatile odds market" as a standalone — suppress it.

### Live MatchRow
- [ ] **Larger minute on mobile**: Increase live minute font from `text-[10px]` to `text-[13px] font-bold`. Make it the clear visual anchor.
- [ ] **Subtle live row tint**: Add `bg-green-950/20` (or similar) background to live match rows to visually separate them from upcoming.
- [ ] **"LIVE" odds label**: On live rows, add a small italic "live odds" label above or below the odds grid so users know these aren't pre-match prices.
- [ ] **"no value" label on live**: When on Live tab and match has no value bet and user is Pro+, show "— no value · model in line with market" sub-line (only if user is Pro+, only on Live tab).

### Odds Columns — Header Labels
- [ ] **1 X 2 column headers**: Add `H  X  A` labels to the column header in `LeagueAccordion` desktop view. On mobile, add a one-time "H / X / A" label or tooltip.

---

## Phase 3 — Sort Toggle (1–2h)
_`MatchesClient` state change._

- [ ] **Sort state**: Add `sortMode: "league" | "value" | "kickoff"` state to `MatchesClient`.
- [ ] **"By value" mode**: Flatten all matches across league groups, sort by `topEdge` descending (from valueBets map). Render as flat list with a thin inline league label on each row. Available to all tiers.
- [ ] **"By kickoff" mode**: Flatten + sort by kickoff time ascending.
- [ ] **Sort UI**: Small `[ By league ▾ ]` dropdown or button group replacing the grade filter row. Default: By league.

---

## Phase 4 — Match Detail Page (separate large task)
_Both agents flag this as highest leverage. Destination of every row tap._

- [ ] Create `dev/active/match-detail-redesign-tasks.md` — spec the "Why this is a value bet" expanded section using existing signal accordion + EdgeBar + signals. This is the biggest single improvement but requires its own planning session.

---

## Phase 5 — Live Momentum Cues (needs pipeline work)
_Blocked: `LiveSnapshot` only stores score+minute, no events._

- [ ] Extend `getLiveSnapshots()` to join latest event from `live_match_events` table (if it exists).
- [ ] Template cue from latest event: "goal 26' · {team} leads", "red card 38' · {team} down to 10".
- [ ] Show as live sub-line on the live row.

---

## What Stays As-Is

| Element | Why |
|---|---|
| Status tabs (All/Live/Upcoming/Finished) | Good — instant orientation |
| Today/Tomorrow date toggle | Fine for now |
| League expand/collapse | Good |
| My Games (favorites) filter | Good — just needs active fill fix |
| Live minute + score layout | Good structure — just needs larger font |

---

## Suggested Build Order

1. **Phase 1** (2–3h) — data layer + controls. Required for Phase 2 to work.
2. **Phase 2** (4–5h) — row redesign. Main value delivery.
3. **Phase 3** (1–2h) — sort toggle falls out once rows are done.
4. **Phase 4** (separate session) — match detail.
5. **Phase 5** (depends on live events in DB) — after pipeline confirms events table.

Total Phase 1–3: ~8–10h.
