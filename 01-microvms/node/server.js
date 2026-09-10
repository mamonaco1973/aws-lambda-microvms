'use strict';
// ============================================================================
// HTTP application and lifecycle-hook listener running inside the MicroVM.
//
// The Node counterpart of server.py. Two servers on two ports, for the same
// reasons:
//
//   * 8080 serves the application (/state, /execute). Endpoint auth tokens are
//     scoped to this port only.
//   * 8081 serves the AWS lifecycle hooks. Keeping hooks off the application
//     port means application traffic cannot drive the session's lifecycle.
//
// Standard library only. Every dependency added here would be baked into the
// snapshot and paid for on every launch.
// ============================================================================
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');
const { spawn } = require('child_process');

// AWS posts lifecycle hooks to this fixed path prefix on the configured port.
const HOOK = '/aws/lambda-microvms/runtime/v1/';
const HOOK_NAMES = ['ready', 'validate', 'run', 'suspend', 'resume', 'terminate'];

const PORT = Number(process.env.PORT || 8080);
const HOOK_PORT = Number(process.env.HOOK_PORT || 8081);
const WORKSPACE = process.env.WORKSPACE || '/workspace';

// ----------------------------------------------------------------------------
// Lab — owns the persistent interpreter child and this session's identity
// ----------------------------------------------------------------------------
// The interpreter runs as a separate process rather than in this one so a cell
// that hangs or calls process.exit can be killed without taking the HTTP server
// down with it. The server survives to report the damage, which is what makes
// "failure is isolated" visible.
class Lab {
  constructor(workspace) {
    this.workspace = path.resolve(workspace);
    fs.mkdirSync(this.workspace, { recursive: true });

    this.worker = spawn(process.execPath, [path.join(__dirname, 'worker.js')], {
      cwd: this.workspace,
      stdio: ['pipe', 'pipe', 'ignore'],
    });

    this.pending = [];        // queued resolvers for in-flight cells
    this.initialization = null;
    this.dead = false;
    this.busy = false;

    readline.createInterface({ input: this.worker.stdout }).on('line', (line) => {
      let value;
      try {
        value = JSON.parse(line);
      } catch (error) {
        value = { ok: false, stdout: 'Worker protocol corrupted; terminate this session.' };
      }
      if (this.initialization === null) {
        this.initialization = value;   // first line is the readiness report
        return;
      }
      const resolve = this.pending.shift();
      if (resolve) resolve(value);
    });

    this.worker.on('exit', () => {
      this.dead = true;
      while (this.pending.length) {
        this.pending.shift()({ ok: false, stdout: 'Worker exited; terminate this session.' });
      }
    });

    // Generated before the snapshot, so every VM cloned from this image shares
    // it. That is the point: it demonstrates what NOT to use as identity.
    this.imageMarker = crypto.randomUUID();

    // Generated in the /run hook instead, after restore, so it is unique per
    // session. Comparing the two across a resume proves the session resumed
    // rather than launched fresh.
    this.sessionNonce = null;

    this.runtime = 'image-build';
    this.microvmId = null;
    this.ticks = 0;
    this.events = [];

    // Never paused by this process, so a wall-clock gap far larger than the
    // tick delta is evidence that AWS genuinely froze the VM.
    this.heartbeat = setInterval(() => { this.ticks += 1; }, 1000);
  }

  // Resolves once the interpreter reports its dataset is loaded. The hook
  // listener does not bind until this settles, which is what gates the
  // snapshot on a fully warm process.
  ready(timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    return new Promise((resolve, reject) => {
      const poll = () => {
        if (this.initialization) {
          return this.initialization.ready
            ? resolve(this.initialization)
            : reject(new Error('Worker initialization failed'));
        }
        if (Date.now() > deadline) return reject(new Error('Worker did not report readiness'));
        setTimeout(poll, 50);
      };
      poll();
    });
  }

  state() {
    const note = path.join(this.workspace, 'note.txt');
    return {
      runtime: this.runtime,
      microvm_id: this.microvmId,
      session_nonce: this.sessionNonce,
      image_marker: this.imageMarker,
      server_pid: process.pid,
      worker_pid: this.worker.pid,
      ticks: this.ticks,
      initialization: this.initialization,
      events: this.events,
      file: fs.existsSync(note) ? fs.readFileSync(note, 'utf8').slice(0, 2000) : null,
      worker_alive: !this.dead && this.worker.exitCode === null,
    };
  }

  hook(name, data) {
    if (!HOOK_NAMES.includes(name)) throw new Error('Unknown hook');
    if (name === 'run') {
      if (this.sessionNonce !== null) {
        // AWS may retry a hook. Repeating it for the same VM is fine; for a
        // different one it means state is being reused wrongly.
        if (this.microvmId !== data.microvmId) throw new Error('Session already assigned');
      } else {
        let config = {};
        try { config = JSON.parse(data.runHookPayload || '{}'); } catch (error) { config = {}; }
        this.runtime = String(config.runtime || 'unknown').slice(0, 40);
        this.microvmId = data.microvmId || null;
        this.sessionNonce = crypto.randomUUID();
      }
    }
    this.events.push({ hook: name, wall_time: Date.now() / 1000, ticks: this.ticks });
    if (this.events.length > 20) this.events.shift();
    return { ok: true };
  }

  // A cell that overruns five seconds has its interpreter killed, which loses
  // the session. A usability guard for a live demo, emphatically not a
  // security sandbox: the VM boundary is the security boundary.
  execute(code) {
    if (typeof code !== 'string' || code.length > 12000) {
      throw new Error('Code must be a string of at most 12000 characters');
    }
    if (this.busy) {
      return Promise.resolve({ ok: false, stdout: 'Another cell is still running.' });
    }
    if (this.dead || this.worker.exitCode !== null) {
      return Promise.resolve({
        ok: false,
        stdout: 'Worker is dead; terminate and launch a fresh session.',
      });
    }

    this.busy = true;
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        this.busy = false;
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => {
        this.worker.kill('SIGKILL');
        this.dead = true;
        finish({
          ok: false,
          stdout: 'Cell exceeded 5 seconds. Worker killed; state is lost. Launch a fresh session.',
        });
      }, 5000);

      this.pending.push(finish);
      this.worker.stdin.write(JSON.stringify({ code }) + '\n');
    });
  }
}

// ----------------------------------------------------------------------------
// HTTP plumbing — one handler shape, two ports, disjoint route sets
// ----------------------------------------------------------------------------
function sendJson(response, value, status = 200) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
  });
  response.end(body);
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > 20000) return reject(new Error('Body too large'));
      chunks.push(chunk);
    });
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    request.on('error', reject);
  });
}

function handler(lab, hooks) {
  return async (request, response) => {
    try {
      if (!hooks && request.method === 'GET' && ['/state', '/health'].includes(request.url)) {
        return sendJson(response, lab.state());
      }
      if (request.method === 'POST') {
        const raw = await readBody(request);
        const data = raw ? JSON.parse(raw) : {};
        if (hooks && request.url.startsWith(HOOK)) {
          return sendJson(response, lab.hook(request.url.slice(HOOK.length), data));
        }
        if (!hooks && request.url === '/execute') {
          return sendJson(response, await lab.execute(data.code));
        }
      }
      sendJson(response, { error: 'Not found' }, 404);
    } catch (error) {
      sendJson(response, { error: error.message }, 400);
    }
  };
}

async function main() {
  const lab = new Lab(WORKSPACE);
  await lab.ready();   // blocks the snapshot until the dataset is in memory
  http.createServer(handler(lab, true)).listen(HOOK_PORT, '0.0.0.0');
  http.createServer(handler(lab, false)).listen(PORT, '0.0.0.0');
}

main().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error) + '\n');
  process.exit(1);
});
