import os
import base64
import hashlib
import secrets
import urllib.parse
from dotenv import load_dotenv

from flask import Flask, redirect, request, session, jsonify

import requests

load_dotenv(".env.local")

# ---------------------------
# Config / env
# ---------------------------
fallback_client_id = ""
fallback_client_secret = ""

CLIENT_ID = os.getenv("CLIENT_ID") or fallback_client_id
CLIENT_SECRET = os.getenv("CLIENT_SECRET") or fallback_client_secret
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/auth/callback")

# Keep scopes tight; expand as needed
# e.g. "accounts:read profiles:read profiles:write events:write lists:read subscriptions:write"
KLAVIYO_SCOPES = os.getenv("KLAVIYO_SCOPES", "accounts:read profiles:read")

# For local dev only. In prod, set a strong, secret value.
# NOTE: This secures Flask's session cookie. In production:
# - use a long, random value
# - set SESSION_COOKIE_SECURE=True (HTTPS)
# - consider server-side session storage
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-not-secret")

# Klaviyo OAuth endpoints
AUTH_URL = "https://klaviyo.com/oauth/authorize"
TOKEN_URL = "https://a.klaviyo.com/oauth/token"

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY


# ---------------------------
# PKCE helpers
# ---------------------------
def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def generate_code_verifier() -> str:
    """
    RFC 7636: 43..128 chars from chars [A-Z/a-z/0-9/-/_/.~/]
    Using token_urlsafe yields base64url; trim to <=128 if needed.
    """
    v = secrets.token_urlsafe(96)  # ~128 chars
    return v[:128]


def generate_code_challenge(verifier: str) -> str:
    # PKCE: the server sends only the *challenge* to the provider,
    # and proves possession later with the *verifier* at the token step.
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_no_pad(digest)


# ---------------------------
# Auth flow
# ---------------------------
@app.route("/auth/start", methods=["GET"])
def auth_start():
    """
    1) Generate state + PKCE challenge
    2) Redirect user to Klaviyo's consent screen
    WHY we store things:
    - `state`: CSRF protection. Must match on callback. Store server-side keyed to the user/session.
    - `code_verifier`: PKCE secret used *only* at token exchange time. Never expose in URLs.
      Store server-side (e.g., DB/Redis) mapped to `state` so you can retrieve it in the callback.
    LOCAL DEV:
    - We keep both in Flask session for simplicity.
    PROD:
    - Store { state -> code_verifier, any user context } in a DB/Redis with TTL.
    - You’ll also want to persist the resulting tokens after the token exchange.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        return jsonify(error="CLIENT_ID / CLIENT_SECRET missing"), 500

    state = secrets.token_urlsafe(24)  # CSRF protection token
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # In local dev we can store verifier/state in the Flask session (cookie).
    # In PROD: store {state -> code_verifier} in server-side storage (Redis/Dynamo/DB) with an expiration.
    # Rationale: relying on browser cookies for security artifacts can be brittle; server-side storage
    # avoids issues with hostname mismatch, cookie policies, and allows multi-server deployments.
    session["oauth_state"] = state
    session["code_verifier"] = code_verifier

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,  # MUST exactly match the app's registered redirect URI
        "scope": KLAVIYO_SCOPES,       # Request only what you need; broader scopes = more risk
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(url, code=302)


@app.route("/auth/callback", methods=["GET"])
def auth_callback():
    """
    3) Klaviyo returns ?code=&state=. Verify state, exchange code for tokens.
    NOTE: Your OAuth app must whitelist the exact `REDIRECT_URI`.
    SECURITY NOTES:
    - Always verify `state` equals what you generated in /auth/start (prevents CSRF).
    - Retrieve the `code_verifier` you stored for this `state`.
    - Send `code_verifier` when exchanging the code (PKCE).
    - On success, PERSIST tokens (access + refresh) in your DB keyed to your user/account.
      Do not return tokens to the browser in production.
    """
    err = request.args.get("error")
    if err:
        return jsonify(error=err, error_description=request.args.get("error_description")), 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return jsonify(error="Missing code/state"), 400

    # In PROD: look up `expected_state` and `code_verifier` from your DB using `state` as the key.
    expected_state = session.pop("oauth_state", None)
    if not expected_state or state != expected_state:
        return jsonify(error="Invalid state"), 400

    code_verifier = session.pop("code_verifier", None)
    if not code_verifier:
        # In PROD, fetch the code_verifier from your server-side store with the incoming state.
        return jsonify(error="Missing code_verifier (session)"), 400

    # Basic auth header: base64(client_id:client_secret)
    # This proves your backend (confidential client) owns the client secret.
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,  # must match the value sent in /auth/start
        "code_verifier": code_verifier,  # PKCE proof of possession
    }

    resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if resp.status_code != 200:
        return jsonify(error="Token exchange failed", status=resp.status_code, body=resp.text), 400

    tokens = resp.json()
    # tokens includes: access_token, refresh_token, token_type, expires_in, etc.

    # PRODUCTION PERSISTENCE:
    # - Save tokens in your database associated with your user/account (e.g., by your own user_id).
    # - Store issued_at and expires_at (now + expires_in) for proactive refresh.
    # - Encrypt at rest. Never log tokens. Avoid sending them back to the browser.
    # DEV ONLY: returning them so you can see the flow end-to-end.
    return jsonify(tokens)


@app.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    """
    4) Exchange a refresh_token for a new access_token.
       POST JSON: {"refresh_token":"..."}
    TOKEN LIFECYCLE (PROD):
    - Read the user's stored refresh_token from your DB when access_token is expired/near expiry.
    - Call this endpoint server-to-server to rotate tokens.
    - Update your DB with the new access_token (and possibly new refresh_token).
    - Handle token revocation/invalid_grant gracefully (e.g., prompt re-auth).
    """
    body = request.get_json(silent=True) or {}
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        return jsonify(error="Missing refresh_token"), 400

    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if resp.status_code != 200:
        return jsonify(error="Refresh failed", status=resp.status_code, body=resp.text), 400

    # In PROD: persist newly returned tokens (some providers rotate refresh tokens).
    return jsonify(resp.json())


@app.route("/whoami", methods=["GET"])
def whoami():
    """
    Example authenticated call (you’ll replace with real Klaviyo API calls).
    HOW TO CALL:
    - Pass the access token as: Authorization: Bearer <token>
    - Include the `revision` header for the API version you build against.
    PROD USAGE:
    - Do NOT ask clients to pass tokens manually.
    - Look up the user's stored access_token from your DB and attach it server-side.
    - If expired, refresh first (see /auth/refresh), then retry the API call.
    """
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return jsonify(error="Provide Authorization: Bearer <access_token>"), 400

    # Example request shape; replace with the real Klaviyo endpoint(s) you need.
    headers = {"Authorization": f"Bearer {token}", "revision": "2025-07-15"}
    r = requests.get("https://a.klaviyo.com/api/accounts/", headers=headers, timeout=30)
    return r.json() if r.headers.get("content-type","").startswith("application/json") else r.text
    # return jsonify(message="You'd call Klaviyo here with the bearer token.", token_starts_with=token[:12])


if __name__ == "__main__":
    # For local dev, ensure your REDIRECT_URI uses the same host (localhost vs 127.0.0.1) you bind here
    # and is exactly whitelisted in the Klaviyo app settings.
    app.run(host="localhost", port=5000, debug=True)
