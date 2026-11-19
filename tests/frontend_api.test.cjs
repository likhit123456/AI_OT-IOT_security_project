// Offline Node tests for the actual TypeScript API helpers; no browser/plant I/O.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

function load(name, fetch) {
  const filename = path.join(__dirname, '../frontend/src/lib', name);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const context = { exports: {}, fetch, AbortSignal, performance };
  vm.runInNewContext(code, context, { filename });
  return context.exports;
}

test('an HTTP 200 error body cannot masquerade as fresh telemetry', async () => {
  const api = load('api.ts', async () => ({ ok: true, json: async () => ({ error: 'not ready' }) }));
  await assert.rejects(api.fetchState(0, 0), /incomplete telemetry/);
});

test('bad trend rows cannot poison polling cursors', async () => {
  const api = load('api.ts', async () => ({ ok: true, json: async () => ({
    status: 'running', telemetry_stale: false, history: [['invalid', 1]], events: []
  }) }));
  await assert.rejects(api.fetchState(0, 0), /incomplete telemetry/);
});

test('startup/stale telemetry remains a valid diagnostic response', async () => {
  const value = { status: 'starting', telemetry_stale: true, history: [], events: [] };
  const api = load('api.ts', async () => ({ ok: true, json: async () => value }));
  assert.equal((await api.fetchState(0, 0)).status, 'starting');
});

test('malformed error events cannot crash the notice renderer', async () => {
  const api = load('api.ts', async () => ({ ok: true, json: async () => ({
    status: 'running', telemetry_stale: false, history: [],
    events: [{ id: 1, t: 1, kind: 'error', msg: { unexpected: 'object' } }]
  }) }));
  await assert.rejects(api.fetchState(0, 0), /incomplete telemetry/);
});

test('a successful HTTP status with a rejected action still yields a visible error', async () => {
  const api = load('api.ts', async () => ({ ok: true, status: 200, json: async () => ({ ok: false, error: '' }) }));
  const result = await api.postAction('chat');
  assert.equal(result.ok, false);
  assert(result.error.length > 0);
});

test('a proxy HTML error yields a useful action error', async () => {
  const api = load('api.ts', async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError('HTML'); } }));
  const result = await api.postAction('clear_faults');
  assert.equal(result.ok, false);
  assert.match(result.error, /HTTP 502/);
});

test('structured server errors cannot put an object into a React toast', async () => {
  const api = load('api.ts', async () => ({ ok: false, status: 500, json: async () => ({ ok: false, error: { reason: 'bad' } }) }));
  const result = await api.postAction('clear_faults');
  assert.equal(result.ok, false);
  assert.equal(typeof result.error, 'string');
});

test('incomplete action acknowledgements are never reported as accepted', async () => {
  const api = load('api.ts', async () => ({ ok: true, status: 200, json: async () => ({}) }));
  assert.equal((await api.postAction('chat')).ok, false);
});

test('telemetry expires while the network request is still pending', () => {
  const { telemetryExpired } = load('telemetryFreshness.ts');
  assert.equal(telemetryExpired(1000, 2000), false);
  assert.equal(telemetryExpired(1000, 4600), true);
  assert.equal(telemetryExpired(0, 100), true);
});

test('observer aliases are configuration, not evidence of availability', () => {
  const { observerStatus } = load('observerStatus.ts');
  const status = observerStatus({ models: { proposals: 'demo' } }, 20, false);
  assert.match(status, /AI configured; availability unverified/);
  assert.match(status, /CPU detector unavailable/);
});

test('observer status distinguishes fresh inference, failed and stale results', () => {
  const { observerStatus } = load('observerStatus.ts');
  const observer = { models: { proposals: 'demo' }, proposals: { t: 10, label: 'normal', note: 'ok' },
    telemetry_specialist: true, cpu_status: 'warming_up' };
  assert.match(observerStatus(observer, 20, false), /recent inference received/);
  assert.match(observerStatus(observer, 20, false), /warming_up/);
  assert.match(observerStatus(observer, 50, false), /availability unverified/);
  observer.proposals.note = 'unavailable';
  assert.match(observerStatus(observer, 20, false), /availability unverified/);
  observer.cpu_status = 'ok';
  assert.match(observerStatus(observer, 20, false), /CPU detector reporting/);
  assert.equal(observerStatus(observer, 20, true), 'Monitor availability unverified');
});
