"""Browser integration using intercepted HTTPS fixtures; no local web server.

Tests real frontend/PKCE code. These fixtures do NOT prove AWS authentication or VM isolation.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://demo.test"
CONFIG = {"cognitoDomain": "login.test", "clientId": "test-client", "redirectUri": SITE + "/callback.html",
          "logoutUri": SITE + "/index.html", "apiBaseUrl": "https://api.test", "scope": "openid email microvms/control"}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL", "msedge" if os.name == "nt" else None), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1120})
        errors = []
        jobs = {}
        token_exchanges = []

        def route(request):
            url = urlparse(request.request.url)
            if url.netloc == "demo.test":
                name = url.path.lstrip("/")
                if name == "config.json":
                    return request.fulfill(json=CONFIG)
                path = ROOT / "03-webapp" / name
                mime = "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "text/html"
                return request.fulfill(body=path.read_bytes(), content_type=mime)
            if url.netloc == "login.test":
                if url.path == "/oauth2/token":
                    token_exchanges.append(parse_qs(request.request.post_data))
                    return request.fulfill(json={"access_token": "fixture-access-token", "expires_in": 1800})
                return request.fulfill(body="Cognito navigation fixture", content_type="text/plain")
            if url.netloc == "api.test":
                assert request.request.headers.get("authorization") == "Bearer fixture-access-token"
                if url.path == "/api/config":
                    return request.fulfill(json={"mode": "BROWSER TEST FIXTURE", "presets": {key: "print('test')" for key in ["seed", "continue", "inspect", "bob", "failure", "kill"]}})
                if url.path == "/api/status":
                    return request.fulfill(json={})
                if url.path == "/api/action":
                    payload = request.request.post_data_json
                    jobs[payload["request_id"]] = {"id": "fixture-microvm", "state": "RUNNING",
                        "result": {"stdout": "balance: 41", "execution_ms": 1}}
                    return request.fulfill(status=202, json={"operation_id": payload["request_id"]})
                return request.fulfill(json={"status": "DONE", "result": jobs[url.path.rsplit("/", 1)[1]]})
            raise AssertionError("Unexpected external request: " + request.request.url)

        context.route("**/*", route)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(SITE + "/index.html")
        expect(page.locator("#login")).to_be_visible()
        assert page.locator(".tenant").count() == 0
        page.locator("#login").click()
        page.wait_for_url("https://login.test/**")
        params = parse_qs(urlparse(page.url).query)
        assert params["code_challenge_method"] == ["S256"]
        assert params["response_type"] == ["code"]
        page.goto(SITE + "/index.html")
        verifier = page.evaluate("sessionStorage.getItem('pkce_verifier')")
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert params["code_challenge"] == [expected]
        page.goto(SITE + "/callback.html?code=fixture-code&state=" + params["state"][0])
        page.wait_for_url(SITE + "/index.html")
        expect(page.locator("#mode")).to_have_text("BROWSER TEST FIXTURE")
        assert token_exchanges[0]["code_verifier"] == [verifier]
        assert page.evaluate("sessionStorage.getItem('pkce_verifier')") is None
        page.locator("#alice button[data-action=launch]").click()
        expect(page.locator("#alice .status")).to_have_text("RUNNING", timeout=10000)
        expect(page.locator("#alice pre")).to_contain_text("balance: 41")
        (ROOT / "test-results").mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT / "test-results" / "dashboard-browser-fixture.png"), full_page=True)
        # Missing state must fail closed, even with a syntactically valid code.
        page.goto(SITE + "/callback.html?code=other&state=forged")
        expect(page.locator("#message")).to_contain_text("missing or invalid")
        assert len(token_exchanges) == 1
        page.goto(SITE + "/index.html")
        page.locator("#logout").click()
        page.wait_for_url("https://login.test/logout**")
        page.goto(SITE + "/index.html")
        expect(page.locator("#login")).to_be_visible()
        assert page.evaluate("sessionStorage.getItem('access_token')") is None
        assert not errors, errors
        browser.close()
    print("PASS Browser: PKCE challenge/exchange, fail-closed callback state, protected requests, async operations and logout")


if __name__ == "__main__":
    main()
