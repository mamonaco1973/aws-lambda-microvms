'use strict';

// -----------------------------------------------------------------------------
// Page state
// -----------------------------------------------------------------------------
let config;                     // { runtimes, presets, specs } from GET /api/config
let settings;                   // config.json -- the API URL and nothing else
let busy = false;
let active;                     // which runtime tab is showing
const states = {};              // last known status per runtime

const RUNTIME_LABELS = { python: 'Python', node: 'Node.js', bash: 'Bash' };

const PRESET_LABELS = {
  seed: '1 - Seed state',
  check: '2 - Check state',
  // Offered only by runtimes that define it; buildPanel skips absent keys.
  background: 'Background job (bash)',
  failure: 'Raise an error',
  kill: 'Kill this interpreter',
};

// -----------------------------------------------------------------------------
// The EC2 comparison. Rows are [label, key into the spec, EC2 equivalent].
// A null equivalent means EC2 has no counterpart -- those are the rows the whole
// demo exists to show, so they are rendered differently.
//
// Display copy lives here rather than in the controller: the controller reports
// what it actually configured, and this file says what that corresponds to.
// -----------------------------------------------------------------------------
const COMPARISON = [
  ['Image', s => `${s.image_name} v${s.image_version}`, 'AMI'],
  ['Base image', s => `al2023-1 v${s.base_image_version}`, null],
  ['Architecture', s => s.architecture, 'Graviton instance family'],
  ['Baseline', s => `${s.baseline_mib} MiB / ${s.baseline_vcpu} vCPU`, 'Instance type'],
  ['Burst ceiling', s => `${s.burst_mib} MiB (4x)`, 'T-series, minus the CPU credits'],
  ['Disk', s => `${s.disk_gb} GB`, 'EBS root volume'],
  ['Instance id', (s, live) => live?.id ?? '(not launched)', 'i-0123456789abcdef0'],
  ['Endpoint', (s, live) => live?.endpoint ?? '(not launched)', 'Public DNS / Elastic IP'],
  ['Ingress', s => s.ingress_connector, 'Security group inbound rules'],
  ['Egress', s => s.egress_connector, 'Security group outbound + IGW'],
  ['Execution role', s => s.execution_role ?? 'none', 'IAM instance profile'],
  ['Launch payload', s => JSON.stringify(s.run_hook_payload), 'User data (both 16 KB)'],
  ['Initialisation', s => `/run hook on port ${s.hook_port}`, 'cloud-init'],
  ['Shell access', s => (s.shell_enabled ? 'SHELL_INGRESS attached' : 'not enabled'), 'SSH keypair on :22'],
  ['Idle policy', s => `suspend after ${s.idle_suspend_seconds}s`, null],
  ['Auto-resume', s => (s.auto_resume ? 'on inbound traffic' : 'disabled'), null],
  ['Suspended TTL', s => `terminate after ${s.suspended_ttl_seconds}s`, null],
  ['Maximum lifetime', s => `${s.max_lifetime_seconds}s (8h ceiling)`, null],
  ['State', (s, live) => live?.state ?? 'NOT LAUNCHED', 'running / stopped'],
];

function log(message, isError = false) {
  const row = document.createElement('p');
  row.textContent = `${new Date().toLocaleTimeString()}  ${message}`;
  if (isError) row.className = 'error';
  document.querySelector('#timeline').prepend(row);
}

// -----------------------------------------------------------------------------
// API access. The passphrase lives in sessionStorage so it dies with the tab,
// and is sent as a header on every call.
// -----------------------------------------------------------------------------
async function api(path, options = {}) {
  const response = await fetch(settings.apiBaseUrl + path, {
    ...options,
    headers: {
      ...options.headers,
      'X-Demo-Passphrase': sessionStorage.getItem('passphrase') || '',
    },
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    // Stale or wrong passphrase: drop it and show the gate again rather than
    // retrying with a credential the API has already refused.
    sessionStorage.removeItem('passphrase');
    location.reload();
  }
  if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
  return data;
}

// -----------------------------------------------------------------------------
// Rendering
// -----------------------------------------------------------------------------
function buildTabs() {
  const tabs = document.querySelector('#tabs');
  for (const runtime of config.runtimes) {
    const button = document.createElement('button');
    button.className = 'tab';
    button.dataset.runtime = runtime;
    button.setAttribute('role', 'tab');
    button.textContent = RUNTIME_LABELS[runtime] ?? runtime;
    button.onclick = () => selectTab(runtime);
    tabs.append(button);
  }
}

function selectTab(runtime) {
  active = runtime;
  for (const tab of document.querySelectorAll('.tab')) {
    tab.classList.toggle('active', tab.dataset.runtime === runtime);
  }
  for (const panel of document.querySelectorAll('.panel')) {
    panel.hidden = panel.dataset.runtime !== runtime;
  }
}

function buildPanel(runtime) {
  const panel = document.createElement('section');
  panel.className = 'panel';
  panel.dataset.runtime = runtime;
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${RUNTIME_LABELS[runtime] ?? runtime} MicroVM</h2>
      <span class="status">NOT LAUNCHED</span>
    </div>
    <div class="buttons">
      <button data-action="launch" class="primary">Launch</button>
      <button data-action="suspend">Suspend</button>
      <button data-action="wake">Wake via HTTPS</button>
      <button data-action="sample">Sample</button>
      <button data-action="terminate">Terminate</button>
    </div>
    <div class="stats">
      <div class="stat"><small>Background ticks</small><strong data-stat="ticks">-</strong></div>
      <div class="stat"><small>Launch to response</small><strong data-stat="launch">-</strong></div>
      <div class="stat"><small>Init inside snapshot</small><strong data-stat="init">-</strong></div>
      <div class="stat"><small>Last round trip</small><strong data-stat="rtt">-</strong></div>
    </div>
    <div class="editor">
      <div class="editor-tools">
        <select aria-label="${runtime} code preset"></select>
        <button data-action="execute" class="primary">Run cell</button>
      </div>
      <textarea aria-label="${runtime} code" spellcheck="false"></textarea>
      <pre aria-label="${runtime} output">Ready for your first cell.</pre>
    </div>
    <details class="config" open>
      <summary>Configuration &mdash; and what it maps to on EC2</summary>
      <table class="comparison">
        <thead>
          <tr><th>Concept</th><th>This MicroVM</th><th>EC2 equivalent</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <p class="legend">
        Rows marked <span class="none">no equivalent</span> are capabilities EC2
        has no counterpart for.
      </p>
    </details>
    <div class="details"></div>`;

  const select = panel.querySelector('select');
  for (const [key, label] of Object.entries(PRESET_LABELS)) {
    if (!config.presets[runtime][key]) continue;
    const option = document.createElement('option');
    option.value = key;
    option.textContent = label;
    select.append(option);
  }
  const editor = panel.querySelector('textarea');
  select.value = 'seed';
  editor.value = config.presets[runtime].seed;
  select.onchange = () => { editor.value = config.presets[runtime][select.value]; };

  for (const button of panel.querySelectorAll('.buttons button, .editor button')) {
    button.onclick = () => action(runtime, button.dataset.action);
  }

  renderComparison(panel, runtime, null);
  return panel;
}

function renderComparison(panel, runtime, live) {
  const spec = config.specs[runtime];
  const body = panel.querySelector('.comparison tbody');
  body.replaceChildren();
  for (const [label, value, ec2] of COMPARISON) {
    const row = document.createElement('tr');
    const concept = document.createElement('td');
    concept.textContent = label;
    const mine = document.createElement('td');
    mine.className = 'mono';
    mine.textContent = String(value(spec, live));
    const theirs = document.createElement('td');
    if (ec2 === null) {
      theirs.className = 'none';
      theirs.textContent = 'no equivalent';
      row.classList.add('unmapped');
    } else {
      theirs.textContent = ec2;
    }
    row.append(concept, mine, theirs);
    body.append(row);
  }
}

function render(runtime, data, sampled = false) {
  states[runtime] = data;
  const panel = document.querySelector(`.panel[data-runtime="${runtime}"]`);
  const snapshot = data.snapshot;

  panel.querySelector('.status').textContent = data.state;
  panel.querySelector('.status').className = `status ${data.state}`;
  renderComparison(panel, runtime, data);

  if (snapshot) {
    panel.querySelector('[data-stat="ticks"]').textContent = snapshot.ticks;
    panel.querySelector('[data-stat="launch"]').textContent =
      data.launch_to_first_response_ms === undefined ? '-' : `${data.launch_to_first_response_ms}ms`;
    panel.querySelector('[data-stat="init"]').textContent =
      snapshot.initialization ? `${Math.round(snapshot.initialization.init_ms)}ms` : '-';
    panel.querySelector('[data-stat="rtt"]').textContent =
      snapshot.round_trip_ms === undefined ? '-' : `${snapshot.round_trip_ms}ms`;

    const details = panel.querySelector('.details');
    details.replaceChildren();
    for (const text of [
      `Session nonce: ${snapshot.session_nonce}`,
      `Image marker: ${snapshot.image_marker}`,
      `Processes: server ${snapshot.server_pid} / interpreter ${snapshot.worker_pid} - ${snapshot.worker_alive ? 'alive' : 'DEAD'}`,
      `File note.txt: ${snapshot.file ?? '(absent)'}`,
      `Hooks: ${snapshot.events.map(event => event.hook).join(' -> ')}`,
      sampled ? `Application sampled ${new Date().toLocaleTimeString()}` : '',
    ]) {
      if (!text) continue;
      const row = document.createElement('div');
      row.textContent = text;
      details.append(row);
    }
  }
  if (data.result) {
    const timing = data.result.execution_ms === undefined
      ? '' : `\n[execution: ${Math.round(data.result.execution_ms)}ms]`;
    panel.querySelector('pre').textContent = data.result.stdout + timing;
  }
}

// -----------------------------------------------------------------------------
// Actions complete synchronously; MicroVM endpoint tokens never leave AWS.
// -----------------------------------------------------------------------------
async function action(runtime, operation) {
  if (busy) return;
  busy = true;
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  log(`${RUNTIME_LABELS[runtime] ?? runtime} - ${operation} requested`);
  try {
    const data = await api('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        runtime,
        action: operation,
        code: document.querySelector(`.panel[data-runtime="${runtime}"] textarea`).value,
      }),
    });
    render(runtime, data, ['launch', 'sample', 'execute', 'wake'].includes(operation));
    const before = data.state_before_request ? ` (before the request: ${data.state_before_request})` : '';
    log(`${RUNTIME_LABELS[runtime] ?? runtime} - ${data.state}${before}`);
  } catch (error) {
    log(`${RUNTIME_LABELS[runtime] ?? runtime} - ${error.message}`, true);
  } finally {
    busy = false;
    for (const button of document.querySelectorAll('button')) button.disabled = false;
  }
}

// Refresh must never generate application traffic: that would resume a
// suspended MicroVM and destroy the thing being demonstrated.
async function refresh() {
  if (busy) return;
  try {
    const data = await api('/api/status');
    for (const [runtime, state] of Object.entries(data)) {
      if (states[runtime] && states[runtime].state !== state.state) {
        log(`${RUNTIME_LABELS[runtime] ?? runtime} - lifecycle ${states[runtime].state} -> ${state.state}`);
      }
      render(runtime, state);
    }
  } catch (error) {
    log(error.message, true);
  }
}

// -----------------------------------------------------------------------------
// Startup
// -----------------------------------------------------------------------------
async function start() {
  config = await api('/api/config');
  document.querySelector('#gate').hidden = true;
  document.querySelector('#app').hidden = false;
  buildTabs();
  const panels = document.querySelector('#panels');
  for (const runtime of config.runtimes) panels.append(buildPanel(runtime));
  selectTab(config.runtimes[0]);
  await refresh();
  setInterval(refresh, 5000);
}

async function main() {
  settings = await (await fetch('./config.json', { cache: 'no-store' })).json();

  const form = document.querySelector('#gate-form');
  const error = document.querySelector('#gate-error');
  form.onsubmit = async (event) => {
    event.preventDefault();
    error.hidden = true;
    sessionStorage.setItem('passphrase', document.querySelector('#passphrase').value.trim());
    try {
      await start();
    } catch (problem) {
      sessionStorage.removeItem('passphrase');
      error.textContent = problem.message;
      error.hidden = false;
    }
  };

  // Skip the gate when this tab already has a working passphrase.
  if (sessionStorage.getItem('passphrase')) {
    start().catch(() => sessionStorage.removeItem('passphrase'));
  }
}

main().catch(error => log(error.message, true));
