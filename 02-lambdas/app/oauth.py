# ================================================================================
# oauth.py
#
# OAuth 2.0 authorization-server proxy for the MCP connector.
#
# Why a proxy? claude.ai uses a dynamic redirect_uri that includes the org ID
# (e.g. https://claude.ai/api/organizations/<id>/mcp/callback). Cognito requires
# an exact allow-list match, so we cannot register claude.ai's URL directly.
# Instead we advertise *ourselves* as the authorization server and proxy through
# our own /authorize and /oauth/callback, registering only our fixed
# /oauth/callback URL with the Cognito MCP client.
#
# Flow:
#   1. GET  /authorize      — validate request, store state, redirect to Cognito
#   2. GET  /oauth/callback — Cognito posts back here; exchange code for a Cognito
#                             access token, store it behind a one-time mac_ code,
#                             redirect back to claude.ai
#   3. POST /oauth/token    — claude.ai exchanges the mac_ code → Cognito access
#                             token
#   4. POST /mcp            — Bearer is a real Cognito access token; validated via
#                             Cognito's /oauth2/userInfo endpoint in mcp.py
#
# The Cognito access token is passed through to claude.ai as-is — no custom token
# storage or crypto. Cognito handles all of that.
#
# DynamoDB records (5-min TTL, table = TABLE_NAME):
#   pk=PENDINGAUTH#<sess>  sk=PENDINGAUTH   — redirect_uri, state
#   pk=AUTHCODE#<code>     sk=AUTHCODE      — cognito_access_token, one-time use
# ================================================================================

import base64
import json
import logging
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, quote

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# NOT the session table: this one holds only in-flight OAuth records.
_table  = boto3.resource("dynamodb").Table(os.environ["OAUTH_TABLE_NAME"])
_region = boto3.session.Session().region_name

PENDING_TTL = 300   # 5 minutes for both PENDINGAUTH and AUTHCODE records


# ================================================================================
# Internal helpers
# ================================================================================

def _ok(body, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _err(msg, status=400):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }


def _redirect(location):
    return {"statusCode": 302, "headers": {"Location": location}, "body": ""}


def _api_base(event):
    """Public base URL of this API — read from the incoming request host."""
    domain = event.get("requestContext", {}).get("domainName", "")
    return f"https://{domain}" if domain else os.environ.get("APP_URL", "")


def _cognito_base():
    domain = os.environ.get("COGNITO_DOMAIN", "")
    return f"https://{domain}.auth.{_region}.amazoncognito.com"


def _mcp_client_id():
    return os.environ.get("MCP_CLIENT_ID", "")


def _mcp_client_secret():
    return os.environ.get("MCP_CLIENT_SECRET", "")


def _decode_jwt_payload(token):
    """Base64url-decode a JWT payload without verifying the signature.

    Safe only for a token obtained directly from Cognito's token endpoint in
    the same request — used here purely to log the user's email.
    """
    payload = token.split(".")[1] if token else ""
    padding = 4 - len(payload) % 4
    return json.loads(base64.b64decode(payload + "=" * padding))


def _parse_body(event):
    """Parse a request body as form-encoded (default) or JSON."""
    body_raw = event.get("body") or ""
    # API Gateway may base64-encode the body when it can't sniff the type.
    if event.get("isBase64Encoded") and body_raw:
        body_raw = base64.b64decode(body_raw).decode("utf-8", errors="replace")
    content_type = (event.get("headers") or {}).get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        return {k: v[0] for k, v in parse_qs(body_raw).items()}
    # OAuth token requests are almost always form-encoded even when the
    # Content-Type header is absent or wrong — try form-decode first.
    if body_raw and "=" in body_raw and not body_raw.lstrip().startswith("{"):
        return {k: v[0] for k, v in parse_qs(body_raw).items()}
    try:
        return json.loads(body_raw) if body_raw else {}
    except json.JSONDecodeError:
        return {}


def _cognito_token_exchange(code, callback_url):
    """Exchange a Cognito authorization code for tokens.

    Uses HTTP Basic auth with the confidential MCP client secret, as required
    for a Cognito client created with generate_secret = true.

    Args:
        code: Authorization code received from Cognito.
        callback_url: The redirect_uri used in the original /authorize request.

    Returns:
        Parsed token response dict, or None on failure.
    """
    import urllib.request

    client_id     = _mcp_client_id()
    client_secret = _mcp_client_secret()
    credentials   = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()

    body = urlencode({
        "grant_type":   "authorization_code",
        "client_id":    client_id,
        "code":         code,
        "redirect_uri": callback_url,
    }).encode()

    req = urllib.request.Request(
        f"{_cognito_base()}/oauth2/token",
        data=body,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - fixed Cognito token endpoint, not user-controlled
            return json.loads(resp.read())
    except Exception:
        logger.exception("Cognito token exchange failed")
        return None


# ================================================================================
# Discovery endpoint (public) — GET /.well-known/oauth-authorization-server
# ================================================================================

def oauth_metadata(event):
    """Return OAuth 2.0 authorization-server metadata per RFC 8414.

    Points at our proxy endpoints, not Cognito directly, so claude.ai uses the
    correct URLs for the authorization-code flow. Advertising
    registration_endpoint tells claude.ai it can self-register instead of
    requiring pre-provisioned credentials.
    """
    base = _api_base(event)
    return _ok({
        "issuer":                                base,
        "authorization_endpoint":                f"{base}/authorize",
        "token_endpoint":                        f"{base}/oauth/token",
        "registration_endpoint":                 f"{base}/oauth/register",
        "grant_types_supported":                 ["authorization_code"],
        "response_types_supported":              ["code"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    })


# ================================================================================
# Dynamic client registration (RFC 7591) — POST /oauth/register
# ================================================================================

def oauth_register(event):
    """Handle RFC 7591 dynamic client registration.

    claude.ai calls this when no pre-configured credentials exist. We always
    return our shared MCP client_id with auth method "none" — the Cognito
    confidential client secret is used server-side only and never exposed to
    the registering client.
    """
    base = _api_base(event)
    return {
        "statusCode": 201,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "client_id":                  _mcp_client_id(),
            "token_endpoint_auth_method": "none",
            "grant_types":                ["authorization_code"],
            "response_types":             ["code"],
            "redirect_uris":              [f"{base}/oauth/callback"],
        }),
    }


# ================================================================================
# Authorization code flow — step 1: GET /authorize
# ================================================================================

def oauth_authorize(event):
    """Proxy the authorization request to Cognito.

    Stores the original claude.ai redirect_uri and state in DynamoDB, then
    redirects the user's browser to Cognito's Hosted UI. Our fixed callback URL
    is what Cognito sees; the claude.ai session rides along in `state`.
    """
    qs            = event.get("queryStringParameters") or {}
    redirect_uri  = qs.get("redirect_uri", "").strip()
    state         = qs.get("state", "")
    response_type = qs.get("response_type", "")

    if response_type != "code":
        return _err("unsupported_response_type", 400)
    if not redirect_uri:
        return _err("invalid_request", 400)

    session_id = secrets.token_urlsafe(16)
    expires_at = int(time.time()) + PENDING_TTL
    _table.put_item(Item={
        "pk":           f"PENDINGAUTH#{session_id}",
        "sk":           "PENDINGAUTH",
        "redirect_uri": redirect_uri,
        "state":        state,
        "expires_at":   expires_at,
        "ttl":          expires_at,
    })

    callback_url = f"{_api_base(event)}/oauth/callback"
    cognito_auth = (
        f"{_cognito_base()}/oauth2/authorize"
        f"?client_id={_mcp_client_id()}"
        f"&response_type=code"
        f"&scope=openid+email+profile"
        f"&redirect_uri={quote(callback_url, safe='')}"
        f"&state={session_id}"
    )

    logger.info("OAuth authorize: session=%s", session_id)
    return _redirect(cognito_auth)


# ================================================================================
# Authorization code flow — step 2: GET /oauth/callback
# ================================================================================

def oauth_callback(event):
    """Exchange the Cognito code, store the access token, redirect to claude.ai."""
    qs           = event.get("queryStringParameters") or {}
    cognito_code = qs.get("code", "").strip()
    session_id   = qs.get("state", "").strip()

    if not cognito_code or not session_id:
        return _err("invalid_request", 400)

    pending = _table.get_item(
        Key={"pk": f"PENDINGAUTH#{session_id}", "sk": "PENDINGAUTH"}
    ).get("Item")

    if not pending or int(time.time()) > int(pending.get("expires_at", 0)):
        return _err("invalid_state", 400)

    callback_url   = f"{_api_base(event)}/oauth/callback"
    cognito_tokens = _cognito_token_exchange(cognito_code, callback_url)
    if not cognito_tokens or "access_token" not in cognito_tokens:
        logger.error("Cognito exchange returned no access_token: %s", cognito_tokens)
        return _err("cognito_exchange_failed", 502)

    # Decode the ID token only to log the user — token came straight from Cognito.
    try:
        claims     = _decode_jwt_payload(cognito_tokens.get("id_token", ""))
        user_email = claims.get("email", "unknown")
    except Exception:
        user_email = "unknown"

    # Issue a one-time mac_ code; stash the real Cognito access token behind it.
    auth_code  = "mac_" + secrets.token_urlsafe(32)
    expires_at = int(time.time()) + PENDING_TTL
    _table.put_item(Item={
        "pk":                   f"AUTHCODE#{auth_code}",
        "sk":                   "AUTHCODE",
        "cognito_access_token": cognito_tokens["access_token"],
        "redirect_uri":         pending["redirect_uri"],
        "expires_at":           expires_at,
        "ttl":                  expires_at,
    })

    _table.delete_item(Key={"pk": f"PENDINGAUTH#{session_id}", "sk": "PENDINGAUTH"})

    original_state = pending.get("state", "")
    dest           = pending["redirect_uri"]
    sep            = "&" if "?" in dest else "?"
    location       = f"{dest}{sep}code={auth_code}&state={quote(original_state, safe='')}"

    logger.info("OAuth callback: user=%s", user_email)
    return _redirect(location)


# ================================================================================
# Token endpoint (public) — POST /oauth/token
# ================================================================================

def oauth_token(event):
    """Exchange a mac_ auth code for the stored Cognito access token."""
    params     = _parse_body(event)
    grant_type = params.get("grant_type", "")

    if grant_type != "authorization_code":
        return _err("unsupported_grant_type", 400)

    # Security lives in the one-time AUTHCODE record — no client credential check
    # needed. Clients registered via /oauth/register use auth method "none".
    code = params.get("code", "").strip()
    if not code:
        return _err("invalid_request", 400)

    code_item = _table.get_item(
        Key={"pk": f"AUTHCODE#{code}", "sk": "AUTHCODE"}
    ).get("Item")

    if not code_item or int(time.time()) > int(code_item.get("expires_at", 0)):
        return _err("invalid_grant", 400)

    # One-time use — delete before returning.
    _table.delete_item(Key={"pk": f"AUTHCODE#{code}", "sk": "AUTHCODE"})

    access_token = code_item["cognito_access_token"]

    # Report 24h — matches the Cognito MCP client access_token_validity.
    # Underreporting (e.g. 3600) makes the client attempt a refresh after 1 hour;
    # we don't implement refresh_token grant, so that would silently break the
    # session.
    return _ok({
        "access_token": access_token,
        "token_type":   "Bearer",
        "expires_in":   86400,
    })
