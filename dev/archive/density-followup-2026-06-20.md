# GROWTH-COPY-DENSITY-AUDIT — 2-week measurement checkpoint

**Target review date:** 2026-06-20 (or later if traffic is still thin)
**Trigger:** ~2 weeks of post-cut PostHog data accumulated

## Context

On 2026-06-06 we shipped a research-backed 49% landing copy reduction
(706 → ~358 visible words) over a single-session 4-day plan. Research
verdict + execution log at `dev/active/density-copy-research-2026-06-06.md`.

PostHog ingestion was silently broken by CSP for weeks before that day;
the CSP fix shipped in the same session (commit `f0aa769` on the web
repo) so we have a clean baseline starting 2026-06-06.

## What to pull (PostHog, last 14 days)

1. **Landing bounce rate** — single-`$pageview` sessions on `/` ÷ total
   landing sessions.
2. **Time on `/` distribution** — `$pageleave timestamp − $pageview
   timestamp`, median + p25/p75.
3. **Funnel: `/` → next page** — what fraction continue to `/value-bets`,
   `/pricing`, `/how-it-works`, `/methodology`, or `/signup`?
4. **Sign-up conversion** — landing sessions that ended in `/signup`
   completion (POSTed) ÷ landing sessions.
5. **Same metrics for `/methodology` + `/how-it-works`** — Day 3
   consolidated /how-it-works. Is `/methodology` still doing
   trust-signalling work for the visitors who click through?

## Decision criteria

| Outcome | Verdict |
|---|---|
| Bounce flat-or-down + signup conversion flat-or-up | Cut+replace worked. File `GROWTH-DESKTOP-DASHBOARD-DENSITY` for the next density cycle. |
| Bounce up significantly (>3pp) | Lost something. Likely candidate: the "Honest about how this works" preamble was doing trust work. Re-add a tightened version (~10 words) and re-measure. |
| Conversion down + bounce flat | Lost something further down the funnel. Likely candidate: the compact pricing CTA section (which we removed) was a meaningful conversion path; consider restoring a slimmer pricing pointer. |
| Signal unclear (small sample) | Wait another 2 weeks before deciding. Don't act on n<200 landing sessions. |

## Pre-checks before pulling data

1. **Verify PostHog actually captured events.** Open PostHog dashboard,
   confirm `$pageview` count > 0 for the last 14 days. If 0, the CSP
   fix didn't deploy or got reverted — investigate before drawing any
   conclusions.
2. **Confirm session count is meaningful.** If <200 landing sessions
   accumulated, this is a "wait longer" signal, not a "decide now"
   signal. Direct traffic on `oddsintel.app` is currently thin so this
   is a realistic risk.
3. **Note any events besides this audit** that might confound — e.g.
   if Reddit launch lands during the window, traffic mix shifts and
   the bounce comparison becomes apples-to-oranges.

## What to publish back

Append findings to `dev/active/density-copy-research-2026-06-06.md`
section "How to measure if this worked." File next-steps follow-ups
in `PRIORITY_QUEUE.md` if anything actionable falls out.

## What NOT to do

- **Don't reverse cuts just because numbers look flat.** Flat bounce
  + flat conversion = the cuts were *neutral*, which means the
  density audit succeeded at its goal (less prose, no engagement loss).
  Only reverse on a clear negative signal.
- **Don't compare to pre-2026-06-06 PostHog data.** It's empty (CSP
  was blocking it). The baseline starts from this measurement window.
- **Don't run the spike again without a hypothesis.** If a second
  density cut is on the table, scope a specific hypothesis (e.g.,
  "cutting the trust strip will reduce time-on-page by X%") and
  A/B test rather than just iterating prose.
