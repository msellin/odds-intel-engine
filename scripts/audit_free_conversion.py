"""
GROWTH-FREE-TIER-CONVERSION-MEASURE — quantify free → paid conversion.

The user-facing question: is our free tier so generous that nobody upgrades,
or so restrictive that signups drop off? Both downstream pricing decisions
(GROWTH-TIER-SIMPLIFY-SPIKE + GROWTH-PRICING-AB) need this number to make
data-driven calls instead of guessing.

Walks the `profiles` table, segments by signup-cohort week, and computes:
  - Total signups per cohort
  - Current paid count + rate (tier IN ('pro', 'elite'))
  - Stripe-touched count (stripe_customer_id IS NOT NULL → proxy for
    "got to checkout at least once")
  - Activity signals (telegram_chat_id present, email engagement)
  - Mature-cohort filter — only count cohorts ≥30 days old for the
    honest conversion-rate headline number, since newer cohorts haven't
    had time to convert

Output: dev/active/free-conversion-audit.md with headline + cohort table +
decision recommendation against the thresholds documented in
GROWTH-FREE-TIER-CONVERSION-MEASURE:
  - <2%  → free tier too generous (consider tightening — fewer free
           picks, hide more features behind paywall)
  - 2-5% → working; leave alone
  - >5%  → free tier too restrictive (consider loosening — adoption
           hook may be undersized)

**Schema reality check (2026-06-05):** there is no per-row history of
tier changes (no `tier_changed_at` column, no audit log table). We CAN
detect "ever touched Stripe" via `stripe_customer_id` and "currently
paid" via `tier`. We CANNOT compute "lag from signup to first upgrade"
without a history table — flagged as a follow-up at the end of the
output doc.

Run:
    python3 scripts/audit_free_conversion.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Conversion-rate thresholds (per GROWTH-FREE-TIER-CONVERSION-MEASURE)
RATE_TOO_GENEROUS = 0.02   # <2% → free tier too generous; tighten
RATE_HEALTHY_LOW = 0.02    # 2-5% = healthy
RATE_HEALTHY_HIGH = 0.05
RATE_TOO_RESTRICTIVE = 0.05  # >5% → free tier too restrictive; loosen

# A cohort is "mature" once at least this many days have passed since the
# end of that week — newer cohorts haven't had time to convert and would
# bias the headline number low.
MATURE_DAYS = 30


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    return psycopg2.connect(url)


def fetch_cohorts() -> list[dict]:
    sql = """
    SELECT
        DATE_TRUNC('week', created_at)::date AS week_start,
        COUNT(*) AS signups,
        COUNT(*) FILTER (WHERE tier::text = 'free') AS still_free,
        COUNT(*) FILTER (WHERE tier::text = 'pro') AS now_pro,
        COUNT(*) FILTER (WHERE tier::text = 'elite') AS now_elite,
        COUNT(*) FILTER (WHERE tier::text IN ('pro','elite')) AS now_paid,
        COUNT(*) FILTER (WHERE stripe_customer_id IS NOT NULL) AS stripe_touched,
        COUNT(*) FILTER (WHERE telegram_chat_id IS NOT NULL) AS telegram_connected,
        COUNT(*) FILTER (WHERE last_email_clicked_at IS NOT NULL) AS email_clicked
    FROM profiles
    WHERE COALESCE(is_superadmin, FALSE) = FALSE
    GROUP BY 1
    ORDER BY 1;
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def aggregate(cohorts: list[dict], mature_only: bool) -> dict:
    today = date.today()
    if mature_only:
        rows = [
            c for c in cohorts
            if (today - c["week_start"]).days >= MATURE_DAYS
        ]
    else:
        rows = cohorts

    signups = sum(c["signups"] for c in rows)
    now_paid = sum(c["now_paid"] for c in rows)
    stripe_touched = sum(c["stripe_touched"] for c in rows)
    telegram = sum(c["telegram_connected"] for c in rows)
    email_clicked = sum(c["email_clicked"] for c in rows)
    return {
        "cohorts": len(rows),
        "signups": signups,
        "now_paid": now_paid,
        "stripe_touched": stripe_touched,
        "telegram_connected": telegram,
        "email_clicked": email_clicked,
        "paid_rate": (now_paid / signups) if signups else None,
        "stripe_rate": (stripe_touched / signups) if signups else None,
        "telegram_rate": (telegram / signups) if signups else None,
    }


def recommend(rate: float | None, n: int) -> str:
    if rate is None or n < 100:
        return (
            "**Sample too small for a statistical call.** "
            f"({n} mature signups; need ≥100 before a tier-rebalance decision is "
            "data-driven instead of guessed.) "
            "Re-run this audit when N ≥ 100 — for now, hold the current tier "
            "balance and prioritise growth tasks (Reddit launch, content engine, "
            "directory wave) that *raise* the sample size."
        )
    if rate < RATE_TOO_GENEROUS:
        return (
            f"**Conversion rate {rate:.2%} is below 2% → free tier is too generous.** "
            "Consider tightening: reduce free daily picks (1/day → 1/week), hide "
            "more match-detail data behind the Pro gate, or add a soft paywall on "
            "the 5th-or-later session. Track GROWTH-TIER-SIMPLIFY-SPIKE."
        )
    if rate <= RATE_HEALTHY_HIGH:
        return (
            f"**Conversion rate {rate:.2%} is healthy (2-5% band).** Tier balance "
            "is working as designed. No structural changes recommended. Use this "
            "baseline to A/B test pricing in GROWTH-PRICING-AB (raise floor or "
            "introduce a higher Elite tier)."
        )
    return (
        f"**Conversion rate {rate:.2%} is above 5% → free tier may be too restrictive.** "
        "Counterintuitive but real: hyper-restrictive free tiers can suppress "
        "top-of-funnel volume even with high conversion. Consider relaxing the "
        "Free tier to test whether total paid users (signups × conversion) "
        "increases, even if conversion rate drops."
    )


def write_md(out_path: Path, cohorts: list[dict], headline: dict, all_time: dict):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    today = date.today()
    lines = [
        "# GROWTH-FREE-TIER-CONVERSION-MEASURE — audit results",
        "",
        f"_Generated {now} — excludes superadmin profiles_",
        "",
        "## Headline (mature cohorts only — week ≥30 days old)",
        "",
        f"- **Mature cohorts:** {headline['cohorts']} weeks",
        f"- **Mature signups (non-superadmin):** {headline['signups']}",
        f"- **Currently paid (Pro + Elite):** {headline['now_paid']}",
        f"- **Conversion rate (paid / signups):** "
        + (f"**{headline['paid_rate']:.2%}**" if headline["paid_rate"] is not None else "n/a"),
        f"- **Stripe-touched rate (got to checkout):** "
        + (f"{headline['stripe_rate']:.2%}" if headline["stripe_rate"] is not None else "n/a"),
        "",
        "## All-time view (every cohort, including newest)",
        "",
        f"- **Total cohorts:** {all_time['cohorts']} weeks",
        f"- **Total signups (non-superadmin):** {all_time['signups']}",
        f"- **Currently paid (Pro + Elite):** {all_time['now_paid']}",
        f"- **All-time conversion rate:** "
        + (f"{all_time['paid_rate']:.2%}" if all_time['paid_rate'] is not None else "n/a"),
        f"- **Telegram-connected:** {all_time['telegram_connected']} ({all_time['telegram_rate']:.2%})"
        if all_time["telegram_rate"] is not None else "- Telegram-connected: n/a",
        f"- **Email-clicked:** {all_time['email_clicked']}",
        "",
        "## Per-cohort breakdown",
        "",
        "| Week start | Age (days) | Signups | Still free | Pro | Elite | Stripe-touched | Telegram | Email-click |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in cohorts:
        age = (today - c["week_start"]).days
        mature_flag = "✓" if age >= MATURE_DAYS else "—"
        lines.append(
            f"| {c['week_start']} {mature_flag} | {age} | {c['signups']} | "
            f"{c['still_free']} | {c['now_pro']} | {c['now_elite']} | "
            f"{c['stripe_touched']} | {c['telegram_connected']} | {c['email_clicked']} |"
        )
    lines += [
        "",
        f"_✓ = mature (≥{MATURE_DAYS} days since cohort week — counted in headline). — = too new._",
        "",
        "## Decision",
        "",
        recommend(headline["paid_rate"], headline["signups"]),
        "",
        "## Honest caveats",
        "",
        f"1. **N is very small** ({all_time['signups']} all-time, {headline['signups']} mature). "
        "Single events swing the rate by percentage points. Treat any number here as "
        "directional, not definitive.",
        "2. **No tier-change history.** The `profiles` table records `tier` (current) and "
        "`created_at` (signup) but no per-row history of when someone upgraded. We can compute "
        "current state but not signup→upgrade lag distribution. **Follow-up:** add a "
        "`tier_changed_at` column or a Stripe-webhook-backed `user_tier_history` table so "
        "future conversion-time-distribution analysis is possible.",
        "3. **`stripe_customer_id` is a proxy, not a fact.** It means \"the user reached Stripe checkout at least once,\" not \"they paid.\" A user could touch Stripe, abandon, and still appear as Stripe-touched.",
        "4. **No signup-source tracking.** We can't break out conversion by Reddit / direct / SEO / referral because the data isn't captured. **Follow-up:** add a `signup_source` column populated from a UTM-tag-aware signup flow. This would massively improve future analysis.",
        "5. **No country / geo segmentation.** Stripe has the data; we don't surface it in `profiles`. Same UTM-style follow-up applies.",
        "",
        "## Re-run cadence",
        "",
        "Run this audit monthly until N ≥ 200, then quarterly. Save outputs to "
        "`dev/active/free-conversion-audit-YYYY-MM-DD.md` so we can track the rate over time.",
        "",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dev/active/free-conversion-audit.md")
    args = parser.parse_args()

    cohorts = fetch_cohorts()
    if not cohorts:
        print("No profiles in DB — nothing to compute.")
        return 0

    headline = aggregate(cohorts, mature_only=True)
    all_time = aggregate(cohorts, mature_only=False)

    print(f"Mature cohorts (≥{MATURE_DAYS}d): "
          f"{headline['signups']} signups, {headline['now_paid']} paid"
          + (f", {headline['paid_rate']:.2%} rate" if headline['paid_rate'] is not None else ""))
    print(f"All-time:                "
          f"{all_time['signups']} signups, {all_time['now_paid']} paid"
          + (f", {all_time['paid_rate']:.2%} rate" if all_time['paid_rate'] is not None else ""))
    print(f"Stripe-touched (proxy for 'got to checkout'): "
          f"{all_time['stripe_touched']} ({all_time['stripe_rate']:.2%})")

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_md(out_path, cohorts, headline, all_time)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
