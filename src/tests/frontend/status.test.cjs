const test = require('node:test');
const assert = require('node:assert/strict');
const { init: statusInit, applications, detail, connections, consolidatedConnections, digitisersTable, percent, lastUpdate } = require('../../ui/frontend_prototype/status.js');
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
  payload.telescope_model.config = {name:'Observatory', longitude:-2.305485};
  let clockConfig;
  payload.profile = 'browser-working-profile';
  payload.telescope_model.dsh_mgr.app = {...payload.telescope_model.dsh_mgr.app, arguments: {profile: 'rotation'}};
  payload.telescope_model.sdp.app = {...payload.telescope_model.sdp.app, arguments: {}};
  const intervals = [];
  const win = { setTimeout, clearTimeout, setInterval(fn, ms) { intervals.push({ fn, ms }); return 1; }, clearInterval() {}, addEventListener() {} };
  win.DmdSiderealClock = {setConfig(config) { clockConfig = config; }};
  const controller = init(doc, win, async (url) => {
    assert.equal(url, '/api/web/telescope/model'); calls++;
    if (!online) throw new Error('Disconnected');
    return { ok: true, json: async () => ({ status: 'success', payload }) };
  });
  await new Promise(setImmediate);
  assert.deepEqual(clockConfig, payload.telescope_model.config);
  assert.equal(intervals[0].ms, 5000);
  assert.equal(get('status-connection').hidden, true);
  assert.equal(get('status-freshness').className, 'zk-muted');
  assert.equal(get('status-detail').hidden, true);
  assert.match(get('status-apps').innerHTML, /<td>Telescope Manager<\/td><td>tm001<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>Science Data Processor<\/td><td>sdp001<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>Digitiser<\/td><td>dig003<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>dm001<\/td><td>rotation<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>tm001<\/td><td>local<\/td>/);
  assert.match(get('status-apps').innerHTML, /<td>sdp001<\/td><td>—<\/td>/);
  assert.doesNotMatch(get('status-apps').innerHTML, /browser-working-profile/);
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
  assert.equal(get('status-connection').hidden, false);
  assert.match(get('status-freshness').className, /zk-chip is-warn/);
  assert.match(get('status-detail-subtitle').className, /zk-chip is-warn/);
  online = true; payload = snapshot(); await controller.refresh();
  assert.equal(controller.state.data.stale, false);
  assert.equal(get('status-connection').hidden, true);
  assert.equal(get('status-freshness').className, 'zk-muted');
  await get('status-apps').emit('keydown', { ...selected, key: 'Enter' });
  await get('status-detail-close').emit('click');
  assert.equal(get('status-detail').hidden, true);
});

test('percentage trends retain the last change across equal reports and hide unknown values', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot(), model = payload.telescope_model;
  // Separate app objects so that each row's trend can be checked independently.
  model.tel_mgr.app = { ...model.tel_mgr.app, availability: 50, reliability: { reliability: 80 } };
  let online = true;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => {
      if (!online) throw new Error('Offline');
      return { ok: true, json: async () => ({ status: 'success', payload }) };
    });
  await new Promise(setImmediate);
  const row = key => get('status-apps').innerHTML.match(new RegExp('<tr data-app-key="' + key + '"[^]*?</tr>'))[0];
  assert.doesNotMatch(row('tm:tm001'), /status-percentage-trend/); // No previous observation yet.
  model.tel_mgr.app.availability = 60;
  model.tel_mgr.app.reliability.reliability = 70;
  model.tel_mgr.app.last_update = en('2026-11-06T08:20:30Z');
  await controller.refresh();
  assert.match(row('tm:tm001'), /60.00%<span class="status-percentage-trend is-up"[^>]*>↑/);
  assert.match(row('tm:tm001'), /70.00%<span class="status-percentage-trend is-down"[^>]*>↓/);
  assert.doesNotMatch(row('ws:ws001'), /is-up|is-down/);
  await controller.refresh(); // Same cached report retains the directions.
  assert.match(row('tm:tm001'), /is-up/);
  assert.match(row('tm:tm001'), /is-down/);
  await get('status-apps').emit('click', { target: { closest: selector => selector === '[data-app-key]' ? { dataset: { appKey: 'tm:tm001' } } : null } });
  assert.match(row('tm:tm001'), /is-up/); // UI-only renders do not change trends.
  model.tel_mgr.app.last_update = en('2026-11-06T08:20:40Z');
  model.wtr_stn.app.last_update = en('2026-11-06T08:20:40Z');
  await controller.refresh();
  assert.match(row('tm:tm001'), /60.00%<span class="status-percentage-trend is-up"/);
  assert.match(row('tm:tm001'), /70.00%<span class="status-percentage-trend is-down"/);
  assert.match(row('ws:ws001'), /aria-label="Unchanged"[^>]*>↔/);
  // Compare full precision and reverse the direction even when displayed values match.
  model.tel_mgr.app.availability = 59.9999;
  model.tel_mgr.app.reliability.reliability = 70.0001;
  await controller.refresh();
  assert.match(row('tm:tm001'), /60.00%<span class="status-percentage-trend is-down"/);
  assert.match(row('tm:tm001'), /70.00%<span class="status-percentage-trend is-up"/);
  model.tel_mgr.app.last_update = en('2026-11-06T08:20:45Z');
  await controller.refresh();
  assert.match(row('tm:tm001'), /60.00%<span class="status-percentage-trend is-down"/);
  assert.match(row('tm:tm001'), /70.00%<span class="status-percentage-trend is-up"/);
  model.tel_mgr.app.availability = null;
  model.tel_mgr.app.reliability.reliability = NaN;
  await controller.refresh();
  assert.doesNotMatch(row('tm:tm001'), /status-percentage-trend/);
  model.tel_mgr.app.availability = 90;
  model.tel_mgr.app.reliability = { reliability_availability: 95 };
  await controller.refresh();
  assert.match(row('tm:tm001'), /<td>90.00%<\/td><td>95.00%<\/td>/);
  model.tel_mgr.app.last_update = en('2026-11-06T08:20:50Z');
  await controller.refresh();
  assert.match(row('tm:tm001'), /is-unchanged/);
  online = false;
  await controller.refresh();
  assert.doesNotMatch(row('tm:tm001'), /status-percentage-trend|90.00%|95.00%/);
  online = true;
  payload.stale = false;
  model.tel_mgr.ws_connected = en('NOT_ESTABLISHED');
  await controller.refresh();
  assert.doesNotMatch(row('ws:ws001'), /status-percentage-trend|0.00%/);
});

test('percentage trends survive reloads and outages and remain separate for each profile', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const saved = new Map();
  const localStorage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) };
  const payload = snapshot();
  payload.telescope_model.tel_mgr.app = { ...payload.telescope_model.tel_mgr.app, availability: 50 };
  const start = async () => {
    const elements = new Map();
    const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
    const controller = init({ getElementById: get, querySelector: get },
      { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}, localStorage },
      async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
    await new Promise(setImmediate);
    return { controller, row: () => get('status-apps').innerHTML.match(/<tr data-app-key="tm:tm001"[^]*?<\/tr>/)[0] };
  };
  const first = await start();
  payload.telescope_model.tel_mgr.app.availability = 50.00001;
  await first.controller.refresh();
  assert.match(first.row(), /50.00%<span class="status-percentage-trend is-up"/);
  const reloaded = await start();
  assert.match(reloaded.row(), /is-up/);
  payload.stale = true;
  await reloaded.controller.refresh();
  assert.doesNotMatch(reloaded.row(), /status-percentage-trend/);
  payload.stale = false;
  payload.telescope_model.tel_mgr.app.last_update = en('2026-11-06T08:21:01Z');
  await reloaded.controller.refresh();
  assert.match(reloaded.row(), /is-up/);
  payload.profile = 'different';
  await reloaded.controller.refresh();
  assert.doesNotMatch(reloaded.row(), /status-percentage-trend/);
  payload.profile = 'local';
  await reloaded.controller.refresh();
  assert.match(reloaded.row(), /is-up/);
});

test('consolidated connections include app and entity links with configured server endpoints', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const payload = snapshot(), model = payload.telescope_model;
  model.tel_mgr.app = { ...model.tel_mgr.app, interface_roles: { dm: 'CLIENT', dig: 'SERVER' },
    arguments: { dm_host: 'dm.example', dm_port: 50002, dig_host: '0.0.0.0', dig_port: 55000 } };
  model.dig_store.dig_list[1].tm_connected = en('ESTABLISHED');
  model.dig_store.dig_list[1].app = { ...model.dig_store.dig_list[1].app,
    arguments: { local_host: '192.168.1.9', tm_port: 9999 }, interface_roles: { tm: 'CLIENT' } };
  model.dig_store.dig_list[1].sdr_connected = en('ESTABLISHED');
  model.dig_store.dig_list[1].sdr_config = { host: 'sdr.example', port: 1234 };
  model.dsh_mgr.dish_store.dish_list = [
    { dsh_id: '<dish001>', driver_type: 'MD01', driver_config: { host: 'dish.example', port: 8000 } },
    { dsh_id: 'dish002', driver_type: 'DRIFT' },
  ];
  let links = consolidatedConnections(model);
  assert.deepEqual(links.find(link => link.application === 'tm001' && link.peer === 'dm001'),
    { application: 'tm001', peer: 'dm001', role: 'Client - Persistent', state: 'ESTABLISHED', endpoint: 'dm.example:50002' });
  const server = links.find(link => link.application === 'tm001' && link.peer === 'dig003');
  assert.equal(server.role, 'Server');
  assert.equal(server.endpoint, '0.0.0.0:55000'); // Listening address rather than the digitiser's address.
  assert.equal(server.state, 'UNKNOWN');
  assert.equal(links.filter(link => [link.application, link.peer].sort().join(':') === 'dig001:tm001').length, 1);
  assert.equal(links.find(link => link.application === 'dig001' && link.peer === 'tm001').state, 'ESTABLISHED');
  assert.equal(links.find(link => link.peer === 'SDR').endpoint, 'sdr.example:1234');
  assert.equal(links.find(link => link.peer === '<dish001>').state, 'UNKNOWN');
  assert.equal(links.find(link => link.peer === '<dish001>').endpoint, 'dish.example:8000');
  assert.equal(links.find(link => link.peer === 'dish002').state, 'Not Applicable');
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  assert.equal(get('status-connections-pane').hidden, true);
  await get('status-connections-open').emit('click');
  assert.equal(get('status-connections-pane').hidden, false);
  assert.doesNotMatch(get('status-connections-rows').innerHTML, /dish001|dish002|SDR/);
  model.tel_mgr.dm_connected = en('NOT_ESTABLISHED');
  await controller.refresh();
  assert.equal(get('status-connections-pane').hidden, false);
  assert.match(get('status-connections-rows').innerHTML, /NOT_ESTABLISHED/);
  links = consolidatedConnections(model, true);
  assert.ok(links.every(link => ['UNKNOWN', 'Not Applicable'].includes(link.state)));
  await get('status-connections-close').emit('click');
  assert.equal(get('status-connections-pane').hidden, true);
});

test('connection pairs resolve actual IDs, reconcile reports and retain distinct digitiser and hardware links', (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const model = snapshot().telescope_model;
  model.tel_mgr.id = 'telmgr001';
  model.dsh_mgr.id = 'dshmgr001';
  model.tel_mgr.app = { ...model.tel_mgr.app, interface_roles: { dm: 'CLIENT', dig: 'SERVER' },
    arguments: { dm_host: 'dm.example', dm_port: 50002, dig_host: '0.0.0.0', dig_port: 50000 } };
  model.dsh_mgr.app = { ...model.dsh_mgr.app, interface_roles: { tm: 'SERVER' },
    arguments: { tm_host: '0.0.0.0', tm_port: 50002 } };
  model.dsh_mgr.tm_connected = en('ESTABLISHED');
  model.dsh_mgr.ws_connected = en('ESTABLISHED');
  model.wtr_stn.dm_connected = en('ESTABLISHED');
  model.sdp.dig_store = { dig_list: model.dig_store.dig_list };
  for (const dig of model.dig_store.dig_list) {
    dig.app = { ...dig.app, interface_roles: { tm: 'CLIENT', sdp: 'CLIENT' },
      arguments: { tm_host: 'tm.example', tm_port: 50000, sdp_host: 'sdp.example', sdp_port: 60000 } };
    dig.tm_connected = en('ESTABLISHED');
    dig.sdp_connected = en('ESTABLISHED');
    dig.sdr_connected = en('ESTABLISHED');
    dig.sdr_config = { host: 'same-sdr.example', port: 1234 };
  }
  const pair = (a, b) => consolidatedConnections(model).filter(link =>
    [link.application, link.peer].sort().join(':') === [a, b].sort().join(':'));
  assert.equal(pair('telmgr001', 'dshmgr001').length, 1);
  assert.equal(pair('telmgr001', 'dshmgr001')[0].endpoint, 'dm.example:50002');
  assert.equal(pair('ws001', 'dshmgr001').length, 1); // Roles need not be available to pair reports.
  for (const dig of model.dig_store.dig_list) {
    assert.equal(pair(dig.dig_id, 'telmgr001').length, 1);
    assert.equal(pair(dig.dig_id, 'sdp001').length, 1);
    assert.equal(pair(dig.dig_id, 'SDR').length, 1);
  }
  assert.equal(consolidatedConnections(model).filter(link => link.peer === 'SDR').length, 2);
  model.dsh_mgr.tm_connected = en('NOT_ESTABLISHED');
  assert.equal(pair('telmgr001', 'dshmgr001')[0].state, 'NOT_ESTABLISHED'); // A conflicting failure wins.
  model.dsh_mgr.tm_connected = en('ESTABLISHED');
  model.dsh_mgr.app.last_update = en('2026-11-06T08:18:00Z');
  assert.equal(pair('telmgr001', 'dshmgr001')[0].state, 'ESTABLISHED'); // A live report supersedes stale data.
  model.tel_mgr.dm_connected = en('NOT_ESTABLISHED');
  assert.equal(pair('telmgr001', 'dshmgr001')[0].state, 'NOT_ESTABLISHED');
  model.tel_mgr.app.last_update = en('2026-11-06T08:18:00Z');
  assert.equal(pair('telmgr001', 'dshmgr001')[0].state, 'UNKNOWN');
});

test('network dish connections reflect controller failures and successful responses', (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const model = snapshot().telescope_model;
  const dish = { dsh_id: 'dish001', driver_type: en('MD01'), driver_failures: 0,
    last_comms_success_dt: null, last_update: en('2026-11-06T08:21:00Z') };
  model.dsh_mgr.dish_store.dish_list = [dish];
  const state = () => consolidatedConnections(model).find(link => link.peer === 'dish001').state;
  assert.equal(state(), 'NOT_ESTABLISHED'); // No successful response yet.
  dish.driver_failures = 3;
  assert.equal(state(), 'NOT_ESTABLISHED');
  dish.last_comms_success_dt = en('2026-11-06T08:20:59Z');
  assert.equal(state(), 'NOT_ESTABLISHED'); // An older success cannot mask later failures.
  dish.driver_failures = 0;
  assert.equal(state(), 'ESTABLISHED');
  dish.driver_type = en('MOTION');
  assert.equal(state(), 'ESTABLISHED');
  dish.driver_failures = 1;
  assert.equal(state(), 'NOT_ESTABLISHED');
  model.tel_mgr.dm_connected = en('NOT_ESTABLISHED');
  assert.equal(state(), 'UNKNOWN');
  model.tel_mgr.dm_connected = en('ESTABLISHED');
  dish.last_update = en('2026-11-06T08:18:00Z');
  assert.equal(state(), 'UNKNOWN');
  dish.driver_type = en('DRIFT');
  assert.equal(state(), 'Not Applicable');
  dish.driver_type = en('MD01');
  delete dish.last_update;
  delete dish.driver_failures;
  delete dish.last_comms_success_dt;
  assert.equal(state(), 'UNKNOWN'); // Older snapshots with no communication evidence.
});

test('Dish Manager opens separate manager and dishes panes; capability opens only dishes', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.telescope_model.dsh_mgr.dish_store.dish_list = [{ dsh_id: 'dish001', mode: 'STOW' }];
  const opened = [], updates = [];
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {},
      DmdDishStatus: { init: () => ({ open: (...args) => opened.push(args), update: data => updates.push(data) }) } },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  assert.equal(get('status-dishes-pane').hidden, true);
  const select = key => get('status-apps').emit('click', { target: {
    closest: selector => selector === '[data-app-key]' ? { dataset: { appKey: key } } : null } });
  await select('dm:dm001');
  assert.equal(get('status-detail').hidden, false);
  assert.equal(get('status-detail-title').textContent, 'Dish Manager — dm001');
  assert.equal(get('status-dishes-pane').hidden, false);
  assert.doesNotMatch(get('status-detail-content').innerHTML, /status-dish-table/);
  assert.match(get('status-detail-content').innerHTML, /Weather monitoring/);
  assert.match(get('status-dishes-content').innerHTML, /data-status-dish="dish001"/);
  await get('status-dishes-close').emit('click');
  assert.equal(get('status-dishes-pane').hidden, true);
  assert.equal(get('status-detail').hidden, false);
  await get('status-dishes-open').emit('click');
  assert.equal(get('status-detail').hidden, true);
  assert.equal(get('status-dishes-pane').hidden, false);
  const event = { target: { closest: () => ({ dataset: { statusDish: 'dish001' } }) } };
  await get('status-dishes-content').emit('click', event);
  await get('status-dishes-content').emit('keydown', { ...event, key: 'Enter' });
  assert.deepEqual(opened.map(args => args[0]), ['dish001', 'dish001']);
  payload.telescope_model.dsh_mgr.dish_store.dish_list[0].mode = 'OPERATE';
  await controller.refresh();
  assert.match(get('status-dishes-content').innerHTML, /<td>OPERATE<\/td>/);
  assert.equal(updates.at(-1), payload);
  assert.equal(get('status-detail').hidden, true);
  await select('tm:tm001');
  assert.equal(get('status-dishes-pane').hidden, true);
  assert.equal(updates.at(-1), null);
});

test('software defined radios lists every digitiser and opens individual controls', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const payload = snapshot(), model = payload.telescope_model;
  const dig = model.dig_store.dig_list[1];
  Object.assign(dig, { load_active: false, gain: 0, center_freq: 1420000000, sample_rate: 2048000,
    bandwidth: 2000000, freq_correction: -2, scanning: false, temp_reading: { temperature: 23.5 },
    temp_max: 40, sdr_eeprom: { serial: '<unsafe>' }, tm_connected: en('ESTABLISHED') });
  const html = digitisersTable(model);
  assert.ok(html.indexOf('data-status-digitiser="dig:dig001"') < html.indexOf('data-status-digitiser="dig:dig003"'));
  assert.match(html, /<td>rtlstream<\/td><td>0 dB<\/td><td>1420 MHz<\/td><td>2.048 MHz<\/td>/);
  assert.match(html, /<th aria-label="SDR Type">SDR<br>Type<\/th>/);
  assert.doesNotMatch(html, /<th aria-label="Load">/);
  assert.match(html, /<td>2 MHz<\/td><td>OFF<\/td><td>23.5 °C<\/td><td>40 °C<\/td>/);
  assert.doesNotMatch(html, /Frequency correction/);
  assert.match(html, /<th aria-label="ID">ID<\/th>/);
  assert.match(html, /<th aria-label="Temp Reading">Temp<br>Reading<\/th>/);
  assert.match(html, /<th aria-label="Max Temp">Max<br>Temp<\/th>/);
  assert.match(html, /<th aria-label="Health">Health<\/th><th aria-label="Last error UTC \/ Message">/);
  assert.match(html, /<td><span class="zk-chip is-live">OK<\/span><\/td><td class="status-dish-error">/);
  assert.match(html, /&lt;unsafe&gt;/);
  assert.doesNotMatch(html, /<unsafe>|<script>/);
  assert.match(digitisersTable({}), /No digitisers reported/);
  dig.scanning = { obs_id: 'obs001' };
  assert.match(digitisersTable(model), /<td>2 MHz<\/td><td>SKY<\/td>/);
  dig.load_active = true;
  assert.match(digitisersTable(model), /<td>2 MHz<\/td><td>LOAD<\/td>/);
  dig.scanning = false;
  assert.match(digitisersTable(model), /<td>2 MHz<\/td><td>OFF<\/td>/);
  dig.scanning = true;
  dig.load_active = null;
  assert.match(digitisersTable(model), /<td>2 MHz<\/td><td>—<\/td>/);
  dig.load_active = false;
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  assert.equal(get('status-digitisers-pane').hidden, true);
  await get('status-digitisers-open').emit('click');
  assert.equal(get('status-digitisers-pane').hidden, false);
  assert.equal(get('status-detail').hidden, true);
  const event = { target: { closest: () => ({ dataset: { statusDigitiser: 'dig:dig001' } }) } };
  await get('status-digitisers-content').emit('click', event);
  assert.equal(controller.state.selected, 'dig:dig001');
  assert.equal(get('status-detail-title').textContent, 'Digitiser — dig001');
  assert.match(get('status-detail-content').innerHTML, /data-digitiser-control-app="dig:dig001"/);
  assert.equal(get('status-digitisers-pane').hidden, false);
  dig.gain = 20;
  await controller.refresh();
  assert.match(get('status-digitisers-content').innerHTML, /<td>rtlstream<\/td><td>20 dB<\/td>/);
  dig.tm_connected = en('NOT_ESTABLISHED');
  await controller.refresh();
  const row = get('status-digitisers-content').innerHTML.match(/<tr data-status-digitiser="dig:dig001"[^]*?<\/tr>/)[0];
  assert.equal((row.match(/status-unknown/g) || []).length, 8);
  await get('status-digitisers-close').emit('click');
  assert.equal(get('status-digitisers-pane').hidden, true);
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

test('execution widget counts observations and reports SDP WIP without listing IDs', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
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
  assert.match(get('status-executions').innerHTML, /WIP UNKNOWN/);
  payload.telescope_model.sdp.scans_wip = 4;
  payload.telescope_model.sdp.scans_aborted = 2;
  payload.telescope_model.sdp.scans_completed = 17;
  await controller.refresh();
  assert.match(get('status-executions').innerHTML, /WIP 4 · Aborted 2 · Completed 17/);
  assert.doesNotMatch(get('status-executions').innerHTML, /obs1|obs2|obs3|dish3/);
  for (const obs of payload.telescope_model.oda.obs_store.obs_list) obs.obs_state = en('IDLE');
  await controller.refresh();
  assert.equal(get('[data-status-value="executions.count"]').textContent, '0 in progress');
  payload.telescope_model.sdp.scans_wip = 0;
  await controller.refresh();
  assert.match(get('status-executions').innerHTML, /WIP 0/);
  delete payload.telescope_model.oda;
  await controller.refresh();
  assert.equal(get('[data-status-value="executions.count"]').textContent, '—');
  payload.telescope_model.tel_mgr.sdp_connected = en('NOT_ESTABLISHED');
  await controller.refresh();
  assert.equal(get('status-executions').innerHTML, '<li class="zk-muted">Scans</li><li class="zk-muted">SDP not connected.</li>');
  payload.telescope_model.tel_mgr.sdp_connected = en('ESTABLISHED');
  await controller.refresh();
  assert.match(get('status-executions').innerHTML, /WIP 0 · Aborted 2 · Completed 17/);
  assert.doesNotMatch(get('status-executions').innerHTML, /SDP not connected/);
  payload.telescope_model.sdp.app.last_update = en('2026-11-06T08:18:00Z');
  await controller.refresh();
  assert.match(get('status-executions').innerHTML, /SDP not connected\./);
  delete payload.telescope_model.sdp;
  await controller.refresh();
  assert.match(get('status-executions').innerHTML, /SDP not connected\./);
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


test('unset application timestamps do not fall back to component creation time', () => {
  const model = snapshot().telescope_model;
  model.dsh_mgr.app.last_update = null;
  model.dsh_mgr.last_update = { _type: 'datetime', value: '2026-11-10T16:52:49Z' };
  const dm = applications(model).find(item => item.type === 'dm');
  assert.equal(dm.updated, null);
  assert.doesNotMatch(lastUpdate(dm.updated), /2026|is-old/);
  assert.match(lastUpdate(dm.updated), /—/);
  // Snapshots predating app timestamps can still supply a component timestamp.
  delete model.dsh_mgr.app.last_update;
  assert.deepEqual(applications(model).find(item => item.type === 'dm').updated,
                   model.dsh_mgr.last_update);
});


test('Engineering Refresh waits for a fresh TM snapshot and resets the displayed age', async () => {
  const elements = new Map(), calls = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const doc = {getElementById:get, querySelector:get};
  let resolveFresh, fail = false;
  const win = {setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {}};
  const previous = snapshot(); previous.age_seconds = 26;
  const controller = init(doc, win, async url => {
    calls.push(url);
    if (url.endsWith('?refresh=1')) {
      if (fail) throw new Error('Fresh TM snapshot request timed out');
      return new Promise(resolve => { resolveFresh = resolve; });
    }
    return {ok:true, json:async()=>({status:'success', payload:previous})};
  });
  await new Promise(setImmediate);
  assert.match(get('status-freshness').textContent, /Received 26 seconds ago/);
  const clicked = get('status-refresh').emit('click');
  assert.equal(calls.at(-1), '/api/web/telescope/model?refresh=1');
  assert.equal(get('status-refresh').disabled, true);
  assert.match(get('status-refresh').textContent, /Requesting TM snapshot/);
  assert.equal(controller.state.data, previous);
  await get('status-refresh').emit('click');
  assert.equal(calls.filter(url => url.endsWith('?refresh=1')).length, 1);
  const fresh = {...snapshot(), snapshot_at:'2026-11-06T08:25:00Z', age_seconds:0};
  resolveFresh({ok:true, json:async()=>({status:'success',payload:fresh})});
  await clicked;
  assert.equal(get('status-refresh').disabled, false);
  assert.match(get('status-freshness').textContent, /Snapshot: 2026-11-06 08:25:00 UTC · Received 0 seconds ago/);
  await controller.refresh();
  assert.equal(calls.at(-1), '/api/web/telescope/model');
  fail = true;
  await get('status-refresh').emit('click');
  assert.match(get('status-freshness').textContent, /timed out.*last snapshot as stale/);
  assert.equal(controller.state.data.stale, true);
  assert.equal(get('status-refresh').disabled, false);
});


test('weather overview rounds wind and highlights alarms, clearing when recovered', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.telescope_model.dsh_mgr.dish_store.dish_list = [
    { dsh_id: 'dish001', weather_alarm: false }, { dsh_id: 'dish002', weather_alarm: true },
  ];
  payload.telescope_model.dsh_mgr.weather_store = {
    weather_enabled: true, weather_summaries: [{ ws_id: 'ws001', wind_avg: 11.457644959531837 }],
    weather_metrics: { ws001: { alarm_triggered: true, avg_wind: 11.457644959531837, max_wind: 15.87654, gust_count: 4 }, ws002: { alarm_triggered: true, sample_count: 0 } },
  };
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const alarm = get('[data-status-value="weather.state"]');
  assert.equal(get('[data-status-value="weather.detail"]').textContent, 'Wind');
  assert.equal(get('[data-status-value="weather.wind"]').textContent, 'Avg 11.46 m/s · Gust 15.88 m/s');
  assert.equal(alarm.textContent, 'ALARM');
  assert.equal(alarm.classes.has('weather-alarm-active'), true);
  // An unused station timeout and a dish's retained interlock must not override ws001's current metrics.
  payload.telescope_model.dsh_mgr.weather_store.weather_metrics.ws001.alarm_triggered = false;
  await controller.refresh();
  assert.equal(alarm.textContent, 'SAFE');
  assert.equal(alarm.classes.has('weather-alarm-active'), false);
  payload.telescope_model.dsh_mgr.weather_store.weather_metrics.ws001.alarm_triggered = true;
  await controller.refresh();
  assert.equal(alarm.classes.has('weather-alarm-active'), true);
});


test('weather overview shows a red UNKNOWN when the station is unavailable and recovers on reconnect', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot(), model = payload.telescope_model;
  const station = model.wtr_stn;
  delete model.wtr_stn;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const state = get('[data-status-value="weather.state"]');
  const unknown = () => {
    assert.equal(state.textContent, 'UNKNOWN');
    assert.equal(state.classes.has('status-summary-failed'), true);
    assert.equal(state.classes.has('status-summary-ok'), false);
    assert.equal(state.classes.has('weather-alarm-active'), false);
  };
  unknown();
  model.wtr_stn = station;
  await controller.refresh();
  assert.equal(state.textContent, 'SAFE');
  assert.equal(state.classes.has('status-summary-ok'), true);
  assert.equal(state.classes.has('status-summary-failed'), false);
  // Retained dishes and alarms must not imply live weather after a disconnect.
  model.dsh_mgr.dish_store.dish_list = [{ dsh_id: 'dish001', weather_alarm: true }];
  for (const [target, key, value] of [
    [model.tel_mgr, 'ws_connected', en('NOT_ESTABLISHED')],
    [model.dsh_mgr, 'ws_connected', en('NOT_ESTABLISHED')],
    [station, 'dm_connected', en('NOT_ESTABLISHED')],
    [station, 'tm_connected', en('NOT_ESTABLISHED')],
    [station.app, 'app_running', false],
    [station.app, 'last_update', en('2026-11-06T08:18:00Z')],
  ]) {
    const previous = target[key];
    target[key] = value;
    await controller.refresh();
    unknown();
    if (previous === undefined) delete target[key];
    else target[key] = previous;
  }
  await controller.refresh();
  assert.equal(state.textContent, 'ALARM');
  assert.equal(state.classes.has('weather-alarm-active'), true);
  assert.equal(state.classes.has('status-summary-failed'), false);
});

test('digitiser summary reflects all app running states and health, and clears warning lights on recovery', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  const digitisers = payload.telescope_model.dig_store.dig_list;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const state = get('[data-status-value="digitiser.state"]');
  const detail = get('[data-status-value="digitiser.detail"]');
  assert.equal(state.textContent, '1 UNKNOWN');
  assert.equal(detail.textContent, 'Connected 0 · Scanning 0');
  assert.equal(detail.classes.has('status-summary-divider'), true);
  digitisers[1].app.health = en('FAILED');
  await controller.refresh();
  assert.equal(state.textContent, '1 FAILED'); // Explicit failure wins over unknown.
  digitisers[1].app.health = en('DEGRADED');
  await controller.refresh();
  assert.equal(state.textContent, '1 UNKNOWN'); // Unknown wins over degraded.
  digitisers[0].app.app_running = true;
  await controller.refresh();
  assert.equal(state.textContent, '1 DEGRADED');
  assert.equal(state.classes.has('status-summary-warning'), true);
  for (const item of digitisers) { item.app.app_running = true; item.app.health = en('OK'); }
  digitisers[1].scanning = { obs_id: 'observation' };
  digitisers[1].sdr_connected = en('ESTABLISHED');
  await controller.refresh();
  assert.equal(state.textContent, '2 OK');
  assert.equal(detail.textContent, 'Connected 1 · Scanning 1');
  assert.equal(state.classes.has('status-summary-ok'), true);
  assert.equal(state.classes.has('status-summary-warning'), false);
  assert.equal(state.classes.has('status-summary-failed'), false);
  digitisers[1].tm_connected = en('NOT_ESTABLISHED');
  await controller.refresh();
  assert.equal(state.textContent, '1 UNKNOWN');
  assert.equal(detail.textContent, 'Connected 0 · Scanning 0');
  payload.telescope_model.dig_store.dig_list = [];
  await controller.refresh();
  assert.equal(state.textContent, '—');
  assert.equal(detail.textContent, 'Connected 0 · Scanning 0');
});


test('dish summary counts individual health and clears indicators after recovery', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  const dishes = [{ dsh_id: 'dish001', health: en('OK'), mode: en('STANDBY_FP'), capability: en('OPERATE_FULL') }, { dsh_id: 'dish003', health: en('FAILED'), mode: en('OPERATE'), capability: en('STANDBY') }];
  payload.telescope_model.dsh_mgr.dish_store.dish_list = dishes;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const state = get('[data-status-value="dish.state"]');
  const detail = get('[data-status-value="dish.detail"]');
  assert.equal(state.textContent, '1 FAILED');
  assert.equal(detail.textContent, '—');
  assert.equal(state.hidden, false);
  assert.equal(state.classes.has('status-summary-failed'), true);
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'Standby 1 · Stow 0 · Operate 0');
  dishes[0].health = en('DEGRADED');
  await controller.refresh();
  assert.equal(state.textContent, '1 FAILED'); // Failure wins over degraded.
  dishes[1].health = en('OK');
  await controller.refresh();
  assert.equal(state.textContent, '1 DEGRADED');
  assert.equal(state.classes.has('status-summary-warning'), true);
  assert.equal(state.classes.has('status-summary-failed'), false);
  for (const dish of dishes) dish.health = en('OK');
  await controller.refresh();
  assert.equal(state.textContent, '2 OK');
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'Standby 1 · Stow 0 · Operate 0');
  dishes[1].capability = en('OPERATE_DEGRADED');
  await controller.refresh();
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'Standby 1 · Stow 0 · Operate 1');
  assert.equal(state.classes.has('status-summary-ok'), true);
  assert.equal(state.classes.has('status-summary-warning'), false);
  assert.equal(state.classes.has('status-summary-failed'), false);
  payload.telescope_model.dsh_mgr.dish_store.dish_list = [];
  await controller.refresh();
  assert.equal(state.textContent, '—');
  assert.equal(detail.textContent, '—');
  assert.equal(get('[data-status-value="dish.modes"]').hidden, true);
});


test('application rows show active processing averages and queue, invalidating stale reported metrics', async (t) => {
  let now = Date.parse('2026-11-06T08:21:21Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.telescope_model.tel_mgr.app.processors = [
    { current_event: 'Event A', processing_time_ms: 10 },
    { current_event: 'Event B', processing_time_ms: 30 },
    { current_event: null, processing_time_ms: 999 },
    { current_event: 'Invalid time', processing_time_ms: null },
  ];
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const row = () => get('status-apps').innerHTML.match(/<tr data-app-key="tm:tm001"[^]*?<\/tr>/)[0];
  assert.match(row(), /<td>0<\/td><td>20.00 ms<\/td><td>0.00%<\/td>/);
  assert.doesNotMatch(row(), />RUNNING<|>IDLE<|>STOPPED</);
  now += 61000;
  await controller.refresh();
  assert.match(row(), /zk-chip status-unknown">UNKNOWN/);
  assert.equal((row().match(/class="zk-chip status-unknown">UNKNOWN/g) || []).length, 5);
  payload.telescope_model.tel_mgr.app.last_update = { value: new Date(now).toISOString() };
  payload.telescope_model.tel_mgr.app.processors = [];
  await controller.refresh();
  assert.doesNotMatch(row(), /status-unknown/);
  assert.match(row(), /zk-chip is-live">OK/);
  assert.match(row(), /<td>0<\/td><td>—<\/td>/);
});


test('dish manager failed or aged overrides dish counts and recovers to the dish summary', async (t) => {
  let now = Date.parse('2026-11-06T08:21:21Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.telescope_model.dsh_mgr.dish_store.dish_list = [{ dsh_id: 'dish001', health: en('OK') }];
  const dm = payload.telescope_model.dsh_mgr.app;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const state = get('[data-status-value="dish.state"]');
  const heading = get('[data-status-value="dish.heading"]');
  const detail = get('[data-status-value="dish.detail"]');
  assert.equal(heading.textContent, 'Dishes');
  assert.equal(state.textContent, '1 OK');
  dm.health = en('FAILED');
  await controller.refresh();
  assert.equal(heading.textContent, 'Dish Manager');
  assert.equal(state.hidden, false);
  assert.equal(detail.classes.has('status-dish-health-counts'), false);
  assert.equal(get('[data-status-value="dish.modes"]').hidden, true);
  assert.equal(state.textContent, 'FAILED');
  assert.equal(detail.hidden, true);
  assert.equal(state.classes.has('status-manager-unavailable'), true);
  assert.equal(state.classes.has('status-summary-failed'), true);
  now += 61000;
  await controller.refresh();
  assert.equal(state.textContent, 'UNKNOWN');
  assert.equal(detail.hidden, true);
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'No contact for 2 minutes');
  assert.equal(get('[data-status-value="dish.modes"]').hidden, false);
  assert.equal(get('[data-status-value="dish.operational"]').hidden, false);
  dm.health = en('OK');
  await controller.refresh();
  assert.equal(state.textContent, 'UNKNOWN');
  dm.last_update = { value: new Date(now).toISOString() };
  await controller.refresh();
  assert.equal(heading.textContent, 'Dishes');
  assert.equal(state.textContent, '1 OK');
  assert.equal(detail.hidden, true);
  assert.equal(detail.textContent, '—');
  assert.equal(state.classes.has('status-manager-unavailable'), false);
  assert.equal(state.classes.has('status-summary-failed'), false);
});


test('TM disconnection immediately invalidates fresh app metrics and recovers on reconnection', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  const model = payload.telescope_model;
  for (const type of ['dm', 'sdp', 'ws', 'web']) model.tel_mgr[type + '_connected'] = en('NOT_ESTABLISHED');
  for (const dig of model.dig_store.dig_list) dig.tm_connected = en('NOT_ESTABLISHED');
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const row = key => get('status-apps').innerHTML.split('<tr data-app-key="' + key + '"')[1].split('</tr>')[0];
  for (const key of ['dm:dm001', 'sdp:sdp001', 'ws:ws001', 'web:web001', 'dig:dig001']) {
    assert.equal((row(key).match(/status-unknown">UNKNOWN/g) || []).length, 5);
    assert.doesNotMatch(row(key), /status-update is-old/);
  }
  assert.equal(get('[data-status-value="dish.heading"]').textContent, 'Dish Manager');
  assert.equal(get('[data-status-value="dish.state"]').textContent, 'UNKNOWN');
  assert.equal(get('[data-status-value="dish.detail"]').hidden, true);
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'DM not connected');
  assert.equal(get('[data-status-value="dish.modes"]').hidden, false);
  assert.equal(get('[data-status-value="dish.operational"]').hidden, false);
  assert.doesNotMatch(row('tm:tm001'), /status-unknown/);
  for (const type of ['dm', 'sdp', 'ws', 'web']) model.tel_mgr[type + '_connected'] = en('ESTABLISHED');
  for (const dig of model.dig_store.dig_list) dig.tm_connected = en('ESTABLISHED');
  await controller.refresh();
  for (const key of ['dm:dm001', 'sdp:sdp001', 'ws:ws001', 'web:web001', 'dig:dig001']) {
    assert.doesNotMatch(row(key), /status-unknown/);
  }
  assert.equal(get('[data-status-value="dish.heading"]').textContent, 'Dishes');
});

test('loss of TM invalidates every summary, ages the contact message and restores on recovery', async (t) => {
  let now = Date.parse('2026-11-06T08:21:21Z');
  t.mock.method(Date, 'now', () => now);
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot();
  payload.received_at = new Date(now).toISOString();
  payload.tm_connected = false;
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const keys = ['tm.state', 'dish.state', 'digitiser.state', 'weather.state', 'executions.count'];
  assert.equal(get('status-connections-open').hidden, true);
  await get('status-connections-open').emit('click');
  assert.equal(controller.state.connectionsOpen, false);
  for (const key of keys) {
    const node = get('[data-status-value="' + key + '"]');
    assert.equal(node.textContent, 'UNKNOWN');
    assert.equal(node.classes.has('status-summary-unavailable'), true);
    assert.equal(node.hidden, false);
  }
  for (const key of ['tm.detail', 'dish.detail', 'digitiser.detail', 'weather.detail']) {
    assert.equal(get('[data-status-value="' + key + '"]').textContent, 'Lost contact with TM');
    assert.equal(get('[data-status-value="' + key + '"]').hidden, false);
  }
  assert.equal(get('[data-status-value="dish.operational"]').hidden, true);
  assert.match(get('status-executions').innerHTML, /Lost contact with TM/);
  now += 181000;
  await controller.refresh();
  for (const key of ['tm.detail', 'dish.detail', 'digitiser.detail', 'weather.detail']) {
    assert.equal(get('[data-status-value="' + key + '"]').textContent, 'No contact for 3 minutes');
  }
  assert.equal(get('[data-status-value="dish.operational"]').hidden, true);
  assert.match(get('status-executions').innerHTML, /No contact for 3 minutes/);
  payload.tm_connected = true;
  payload.stale = false;
  payload.received_at = new Date(now).toISOString();
  payload.age_seconds = 0;
  await controller.refresh();
  assert.equal(get('status-connections-open').hidden, false);
  assert.equal(get('[data-status-value="tm.detail"]').hidden, true);
  for (const key of keys) assert.equal(get('[data-status-value="' + key + '"]').classes.has('status-summary-unavailable'), false);
  assert.equal(get('[data-status-value="tm.state"]').textContent, '7 UNKNOWN');
  // Once TM returns, the independently stale DM uses its own contact time and layout.
  assert.equal(get('[data-status-value="dish.detail"]').hidden, true);
  assert.equal(get('[data-status-value="dish.operational"]').hidden, false);
  assert.equal(get('[data-status-value="dish.modes"]').hidden, false);
  assert.equal(get('[data-status-value="dish.modes"]').textContent, 'No contact for 4 minutes');
});


test('applications summary and connection pane include only persistent app links', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-11-06T08:21:21Z'));
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const payload = snapshot(), model = payload.telescope_model;
  model.tel_mgr.dm_connected = en('ESTABLISHED');
  model.tel_mgr.sdp_connected = en('NOT_ESTABLISHED');
  model.wtr_stn.tm_connected = en('ESTABLISHED');
  model.dig_store.dig_list[0].tm_connected = en('ESTABLISHED');
  model.dig_store.dig_list[0].sdr_connected = en('ESTABLISHED');
  model.dig_store.dig_list[1].tm_connected = en('ESTABLISHED');
  model.dig_store.dig_list[1].sdr_connected = en('NOT_ESTABLISHED');
  model.dig_store.dig_list[1].temp_connected = en('ESTABLISHED');
  model.dsh_mgr.dish_store.dish_list = [
    { dsh_id: 'dish001', driver_type: 'MD01', driver_failures: 3, last_comms_success_dt: null },
    { dsh_id: 'dish002', driver_type: 'DRIFT' },
  ];
  model.tel_mgr.app = { ...model.tel_mgr.app, health: en('FAILED') };
  const controller = init({ getElementById: get, querySelector: get },
    { setTimeout, clearTimeout, setInterval() {}, clearInterval() {}, addEventListener() {} },
    async () => ({ ok: true, json: async () => ({ status: 'success', payload }) }));
  await new Promise(setImmediate);
  const state = get('[data-status-value="tm.state"]');
  assert.equal(state.textContent, '1 FAILED');
  assert.equal(get('[data-status-value="tm.connections"]').textContent, 'Established 4 · Not Established 1');
  assert.equal(get('[data-status-value="tm.detail"]').textContent, 'App Connections');
  await get('status-connections-open').emit('click');
  const rows = get('status-connections-rows').innerHTML;
  assert.equal((rows.match(/<tr>/g) || []).length, 5);
  assert.doesNotMatch(rows, /dish001|dish002|SDR|TEMP|On demand|Not Applicable/);
  assert.equal((rows.match(/>ESTABLISHED</g) || []).length, 4);
  assert.equal((rows.match(/>NOT_ESTABLISHED</g) || []).length, 1);
  model.tel_mgr.app.health = en('DEGRADED');
  await controller.refresh();
  assert.equal(state.textContent, '1 UNKNOWN');
  model.tel_mgr.sdp_connected = en('ESTABLISHED');
  await controller.refresh();
  assert.equal(state.textContent, '1 DEGRADED');
  model.tel_mgr.app.health = en('OK');
  await controller.refresh();
  assert.equal(state.textContent, '7 OK');
  assert.equal(state.classes.has('status-summary-ok'), true);
});
