"""Cognito-protected HTTP API that drives the MicroVM lifecycle synchronously.

MicroVM launch and resume run from a pre-initialized snapshot in a few seconds,
so every action completes inside the API Gateway integration timeout. DynamoDB
holds only identifiers and the last application sample -- never interpreter
state, which lives exclusively inside each MicroVM.
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

ACTIONS = {"launch", "suspend", "wake", "sample", "execute", "auth-check", "terminate"}
TENANTS = ("alice", "bob")
REGION = os.environ["AWS_REGION"]
IMAGE_ARN = os.environ["IMAGE_ARN"]
IMAGE_VERSION = os.environ["IMAGE_VERSION"]

# Endpoint auth tokens are cached per warm container and never leave the Lambda.
TOKENS = {}


# ==============================================================================
# AWS Clients and Session Storage
# ==============================================================================

def microvms():
    """Return a lambda-microvms client tuned for short, synchronous calls."""
    return boto3.client("lambda-microvms", region_name=REGION, config=Config(
        connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 3}))


def table():
    return boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])


def load(tenant):
    """Read one tenant's session record, or None when it was never launched."""
    item = table().get_item(Key={"id": tenant}, ConsistentRead=True).get("Item")
    if not item or item.get("image") != IMAGE_ARN:
        return None
    return json.loads(item["data"])


def save(tenant, session):
    # One row per tenant, so Alice and Bob can never clobber each other's record.
    table().put_item(Item={"id": tenant, "image": IMAGE_ARN, "data": json.dumps(session)})


def forget(tenant):
    table().delete_item(Key={"id": tenant})


def pages(client, method, **params):
    while True:
        result = getattr(client, method)(**params)
        yield from result.get("items", [])
        token = result.get("nextToken")
        if not token:
            return
        params["nextToken"] = token


# ==============================================================================
# MicroVM Lifecycle
# ==============================================================================

def state_of(client, vm_id):
    try:
        return client.get_microvm(microvmIdentifier=vm_id)["state"]
    except client.exceptions.ResourceNotFoundException:
        return "TERMINATED"


def wait(client, vm_id, desired, timeout=20):
    """Poll until the MicroVM reaches `desired`, or raise."""
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
    """Mint (and cache) an endpoint token scoped to the application port only."""
    cached = TOKENS.get(vm_id)
    if not cached or cached[1] < time.time():
        result = client.create_microvm_auth_token(
            microvmIdentifier=vm_id, expirationInMinutes=30, allowedPorts=[{"port": 8080}])
        cached = (result["authToken"]["X-aws-proxy-auth"], time.time() + 25 * 60)
        TOKENS[vm_id] = cached
    return cached[0]


def call(client, session, path, body=None, headers=None):
    """Send an authenticated HTTPS request to the MicroVM's own endpoint."""
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
    # Control-plane only. Sampling the endpoint here would auto-resume the
    # MicroVM and destroy the very suspension this demo is showing.
    return dict(session, tenant=tenant, state=state_of(client, session["id"]),
                observation="Cached application data; AWS lifecycle state is current")


def launch(client, tenant):
    existing = load(tenant)
    if existing and state_of(client, existing["id"]) != "TERMINATED":
        raise ValueError("Terminate the existing session first")
    active = [vm for vm in pages(client, "list_microvms", imageIdentifier=IMAGE_ARN)
              if vm["state"] != "TERMINATED"]
    if len(active) >= 2:
        raise ValueError("Two-session cap reached, including orphans. Terminate one or run destroy.sh.")

    start = time.perf_counter()
    response = client.run_microvm(
        imageIdentifier=IMAGE_ARN,
        imageVersion=IMAGE_VERSION,
        clientToken=str(uuid.uuid4()),
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
    """Prove the endpoint rejects a missing token, a wrong port and the peer's token."""
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
    if action == "launch":
        return launch(client, tenant)

    session = load(tenant)
    if not session:
        raise ValueError("Launch this tenant first")

    if action == "auth-check":
        return auth_check(client, tenant, session)

    if action == "suspend":
        # Sample before suspending; nothing afterwards may wake the MicroVM.
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
        # wake / sample / execute all send real traffic, which auto-resumes the VM.
        before = state_of(client, session["id"])
        result = call(client, session, "/execute", {"code": code}) if action == "execute" else None
        session["snapshot"] = call(client, session, "/state")
        save(tenant, session)
        return dict(status(client, tenant, session), result=result, state_before_request=before)

    save(tenant, session)
    return status(client, tenant, session)


# ==============================================================================
# HTTP API
# ==============================================================================

def response(code, data):
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(data)}


def api(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    if not claims.get("sub") or claims.get("token_use") != "access":
        return response(401, {"error": "Cognito access token required"})
    try:
        client = microvms()
        route = event.get("routeKey")

        if route == "GET /api/config":
            return response(200, {"presets": PRESETS})

        if route == "GET /api/status":
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
        # Never leak SDK headers, endpoint tokens or stack traces to the browser.
        return response(503, {"error": "AWS controller request failed. Check service availability."})
