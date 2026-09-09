"""Deployment helpers; application serving and authentication run entirely in AWS."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import boto3
from terraform_state import read_outputs

ROOT = Path(__file__).resolve().parents[1]


def outputs(directory):
    return read_outputs(ROOT, directory)


def package():
    (ROOT / "dist").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="controller-") as temporary:
        target = Path(temporary)
        environment = dict(os.environ)
        if environment.get("AWS_CA_BUNDLE"):
            environment.setdefault("PIP_CERT", environment["AWS_CA_BUNDLE"])
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
                        "--only-binary=:all:", "--target", temporary, "-r", str(ROOT / "requirements.txt")],
                       check=True, env=environment)
        # All pinned dependencies are pure Python; reject accidental native Windows packages.
        if list(target.rglob("*.pyd")) or list(target.rglob("*.dll")):
            raise RuntimeError("Controller dependencies must be compatible with Linux arm64")
        for source in (ROOT / "02-lambdas" / "app").glob("*.py"):
            (target / source.name).write_bytes(source.read_bytes())
        with zipfile.ZipFile(ROOT / "dist" / "controller.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and "bin" not in path.relative_to(target).parts:
                    info = zipfile.ZipInfo(path.relative_to(target).as_posix(), (2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
    print("NOTE: Packaged dist/controller.zip, including the MicroVM-capable AWS SDK")


def write_vars(directory, settings):
    (ROOT / directory / "deployment.auto.tfvars.json").write_text(json.dumps(settings, indent=2))


def create_user(settings, region):
    email = input("Presenter email: ").strip()
    password = getpass.getpass("Password (12+ characters, uppercase, lowercase, number): ")
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords do not match")
    client = boto3.client("cognito-idp", region_name=region)
    try:
        client.admin_create_user(UserPoolId=settings["user_pool_id"], Username=email, MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])
    except client.exceptions.UsernameExistsException:
        print("NOTE: Updating the existing presenter's password")
    client.admin_set_user_password(UserPoolId=settings["user_pool_id"], Username=email, Password=password, Permanent=True)
    print("NOTE: Presenter ready. Sign in at " + settings["web_url"])


def check_web(settings):
    for file in ["index.html", "callback.html", "auth.js", "app.js", "style.css", "config.json"]:
        url = settings["web_url"].rsplit("/", 1)[0] + "/" + file
        with urllib.request.urlopen(url, timeout=20) as result:
            if result.status != 200:
                raise AssertionError(url)
        print("PASS Hosted asset: " + file)
    for headers in [{}, {"Authorization": "Bearer invalid"}]:
        try:
            urllib.request.urlopen(urllib.request.Request(settings["api_url"] + "/api/status", headers=headers), timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code not in {401, 403}:
                raise
        else:
            raise AssertionError("Protected API accepted an unauthenticated request")
    print("PASS API rejects absent and invalid access tokens")


def quiesce(settings, region):
    """Block submissions, stop queue polling and allow any in-flight worker to finish."""
    if not settings.get("state_table"):
        return  # A partial apply did not reach the controller's state table.
    table = boto3.resource("dynamodb", region_name=region).Table(settings["state_table"])
    table.put_item(Item={"id": "maintenance", "stopping": True})
    client = boto3.client("lambda", region_name=region)
    mapping = settings.get("worker_mapping_uuid")
    if not mapping:
        return
    set_mapping(client, mapping, False)
    deadline = time.monotonic() + 300
    # A disabling mapping can still have a running invocation. Its hard timeout is 240 seconds.
    # Jobs record their start time, so completed operations do not force a four-minute wait.
    while True:
        items = []
        params = {"ConsistentRead": True}
        while True:
            page = table.scan(**params)
            items.extend(page["Items"])
            if "LastEvaluatedKey" not in page:
                break
            params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        running = [item for item in items if item.get("status") == "RUNNING" and time.time() < int(item.get("started", 0)) + 250]
        if not running:
            break
        if time.monotonic() > deadline:
            raise TimeoutError("An operation is still running; retry teardown after it finishes")
        print("NOTE: Waiting for the in-flight AWS operation to finish...", flush=True)
        time.sleep(5)


def set_mapping(client, mapping, enabled):
    desired = "Enabled" if enabled else "Disabled"
    deadline = time.monotonic() + 300
    while True:
        state = client.get_event_source_mapping(UUID=mapping)["State"]
        if state == desired:
            return
        if time.monotonic() > deadline:
            raise TimeoutError("Worker event source did not reach " + desired)
        if state in {"Enabled", "Disabled"}:
            client.update_event_source_mapping(UUID=mapping, Enabled=enabled)
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["package", "prepare-backend", "prepare-web", "user", "url", "check", "quiesce", "resume"])
    args = parser.parse_args()
    if args.command == "package":
        return package()
    image = outputs("01-microvms")
    if args.command == "prepare-backend":
        return write_vars("02-lambdas", {key: image[key] for key in ["region", "name", "image_arn", "image_version"]})
    settings = outputs("02-lambdas")
    if not settings:
        if args.command == "quiesce":
            print("NOTE: No deployed controller in local Terraform state; maintenance is not needed.")
            return
        raise RuntimeError("AWS web backend has not been deployed")
    region = image["region"]
    if args.command == "prepare-web":
        return write_vars("03-webapp", {"region": region, "web_bucket_name": settings["web_bucket_name"], "web_config": settings["web_config"]})
    if args.command == "user":
        return create_user(settings, region)
    if args.command == "url":
        return print("Open " + settings["web_url"] + " and sign in with Cognito. No local server is needed.")
    if args.command == "check":
        return check_web(settings)
    if args.command == "quiesce":
        return quiesce(settings, region)
    if args.command == "resume":
        set_mapping(boto3.client("lambda", region_name=region), settings["worker_mapping_uuid"], True)
        boto3.resource("dynamodb", region_name=region).Table(settings["state_table"]).delete_item(Key={"id": "maintenance"})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
