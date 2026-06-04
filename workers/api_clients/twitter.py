"""WC-F2 (2026-06-04) — Twitter / X v2 post client (write-only).

User flow: create a Twitter developer project with Read+Write access; mint
consumer (API) key/secret + a user-context access token/secret pair; store
all four in `.env`. Free tier allows ~1,500 tweets/month — well over the
~104 WC2026 fixtures this client will fire for.

Env vars (ALL FOUR required for any posting — module silently no-ops if
any are missing, so absence is non-blocking for dev / CI):
    TWITTER_API_KEY        consumer key (project API key)
    TWITTER_API_SECRET     consumer secret (project API secret)
    TWITTER_ACCESS_TOKEN   user-context access token (the bot's @handle)
    TWITTER_ACCESS_SECRET  user-context access token secret

Single public function: `post_tweet(text, image_url=None)`. Returns the
tweet ID string on success, None on any failure (creds missing, HTTP
error, OAuth signature reject, network timeout). NEVER raises — caller
must be able to drop us into a settlement loop without try/except scaffolding.

Implementation note: deliberately uses raw HMAC-SHA1 OAuth 1.0a signature
(stdlib only: hmac/hashlib/urllib/secrets) rather than pulling in tweepy
or requests-oauthlib. Twitter v2 POST /2/tweets is the only endpoint we
hit and the signature spec hasn't changed since 2010; adding a 2 MB
dependency for one HTTP call is not worth it.

image_url is accepted for forward-compat (WC-F5 OG image embedding) but
currently NOT used — posting an inline image requires the v1.1
media/upload endpoint + a separate OAuth-signed multipart POST, which is
WC-F5's scope, not F2's. The text already includes the canonical match
URL so Twitter's link-card unfurler will pull the OG image automatically
once the FE ships per-match OG images.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_TWEET_URL = "https://api.twitter.com/2/tweets"


def _quote(s: str) -> str:
    """OAuth 1.0a percent-encoding — RFC 3986 unreserved chars only."""
    return quote(str(s), safe="-._~")


def _build_oauth_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
) -> str:
    """Build the OAuth 1.0a Authorization header for a JSON-body POST.

    The v2 POST /2/tweets body is JSON (not form-encoded), so the signature
    base string is built from the OAuth params ONLY — body params are NOT
    folded in (per RFC 5849 §3.4.1.3.1, JSON bodies are excluded).
    """
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    # Build parameter string: percent-encoded, sorted alphabetically by key.
    param_str = "&".join(
        f"{_quote(k)}={_quote(v)}"
        for k, v in sorted(oauth_params.items())
    )

    # Signature base string: METHOD&URL&PARAMSTRING (each component encoded).
    base_string = "&".join([
        method.upper(),
        _quote(url),
        _quote(param_str),
    ])

    # Signing key: consumer_secret&token_secret (each encoded).
    signing_key = f"{_quote(consumer_secret)}&{_quote(token_secret)}"

    # HMAC-SHA1, base64-encoded.
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")

    oauth_params["oauth_signature"] = signature

    # Build header: OAuth k="v", k="v", ... (alphabetical, quoted)
    return "OAuth " + ", ".join(
        f'{_quote(k)}="{_quote(v)}"'
        for k, v in sorted(oauth_params.items())
    )


def post_tweet(text: str, image_url: Optional[str] = None) -> Optional[str]:
    """Post `text` to the configured Twitter/X account.

    Returns the new tweet's ID (string) on success, None on any failure.
    Never raises — failures log a warning and return None so callers can
    fire-and-forget from settlement loops.

    Args:
        text: tweet body, capped to 280 chars (any t.co URL Twitter wraps
            still counts as 23 chars regardless of original length).
        image_url: forward-compat hint for WC-F5 OG-image attachment.
            Currently ignored — Twitter's link-card unfurler pulls the OG
            image from any URL in the tweet text automatically.

    Env vars (all four required; missing → return None):
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET
    """
    consumer_key = os.getenv("TWITTER_API_KEY")
    consumer_secret = os.getenv("TWITTER_API_SECRET")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN")
    access_secret = os.getenv("TWITTER_ACCESS_SECRET")

    if not (consumer_key and consumer_secret and access_token and access_secret):
        # Silent no-op — dev/CI will hit this every run, don't spam logs.
        return None

    if not text:
        return None

    # Trim to 280 — Twitter still rejects past the limit even when t.co
    # would shrink long URLs, so we trust the caller has counted correctly.
    body_text = text[:280]

    try:
        auth_header = _build_oauth_header(
            method="POST",
            url=_TWEET_URL,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            token=access_token,
            token_secret=access_secret,
        )
    except Exception as e:
        log.warning("post_tweet: OAuth header build failed: %s", e)
        return None

    try:
        resp = requests.post(
            _TWEET_URL,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
            },
            json={"text": body_text},
            timeout=10,
        )
    except Exception as e:
        log.warning("post_tweet: HTTP failed: %s", e)
        return None

    if resp.status_code in (200, 201):
        try:
            tweet_id = resp.json().get("data", {}).get("id")
            if tweet_id:
                return str(tweet_id)
            log.warning("post_tweet: 200 but no id in body: %s", resp.text[:200])
            return None
        except Exception as e:
            log.warning("post_tweet: JSON parse failed: %s", e)
            return None

    log.warning(
        "post_tweet: HTTP %d: %s",
        resp.status_code,
        resp.text[:300],
    )
    return None
