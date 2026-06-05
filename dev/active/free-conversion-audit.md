# GROWTH-FREE-TIER-CONVERSION-MEASURE — audit results

_Generated 2026-06-05 10:18 UTC — excludes superadmin profiles_

## Headline (mature cohorts only — week ≥30 days old)

- **Mature cohorts:** 2 weeks
- **Mature signups (non-superadmin):** 15
- **Currently paid (Pro + Elite):** 0
- **Conversion rate (paid / signups):** **0.00%**
- **Stripe-touched rate (got to checkout):** 26.67%

## All-time view (every cohort, including newest)

- **Total cohorts:** 6 weeks
- **Total signups (non-superadmin):** 35
- **Currently paid (Pro + Elite):** 1
- **All-time conversion rate:** 2.86%
- **Telegram-connected:** 0 (0.00%)
- **Email-clicked:** 0

## Per-cohort breakdown

| Week start | Age (days) | Signups | Still free | Pro | Elite | Stripe-touched | Telegram | Email-click |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-04-27 ✓ | 39 | 3 | 3 | 0 | 0 | 2 | 0 | 0 |
| 2026-05-04 ✓ | 32 | 12 | 12 | 0 | 0 | 2 | 0 | 0 |
| 2026-05-11 — | 25 | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| 2026-05-18 — | 18 | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| 2026-05-25 — | 11 | 5 | 4 | 1 | 0 | 1 | 0 | 0 |
| 2026-06-01 — | 4 | 5 | 5 | 0 | 0 | 0 | 0 | 0 |

_✓ = mature (≥30 days since cohort week — counted in headline). — = too new._

## Decision

**Sample too small for a statistical call.** (15 mature signups; need ≥100 before a tier-rebalance decision is data-driven instead of guessed.) Re-run this audit when N ≥ 100 — for now, hold the current tier balance and prioritise growth tasks (Reddit launch, content engine, directory wave) that *raise* the sample size.

## Honest caveats

1. **N is very small** (35 all-time, 15 mature). Single events swing the rate by percentage points. Treat any number here as directional, not definitive.
2. **No tier-change history.** The `profiles` table records `tier` (current) and `created_at` (signup) but no per-row history of when someone upgraded. We can compute current state but not signup→upgrade lag distribution. **Follow-up:** add a `tier_changed_at` column or a Stripe-webhook-backed `user_tier_history` table so future conversion-time-distribution analysis is possible.
3. **`stripe_customer_id` is a proxy, not a fact.** It means "the user reached Stripe checkout at least once," not "they paid." A user could touch Stripe, abandon, and still appear as Stripe-touched.
4. **No signup-source tracking.** We can't break out conversion by Reddit / direct / SEO / referral because the data isn't captured. **Follow-up:** add a `signup_source` column populated from a UTM-tag-aware signup flow. This would massively improve future analysis.
5. **No country / geo segmentation.** Stripe has the data; we don't surface it in `profiles`. Same UTM-style follow-up applies.

## Re-run cadence

Run this audit monthly until N ≥ 200, then quarterly. Save outputs to `dev/active/free-conversion-audit-YYYY-MM-DD.md` so we can track the rate over time.

