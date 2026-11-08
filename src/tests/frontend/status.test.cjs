const test = require('node:test');
const assert = require('node:assert/strict');
const { init: statusInit, applications, detail, connections, percent, lastUpdate } = require('../../ui/frontend_prototype/status.js');
// Existing status scenarios use an empty command catalogue; command scenarios
// below exercise the real GET/POST interaction explicitly.
function init(doc, win, fetcher) {
  return statusInit(doc, win, (url, options) => url.startsWith('/api/commands?')
    ? Promise.resolve({ ok: true, json: async () => ({ status: 'success', payload: { commands: [] } }) })
    : fetcher(url, options));
}
const en = (value) => ({ _type: 'enum.IntEnum', value });
function snapshot() {
  const app = { health: en('OK'), app_running: true, version: 'v1.1.0-test',
    app_tracing: false, app_debug: true, availability: 0, reliability: { reliability: 0 },
    num_processors: 2, queue_size: 0, interfaces: ['dm', 'web'],
    last_update: { _type: 'datetime', value: '2026-11-06T08:20:21Z' },
    arguments: { profile: 'local', dm_host: '127.0.0.1', dm_port: 50002 },
    processors: [{ name: 'tm-Processor-1', current_event: '<unsafe event>', processing_time_ms: 0 }],
    last_err_dt: { value: '2026-11-06T08:19:00Z' }, last_err_msg: '<script>bad()</script>' };
  return { profile: 'local', stale: false, age_seconds: 2, snapshot_at: '2026-11-06T08:20:22Z', telescope_model: {
    tel_mgr: { id: 'tm001', app, dm_connected: en('ESTABLISHED'), allocations: { alloc_list: [] } },
    dsh_mgr: { id: 'dm001', app: { ...app, app_debug: false }, dish_store: { dish_list: [] } },
    web_svr: { id: 'web001', app }, sdp: { sdp_id: 'sdp001', app, scans_completed: 0 },
    wtr_stn: { id: 'ws001', app },
    dig_store: { dig_list: [{ dig_id: 'dig003', app: { ...app, app_running: false }, scanning: false },
      { dig_id: 'dig001', app, scanning: false, sdr_type: 'rtlstream' }] },
  } };
}
class Element {
  constructor() { this.listeners = {}; this.textContent = ''; this.innerHTML = ''; this.hidden = false; this.classes = new Set(); this.classList = { toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key), contains: (key) => this.classes.has(key) }; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  async emit(name, event = {}) { return this.listeners[name]?.({ preventDefault() {}, ...event }); }
  querySelectorAll() { return []; }
}

test('model adapter includes every digitiser and WEB and retains zero/false data', () => {
  const items = applications(snapshot().telescope_model);
  assert.deepEqual(items.filter((item) => item.type === 'dig').map((item) => item.id), ['dig001', 'dig003']);
  assert.equal(items.find((item) => item.id === 'dig003').state, 'STOPPED');
  assert.ok(items.some((item) => item.id === 'web001'));
  const tm = items.find((item) => item.type === 'tm');
  assert.deepEqual(connections(tm)[0], { peer: 'DM', state: 'ESTABLISHED', endpoint: '127.0.0.1:50002' });
  const html = detail(tm);
  assert.doesNotMatch(html, /v1.1.0-test|<dt>Version<\/dt>/);
  assert.equal(tm.app.availability, 0);
  assert.equal(tm.app.app_tracing, false);
  assert.equal(tm.app.app_debug, true);
  assert.doesNotMatch(html, /<dt>(ID|Health|State|Last update|Availability|Reliability|Tracing|Debug)<\/dt>/);
  assert.match(html, /^<h3>Last error/);
  assert.match(html, /Last error<span class="status-last-error-time">2026-11-06 08:19:00 UTC<\/span><\/h3>/);
  assert.match(html, /class="status-last-error">&lt;script&gt;bad\(\)/);
  for (const label of ['Interfaces', 'Command endpoint']) assert.ok(html.includes('<dt>' + label + '</dt>'));
  assert.match(html, /id="status-processor-table" class="status-processors" hidden/);
  assert.match(html, /tm-Processor-1/); assert.match(html, /&lt;unsafe event&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal(percent(null), '—');
});

test('selectable application pane refreshes in place and marks retained data stale on outages', async () => {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const doc = { getElementById: get, querySelector: (key) => get(key) };
  let payload = snapshot(), online = true, calls = 0;
  const intervals = [];
  const win = { setTimeout, clearTimeout, setInterval(fn, ms) { intervals.push({ fn, ms }); return 1; }, clearInterval() {}, addEventListener() {} };
  const controller = init(doc, win, async (url) => {
    assert.equal(url, '/api/web/telescope/model'); calls++;
    if (!online) throw new Error('Disconnected');
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  assert.equal(intervals[0].ms, 5000);
  assert.equal(get('status-detail').hidden, true);
  assert.match(get('status-apps').innerHTML, /<td>Telescope Manager<\/td><td>tm001<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>Science Data Processor<\/td><td>sdp001<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>Digitiser<\/td><td>dig003<\/td>/);
  const selected = { target: { closest: () => ({ dataset: { appKey: 'tm:tm001' } }) } };
  await get('status-apps').emit('click', selected);
  assert.match(get('status-detail-title').textContent, /Telescope Manager/);
  assert.equal(get('status-detail').hidden, false);
  assert.equal(calls, 1); // Clicking apps uses the cached response already loaded.
  payload = snapshot(); payload.telescope_model.tel_mgr.app.queue_size = 8;
  await controller.refresh();
  assert.equal(controller.state.selected, 'tm:tm001');
  assert.match(get('status-detail-content').innerHTML, /Queue size<\/span><strong>8<\/strong>/);
  online = false; await controller.refresh();
  assert.equal(controller.state.data.stale, true);
  assert.match(get('status-detail-subtitle').textContent, /Stale/);
  assert.match(get('status-connection').textContent, /unavailable/);
  online = true; payload = snapshot(); await controller.refresh();
  assert.equal(controller.state.data.stale, false);
  await get('status-apps').emit('keydown', { ...selected, key: 'Enter' });
  await get('status-detail-close').emit('click');
  assert.equal(get('status-detail').hidden, true);
});

test('timestamps become old after two minutes and ignore missing or future dates', () => {
  const now = Date.parse('2026-11-06T08:22:21Z');
  const item = applications(snapshot().telescope_model)[0];
  assert.doesNotMatch(lastUpdate(item.updated, now), /status-update is-old/); // Exactly two minutes.
  assert.match(lastUpdate(item.updated, now + 1), /status-update is-old/);
  item.updated = { value: '2026-11-06T08:25:00Z' };
  assert.doesNotMatch(lastUpdate(item.updated, now), /status-update is-old/);
  for (const value of [null, '', 'invalid date']) {
    item.updated = value;
    assert.doesNotMatch(lastUpdate(item.updated, now), /status-update is-old/);
  }
});

test('timestamp highlights age on refresh and clear on a new application report', async (t) => {
  let now = Date.parse('2026-11-06T08:22:21Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.telescope_model.tel_mgr.app.health = en('FAILED');
  const doc = { getElementById: get, querySelector: get };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} };
  const controller = init(doc, win, async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  assert.match(get('status-apps').innerHTML, /zk-chip is-bad">FAILED/);
  assert.doesNotMatch(get('status-apps').innerHTML, /status-update is-old/);
  await get('status-apps').emit('click', { target: { closest: () => ({ dataset: { appKey: 'tm:tm001' } }) } });
  now += 1;
  await controller.refresh();
  assert.match(get('status-apps').innerHTML, /status-update is-old/);
  assert.doesNotMatch(get('status-detail-content').innerHTML, /status-update/);
  assert.equal(get('status-detail-subtitle').hidden, true);
  payload.telescope_model.tel_mgr.app.last_update = { value: new Date(now).toISOString() };
  await controller.refresh();
  assert.doesNotMatch(get('status-detail-content').innerHTML, /status-update is-old/);
  assert.equal(get('status-detail-subtitle').hidden, true);
});

test('execution widget lists simultaneous active observations and tracks their transitions', async () => {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const doc = { getElementById: get, querySelector: get };
  const payload = snapshot();
  payload.telescope_model.oda = { obs_store: { obs_list: [
    { obs_id: '<obs3>', dsh_id: '<dish3>', obs_state: en('SCANNING') },
    { obs_id: 'obs1', dsh_id: 'dish1', obs_state: 'CONFIGURING' },
    { obs_id: 'obs2', dsh_id: 'dish2', obs_state: en('READY') },
    ...['IDLE', 'EMPTY', 'ABORTED', 'FAULT'].map((state) => ({ obs_id: 'excluded-' + state, obs_state: en(state) })),
  ] } };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} };
  const controller = init(doc, win, async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  assert.equal(get('[data-status-value="executions.count"]').textContent, '3 in progress');
  const html = get('status-executions').innerHTML;
  for (const text of ['obs1', 'obs2', '&lt;obs3&gt;', '&lt;dish3&gt;', 'SCANNING', 'CONFIGURING', 'READY']) assert.ok(html.includes(text));
  assert.doesNotMatch(html, /excluded-|<obs3>|<dish3>/);
  for (const obs of payload.telescope_model.oda.obs_store.obs_list) obs.obs_state = en('IDLE');
  await controller.refresh();
  assert.equal(get('[data-status-value="executions.count"]').textContent, '0 in progress');
  assert.match(get('status-executions').innerHTML, /No observations in progress/);
  delete payload.telescope_model.oda;
  await controller.refresh();
  assert.equal(get('[data-status-value="executions.count"]').textContent, '—');
  assert.match(get('status-executions').innerHTML, /unavailable/);
});

test('background refresh preserves reading position while restoring keyboard focus', async () => {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let scrollY = 1600, explicitScrolls = 0;
  const focusCalls = [];
  const row = { dataset: { appKey: 'tm:tm001' }, focus(options) {
    focusCalls.push(options);
    if (!options?.preventScroll) scrollY = 0;
  } };
  const doc = { getElementById: get, querySelector: get, activeElement: { closest: () => row } };
  get('status-apps').querySelectorAll = () => [row];
  get('status-detail').scrollIntoView = () => { explicitScrolls++; };
  const payload = snapshot();
  let online = true;
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} };
  const controller = init(doc, win, async () => {
    if (!online) throw new Error('Disconnected');
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  await get('status-apps').emit('keydown', { key: 'Enter', target: { closest: () => row } });
  assert.equal(explicitScrolls, 1); // Navigation is only requested by selecting an app.
  payload.telescope_model.tel_mgr.app.queue_size = 17;
  payload.telescope_model.tel_mgr.app.health = en('DEGRADED');
  await controller.refresh();
  assert.match(get('status-detail-content').innerHTML, /Queue size<\/span><strong>17<\/strong>/);
  assert.equal(scrollY, 1600);
  assert.ok(focusCalls.length >= 2);
  assert.ok(focusCalls.every(options => options.preventScroll === true));
  const count = focusCalls.length;
  await controller.refresh();
  assert.equal(focusCalls.length, count); // Unchanged cached rows keep their DOM/focus.
  online = false;
  await controller.refresh();
  assert.equal(scrollY, 1600);
  assert.equal(explicitScrolls, 1);
  assert.equal(controller.state.selected, 'tm:tm001');
});

test('TM details show every resource allocation with state and UTC expiry', () => {
  const item = applications(snapshot().telescope_model)[0];
  assert.match(detail(item), /No resource allocations/);
  item.model.allocations.alloc_list = [
    { resource_type: 'dish', resource_id: 'dish001', allocated_type: 'observation', allocated_id: '<obs1>',
      state: en('ACTIVE'), expires: { _type: 'datetime', value: '2026-11-06T18:00:00+08:00' } },
    { resource_type: 'digitiser', resource_id: 'dig001', allocated_type: 'observation', allocated_id: 'obs2',
      state: en('REQUESTED'), expires: null },
    { resource_type: 'dish', resource_id: 'dish002', allocated_type: 'observation', allocated_id: 'obs3',
      state: 'RELEASED', expires: '2026-11-06T09:00:00Z' },
  ];
  const html = detail(item);
  assert.match(html, /Resource Allocations/);
  for (const value of ['[0]', '[1]', '[2]', 'dish001', 'dig001', 'dish002', '&lt;obs1&gt;', 'obs2', 'obs3', '2026-11-06 10:00:00 UTC', 'No expiry']) assert.ok(html.includes(value));
  assert.match(html, /is-live">ACTIVE/);
  assert.match(html, /is-warn">REQUESTED/);
  assert.match(html, /is-empty">RELEASED/);
  assert.doesNotMatch(html, /<obs1>/);
  delete item.model.allocations;
  assert.match(detail(item), /Resource allocations unavailable/);
});

test('Tracing and Debug links open file panes instead of selecting application details', async () => {
  const elements = new Map(), opened = [];
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let closes = 0;
  const doc = { getElementById: get, querySelector: get };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {},
    DmdDiagnostics: { init: () => ({ open: (app, kind) => opened.push([app.id, kind]), closeAll: () => { closes++; } }) } };
  const controller = init(doc, win, async () => ({ ok: true, json: async () => ({ status: 'success', payload: snapshot() }) }));
  await new Promise(setImmediate);
  assert.match(get('status-apps').innerHTML, /data-status-file="trace"[^>]*>View<\/a>/);
  assert.match(get('status-apps').innerHTML, /data-status-file="log"[^>]*>View<\/a>/);
  const row = { dataset: { appKey: 'tm:tm001' } };
  await get('status-apps').emit('click', { target: { closest: () => row } });
  assert.equal(controller.state.selected, 'tm:tm001');
  for (const kind of ['trace', 'log']) {
    const target = { closest: (selector) => selector === '[data-app-key]' ? row : { dataset: { statusFile: kind } } };
    await get('status-apps').emit('click', { target });
    assert.equal(controller.state.selected, null);
    assert.equal(get('status-detail').hidden, true);
  }
  assert.deepEqual(opened, [['tm001', 'trace'], ['tm001', 'log']]);
  const count = opened.length;
  await controller.refresh();
  assert.equal(opened.length, count);
  assert.equal(closes, 1);
});

test('tracing toggle sends once and holds confirmed state until the next application report', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:20:30Z'));
  const elements = new Map(), calls = [], payload = snapshot();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let finish;
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: true } };
  const controller = init({ getElementById: get, querySelector: get }, win, async (url, options) => {
    calls.push({ url, options });
    if (url === '/api/commands') return new Promise(resolve => { finish = resolve; });
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  assert.doesNotMatch(get('status-apps').innerHTML, /<select|Submit|RESYNC/);
  assert.match(get('status-apps').innerHTML, /role="switch" aria-checked="false" data-status-toggle="trace"/);
  assert.equal(calls.filter(c => c.url === '/api/commands').length, 0);
  const row = { dataset: { appKey: 'tm:tm001' } };
  const target = { closest: selector => selector === '[data-app-key]' ? row : { dataset: { statusToggle: 'trace' } } };
  const pending = get('status-apps').emit('click', { target });
  await new Promise(setImmediate);
  assert.equal(get('status-command-status').textContent, 'pending');
  await controller.refresh();
  await get('status-apps').emit('click', { target });
  let sent = calls.filter(c => c.url === '/api/commands');
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(sent[0].options.body), { app: 'tm', cmd: 'trace', value: 'ON' });
  finish({ ok: true, json: async () => ({ status: 'success', message: 'Tracing set to ON' }) });
  await pending;
  assert.equal(get('status-command-status').textContent, 'success');
  assert.match(get('status-command-time').textContent, /UTC/);
  await controller.refresh();
  assert.match(get('status-apps').innerHTML, /aria-checked="true" data-status-toggle="trace"/);
  const second = get('status-apps').emit('click', { target });
  await new Promise(setImmediate);
  sent = calls.filter(c => c.url === '/api/commands');
  assert.equal(JSON.parse(sent[1].options.body).value, 'OFF');
  finish({ ok: true, json: async () => ({ status: 'error', message: 'Command failed' }) });
  await second;
  assert.match(get('status-apps').innerHTML, /aria-checked="true" data-status-toggle="trace"/);
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:21:21Z';
  payload.snapshot_at = '2026-11-06T08:21:22Z';
  await controller.refresh();
  assert.match(get('status-apps').innerHTML, /aria-checked="false" data-status-toggle="trace"/);
});

test('viewer toggle controls stay disabled and cannot issue a command', async () => {
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: false } };
  init({ getElementById: get, querySelector: get }, win, async url => {
    assert.equal(url, '/api/web/telescope/model');
    return { ok: true, json: async () => ({ status: 'success', payload: snapshot() }) };
  });
  await new Promise(setImmediate);
  assert.match(get('status-apps').innerHTML, /data-status-toggle="trace"[^>]*disabled/);
  const row = { dataset: { appKey: 'tm:tm001' } };
  await get('status-apps').emit('click', { target: { closest: selector => selector === '[data-app-key]' ? row : { dataset: { statusToggle: 'debug' } } } });
});

test('debug toggle targets an individual digitiser and sends OFF when debug is enabled', async () => {
  const elements = new Map(), sent = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: true } };
  init({ getElementById: get, querySelector: get }, win, async (url, options) => {
    if (url === '/api/commands') {
      sent.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ status: 'success', message: 'Debug set to OFF' }) };
    }
    return { ok: true, json: async () => ({ status: 'success', payload: snapshot() }) };
  });
  await new Promise(setImmediate);
  const row = { dataset: { appKey: 'dig:dig003' } };
  await get('status-apps').emit('click', { target: { closest: selector => selector === '[data-app-key]' ? row : { dataset: { statusToggle: 'debug' } } } });
  assert.deepEqual(sent, [{ app: 'dig003', cmd: 'debug', value: 'OFF' }]);
  assert.equal(get('status-command-message').textContent, 'Debug set to OFF');
});

test('pre-command application reports cannot undo confirmed trace/debug settings', async (t) => {
  let now = Date.parse('2026-11-06T08:20:30Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map(), payload = snapshot(), sent = [];
  payload.telescope_model.tel_mgr.app.app_debug = false;
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: true } };
  const controller = init({ getElementById: get, querySelector: get }, win, async (url, options) => {
    if (url === '/api/commands') {
      sent.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ status: 'success', message: 'Setting applied' }) };
    }
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  const row = { dataset: { appKey: 'tm:tm001' } };
  for (const command of ['trace', 'debug']) await get('status-apps').emit('click', { target: {
    closest: selector => selector === '[data-app-key]' ? row : { dataset: { statusToggle: command } } } });
  // TM refreshes its model, but this application's report predates both commands.
  payload.snapshot_at = '2026-11-06T08:20:35Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:20:25Z';
  await controller.refresh();
  for (const command of ['trace', 'debug']) assert.match(get('status-apps').innerHTML.match(/<tr data-app-key="tm:tm001"[\s\S]*?<\/tr>/)[0],
    new RegExp('aria-checked="true" data-status-toggle="' + command + '"'));
  // An in-flight older snapshot must be ignored too, even if its timestamp differs.
  payload.snapshot_at = '2026-11-06T08:20:20Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:20:19Z';
  await controller.refresh();
  assert.match(get('status-apps').innerHTML, /aria-checked="true" data-status-toggle="trace"/);
  // Once a post-command report arrives, normal model-driven updates take over.
  payload.snapshot_at = '2026-11-06T08:21:00Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:20:59Z';
  payload.telescope_model.tel_mgr.app.app_tracing = true;
  payload.telescope_model.tel_mgr.app.app_debug = true;
  await controller.refresh();
  payload.snapshot_at = '2026-11-06T08:21:30Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:21:29Z';
  payload.telescope_model.tel_mgr.app.app_tracing = false;
  await controller.refresh();
  assert.match(get('status-apps').innerHTML, /aria-checked="false" data-status-toggle="trace"/);
  assert.equal(sent.length, 2);
});

test('rapid toggles do not lose their confirmation when old cached values happen to match', async (t) => {
  let now = Date.parse('2026-11-06T08:20:30Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map(), payload = snapshot(), sent = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const win = { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: true } };
  const controller = init({ getElementById: get, querySelector: get }, win, async (url, options) => {
    if (url === '/api/commands') {
      sent.push(JSON.parse(options.body).value);
      return { ok: true, json: async () => ({ status: 'success', message: 'Applied' }) };
    }
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  const row = { dataset: { appKey: 'tm:tm001' } };
  const event = { target: { closest: selector => selector === '[data-app-key]' ? row : { dataset: { statusToggle: 'trace' } } } };
  await get('status-apps').emit('click', event); // ON
  now += 1000;
  await get('status-apps').emit('click', event); // OFF, matching the old cached value.
  payload.snapshot_at = '2026-11-06T08:20:32Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:20:30.500Z';
  payload.telescope_model.tel_mgr.app.app_tracing = true;
  await controller.refresh();
  const tmRow = () => get('status-apps').innerHTML.match(/<tr data-app-key="tm:tm001"[\s\S]*?<\/tr>/)[0];
  assert.match(tmRow(), /aria-checked="false" data-status-toggle="trace"/);
  now += 2000;
  await get('status-apps').emit('click', event); // ON, matching an older ON report.
  payload.snapshot_at = '2026-11-06T08:20:34Z';
  payload.telescope_model.tel_mgr.app.last_update.value = '2026-11-06T08:20:31.500Z';
  payload.telescope_model.tel_mgr.app.app_tracing = false;
  await controller.refresh();
  assert.match(tmRow(), /aria-checked="true" data-status-toggle="trace"/);
  assert.deepEqual(sent, ['ON', 'OFF', 'ON']);
});


test('queue and processor controls toggle one table and preserve expansion through model refreshes', async () => {
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const doc = { getElementById: get, querySelector: key => get(key) };
  let payload = snapshot();
  const win = { setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, addEventListener() {} };
  const controller = init(doc, win, async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const selectApp = key => get('status-apps').emit('click', { target: { closest: selector => selector === '[data-app-key]' ? { dataset: { appKey: key } } : null } });
  const toggle = key => get('status-detail-content').emit('click', { target: { closest: selector => selector === '[data-status-processors]' ? { dataset: { statusProcessors: key } } : null } });
  await selectApp('tm:tm001');
  assert.match(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
  assert.match(get('status-detail-content').innerHTML, /Processors<\/span><strong>2<\/strong>/);
  await toggle('queue');
  assert.doesNotMatch(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
  assert.match(get('status-detail-content').innerHTML, /aria-expanded="true"/);
  payload = snapshot(); payload.telescope_model.tel_mgr.app.queue_size = 42;
  payload.telescope_model.tel_mgr.app.processors[0].current_event = 'New event';
  await controller.refresh();
  assert.doesNotMatch(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
  assert.match(get('status-detail-content').innerHTML, /Queue size<\/span><strong>42<\/strong>/);
  assert.match(get('status-detail-content').innerHTML, /New event/);
  await selectApp('dm:dm001');
  assert.match(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
  await selectApp('tm:tm001');
  assert.doesNotMatch(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
  await toggle('count');
  assert.match(get('status-detail-content').innerHTML, /class="status-processors" hidden/);
});


test('App and Entity Connections separate sorted digitiser states for TM and SDP', () => {
  const model = snapshot().telescope_model;
  model.tel_mgr.dig_connected = en('ESTABLISHED');
  model.dig_store.dig_list[0].tm_connected = en('NOT_ESTABLISHED');
  model.dig_store.dig_list[0].app = { ...model.dig_store.dig_list[0].app, arguments: { local_host: '192.168.50.2', port: null } };
  model.dig_store.dig_list[1].tm_connected = en('ESTABLISHED');
  model.sdp.dig_connected = en('ESTABLISHED');
  model.sdp.tm_connected = en('ESTABLISHED');
  model.sdp.dig_store = { dig_list: [{ dig_id: 'dig003', sdp_connected: en('DISABLED') }] };
  model.dig_store.dig_list[1].sdr_connected = en('NOT_ESTABLISHED');
  model.dig_store.dig_list[1].tm_connected = en('ESTABLISHED');
  let apps = applications(model), tm = apps.find(app => app.type === 'tm');
  assert.deepEqual(connections(tm).map(link => link.peer), ['DM']);
  assert.deepEqual(tm.entityLinks, [{ peer: 'dig001', state: 'ESTABLISHED', host: null, port: null }, { peer: 'dig003', state: 'NOT_ESTABLISHED', host: '192.168.50.2', port: null }]);
  const html = detail(tm);
  const left = html.slice(html.indexOf('class="status-app-connections"'), html.indexOf('class="status-entity-connections"'));
  const right = html.slice(html.indexOf('class="status-entity-connections"'), html.indexOf('id="status-processor-table"'));
  assert.match(left, /App Connections/); assert.doesNotMatch(left, /<td>DIG<\/td>|dig001|dig003/);
  assert.match(right, /Entity Connections/); assert.match(right, /dig001/); assert.match(right, /NOT_ESTABLISHED/); assert.match(right, /Configured host/); assert.match(right, /192\.168\.50\.2/);
  const sdp = apps.find(app => app.type === 'sdp');
  assert.deepEqual(connections(sdp).map(link => link.peer), ['TM']);
  assert.deepEqual(sdp.entityLinks, [{ peer: 'dig003', state: 'DISABLED', host: null, port: null }]);
  const dig = apps.find(app => app.id === 'dig001');
  assert.deepEqual(dig.entityLinks, [{ peer: 'SDR', state: 'NOT_ESTABLISHED', host: null, port: null }]);
  assert.doesNotMatch(JSON.stringify(connections(dig)), /SDR/);
  model.dig_store.dig_list[0].tm_connected = en('ESTABLISHED');
  apps = applications(model); assert.equal(apps.find(app => app.type === 'tm').entityLinks[1].state, 'ESTABLISHED');
  delete model.dig_store.dig_list[0].tm_connected;
  model.dig_store.dig_list[0].dig_id = '<unsafe>';
  tm = applications(model).find(app => app.type === 'tm');
  assert.match(detail(tm), /&lt;unsafe&gt;/); assert.doesNotMatch(detail(tm), /<unsafe>/);
  assert.ok(tm.entityLinks.some(link => link.state === 'UNKNOWN'));
});


test('entity port is the configured TM/SDP server port, not the digitiser command or source port', () => {
  const model = snapshot().telescope_model;
  model.tel_mgr.app.arguments.dig_port = 50000;
  model.dig_store.dig_list[1].app = { ...model.dig_store.dig_list[1].app,
    app_cmd_port: 60006, arguments: { local_host: '192.168.50.2', tm_port: 55000 } };
  model.sdp.dig_store = { dig_list: [{ dig_id: 'dig001', app: { arguments: { sdp_port: 60000 } } }] };
  model.dig_store.dig_list[1].sdr_connected = en('ESTABLISHED');
  model.dig_store.dig_list[1].sdr_config = { host: '192.168.50.3', port: 1234 };
  const apps = applications(model), tm = apps.find(app => app.type === 'tm');
  assert.equal(tm.entityLinks[0].port, 55000); // Digitiser's reported connection arguments.
  assert.equal(tm.entityLinks[1].port, 50000); // TM's configured listener when digitiser has no report.
  assert.equal(apps.find(app => app.type === 'sdp').entityLinks[0].port, 60000);
  assert.equal(apps.find(app => app.id === 'dig001').entityLinks[0].port, 1234);
  const html = detail(tm).split('class="status-entity-connections"')[1];
  assert.match(html, /Server port/); assert.match(html, /192\.168\.50\.2<\/td><td>55000/);
  assert.doesNotMatch(html, /60006/);
});

test('connection tables show the selected app TCP role, including disconnected entities', () => {
  const model = snapshot().telescope_model;
  model.tel_mgr.app = { ...model.tel_mgr.app, interface_roles: { dm: 'CLIENT', web: 'CLIENT', dig: 'SERVER' } };
  model.tel_mgr.web_connected = en('NOT_ESTABLISHED');
  model.dig_store.dig_list[0].tm_connected = en('NOT_ESTABLISHED');
  let html = detail(applications(model).find(app => app.type === 'tm'));
  assert.match(html, /Local role/);
  assert.match(html, /<td>DM<\/td><td>Client - Persistent<\/td>/);
  assert.match(html, /<td>WEB<\/td><td>Client - Persistent<\/td>/);
  assert.match(html, /<td>dig003<\/td><td>Server<\/td><td><span[^>]*>NOT_ESTABLISHED/);
  model.web_svr.app = { ...model.web_svr.app, interface_roles: { tm: 'SERVER' } };
  model.web_svr.tm_connected = en('ESTABLISHED');
  html = detail(applications(model).find(app => app.type === 'web'));
  assert.match(html, /<td>TM<\/td><td>Server<\/td>/);
  model.sdp.app = { ...model.sdp.app, interface_roles: { tm: 'SERVER', dig: 'SERVER' } };
  model.sdp.tm_connected = en('ESTABLISHED');
  model.sdp.dig_store = { dig_list: [{ dig_id: 'dig001' }] };
  html = detail(applications(model).find(app => app.type === 'sdp'));
  assert.match(html, /<td>TM<\/td><td>Server<\/td>/);
  assert.match(html, /<td>dig001<\/td><td>Server<\/td>/);
  const dig = model.dig_store.dig_list[1];
  dig.app = { ...dig.app, interface_roles: { tm: 'CLIENT', sdp: 'CLIENT' } };
  dig.tm_connected = en('ESTABLISHED'); dig.sdp_connected = en('NOT_ESTABLISHED');
  dig.sdr_connected = en('ESTABLISHED');
  html = detail(applications(model).find(app => app.id === 'dig001'));
  assert.match(html, /<td>TM<\/td><td>Client - Persistent<\/td>/);
  assert.match(html, /<td>SDP<\/td><td>Client - Persistent<\/td>/);
  assert.match(html, /<td>SDR<\/td><td>—<\/td>/);
  delete model.tel_mgr.app.interface_roles;
  html = detail(applications(model).find(app => app.type === 'tm'));
  assert.match(html, /<td>DM<\/td><td>Unknown<\/td>/);
});

function commandHarness(readOnly = false) {
  const elements = new Map(), posts = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const win = { setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, addEventListener() {}, DmdSession: { can_edit: !readOnly } };
  const h = { get, posts, win, pending: null, fail: false };
  h.controller = statusInit({ getElementById: get, querySelector: () => null }, win, async (url, options) => {
    if (url.includes('/telescope/model')) return { ok: true, json: async () => ({ status: 'success', payload: snapshot() }) };
    if (url.startsWith('/api/commands?')) return { ok: true, json: async () => ({ status: 'success', payload: { commands: [
      { id: 'resync', cmd: 'resync', label: 'RESYNC', description: 'Reload application configuration' },
      { id: 'trace-get', cmd: 'trace', label: 'TRACE ?' },
      { id: 'trace-on', cmd: 'trace', label: 'TRACE ON', value: 'ON' },
      ...(url.endsWith('dm') ? [{ id: 'stop', cmd: 'stop', label: 'STOP', takes_value: true, value_required: true, description: 'Stop dish movement' }] : []),
    ] } }) };
    assert.equal(url, '/api/commands'); posts.push(JSON.parse(options.body));
    if (h.fail) throw new Error('Connection lost');
    return h.pending || { ok: true, json: async () => ({ status: 'success', message: '<actual reply>' }) };
  });
  h.selectApp = async key => {
    await get('status-apps').emit('click', { target: { closest: selector => selector === '[data-app-key]' ? { dataset: { appKey: key } } : null } });
    await new Promise(setImmediate);
  };
  h.choose = value => get('status-detail-content').emit('change', { target: { dataset: { statusCommand: 'select' }, value } });
  h.value = value => get('status-detail-content').emit('input', { target: { dataset: { statusCommand: 'value' }, value } });
  h.send = () => get('status-detail-content').emit('click', { target: { closest: selector => selector === '[data-status-command]' ? { dataset: { statusCommand: 'send' } } : null } });
  h.html = () => get('status-detail-content').innerHTML;
  return h;
}

test('app detail commands target the selected digitiser, block duplicates and retain each app response', async () => {
  const h = commandHarness(); await new Promise(setImmediate);
  await h.selectApp('dig:dig003'); await h.choose('resync');
  let finish;
  h.pending = new Promise(resolve => { finish = resolve; });
  const sending = h.send(); await h.send();
  assert.deepEqual(h.posts, [{ app: 'dig003', cmd: 'resync' }]);
  assert.match(h.html(), /Sending RESYNC/);
  await h.selectApp('tm:tm001');
  assert.doesNotMatch(h.html(), /Sending RESYNC/);
  finish({ ok: true, json: async () => ({ status: 'success', message: '<actual reply>' }) });
  await sending;
  assert.doesNotMatch(h.html(), /actual reply/);
  await h.selectApp('dig:dig003');
  assert.match(h.html(), /UTC · success: &lt;actual reply&gt;/);
  assert.match(h.html(), /value="resync" selected/);
  await h.controller.refresh();
  assert.match(h.html(), /value="resync" selected/);
  assert.match(h.html(), /actual reply/);
});

test('app-specific commands require a value and preserve drafts during polling', async () => {
  const h = commandHarness(); await new Promise(setImmediate);
  await h.selectApp('dm:dm001'); await h.choose('stop');
  assert.match(h.html(), /placeholder="Dish ID"/);
  await h.send(); assert.equal(h.posts.length, 0);
  assert.match(h.html(), /Enter a dish ID/);
  await h.value('dish002'); await h.controller.refresh();
  assert.match(h.html(), /value="dish002"/);
  await h.send();
  assert.deepEqual(h.posts, [{ app: 'dm', cmd: 'stop', value: 'dish002' }]);
  await h.choose('trace-get'); await h.send();
  assert.deepEqual(h.posts[1], { app: 'dm', cmd: 'trace' });
  await h.choose('trace-on'); await h.send();
  assert.deepEqual(h.posts[2], { app: 'dm', cmd: 'trace', value: 'ON' });
  assert.match(h.get('status-apps').innerHTML, /data-status-toggle="trace"[^>]*aria-label="Toggle tracing for dm001"/);
});

test('detail commands respect viewer access and report an unknown outcome without retry', async () => {
  const h = commandHarness(true); await new Promise(setImmediate);
  await h.selectApp('tm:tm001'); await h.choose('resync'); await h.send();
  assert.equal(h.posts.length, 0);
  assert.match(h.html(), /data-status-command="send" disabled/);
  h.win.DmdSession.can_edit = true; h.fail = true;
  await h.send(); assert.equal(h.posts.length, 1);
  assert.match(h.html(), /outcome_unknown/);
  assert.match(h.html(), /check the application before retrying/);
});

test('command description shares the response space and is replaced during sending', async () => {
  const h = commandHarness(); await new Promise(setImmediate);
  await h.selectApp('tm:tm001'); await h.choose('resync');
  assert.match(h.html(), /Send<\/button><p[^>]*aria-label="Command description"[^>]*>Reload application configuration<\/p><\/div>/);
  assert.doesNotMatch(h.html(), /status-app-command-description/);
  let finish;
  h.pending = new Promise(resolve => { finish = resolve; });
  const sending = h.send();
  assert.doesNotMatch(h.html(), /Reload application configuration/);
  assert.match(h.html(), /aria-label="Command response"[^>]*>[^<]*Sending RESYNC/);
  finish({ ok: true, json: async () => ({ status: 'success', message: 'Resynchronised' }) });
  await sending;
  assert.match(h.html(), /success: Resynchronised/);
  assert.doesNotMatch(h.html(), /Reload application configuration/);
  await h.choose('resync');
  assert.match(h.html(), /Reload application configuration/);
  assert.doesNotMatch(h.html(), /success: Resynchronised/);
});

test('DM entity connections list configured network dishes and explicit last communication times', () => {
  const model = snapshot().telescope_model;
  model.dsh_mgr.dish_store.dish_list = [
    { dsh_id: 'dish003', driver_type: en('MD01'), driver_config: { host: '192.168.50.3', port: 65000 },
      last_update: { value: '2026-11-07T12:00:00Z' }, driver_failures: 4, health: en('FAILED'),
      last_comms_success_dt: { value: '2026-11-07T11:00:00Z' } },
    { dsh_id: 'dish002', driver_type: en('DRIFT'), driver_config: {} },
    { dsh_id: 'dish001', driver_type: 'MD01', driver_config: { host: '<host>', port: 23 },
      last_update: { value: '2026-11-07T12:01:00Z' } },
    { dsh_id: 'dish004', driver_type: en('MOTION'), driver_config: { imu_host: '192.168.50.4', imu_port: 52500 } },
    { dsh_id: 'dish005', driver_type: en('MD01') },
  ];
  const dm = applications(model).find(item => item.type === 'dm');
  assert.deepEqual(dm.entityLinks.map(link => link.peer), ['dish001', 'dish002', 'dish003', 'dish004', 'dish005']);
  assert.equal(dm.entityLinks[0].lastSuccess, null);
  assert.equal(dm.entityLinks[3].host, '192.168.50.4');
  const table = detail(dm).split('class="status-entity-connections"')[1].split('id="status-processor-table"')[0];
  assert.match(table, /Last successful communication UTC/);
  assert.match(table, /dish001<\/td><td>Client - On demand<\/td><td>&lt;host&gt;<\/td><td>23<\/td><td>—/);
  assert.match(table, /192\.168\.50\.3<\/td><td>65000<\/td><td>2026-11-07 11:00:00 UTC/);
  assert.match(table, /dish005<\/td><td>Client - On demand<\/td><td>—<\/td><td>—<\/td><td>—/);
  assert.match(table, /dish002<\/td><td>—<\/td><td colspan="3">Not Applicable/);
  assert.doesNotMatch(table, /<host>|12:0[01]:00|ESTABLISHED/);
});
