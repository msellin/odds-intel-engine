# Anonymous Auth — Tasks

All Phase 1–4 implementation tasks. Marked done as each shipped.
See `anon-auth-plan.md` for design rationale, `anon-auth-context.md` for file inventory.

## Phase 1 — Foundation (DB + isAnonymous plumbing)

- [x] Enable "Allow anonymous sign-ins" in Supabase (user)
- [x] Migration 234 — `profiles.email` DROP NOT NULL + trigger passes NULL through
- [x] Migration 234 — defensive normalisation of pre-existing `email=''` rows to NULL
- [x] `UserProfile.email` typed `string | null` in auth-provider
- [x] `useAuth()` exposes `isAnonymous` (truth: `user.is_anonymous`)
- [x] App-code audit for `profile.email` non-null assumptions — found 1 (PostHog identify), fixed
- [x] Stripe checkout API returns 403 `anonymous_upgrade_required` for anon users
- [x] PostHog uses `register({distinct_id})` for anon, only `identify()` after upgrade

## Phase 2 — Lazy creation on first save

- [x] `src/lib/anon-auth.ts` — `ensureAnonUser(supabase, source)` helper
- [x] MatchFavoriteButton: removed `!user` gate, calls ensureAnonUser
- [x] MatchPickButton: removed signup CTA card, calls ensureAnonUser
- [x] PostHog events for source attribution (`anon_user_create_attempt`, `anon_user_created`)

## Phase 3 — Upgrade UX

- [x] `UpgradeModal` component — Google/Discord via linkIdentity, email+password via updateUser
- [x] Merge-conflict path (email already belongs to existing real user) handled with clear data-loss warning
- [x] `UpgradeModalMount` component — wires AuthProvider state to UpgradeModal
- [x] Mounted in root layout next to GoogleOneTap
- [x] `AnonUpgradeBanner` — sticky banner across app pages, suppressed on auth pages
- [x] Banner 7-day dismissal cooldown via localStorage
- [x] 3rd-favorite trigger: MatchFavoriteButton opens modal 350ms after 3rd star
- [x] Stripe-checkout trigger: PricingCards handles 403 → opens modal
- [x] Banner click trigger
- [x] PostHog: `anon_upgrade_modal_shown`, `anon_upgrade_method_chosen`, `anon_upgrade_success`, `anon_upgrade_error`, `anon_upgrade_conflict`

## Phase 4 — Hardening

- [x] Cloudflare Turnstile widget created (user — invisible mode, 3 hostnames)
- [x] `NEXT_PUBLIC_TURNSTILE_SITE_KEY` set in Vercel (production / preview / dev)
- [x] `src/lib/turnstile.ts` — invisible widget loader, returns single-use token
- [x] `ensureAnonUser` calls `getTurnstileToken()` and passes `captchaToken` to `signInAnonymously`
- [x] Supabase Auth → Attack Protection: captcha provider = Cloudflare Turnstile, secret pasted, enabled (user)
- [x] Verified enforcement via direct API call (`captcha_failed` response without token)
- [x] Migration 237 — adds anon-tracking columns to `ops_snapshots`
- [x] `write_ops_snapshot` populates anon metrics + fixes `total_users` to exclude anon
- [x] `workers/jobs/prune_anon_users.py` — weekly prune, 90-day idle threshold, 10k hard cap
- [x] Scheduler registration Sun 02:00 UTC
- [ ] Cloudflare WAF rate-limit rule on `/auth/v1/signup` (10 req/min/IP) — user, optional defense in depth
- [ ] Ops dashboard frontend tiles for the new anon_* columns — Claude TBD if user wants the tiles rendered
- [ ] Hide email-based UI features for anon (digest opt-in, telegram link, etc.) — cosmetic, backend already blocks

## Cleanup / verified state

- [x] Removed 9 anon test users that polluted production (user, via Supabase dashboard)
- [x] Fixed duplicate `signInAnonymously` call in test script
- [x] Fixed test script `match_notes` payload (`note_text` not `note`)
- [x] Resolved migration-number collision (renumbered 232/233/234 → 234/235/236)
- [x] Fixed pre-existing migration 229 (`cs2_results.is_lan` backfill) that was blocking all subsequent migrations
- [x] Fixed pre-existing migration 231 (`bots.bot_name` → `name`, added `strategy` NOT NULL)
- [x] PRIORITY_QUEUE.md updated with ANON-AUTH-PHASE-1/2/3/4 entries
- [x] anon-auth-plan.md updated from "Draft" to "Shipped"
- [x] anon-auth-context.md created
- [x] anon-auth-tasks.md created (this file)

## Future / deferred

- [ ] Other login providers (Facebook / Microsoft / Apple Sign-In / GitHub) — deferred until conversion data justifies the expansion
- [ ] Pre-commit hook to run migrations against a throwaway Supabase project — prevent the migration-blocker class of bug we hit twice
- [ ] Smoke test for anon auth (in `scripts/smoke_test.py`) — source inspection of `ensureAnonUser` + `UpgradeModal` + key migration columns

## How to read this file

Each `- [x]` is shipped to production. Each `- [ ]` is either deferred (with reason) or pending user action. No item should disappear silently — if it becomes irrelevant, mark it `- [skipped]` with a one-line note.
