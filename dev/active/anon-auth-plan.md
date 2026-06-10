# Anonymous → Authenticated User Flow

**Status:** Draft plan — pending decisions from user before implementation starts.

**Created:** 2026-06-10
**Owner:** Margus + Claude
**Estimated effort:** 3–4 days across 4 phases

---

## Why

Current signup funnel converts at ~8% on magic link. Even with password auth (just shipped, AUTH-PASSWORD) we still demand a commitment before the user has experienced anything. Anonymous auth lets users *use* OddsIntel first — favorite teams, log tracker picks, see personalized "My Matches" — and prompts signup only at the moment they're already invested.

Industry data: anon-first apps (Notion, Linear, Vercel dashboard, even Stripe checkout draft state) see 2–5× activation improvement vs. forced-signup-first apps.

---

## Goal

A visitor lands on `oddsintel.app`, immediately starts using the product without seeing /signup. The moment they try to persist something (favorite a team, save a pick), we silently create an anonymous Supabase user behind the scenes. Their data lives against that user_id. When they later upgrade (Google / email+password / magic link), the **same user_id is preserved** and all their data carries over seamlessly.

---

## Architecture decisions

### 1. When to create the anonymous user

| Option | Pro | Con |
|---|---|---|
| A. Eagerly on first visit | Simple — every visitor has a user_id | Inflates `auth.users` with throwaway rows; cost; metrics dirty |
| B. Lazily on first save action | Clean `auth.users` | More code paths to instrument; race conditions |
| **C. Eager Supabase row, but data in localStorage until first save** | Clean DB, simple state, fast UX, single migration point | localStorage doesn't survive device switches (acceptable for anon) |

**Decision: C.** Anonymous users get value from localStorage (favorites are cheap to mirror there). On their first attempt to write something that needs DB persistence (favorite a team, save a pick), we call `supabase.auth.signInAnonymously()` THEN migrate localStorage state to DB in the same transaction. From that moment on, the user is operating against a real anon user_id.

### 2. What anonymous users can do

| Write | Allowed for anon? | Reason |
|---|---|---|
| `user_favorites` | ✅ | Primary activation moment |
| `tracker_picks` | ✅ | Second-most engagement signal |
| `match_notes` | ❌ | Low value, abuse risk |
| `community_votes` | ❌ | Vote inflation risk |
| `profile` updates | ❌ | No identity to update |
| Telegram link | ❌ | Requires email |
| Email digest opt-in | ❌ | No email |
| Stripe checkout | ❌ | Force upgrade first |

Reading is unaffected — all the public-read RLS policies already cover anon users (whether logged-out anon or `is_anonymous=true` Supabase user, `anon` role still applies).

### 3. How anonymous users upgrade

Supabase offers two methods that preserve `user.id`:
- `updateUser({ email, password })` — adds credentials to the anon user
- `linkIdentity({ provider: 'google' })` — links an OAuth identity to the anon user

Either keeps the same `auth.users.id`, so all data keyed by user_id transparently survives.

**Upgrade triggers** (ordered by signal strength):
1. After **3rd favorite team starred** → contextual modal: "Save your favorites across devices?"
2. When they hit a **Pro feature gate** → "Create an account to start a 7-day free trial"
3. Persistent **dismissible header banner** for anon users — re-shows after 7 days if dismissed
4. After **5th tracker pick logged** → "You're tracking 5 picks — save your hit rate by signing up"

### 4. Profile row creation

The existing trigger on `auth.users` insert (which creates a `profiles` row) needs to handle anonymous users:
- Anon users have `email = NULL` in `auth.users`
- Our `profiles.email` is probably `NOT NULL` — trigger will fail

**Three options:**
- A. Make `profiles.email` nullable → cleanest semantically, requires app-code audit
- B. Generate placeholder email like `anon-{uuid}@anonymous.local` → ugly, leaks into UI
- C. Trigger skips anonymous users → profile row created at upgrade time → app must handle "anon user with no profile row"

**Decision: A.** Make `profiles.email` nullable. Cheapest long-term. Audit needed (Phase 1) — primary risk areas: profile display name fallback, email digest job, Stripe webhook handlers.

### 5. Tier handling

Anonymous users are effectively `free` tier:
- They see all free features
- They cannot subscribe (no email → no Stripe customer)
- Server-side gates already use `profile.tier`; `null` profile → treated as not-pro (correct)

`isPro` and `isElite` in `src/app/(app)/matches/[id]/page.tsx` etc. need to handle anonymous users — likely already do (default false on missing profile), but worth verifying.

### 6. RLS audit

Most current policies are user_id-keyed:
```sql
auth.uid() = user_id  -- works fine for anon
```

Need to grep `supabase/migrations/` for any policies that:
- Check `email IS NOT NULL` (won't apply to anon)
- Check `tier IN (...)` without nullability handling
- Reference `auth.users.email`

If found, update to handle anon gracefully (allow or explicitly block, but don't crash).

### 7. The upgrade UX

When anon user triggers upgrade (modal in-place, **not** redirect — preserves context):

```
┌───────────────────────────────────────┐
│ Save your favorites across devices    │
│                                       │
│ You're following 3 teams. Sign up     │
│ free to keep them safe.               │
│                                       │
│ [ Continue with Google ]              │
│ [ Continue with Discord ]             │
│ ──────── or ────────                  │
│ Email:    [_______________]           │
│ Password: [_______________]           │
│ [ Create my account ]                 │
│                                       │
│ Already have an account? Sign in →    │
└───────────────────────────────────────┘
```

On submission:
- **OAuth**: `linkIdentity({ provider })` — same user.id, OAuth identity attached. On return from OAuth redirect, modal closes, user is upgraded, profile row populated.
- **Email+password**: `updateUser({ email, password })` — same user.id, credentials added. If "Confirm email" is ON in Supabase, user gets confirmation email; they're still functional as anon until they confirm. If OFF, they're immediately upgraded.
- **Magic link**: handled by `updateUser({ email })` then Supabase emails them; on link click, callback merges identities.

### 8. Merge-conflict handling (CRITICAL)

User flow: Anon user A on shared computer tries to upgrade with email of existing user B. Supabase rejects (`updateUser({email})` errors when email already registered).

UI must handle:
```
"That email is already in use. Sign in to your existing account?
 [ Sign in instead ]   [ Use a different email ]"
```

If they click "Sign in instead": current anon session is **discarded** (data lost) and they sign into existing account. We surface this clearly: *"Your current favorites won't be merged into the existing account. Continue?"*

A future enhancement could attempt server-side data merge across the two user_ids, but that's a v2 feature.

### 9. Spam mitigation

Anonymous signup is free → bot risk → millions of `auth.users` rows.

**Mitigations:**
- Vercel WAF rate-limit on `/auth/v1/signup` (anon endpoint) by IP — 10 per hour per IP
- Supabase auth's built-in rate limiting (already on)
- Weekly prune cron: delete anon users idle >90 days (cascades to favorites/picks)
- Monitor `auth.users WHERE is_anonymous=true` growth rate; alert if >5× expected baseline

### 10. PostHog identification

Currently `identify(user.id)` fires when profile loads. For anon users:
- We DO want to track their events under a stable distinct_id
- We do NOT want to call `identify(user.id)` because PostHog bills per identified user
- Use Supabase user.id as `$distinct_id` via `posthog.register({ distinct_id })` rather than `identify()`. Only call `identify()` after upgrade.

PostHog event suite to add (Phase 3):
- `anon_user_created` — first-time anon signup, with trigger source (favorite vs. pick)
- `anon_favorite_added` (with count)
- `anon_upgrade_modal_shown` (with trigger: 3rd_favorite | pro_gate | banner | nth_pick)
- `anon_upgrade_method_chosen` (provider: google | discord | password | magic)
- `anon_upgrade_success`
- `anon_upgrade_error` (with error_message)
- `anon_upgrade_conflict` (when email already exists)

---

## Phasing

### Phase 1 — Foundation (1–2 days)

- [ ] Enable Anonymous Auth in Supabase Dashboard → Authentication → Providers → Anonymous
- [ ] Supabase migration `NNN_anon_auth_support.sql`:
  - `ALTER TABLE profiles ALTER COLUMN email DROP NOT NULL`
  - Update profile-creation trigger to handle anon users (use `COALESCE(NEW.email, NULL)`)
  - Audit RLS — fix any `email IS NOT NULL` checks
- [ ] App-code audit: grep `profile.email` for places that assume non-null; add `?? "Anonymous"` fallbacks or hide UI for anon
- [ ] `useAuth()` exposes `isAnonymous: boolean` (from `user.is_anonymous` or `profile.email === null`)
- [ ] Smoke test: manually call `signInAnonymously()` in console, verify profile row created, no crashes

### Phase 2 — Activation surface (1 day)

- [ ] Lazy anon-signup wrapper: `ensureAnonUser()` helper that creates anon user on first save attempt
- [ ] Wire into favorite-team click: if no session, `await ensureAnonUser()` then write favorite
- [ ] Wire into tracker-pick save: same pattern
- [ ] LocalStorage migration: existing localStorage favorites (if any) get copied to DB on first ensureAnonUser

### Phase 3 — Upgrade UX (1 day)

- [ ] Build `<UpgradeModal />` reusing OAuth buttons + password fields from /signup
- [ ] Modal triggers: 3rd favorite (state hook), Pro feature click, persistent banner
- [ ] OAuth path: `linkIdentity({ provider })` instead of `signInWithOAuth`
- [ ] Email+password path: `updateUser({ email, password })`
- [ ] Magic-link path: `updateUser({ email })`
- [ ] Merge-conflict modal: handle "email already exists" with clear data-loss warning
- [ ] Hide email-based features (digest opt-in, telegram link, Stripe checkout) for `isAnonymous=true`

### Phase 4 — Hardening (0.5 days)

- [ ] Vercel WAF rule: rate-limit `/auth/v1/signup` to 10/hour/IP
- [ ] PostHog event suite (`anon_*` events listed above)
- [ ] Weekly Railway cron: prune `auth.users WHERE is_anonymous=true AND last_sign_in_at < NOW() - INTERVAL '90 days'` (Phase 1 of GDPR-friendly housekeeping)
- [ ] Ops dashboard tile: anonymous user count + 7-day activation rate (anon → real upgrade %)

---

## Open decisions needed from user

1. **OK to make `profiles.email` nullable?** Schema change; requires app-code audit. Alternative: placeholder email, but uglier.
2. **Where to surface upgrade CTA?** Default plan: persistent dismissible banner + contextual modal on 3rd favorite. Open to suggestions.
3. **Stripe checkout for anon users?** Default plan: force upgrade-to-real-account first. Stripe customer requires email anyway.
4. **Anon prune horizon: 90 days?** GDPR-friendly default. Could go shorter (30d) or longer (180d).
5. **Should anon users count in "Total users" social-proof on landing page?** Default plan: **NO** — count only verified accounts.
6. **Modal-in-place vs. /signup redirect for upgrade?** Default plan: modal. Less context-switching friction.

---

## Risks summary

| Risk | Likelihood | Mitigation |
|---|---|---|
| Bot spam → millions of anon users | Medium | Vercel WAF + Supabase rate-limit + 90d prune |
| App crashes on null profile.email | High at first | Audit + fallbacks in Phase 1 |
| Lost data on upgrade merge-conflict | Low | Clear warning + opt-in confirmation |
| RLS oversight allows anon to do something they shouldn't | Medium | Audit migration + integration test |
| PostHog billing explosion | Low | Use `register({distinct_id})` not `identify()` until upgrade |

---

## When to revisit / decide go/no-go

Wait 24–48h after AUTH-PASSWORD ships to measure:
- New signup conversion lift from password vs. magic link
- New events from the funnel tracking show where actual drop-offs are

If password auth gets us to >20% signup conversion, anon-auth is lower priority. If we're still <15%, anon-auth is the next big lever and we kick off Phase 1.
