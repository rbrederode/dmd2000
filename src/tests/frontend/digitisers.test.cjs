const test = require('node:test');
const assert = require('node:assert/strict');
const { init: initDishes } = require('../../ui/frontend_prototype/dishes.js');
const { init: initDigitisers, defaults } = require('../../ui/frontend_prototype/digitisers.js');

class Element {
  constructor() { this.listeners = {}; this.children = []; this._value = ''; this.hidden = false; this.checked = false; this.validity = ''; this.dataset = {}; this.classes = new Set(); this.classList = { toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key), contains: (key) => this.classes.has(key) }; }
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async emit(name, event = {}) { for (const fn of this.listeners[name] || []) await fn({ preventDefault() {}, ...event }); }
  setAttribute(name, value) { this[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return []; }
  reportValidity() { return !this.validity; }
  setCustomValidity(value) { this.validity = value; }
  focus() { this.focused = true; }
}

function harness() {
  const elements = new Map(), fields = new Map(), calls = [], events = {};
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const field = (prefix, name) => { const key = prefix + ':' + name; if (!fields.has(key)) fields.set(key, new Element()); return fields.get(key); };
  for (const prefix of ['dish', 'dig']) {
    const form = get(prefix + '-form'); form.hidden = true;
    form.elements = { namedItem: (name) => field(prefix, name) };
  }
  const win = { confirm: () => true, addEventListener: (name, fn) => { events[name] = fn; } };
  const doc = { getElementById: get, createElement: () => new Element(), querySelector: () => new Element(), querySelectorAll: () => [] };
  const dish = { dsh_id: 'dish001', short_desc: 'Dish', diameter: 1, fd_ratio: 0.4, latitude: 0, longitude: 0,
    height: 0, feed_type: 'NONE', dig_id: null, ws_id: null, driver_type: 'DRIFT', driver_poll_period: 1000, driver_config: { alt: 90, az: 0 } };
  const digitiser = { ...defaults(), dig_id: 'dig001', local_host: '192.168.2.104', sdr_type: 'rtlstream',
    sample_rate: 2048000, temp_type: 'bme280', temp_config: { bus_number: 1, address: 118 },
    sdr_config: { ring_seconds: 10, read_timeout_sec: 10 }, bpf_type: 'SAWBIRD_H1_BAREBONES', bpf_config: { gpio_pin_power: 18, gpio_pin_load: null } };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const data = { local: { dishes: [clone(dish)], digitisers: [clone(digitiser)] },
    other: { dishes: [clone(dish)], digitisers: [{ ...clone(digitiser), local_host: 'other-host' }] } };
  let profile = 'local';
  const versions = { dishes: 1, digitisers: 1 };
  async function api(resource, options = {}) {
    calls.push({ resource, ...clone(options) });
    if (options.profile && options.profile !== profile) throw Object.assign(new Error('Profile changed'), { code: 'profile_changed' });
    if (resource === 'profiles') return { payload: { profile, profiles: Object.keys(data).map((profile) => ({ profile, available: true })) } };
    if (resource === 'profile') { profile = options.body.profile; return { payload: { profile } }; }
    const [kind, id] = resource.split('/');
    const singular = kind === 'dishes' ? 'dish' : 'digitiser';
    const key = kind === 'dishes' ? 'dsh_id' : 'dig_id';
    const items = data[profile][kind];
    const etag = () => '"' + kind + '-' + versions[kind] + '"';
    if (options.method && options.etag !== etag()) throw Object.assign(new Error('Stale'), { code: 'version_conflict' });
    if (!options.method && !id) return { payload: { profile, [kind]: clone(items) }, etag: etag() };
    if (options.method === 'POST') {
      items.push(clone(options.body)); versions[kind]++;
      return { payload: { profile, [singular]: clone(options.body) }, etag: etag() };
    }
    const record = items.find((item) => item[key] === id);
    if (!record) throw new Error('Missing definition');
    if (options.method === 'DELETE') {
      items.splice(items.indexOf(record), 1); versions[kind]++;
      return { payload: { profile, deleted_id: id }, etag: etag() };
    }
    if (options.method === 'PATCH') { Object.assign(record, clone(options.body)); versions[kind]++; }
    return { payload: { profile, [singular]: clone(record) }, etag: etag() };
  }
  const dishes = initDishes(doc, win, api);
  const dig = initDigitisers(doc, win);
  return { dishes, dig, win, events, data, calls,
    get: (id) => get('dig-' + id), field: (name) => field('dig', name),
    shared: (id) => get('dish-' + id), dishField: (name) => field('dish', name),
    bump: () => versions.digitisers++, externalProfile: (name) => { profile = name; },
    async open() { await dishes.refresh(); await dishes.admin.activate('digitisers'); } };
}

test('digitisers load on tab selection and save via versioned WEB APIs', async () => {
  const h = harness(); await h.open();
  assert.equal(h.dig.state.loaded, true);
  assert.equal(h.field('dig_id').value, 'dig001');
  assert.equal(h.field('dig_id').readOnly, true);
  assert.match(h.get('rows').innerHTML, /192.168.2.104/);
  h.field('gain').value = '24'; h.field('sdr_config').value = '{"ring_seconds":12}';
  await h.dig.save({ preventDefault() {} });
  const write = h.calls.find((call) => call.method === 'PATCH');
  assert.equal(write.resource, 'digitisers/dig001');
  assert.equal(write.profile, 'local'); assert.equal(write.etag, '"digitisers-1"');
  assert.equal(write.body.gain, 24); assert.equal(write.body.sdr_config.ring_seconds, 12);
  assert.equal(write.body.bpf_config.gpio_pin_load, null);
  assert.equal(h.dig.dirty(), false);
});

test('create and confirmed delete update digitiser rows', async () => {
  const h = harness(); await h.open(); await h.get('new').emit('click');
  assert.equal(h.field('dig_id').readOnly, false);
  assert.equal(h.get('delete').hidden, true);
  h.field('dig_id').value = 'dig004';
  await h.dig.save({ preventDefault() {} });
  assert.equal(h.dig.state.selected, 'dig004');
  assert.equal(h.field('dig_id').readOnly, true);
  h.win.confirm = () => false; await h.get('delete').emit('click');
  assert.equal(h.data.local.digitisers.length, 2);
  h.win.confirm = () => true; await h.get('delete').emit('click');
  assert.equal(h.data.local.digitisers.length, 1);
  assert.equal(h.dig.state.selected, 'dig001');
  await h.get('delete').emit('click');
  assert.equal(h.get('form').hidden, true);
  assert.equal(h.dig.state.items.length, 0);
});

test('shared profile changes and refresh check unfinished edits in both editors', async () => {
  const h = harness(); await h.open();
  h.field('sdr_config').value = '{broken'; // Invalid input is still a draft.
  h.win.confirm = () => false;
  h.shared('profile').value = 'other'; await h.dishes.switchProfile();
  assert.equal(h.dishes.state.profile, 'local');
  assert.equal(h.field('sdr_config').value, '{broken');
  const before = h.calls.length; await h.dishes.refresh();
  assert.equal(h.calls.length, before);
  let prevented = false; h.events.beforeunload({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  h.win.confirm = () => true;
  h.shared('profile').value = 'other'; await h.dishes.switchProfile();
  assert.equal(h.dig.state.profile, 'other');
  assert.equal(h.field('local_host').value, 'other-host');
  assert.equal(h.dig.dirty(), false);
  // A dirty dish also guards the shared profile while Digitisers is active.
  await h.dishes.admin.activate('dishes'); h.dishField('short_desc').value = 'Unsaved dish';
  await h.dishes.admin.activate('digitisers'); h.win.confirm = () => false;
  h.shared('profile').value = 'local'; await h.dishes.switchProfile();
  assert.equal(h.dig.state.profile, 'other');
});

test('tab changes retain drafts; JSON and type changes are handled before saving', async () => {
  const h = harness(); await h.open();
  h.field('sdr_config').value = '[]';
  await h.dishes.admin.activate('dishes'); await h.dishes.admin.activate('digitisers');
  assert.equal(h.field('sdr_config').value, '[]');
  const before = h.calls.filter((call) => call.method === 'PATCH').length;
  await h.dig.save({ preventDefault() {} });
  assert.equal(h.calls.filter((call) => call.method === 'PATCH').length, before);
  assert.match(h.shared('message').textContent, /valid JSON object/);
  h.field('sdr_type').value = 'soapy'; await h.field('sdr_type').emit('change');
  assert.equal(h.field('sdr_config').value, '{}');
  h.field('temp_type').value = 'none'; await h.field('temp_type').emit('change');
  assert.equal(h.field('temp_config').value, '{}');
  h.field('bpf_type').value = 'UNKNOWN'; await h.field('bpf_type').emit('change');
  assert.equal(h.dig.payload().bpf_config, null);
});

test('conflicts retain the digitiser draft and prevent wrong-profile writes', async () => {
  const h = harness(); await h.open();
  h.field('gain').value = '31'; h.bump();
  await h.dig.save({ preventDefault() {} });
  assert.equal(h.field('gain').value, '31');
  assert.equal(h.dig.dirty(), true);
  assert.match(h.shared('message').textContent, /Your edits are kept/);
  h.externalProfile('other');
  await h.get('delete').emit('click');
  assert.equal(h.data.local.digitisers.length, 1);
  assert.equal(h.data.other.digitisers.length, 1);
  assert.match(h.shared('message').textContent, /Another session/);
});

test('digitiser rows support click and keyboard selection, filter and escaped display', async () => {
  const h = harness();
  h.data.local.digitisers.push({ ...defaults(), dig_id: 'dig002', local_host: '<img onerror="bad">' });
  await h.open();
  assert.match(h.get('rows').innerHTML, /&lt;img/);
  assert.doesNotMatch(h.get('rows').innerHTML, /<img/);
  await h.get('rows').emit('click', { target: { closest: () => ({ dataset: { digitiserId: 'dig002' } }) } });
  assert.equal(h.dig.state.selected, 'dig002');
  await h.get('rows').emit('keydown', { type: 'keydown', key: 'Enter', target: { closest: () => ({ dataset: { digitiserId: 'dig001' } }) } });
  assert.equal(h.dig.state.selected, 'dig001');
  h.get('filter').value = 'dig002'; await h.get('filter').emit('input');
  assert.match(h.get('rows').innerHTML, /dig002/);
  assert.doesNotMatch(h.get('rows').innerHTML, /data-digitiser-id="dig001"/);
});
