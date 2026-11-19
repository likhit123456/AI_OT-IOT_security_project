// Execute the actual reactor bootstrap with a never-ready Unity fixture.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'live/static/reactor.html'), 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
let now = 0, interval, removed = false;
const overlay = { isConnected: true, remove() { removed = true; this.isConnected = false; } };
const sandbox = {
  document: {getElementById: id => id === 'load' ? overlay : {style: {}}},
  UnityLoader: {instantiate: () => ({Module: {}})}, window: {},
  Date: {now: () => now}, setInterval: fn => { interval = fn; return 1; },
  clearInterval() {}, setTimeout: fn => fn()
};
vm.runInNewContext(source, sandbox);
now = 21000;
interval();
const result = {fixture: 'Unity loader present, engine never became ready',
  elapsed_ms: now, calledRun: Boolean(sandbox.window.unityInstance.Module.calledRun),
  overlay_removed: removed, real_browser: false, simulator_requests: false};
fs.writeFileSync(path.join(root, 'artifacts/project-evaluation-3d-20261118.json'), JSON.stringify(result, null, 2));
console.log(result);
