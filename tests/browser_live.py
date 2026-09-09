"""Live Cognito + hosted controller acceptance test. Creates no local HTTP server.

Uses a temporary Cognito presenter, then terminates test sessions and deletes it.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid

import boto3
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "02-lambdas" / "app"))
import cloud
from control import AWSLab
from handler import Store
from validation import validate


class HostedLab:
    mode = "AWS LIVE"

    def __init__(self, page):
        self.page = page
        self.sessions = {}

    def api(self, path, data=None):
        return self.page.evaluate("""async ({path, data}) => auth.api(path, data === null ? {} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        })""", {"path": path, "data": data})

    def status(self, tenant):
        return self.api("/api/status")[tenant]

    def wait(self, vm_id, desired, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for session in self.api("/api/status").values():
                if session["id"] == vm_id and session["state"] == desired:
                    return session
            time.sleep(1)
        raise TimeoutError("Hosted controller did not observe " + desired)

    def action(self, tenant, action, code=None):
        submitted = self.api("/api/action", {"request_id": str(uuid.uuid4()), "tenant": tenant, "action": action, "code": code or ""})
        deadline = time.monotonic() + 500
        while time.monotonic() < deadline:
            job = self.api("/api/operations/" + submitted["operation_id"])
            if job["status"] == "FAILED":
                raise RuntimeError(job["result"]["error"])
            if job["status"] == "DONE":
                result = job["result"]
                if "id" in result:
                    self.sessions[tenant] = result
                return result
            time.sleep(1)
        raise TimeoutError("Hosted operation timed out; execution was not replayed")

    def cleanup(self):
        for tenant, session in self.api("/api/status").items():
            if session["state"] != "TERMINATED":
                self.action(tenant, "terminate")


def main():
    image = cloud.outputs("01-microvms")
    settings = cloud.outputs("02-lambdas")
    if not image or not settings:
        raise RuntimeError("Apply the AWS infrastructure before running live browser validation")
    cognito = boto3.client("cognito-idp", region_name=image["region"])
    email = "microvm-test-" + uuid.uuid4().hex + "@example.invalid"
    password = "Aa9-" + secrets.token_urlsafe(24)
    created = False
    try:
        cloud.quiesce(settings, image["region"])
        table = boto3.resource("dynamodb", region_name=image["region"]).Table(settings["state_table"])
        AWSLab(image, store=Store(table)).cleanup()
        cloud.set_mapping(boto3.client("lambda", region_name=image["region"]), settings["worker_mapping_uuid"], True)
        table.delete_item(Key={"id": "maintenance"})
        cognito.admin_create_user(UserPoolId=settings["user_pool_id"], Username=email, MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])
        created = True
        cognito.admin_set_user_password(UserPoolId=settings["user_pool_id"], Username=email, Password=password, Permanent=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL", "msedge" if os.name == "nt" else None), headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1120}, locale="en-US")
            page = context.new_page()
            page.goto(settings["web_url"])
            page.get_by_role("button", name="Sign in with Cognito").click()
            page.locator('input[name="username"]:visible').fill(email)
            page.locator('input[name="password"]:visible').fill(password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url(settings["web_url"], timeout=60000)
            expect(page.locator("#mode")).to_have_text("AWS LIVE", timeout=30000)
            print("PASS Real Cognito Hosted UI + PKCE sign-in", flush=True)
            # The common acceptance sequence now runs through API Gateway, FIFO and worker.
            # No MicroVM token or AWS key is exposed to the browser test adapter.
            validate(HostedLab(page), report_name="aws-hosted-validation.json")
            page.screenshot(path=str(ROOT / "test-results" / "dashboard-aws.png"), full_page=True)
            page.get_by_role("button", name="Sign out", exact=True).click()
            page.wait_for_url(settings["web_url"], timeout=60000)
            expect(page.get_by_role("button", name="Sign in with Cognito")).to_be_visible()
            print("PASS Real Cognito logout and hosted-controller lifecycle validation", flush=True)
            browser.close()
    finally:
        # This image-scoped administrative cleanup also handles failed submissions or lost browser connectivity.
        try:
            cloud.quiesce(settings, image["region"])
            store = Store(boto3.resource("dynamodb", region_name=image["region"]).Table(settings["state_table"]))
            AWSLab(image, store=store).cleanup()
        finally:
            try:
                if created:
                    cognito.admin_delete_user(UserPoolId=settings["user_pool_id"], Username=email)
            finally:
                cloud.set_mapping(boto3.client("lambda", region_name=image["region"]), settings["worker_mapping_uuid"], True)
                boto3.resource("dynamodb", region_name=image["region"]).Table(settings["state_table"]).delete_item(Key={"id": "maintenance"})


if __name__ == "__main__":
    main()
