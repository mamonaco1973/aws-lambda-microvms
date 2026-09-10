"""Cognito-protected HTTP API that drives the MicroVM lifecycle synchronously.

MicroVM launch and resume run from a pre-initialized Firecracker snapshot in a
few seconds, so every action completes well inside the API Gateway integration
timeout. That is why there is no queue, no worker and no job table here: the
asynchronous machinery those would buy is latency insurance this service does
not need.

DynamoDB holds only identifiers and the last application sample. Interpreter
state -- variables, generator cursors, open files, background threads -- lives
exclusively inside each MicroVM and is never serialized out. Restoring a session
means resuming the VM, not replaying its history.
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

from presets import PRESETS

# Lifecycle verbs the browser is allowed to submit. Anything else is a 400.
ACTIONS = {"launch", "suspend", "wake", "sample", "execute", "auth-check", "terminate"}

# Fixed two-slot lab. Named tenants keep the demo cheap and the UI legible.
TENANTS = ("alice", "bob")

REGION = os.environ["AWS_REGION"]
IMAGE_ARN = os.environ["IMAGE_ARN"]
IMAGE_VERSION = os.environ["IMAGE_VERSION"]

# Endpoint auth tokens cached per warm container. They are deliberately module
# level: they must never reach DynamoDB or the browser, and a warm container
# reusing one avoids a control-plane call on every single request.
TOKENS = {}


# ==============================================================================
# AWS Clients and Session Storage — thin wrappers over boto3 and DynamoDB
# ==============================================================================

def microvms():
    """Build a lambda-microvms client tuned for short, synchronous calls.

    Returns:
        A boto3 client with aggressive timeouts, so a hung control-plane call
        surfaces as an error rather than consuming the whole Lambda budget.
    """
    return boto3.client("lambda-microvms", region_name=REGION, config=Config(
        connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 3}))


def table():
    """Return the DynamoDB Table resource holding session records."""
    return boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])


def load(tenant):
    """Read one tenant's session record.

    Args:
        tenant: Either "alice" or "bob".

    Returns:
        The stored session dict, or None when the tenant was never launched or
        its record belongs to a superseded image. The image check matters
        because reapplying with changed source produces a new image name, and a
        stale MicroVM id from the previous image must not be treated as live.
    """
    item = table().get_item(Key={"id": tenant}, ConsistentRead=True).get("Item")
    if not item or item.get("image") != IMAGE_ARN:
        return None
    return json.loads(item["data"])


def save(tenant, session):
    """Persist one tenant's session record.

    One row per tenant is a deliberate choice: a single shared row would make
    concurrent writes clobber each other, and a lost MicroVM id orphans a
    running VM that bills until its maximum duration expires.
    """
    table().put_item(Item={"id": tenant, "image": IMAGE_ARN, "data": json.dumps(session)})


def forget(tenant):
    """Drop a tenant's session record after the MicroVM is terminated."""
    table().delete_item(Key={"id": tenant})


def pages(client, method, **params):
    """Yield every item from a paginated lambda-microvms list call.

    Args:
        client: The lambda-microvms client.
        method: Name of the list operation, for example "list_microvms".
        **params: Passed through to the operation.

    Yields:
        Each element of the response's "items" array, following nextToken until
        the service stops returning one.
    """
    while True:
        result = getattr(client, method)(**params)
        yield from result.get("items", [])
        token = result.get("nextToken")
        if not token:
            return
        params["nextToken"] = token


# ==============================================================================
# MicroVM Lifecycle — run, suspend, resume, terminate and endpoint access
# ==============================================================================

def state_of(client, vm_id):
    """Return a MicroVM's current lifecycle state.

    A deleted MicroVM eventually stops resolving entirely, so a missing resource
    is reported as TERMINATED rather than raised. Callers treat the two the
    same, and this keeps every caller from repeating the same try/except.
    """
    try:
        return client.get_microvm(microvmIdentifier=vm_id)["state"]
    except client.exceptions.ResourceNotFoundException:
        return "TERMINATED"


def wait(client, vm_id, desired, timeout=20):
    """Poll until a MicroVM reaches the requested state.

    Args:
        client: The lambda-microvms client.
        vm_id: The MicroVM identifier.
        desired: Target state, for example "RUNNING" or "SUSPENDED".
        timeout: Seconds to wait. The default is generous for snapshot-backed
            transitions that normally complete in one to three seconds, while
            still leaving room inside the Lambda timeout.

    Returns:
        The state that was reached.

    Raises:
        RuntimeError: The VM reached a terminal or failed state instead.
        TimeoutError: The target state was not reached in time.
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
    """Mint, and cache, an endpoint auth token for one MicroVM.

    Tokens are scoped to port 8080 only, so a leaked token still cannot reach
    the lifecycle hook listener on 8081. The cache expires five minutes early to
    avoid handing out a token that dies mid-request.
    """
    cached = TOKENS.get(vm_id)
    if not cached or cached[1] < time.time():
        result = client.create_microvm_auth_token(
            microvmIdentifier=vm_id, expirationInMinutes=30, allowedPorts=[{"port": 8080}])
        cached = (result["authToken"]["X-aws-proxy-auth"], time.time() + 25 * 60)
        TOKENS[vm_id] = cached
    return cached[0]


def call(client, session, path, body=None, headers=None):
    """Send an authenticated HTTPS request to a MicroVM's own endpoint.

    Args:
        client: The lambda-microvms client, used to mint a token when needed.
        session: The stored session dict, providing the endpoint.
        path: Request path on the MicroVM's application server.
        body: Optional JSON-serializable request body; omit for a GET.
        headers: Optional override, used by the authentication checks to send
            deliberately wrong credentials.

    Returns:
        The decoded JSON response, with the measured round trip added so the
        dashboard can show real network latency rather than an estimate.
    """
    endpoint = session["endpoint"]
    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint
    if headers is None:
        headers = {"X-aws-proxy-auth": token(client, session["id"]), "X-aws-proxy-port": "8080"}
    if body is not None:
        headers = dict(headers, **{"Content-Type": "application/json"})
    request = urllib.request.Request(
        endpoint + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.load(response)
    value["round_trip_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return value


def status(client, tenant, session):
    """Report a session's AWS state alongside its last application sample.

    Deliberately control-plane only. Sampling the MicroVM endpoint here would
    count as traffic, auto-resume a suspended VM, and destroy the very
    suspension the dashboard is trying to display.
    """
    return dict(session, tenant=tenant, state=state_of(client, session["id"]),
                observation="Cached application data; AWS lifecycle state is current")


def launch(client, tenant):
    """Run a new MicroVM for a tenant and record its first observation.

    Raises:
        ValueError: The tenant already holds a live session, or the two-slot
            cap is reached once orphaned VMs are counted.
    """
    existing = load(tenant)
    if existing and state_of(client, existing["id"]) != "TERMINATED":
        raise ValueError("Terminate the existing session first")

    # Inventory the service rather than DynamoDB: a VM orphaned by an earlier
    # failure still costs money and still counts against the demo's cap.
    active = [vm for vm in pages(client, "list_microvms", imageIdentifier=IMAGE_ARN)
              if vm["state"] != "TERMINATED"]
    if len(active) >= 2:
        raise ValueError("Two-session cap reached, including orphans. Terminate one or run destroy.sh.")

    start = time.perf_counter()
    response = client.run_microvm(
        imageIdentifier=IMAGE_ARN,
        imageVersion=IMAGE_VERSION,
        clientToken=str(uuid.uuid4()),
        # The tenant name reaches the /run hook, which generates this session's
        # nonce. Identity must be created after the snapshot, never inside it.
        runHookPayload=json.dumps({"tenant": tenant}),
        ingressNetworkConnectors=[
            f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS"],
        idlePolicy={"autoResumeEnabled": True, "maxIdleDurationSeconds": 60,
                    "suspendedDurationSeconds": 900},
        maximumDurationInSeconds=1800,
        logging={"disabled": {}})  # No execution role, so the VM holds no AWS credentials.

    session = {"id": response["microvmId"], "endpoint": response["endpoint"]}
    save(tenant, session)  # Persist BEFORE the first HTTP call, so cleanup finds it on failure.
    wait(client, session["id"], "RUNNING")
    session["snapshot"] = call(client, session, "/state")
    session["launch_to_first_response_ms"] = round((time.perf_counter() - start) * 1000, 1)
    save(tenant, session)
    return status(client, tenant, session)


def auth_check(client, tenant, session):
    """Prove the MicroVM endpoint rejects every wrong form of credential.

    Exercises three refusals: no token at all, a valid token aimed at the hook
    port, and the other tenant's token. All three must return 403, which is what
    separates VM-level isolation from a shared process that merely pretends.

    Raises:
        RuntimeError: Any case was not refused, meaning the boundary leaks.
    """
    cases = {"no_token": {},
             "wrong_port": {"X-aws-proxy-auth": token(client, session["id"]),
                            "X-aws-proxy-port": "8081"}}
    other = "bob" if tenant == "alice" else "alice"
    peer = load(other)
    if peer and state_of(client, peer["id"]) != "TERMINATED":
        cases["other_tenant_token"] = {"X-aws-proxy-auth": token(client, peer["id"])}

    outcomes = {}
    for name, headers in cases.items():
        try:
            call(client, session, "/state", headers=headers)
            outcomes[name] = 200
        except urllib.error.HTTPError as exc:
            outcomes[name] = exc.code
    if any(code != 403 for code in outcomes.values()):
        raise RuntimeError(f"Authentication boundary check failed: {outcomes}")
    return {"checks": outcomes, "ok": True}


def act(client, tenant, action, code):
    """Dispatch one lifecycle action against a tenant's session.

    Args:
        client: The lambda-microvms client.
        tenant: Either "alice" or "bob".
        action: A member of ACTIONS.
        code: Python source, used only by the "execute" action.

    Returns:
        The session status, or the action's own result for auth-check.

    Raises:
        ValueError: The tenant has no session, or the action is unknown.
    """
    if action == "launch":
        return launch(client, tenant)

    session = load(tenant)
    if not session:
        raise ValueError("Launch this tenant first")

    if action == "auth-check":
        return auth_check(client, tenant, session)

    if action == "suspend":
        # Sample the application before suspending. Once suspended, nothing may
        # touch the endpoint, because any request would silently resume it.
        session["before_suspend"] = call(client, session, "/state")
        client.suspend_microvm(microvmIdentifier=session["id"])
        wait(client, session["id"], "SUSPENDED")
    elif action == "terminate":
        if state_of(client, session["id"]) != "TERMINATED":
            client.terminate_microvm(microvmIdentifier=session["id"])
            wait(client, session["id"], "TERMINATED")
        forget(tenant)
        return {"tenant": tenant, "id": session["id"], "state": "TERMINATED"}
    else:
        # wake, sample and execute all send real traffic, which is exactly what
        # auto-resumes a suspended VM. Capturing the state first is what lets
        # the dashboard show that the request itself did the waking.
        before = state_of(client, session["id"])
        result = call(client, session, "/execute", {"code": code}) if action == "execute" else None
        session["snapshot"] = call(client, session, "/state")
        save(tenant, session)
        return dict(status(client, tenant, session), result=result, state_before_request=before)

    save(tenant, session)
    return status(client, tenant, session)


# ==============================================================================
# HTTP API — routes behind the API Gateway Cognito JWT authorizer
# ==============================================================================

def response(code, data):
    """Build an API Gateway proxy response.

    Cache-Control is no-store because every payload reflects live lifecycle
    state that is stale the moment it is written.
    """
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(data)}


def api(event, context):
    """Handle one API Gateway HTTP API request.

    API Gateway has already validated the Cognito JWT's signature, issuer,
    audience, expiry and scope. This function re-checks token_use, because a
    Cognito ID token is signed by the same issuer as an access token and would
    otherwise satisfy the authorizer while carrying the wrong claims.

    Args:
        event: API Gateway payload format 2.0 event.
        context: Lambda context object; unused.

    Returns:
        A proxy response. Client mistakes are 400, lifecycle conflicts and
        timeouts are 409, and anything unexpected is a deliberately vague 503.
    """
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    if not claims.get("sub") or claims.get("token_use") != "access":
        return response(401, {"error": "Cognito access token required"})
    try:
        client = microvms()
        route = event.get("routeKey")

        if route == "GET /api/config":
            return response(200, {"presets": PRESETS})

        if route == "GET /api/status":
            # Only launched tenants appear; the UI shows the rest as idle.
            result = {}
            for tenant in TENANTS:
                session = load(tenant)
                if session:
                    result[tenant] = status(client, tenant, session)
            return response(200, result)

        if route == "POST /api/action":
            body = event.get("body", "")
            if event.get("isBase64Encoded") or len(body.encode()) > 20000:
                raise ValueError("Invalid request body")
            payload = json.loads(body)
            tenant, action = payload.get("tenant"), payload.get("action")
            if tenant not in TENANTS or action not in ACTIONS:
                raise ValueError("Invalid tenant or action")
            code = payload.get("code", "")
            if not isinstance(code, str) or len(code.encode()) > 16000:
                raise ValueError("Code must be a string of at most 16000 bytes")
            return response(200, act(client, tenant, action, code))

        return response(404, {"error": "Not found"})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {"error": str(exc)})
    except (RuntimeError, TimeoutError) as exc:
        return response(409, {"error": str(exc)})
    except Exception:
        # Never leak SDK headers, endpoint tokens or stack traces to a browser.
        # The detail lands in CloudWatch instead, where it is not public.
        return response(503, {"error": "AWS controller request failed. Check service availability."})
