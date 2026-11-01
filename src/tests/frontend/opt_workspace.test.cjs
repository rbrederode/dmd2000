// Logic-level UI checks; these use a tiny DOM stub, not a browser renderer.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../ui/frontend_prototype/opt.js'), 'utf8');

async function workspace() {
  const elements = new Map();
  const timers = new Map();
  let nextTimer = 0;
  let online = true;
  const calls = [];
  class Element {
    constructor() {
      this.value = ''; this.disabled = false; this.hidden = false;
      this.textContent = ''; this.innerHTML = ''; this.listeners = {};
      this.classes = new Set(); this.children = new Map();
      this.classList = { toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name) };
    }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    querySelectorAll() { return []; }
    querySelector(selector) { if (!this.children.has(selector)) this.children.set(selector, new Element()); return this.children.get(selector); }
    replaceChildren(...children) { this.nodes = children; }
    setAttribute(name, value) { this[name] = value; }
    click() {}
  }
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  for (const [name, value] of Object.entries({ baseline: 'linear', vmin: '-150', vmax: '150', smoothing: '2', kind: 'all', profile: 'default' })) get(`opt-${name}`).value = value;
  const entries = [
    { id: 'obs-new', name: 'Galactic plane', dish: 'dish003', completed_scans: 3, total_scans: 4, completed_types: { SKY: 2, LOAD: 1 }, completed_integration_s: 135 },
    { id: 'obs-old', name: 'Earlier observation', dish: 'dish002', completed_scans: 1, total_scans: 2, completed_types: { SKY: 1 }, completed_integration_s: 60 },
  ];
  const context = vm.createContext({
    AbortController, console,
    document: { getElementById: get, querySelectorAll: () => [], querySelector: () => new Element() },
    window: { location: { hash: '' }, setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); } },
    localStorage: { getItem: () => null, setItem() {} },
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    fetch: async (url, options = {}) => {
      if (!online) throw new TypeError('Network disconnected');
      const body = options.body ? JSON.parse(options.body) : null;
      calls.push({ url, body });
      let data;
      if (url.endsWith('/config')) data = { roots: ['/samples'], directories: ['/samples'], profiles: ['default'] };
      else if (url.includes('/observations?')) data = { directory: '/samples', observations: entries.map((item) => item.id), entries };
      else if (url.endsWith('/health')) data = { status: 'ok' };
      else if (url.endsWith('/open')) data = { id: body.observation, directory: body.directory, scans: [] };
      else throw new Error(`Unexpected API request: ${url}`);
      return { ok: true, status: 200, json: async () => data };
    },
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  return {
    element: (name) => get(`opt-${name}`), calls,
    setOnline: (value) => { online = value; },
    async heartbeat() {
      const entry = [...timers].find(([, timer]) => timer.ms === 5000);
      assert.ok(entry, 'heartbeat is scheduled'); timers.delete(entry[0]);
      await entry[1].fn(); await new Promise(setImmediate);
    },
  };
}

test('catalogue presents metadata and opens only the selected observation', async () => {
  const app = await workspace();
  const listing = app.element('observation-rows').innerHTML;
  assert.match(listing, /Galactic plane/);
  assert.match(listing, /dish003/);
  assert.match(listing, /3 \/ 4/);
  assert.match(listing, /SKY 2 · LOAD 1/);
  assert.match(listing, /2m 15s/);
  assert.equal(app.element('observation').value, 'obs-new');
  await app.element('observation-rows').listeners.click[0]({ target: { closest: () => ({ dataset: { optObservation: 'obs-old' } }) } });
  assert.equal(app.element('observation').value, 'obs-old');
  assert.equal(app.element('observation-choice').textContent, 'Earlier observation');
  assert.equal(app.calls.filter((call) => call.url.endsWith('/open')).length, 0);
  await app.element('open').listeners.click[0]();
  assert.equal(app.calls.find((call) => call.url.endsWith('/open')).body.observation, 'obs-old');
  assert.equal(app.element('observation-browser').open, false);
});

test('connection badge turns red when offline and recovers on heartbeat', async () => {
  const app = await workspace();
  const badge = app.element('connection');
  assert.equal(badge.textContent, 'Local OPT connected');
  assert.ok(badge.classes.has('is-live'));
  app.setOnline(false);
  await app.heartbeat();
  assert.equal(badge.textContent, 'OPT Disconnected');
  assert.ok(badge.classes.has('is-disconnected'));
  assert.ok(!badge.classes.has('is-live'));
  assert.equal(app.element('open').disabled, true);
  app.setOnline(true);
  await app.heartbeat();
  assert.equal(badge.textContent, 'Local OPT connected');
  assert.ok(!badge.classes.has('is-disconnected'));
  assert.equal(app.element('open').disabled, false);
});
