"""HTTP API that drives the MicroVM lifecycle for every runtime, synchronously.

MicroVM launch and resume run from a pre-initialized Firecracker snapshot in a
few seconds, so every action completes well inside the API Gateway integration
timeout. That is why there is no queue, no worker and no job table.

Every image is launched from the same code path -- Python, Node and Bash -- which
is the demonstration: the platform is identical, the runtime is yours.

DynamoDB holds only identifiers and the last application sample. Interpreter
state lives exclusively inside each MicroVM and is never serialized out.
"""
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

from presets import PRESETS

ACTIONS = {"launch", "suspend", "wake", "execute", "terminate"}

REGION = os.environ["AWS_REGION"]
IMAGES = json.loads(os.environ["IMAGES"])          # {"python": {...}, "node": {...}, ...}
RUNTIMES = tuple(sorted(IMAGES))
BASE_IMAGE_ARN = os.environ["BASE_IMAGE_ARN"]
BASE_IMAGE_VERSION = os.environ["BASE_IMAGE_VERSION"]
DEMO_PASSPHRASE = os.environ["DEMO_PASSPHRASE"]

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
MAX_LIFETIME_SECONDS = 3600
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


def load(runtime):
    """Read one runtime's session record, or None if it has no live session.

    The image check matters because reapplying with changed source produces a
    new image, and a stale MicroVM id from the previous one is not live.
    """
    item = table().get_item(Key={"id": runtime}, ConsistentRead=True).get("Item")
    if not item or item.get("image") != IMAGES[runtime]["image_arn"]:
        return None
    return json.loads(item["data"])


def save(runtime, session):
    """Persist one runtime's session record.

    One row per runtime: a shared row would let concurrent writes clobber each
    other, and a lost MicroVM id orphans a VM that bills until it expires.
    """
    table().put_item(Item={"id": runtime, "image": IMAGES[runtime]["image_arn"],
                           "data": json.dumps(session)})


def forget(runtime):
    """Drop a runtime's session record after its MicroVM is terminated."""
    table().delete_item(Key={"id": runtime})


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
        "execution_role": None,                        # no AWS credentials in the VM
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
    with urllib.request.urlopen(request, timeout=15) as response:
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


def launch(client, runtime):
    """Run a new MicroVM for a runtime and record its first observation."""
    existing = load(runtime)
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
        ingressNetworkConnectors=[
            f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS"],
        idlePolicy={"autoResumeEnabled": True,
                    "maxIdleDurationSeconds": IDLE_SUSPEND_SECONDS,
                    "suspendedDurationSeconds": SUSPENDED_TTL_SECONDS},
        maximumDurationInSeconds=MAX_LIFETIME_SECONDS,
        logging={"disabled": {}})  # No execution role, so no AWS credentials inside.

    session = {"id": response["microvmId"], "endpoint": response["endpoint"]}
    save(runtime, session)  # Persist BEFORE the first HTTP call, so cleanup finds it.
    wait(client, session["id"], "RUNNING")
    session["snapshot"] = call(client, session, "/state")
    session["launch_to_first_response_ms"] = round((time.perf_counter() - start) * 1000, 1)
    save(runtime, session)
    return status(client, runtime, session)


def act(client, runtime, action, code):
    """Dispatch one lifecycle action against a runtime's session."""
    if action == "launch":
        return launch(client, runtime)

    session = load(runtime)
    if not session:
        raise ValueError("Launch this runtime first")

    if action == "suspend":
        # Sample before suspending. Once suspended, nothing may touch the
        # endpoint, because any request would silently resume it.
        session["before_suspend"] = call(client, session, "/state")
        client.suspend_microvm(microvmIdentifier=session["id"])
        wait(client, session["id"], "SUSPENDED")
    elif action == "terminate":
        if state_of(client, session["id"]) != "TERMINATED":
            client.terminate_microvm(microvmIdentifier=session["id"])
            wait(client, session["id"], "TERMINATED")
        forget(runtime)
        return {"runtime": runtime, "id": session["id"], "state": "TERMINATED"}
    else:
        # Both wake and execute send real traffic, which is what auto-resumes a
        # suspended VM. Capturing the state first is what lets the panel show
        # that the request itself did the waking.
        before = state_of(client, session["id"])
        result = call(client, session, "/execute", {"code": code}) if action == "execute" else None
        session["snapshot"] = call(client, session, "/state")
        save(runtime, session)
        return dict(status(client, runtime, session), result=result,
                    state_before_request=before)

    save(runtime, session)
    return status(client, runtime, session)


# ==============================================================================
# HTTP API
# ==============================================================================

def response(code, data):
    """Build an API Gateway proxy response."""
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(data)}


def authorized(event):
    """Check the shared demo passphrase.

    compare_digest rather than == so the comparison does not leak the
    passphrase's length or prefix through timing. Header names arrive
    lowercased in payload format 2.0.
    """
    headers = event.get("headers") or {}
    supplied = headers.get("x-demo-passphrase", "")
    return hmac.compare_digest(supplied, DEMO_PASSPHRASE)


def api(event, context):
    """Handle one API Gateway HTTP API request."""
    if not authorized(event):
        return response(401, {"error": "Wrong or missing demo passphrase"})
    try:
        client = microvms()
        route = event.get("routeKey")

        if route == "GET /api/config":
            return response(200, {
                "runtimes": RUNTIMES,
                "presets": {r: PRESETS[r] for r in RUNTIMES},
                "specs": {r: spec(r) for r in RUNTIMES},
            })

        if route == "GET /api/status":
            result = {}
            for runtime in RUNTIMES:
                session = load(runtime)
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
            return response(200, act(client, runtime, action, code))

        return response(404, {"error": "Not found"})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {"error": str(exc)})
    except (RuntimeError, TimeoutError) as exc:
        return response(409, {"error": str(exc)})
    except Exception:
        # Never leak SDK headers, endpoint tokens or stack traces to a browser.
        return response(503, {"error": "AWS controller request failed. Check service availability."})
