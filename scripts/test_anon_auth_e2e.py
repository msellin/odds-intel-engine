#!/usr/bin/env python3
"""
Phase 1-3 anonymous-auth end-to-end backend test.

Calls the Supabase Auth REST API directly + makes user-scoped DB queries
using the returned JWT. Covers everything except the OAuth round-trip
(which requires a real browser to complete the Google redirect).

Run: python3 scripts/test_anon_auth_e2e.py
Requires: .env with SUPABASE_URL + SUPABASE_ANON_KEY (NOT service role).
"""

import os
import sys
import time
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
# Engine .env stores the anon (publishable) key as SUPABASE_KEY; frontend
# uses NEXT_PUBLIC_SUPABASE_ANON_KEY. Either is acceptable here.
SUPABASE_ANON_KEY = (
    os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY")
)

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("ERR: missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")
    sys.exit(1)

AUTH_BASE = f"{SUPABASE_URL}/auth/v1"
REST_BASE = f"{SUPABASE_URL}/rest/v1"
HEAD_ANON = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def section(name):
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")


def pp(label, ok, detail=""):
    sym = "PASS" if ok else "FAIL"
    print(f"  [{sym}] {label}" + (f" — {detail}" if detail else ""))


def with_auth(token):
    return {**HEAD_ANON, "Authorization": f"Bearer {token}"}


def run():
    section("PHASE 1: anonymous user creation + profile row + email=NULL")

    # 1. signInAnonymously — supabase-js calls POST /auth/v1/signup with
    # an empty `data` payload (no email, no password) which kicks off the
    # anonymous flow when "Allow anonymous sign-ins" is enabled.
    r = requests.post(f"{AUTH_BASE}/signup", headers=HEAD_ANON, json={"data": {}})
    pp("signInAnonymously POST /signup returns 200",
       r.status_code == 200,
       f"status={r.status_code} body={r.text[:200]}")
    if r.status_code != 200:
        print("  Cannot continue — anon signup failed at the endpoint.")
        return

    body = r.json()
    anon_token = body.get("access_token")
    anon_user_id = body.get("user", {}).get("id")
    is_anon = body.get("user", {}).get("is_anonymous")
    pp("Response has access_token", anon_token is not None)
    pp("Response has user.id", anon_user_id is not None, anon_user_id)
    pp("user.is_anonymous == True", is_anon is True, f"got {is_anon!r}")

    if not anon_token:
        return

    # 2. profile row was created with email NULL (via trigger)
    r = requests.get(
        f"{REST_BASE}/profiles?id=eq.{anon_user_id}&select=id,email,tier",
        headers=with_auth(anon_token),
    )
    profiles = r.json() if r.ok else []
    pp("profile row exists for anon user", len(profiles) == 1)
    if profiles:
        pp("profile.email IS NULL", profiles[0].get("email") is None,
           f"got {profiles[0].get('email')!r}")
        pp("profile.tier == 'free'", profiles[0].get("tier") == "free",
           profiles[0].get("tier"))

    section("PHASE 2: anon user can write to favorite + tracker tables")

    # 3. user_match_favorites insert
    # Need a real match_id — grab any existing one
    r = requests.get(f"{REST_BASE}/matches?select=id&limit=1", headers=with_auth(anon_token))
    matches = r.json() if r.ok else []
    if not matches:
        pp("ABORT — no matches in DB to test favorite insert", False)
        return
    test_match_id = matches[0]["id"]

    r = requests.post(
        f"{REST_BASE}/user_match_favorites",
        headers={**with_auth(anon_token), "Prefer": "return=representation"},
        json={"user_id": anon_user_id, "match_id": test_match_id},
    )
    pp("anon can INSERT into user_match_favorites",
       r.status_code in (200, 201),
       f"status={r.status_code} {r.text[:100]}")

    # 4. RLS check: anon CANNOT write to match_votes (mig 233 blocks)
    r = requests.post(
        f"{REST_BASE}/match_votes",
        headers=with_auth(anon_token),
        json={"user_id": anon_user_id, "match_id": test_match_id, "vote": "home"},
    )
    pp("anon BLOCKED from INSERT into match_votes (RLS)",
       r.status_code in (401, 403),
       f"got status={r.status_code}")

    # 5. RLS check: anon CANNOT write to match_notes (mig 233 blocks)
    # NB: column is note_text not note
    r = requests.post(
        f"{REST_BASE}/match_notes",
        headers=with_auth(anon_token),
        json={"user_id": anon_user_id, "match_id": test_match_id, "note_text": "test"},
    )
    pp("anon BLOCKED from INSERT into match_notes (RLS)",
       r.status_code in (401, 403),
       f"got status={r.status_code}")

    section("PHASE 3: upgrade via email+password preserves user.id")

    # 6. updateUser({ email, password }) — should succeed AND keep same user.id
    test_email = f"anon-test-{uuid.uuid4().hex[:8]}@oddsintel.test"
    test_password = "test-password-123"

    r = requests.put(
        f"{AUTH_BASE}/user",
        headers=with_auth(anon_token),
        json={"email": test_email, "password": test_password},
    )
    pp("updateUser({email, password}) returns 200",
       r.status_code == 200,
       f"status={r.status_code} {r.text[:200]}")

    if r.ok:
        upgraded = r.json()
        # Supabase responds with the user — id should be unchanged
        upgraded_id = upgraded.get("id")
        pp("user.id preserved after upgrade",
           upgraded_id == anon_user_id,
           f"before={anon_user_id} after={upgraded_id}")

    # 7. profile row still has same id, email about to land after confirm
    r = requests.get(
        f"{REST_BASE}/profiles?id=eq.{anon_user_id}&select=id,email",
        headers=with_auth(anon_token),
    )
    profiles = r.json() if r.ok else []
    pp("profile.id still matches anon user.id",
       len(profiles) == 1 and profiles[0]["id"] == anon_user_id)

    # 8. favorite from Phase 2 should still exist (same user.id)
    r = requests.get(
        f"{REST_BASE}/user_match_favorites?user_id=eq.{anon_user_id}&match_id=eq.{test_match_id}",
        headers=with_auth(anon_token),
    )
    favs = r.json() if r.ok else []
    pp("favorite_match from anon phase still owned by upgraded user",
       len(favs) == 1)

    section("CLEANUP — removing test rows")
    requests.delete(
        f"{REST_BASE}/user_match_favorites?user_id=eq.{anon_user_id}",
        headers=with_auth(anon_token),
    )
    print("  (test user retained — manually delete from auth.users dashboard if desired)")
    print(f"\n  Test user id: {anon_user_id}")
    print(f"  Test user email (upgraded to): {test_email}")


if __name__ == "__main__":
    run()
