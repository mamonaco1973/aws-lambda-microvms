'use strict';

// -----------------------------------------------------------------------------
// Cognito Hosted UI sign-in: authorization code flow with S256 PKCE.
//
// A browser cannot keep a client secret, so the SPA client is public and PKCE
// replaces the secret: the code returned to the callback is worthless without
// the verifier, which never leaves this tab. Everything lives in an IIFE so the
// verifier and tokens are not reachable from tenant-facing page code.
// -----------------------------------------------------------------------------
window.auth = (() => {
  let settings;

  // sessionStorage, not localStorage: tokens die with the tab. On a shared or
  // recorded machine, closing the browser ends the session.
  const storage = sessionStorage;

  // OAuth requires base64url, which differs from btoa's alphabet and padding.
  const base64url = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  // 256 bits from the CSPRNG. Math.random is not acceptable for either value.
  const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));

  // Endpoints are fetched rather than compiled in, so redeploying to a new
  // account or region needs no source edit. no-store because a cached config
  // would point a fresh deployment at torn-down endpoints.
  async function init() {
    const response = await fetch('./config.json', { cache: 'no-store' });
    if (!response.ok) throw Error('Could not load AWS deployment configuration');
    settings = await response.json();
  }

  // Treat a token as expired 30s early, so one that dies mid-flight does not
  // surface as a confusing 401 in the middle of a lifecycle action.
  function token() {
    if (Date.now() >= Number(storage.getItem('expires_at') || 0) - 30000) return null;
    return storage.getItem('access_token');
  }

  async function login() {
    // verifier proves this tab started the flow; state defends against a
    // callback being replayed or forged from somewhere else.
    const verifier = random();
    const state = random();
    storage.setItem('pkce_verifier', verifier);
    storage.setItem('oauth_state', state);

    // Only the SHA-256 challenge crosses the network. The verifier is sent
    // once, later, directly to the token endpoint.
    const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
    const params = new URLSearchParams({ client_id: settings.clientId, response_type: 'code',
      redirect_uri: settings.redirectUri, scope: settings.scope, state,
      code_challenge_method: 'S256', code_challenge: challenge });
    location.assign(`https://${settings.cognitoDomain}/oauth2/authorize?${params}`);
  }

  async function callback() {
    const params = new URLSearchParams(location.search);

    // Strip the code from the address bar before anything can await, so it
    // stays out of history, bookmarks and any screen recording.
    history.replaceState({}, '', location.pathname);

    const expected = storage.getItem('oauth_state');
    const verifier = storage.getItem('pkce_verifier');

    // Single-use: clear both before validating, so a replayed callback finds
    // nothing to work with even if this attempt fails.
    storage.removeItem('oauth_state');
    storage.removeItem('pkce_verifier');

    if (params.has('error')) throw Error('Cognito sign-in was cancelled or rejected. Return to the demo and sign in again.');
    if (!expected || params.get('state') !== expected || !verifier || !params.get('code')) {
      throw Error('Sign-in state is missing or invalid. Return to the demo and start a new sign-in.');
    }

    // No client_secret; the verifier is what authenticates this exchange.
    const response = await fetch(`https://${settings.cognitoDomain}/oauth2/token`, {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'authorization_code', client_id: settings.clientId,
        redirect_uri: settings.redirectUri, code: params.get('code'), code_verifier: verifier }),
    });
    if (!response.ok) throw Error('Cognito could not complete sign-in. Please start again.');

    const tokens = await response.json();
    if (!tokens.access_token || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0) {
      throw Error('Invalid Cognito token response');
    }
    storage.setItem('access_token', tokens.access_token);
    storage.setItem('expires_at', String(Date.now() + tokens.expires_in * 1000));

    // Only the access token is kept. The API authorizes on its scope, and a
    // refresh token would outlive the tab it was issued to for no benefit.
    location.replace(settings.logoutUri);
  }

  // Clear local state first: if the redirect fails, nothing usable is left.
  function logout() {
    for (const key of ['access_token', 'expires_at', 'pkce_verifier', 'oauth_state']) storage.removeItem(key);
    const params = new URLSearchParams({ client_id: settings.clientId, logout_uri: settings.logoutUri });
    location.assign(`https://${settings.cognitoDomain}/logout?${params}`);
  }

  // Single call path to the controller, so the bearer header can never be
  // forgotten at a call site.
  async function api(path, options = {}) {
    const bearer = token();
    if (!bearer) throw Error('Sign-in expired. Sign in again; your MicroVMs keep their lifecycle policies.');
    const response = await fetch(settings.apiBaseUrl + path, {
      ...options, headers: { ...options.headers, Authorization: `Bearer ${bearer}` },
    });
    const data = await response.json();

    // A rejected token is dropped immediately so the UI prompts for sign-in
    // instead of retrying with credentials the API has already refused.
    if (response.status === 401) storage.removeItem('access_token');
    if (!response.ok) throw Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
  }

  return { init, token, login, logout, callback, api };
})();

// callback.html loads this same script; the redirect lands here, not on the
// dashboard, so the code exchange never runs on a page holding session data.
if (location.pathname.endsWith('/callback.html')) {
  auth.init().then(auth.callback).catch(error => {
    document.getElementById('message').textContent = error.message;
  });
}
