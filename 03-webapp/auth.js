'use strict';

// Cognito Hosted UI, authorization code + S256 PKCE. No client secret in the SPA.
window.auth = (() => {
  let settings;
  const storage = sessionStorage;
  const base64url = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));
  async function init() {
    const response = await fetch('./config.json', { cache: 'no-store' });
    if (!response.ok) throw Error('Could not load AWS deployment configuration');
    settings = await response.json();
  }
  function token() {
    if (Date.now() >= Number(storage.getItem('expires_at') || 0) - 30000) return null;
    return storage.getItem('access_token');
  }
  async function login() {
    const verifier = random();
    const state = random();
    storage.setItem('pkce_verifier', verifier);
    storage.setItem('oauth_state', state);
    const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
    const params = new URLSearchParams({ client_id: settings.clientId, response_type: 'code',
      redirect_uri: settings.redirectUri, scope: settings.scope, state,
      code_challenge_method: 'S256', code_challenge: challenge });
    location.assign(`https://${settings.cognitoDomain}/oauth2/authorize?${params}`);
  }
  async function callback() {
    const params = new URLSearchParams(location.search);
    history.replaceState({}, '', location.pathname);
    const expected = storage.getItem('oauth_state');
    const verifier = storage.getItem('pkce_verifier');
    storage.removeItem('oauth_state');
    storage.removeItem('pkce_verifier');
    if (params.has('error')) throw Error('Cognito sign-in was cancelled or rejected. Return to the demo and sign in again.');
    if (!expected || params.get('state') !== expected || !verifier || !params.get('code')) {
      throw Error('Sign-in state is missing or invalid. Return to the demo and start a new sign-in.');
    }
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
    // ID and refresh tokens are not needed by this short recording session.
    location.replace(settings.logoutUri);
  }
  function logout() {
    for (const key of ['access_token', 'expires_at', 'pkce_verifier', 'oauth_state']) storage.removeItem(key);
    const params = new URLSearchParams({ client_id: settings.clientId, logout_uri: settings.logoutUri });
    location.assign(`https://${settings.cognitoDomain}/logout?${params}`);
  }
  async function api(path, options = {}) {
    const bearer = token();
    if (!bearer) throw Error('Sign-in expired. Sign in again; your MicroVMs keep their lifecycle policies.');
    const response = await fetch(settings.apiBaseUrl + path, {
      ...options, headers: { ...options.headers, Authorization: `Bearer ${bearer}` },
    });
    const data = await response.json();
    if (response.status === 401) storage.removeItem('access_token');
    if (!response.ok) throw Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
  }
  return { init, token, login, logout, callback, api };
})();

if (location.pathname.endsWith('/callback.html')) {
  auth.init().then(auth.callback).catch(error => {
    document.getElementById('message').textContent = error.message;
  });
}
