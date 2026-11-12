const test = require('node:test');
const assert = require('node:assert/strict');
const { init, observations, scansFor, completed, historyObservations, historyStatus, rows, scanDetails, timestamp, duration } = require('../../ui/frontend_prototype/schedule.js');
const en = value => ({ _type: 'enum.IntEnum', value });
const dt = value => ({ _type: 'datetime', value });
const now = Date.parse('2026-11-08T15:00:00Z');
const obs = (id, status = 'EMPTY', start = '2026-11-08T16:00:00Z') => ({
  obs_id: id, title: 'Galactic rotation', dsh_id: 'dish003', obs_state: en(status),
  scheduling_block_start: dt(start), scheduling_block_end: dt('2026-11-08T23:00:00Z'),
  estimated_observation_duration: '00:07:30', created: dt('2026-11-08T10:00:00Z'), user_email: 'observer@example.org',
  tgt_idx: 0, tgt_scan: 0, target_configs: [{ tgt_idx: 0 }], targets: [{ tgt_idx: 0, id: 'Galactic longitude 20' }],
  target_scans: [{ tgt_idx: 0, scans: [
    { tgt_idx: 0, freq_scan: 0, scan_iter: 0, status: en('EMPTY'), scan_type: en('LOAD'), dig_id: 'dig001', center_freq: 1420000000, gain: 17, duration: 10 },
    { tgt_idx: 0, freq_scan: 0, scan_iter: 1, status: en('WIP'), scan_type: en('SKY'), dig_id: 'dig001', center_freq: 1420000000, gain: 17, duration: 60 },
  ] }],
});
const model = list => ({ oda: { obs_store: { obs_list: list } } });
const archive = (item, folder = '.') => ({ ...item, id: item.obs_id, filename: item.obs_id + '-obs.json',
  folder, dish: item.dsh_id, state: item.obs_state, started_at: item.scheduling_block_start, revision: '1' });

test('disk history excludes live IDs, deduplicates copies and sorts newest first', () => {
  const queued = obs('queued'), running = obs('running', 'SCANNING'), finished = obs('finished', 'IDLE');
  finished.tgt_idx = 1;
  const old = archive(obs('old', 'IDLE', '2026-11-07T13:00:00Z'));
  const duplicate = { ...archive(finished, 'new-copy'), revision: '2', completed: true };
  const result = historyObservations([archive(queued), archive(running), old, archive(finished), duplicate], model([queued, running, finished]), now);
  assert.deepEqual(result.map(item => item.obs_id), ['finished', 'old']);
  assert.equal(result[0].folder, 'new-copy');
  assert.equal(historyStatus(result[0]), 'COMPLETE');
  assert.equal(historyStatus({ obs_state: 'SCANNING' }), 'INCOMPLETE');
  assert.equal(historyStatus({ obs_state: 'COMPLETE' }), 'COMPLETE');
  assert.equal(historyStatus({ obs_state: 'ABORTED' }), 'ABORTED');
  assert.equal(historyStatus({ error: 'invalid file' }), 'UNAVAILABLE');
});

test('running observations precede the pending queue, ordered by scheduled start', () => {
  const list = observations(model([
    obs('later'), obs('earlier', 'IDLE', '2026-11-08T15:30:00Z'),
    obs('scanning', 'SCANNING'), obs('configuring', 'CONFIGURING', '2026-11-08T14:00:00Z'),
    obs('aborted', 'ABORTED'), obs('fault', 'FAULT'),
  ]), now);
  assert.deepEqual(list.map(item => item.obs_id), ['configuring', 'scanning', 'earlier', 'later']);
});

test('completed observations retained in the model are excluded from the live queue', () => {
  const byCursor = obs('cursor', 'IDLE'); byCursor.tgt_idx = 1;
  const byScans = obs('scans', 'IDLE');
  byScans.target_scans[0].scans.forEach(scan => { scan.status = en('COMPLETE'); });
  const expired = obs('expired'); expired.scheduling_block_end = dt('2026-11-08T14:00:00Z');
  const running = obs('still-running', 'SCANNING'); running.scheduling_block_end = expired.scheduling_block_end;
  const unprepared = obs('not-prepared'); unprepared.target_scans = [];
  assert.ok(completed(byCursor)); assert.ok(completed(byScans));
  assert.deepEqual(observations(model([byCursor, byScans, expired, running, unprepared]), now).map(item => item.obs_id), ['still-running', 'not-prepared']);
  assert.match(scanDetails(unprepared), /TM has not prepared the scan list/);
});

test('missing queue is distinguished from an empty queue and numeric enum values are supported', () => {
  assert.equal(observations({}, now), null);
  assert.deepEqual(observations(model([]), now), []);
  const item = obs('numeric'); item.obs_state = en(5); item.target_scans[0].scans[0].status = en(3);
  assert.equal(observations(model([item]), now).length, 1);
  assert.match(scanDetails(item), />COMPLETE</);
  assert.match(rows([item], new Set()), />SCANNING</);
});

test('observation attributes match the queue sheet and unsafe text is escaped', () => {
  const item = obs('obs-<script>'); item.title = '<img src=x onerror=alert(1)>'; item.user_email = 'a&b@example.org';
  const html = rows([item], new Set([item.obs_id]));
  for (const text of ['dish003', '00:07:30', '2026-11-08 16:00:00', '2026-11-08 23:00:00', '2026-11-08 10:00:00', 'a&amp;b@example.org']) assert.ok(html.includes(text));
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /obs-&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|<img /);
});

test('expanded scans retain individual statuses, tuning, sample times and current scan', () => {
  const item = obs('obs001', 'SCANNING'); item.tgt_scan = 1;
  item.target_scans[0].scans[0].status = en('COMPLETE');
  item.target_scans[0].scans[1].read_start = dt('2026-11-08T15:00:02Z');
  assert.deepEqual(scansFor(item).map(scan => scan.current), [false, true]);
  const html = scanDetails(item);
  for (const text of ['1 of 2 complete', 'Galactic longitude 20', '>LOAD<', '>SKY<', '>COMPLETE<', '>WIP<', '1420.000', 'dig001', '2026-11-08 15:00:02', '>Current<']) assert.ok(html.includes(text), text);
  assert.equal(timestamp(null), '—'); assert.equal(timestamp('invalid'), '—');
  assert.equal(timestamp(dt('2026-11-08T16:00:00+01:00')), '2026-11-08 15:00:00');
  assert.equal(duration(450), '00:07:30'); assert.equal(duration(90000), '25:00:00');
});


test('only completed scans offer SPR downloads with encoded archive and profile context', () => {
  const item = obs('live001', 'SCANNING');
  item.target_scans[0].scans[0].status = en('COMPLETE');
  const live = scanDetails(item, { profile: 'rotation' });
  assert.equal((live.match(/class="zk-icon-btn schedule-scan-download"/g) || []).length, 1);
  assert.match(live, /\/api\/web\/observations\/live001\/scans\/0\/0\/0\/spr\?profile=rotation/);
  assert.match(live, /aria-label="Download SPR CSV for scan 0 \/ 0 \/ 0"/);
  const historic = scanDetails(item, { filename: 'saved-file-obs.json', folder: '2026/November', profile: 'local' });
  assert.match(historic, /data-scan-signal="[^"]*saved-file\/scans\/0\/0\/0\/signal/);
  assert.match(historic, /saved-file\/scans\/0\/0\/0\/spr\?folder=2026%2FNovember&amp;profile=local/);
  item.target_scans[0].scans[0].status = en('ABORTED');
  assert.doesNotMatch(scanDetails(item), /schedule-scan-download/);
});

class Element {
  constructor() { this.listeners = {}; this.innerHTML = ''; this.textContent = ''; this.scrollTop = 0; this.scrollLeft = 0; this.disabled = false; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelectorAll() { return []; }
  removeAttribute(name) { delete this[name]; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
function harness({ history = true } = {}) {
  const elements = new Map(), calls = [], events = {};
  const get = id => { if (!history && id.startsWith('schedule-history-')) return null; if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const h = { get: id => get('schedule-' + id), calls, events, error: null, historyError: null,
    profile: 'rotation', entries: [], saved: new Map(),
    data: { telescope_model: model([obs('obs001', 'SCANNING')]), profile: 'rotation', snapshot_at: '2026-11-08T15:00:00Z' } };
  h.downloaded = []; h.blobs = new Map();
  h.controller = init({ getElementById: get, body: { appendChild() {} }, createElement() {
    return { click() { h.downloaded.push({ filename: this.download, blob: h.blobs.get(this.href) }); }, remove() {} };
  } }, {
    URL: { createObjectURL(blob) { h.blobs.set('blob:test', blob); return 'blob:test'; }, revokeObjectURL(url) { h.blobs.delete(url); } },
    setTimeout(fn, delay) { return setTimeout(fn, delay === 1000 ? 0 : delay); }, clearTimeout,
    setInterval(fn) { h.poll = fn; return 1; }, clearInterval() { h.cleared = true; },
    addEventListener(name, fn) { events[name] = fn; },
  }, async (url, options) => {
    calls.push({ url, options });
    if (url.includes('/spr?')) return h.spectrum;
    if (url.includes('/signal?')) return h.signal;
    if (url === '/api/web/profile') return { ok: true, json: async () => ({ status: 'success', payload: { profile: h.profile, scan_store_dir: '~/samples/' + h.profile } }) };
    if (url === '/api/web/observations') {
      if (h.historyError) throw new Error(h.historyError);
      return { ok: true, json: async () => ({ status: 'success', payload: { observations: h.entries } }) };
    }
    if (url.startsWith('/api/web/observations/')) {
      if (h.pendingDetail) return h.pendingDetail;
      const saved = h.saved.get(url);
      return { ok: !!saved, json: async () => saved ? { status: 'success', payload: { observation: saved } } : { status: 'error', message: 'File unavailable' } };
    }
    if (h.error) throw new Error(h.error);
    return { ok: true, json: async () => ({ status: 'success', payload: h.data }) };
  });
  h.toggle = id => h.get('observations').listeners.click({ target: { closest: selector => ['[data-scan-download]', '[data-scan-signal]'].includes(selector) ? null : ({ dataset: { scheduleObservation: id } }) } });
  h.toggleHistory = key => h.get('history-observations').listeners.click({ target: { closest: selector => ['[data-history-retry]', '[data-scan-download]', '[data-scan-signal]'].includes(selector) ? null : ({ dataset: { scheduleObservation: key } }) } });
  return h;
}

test('expansion survives polling and scroll position is retained as scan status changes', async () => {
  const h = harness(); await new Promise(setImmediate);
  assert.equal(h.calls[0].url, '/api/web/telescope/model');
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  h.toggle('obs001');
  h.get('scroll').scrollTop = 160; h.get('scroll').scrollLeft = 200;
  h.data.telescope_model.oda.obs_store.obs_list[0].target_scans[0].scans[0].status = en('COMPLETE');
  await h.controller.refresh();
  assert.ok(h.controller.state.expanded.has('obs001'));
  assert.match(h.get('observations').innerHTML, /1 of 2 complete/);
  assert.equal(h.get('scroll').scrollTop, 160); assert.equal(h.get('scroll').scrollLeft, 200);
  h.toggle('obs001'); assert.doesNotMatch(h.get('observations').innerHTML, /schedule-expanded/);
});

test('outages label retained rows as stale and finished rows disappear on the next snapshot', async () => {
  const h = harness(); await new Promise(setImmediate);
  h.toggle('obs001'); h.error = 'TM disconnected';
  await h.controller.refresh();
  assert.match(h.get('freshness').textContent, /Showing the last snapshot as stale/);
  assert.match(h.get('observations').innerHTML, /obs001/);
  h.error = null; h.data.stale = false;
  h.data.telescope_model.oda.obs_store.obs_list[0].tgt_idx = 1;
  await h.controller.refresh();
  assert.equal(h.get('count').textContent, '0');
  assert.match(h.get('observations').innerHTML, /No observations are queued or running/);
  assert.equal(h.controller.state.expanded.size, 0);
  h.events.pagehide(); const count = h.calls.length; await h.controller.refresh();
  assert.equal(h.calls.length, count); assert.ok(h.cleared);
});

test('historic scans load only on expansion, use the selected profile and work without TM', async () => {
  const h = harness(); await new Promise(setImmediate);
  h.error = 'TM disconnected'; await h.controller.refresh();
  const saved = obs('saved001', 'READY');
  saved.target_scans[0].scans.forEach(scan => { scan.status = en('COMPLETE'); });
  h.entries = [{ ...archive(saved, '2026/November'), completed: true }];
  h.saved.set('/api/web/observations/saved001?folder=2026%2FNovember', { ...saved, _type: 'ObsModel' });
  await h.controller.refreshHistory(true);
  assert.equal(h.get('history-count').textContent, '1');
  assert.match(h.get('history-observations').innerHTML, />COMPLETE</);
  assert.equal(h.calls.filter(call => call.url.startsWith('/api/web/observations/')).length, 0);
  h.toggleHistory('2026/November/saved001-obs.json');
  await new Promise(setImmediate);
  const html = h.get('history-observations').innerHTML;
  assert.match(html, /2 of 2 complete/);
  assert.doesNotMatch(html, />Current</);
  assert.equal(h.calls.at(-1).options.headers['X-Config-Profile'], 'rotation');
  const calls = h.calls.length;
  h.toggleHistory('2026/November/saved001-obs.json'); h.toggleHistory('2026/November/saved001-obs.json');
  await new Promise(setImmediate);
  assert.equal(h.calls.length, calls);
});

test('disk outages retain the previous catalogue and changed file revisions reload expanded scans', async () => {
  const h = harness(); await new Promise(setImmediate);
  const saved = { ...obs('saved001', 'ABORTED'), _type: 'ObsModel' };
  h.entries = [archive(saved)]; h.saved.set('/api/web/observations/saved001?folder=.', saved);
  await h.controller.refreshHistory(true); h.toggleHistory('./saved001-obs.json'); await new Promise(setImmediate);
  h.get('history-scroll').scrollTop = 140;
  h.historyError = 'Disk unavailable'; await h.controller.refreshHistory(true);
  assert.match(h.get('history-freshness').textContent, /Showing the last disk catalogue/);
  assert.match(h.get('history-observations').innerHTML, /saved001/);
  h.historyError = null; h.entries[0].revision = '2';
  saved.target_scans[0].scans[0].status = en('COMPLETE');
  await h.controller.refreshHistory(true); await new Promise(setImmediate);
  assert.match(h.get('history-observations').innerHTML, /1 of 2 complete/);
  assert.equal(h.get('history-scroll').scrollTop, 140);
  assert.equal(h.calls.filter(call => call.url.startsWith('/api/web/observations/')).length, 2);
});

test('a late scan response from an old archive profile cannot populate the new history', async () => {
  const h = harness(); await new Promise(setImmediate);
  h.entries = [archive(obs('saved001'))]; await h.controller.refreshHistory(true);
  let resolve;
  h.pendingDetail = new Promise(done => { resolve = done; });
  h.toggleHistory('./saved001-obs.json');
  h.profile = 'other'; await h.controller.refreshHistory(true);
  resolve({ ok: true, json: async () => ({ status: 'success', payload: { observation: { ...obs('saved001'), _type: 'ObsModel', title: 'OLD PROFILE' } } }) });
  await new Promise(setImmediate);
  assert.equal(h.controller.state.history.profile, 'other');
  assert.equal(h.controller.state.history.details.size, 0);
  assert.equal(h.controller.state.history.expanded.size, 0);
  assert.doesNotMatch(h.get('history-observations').innerHTML, /OLD PROFILE/);
});

test('download saves validated CSV bytes under the original filename', async () => {
  const h = harness(); await new Promise(setImmediate);
  h.spectrum = { ok: true, headers: new Headers({ 'Content-Type': 'text/csv; charset=utf-8',
    'Content-Disposition': 'attachment; filename="recorded-scan-spr.csv"' }), blob: async () => new Blob(['1,2\n3,4\n']) };
  const button = { dataset: { scanDownload: '/api/web/observations/obs001/scans/0/0/0/spr?profile=rotation' } };
  await h.controller.downloadScan(button, true);
  assert.equal(h.downloaded[0].filename, 'recorded-scan-spr.csv');
  assert.equal(await h.downloaded[0].blob.text(), '1,2\n3,4\n');
  assert.match(h.get('history-download-result').textContent, /Downloaded recorded-scan-spr.csv/);
  assert.equal(button.disabled, false);
});

test('missing files and JSON replies produce an inline error and never a JSON download', async () => {
  const h = harness(); await new Promise(setImmediate);
  for (const ok of [false, true]) {
    h.spectrum = { ok, headers: new Headers({ 'Content-Type': 'application/json' }),
      json: async () => ({ status: 'error', message: 'The scan spectrum CSV was not found in this archive' }) };
    await h.controller.downloadScan({ dataset: { scanDownload: '/api/web/observations/obs001/scans/0/0/0/spr?profile=rotation' } }, true);
    assert.match(h.get('history-download-result').textContent, /SPR download failed: The scan spectrum CSV was not found/);
    assert.equal(h.downloaded.length, 0);
  }
});


test('completed scans have a Signal column before SPR and PNG preview uses the original filename', async () => {
  const item = obs('obs001', 'SCANNING');
  item.target_scans[0].scans[0].status = en('COMPLETE');
  const html = scanDetails(item, { profile: 'rotation' });
  assert.match(html, />Signal<\/th><th[^>]*>SPR/);
  assert.equal((html.match(/data-scan-signal=/g) || []).length, 1);
  const h = harness(); await new Promise(setImmediate);
  const blob = new Blob(['png']);
  h.signal = { ok: true, headers: new Headers({ 'Content-Type': 'image/png',
    'Content-Disposition': 'attachment; filename="recorded-sigfig.png"' }), blob: async () => blob };
  await h.controller.previewSignal({ dataset: { scanSignal: '/api/web/observations/obs001/scans/0/0/0/signal?profile=rotation', scanLabel: 'obs001 · 0 / 0 / 0' } });
  assert.equal(h.get('signal-dialog').open, true);
  assert.equal(h.get('signal-image').hidden, false);
  assert.equal(h.get('signal-download').download, 'recorded-sigfig.png');
  assert.equal(h.blobs.get(h.get('signal-download').href), blob);
  h.get('signal-close').listeners.click();
  assert.equal(h.blobs.size, 0);
  assert.equal(h.get('signal-image').src, undefined);
});

test('missing signal PNG shows an error with no download link', async () => {
  const h = harness(); await new Promise(setImmediate);
  h.signal = { ok: false, headers: new Headers(), json: async () => ({ message: 'The scan signal PNG was not found in this archive' }) };
  await h.controller.previewSignal({ dataset: { scanSignal: '/api/web/observations/obs001/scans/0/0/0/signal?profile=rotation' } });
  assert.match(h.get('signal-message').textContent, /not found/);
  assert.equal(h.get('signal-download').hidden, true);
  assert.equal(h.blobs.size, 0);
});


test('closing a signal preview discards a late response and allows reopening', async () => {
  const h = harness(); await new Promise(setImmediate);
  let resolve;
  h.signal = new Promise(done => { resolve = done; });
  const button = { dataset: { scanSignal: '/api/web/observations/obs001/scans/0/0/0/signal?profile=rotation' } };
  const pending = h.controller.previewSignal(button);
  h.get('signal-close').listeners.click();
  resolve({ ok: true, headers: new Headers({ 'Content-Type': 'image/png' }), blob: async () => new Blob(['png']) });
  await pending;
  assert.equal(h.get('signal-dialog').open, false);
  assert.equal(h.blobs.size, 0);
  assert.equal(h.get('signal-download').hidden, true);
  h.signal = { ok: true, headers: new Headers({ 'Content-Type': 'image/png' }), blob: async () => new Blob(['png']) };
  await h.controller.previewSignal(button);
  assert.equal(h.get('signal-dialog').open, true);
  assert.equal(h.get('signal-image').hidden, false);
  h.get('signal-close').listeners.click();
});


test('Schedule without historic pane only polls live snapshots', async () => {
  const h = harness({ history: false }); await new Promise(setImmediate);
  assert.ok(h.calls.some(call => call.url === '/api/web/telescope/model'));
  assert.ok(!h.calls.some(call => call.url === '/api/web/observations' || call.url === '/api/web/profile'));
  await h.controller.refreshHistory(true);
  assert.ok(!h.calls.some(call => call.url === '/api/web/observations'));
});
