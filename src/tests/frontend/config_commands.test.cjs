const test = require('node:test');
const assert = require('node:assert/strict');
const { init } = require('../../ui/frontend_prototype/config_commands.js');
class Element {
  constructor() { this.listeners = {}; this.value = ''; this.textContent = ''; this.innerHTML = ''; this.classes = new Set(); this.classList = { toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key) }; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  emit(name) { return this.listeners[name]?.(); }
}
function harness() {
  const nodes = new Map(), calls = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const h = { get: id => get('config-resync-' + id), calls, dirty: false, fail: false, pending: null };
  let peer;
  const win = { setTimeout, clearTimeout, DmdSession: { can_edit: true } };
  const admin = { register: (_, editor) => { peer = editor; }, update: () => peer.update(),
    isBusy: () => peer.isBusy(), isDirty: () => h.dirty,
    request: async (resource, options) => {
      assert.equal(resource, 'digitisers'); assert.ok(options.profile);
      return { payload: { digitisers: [{ dig_id: 'dig003' }, { dig_id: 'dig001' }] } };
    } };
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    if (h.pending) return h.pending;
    if (h.fail) throw new Error('Connection lost');
    return { ok: true, json: async () => ({ status: 'success', message: 'Application configuration resynchronised' }) };
  };
  h.win = win; h.admin = admin;
  h.controller = init({ getElementById: get }, win, admin, fetcher);
  return h;
}

test('Config RESYNC lists apps and digitisers, sends once and shows the actual response', async () => {
  const h = harness(); await h.controller.reset('local');
  assert.match(h.get('app').innerHTML, /value="tm"/);
  assert.ok(h.get('app').innerHTML.indexOf('dig001') < h.get('app').innerHTML.indexOf('dig003'));
  h.get('app').value = 'dm'; h.get('app').emit('change');
  assert.equal(h.get('button').disabled, false);
  let finish;
  h.pending = new Promise(resolve => { finish = resolve; });
  const response = h.get('button').emit('click');
  await new Promise(setImmediate);
  assert.equal(h.get('app').disabled, true);
  assert.equal(h.get('button').disabled, true);
  assert.match(h.get('result').textContent, /Sending RESYNC to dm/);
  await h.controller.send();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/commands');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), { app: 'dm', cmd: 'resync' });
  finish({ ok: true, json: async () => ({ status: 'success', message: 'Application configuration resynchronised' }) });
  await response;
  assert.ok(h.get('button').classes.has('is-success'));
  assert.match(h.get('result').textContent, /dm · .* UTC · success: Application configuration resynchronised/);
  assert.equal(h.get('button').disabled, false);
});

test('unsaved configuration and viewer permissions prevent RESYNC', async () => {
  const h = harness(); await h.controller.reset('local');
  h.get('app').value = 'dig001'; h.get('app').emit('change');
  h.dirty = true; h.admin.update();
  assert.equal(h.get('button').disabled, true);
  assert.match(h.get('result').textContent, /Save changes/);
  await h.controller.send(); assert.equal(h.calls.length, 0);
  h.dirty = false; h.win.DmdSession.can_edit = false; h.admin.update();
  assert.equal(h.get('app').disabled, true);
  await h.controller.send(); assert.equal(h.calls.length, 0);
});

test('failure responses are red and transport failures remain unknown without retry', async () => {
  const h = harness(); await h.controller.reset('local');
  h.get('app').value = 'web'; h.get('app').emit('change');
  h.pending = Promise.resolve({ ok: true, json: async () => ({ status: 'error', code: 'timeout', message: 'Timed out connecting to target application' }) });
  await h.controller.send();
  assert.ok(h.get('button').classes.has('is-error'));
  assert.match(h.get('result').textContent, /error \(timeout\): Timed out/);
  h.pending = null; h.fail = true;
  await h.controller.send();
  assert.match(h.get('result').textContent, /outcome is unknown/);
  assert.equal(h.calls.length, 2);
  h.get('app').value = 'tm'; h.get('app').emit('change');
  assert.ok(!h.get('button').classes.has('is-error'));
  assert.doesNotMatch(h.get('result').textContent, /web/);
});
