const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../live/static/reactor.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];

function boot(mode = 'slow') {
  let now = 0, poll, options, reloads = 0;
  const elements = Object.fromEntries(['load', 'bar', 'message', 'retry'].map(id => [id, {
    style: {}, hidden: id === 'retry', textContent: '', addEventListener(name, fn) { this[name] = fn; }
  }]));
  const events = {}, messages = [], instance = { Module: {} };
  const context = {
    document: { getElementById: id => elements[id] },
    location: { origin: 'http://scram.test', reload: () => reloads++ },
    Date: { now: () => now },
    setInterval: fn => { poll = fn; return 1; }, clearInterval: () => { poll = null; },
    window: { parent: { postMessage: value => messages.push(value) },
      addEventListener: (name, fn) => { events[name] = fn; } }
  };
  if (mode !== 'missing') context.UnityLoader = { instantiate(id, url, opts) {
    options = opts;
    if (mode === 'throw') throw new Error('download failed');
    return instance;
  }};
  vm.runInNewContext(script, context);
  return { elements, messages, events, instance, options,
    tick(ms) { now = ms; if (poll) poll(); }, reloads: () => reloads };
}

test('slow download and 100% download progress do not hide loading before runtime startup', () => {
  const app = boot();
  app.tick(21000);
  assert.notEqual(app.elements.load.style.display, 'none');
  assert.match(app.elements.message.textContent, /STILL LOADING/);
  app.options.onProgress(app.instance, 1);
  app.instance.Module.asmLibraryArg = {};
  app.tick(30000);
  assert.notEqual(app.elements.load.style.display, 'none');
  assert(!app.messages.some(m => m.status === 'ready'));
  app.instance.Module.calledRun = true;
  app.tick(31000);
  assert.equal(app.elements.load.style.display, 'none');
  assert.equal(app.messages.at(-1).status, 'ready');
});

test('never-ready runtime shows explicit timeout and working retry', () => {
  const app = boot();
  app.tick(120000);
  assert.match(app.elements.message.textContent, /timed out/);
  assert.equal(app.elements.retry.hidden, false);
  assert.equal(app.messages.at(-1).status, 'error');
  app.elements.retry.click();
  assert.equal(app.reloads(), 1);
  app.instance.Module.calledRun = true;
  app.tick(121000);
  assert.equal(app.messages.at(-1).status, 'error');
});

for (const mode of ['missing', 'throw', 'abort', 'asset', 'rejection']) {
  test(`${mode} failure stays visible and offers retry`, () => {
    const app = boot(mode);
    if (mode === 'abort') app.options.Module.onAbort();
    if (mode === 'asset') app.events.error();
    if (mode === 'rejection') app.events.unhandledrejection();
    assert.equal(app.elements.load.className, 'err');
    assert.equal(app.elements.retry.hidden, false);
    assert.equal(app.messages.at(-1).status, 'error');
  });
}
