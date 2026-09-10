'use strict';
// ============================================================================
// A real, persistent Node.js interpreter holding one session's state.
//
// The Node counterpart of worker.py, deliberately structured the same way so
// the two runtimes can be read side by side. Its context object survives
// suspend and resume because AWS checkpoints the VM's memory -- there is no
// save path, no serialization and no replay. When a resumed session still
// knows `balance`, it is the same process that set it.
//
// The VM is the security boundary, not this interpreter. Submitted code runs
// through vm.runInContext with full access to the context, which is acceptable
// only because the MicroVM around it is isolated and holds no AWS credentials.
// ============================================================================
const vm = require('vm');
const fs = require('fs');
const crypto = require('crypto');
const readline = require('readline');

// Total captured output per cell, matching the Python worker's cap. Keeps a
// runaway loop from returning a response too large for API Gateway.
const OUTPUT_LIMIT = 16000;

function main() {
  const started = process.hrtime.bigint();

  // Real initialization work, performed before the readiness line is printed,
  // so the image snapshot captures it and launches skip it entirely.
  const sales = [];
  for (let i = 0; i < 200000; i++) {
    sales.push([i % 12, ((i * 7919) % 10000) / 100]);
  }
  const totals = {};
  for (let m = 0; m < 12; m++) totals[m] = 0;
  for (const [m, v] of sales) totals[m] += v;

  // The persistent scope. Same role as the Python worker's namespace dict:
  // assignments made by submitted code land here and outlive the cell.
  const context = vm.createContext({ sales, totals, fs });

  const digest = crypto.createHash('sha256')
    .update(JSON.stringify(totals)).digest('hex');

  send({
    ready: true,
    rows: sales.length,
    init_ms: Number(process.hrtime.bigint() - started) / 1e6,
    dataset_sha256: digest,
    pid: process.pid,
  });

  // One JSON object per line in each direction, matching the Python worker so
  // the server implementations stay interchangeable.
  readline.createInterface({ input: process.stdin }).on('line', (line) => {
    let payload;
    try {
      payload = JSON.parse(line);
    } catch (error) {
      // Answer anyway. A silent iteration would hang the server waiting on a
      // response that never arrives.
      send({ ok: false, stdout: String(error) });
      return;
    }
    send(run(context, payload.code));
  });
}

function run(context, code) {
  let output = '';
  // Submitted code must never write to the real stdout: that is the protocol
  // channel back to the server, and a console.log would corrupt the stream.
  // Give the context its own console instead of redirecting the process.
  const capture = (...args) => {
    if (output.length < OUTPUT_LIMIT) {
      const text = args.map(a => typeof a === 'string' ? a : format(a)).join(' ');
      output += text.slice(0, OUTPUT_LIMIT - output.length) + '\n';
    }
  };
  context.console = { log: capture, error: capture, warn: capture, info: capture };

  const started = process.hrtime.bigint();
  let ok = true;
  try {
    vm.runInContext(code, context, { filename: '<cell>' });
  } catch (error) {
    ok = false;
    output += (error && error.stack ? error.stack : String(error)) + '\n';
  }
  return {
    ok,
    stdout: output,
    execution_ms: Number(process.hrtime.bigint() - started) / 1e6,
  };
}

// console.log-like rendering, so printing an object shows its contents rather
// than [object Object].
function format(value) {
  try {
    return require('util').inspect(value, { depth: 3, breakLength: 100 });
  } catch (error) {
    return String(value);
  }
}

function send(value) {
  process.stdout.write(JSON.stringify(value) + '\n');
}

main();
