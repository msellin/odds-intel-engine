# Anonymous Auth — Context

Last updated: 2026-06-10
Status: All 4 phases shipped same day. See `anon-auth-plan.md` for full implementation log + resolved decisions.

## Quick file inventory

### Engine
| File | What it does |
|---|---|
| `supabase/migrations/234_anon_auth_support.sql` | profiles.email nullable + trigger fix |
| `supabase/migrations/235_anon_rls_hardening.sql` | RLS on 9 tables + anon-block on match_votes/match_notes |
| `supabase/migrations/236_pit_team_map_security_invoker.sql` | view security invoker fix |
| `supabase/migrations/237_ops_snapshot_anon_metrics.sql` | adds anon_users_* columns to ops_snapshots |
| `workers/api_clients/supabase_client.py` (`write_ops_snapshot`) | populates anon metrics + fixes total_users to exclude anon |
| `workers/jobs/prune_anon_users.py` | weekly prune cron, hard cap 10k |
| `workers/scheduler.py` (`job_prune_anon_users`) | Sun 02:00 UTC registration |
| `scripts/test_anon_auth_e2e.py` | backend E2E test (broken by captcha — useful pre-deploy only) |

### Frontend (`../odds-intel-web/`)
| File | What it does |
|---|---|
| `src/lib/anon-auth.ts` | `ensureAnonUser(supabase, source)` lazy-create helper |
| `src/lib/turnstile.ts` | Invisible Turnstile widget loader, returns single-use token |
| `src/components/auth-provider.tsx` | exposes `isAnonymous`, `openUpgradeModal(trigger)` |
| `src/components/upgrade-modal.tsx` | upgrade UI — Google/Discord via linkIdentity, email+password via updateUser |
| `src/components/upgrade-modal-mount.tsx` | mounts modal at root layout |
| `src/components/anon-upgrade-banner.tsx` | sticky banner, 7d dismissal cooldown via localStorage |
| `src/components/match-favorite-button.tsx` | calls ensureAnonUser, opens modal on 3rd fav |
| `src/components/match-pick-button.tsx` | calls ensureAnonUser on first tracker pick |
| `src/components/pricing-cards.tsx` | handles 403 anonymous_upgrade_required → opens modal |
| `src/app/api/stripe/checkout/route.ts` | returns 403 for anon users (no email = can't create Stripe customer) |
| `src/components/posthog-provider.tsx` | anon users get `register({distinct_id})` not `identify()` |

## Environment variables

| Var | Where | Value |
|---|---|---|
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | Vercel (production / preview / dev) | `0x4AAAAAADiENvRT4baX7p_D` (public) |
| Turnstile Secret | Supabase Auth → Attack Protection | (in Supabase only — never in Vercel envs) |
| Anonymous Auth toggle | Supabase Auth → Providers → User Signups | ON |
| Manual linking toggle | Supabase Auth → Providers → User Signups | ON |
| Confirm email | Supabase Auth → Providers → User Signups | ON |
| Captcha protection | Supabase Auth → Attack Protection | ON (Turnstile provider) |

## PostHog event suite

Funnel events to filter on for activation/conversion analysis:

```
anon_user_create_attempt           { source }                    — ensureAnonUser called
anon_user_captcha_token_obtained   { source }                    — Turnstile gave us a token
anon_user_created                  { source }                    — Supabase returned an anon user
anon_user_create_error             { source, error_message,
                                     had_captcha_token }         — captcha fail / rate limit / etc.

favorite_match_added                                              — first activation signal
tracker_pick_made                  { selection }                  — second activation signal

anon_upgrade_trigger_fired         { trigger }                    — 3rd favorite hit
anon_upgrade_modal_shown           { trigger }                    — modal rendered
anon_upgrade_method_chosen         { provider, trigger }          — clicked Google/Discord/password
anon_upgrade_success               { provider, email_domain,
                                     trigger, user_id }           — link / updateUser worked
anon_upgrade_error                 { provider, error_message,
                                     trigger }                    — usually "manual linking disabled" or merge conflict
anon_upgrade_conflict              { email_domain, trigger }      — email already used by a different real user
anon_upgrade_banner_clicked        { pathname }
anon_upgrade_banner_dismissed      { pathname }
```

## Key gotchas (lessons learned)

1. **Test pollution**: my E2E test script had a duplicate `signInAnonymously` call, created 9 fake anon users that looked like real growth. Fixed in `b146544`. Lesson: scripts that write to production DB need explicit dry-run modes.

2. **Migration number collisions**: my anon-auth migrations 232/233/234 collided with CS2 work that landed in parallel. Renumbered to 234/235/236. Lesson: bump migration numbers conservatively if there's any chance of parallel commits.

3. **Captcha enforcement breaks backend tests**: once captcha is on in Supabase, ANY caller without a browser-issued Turnstile token gets `400 captcha_failed`. The E2E test now fails at the first step. Use Cloudflare's "always-pass" test keys (`1x0000000000000000000000000000000AA`) if you need to revive it.

4. **Supabase anti-enumeration**: with "Confirm email" ON, `signUp` for an email that already exists returns a fake-looking success (200, user object, no session) instead of an error. This means the unified `/login` page's signin-then-signup fallback can silently no-op for existing-user-wrong-password scenarios. The `/login` recovery panel surfaces magic-link + reset options to catch this.

5. **Manual linking toggle**: `linkIdentity()` requires "Allow manual linking" in Supabase Auth. It defaults to OFF. Without it, the OAuth upgrade path in the modal silently fails.

6. **profile.email='' vs NULL**: pre-mig-234 trigger used `COALESCE(email, '')`. Mig 234 fixes the trigger; defensively normalises any pre-existing `''` rows to NULL.

## Other login providers — deferred

User raised Facebook + Microsoft + Apple Sign-In + GitHub during the session. All deferred until anon-auth conversion data justifies expanding the provider list. None require code changes here — they'd be added as separate `signInWithOAuth({provider: …})` buttons in the existing flow.

## Things still on the list

| Item | Owner | Status |
|---|---|---|
| Cloudflare WAF rate-limit rule on `/auth/v1/signup` | User (Cloudflare dashboard) | Not started — optional defense in depth |
| Hide email-based UI for anon users (digest opt-in, telegram link) | Claude | Backend already blocks; UI cosmetic only |
| Phase 4 ops dashboard tile rendering (frontend) | Claude | Backend ops_snapshot writes the data; frontend reads exist as raw columns |
| Email digest exclusion for anon | Verify | Should already be excluded because `email IS NULL`, but worth a sanity check |

## How to resume work

If returning to this in a future session, read in this order:
1. `anon-auth-plan.md` — what was built, why, with which decisions
2. `anon-auth-context.md` (this file) — files + gotchas
3. `anon-auth-tasks.md` — what's done, what's left
4. Git: `git log --oneline --grep ANON-AUTH` for the 4 commits

Then look at PostHog conversion funnel events to see how the production data is shaping up before deciding what to invest in next.
