"""HTTP API that drives the MicroVM lifecycle for every runtime.

Lifecycle actions are synchronous: launch and resume run from a pre-initialized
Firecracker snapshot in a few seconds, so there is nothing worth queueing.

Cells are not. "execute" submits a cell and gets a job id back; "result" polls
for it. The work runs inside the MicroVM, which already holds the session's
state and can just as easily hold its answer -- so no request here is ever
long, and a cell may run far past any HTTP timeout in the chain. That is what
lets this sit behind an ordinary API Gateway, whose 30-second cap would
otherwise be the limit on how long a cell could run.

One image, a persistent bash shell. The runtime map is still what drives this
module, so a second image needs no change here.

DynamoDB holds only identifiers and the last application sample. Interpreter
state lives exclusively inside each MicroVM and is never serialized out.
"""
import json
import os
import re
import time
import traceback
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

import mcp
from mcp import TOOL_REGISTRY  # noqa: F401  (kept for a single source of truth)
from oauth import (
    oauth_authorize,
    oauth_callback,
    oauth_metadata,
    oauth_register,
    oauth_token,
)
from presets import PRESETS

ACTIONS = {"launch", "suspend", "wake", "execute", "result", "reset",
           "terminate"}

# MCP tool name -> controller action. "config" and "status" are read-only and
# have no lifecycle action of their own.
TOOL_ACTIONS = {
    "launch_session": "launch",
    "run_cell": "execute",
    "get_result": "result",
    "session_status": "status",
    "suspend_session": "suspend",
    "reset_session": "reset",
    "terminate_session": "terminate",
}

REGION = os.environ["AWS_REGION"]
IMAGES = json.loads(os.environ["IMAGES"])          # {"python": {...}, "node": {...}, ...}
RUNTIMES = tuple(sorted(IMAGES))
BASE_IMAGE_ARN = os.environ["BASE_IMAGE_ARN"]
BASE_IMAGE_VERSION = os.environ["BASE_IMAGE_VERSION"]

# Passed to RunMicrovm, which is the MicroVM equivalent of an EC2 instance
# profile: the guest authenticates as this role with no key material inside it.
# Scoped to reading the web bucket only, because submitted code runs through
# eval -- whatever this role can do, anything typed into the editor can do.
MICROVM_ROLE_ARN = os.environ["MICROVM_ROLE_ARN"]

# Substituted into the AWS CLI preset so the cell can name a real bucket
# without the browser having to assemble the command.
WEB_BUCKET = os.environ["WEB_BUCKET"]

# Launch settings applied to every MicroVM. Kept in one place because the
# configuration panel reports them back verbatim -- what the browser displays
# is what was actually sent to RunMicrovm, not a hand-written copy.
BASELINE_MIB = 512

# Auto-suspend is a cost guard, not the demonstration -- the UI has a Suspend
# button, so nothing here needs to fire for the demo to work. It is set well
# above the suspend/resume breakeven (~10 minutes per GB of snapshot: a
# $0.0038/GB write plus a $0.00155/GB read against $0.0315/hour of compute),
# because suspending a VM that comes back a minute later costs more than
# leaving it running.
IDLE_SUSPEND_SECONDS = 1800

# Must stay below MAX_LIFETIME_SECONDS or idle suspend can never fire -- the VM
# would be terminated at the same instant it first became eligible.
SUSPENDED_TTL_SECONDS = 1800

# The service maximum, 8 hours. Set to the ceiling deliberately: a sandbox that
# an agent installs tools into over the course of a working session should not
# expire mid-conversation, and nothing is gained by picking a smaller arbitrary
# number. Cost is bounded by auto-suspend rather than by this -- an idle VM
# stops costing compute after IDLE_SUSPEND_SECONDS and is terminated
# SUSPENDED_TTL_SECONDS later, so the full 8 hours is only ever reached by a
# session someone is actually using.
MAX_LIFETIME_SECONDS = 28800
APP_PORT = 8080
HOOK_PORT = 8081

# Endpoint auth tokens cached per warm container. Module level so a warm
# container avoids a control-plane call per request; never persisted or
# returned to the browser.
TOKENS = {}


# ==============================================================================
# AWS Clients and Session Storage
# ==============================================================================

def microvms():
    """Return a lambda-microvms client tuned for short, synchronous calls."""
    return boto3.client("lambda-microvms", region_name=REGION, config=Config(
        connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 3}))


def table():
    """Return the DynamoDB Table resource holding session records."""
    return boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])


def key(user, runtime):
    """Build the session row id.

    Keyed on the authenticated user, which is what gives each person their own
    MicroVM -- and what makes the browser and the MCP connector land on the
    SAME sandbox, since both authenticate as the same Cognito identity.
    """
    return f"{user}#{runtime}"


def load(user, runtime):
    """Read this user's session record, or None if they have no live session.

    The image check matters because reapplying with changed source produces a
    new image, and a stale MicroVM id from the previous one is not live.
    """
    item = table().get_item(Key={"id": key(user, runtime)},
                            ConsistentRead=True).get("Item")
    if not item or item.get("image") != IMAGES[runtime]["image_arn"]:
        return None
    return json.loads(item["data"])


def save(user, runtime, session):
    """Persist this user's session record.

    One row per user and runtime: a shared row would let concurrent writes
    clobber each other, and a lost MicroVM id orphans a VM that bills until it
    expires.
    """
    table().put_item(Item={"id": key(user, runtime),
                           "image": IMAGES[runtime]["image_arn"],
                           "data": json.dumps(session)})


def forget(user, runtime):
    """Drop a session record after its MicroVM is terminated."""
    table().delete_item(Key={"id": key(user, runtime)})


def pages(client, method, **params):
    """Yield every item from a paginated lambda-microvms list call."""
    while True:
        result = getattr(client, method)(**params)
        yield from result.get("items", [])
        token = result.get("nextToken")
        if not token:
            return
        params["nextToken"] = token


# ==============================================================================
# Configuration Reporting — what the EC2 comparison panel renders
# ==============================================================================

def presets_for(runtime):
    """Return a runtime's presets with deployment-specific values filled in.

    Substituted here rather than in presets.py so that file stays a plain
    catalogue of shell snippets with no import of deployment state.
    """
    return {k: v.replace("__WEB_BUCKET__", WEB_BUCKET).replace("__REGION__", REGION)
            for k, v in PRESETS[runtime].items()}


def spec(runtime):
    """Describe how this runtime's MicroVMs are configured.

    Every value here is either what was passed to RunMicrovm or what the image
    was built with, so the panel shows the real configuration rather than a
    description of it. The EC2 equivalents are supplied by the frontend, which
    is where display copy belongs.
    """
    image = IMAGES[runtime]
    return {
        "runtime": runtime,
        "image_name": image["image_name"],
        "image_arn": image["image_arn"],
        "image_version": image["image_version"],
        "base_image_arn": BASE_IMAGE_ARN,
        "base_image_version": BASE_IMAGE_VERSION,
        "architecture": "ARM_64",
        "baseline_mib": BASELINE_MIB,
        "baseline_vcpu": BASELINE_MIB / 2048,          # 2 GB == 1 vCPU
        "burst_mib": BASELINE_MIB * 4,                 # vertical scale ceiling
        "disk_gb": 8,
        "execution_role": MICROVM_ROLE_ARN,            # read-only on the web bucket
        "ingress_connector": "ALL_INGRESS",
        "egress_connector": "INTERNET_EGRESS",
        "shell_enabled": False,                        # no SHELL_INGRESS attached
        "app_port": APP_PORT,
        "hook_port": HOOK_PORT,
        "auto_resume": True,
        "idle_suspend_seconds": IDLE_SUSPEND_SECONDS,
        "suspended_ttl_seconds": SUSPENDED_TTL_SECONDS,
        "max_lifetime_seconds": MAX_LIFETIME_SECONDS,
        "run_hook_payload": {"runtime": runtime},
    }


# ==============================================================================
# MicroVM Lifecycle
# ==============================================================================

def state_of(client, vm_id):
    """Return a MicroVM's lifecycle state, treating a missing VM as TERMINATED."""
    try:
        return client.get_microvm(microvmIdentifier=vm_id)["state"]
    except client.exceptions.ResourceNotFoundException:
        return "TERMINATED"


def wait(client, vm_id, desired, timeout=20):
    """Poll until a MicroVM reaches `desired`.

    Raises:
        RuntimeError: it reached a terminal or failed state instead.
        TimeoutError: it did not get there in time.
    """
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        state = state_of(client, vm_id)
        if state == desired:
            return state
        if state == "TERMINATED" or "FAILED" in state:
            raise RuntimeError(f"MicroVM reached {state}, expected {desired}")
        time.sleep(0.25)
    raise TimeoutError(f"MicroVM did not reach {desired} within {timeout}s")


def token(client, vm_id):
    """Mint, and cache, an endpoint token scoped to the application port only.

    Port-scoped so a leaked token still cannot reach the lifecycle hook
    listener. Cached until five minutes before expiry to avoid handing out one
    that dies mid-request.
    """
    cached = TOKENS.get(vm_id)
    if not cached or cached[1] < time.time():
        result = client.create_microvm_auth_token(
            microvmIdentifier=vm_id, expirationInMinutes=30,
            allowedPorts=[{"port": APP_PORT}])
        cached = (result["authToken"]["X-aws-proxy-auth"], time.time() + 25 * 60)
        TOKENS[vm_id] = cached
    return cached[0]


def call(client, session, path, body=None):
    """Send an authenticated HTTPS request to a MicroVM's own endpoint."""
    endpoint = session["endpoint"]
    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint
    headers = {"X-aws-proxy-auth": token(client, session["id"]),
               "X-aws-proxy-port": str(APP_PORT)}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint + path, data=None if body is None else json.dumps(body).encode(),
        headers=headers)
    start = time.perf_counter()
    # Every call here is short now: submitting a cell returns a job id, and
    # polling returns a status. Nothing waits for the work itself, so this no
    # longer has to be sized against the Lambda's ceiling. It must still be the
    # SMALLEST of the outer timeouts, or the caller sees a Lambda timeout
    # instead of a JSON error it can render. An auto-resume can take a few
    # seconds, which is what this has to cover.
    with urllib.request.urlopen(request, timeout=25) as response:
        value = json.load(response)
    value["round_trip_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return value


def status(client, runtime, session):
    """Report a session's AWS state alongside its last application sample.

    Control-plane only. Sampling the endpoint here would count as traffic,
    auto-resume a suspended VM, and destroy the suspension being displayed.
    """
    return dict(session, runtime=runtime, state=state_of(client, session["id"]),
                observation="Cached application data; AWS lifecycle state is current")


def launch(client, user, runtime):
    """Run a new MicroVM for this user and record its first observation."""
    existing = load(user, runtime)
    if existing and state_of(client, existing["id"]) != "TERMINATED":
        raise ValueError("Terminate the existing session first")

    image = IMAGES[runtime]
    start = time.perf_counter()
    response = client.run_microvm(
        imageIdentifier=image["image_arn"],
        imageVersion=image["image_version"],
        clientToken=str(uuid.uuid4()),
        # Reaches the /run hook, which generates this session's nonce. Identity
        # must be created after the snapshot, never baked into it.
        runHookPayload=json.dumps({"runtime": runtime}),
        executionRoleArn=MICROVM_ROLE_ARN,
        ingressNetworkConnectors=[
            f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS"],
        idlePolicy={"autoResumeEnabled": True,
                    "maxIdleDurationSeconds": IDLE_SUSPEND_SECONDS,
                    "suspendedDurationSeconds": SUSPENDED_TTL_SECONDS},
        maximumDurationInSeconds=MAX_LIFETIME_SECONDS,
        logging={"disabled": {}})  # Guest logs would carry submitted code.

    session = {"id": response["microvmId"], "endpoint": response["endpoint"]}
    save(user, runtime, session)  # Persist BEFORE the first call, so cleanup finds it.
    wait(client, session["id"], "RUNNING")
    session["snapshot"] = call(client, session, "/state")
    session["launch_to_first_response_ms"] = round((time.perf_counter() - start) * 1000, 1)
    save(user, runtime, session)
    return status(client, runtime, session)


def act(client, user, runtime, action, code, job=None):
    """Dispatch one lifecycle action against a runtime's session.

    Args:
        client: A lambda-microvms client.
        runtime: Which runtime's session to act on.
        action: One of ACTIONS.
        code: Cell source, for "execute" only.
        job: Job id, for "result" only.
    """
    if action == "launch":
        return launch(client, user, runtime)

    session = load(user, runtime)
    if not session:
        if action == "reset":
            return launch(client, user, runtime)     # nothing to discard
        raise ValueError("Launch this runtime first")

    if action == "result":
        # The hot path: called once a second while a cell runs, so it does the
        # least possible work -- no state sampling, no DynamoDB write.
        return {"runtime": runtime, "result": call(client, session,
                                                   f"/result/{job}")}

    if action == "suspend":
        # Sample before suspending. Once suspended, nothing may touch the
        # endpoint, because any request would silently resume it.
        session["before_suspend"] = call(client, session, "/state")
        client.suspend_microvm(microvmIdentifier=session["id"])
        wait(client, session["id"], "SUSPENDED")
    elif action in ("terminate", "reset"):
        if state_of(client, session["id"]) != "TERMINATED":
            client.terminate_microvm(microvmIdentifier=session["id"])
            # Blocking here is the entire reason reset is one action rather
            # than advice to call terminate and then launch: launch refuses
            # while the old MicroVM is still winding down, and termination is
            # not instant.
            wait(client, session["id"], "TERMINATED")
        forget(user, runtime)
        if action == "reset":
            return launch(client, user, runtime)
        return {"runtime": runtime, "id": session["id"], "state": "TERMINATED"}
    else:
        # Both wake and execute send real traffic, which is what auto-resumes a
        # suspended VM. Capturing the state first is what lets the panel show
        # that the request itself did the waking.
        before = state_of(client, session["id"])
        # Returns a job id in milliseconds; the work continues in the VM.
        result = call(client, session, "/execute", {"code": code}) if action == "execute" else None
        session["snapshot"] = call(client, session, "/state")
        save(user, runtime, session)
        return dict(status(client, runtime, session), result=result,
                    state_before_request=before)

    save(user, runtime, session)
    return status(client, runtime, session)


# ==============================================================================
# HTTP API
# ==============================================================================

def response(code, data):
    """Build a Lambda proxy response.

    Uses the API Gateway HTTP API proxy response format.
    """
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(data)}


def caller(event):
    """Resolve the Cognito access token on a request to this user's email.

    The same check serves the SPA and the MCP connector, which is the point:
    one identity, so signing in with a browser and connecting with Claude
    reach the same sandbox. Returns None when the token is missing or invalid.
    """
    return mcp.get_auth_user(event)


def run_tool(tool_name, arguments, user):
    """Execute one MCP tool. Injected into mcp.handle_mcp.

    The tool surface is deliberately the controller's own action set, so the
    browser and Claude drive identical code -- there is no second
    implementation to drift.
    """
    client = microvms()
    runtime = RUNTIMES[0]
    action = TOOL_ACTIONS[tool_name]

    if action == "config":
        return {"presets": presets_for(runtime), "spec": spec(runtime)}
    if action == "status":
        session = load(user, runtime)
        if not session:
            return {"state": "NONE", "note": "No sandbox. Call launch_session."}
        return status(client, runtime, session)

    return act(client, user, runtime, action,
               arguments.get("code", ""), arguments.get("job", ""))


def api(event, context):
    """Handle one API Gateway HTTP API request.

    Three families of route, and only the first is authenticated here:
      /api/*     the SPA, carrying a Cognito access token
      /mcp       the connector, carrying the same kind of token (checked in
                 mcp.handle_mcp, which needs the email rather than a boolean)
      /oauth/*   the OAuth proxy, which IS the authentication and so cannot
                 require it
    """
    http = (event.get("requestContext") or {}).get("http") or {}
    route = f'{http.get("method", "")} {event.get("rawPath", "")}'.strip()

    if route == "GET /.well-known/oauth-authorization-server":
        return oauth_metadata(event)
    if route == "POST /oauth/register":
        return oauth_register(event)
    if route == "GET /authorize":
        return oauth_authorize(event)
    if route == "GET /oauth/callback":
        return oauth_callback(event)
    if route == "POST /oauth/token":
        return oauth_token(event)
    if route == "POST /mcp":
        return mcp.handle_mcp(event, run_tool)

    user = caller(event)
    if not user:
        return response(401, {"error": "Sign in first"})
    try:
        client = microvms()

        if route == "GET /api/config":
            return response(200, {
                "runtimes": RUNTIMES,
                "presets": {r: presets_for(r) for r in RUNTIMES},
                "specs": {r: spec(r) for r in RUNTIMES},
            })

        if route == "GET /api/status":
            result = {}
            for runtime in RUNTIMES:
                session = load(user, runtime)
                if session:
                    result[runtime] = status(client, runtime, session)
            return response(200, result)

        if route == "POST /api/action":
            body = event.get("body", "")
            if event.get("isBase64Encoded") or len(body.encode()) > 20000:
                raise ValueError("Invalid request body")
            payload = json.loads(body)
            runtime, action = payload.get("runtime"), payload.get("action")
            if runtime not in RUNTIMES or action not in ACTIONS:
                raise ValueError("Invalid runtime or action")
            code = payload.get("code", "")
            if not isinstance(code, str) or len(code.encode()) > 16000:
                raise ValueError("Code must be a string of at most 16000 bytes")
            job = payload.get("job", "")
            # Interpolated into the VM's URL path, so it is constrained here
            # rather than trusted: hex only, and the length the VM issues.
            if action == "result" and not re.fullmatch(r"[0-9a-f]{1,32}", job):
                raise ValueError("Invalid job id")
            return response(200, act(client, user, runtime, action, code, job))

        return response(404, {"error": "Not found"})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {"error": str(exc)})
    except (RuntimeError, TimeoutError) as exc:
        return response(409, {"error": str(exc)})
    except Exception:
        # The browser gets a generic message -- an SDK error carries headers,
        # endpoint tokens and stack frames. CloudWatch gets the real one,
        # because without it an AccessDenied from RunMicrovm looks exactly
        # like a service outage, with nowhere to look up which it was.
        traceback.print_exc()
        return response(503, {"error": "AWS controller request failed. Check service availability."})
