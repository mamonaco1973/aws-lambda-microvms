'use strict';

// -----------------------------------------------------------------------------
// Page state and request log
// -----------------------------------------------------------------------------
let config;
let busy = false;
const states = {};
const presetNames = {
  seed: '1 - Create Alice state',
  continue: '2 - Continue Alice state',
  inspect: 'Inspect fresh session',
  bob: 'Create Bob state',
  failure: 'Raise a Python error',
  kill: 'Kill this interpreter',
};

function log(message, error = false) {
  const row = document.createElement('p');
  row.textContent = `${new Date().toLocaleTimeString()}  ${message}`;
  if (error) row.className = 'error';
  document.querySelector('#timeline').prepend(row);
}

// -----------------------------------------------------------------------------
// One panel for each tenant. All tenant-supplied output uses textContent.
// -----------------------------------------------------------------------------
function createPanel(tenant) {
  const panel = document.createElement('article');
  panel.className = `tenant ${tenant}`;
  panel.id = tenant;
  panel.innerHTML = `
    <div class="tenant-head">
      <h2>${tenant === 'alice' ? 'Alice' : 'Bob'} - Python Session</h2>
      <span class="status">NOT LAUNCHED</span>
    </div>
    <div class="id">One dedicated MicroVM per tenant</div>
    <div class="buttons">
      <button data-action="launch" class="primary">Launch</button>
      <button data-action="suspend">Suspend</button>
      <button data-action="wake">Wake via HTTPS</button>
      <button data-action="sample">Sample</button>
      <button data-action="auth-check">Check auth</button>
      <button data-action="terminate">Terminate</button>
    </div>
    <div class="stats">
      <div class="stat"><small>Background ticks</small><strong data-stat="ticks">-</strong></div>
      <div class="stat"><small>Launch to response</small><strong data-stat="launch">-</strong></div>
      <div class="stat"><small>Last HTTP round trip</small><strong data-stat="rtt">-</strong></div>
    </div>
    <div class="details"></div>
    <div class="sample-label">No application sample yet</div>
    <div class="editor">
      <div class="editor-tools">
        <select aria-label="${tenant} code preset"></select>
        <button data-action="execute" class="primary">Run Python cell</button>
      </div>
      <textarea aria-label="${tenant} Python code" spellcheck="false"></textarea>
      <pre aria-label="${tenant} cell output">Ready for your first cell.</pre>
    </div>`;

  const select = panel.querySelector('select');
  for (const [key, label] of Object.entries(presetNames)) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = label;
    select.append(option);
  }
  select.value = tenant === 'alice' ? 'seed' : 'inspect';
  const editor = panel.querySelector('textarea');
  editor.value = config.presets[select.value];
  editor.readOnly = config.mode.startsWith('LOCAL');
  select.onchange = () => { editor.value = config.presets[select.value]; };

  for (const button of panel.querySelectorAll('button')) {
    button.onclick = () => action(tenant, button.dataset.action);
    if (config.mode.startsWith('LOCAL') && ['suspend', 'wake', 'auth-check'].includes(button.dataset.action)) {
      button.disabled = true;
      button.dataset.unsupported = 'true';
    }
  }
  return panel;
}

function render(tenant, data, sampled = false) {
  states[tenant] = data;
  const panel = document.getElementById(tenant);
  const snapshot = data.snapshot;
  panel.querySelector('.status').textContent = data.state;
  panel.querySelector('.status').className = `status ${data.state}`;
  panel.querySelector('.id').textContent = data.id;

  if (snapshot) {
    panel.querySelector('[data-stat="ticks"]').textContent = snapshot.ticks;
    panel.querySelector('[data-stat="launch"]').textContent =
      data.launch_to_first_response_ms === undefined ? '-' : `${data.launch_to_first_response_ms}ms`;
    panel.querySelector('[data-stat="rtt"]').textContent =
      snapshot.round_trip_ms === undefined ? 'local' : `${snapshot.round_trip_ms}ms`;

    const details = panel.querySelector('.details');
    details.replaceChildren();
    const rows = [
      `Session nonce: ${snapshot.session_nonce}`,
      `Image marker: ${snapshot.image_marker}`,
      `Processes: server ${snapshot.server_pid} / interpreter ${snapshot.worker_pid} - ${snapshot.worker_alive ? 'alive' : 'DEAD'}`,
      `File note.txt: ${snapshot.file ?? '(absent)'}`,
      `Preloaded: ${snapshot.initialization.rows.toLocaleString()} rows - initialization ${snapshot.initialization.init_ms}ms`,
      `Hooks: ${snapshot.events.map(event => event.hook).join(' -> ')}`,
    ];
    for (const text of rows) {
      const row = document.createElement('div');
      row.textContent = text;
      details.append(row);
    }
    if (sampled) {
      panel.querySelector('.sample-label').textContent =
        `Application sampled ${new Date().toLocaleTimeString()} - subsequent refreshes query control plane only`;
    }
  }
  if (data.result) {
    const timing = data.result.execution_ms === undefined ? '' : `\n[Python execution: ${data.result.execution_ms}ms]`;
    panel.querySelector('pre').textContent = data.result.stdout + timing;
  }
}

// -----------------------------------------------------------------------------
// FIFO worker serializes lifecycle operations. MicroVM tokens stay inside AWS.
// -----------------------------------------------------------------------------
async function action(tenant, operation) {
  if (busy) return;
  busy = true;
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  log(`${tenant.toUpperCase()} - ${operation} requested`);
  try {
    const submitted = await auth.api('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenant,
        request_id: crypto.randomUUID(),
        action: operation,
        code: document.querySelector(`#${tenant} textarea`).value,
      }),
    });
    let job;
    const deadline = Date.now() + 500000;
    do {
      if (Date.now() > deadline) throw Error('Operation timed out. Inspect the session before submitting again.');
      await new Promise(resolve => setTimeout(resolve, 1500));
      job = await auth.api(`/api/operations/${submitted.operation_id}`);
    } while (['QUEUED', 'RUNNING'].includes(job.status));
    const data = job.result;
    if (job.status !== 'DONE') throw Error(data?.error || 'Operation failed');
    if (data.checks) {
      log(`${tenant.toUpperCase()} - AUTH PASS ${JSON.stringify(data.checks)}`);
    } else {
      render(tenant, data, ['launch', 'sample', 'execute', 'wake'].includes(operation));
      const before = data.state_before_request ? ` (before HTTP: ${data.state_before_request})` : '';
      log(`${tenant.toUpperCase()} - ${data.state}${before}`);
    }
  } catch (error) {
    log(`${tenant.toUpperCase()} - ${error.message}`, true);
  } finally {
    busy = false;
    for (const button of document.querySelectorAll('button')) {
      button.disabled = button.dataset.unsupported === 'true';
    }
  }
}

// Refresh must never generate application traffic: that would defeat suspension.
async function refresh() {
  if (busy) return;
  if (!auth.token()) {
    document.querySelector('#login').hidden = false;
    document.querySelector('#logout').hidden = true;
    document.querySelector('#mode').textContent = 'Sign-in expired. Sign in again to control the sessions.';
    return;
  }
  try {
    const data = await auth.api('/api/status');
    if (data.busy) return;
    for (const [tenant, state] of Object.entries(data)) {
      if (states[tenant] && states[tenant].state !== state.state) {
        log(`${tenant.toUpperCase()} - AWS lifecycle ${states[tenant].state} -> ${state.state}`);
      }
      render(tenant, state);
    }
  } catch (error) {
    log(error.message, true);
  }
}

async function main() {
  await auth.init();
  document.querySelector('#login').onclick = () => auth.login().catch(error => log(error.message, true));
  document.querySelector('#logout').onclick = auth.logout;
  if (!auth.token()) return;
  document.querySelector('#login').hidden = true;
  document.querySelector('#logout').hidden = false;
  config = await auth.api('/api/config');
  const badge = document.querySelector('#mode');
  badge.textContent = config.mode;
  if (config.mode.startsWith('LOCAL')) badge.classList.add('local');
  for (const tenant of ['alice', 'bob']) {
    document.querySelector('#tenants').append(createPanel(tenant));
  }
  await refresh();
  setInterval(refresh, 5000);
}

main().catch(error => log(error.message, true));
