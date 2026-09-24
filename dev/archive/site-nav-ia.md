# SITE-NAV-IA — Navigation & Information Architecture Proposal

> Research-only. No implementation until a design is agreed.
> Drafted 2026-06-07. Target: implement ~2026-07-22 alongside GROWTH-WC-SUNSET-PLAN.

---

## Current route inventory

### Root-level (public / marketing)
| Route | Classification |
|-------|---------------|
| `/` | Landing |
| `/login` / `/signup` | Auth |
| `/pricing` | Marketing |
| `/changelog` | Trust / marketing |
| `/privacy` / `/terms` | Legal |

### App routes — primary features
| Route | Classification |
|-------|---------------|
| `/matches` | Primary nav |
| `/matches/[id]` | Primary nav |
| `/value-bets` | Primary nav |
| `/live` | Primary nav |
| `/bankroll` | Secondary (personal) |
| `/my-picks` | Secondary (personal) |
| `/profile` | Secondary (personal) |
| `/welcome` | Onboarding |

### App routes — analytics / track record
| Route | Classification |
|-------|---------------|
| `/accuracy` | Secondary |
| `/performance` | Secondary |
| `/track-record` | Secondary |
| `/predictions` | Secondary |
| `/predictions/[league]` | Secondary |
| `/predictions/[league]/[fixture]` | Secondary |

### SEO / educational content
| Route | Classification |
|-------|---------------|
| `/how-it-works` | SEO/trust |
| `/methodology` | SEO/trust |
| `/learn` / `/learn/[term]` | SEO/educational |
| `/vs` / `/vs/[competitor]` | SEO/marketing |
| `/recaps` / `/recaps/[slug]` | SEO/editorial |

### World Cup (temporary until ~2026-07-19)
| Route | Classification |
|-------|---------------|
| `/world-cup` + 10 sub-routes | Campaign hub |

### Admin
`/admin/bots`, `/admin/ops`, `/admin/place`, `/admin/real-bets` — internal only

---

## Problem statement

**Current nav has 8 primary links** (Matches, Value Bets, Live, Accuracy, Bankroll, My Picks, Profile, World Cup). Several pages that matter for SEO and trust (/methodology, /learn, /vs, /recaps) are only reachable via Google — not from within the app. As page count grows post-WC, this breaks completely.

**Goal:** every page reachable in ≤2 taps from anywhere. SEO/trust pages shouldn't require Google to discover.

---

## Proposed groupings

### Group A — "Intelligence" (core product, primary nav)
- Matches
- Value Bets
- Live

### Group B — "Performance" (track record, secondary nav or dropdown)
- Accuracy
- Performance / Track Record
- Predictions

### Group C — "Learn" (educational, footer + dropdown)
- Methodology
- How It Works
- Glossary (/learn)
- Competitor comparisons (/vs)

### Group D — "Stories" (editorial, footer)
- Match Recaps

### Group E — Personal (profile flyout or footer)
- Bankroll
- My Picks
- Profile

---

## Three concrete options

### Option 1: Footer-only for content pages (low effort)
Move /methodology, /how-it-works, /learn, /vs, /recaps to a proper footer (currently footer is sparse). Primary nav stays 3-4 links (Matches, Value Bets, Live, + Pricing for anon users). Performance links (Accuracy, Track Record) move to a "Performance" secondary nav row under the primary.

**Pro:** minimal change, works for current scale.
**Con:** content pages still buried; post-WC nav pressure not resolved.

### Option 2: Grouped dropdown nav (medium effort)
Replace flat 8-link primary nav with 3-4 grouped entries:
- **Intelligence** → Matches / Value Bets / Live
- **Performance** → Accuracy / Track Record / Predictions
- **Learn** → Methodology / How It Works / Glossary / vs

Personal pages (Bankroll, My Picks, Profile) move into the account dropdown/avatar menu.

**Pro:** scales to 20+ pages. All pages discoverable in 2 taps.
**Con:** dropdown adds interaction cost. Mobile dropdown patterns need careful implementation.

### Option 3: Persistent sidebar (high effort, best for power users)
Left sidebar (collapsible on mobile) with grouped sections. Common pattern for data-heavy tools (Notion, Linear).

**Pro:** all pages visible at once; no hover required.
**Con:** 3-day frontend effort. Changes entire layout system. Premature for current user count.

---

## Recommendation

**Option 2** after WC sunset. Sequencing:
1. (July 22) Remove `/world-cup` from nav
2. Consolidate Performance links under a "Performance" dropdown
3. Add a minimal footer with /methodology, /how-it-works, /learn, /vs, /recaps
4. Move personal pages (Bankroll, My Picks) to account flyout

Option 1 footer cleanup can happen independently and sooner (it's 1h, no structural change).

---

## Open questions
- Does PostHog show meaningful traffic to /accuracy, /track-record, /performance from within the app? If yes, keep them visible; if no, they can go in a dropdown.
- /recaps: editorial feature or SEO play? If editorial, it deserves primary nav. If SEO, footer is fine.
- Post-WC: does `/world-cup` become an archive section in nav or disappears entirely? GROWTH-WC-SUNSET-PLAN should decide.
