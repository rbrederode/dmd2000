const test = require('node:test');
const assert = require('node:assert/strict');
const { init, requestApi } = require('../../ui/frontend_prototype/dishes.js');

class Element {
  constructor() { this.listeners = {}; this.children = []; this._value = ''; this.hidden = false; this.checked = false; this.type = 'text'; this.dataset = {}; this.classes = new Set(); this.classList = { toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key), contains: (key) => this.classes.has(key) }; }
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async emit(name, event = {}) { for (const fn of this.listeners[name] || []) await fn({ preventDefault() {}, ...event }); }
  setAttribute(name, value) { this[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return []; }
  reportValidity() { return true; }
  focus() { this.focused = true; }
}

function harness() {
  const elements = new Map(), fields = new Map(), calls = [], events = {};
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const field = (name) => { if (!fields.has(name)) fields.set(name, new Element()); return fields.get(name); };
  const form = get('dish-form'); form.hidden = true; form.elements = { namedItem: field };
  const win = { CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    dispatchEvent(event) { events[event.type]?.(event); }, confirm: () => true, addEventListener: (name, fn) => { events[name] = fn; } };
  const doc = { getElementById: get, createElement: () => new Element(), querySelector: () => new Element() };
  const record = { dsh_id: 'dish001', short_desc: 'Original dish', diameter: 0.7, fd_ratio: 0.37, latitude: 53,
    longitude: -2, height: 90, feed_type: 'H3T_1420', dig_id: null, ws_id: null,
    driver_type: 'DRIFT', driver_poll_period: 1000, driver_config: { alt: 90, az: 0 } };
  const data = { local: [structuredClone(record)], other: [{ ...record, short_desc: 'Other profile' }] };
  let selected = 'local', version = 1;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  async function api(resource, options = {}) {
    calls.push({ resource, ...clone(options) });
    if (options.profile && selected !== options.profile) throw Object.assign(new Error('Profile changed'), { code: 'profile_changed' });
    if (resource === 'profiles') return { payload: { profile: selected, profiles: [{ profile: 'local', available: true }, { profile: 'other', available: true }] } };
    if (resource === 'profile') { selected = options.body.profile; return { payload: { profile: selected, scan_store_dir: '/samples/' + selected } }; }
    const etag = () => '"' + version + '"';
    if (options.method && options.etag !== etag()) throw Object.assign(new Error('Stale'), { code: 'version_conflict' });
    if (resource === 'dishes' && !options.method) return { payload: { profile: selected, dishes: clone(data[selected]) }, etag: etag() };
    if (options.method === 'POST') {
      data[selected].push(clone(options.body)); version++;
      return { payload: { profile: selected, dish: clone(options.body) }, etag: etag() };
    }
    const dish = data[selected].find((dish) => resource === 'dishes/' + dish.dsh_id);
    if (!dish) throw new Error('Missing dish');
    if (options.method === 'DELETE') {
      data[selected].splice(data[selected].indexOf(dish), 1); version++;
      return { payload: { deleted_id: dish.dsh_id }, etag: etag() };
    }
    if (options.method === 'PATCH') { Object.assign(dish, clone(options.body)); version++; }
    return { payload: { profile: selected, dish: clone(dish) }, etag: etag() };
  }
  return { editor: init(doc, win, api), get: (id) => get('dish-' + id), field, calls, win, events, data,
    bump: () => version++, select: (name) => { selected = name; } };
}

test('loads real API list shape and edits a versioned dish', async () => {
  const h = harness(); await h.editor.refresh();
  assert.match(h.get('rows').innerHTML, /Original dish/);
  assert.equal(h.get('form').hidden, false);
  assert.equal(h.field('dsh_id').readOnly, true);
  h.field('short_desc').value = 'Updated dish'; await h.get('form').emit('input');
  assert.equal(h.get('dirty').hidden, false);
  await h.editor.save({ preventDefault() {} });
  const save = h.calls.find((call) => call.method === 'PATCH');
  assert.equal(save.profile, 'local'); assert.equal(save.etag, '"1"');
  assert.equal(save.body.short_desc, 'Updated dish');
  assert.equal(h.editor.dirty(), false);
  assert.match(h.get('message').textContent, /Saved dish001/);
});

test('creates a new dish and reloads the confirmed record', async () => {
  const h = harness(); await h.editor.refresh(); await h.get('new').emit('click');
  assert.equal(h.field('dsh_id').readOnly, false);
  assert.equal(h.field('driver_type').value, 'DRIFT');
  h.field('dsh_id').value = 'dish004'; h.field('short_desc').value = 'New dish';
  await h.editor.save({ preventDefault() {} });
  assert.equal(h.calls.find((call) => call.method === 'POST').body.dsh_id, 'dish004');
  assert.equal(h.editor.state.selected, 'dish004');
  assert.equal(h.field('dsh_id').readOnly, true);
  assert.equal(h.editor.state.isNew, false);
});

test('retains unsaved draft on conflict and guards profile selection', async () => {
  const h = harness(); await h.editor.refresh();
  h.field('short_desc').value = 'Keep my draft'; h.bump();
  await h.editor.save({ preventDefault() {} });
  assert.equal(h.field('short_desc').value, 'Keep my draft');
  assert.equal(h.editor.dirty(), true);
  assert.match(h.get('message').textContent, /Your edits are kept/);
  h.win.confirm = () => false; h.get('profile').value = 'other';
  await h.editor.switchProfile();
  assert.equal(h.editor.state.profile, 'local');
  assert.equal(h.get('profile').value, 'local');
  assert.equal(h.calls.filter((call) => call.resource === 'profile').length, 0);
});

test('profile switch refreshes dishes and detects external profile changes', async () => {
  const h = harness(); await h.editor.refresh();
  h.get('profile').value = 'other'; await h.editor.switchProfile();
  assert.equal(h.editor.state.profile, 'other');
  assert.equal(h.field('short_desc').value, 'Other profile');
  h.field('short_desc').value = 'Draft'; h.select('local');
  await h.editor.save({ preventDefault() {} });
  assert.equal(h.field('short_desc').value, 'Draft');
  assert.match(h.get('message').textContent, /Another session selected/);
  assert.equal(h.data.local[0].short_desc, 'Original dish');
});

test('driver changes reset settings and unsaved drafts guard page exit', async () => {
  const h = harness(); await h.editor.refresh();
  h.field('driver_type').value = 'MD01'; await h.field('driver_type').emit('change');
  assert.equal(h.editor.payload().driver_config.host, 'localhost');
  assert.equal(h.editor.payload().driver_config.alt, undefined);
  let prevented = false;
  h.events.beforeunload({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  await h.get('cancel').emit('click');
  assert.equal(h.editor.payload().driver_type, 'DRIFT');
  assert.equal(h.editor.dirty(), false);
});

test('list rendering escapes configuration text', async () => {
  const h = harness(); h.data.local[0].short_desc = '<img onerror="bad">';
  await h.editor.refresh();
  assert.match(h.get('rows').innerHTML, /&lt;img/);
  assert.doesNotMatch(h.get('rows').innerHTML, /<img/);
});

test('HTTP client checks application status and sends versions without credentials', async () => {
  const previous = global.fetch;
  try {
    let options;
    global.fetch = async (url, value) => { options = value; assert.equal(url, '/api/web/dishes/dish001');
      return { ok: true, headers: { get: () => '"new"' }, json: async () => ({ status: 'success', payload: { dish: {} } }) }; };
    const result = await requestApi('dishes/dish001', { method: 'PATCH', body: { diameter: 1 }, profile: 'local', etag: '"old"' });
    assert.equal(result.etag, '"new"');
    assert.equal(options.headers['X-Config-Profile'], 'local');
    assert.equal(options.headers['If-Match'], '"old"');
    assert.equal(options.headers['X-Webhook-Token'], undefined);
    global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'error', code: 'configuration_failed', message: 'Rejected' }) });
    await assert.rejects(requestApi('dishes'), /Rejected/);
  } finally { global.fetch = previous; }
});

test('clicking a dish row opens it and preserves unsaved-edit protection', async () => {
  const h = harness();
  h.data.local.push({ ...h.data.local[0], dsh_id: 'dish002', short_desc: 'Second dish' });
  await h.editor.refresh();
  const event = { target: { closest: () => ({ dataset: { dishId: 'dish002' } }) } };
  h.field('short_desc').value = 'Unsaved draft';
  h.win.confirm = () => false;
  await h.get('rows').emit('click', event);
  assert.equal(h.editor.state.selected, 'dish001');
  assert.equal(h.field('short_desc').value, 'Unsaved draft');
  h.win.confirm = () => true;
  await h.get('rows').emit('click', event);
  assert.equal(h.editor.state.selected, 'dish002');
  assert.equal(h.field('short_desc').value, 'Second dish');
});

test('dish rows support Enter and Space without an Edit button', async () => {
  const h = harness();
  h.data.local.push({ ...h.data.local[0], dsh_id: 'dish002' });
  await h.editor.refresh();
  assert.match(h.get('rows').innerHTML, /tabindex="0"/);
  assert.doesNotMatch(h.get('rows').innerHTML, /<button/);
  for (const [key, id] of [['Enter', 'dish002'], [' ', 'dish001']]) {
    await h.get('rows').emit('keydown', { type: 'keydown', key,
      target: { closest: () => ({ dataset: { dishId: id } }) } });
    assert.equal(h.editor.state.selected, id);
  }
});


test('deleting a dish requires confirmation and clears the editor after deleting the last dish', async () => {
  const h = harness(); await h.editor.refresh();
  assert.equal(h.get('delete').hidden, false);
  h.win.confirm = () => false;
  await h.get('delete').emit('click');
  assert.equal(h.calls.some((call) => call.method === 'DELETE'), false);
  let prompt;
  h.win.confirm = (text) => { prompt = text; return true; };
  h.field('short_desc').value = 'Unsaved edits';
  await h.get('delete').emit('click');
  assert.match(prompt, /dish001.*local/);
  assert.match(prompt, /Unsaved edits/);
  const deletion = h.calls.find((call) => call.method === 'DELETE');
  assert.equal(deletion.profile, 'local'); assert.equal(deletion.etag, '"1"');
  assert.equal(h.editor.state.dishes.length, 0);
  assert.equal(h.get('form').hidden, true);
  assert.match(h.get('message').textContent, /Deleted dish001/);
  await h.get('new').emit('click');
  assert.equal(h.get('delete').hidden, true);
});

test('a stale deletion retains the dish and its unsaved draft', async () => {
  const h = harness(); await h.editor.refresh();
  h.field('short_desc').value = 'Keep draft'; h.bump();
  await h.get('delete').emit('click');
  assert.equal(h.data.local.length, 1);
  assert.equal(h.field('short_desc').value, 'Keep draft');
  assert.equal(h.editor.dirty(), true);
  assert.match(h.get('message').textContent, /Your edits are kept/);
});


test('header profile publishes successful selection and blocks switches during OPT processing', async () => {
  const h = harness();
  const selected = [];
  h.events['dmd:profile'] = event => selected.push(event.detail.profile);
  await h.editor.refresh();
  assert.deepEqual(selected, ['local']);
  h.win.DmdOpt = {isBusy: () => true};
  h.editor.admin.update();
  assert.equal(h.get('profile').disabled, true);
  h.get('profile').value = 'other';
  await h.editor.switchProfile();
  assert.equal(h.get('profile').value, 'local');
  assert.deepEqual(selected, ['local']);
  assert.equal(h.calls.filter(call => call.resource === 'profile').length, 0);
  h.win.DmdOpt = {isBusy: () => false};
  h.get('profile').value = 'other';
  await h.editor.switchProfile();
  assert.deepEqual(selected, ['local', 'other']);
  assert.equal(h.editor.admin.profile(), 'other');
  assert.equal(h.editor.admin.selection().scan_store_dir, '/samples/other');
});
