# ================================================================================
# mcp.py
#
# MCP (Model Context Protocol) server over HTTP.
# Implements the streamable-HTTP transport — a plain POST carrying synchronous
# JSON-RPC 2.0. This is what claude.ai / Claude Desktop speak to a remote MCP
# connector; no local proxy or stdio bridge is involved.
#
# Auth: Bearer token in the Authorization header. The token is a Cognito access
# token issued by the claude.ai OAuth flow (see oauth.py) and validated here via
# Cognito's /oauth2/userInfo endpoint, which returns the user's email.
#
# Tools: every tool drives the same MicroVM controller, so unlike the cost
# connector this was adapted from there is no fan-out to per-tool Lambdas --
# handler.py injects a callable and this module invokes it in process. That
# also avoids a circular import between the two modules.
#
# The tool descriptions matter as much as the code: they are how the model
# decides to poll get_result rather than conclude a slow cell has failed.
# ================================================================================

import json
import logging
import os
import secrets
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MCP_VERSION  = "2025-03-26"
_SERVER_NAME = "microvm-sandbox-mcp"
_SERVER_VER  = "1.0.0"

# ================================================================================
# Tool registry
# ================================================================================
# Descriptions are written for a model, not a person. Two of them carry the
# only facts it cannot infer from the schema: that run_cell returns before the
# work is done, and that a slow cell is normal rather than broken.

TOOL_REGISTRY = [
    {
        "name": "launch_session",
        "description": (
            "Start this user's MicroVM sandbox: a persistent Linux shell. "
            "Idempotent -- returns the existing session if one is already "
            "running. State from an earlier conversation is still there until "
            "the session is terminated."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_cell",
        "description": (
            "Run bash in the user's persistent shell. Returns a job id "
            "IMMEDIATELY, before the command has finished -- always call "
            "get_result afterwards to collect the output. Variables, files, "
            "working directory and installed packages persist between calls "
            "and across suspend and resume."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Bash source to run."}},
            "required": ["code"],
        },
    },
    {
        "name": "get_result",
        "description": (
            "Collect a cell submitted with run_cell. Returns state 'running' "
            "with elapsed seconds, or 'done' with the output. If it is still "
            "running, wait a few seconds and call again -- a package install "
            "can take minutes and that is normal, not a failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job": {"type": "string", "description": "Job id from run_cell."}},
            "required": ["job"],
        },
    },
    {
        "name": "session_status",
        "description": (
            "Report the sandbox's lifecycle state (RUNNING, SUSPENDED or none), "
            "how long it has existed, and how it is configured."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "suspend_session",
        "description": (
            "Suspend the sandbox, stopping compute charges while preserving "
            "all state in a memory snapshot. The next run_cell resumes it "
            "automatically; nothing is lost."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reset_session",
        "description": (
            "Throw this sandbox away and start a clean one. ALL state is lost: "
            "variables, files and installed packages. Use it when the shell is "
            "wedged or the user asks to start over."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "terminate_session",
        "description": (
            "Destroy the sandbox and stop all charges. All state is lost and "
            "no new session is started."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


_TOOL_NAMES = {t["name"] for t in TOOL_REGISTRY}


# ================================================================================
# HTTP + JSON-RPC helpers
# ================================================================================

def _ok(body, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": 200, "headers": headers, "body": json.dumps(body)}


def _accepted():
    # Correct HTTP response for a JSON-RPC notification (no response body).
    return {"statusCode": 202, "headers": {}, "body": ""}


def _http_err(msg, status):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }


def _rpc_error(req_id, code, message, extra_headers=None):
    return _ok(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        extra_headers,
    )


def _rpc_ok(req_id, result, extra_headers=None):
    return _ok({"jsonrpc": "2.0", "id": req_id, "result": result}, extra_headers)


# ================================================================================
# Auth — validate the Cognito access token via userInfo
# ================================================================================

_cognito_userinfo_url = None


def _get_cognito_userinfo_url():
    """Build the Cognito userInfo URL once and cache it in module memory."""
    global _cognito_userinfo_url
    if not _cognito_userinfo_url:
        domain = os.environ.get("COGNITO_DOMAIN", "")
        # AWS_REGION is set by the Lambda runtime. Read directly rather than
        # through a boto3 session: this module no longer needs an AWS client
        # at all, and importing boto3 for one string would be the only reason.
        region = os.environ["AWS_REGION"]
        _cognito_userinfo_url = (
            f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/userInfo"
        )
    return _cognito_userinfo_url


def _resolve_cognito_token(token):
    """Validate a Cognito access token via the userInfo endpoint.

    Calling userInfo is stateless — Cognito verifies the signature and expiry
    server-side, so no crypto library is needed here. Returns the user's email
    on success, or None if the token is invalid or expired.
    """
    req = urllib.request.Request(
        _get_cognito_userinfo_url(),
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - fixed Cognito userInfo endpoint, not user-controlled
            claims = json.loads(resp.read())
        return claims.get("email", "").lower().strip() or None
    except Exception:
        return None


def get_auth_user(event):
    """Extract and resolve the Bearer token from the Authorization header."""
    headers = event.get("headers") or {}
    auth    = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    return _resolve_cognito_token(token)


# ================================================================================
# Tool registry + invocation
# ================================================================================

# ================================================================================
# JSON-RPC method handlers
# ================================================================================

def _handle_initialize(req, session_id):
    # Mcp-Session-Id is required by the 2025-03-26 streamable-HTTP transport.
    return _rpc_ok(
        req.get("id"),
        {
            "protocolVersion": MCP_VERSION,
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": _SERVER_NAME, "version": _SERVER_VER},
        },
        extra_headers={"Mcp-Session-Id": session_id},
    )


def _handle_tools_list(req):
    return _rpc_ok(req.get("id"), {"tools": TOOL_REGISTRY})


def _handle_tools_call(req, user_email, run_tool):
    """Dispatch one tool call to the controller.

    Args:
        req: The JSON-RPC request.
        user_email: The authenticated caller; also the session key.
        run_tool: Callable (tool_name, arguments, user_email) -> dict.

    Note:
        The arguments are forwarded. The project this was adapted from had no
        tool that took any, so nothing there exercised this path.
    """
    params    = req.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments") or {}

    if tool_name not in _TOOL_NAMES:
        return _rpc_error(req.get("id"), -32601, f"Unknown tool: {tool_name}")

    logger.info("MCP tools/call: tool=%s user=%s", tool_name, user_email)

    try:
        result = run_tool(tool_name, arguments, user_email)
    except Exception as exc:
        logger.exception("Tool invocation failed: %s", tool_name)
        return _rpc_error(req.get("id"), -32603, f"Tool invocation failed: {exc}")

    # Returned as pretty JSON text: the model reads these values back to the
    # user, and a job id or an exit code buried in one line is easy to misread.
    text = json.dumps(result, indent=2, default=str)
    return _rpc_ok(req.get("id"), {"content": [{"type": "text", "text": text}]})


# ================================================================================
# Entry point — POST /mcp
# ================================================================================

def handle_mcp(event, run_tool):
    """MCP JSON-RPC endpoint — auth via the OAuth Cognito access token.

    Args:
        event: The API Gateway event.
        run_tool: Callable the controller injects to execute a tool.
    """
    user_id = get_auth_user(event)
    if not user_id:
        return _http_err("Unauthorized — connect via the claude.ai OAuth flow", 401)

    try:
        req = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _http_err("Invalid JSON", 400)

    method = req.get("method", "")
    logger.info("MCP request: method=%s user=%s", method, user_id)

    # Session ID is stateless — generated fresh on initialize, echoed back on
    # later requests via the Mcp-Session-Id header (accepted but not validated).
    session_id = (event.get("headers") or {}).get("mcp-session-id") or secrets.token_hex(16)

    if method == "initialize":
        return _handle_initialize(req, session_id)
    if method in ("notifications/initialized", "notifications/cancelled"):
        return _accepted()
    if method == "tools/list":
        return _handle_tools_list(req)
    if method == "tools/call":
        return _handle_tools_call(req, user_id, run_tool)

    return _rpc_error(req.get("id"), -32601, f"Method not found: {method}")
