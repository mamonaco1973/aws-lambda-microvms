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
import base64
import json
import os
import re
import time
import traceback
import urllib.parse
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
    "get_file": "file",
    "share_file": "share",
    "view_file": "view",
}

REGION = os.environ["AWS_REGION"]
IMAGES = json.loads(os.environ["IMAGES"])          # {"python": {...}, "node": {...}, ...}
RUNTIMES = tuple(sorted(IMAGES))
BASE_IMAGE_ARN = os.environ["BASE_IMAGE_ARN"]
BASE_IMAGE_VERSION = os.environ["BASE_IMAGE_VERSION"]

# Passed to RunMicrovm, which is the MicroVM equivalent of an EC2 instance
# profile: the guest authenticates as this role with no key material inside it.
# Guest code can read the web bucket and mount/write the shared S3 Files
# filesystem. Whatever this role can do, submitted code can do.
MICROVM_ROLE_ARN = os.environ["MICROVM_ROLE_ARN"]

# Substituted into the AWS CLI preset so the cell can name a real bucket
# without the browser having to assemble the command.
WEB_BUCKET = os.environ["WEB_BUCKET"]
STORAGE = json.loads(os.environ.get("STORAGE_CONFIG", "{}"))

# Where files too large to inline are staged for download. Written by THIS
# function, never by the MicroVM: the controller already holds credentials, so
# routing the upload through here leaves the guest's execution role untouched.
SHARE_BUCKET = os.environ["SHARE_BUCKET"]

# This API's own base URL, used to build /dl links. Set by Terraform rather than
# derived from the request, so a link is the same whatever reached the Lambda.
APP_URL = os.environ["APP_URL"]

# Below this, a file is returned inline in the MCP response and rendered in the
# conversation. Base64 inflates by a third and the result has to survive the
# model's context, so this is deliberately well under what the transport would
# bear. Anything larger becomes a presigned link instead.
INLINE_LIMIT = 750_000

# Life of the presigned URL the /dl route redirects TO. It only has to outlive
# the redirect itself, since it is signed fresh on every click -- which is the
# reason /dl exists rather than handing out a presigned URL directly. Signing
# once, up front, would bind the link to the Lambda's temporary credentials and
# it could stop working long before its stated expiry.
SHARE_EXPIRY_SECONDS = 28800

# Types a browser would EXECUTE rather than display if served inline: markup
# and script. A view_file link serves these as plain text, so a generated page
# or SVG shows its source instead of running in the viewer's browser. Download
# links are unaffected -- a saved file does not run on its own.
ACTIVE_TYPES = {"text/html", "application/xhtml+xml", "image/svg+xml",
                "text/javascript", "application/javascript",
                "application/xml", "text/xml"}

# How much of a text file the inline viewer is given. It is a preview; the
# model already receives the whole file as text content.
VIEWER_TEXT_LIMIT = 20_000

# Types that are text despite not saying text/*. Worth listing because these
# are exactly what a cell tends to produce -- a JSON result, an SVG plot.
TEXTUAL = {"application/json", "application/xml", "image/svg+xml",
           "application/x-sh", "application/javascript"}

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

# Keep suspended state available throughout the session's remaining lifetime.
# This is a separate timer from idle suspension; it must not discard the
# sandbox just 30 minutes after it suspends.
SUSPENDED_TTL_SECONDS = 28800

# Eight hours total from launch, including time spent suspended. Resuming does
# not reset this deadline. Idle suspension still stops running compute charges.
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
        "execution_role": MICROVM_ROLE_ARN,            # web reads and shared filesystem access
        "ingress_connector": "ALL_INGRESS",
        "egress_connector": STORAGE.get("connector_arn", "INTERNET_EGRESS"),
        "shared_storage": STORAGE.get("file_system_id", "disabled"),
        "shell_enabled": False,                        # no SHELL_INGRESS attached
        "app_port": APP_PORT,
        "hook_port": HOOK_PORT,
        "auto_resume": True,
        "idle_suspend_seconds": IDLE_SUSPEND_SECONDS,
        "suspended_ttl_seconds": SUSPENDED_TTL_SECONDS,
        "max_lifetime_seconds": MAX_LIFETIME_SECONDS,
        "run_hook_payload": {"runtime": runtime, "storage": STORAGE},
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
    """Run a new MicroVM for this user, or report the one already running.

    Idempotent by design, because the MCP tool that calls it advertises itself
    that way: an agent quite reasonably calls launch_session before doing
    anything else, and refusing that with an error -- as this did -- makes the
    safe move look like a failure. Returning the live session is what the
    caller wanted in either case.

    The refusal this replaced was guarding against a stray Launch click
    orphaning a billable MicroVM. It cannot: the session record is keyed per
    user, so a second launch would have overwritten the id of the VM it was
    protecting, which is the very thing that orphans one.
    """
    existing = load(user, runtime)
    if existing and state_of(client, existing["id"]) != "TERMINATED":
        return status(client, runtime, existing)

    image = IMAGES[runtime]
    start = time.perf_counter()
    response = client.run_microvm(
        imageIdentifier=image["image_arn"],
        imageVersion=image["image_version"],
        clientToken=str(uuid.uuid4()),
        # Reaches the /run hook, which generates this session's nonce. Identity
        # must be created after the snapshot, never baked into it.
        runHookPayload=json.dumps({"runtime": runtime, "storage": STORAGE}),
        egressNetworkConnectors=[STORAGE["connector_arn"]] if STORAGE else [
            f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"],
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


def fetch_file(client, session, path):
    """Read one file out of a MicroVM and return it with its metadata.

    Bytes take their own endpoint rather than a cell, because a cell's stdout
    is capped and truncated -- base64 through the cell protocol is the thing
    this exists to avoid.

    Returns:
        (body, mime, name)

    Raises:
        ValueError: The MicroVM refused the request, with its reason.
    """
    endpoint = session["endpoint"]
    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint
    query = urllib.parse.urlencode({"path": path})
    request = urllib.request.Request(
        f"{endpoint}/file?{query}",
        headers={"X-aws-proxy-auth": token(client, session["id"]),
                 "X-aws-proxy-port": str(APP_PORT)})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return (response.read(),
                    response.headers.get("Content-Type", "application/octet-stream"),
                    response.headers.get("X-Microvm-File-Name", "file"))
    except urllib.error.HTTPError as exc:
        # The VM answers a refusal as JSON, so surface its message rather than
        # a bare status the user can do nothing with.
        try:
            message = json.load(exc).get("error", "")
        except ValueError:
            message = ""
        raise ValueError(message or f"MicroVM returned HTTP {exc.code}") from None


# Leading bytes of the types worth recognising. Checked before the name,
# because the MicroVM can only guess from the extension and a file written by a
# cell may have a misleading one or none at all -- `plot`, or `out.txt` holding
# a PNG. Getting this wrong means an image returned as mojibake text.
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)


def sniff(body, declared):
    """Decide a file's type from its content, falling back to its name.

    Args:
        body: The file's bytes.
        declared: What the MicroVM guessed from the extension.

    Returns:
        A media type. Text is detected by decoding rather than by magic bytes,
        since it has none -- anything that is valid UTF-8 and free of NULs is
        treated as text so it can be returned readable.
    """
    for prefix, mime in MAGIC:
        if body.startswith(prefix):
            return mime
    if body.lstrip()[:5].lower().startswith(b"<svg") or b"<svg" in body[:200].lower():
        return "image/svg+xml"
    if b"\x00" not in body[:8192]:
        try:
            body[:8192].decode("utf-8")
            # Keep a more specific declared text type (text/csv, application
            # /json) rather than flattening everything to text/plain.
            if declared.startswith("text/") or declared in TEXTUAL:
                return declared
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return declared


def share(body, mime, name, route="dl"):
    """Stage a file in S3 and return a short link to it.

    Returns a link to this API rather than a presigned S3 URL, for two
    reasons. The signature is then minted when the link is CLICKED, from
    whatever credentials are live at that moment -- a URL signed here and now
    would be signed with the Lambda's temporary credentials and could stop
    working when those lapse, well before its stated expiry. And the result is
    a link a person can read, rather than 700 characters of query string.

    Args:
        body: The file's bytes.
        mime: Its media type, as decided by sniff().
        name: The filename to offer the downloader.
        route: "dl" for a link that downloads the file, "view" for one that
            opens it in the browser. The staged object is identical; only the
            route -- and so the Content-Disposition it is signed with --
            differs.

    Returns:
        An absolute https URL to this API's /dl or /view route.
    """
    s3 = boto3.client("s3", region_name=REGION)
    token = uuid.uuid4().hex[:12]
    # The random segment is the whole secret, so the name must not be part of
    # the id -- it goes in the key beneath it, where it is only ever revealed
    # to someone who already holds the link.
    s3.put_object(Bucket=SHARE_BUCKET, Key=f"{token}/{name}", Body=body,
                  ContentType=mime)
    return f"{APP_URL}/{route}/{token}"


def download(token, inline=False):
    """Redirect a /dl or /view link to a freshly signed S3 URL.

    Args:
        token: The random segment from the link.
        inline: True for /view -- sign the URL so the browser displays the
            file rather than saving it.

    Returns:
        A 302 to a presigned URL, or a 404 when the object is gone -- which is
        how these links expire: the bucket's lifecycle rule reaps staged files
        within about a day, and the link stops resolving with them.
    """
    s3 = boto3.client("s3", region_name=REGION)
    listing = s3.list_objects_v2(Bucket=SHARE_BUCKET, Prefix=f"{token}/",
                                 MaxKeys=1)
    contents = listing.get("Contents") or []
    if not contents:
        return {"statusCode": 404,
                "headers": {"Content-Type": "text/plain",
                            "Cache-Control": "no-store"},
                "body": "This download link has expired."}

    key = contents[0]["Key"]
    name = key.split("/", 1)[1]
    # The filename matters either way: without it the browser would name the
    # saved file -- or the tab -- after the key, random segment and all.
    disposition = "inline" if inline else "attachment"
    params = {"Bucket": SHARE_BUCKET, "Key": key,
              "ResponseContentDisposition": f'{disposition}; filename="{name}"'}
    if inline:
        # Anything a browser would execute is served as text, so viewing a
        # generated page shows its source rather than running it.
        stored = s3.head_object(Bucket=SHARE_BUCKET, Key=key)["ContentType"]
        if stored.split(";")[0].strip().lower() in ACTIVE_TYPES:
            params["ResponseContentType"] = "text/plain; charset=utf-8"
    url = s3.generate_presigned_url("get_object", Params=params,
                                    ExpiresIn=SHARE_EXPIRY_SECONDS)
    return {"statusCode": 302,
            "headers": {"Location": url, "Cache-Control": "no-store"},
            "body": ""}


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

    if action in ("file", "share", "view"):
        session = load(user, runtime)
        if not session:
            raise ValueError("Launch this runtime first")
        body, declared, name = fetch_file(client, session, arguments.get("path", ""))
        mime = sniff(body, declared)

        # An image small enough to inline goes back as MCP image content, which
        # the client renders in the conversation. Text does the same as text.
        # Everything else -- too big, or a type with nothing to render -- gets
        # a link, which is also what "share" asks for outright.
        if action == "file" and len(body) <= INLINE_LIMIT:
            # Textual first, and SVG is the reason: it is image/* but an MCP
            # image block carries raster data, so an SVG returned that way
            # renders as nothing. As text the client can draw it.
            if mime.startswith("text/") or mime in TEXTUAL:
                text = body.decode("utf-8", "replace")
                return {"_content": [{"type": "text", "text": text}],
                        "_structured": {"kind": "text", "name": name,
                                        "bytes": len(body),
                                        "text": text[:VIEWER_TEXT_LIMIT]}}
            if mime.startswith("image/"):
                data = base64.b64encode(body).decode()
                # The viewer loads the image from a /view link, never from
                # base64 in structuredContent. ChatGPT hands structuredContent
                # to the model as well as the viewer, so an embedded image put
                # most of a megabyte into it -- and those results stopped
                # reaching the viewer at all. The link path is what displayed
                # the large images reliably. Claude still gets the image block
                # below, which is untouched.
                return {"_content": [{"type": "image", "mimeType": mime,
                                      "data": data}],
                        "_structured": {"kind": "image", "name": name,
                                        "mime": mime, "bytes": len(body),
                                        "url": share(body, mime, name, "view")}}

        # An image too big to inline is still shown by the viewer, so it
        # gets a /view link rather than a download.
        showable = mime.startswith("image/") and mime not in ACTIVE_TYPES
        route = "view" if action == "view" or (action == "file" and showable) else "dl"
        url = share(body, mime, name, route)
        # The note is what the model reads, and it must not claim the user saw
        # nothing when the viewer showed them the image. An earlier wording
        # ("too large to display, uploaded instead") sent the model off to
        # shrink the file and fetch it again, and the user saw it twice. It
        # cannot say "displayed" outright either: the server cannot tell
        # which client is calling, and only some hosts draw the viewer.
        note = "Give the user this link; it is not a file you can read."
        if action == "view":
            note = ("Give the user this link; it opens the file in their "
                    "browser rather than downloading it.")
        if action == "file":
            note = (f"{name} is {len(body)} bytes, too large to embed, so it "
                    "was uploaded and returned as a link. " + note)
        if action in ("file", "view") and showable:
            note = ("This image is already shown to the user in the inline "
                    "viewer where the client supports one; the link is the "
                    "fallback. Done -- do not fetch, convert or shrink the "
                    "file to show it again.")
        result = {"name": name, "bytes": len(body), "mime": mime,
                  "url": url, "expires_in_seconds": SHARE_EXPIRY_SECONDS,
                  "note": note}
        if action in ("file", "view"):
            result["_structured"] = {"kind": "image" if showable else "link",
                                     "name": name, "mime": mime,
                                     "bytes": len(body), "url": url}
        return result

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

    # Public deliberately: a browser following a download link carries no token,
    # and a viewer cannot be asked to sign in to collect a file. The random
    # segment is the credential, exactly as it is for a presigned URL -- this is
    # the same bearer-link model, with a shorter string.
    # /view is the same link, opened in the browser instead of saved.
    raw = event.get("rawPath", "")
    if http.get("method") == "GET" and raw.startswith(("/dl/", "/view/")):
        inline = raw.startswith("/view/")
        token = raw[len("/view/" if inline else "/dl/"):]
        if not re.fullmatch(r"[0-9a-f]{1,32}", token):
            return response(400, {"error": "Invalid download link"})
        return download(token, inline)

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
