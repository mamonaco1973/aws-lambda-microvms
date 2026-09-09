"""AWS lifecycle driver. MicroVM tokens remain in the controller's memory."""
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]


def http_json(url, data=None, headers=None, timeout=30):
    headers = dict(headers or {})
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def aws_client(region):
    import boto3
    from botocore.config import Config
    session = boto3.Session(region_name=region)
    if "lambda-microvms" not in session.get_available_services():
        raise RuntimeError("Installed boto3 lacks lambda-microvms. Upgrade requirements before deploying.")
    return session.client("lambda-microvms", config=Config(
        connect_timeout=10, read_timeout=60, retries={"mode": "standard", "max_attempts": 4}))


def pages(client, method, **params):
    while True:
        result = getattr(client, method)(**params)
        yield from result.get("items", [])
        token = result.get("nextToken")
        if not token:
            return
        params["nextToken"] = token


class AWSLab:
    mode = "AWS LIVE"

    def __init__(self, config, client=None, state_path=None, store=None):
        self.config = config
        self.client = client or aws_client(config["region"])
        self.state_path = state_path or ROOT / ".lab" / "sessions.json"
        self.sessions = {}
        self.tokens = {}
        self.lock = threading.RLock()
        self.store = store
        if store is not None:
            self.sessions = store.read_sessions(config["image_arn"])
        elif self.state_path.exists():
            saved = json.loads(self.state_path.read_text())
            if saved["image_arn"] == config["image_arn"]:
                self.sessions = saved["sessions"]

    def save(self):
        if self.store is not None:
            self.store.write_sessions(self.config["image_arn"], self.sessions)
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"image_arn": self.config["image_arn"], "sessions": self.sessions}, indent=2))
        temp.replace(self.state_path)

    def wait(self, vm_id, desired, timeout=120):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                response = self.client.get_microvm(microvmIdentifier=vm_id)
            except self.client.exceptions.ResourceNotFoundException:
                if desired == "TERMINATED":
                    return {"state": "TERMINATED"}
                raise
            state = response["state"]
            if state == desired:
                return response
            if state == "TERMINATED" or "FAILED" in state:
                raise RuntimeError(f"MicroVM reached {state}, expected {desired}")
            time.sleep(0.5)
        raise TimeoutError(f"MicroVM did not reach {desired} within {timeout}s; use cleanup")

    def launch(self, tenant):
        with self.lock:
            if tenant not in {"alice", "bob"}:
                raise ValueError("Only alice and bob are allowed: two-session cost cap")
            if tenant in self.sessions:
                old = self.status(tenant)
                if old["state"] != "TERMINATED":
                    raise ValueError("Terminate the existing tenant session first")
            start = time.perf_counter()
            response = self.client.run_microvm(
                imageIdentifier=self.config["image_arn"], imageVersion=self.config["image_version"],
                clientToken=str(uuid.uuid4()),
                runHookPayload=json.dumps({"tenant": tenant}),
                ingressNetworkConnectors=[f"arn:aws:lambda:{self.config['region']}:aws:network-connector:aws-network-connector:ALL_INGRESS"],
                idlePolicy={"autoResumeEnabled": True, "maxIdleDurationSeconds": 60, "suspendedDurationSeconds": 900},
                maximumDurationInSeconds=1800,
                logging={"disabled": {}})  # No execution role / AWS credentials in the sandbox.
            self.sessions[tenant] = {"id": response["microvmId"], "endpoint": response["endpoint"]}
            self.save()  # Persist BEFORE readiness/token work, so cleanup survives a failure.
            self.wait(response["microvmId"], "RUNNING")
            snapshot = self.request(tenant, "/state")
            self.sessions[tenant]["launch_to_first_response_ms"] = round((time.perf_counter() - start) * 1000, 1)
            self.sessions[tenant]["snapshot"] = snapshot
            self.save()
            return self.status(tenant)

    def token(self, tenant):
        vm_id = self.sessions[tenant]["id"]
        cached = self.tokens.get(vm_id)
        if not cached or cached[1] < time.time():
            result = self.client.create_microvm_auth_token(
                microvmIdentifier=vm_id, expirationInMinutes=30, allowedPorts=[{"port": 8080}])
            cached = (result["authToken"]["X-aws-proxy-auth"], time.time() + 25 * 60)
            self.tokens[vm_id] = cached
        return cached[0]

    def request(self, tenant, path, data=None):
        endpoint = self.sessions[tenant]["endpoint"]
        endpoint = endpoint if endpoint.startswith("https://") else "https://" + endpoint
        start = time.perf_counter()
        value = http_json(endpoint + path, data, {"X-aws-proxy-auth": self.token(tenant), "X-aws-proxy-port": "8080"})
        value["round_trip_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return value

    def status(self, tenant):
        session = self.sessions[tenant]
        try:
            state = self.client.get_microvm(microvmIdentifier=session["id"])["state"]
        except self.client.exceptions.ResourceNotFoundException:
            state = "TERMINATED"
        # Intentionally control-plane only. Never refresh /state here.
        return dict(session, state=state, observation="Cached application data; AWS lifecycle state is current")

    def action(self, tenant, action, code=None):
        if action == "launch":
            return self.launch(tenant)
        if tenant not in self.sessions:
            raise ValueError("Launch this tenant first")
        if action == "status":
            return self.status(tenant)
        vm_id = self.sessions[tenant]["id"]
        if action == "suspend":
            # Explicit sample before suspension; subsequent monitoring cannot wake it.
            self.sessions[tenant]["before_suspend"] = self.request(tenant, "/state")
            self.client.suspend_microvm(microvmIdentifier=vm_id)
            self.wait(vm_id, "SUSPENDED")
        elif action == "terminate":
            if self.status(tenant)["state"] != "TERMINATED":
                self.client.terminate_microvm(microvmIdentifier=vm_id)
                self.wait(vm_id, "TERMINATED")
        elif action in {"wake", "sample", "execute"}:
            state_before = self.status(tenant)["state"]
            result = self.request(tenant, "/execute", {"code": code}) if action == "execute" else None
            self.sessions[tenant]["snapshot"] = self.request(tenant, "/state")
            self.save()
            return dict(self.status(tenant), result=result, state_before_request=state_before)
        elif action == "auth-check":
            return self.auth_check(tenant)
        else:
            raise ValueError("Unknown action")
        self.save()
        return self.status(tenant)

    def auth_check(self, tenant):
        endpoint = self.sessions[tenant]["endpoint"]
        base = endpoint if endpoint.startswith("https://") else "https://" + endpoint
        outcomes = {}
        cases = {"no_token": {}, "wrong_port": {"X-aws-proxy-auth": self.token(tenant), "X-aws-proxy-port": "8081"}}
        other = "bob" if tenant == "alice" else "alice"
        if other in self.sessions and self.status(other)["state"] != "TERMINATED":
            cases["other_tenant_token"] = {"X-aws-proxy-auth": self.token(other)}
        for name, headers in cases.items():
            try:
                http_json(base + "/state", headers=headers)
                outcomes[name] = 200
            except urllib.error.HTTPError as exc:
                outcomes[name] = exc.code
        if any(status != 403 for status in outcomes.values()):
            raise RuntimeError(f"Authentication boundary check failed: {outcomes}")
        return {"checks": outcomes, "ok": True}

    def cleanup(self):
        # Paginated service inventory handles orphaned sessions and lost local state.
        remaining = list(pages(self.client, "list_microvms", imageIdentifier=self.config["image_arn"]))
        for vm in remaining:
            if vm["state"] not in {"TERMINATED", "TERMINATING"}:
                self.client.terminate_microvm(microvmIdentifier=vm["microvmId"])
            self.wait(vm["microvmId"], "TERMINATED")
        self.sessions.clear()
        self.save()
        return {"terminated": len(remaining)}
