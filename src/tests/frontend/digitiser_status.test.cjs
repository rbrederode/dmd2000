const test = require('node:test');
const assert = require('node:assert/strict');
const { init } = require('../../ui/frontend_prototype/digitiser_status.js');

function fixture() {
  const root = { listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; } };
  const h = { posts: [], pending: null, fail: false, html: '',
    win: { setTimeout, clearTimeout, DmdSession: { can_edit: true } },
    data: { stale: false, tm_connected: true },
    item: { key: 'dig:dig003', id: 'dig003', type: 'dig', app: { arguments: { local_host: '192.0.2.3' } },
      model: { load_active: false, gain: 17, sample_rate: 2048000, bandwidth: 2000000, center_freq: 1420000000,
        freq_correction: -140, channels: 0, scan_duration: 0, scanning: false,
        tm_connected: { value: 'ESTABLISHED' }, sdr_type: 'rtlstream' },
      scanModel: { channels: 2048, scan_duration: 60 } } };
  h.controller = init({ getElementById: () => root }, h.win, async (url, options) => {
    h.posts.push({ url, body: JSON.parse(options.body) });
    if (h.fail) throw new Error('Connection lost');
    return h.pending || { ok: true, json: async () => ({ status: 'success', message: 'Configuration scheduled' }) };
  }, () => h.refresh());
  h.refresh = () => { h.html = h.controller.view(h.item, h.data); };
  h.input = (field, value) => root.listeners.input({ target: { dataset: { digitiserField: field }, value } });
  h.send = action => h.controller.send(action);
  h.refresh();
  return h;
}

test('desired/actual tuning keeps drafts during refresh and uses reported scan settings from SDP', async () => {
  const h = fixture();
  assert.match(h.html, /Property configuration/);
  assert.match(h.html, /Driver and status/);
  assert.match(h.html, /<td>2048<\/td>/);
  h.input('gain', '22'); h.input('center_freq', '1.42E+09');
  h.item.model.gain = 18; h.refresh();
  assert.match(h.html, /data-digitiser-field="gain"[^>]*value="22"/);
  assert.match(h.html, /<td>18<\/td>/);
  await h.send('gain');
  assert.deepEqual(h.posts[0], { url: '/api/digitiser-control/dig003', body: { gain: 22 } });
  assert.match(h.html, /awaiting fresh application reports/);
  assert.equal(h.item.model.gain, 18);
  await h.send('actual');
  assert.match(h.html, /data-digitiser-field="gain"[^>]*value="18"/);
});

test('Apply validates and submits settings once; scanning has a separate request', async () => {
  const h = fixture();
  h.input('channels', '0'); await h.send('apply');
  assert.equal(h.posts.length, 0); assert.match(h.html, /Enter a valid channels/);
  h.input('channels', '4096'); h.input('load_active', 'true');
  await h.send('apply');
  assert.equal(h.posts[0].body.channels, 4096);
  assert.equal(h.posts[0].body.load_active, true);
  assert.equal(Object.hasOwn(h.posts[0].body, 'scanning'), false);
  h.input('scanning', 'true'); await h.send('scanning');
  assert.deepEqual(h.posts[1].body, { scanning: true, channels: 4096, scan_duration: 60 });
  h.input('scanning', 'false'); await h.send('scanning');
  assert.deepEqual(h.posts[2].body, { scanning: false });
});

test('pending requests block duplicates and late responses stay with the original digitiser', async () => {
  const h = fixture(); let finish;
  h.pending = new Promise(resolve => { finish = resolve; });
  const sending = h.send('gain'); await h.send('gain');
  assert.equal(h.posts.length, 1);
  const original = h.item;
  h.item = { ...h.item, key: 'dig:dig001', id: 'dig001' }; h.refresh();
  finish({ ok: true, json: async () => ({ status: 'success', message: 'Original digitiser acknowledgement' }) });
  await sending;
  assert.doesNotMatch(h.html, /Original digitiser acknowledgement/);
  h.item = original; h.refresh();
  assert.match(h.html, /Original digitiser acknowledgement/);
});

test('viewer access, stale data and lost TM connections prevent writes', async () => {
  const h = fixture();
  h.win.DmdSession.can_edit = false; h.refresh(); await h.send('gain');
  assert.equal(h.posts.length, 0);
  assert.match(h.html, /data-digitiser-action="apply" disabled/);
  h.win.DmdSession.can_edit = true; h.data.stale = true; await h.send('gain');
  h.data.stale = false; h.data.tm_connected = false; await h.send('gain');
  h.data.tm_connected = true; h.item.model.tm_connected = { value: 'NOT_ESTABLISHED' }; await h.send('gain');
  assert.equal(h.posts.length, 0);
});

test('transport failures preserve drafts and never retry or invent actual values', async () => {
  const h = fixture(); h.fail = true; h.input('gain', '25');
  await h.send('gain');
  assert.equal(h.posts.length, 1);
  assert.match(h.html, /Outcome unknown/);
  assert.match(h.html, /data-digitiser-field="gain"[^>]*value="25"/);
  assert.equal(h.item.model.gain, 17);
});

test('scanning blocks tuning controls while retaining a manual stop request', async () => {
  const h = fixture(); h.item.model.scanning = { obs_id: 'USR-current' }; h.refresh();
  await h.send('gain'); assert.equal(h.posts.length, 0);
  h.input('scanning', 'false'); await h.send('scanning');
  assert.deepEqual(h.posts[0].body, { scanning: false });
});
