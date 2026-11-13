const test = require('node:test');
const assert = require('node:assert/strict');
const { init, info, degrees } = require('../../ui/frontend_prototype/dish_status.js');
const { applications, detail } = require('../../ui/frontend_prototype/status.js');
class Element {
  constructor() { this.value = ''; this.listeners = {}; this.classList = { toggle() {} }; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
function fixture(fetcher) {
  const nodes = new Map(), get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const win = { DmdSession: { can_edit: true }, setTimeout, clearTimeout, addEventListener() {} };
  const control = init({ getElementById: get }, win, fetcher);
  const dish = { dsh_id: 'dish002', short_desc: '<dish>', mode: { value: 'STANDBY_FP' }, capability: { value: 'OPERATE_FULL' },
    desired_altaz: { alt: 90, az: 0 }, pointing_altaz: { alt: 89.5, az: 1.25 }, tgt_id: 'obs001_1',
    tgt_pec: [{ tgt_id: 'obs001_1', alt_rms: 0, az_rms: 2 }], last_err_dt: { value: '2026-11-06T12:00:00Z' }, last_err_msg: '<error>' };
  const data = { tm_connected: true, stale: false, profile: 'local', telescope_model: { dsh_mgr: { id: 'dm001', app: {}, dish_store: { dish_list: [dish] } } } };
  control.open('dish002', data);
  return { control, data, dish, win, get: id => get('dish-status-' + id) };
}
test('dish table is alphabetical, has compact diagnostics and escaped engineering fields', () => {
  const { data, dish } = fixture();
  Object.assign(dish, { latitude: 53.187052, longitude: -2.256079, height: 88,
    diameter: 0.7, fd_ratio: 0.37, feed_type: { value: 'H3T_1420' }, dig_id: 'dig001', driver_type: { value: 'MD01' }, driver_failures: 370 });
  data.telescope_model.dsh_mgr.dish_store.dish_list.push({ ...dish, dsh_id: 'dish001' });
  const html = detail(applications(data.telescope_model)[0]);
  assert.ok(html.indexOf('data-status-dish="dish001"') < html.indexOf('data-status-dish="dish002"'));
  for (const value of ['status-diagnostics-grid', 'Capability', 'Weather', '2026-11-06 12:00:00 UTC', '&lt;error&gt;']) assert.ok(html.includes(value));
  assert.doesNotMatch(html, /<th>(Diameter \[m\]|F\/D|Digitiser)<\/th>/);
  assert.match(html, /<th>Dish \/ Description<\/th><th>Feed<\/th><th>Driver type<\/th><th>Mode<\/th>/);
  assert.match(html, /<td>H3T_1420<\/td><td>MD01<\/td>/);
  assert.match(html, /<th>Driver Failures<\/th><th>Health<\/th>/);
  assert.match(html, /<td>370<\/td><td><span class="zk-chip/);
  assert.doesNotMatch(info(dish), /Consecutive driver failures|Driver Failures/);
  assert.match(info(dish), /Diameter \[m\]<\/dt><dd>0\.7/);
  assert.match(info(dish), /F\/D<\/dt><dd>0\.37/);
  assert.doesNotMatch(info(dish), /<dt>(Feed|Driver)<\/dt>/);
  assert.match(info(dish), /Digitiser<\/dt><dd>dig001/);
  assert.doesNotMatch(html, /Lat, Long, Height|<th>Height \[m\]<\/th>|53\.187052/);
  assert.match(info(dish), /Latitude \[°\]<\/dt><dd>53\.187052/);
  assert.match(info(dish), /Longitude \[°\]<\/dt><dd>-2\.256079/);
  assert.match(info(dish), /Height \[m\]<\/dt><dd>88/);
  assert.doesNotMatch(html, /<error>|<dish>/);
  assert.equal(degrees(0), '0.0000'); assert.equal(degrees(null), '—');
  dish.tgt_pec[0].alt_rms = 0.0419512835487255;
  dish.tgt_pec[0].az_rms = 0.07450711831930694;
  dish.velocity_altaz = {alt: 0.09999999999999432, az: 0.5};
  assert.match(info(dish), /PEC altitude RMS \[arcsec\]<\/dt><dd>0\.042<\/dd>/);
  assert.match(info(dish), /PEC azimuth RMS \[arcsec\]<\/dt><dd>0\.075<\/dd>/);
  assert.match(info(dish), /Altitude velocity \[°\/s\]<\/dt><dd>0\.100<\/dd>/);
  assert.match(info(dish), /Azimuth velocity \[°\/s\]<\/dt><dd>0\.500<\/dd>/);
});
test('model refresh updates actual pointing while preserving a desired selection', () => {
  const { control, data, dish, get } = fixture();
  get('mode').value = 'MAINTENANCE';
  dish.mode = { value: 'STOW' }; dish.pointing_altaz.alt = 88;
  control.update(data);
  assert.equal(get('mode').value, 'MAINTENANCE');
  const overview = detail(applications(data.telescope_model)[0]);
  assert.match(overview, /<th>Pointing<\/th><th>Actual AltAz<\/th>/);
  assert.match(overview, /<td>STOW<\/td>/);
  assert.match(overview, /<td>88.0°, 1.3°<\/td>/);
  assert.equal(get('az-desired').textContent, '0.0000');
  assert.equal(get('error-time').textContent, '2026-11-06 12:00:00 UTC');
});

test('shared weather is displayed above the table and preserves any reported alarm', () => {
  const { data, dish } = fixture();
  const manager = data.telescope_model.dsh_mgr;
  manager.weather_store = { weather_enabled: true };
  manager.dish_store.dish_list.push({ ...dish, dsh_id: 'dish003' });
  const summary = () => {
    const html = detail(applications(data.telescope_model)[0]);
    assert.doesNotMatch(html, /<th>Weather<\/th>/);
    return html.split('class="status-kv status-weather-summary"')[1].split('</dl>')[0];
  };
  manager.dish_store.dish_list.forEach(item => { item.weather_alarm = false; });
  assert.match(summary(), /is-live">Clear/);
  manager.dish_store.dish_list[1].weather_alarm = true;
  assert.match(summary(), /is-bad">ALARM/);
  manager.dish_store.dish_list[1].weather_alarm = undefined;
  assert.doesNotMatch(summary(), />Clear</);
  manager.weather_store.weather_enabled = false;
  assert.match(summary(), /Not monitored/);
  assert.doesNotMatch(info(dish), /Weather alarm/);
});
test('settings use TM configuration; STOP targets DM once and remains available while settings wait', async () => {
  const calls = []; let resolve;
  const { control, get, dish } = fixture(async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    if (url.startsWith('/api/dish-control')) await new Promise(done => { resolve = done; });
    return { ok: true, json: async () => ({ status: 'success', message: 'Accepted' }) };
  });
  get('mode').value = 'STOW';
  const setting = control.send('mode');
  await control.send('mode'); // No repeated submission.
  assert.equal(get('mode-send').disabled, true); assert.equal(get('stop').disabled, false);
  await control.send('stop'); resolve(); await setting;
  assert.deepEqual(calls, [{ url: '/api/dish-control/dish002', body: { mode: 'STOW' } }, { url: '/api/commands', body: { app: 'dm', cmd: 'stop', value: 'dish002' } }]);
  assert.doesNotMatch(get('result').textContent, /Awaiting a fresh dish report/); // Latest STOP reply remains visible.
  assert.equal(dish.mode.value, 'STANDBY_FP');
});
test('viewer cannot send; stale snapshots disable settings but permit direct STOP; errors never replay', async () => {
  let calls = 0;
  const { control, win, data, get } = fixture(async () => { calls++; throw new Error('timeout'); });
  win.DmdSession.can_edit = false; control.update(data);
  await control.send('mode'); await control.send('stop'); assert.equal(calls, 0);
  assert.equal(get('stop').disabled, true);
  win.DmdSession.can_edit = true; data.stale = true; control.update(data);
  assert.equal(get('mode-send').disabled, true); assert.equal(get('stop').disabled, false);
  await control.send('capability'); await control.send('stop');
  assert.equal(calls, 1); assert.match(get('result').textContent, /Outcome unknown/);
});
test('responses for a previously selected dish do not appear on another dish', async () => {
  let resolve;
  const { control, data, dish, get } = fixture(async () => {
    await new Promise(done => { resolve = done; }); return { ok: true, json: async () => ({ status: 'success', message: 'Old response' }) };
  });
  const pending = control.send('stop');
  data.telescope_model.dsh_mgr.dish_store.dish_list.push({ ...dish, dsh_id: 'dish003' });
  control.open('dish003', data); resolve(); await pending;
  assert.equal(get('result').textContent, '');
});

test('polar projection puts north up, east right, horizon at edge and zenith at centre', () => {
  const { polarPoint } = require('../../ui/frontend_prototype/dish_status.js');
  const near = (point, x, y) => { assert.ok(Math.abs(point.x - x) < 1e-9); assert.ok(Math.abs(point.y - y) < 1e-9); };
  near(polarPoint({ alt: 0, az: 0 }), 140, 36);
  near(polarPoint({ alt: 0, az: 90 }), 244, 140);
  near(polarPoint({ alt: 0, az: 180 }), 140, 244);
  near(polarPoint({ alt: 0, az: 270 }), 36, 140);
  near(polarPoint({ alt: 90, az: 23 }), 140, 140);
  near(polarPoint({ alt: 45, az: -90 }), 88, 140);
  near(polarPoint({ alt: 45, az: 450 }), 192, 140);
  for (const position of [null, {}, { alt: null, az: 0 }, { alt: 0, az: NaN },
    { alt: Infinity, az: 0 }, { alt: -1, az: 0 }, { alt: 91, az: 0 }]) assert.equal(polarPoint(position), null);
});
test('polar plot keeps coincident markers distinct and labels missing, below-horizon and stale values', () => {
  const { polarPlot } = require('../../ui/frontend_prototype/dish_status.js');
  let html = polarPlot({ pointing_altaz: { alt: 90, az: 0 }, desired_altaz: { alt: 90, az: 180 } });
  assert.match(html, /class="dish-polar-actual"/); assert.match(html, /class="dish-polar-desired"/);
  assert.match(html, /Actual: Alt 90.0000°, Az 0.0000°/);
  assert.match(html, /Desired: Alt 90.0000°, Az 180.0000°/);
  html = polarPlot({ pointing_altaz: null, desired_altaz: { alt: -5, az: 45 } }, true);
  assert.match(html, /Actual: unavailable/); assert.match(html, /Desired: below horizon/); assert.match(html, /Stale snapshot/);
  assert.doesNotMatch(html, /class="dish-polar-(actual|desired)"|NaN|Infinity/);
});
test('polar markers refresh from reported coordinates without changing mode drafts', () => {
  const { control, data, dish, get } = fixture();
  get('mode').value = 'STOW';
  const before = get('polar').innerHTML;
  dish.pointing_altaz = { alt: 45, az: 270 }; control.update(data);
  assert.notEqual(get('polar').innerHTML, before);
  assert.match(get('polar').innerHTML, /Actual: Alt 45.0000°, Az 270.0000°/);
  assert.equal(get('mode').value, 'STOW');
});

test('slew trail retains five previous positions and ignores duplicate, stale and older samples', () => {
  const { control, data, dish, get } = fixture();
  dish.pointing_state = { value: 'SLEW' };
  for (let index = 0; index < 8; index++) {
    dish.pointing_altaz = { alt: 40 + index, az: 20 + index };
    dish.pointing_altaz_dt = { value: `2026-11-07T12:00:0${index}Z` };
    control.update(data);
  }
  const trail = () => get('polar').innerHTML.match(/<polyline class="dish-polar-trail" points="([^"]+)"/)[1];
  const before = trail();
  assert.equal(before.split(' ').length, 6);
  assert.equal((get('polar').innerHTML.match(/class="dish-polar-history-point"/g) || []).length, 5);
  assert.match(get('polar').innerHTML, /Slew trail · previous 5 positions/);
  const { polarPoint } = require('../../ui/frontend_prototype/dish_status.js');
  const oldest = polarPoint({ alt: 42, az: 22 });
  assert.equal(before.split(' ')[0], oldest.x.toFixed(3) + ',' + oldest.y.toFixed(3));
  control.update(data); assert.equal(trail(), before);
  dish.pointing_altaz = { alt: 60, az: 90 }; data.stale = true;
  dish.pointing_altaz_dt = { value: '2026-11-07T12:00:08Z' };
  control.update(data); assert.equal(trail(), before);
  data.stale = false; dish.pointing_altaz_dt = { value: '2026-11-07T12:00:01Z' };
  control.update(data); assert.equal(trail(), before);
  dish.pointing_state = { value: 'READY' }; control.update(data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail|dish-polar-history-point/);
  dish.pointing_state = { value: 'SLEW' }; control.update(data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail/);
});

test('slew trail stays within the selected dish and target, and skips overlapping positions', () => {
  const { control, data, dish, get } = fixture();
  dish.pointing_state = 'SLEW';
  dish.pointing_altaz = { alt: 45, az: 10 }; control.update(data);
  control.update(data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail/);
  dish.pointing_altaz = { alt: 46, az: 15 }; control.update(data);
  assert.match(get('polar').innerHTML, /Slew trail · previous 1 position/);
  dish.tgt_id = 'new-target'; control.update(data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail/);
  dish.pointing_altaz = { alt: 47, az: 20 }; control.update(data);
  assert.match(get('polar').innerHTML, /dish-polar-trail/);
  data.telescope_model.dsh_mgr.dish_store.dish_list.push({ ...dish, dsh_id: 'dish003' });
  control.open('dish003', data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail/);
  control.open('dish002', data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail/);
  dish.pointing_altaz = null; control.update(data);
  assert.doesNotMatch(get('polar').innerHTML, /dish-polar-trail|NaN/);
});
