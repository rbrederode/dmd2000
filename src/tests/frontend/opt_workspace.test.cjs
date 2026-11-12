// Logic-level UI checks; these use a tiny DOM stub, not a browser renderer.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const schedule = require('../../ui/frontend_prototype/schedule.js');
const source = fs.readFileSync(path.join(__dirname, '../../ui/frontend_prototype/opt.js'), 'utf8');

async function workspace({ sharedProfile = null, scanStoreDir = null, overviewAvailable = true, savedPreferences = {}, perSecondAvailable = true, individualFrequencyAvailable = true, velocityAvailable = true } = {}) {
  const elements = new Map();
  const timers = new Map();
  let nextTimer = 0;
  let online = true;
  const calls = [];
  const windowEvents = new Map();
  const openResponses = new Map();
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
  const get = (id) => { if (id === 'dish-profile') id = 'opt-profile'; if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const html = fs.readFileSync(path.join(__dirname, '../../ui/frontend_prototype/index.html'), 'utf8');
  for (const [name, tag] of [['title', 'h3'], ['description', 'p'], ['details', 'div']]) {
    const markup = html.match(new RegExp(`<${tag} id="opt-pipeline-help-${name}"[^>]*>([\\s\\S]*?)</${tag}>`))[1];
    get(`opt-pipeline-help-${name}`).textContent = markup.replace(/<[^>]*>/g, '');
    get(`opt-pipeline-help-${name}`).innerHTML = markup;
  }
  for (const [name, value] of Object.entries({ baseline: 'none', vmin: '', vmax: '', smoothing: '2', 'lab-beam': '5', kind: 'all', profile: 'default' })) get(`opt-${name}`).value = value;
  if (sharedProfile) get('dish-profile').value = sharedProfile;
  const entries = [
    { id: 'obs-new', name: 'Galactic plane', dish: 'dish003', completed_scans: 3, total_scans: 4, completed_types: { SKY: 2, LOAD: 1 }, completed_integration_s: 135 },
    { id: 'obs-old', name: 'Earlier observation', dish: 'dish002', completed_scans: 1, total_scans: 2, completed_types: { SKY: 1 }, completed_integration_s: 60 },
  ];
  const tabs = Object.fromEntries(['signal', 'frequency', 'velocity', 'timeline', 'lv', 'details'].map(view => { const tab = new Element(); tab.dataset = { optView: view }; return [view, tab]; }));
  for (const axis of ['lon', 'lat']) {
    get('opt-lab-' + axis + '-min').value = '-10';
    get('opt-lab-' + axis + '-max').value = '10';
    get('opt-lab-' + axis + '-step').value = '1';
  }
  get('opt-lab-sweep').checked = true;
  const velocityTab = tabs.velocity;
  get("opt-frequency-overlay").checked = true;
  get("opt-timeline-overlay").checked = true;
  get("opt-timeline-resolution").value = 'scans';
  const context = vm.createContext({
    AbortController, console,
    document: { getElementById: get, querySelectorAll: selector => selector === '[data-opt-view]' ? Object.values(tabs) : [], querySelector: () => new Element() },
    window: { ...(sharedProfile ? {DmdAdmin: {profile: () => get('dish-profile').value, selection: () => ({profile: get('dish-profile').value, scan_store_dir: scanStoreDir}), update() {}, isBusy: () => false}} : {}), addEventListener(name, fn) { windowEvents.set(name, fn); }, DmdSchedule: schedule, DmdScheduleController: {previewSignal() {}, downloadScan() {}}, location: { hash: '' }, setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); } },
    localStorage: { getItem: () => JSON.stringify(savedPreferences), setItem() {} },
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    fetch: async (url, options = {}) => {
      if (!online) throw new TypeError('Network disconnected');
      const body = options.body ? JSON.parse(options.body) : null;
      calls.push({ url, body });
      let data;
      if (url.endsWith('/config')) data = { roots: ['/samples'], directories: ['/samples'], profiles: ['default', 'rotation'], pipeline_overview: overviewAvailable ? {
        title: 'How the pipeline works', overview: 'The spectrum passes through spr → cal → mpr.',
        stages: [{title: '1. spr — Summed Power Spectrum', description: 'Sum FFT powers before calibration.'},
          {title: '2. cal — Calibrated Spectrum', description: 'Process a copy of SPR.'},
          {title: '3. mpr — Mean Power Spectrum', description: 'Average usable calibrated samples, then run QA.'}],
        notes: ['Saved SPR processing does not replay spr steps.']
      } : undefined, available_steps: [
        {step: 'DC_SPIKE', params: {context: 'spr'}},
        {step: 'LOAD', params: {context: 'cal'}},
        {step: 'BANDPASS_FILTER', params: {context: 'cal', ranges_pct: [[0, 100]]}},
        {step: 'RFI_FLAG', description: 'Detect and flag likely RFI outliers without changing measured values.', params: {context: 'cal', threshold: 5, window_size: 21}},
        {step: 'BASELINE', description: 'Subtract a baseline or flatten its slope; preserve LOAD references.', help: {overview: 'Fits a background and subtracts it or flattens its slope; preserve LOAD references.', parameters: {method: 'Choose flatten (default), linear, constant, or none.', ranges_pct: 'Select line-free percentiles of usable channels.'}, notes: ['Flatten retains solar continuum increases.', '<unsafe>']}, params: {context: 'cal', method: 'flatten', ranges_pct: [[0, 10], [90, 100]]}},
        {step: 'QA', params: {context: 'mpr', window_frac: .2}}
      ], pipelines: {default: {default: [{step: 'NOP', params: {context: 'cal'}}]}, rotation: {default: [{step: 'BASELINE', params: {context: 'cal', method: 'linear'}}]}} };
      else if (url.includes('/observations?')) data = { directory: decodeURIComponent(url.split('directory=')[1]), observations: entries.map((item) => item.id), entries };
      else if (url.endsWith('/health')) data = { status: 'ok' };
      else if (url.endsWith('/open')) {
        if (openResponses.has(body.observation)) return await openResponses.get(body.observation);
        data = { id: body.observation, directory: body.directory,
        scans: [{id: 'scan1', kind: 'SKY', status: 'COMPLETE', duration_s: 60}],
        metadata: { _type: 'ObsModel', obs_id: body.observation, targets: [{tgt_idx: 1, id: 'Hydrogen target'}],
          target_scans: [{tgt_idx: 1, scans: [{tgt_idx: 1, freq_scan: 0, scan_iter: 0,
            scan_type: {value: 'SKY'}, status: {value: 'COMPLETE'}, dig_id: 'dig003', center_freq: 1420e6, duration: 60}]}] } };
      }
      else if (url.endsWith('/pipeline')) data = {profile: body.profile, pipeline: body.pipeline, message: `Pipeline configuration saved to ${body.profile}/PipelineConfig.json`};
      else if (url.endsWith('/process') || url.endsWith('/reanalyse')) data = { id: 'job1' };
      else if (url.endsWith('/jobs/job1')) {
        const input = calls.find(call => call.url.endsWith('/process')).body;
        data = { state: 'ready', result: { observation: input.observation, directory: input.directory,
          profile: input.profile, pipeline_config: {steps_map: input.pipeline}, excluded: input.excluded, aggregates: [
            { id: 'sky', scan_id: 'sky-scan', kind: 'SKY', target_id: 15, freq_scan: 0, views: { velocity: 'sky-velocity.png', frequency: 'sky-frequency.png', timeline: 'sky-timeline.png', timeline_seconds: 'sky-timeline-seconds.png' } },
            { id: 'sky2', scan_id: 'sky2-scan', kind: 'SKY', target_id: 2, freq_scan: 0, views: { frequency: 'sky2-frequency.png', timeline: 'sky2-timeline.png', timeline_seconds: 'sky2-timeline-seconds.png' } },
            { id: 'load', scan_id: 'load-scan', kind: 'LOAD', views: {timeline: 'load-timeline.png', timeline_seconds: 'load-timeline-seconds.png'} }], views: {frequency: 'frequency.png', timeline: 'timeline.png', timeline_seconds: 'timeline-seconds.png'}, warnings: [] } };
        if (!perSecondAvailable) {
          delete data.result.views.timeline_seconds;
          data.result.aggregates.forEach(row => delete row.views.timeline_seconds);
        }
        if (!individualFrequencyAvailable) data.result.aggregates.forEach(row => delete row.views.frequency);
        if (!velocityAvailable) {
          data.result.aggregates.forEach(row => {
            delete row.views.velocity;
            row.view_errors = {velocity: 'This observation has no recorded target sky coordinates or Alt/Az.'};
          });
        }
        const latest = [...calls].reverse().find(call => call.url.endsWith('/reanalyse'))?.body;
        if (latest?.lab_comparison) {
          data.result.aggregates[0].views.lab = 'sky-lab.png';
          data.result.lab_comparison = { beam_fwhm_deg: latest.lab_comparison.beam, sweep_ranges: latest.lab_comparison.sweep || null };
        }
      }
      else throw new Error(`Unexpected API request: ${url}`);
      return { ok: true, status: 200, json: async () => data };
    },
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  return {
    velocityTab, tabs, openResponses,
    profileChanged(profile, directory = null) { get('dish-profile').value = profile; return windowEvents.get('dmd:profile')?.({detail: {profile, scan_store_dir: directory}}); },
    async select(id = 'obs-new') {
      await get('opt-observation-rows').listeners.change[0]({target: {closest: selector => selector === '[data-opt-select]' ? {dataset: {optSelect: id}} : null}});
    },
    async expand(id) {
      await get('opt-observation-rows').listeners.click[0]({target: {closest: selector => selector === '[data-opt-observation]' ? {dataset: {optObservation: id}} : null}});
    },
    element: (name) => get(`opt-${name}`), calls,
    setOnline: (value) => { online = value; },
    async heartbeat() {
      const entry = [...timers].find(([, timer]) => timer.ms === 5000);
      assert.ok(entry, 'heartbeat is scheduled'); timers.delete(entry[0]);
      await entry[1].fn(); await new Promise(setImmediate);
    },
  };
}

test('catalogue expands saved scans before selection and needs no Open observation step', async () => {
  const app = await workspace();
  const listing = app.element('observation-rows').innerHTML;
  assert.match(listing, /Galactic plane/);
  assert.match(listing, /dish003/);
  assert.equal(app.element('observation').value, '');
  assert.equal(app.element('process').disabled, true);
  await app.expand('obs-old');
  assert.equal(app.calls.filter(call => call.url.endsWith('/open')).length, 1);
  assert.equal(app.element('observation').value, '');
  assert.match(app.element('observation-rows').innerHTML, /Hydrogen target/);
  assert.match(app.element('observation-rows').innerHTML, /data-scan-signal=/);
  assert.match(app.element('observation-rows').innerHTML, /directory=%2Fsamples/);
  await app.select('obs-old');
  assert.equal(app.element('observation').value, 'obs-old');
  assert.equal(app.element('observation-choice').textContent, 'Earlier observation');
  assert.equal(app.calls.filter(call => call.url.endsWith('/open')).length, 1);
  assert.equal(app.element('process').disabled, false);
  assert.equal(app.element('observation-browser').open, true);
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
  assert.equal(app.element('find').disabled, true);
  app.setOnline(true);
  await app.heartbeat();
  assert.equal(badge.textContent, 'Local OPT connected');
  assert.ok(!badge.classes.has('is-disconnected'));
  assert.equal(app.element('find').disabled, false);
});


test('interactive baseline starts with no subtraction even when an old method was remembered', async () => {
  for (const saved of [null, 'linear', 'constant', 'flatten']) {
    const app = await workspace({savedPreferences: saved ? {velocityRangeVersion: 1, settings: {baseline: saved, baseline_vmin: -200, baseline_vmax: 200}} : {}});
    assert.equal(app.element('baseline').value, 'none');
    if (saved) assert.equal(app.element('vmin').value, -200);
    await app.select();
    await app.element('process').listeners.click[0]();
    assert.equal(app.calls.find(call => call.url.endsWith('/process')).body.settings.baseline, 'none');
  }
});

for (const method of ['linear', 'flatten']) {
test(`analysis controls accept ${method} for cached updates; profile changes require reprocessing`, async () => {
  const app = await workspace();
  await app.select();
  for (const [key, value] of Object.entries({profile: 'rotation', baseline: 'none', vmin: '-400', vmax: '401', smoothing: '0', contours: '.01, .02, .04'})) app.element(key).value = value;
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  const sent = app.calls.find(call => call.url.endsWith('/process')).body;
  assert.equal(sent.profile, 'rotation');
  assert.deepEqual(sent.settings, { total_power_basis: 'frequency', baseline: 'none', baseline_vmin: -400, baseline_vmax: 401, frequency_min_mhz: null, frequency_max_mhz: null, contour_smoothing: 0, contours: [.01, .02, .04] });
  app.velocityTab.listeners.click[0]();
  app.element('baseline').value = method;
  app.element('baseline').listeners.change[0]();
  assert.equal(app.element('replot').disabled, false);
  await app.element('replot').listeners.click[0]();
  await new Promise(setImmediate);
  const update = app.calls.find(call => call.url.endsWith('/reanalyse')).body;
  assert.equal(update.job, 'job1');
  assert.equal(update.settings.baseline, method);
  assert.equal(update.settings.total_power_basis, "velocity");
  assert.equal(update.settings.contour_smoothing, 0);
  app.element('profile').value = 'default';
  app.profileChanged('default');
  assert.equal(app.element('replot').disabled, true);
});
}

test('blank velocity limits show the full range and obsolete saved defaults are ignored', async () => {
  const app = await workspace({savedPreferences: {settings: {baseline_vmin: -100, baseline_vmax: 100}}});
  assert.equal(app.element('vmin').value, '');
  assert.equal(app.element('vmax').value, '');
  await app.select();
  await app.element('process').listeners.click[0]();
  const settings = app.calls.find(call => call.url.endsWith('/process')).body.settings;
  assert.equal(settings.baseline_vmin, null);
  assert.equal(settings.baseline_vmax, null);
  await app.velocityTab.listeners.click[0]();
  app.element('baseline').value = 'flatten';
  app.element('baseline').listeners.change[0]();
  await app.element('replot').listeners.click[0]();
  assert.equal(app.calls.find(call => call.url.endsWith('/reanalyse')).body.settings.baseline, 'flatten');
});


test('LAB comparison is requested for one SKY aggregate and excluded from all-SKY and calibration views', async () => {
  const app = await workspace();
  app.element('lab-sweep').checked = false;
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  app.velocityTab.listeners.click[0]();
  assert.equal(app.element('lab-controls').hidden, false);
  app.element('lab').checked = true;
  app.element('lab-beam').value = '8';
  await app.element('lab').listeners.change[0]();
  await new Promise(setImmediate);
  const sent = app.calls.find(call => call.url.endsWith('/reanalyse')).body;
  assert.deepEqual(sent.lab_comparison, { aggregate_id: 'sky', beam: 8 });
  assert.match(app.element('plot').src, /sky-lab.png/);
  app.element('lab').checked = false;
  await app.element('lab').listeners.change[0]();
  assert.match(app.element('plot').src, /sky-velocity.png/);
  app.element('overlay').checked = true;
  app.element('overlay').listeners.change[0]();
  assert.equal(app.element('lab-controls').hidden, true);
  app.element('overlay').checked = false;
  app.element('overlay').listeners.change[0]();
  app.element('scan-list').listeners.click[0]({ target: { closest: () => ({ dataset: { optRow: 'load' } }) } });
  assert.equal(app.element('lab-controls').hidden, true);
});


test('analysis settings follow the selected tab and retain values while disabled', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  const expectEnabled = names => {
    for (const id of ['baseline', 'vmin', 'vmax', 'smoothing', 'contours'])
      assert.equal(app.element(id).disabled, !names.includes(id), id);
    assert.equal(app.element('profile').disabled, false);
  };
  for (const view of ['signal', 'timeline', 'frequency', 'details']) {
    app.tabs[view].listeners.click[0]();
    expectEnabled([]);
    assert.equal(app.element('replot').disabled, true);
  }
  app.tabs.velocity.listeners.click[0]();
  expectEnabled(['baseline', 'vmin', 'vmax']);
  app.element('vmin').value = '-250';
  app.element('vmin').listeners.change[0]();
  assert.equal(app.element('replot').disabled, false);
  app.tabs.lv.listeners.click[0]();
  expectEnabled(['baseline', 'vmin', 'vmax', 'smoothing', 'contours']);
  app.element('baseline').value = 'none';
  app.element('baseline').listeners.change[0]();
  expectEnabled(['baseline', 'vmin', 'vmax', 'smoothing', 'contours']);
  assert.equal(app.element('vmin').value, '-250');
  app.element('smoothing').value = '0';
  app.element('smoothing').listeners.change[0]();
  app.tabs.velocity.listeners.click[0]();
  expectEnabled(['baseline', 'vmin', 'vmax']);
  assert.equal(app.element('smoothing').value, '0');
  app.element('scan-list').listeners.click[0]({ target: { closest: () => ({ dataset: { optRow: 'load' } }) } });
  expectEnabled([]);
  app.element('overlay').checked = true;
  app.element('overlay').listeners.change[0]();
  expectEnabled(['baseline', 'vmin', 'vmax']);
  app.setOnline(false); await app.heartbeat();
  for (const id of ['baseline', 'vmin', 'vmax', 'smoothing', 'contours']) assert.equal(app.element(id).disabled, true);
});


test('sweep controls send offset grids and disabling sweep restores nominal comparison', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  app.velocityTab.listeners.click[0]();
  app.element('lab').checked = true;
  await app.element('lab').listeners.change[0]();
  await new Promise(setImmediate);
  assert.equal(app.element('lab-sweep-controls').hidden, false);
  assert.equal(app.element('lab-sweep').checked, true);
  assert.equal(app.element('lab-find').disabled, false);
  await app.element('lab-find').listeners.click[0]();
  await new Promise(setImmediate);
  let latest = [...app.calls].reverse().find(call => call.url.endsWith('/reanalyse')).body;
  assert.deepEqual(latest.lab_comparison.sweep, { longitude: [-10, 10, 1], latitude: [-10, 10, 1] });
  assert.match(app.element('plot').src, /sky-lab.png/);
  const count = app.calls.filter(call => call.url.endsWith('/reanalyse')).length;
  app.element('lab-lon-step').value = '0';
  await app.element('lab-find').listeners.click[0]();
  assert.equal(app.calls.filter(call => call.url.endsWith('/reanalyse')).length, count);
  assert.match(app.element('error').textContent, /positive steps/);
  app.element('lab-sweep').checked = false;
  await app.element('lab-sweep').listeners.change[0]();
  await new Promise(setImmediate);
  latest = [...app.calls].reverse().find(call => call.url.endsWith('/reanalyse')).body;
  assert.equal(latest.lab_comparison.sweep, undefined);
  assert.equal(app.element('lab-find').disabled, true);
});


test('Frequency shows all SKY aggregates by default and switches to the selected aggregate', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  app.tabs.frequency.listeners.click[0]();
  assert.equal(app.element('frequency-overlay-label').hidden, false);
  assert.equal(app.element('overlay-label').hidden, true);
  assert.match(app.element('plot').src, /files\/frequency.png$/);
  app.element('frequency-overlay').checked = false;
  app.element('frequency-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/sky-frequency.png$/);
  app.element('scan-list').listeners.click[0]({target: {closest: () => ({dataset: {optRow: 'sky2'}})}});
  assert.match(app.element('plot').src, /files\/sky2-frequency.png$/);
  app.element('frequency-overlay').checked = true;
  app.element('frequency-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/frequency.png$/);
  app.element('frequency-overlay').checked = false;
  app.element('scan-list').listeners.click[0]({target: {closest: () => ({dataset: {optRow: 'load'}})}});
  assert.equal(app.element('plot').hidden, true);
});


test('individual Frequency remains usable without velocity and explains missing pointing on Velocity', async () => {
  const app = await workspace({velocityAvailable: false});
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  await app.tabs.frequency.listeners.click[0]();
  app.element('frequency-overlay').checked = false;
  app.element('frequency-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /sky-frequency.png$/);
  assert.equal(app.element('plot').hidden, false);
  await app.tabs.velocity.listeners.click[0]();
  assert.equal(app.element('plot').hidden, true);
  assert.match(app.element('placeholder').querySelector('p').textContent, /no recorded.*coordinates or Alt\/Az/i);
  assert.equal(app.element('lab-controls').hidden, true);
});

test('missing individual Frequency in older results explains that reprocessing is needed', async () => {
  const app = await workspace({individualFrequencyAvailable: false});
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  await app.tabs.frequency.listeners.click[0]();
  app.element('frequency-overlay').checked = false;
  app.element('frequency-overlay').listeners.change[0]();
  assert.equal(app.element('plot').hidden, true);
  assert.match(app.element('placeholder').querySelector('p').textContent, /Reprocess.*individual Frequency|individual Frequency.*Reprocess/);
  app.element('frequency-overlay').checked = true;
  app.element('frequency-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/frequency.png$/);
});

test('Frequency MHz limits and baseline apply only to the selected SKY view', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  app.tabs.frequency.listeners.click[0]();
  assert.equal(app.element('baseline').disabled, true);
  assert.equal(app.element('fmin').disabled, true);
  app.element('frequency-overlay').checked = false;
  app.element('frequency-overlay').listeners.change[0]();
  for (const name of ['baseline', 'fmin', 'fmax']) assert.equal(app.element(name).disabled, false);
  for (const name of ['vmin', 'vmax', 'smoothing', 'contours']) assert.equal(app.element(name).disabled, true);
  app.element('fmin').value = '1419.8'; app.element('fmax').value = '1421';
  app.element('fmin').listeners.change[0]();
  assert.equal(app.element('replot').disabled, false);
  await app.element('replot').listeners.click[0]();
  await new Promise(setImmediate);
  const settings = [...app.calls].reverse().find(call => call.url.endsWith('/reanalyse')).body.settings;
  assert.equal(settings.total_power_basis, "frequency");
  assert.equal(settings.frequency_min_mhz, 1419.8);
  assert.equal(settings.frequency_max_mhz, 1421);
  app.element('frequency-overlay').checked = true;
  app.element('frequency-overlay').listeners.change[0]();
  assert.equal(app.element('baseline').disabled, true);
  assert.equal(app.element('fmax').disabled, true);
  assert.equal(app.element('fmin').value, '1419.8');
});


test('failed saved-scan expansion can be retried without selecting or processing it', async () => {
  const app = await workspace();
  app.openResponses.set('obs-old', {ok: false, status: 400, json: async () => ({error: 'Saved scans unavailable'})});
  await app.expand('obs-old');
  assert.match(app.element('observation-rows').innerHTML, /data-history-retry=/);
  assert.equal(app.element('process').disabled, true);
  app.openResponses.delete('obs-old');
  await app.element('observation-rows').listeners.click[0]({target: {closest: selector => selector === '[data-history-retry]' ? {dataset: {historyRetry: 'obs-old'}} : null}});
  assert.match(app.element('observation-rows').innerHTML, /Hydrogen target/);
  assert.doesNotMatch(app.element('observation-rows').innerHTML, /data-history-retry=/);
  assert.equal(app.element('process').disabled, true);
});

test('late scan metadata cannot populate a refreshed observation directory', async () => {
  const app = await workspace();
  let resolve;
  app.openResponses.set('obs-old', new Promise(done => { resolve = done; }));
  const pending = app.expand('obs-old');
  await app.element('find').listeners.click[0]();
  resolve({ok: true, status: 200, json: async () => ({id: 'obs-old', directory: '/samples', scans: [],
    metadata: {_type: 'ObsModel', obs_id: 'STALE_RESPONSE', target_scans: []}})});
  await pending;
  assert.doesNotMatch(app.element('observation-rows').innerHTML, /STALE_RESPONSE/);
  assert.equal(app.element('process').disabled, true);
});


test('selection preserves a saved filename that differs from the observation ID', async () => {
  const app = await workspace();
  app.openResponses.set('obs-old', {ok: true, status: 200, json: async () => ({id: 'original-id', file_id: 'obs-old', directory: '/samples',
    scans: [{id: 'scan1', kind: 'SKY', status: 'COMPLETE', duration_s: 60}],
    metadata: {_type: 'ObsModel', obs_id: 'original-id', target_scans: []}})});
  await app.select('obs-old');
  assert.equal(app.element('process').disabled, false);
  await app.element('process').listeners.click[0]();
  assert.equal(app.calls.find(call => call.url.endsWith('/process')).body.observation, 'obs-old');
});


test('Total Power switches between all SKY aggregates and the selected aggregate', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  app.tabs.timeline.listeners.click[0]();
  assert.equal(app.element('timeline-overlay-label').hidden, false);
  assert.equal(app.element('frequency-overlay-label').hidden, true);
  assert.match(app.element('plot').src, /files\/timeline.png$/);
  const calls = app.calls.length;
  app.element('timeline-overlay').checked = false;
  app.element('timeline-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/sky-timeline.png$/);
  app.element('scan-list').listeners.click[0]({target: {closest: () => ({dataset: {optRow: 'sky2'}})}});
  assert.match(app.element('plot').src, /files\/sky2-timeline.png$/);
  app.element('scan-list').listeners.click[0]({target: {closest: () => ({dataset: {optRow: 'load'}})}});
  assert.match(app.element('plot').src, /files\/load-timeline.png$/);
  app.element('timeline-overlay').checked = true;
  app.element('timeline-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/timeline.png$/);
  assert.equal(app.calls.length, calls);
  assert.equal(app.element('frequency-overlay').checked, true);
});

test('Total Power switches resolution locally for overlay and individual scans', async () => {
  const app = await workspace();
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  await app.tabs.timeline.listeners.click[0]();
  const requests = app.calls.length;
  assert.equal(app.element('timeline-resolution-label').hidden, false);
  app.element('timeline-resolution').value = 'seconds';
  app.element('timeline-resolution').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/timeline-seconds.png$/);
  app.element('timeline-overlay').checked = false;
  app.element('timeline-overlay').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/sky-timeline-seconds.png$/);
  app.element('scan-list').listeners.click[0]({target: {closest: () => ({dataset: {optRow: 'load'}})}});
  assert.match(app.element('plot').src, /files\/load-timeline-seconds.png$/);
  app.element('timeline-resolution').value = 'scans';
  app.element('timeline-resolution').listeners.change[0]();
  assert.match(app.element('plot').src, /files\/load-timeline.png$/);
  assert.equal(app.calls.length, requests);
  app.tabs.frequency.listeners.click[0]();
  assert.equal(app.element('timeline-resolution-label').hidden, true);
});

test('old results request reprocessing rather than relabelling scans as seconds', async () => {
  const app = await workspace({perSecondAvailable: false});
  await app.select();
  await app.element('process').listeners.click[0]();
  await new Promise(setImmediate);
  await app.tabs.timeline.listeners.click[0]();
  app.element('timeline-resolution').value = 'seconds';
  app.element('timeline-resolution').listeners.change[0]();
  assert.equal(app.element('plot').hidden, true);
  assert.match(app.element('placeholder').querySelector('p').textContent, /reprocess.*per-second/i);
});


test('pipeline edits are per-run, disable cached updates until reprocessing, and reset from profile', async () => {
  const app = await workspace();
  await app.select('obs-new');
  await app.element('process').listeners.click[0]();
  assert.equal(app.calls.find(call => call.url.endsWith('/process')).body.pipeline.default[0].step, 'NOP');
  app.element('pipeline-editor').value = JSON.stringify({default: [{step: 'BASELINE', params: {context: 'cal', method: 'constant'}}]});
  await app.element('pipeline-editor').listeners.input[0]();
  await app.tabs.velocity.listeners.click[0]();
  assert.equal(app.element('replot').disabled, true);
  await app.element('process').listeners.click[0]();
  assert.equal(app.calls.filter(call => call.url.endsWith('/process')).at(-1).body.pipeline.default[0].params.method, 'constant');
  await app.element('pipeline-reset').listeners.click[0]();
  assert.equal(JSON.parse(app.element('pipeline-editor').value).default[0].step, 'NOP');
});


test('Results follows the accepted header profile and retains old results until reprocessing', async () => {
  const app = await workspace({sharedProfile: 'rotation'});
  assert.equal(app.element('profile').value, 'rotation');
  assert.equal(JSON.parse(app.element('pipeline-editor').value).default[0].step, 'BASELINE');
  await app.select();
  await app.element('process').listeners.click[0]();
  assert.equal(app.calls.find(call => call.url.endsWith('/process')).body.profile, 'rotation');
  await new Promise(setImmediate);
  app.tabs.velocity.listeners.click[0]();
  app.profileChanged('default');
  assert.equal(JSON.parse(app.element('pipeline-editor').value).default[0].step, 'NOP');
  assert.equal(app.element('replot').disabled, true);
  assert.ok(app.element('export').href.includes('job1'));
  assert.equal(app.element('aggregate-count').textContent, 3);
  assert.equal(app.calls.filter(call => call.url.endsWith('/process')).length, 1);
});


test('Config Profile scan_store_dir supplies initial and switched directories with manual override afterward', async () => {
  const app = await workspace({sharedProfile: 'rotation', scanStoreDir: '/samples/rotation'});
  assert.equal(app.element('directory').value, '/samples/rotation');
  assert.ok(app.calls.some(call => call.url.includes('directory=%2Fsamples%2Frotation')));
  await app.select();
  await app.profileChanged('default', '/samples/patio');
  assert.equal(app.element('directory').value, '/samples/patio');
  assert.equal(app.element('observation').value, '');
  assert.ok(app.calls.some(call => call.url.includes('directory=%2Fsamples%2Fpatio')));
  app.element('directory').value = '/samples/manual';
  await app.element('directory').listeners.change[0]();
  assert.equal(app.element('directory').value, '/samples/manual');
  assert.ok(app.calls.some(call => call.url.includes('directory=%2Fsamples%2Fmanual')));
  assert.equal(app.element('profile').value, 'default');
});


test('step parameter fields synchronize typed JSON, validate ranges, and reach the processing request', async () => {
  const app = await workspace();
  await app.select();
  const pipeline = {default: [
    {step: 'BANDPASS_FILTER', params: {context: 'cal', ranges_pct: [[10, 85]]}},
    {step: 'RFI_FLAG', params: {context: 'cal', threshold: 5, window_size: 21}},
    {step: 'QA', params: {context: 'mpr', window_frac: .2}},
    {step: 'BASELINE', params: {context: 'cal', method: 'linear'}}
  ]};
  app.element('pipeline-editor').value = JSON.stringify(pipeline);
  app.element('pipeline-editor').listeners.input[0]();
  const html = app.element('pipeline-summary').innerHTML;
  assert.match(html, /<strong>Pipeline Steps<\/strong>/);
  for (const param of ['context', 'ranges_pct', 'threshold', 'window_size', 'window_frac', 'smooth_window', 'noise_estimator', 'method']) {
    assert.ok(html.includes(`data-pipeline-param="${param}"`));
  }
  async function change(index, name, value, type = 'number', extra = {}) {
    await app.element('pipeline-summary').listeners.change[0]({target: {value: String(value),
      dataset: {pipelineGroup: 'default', pipelineIndex: String(index), pipelineParam: name, pipelineType: type, ...extra}}});
  }
  await change(1, 'threshold', 7.5);
  await change(1, 'window_size', 25);
  await change(2, 'noise_estimator', 'mean_rms', 'string');
  await change(2, 'smooth_window', 12);
  await change(0, 'ranges_pct', 90, 'number', {pipelineRange: '0', pipelineBound: '1'});
  const beforeInvalid = app.element('pipeline-editor').value;
  await change(1, 'window_size', 24);
  assert.match(app.element('error').textContent, /positive odd/);
  assert.equal(app.element('pipeline-editor').value, beforeInvalid);
  assert.equal(app.element('process').disabled, true);
  await change(1, 'window_size', 27);
  await change(0, 'ranges_pct', 100, 'number', {pipelineRange: '0', pipelineBound: '0'});
  assert.match(app.element('error').textContent, /minimum < maximum/);
  await change(0, 'ranges_pct', 15, 'number', {pipelineRange: '0', pipelineBound: '0'});
  await app.element('process').listeners.click[0]();
  const sent = app.calls.find(call => call.url.endsWith('/process')).body.pipeline.default;
  assert.equal(sent[1].params.threshold, 7.5);
  assert.equal(sent[1].params.window_size, 27);
  assert.equal(sent[2].params.noise_estimator, 'mean_rms');
  assert.equal(sent[2].params.smooth_window, 12);
  assert.deepEqual(sent[0].params.ranges_pct, [[15, 90]]);
  // Rendering optional defaults does not silently insert them into the JSON.
  assert.equal(sent[3].params.ranges_pct, undefined);
});

test('multiple percentage ranges can be added and removed from a step', async () => {
  const app = await workspace();
  app.element('pipeline-editor').value = JSON.stringify({default: [{step: 'BASELINE', params: {context: 'cal'}}]});
  app.element('pipeline-editor').listeners.input[0]();
  const control = {disabled: false, dataset: {pipelineGroup: 'default', pipelineIndex: '0', pipelineParam: 'ranges_pct', pipelineAddRange: ''}};
  await app.element('pipeline-summary').listeners.click[0]({target: {closest: () => control}});
  assert.deepEqual(JSON.parse(app.element('pipeline-editor').value).default[0].params.ranges_pct, [[0, 10], [90, 100], [0, 100]]);
  delete control.dataset.pipelineAddRange;
  control.dataset.pipelineRemoveRange = '2';
  await app.element('pipeline-summary').listeners.click[0]({target: {closest: () => control}});
  assert.deepEqual(JSON.parse(app.element('pipeline-editor').value).default[0].params.ranges_pct, [[0, 10], [90, 100]]);
});

test('flatten can be selected in the baseline method field and submitted for processing', async () => {
  const app = await workspace({sharedProfile: 'rotation'});
  await app.select();
  assert.match(app.element('pipeline-summary').innerHTML, /value="flatten"/);
  await app.element('pipeline-summary').listeners.change[0]({target: {
    value: 'flatten', dataset: {pipelineGroup: 'default', pipelineIndex: '0', pipelineParam: 'method', pipelineType: 'string'}
  }});
  assert.equal(JSON.parse(app.element('pipeline-editor').value).default[0].params.method, 'flatten');
  await app.element('process').listeners.click[0]();
  assert.equal(app.calls.find(call => call.url.endsWith('/process')).body.pipeline.default[0].params.method, 'flatten');
});


test('unconfigured available steps stay out of JSON until checked and enter before QA', async () => {
  const app = await workspace();
  await app.select();
  app.element('pipeline-editor').value = JSON.stringify({default: [
    {step: 'LOAD', params: {context: 'cal'}},
    {step: 'QA', params: {context: 'mpr', window_frac: .2}}
  ]});
  app.element('pipeline-editor').listeners.input[0]();
  const html = app.element('pipeline-summary').innerHTML;
  for (const step of ['DC_SPIKE', 'BANDPASS_FILTER', 'RFI_FLAG', 'BASELINE']) {
    assert.ok(html.includes(`data-pipeline-index="-1" data-pipeline-step="${step}"`));
  }
  assert.equal(JSON.parse(app.element('pipeline-editor').value).default.length, 2);
  assert.doesNotMatch(html, /data-pipeline-step="NOP"/);
  await app.element('pipeline-summary').listeners.change[0]({target: {checked: true,
    dataset: {pipelineEnabled: 'default', pipelineIndex: '-1', pipelineStep: 'BASELINE'}}});
  let configured = JSON.parse(app.element('pipeline-editor').value).default;
  assert.deepEqual(configured.map(step => step.step), ['LOAD', 'BASELINE', 'QA']);
  assert.equal(configured[1].params.method, 'flatten');
  assert.deepEqual(configured[1].params.ranges_pct, [[0, 10], [90, 100]]);
  await app.element('pipeline-summary').listeners.change[0]({target: {checked: false,
    dataset: {pipelineEnabled: 'default', pipelineIndex: '1', pipelineStep: 'BASELINE'}}});
  await app.element('pipeline-summary').listeners.change[0]({target: {checked: true,
    dataset: {pipelineEnabled: 'default', pipelineIndex: '1', pipelineStep: 'BASELINE'}}});
  configured = JSON.parse(app.element('pipeline-editor').value).default;
  assert.equal(configured.filter(step => step.step === 'BASELINE').length, 1);
  await app.element('process').listeners.click[0]();
  const sent = app.calls.find(call => call.url.endsWith('/process')).body.pipeline.default;
  assert.deepEqual(sent.map(step => step.step), ['LOAD', 'BASELINE', 'QA']);
  assert.equal(sent[1].params.enabled, true);
  assert.deepEqual(Object.keys(sent[1]).sort(), ['params', 'step']);
});

test('pipeline table groups contexts while preserving configured order and original edit indices', async () => {
  const app = await workspace();
  const configured = {default: [
    {step: 'QA', params: {context: 'mpr', window_frac: .2}},
    {step: 'RFI_FLAG', params: {context: 'cal', threshold: 5, window_size: 21}},
    {step: 'DC_SPIKE', params: {context: 'spr'}},
    {step: 'LOAD', params: {context: 'cal'}},
    {step: 'QA', params: {context: 'cal', window_frac: .3}}
  ]};
  app.element('pipeline-editor').value = JSON.stringify(configured);
  app.element('pipeline-editor').listeners.input[0]();
  const html = app.element('pipeline-summary').innerHTML;
  for (const title of ['Enabled', 'Context', 'Step', 'Parameters']) assert.ok(html.includes(`<th scope="col">${title}</th>`));
  const rows = [...html.matchAll(/<tr data-pipeline-row-step="([^"]+)" data-pipeline-row-context="([^"]+)"[^>]*>([\s\S]*?)<\/tr>/g)]
    .filter(row => !row[3].includes('data-pipeline-index="-1"'));
  assert.deepEqual(rows.map(row => [row[1], row[2]]), [
    ['DC_SPIKE', 'spr'], ['RFI_FLAG', 'cal'], ['LOAD', 'cal'], ['QA', 'cal'], ['QA', 'mpr']
  ]);
  assert.deepEqual(JSON.parse(app.element('pipeline-editor').value), configured);
  assert.ok(html.includes('DC spike removal'));
  assert.ok(html.includes('Quality assessment'));
  await app.element('pipeline-summary').listeners.change[0]({target: {value: '8', dataset: {
    pipelineGroup: 'default', pipelineIndex: '1', pipelineParam: 'threshold', pipelineType: 'number'
  }}});
  const edited = JSON.parse(app.element('pipeline-editor').value).default;
  assert.equal(edited[1].step, 'RFI_FLAG');
  assert.equal(edited[1].params.threshold, 8);
  assert.equal(edited[0].params.window_frac, .2);
});


test('step question marks show help for unconfigured steps without changing the pipeline', async () => {
  const app = await workspace();
  const before = app.element('pipeline-editor').value;
  const callsBefore = app.calls.length;
  const html = app.element('pipeline-summary').innerHTML;
  assert.match(html, /data-pipeline-help-step="BASELINE"/);
  assert.match(html, /aria-label="Help for Baseline subtraction"/);
  assert.match(html, /aria-controls="opt-pipeline-step-help"/);
  const help = {dataset: {pipelineHelpStep: 'BASELINE'}};
  const click = () => app.element('pipeline-summary').listeners.click[0]({target: {
    closest: selector => selector === '[data-pipeline-help-step]' ? help : null
  }});
  await click();
  assert.equal(app.element('pipeline-help-title').textContent, 'Baseline subtraction');
  assert.match(app.element('pipeline-help-description').textContent, /preserve LOAD references/);
  assert.match(app.element('pipeline-help-details').innerHTML, /Parameters/);
  assert.match(app.element('pipeline-help-details').innerHTML, /line-free percentiles/);
  assert.match(app.element('pipeline-help-details').innerHTML, /solar continuum/);
  assert.match(app.element('pipeline-help-details').innerHTML, /&lt;unsafe&gt;/);
  assert.doesNotMatch(app.element('pipeline-help-details').innerHTML, /<unsafe>/);
  help.dataset.pipelineHelpStep = 'RFI_FLAG';
  await click();
  assert.equal(app.element('pipeline-help-title').textContent, 'RFI flagging');
  assert.match(app.element('pipeline-help-description').textContent, /without changing measured values/);
  assert.equal(app.element('pipeline-help-details').innerHTML, '');
  assert.equal(app.element('pipeline-editor').value, before);
  assert.equal(app.calls.length, callsBefore);
});


test('pipeline overview is shown initially and its heading question mark restores it', async () => {
  const app = await workspace();
  const before = app.element('pipeline-editor').value;
  const requests = app.calls.length;
  assert.equal(app.element('pipeline-help-title').textContent, 'How the pipeline works');
  assert.match(app.element('pipeline-help-description').textContent, /spr → cal → mpr/);
  assert.match(app.element('pipeline-help-details').innerHTML, /Summed Power Spectrum/);
  assert.match(app.element('pipeline-help-details').innerHTML, /Mean Power Spectrum/);
  assert.match(app.element('pipeline-help-details').innerHTML, /does not replay spr steps/);
  assert.match(app.element('pipeline-summary').innerHTML, /data-pipeline-help-overview aria-label="Help for Pipeline Steps"/);
  await app.element('pipeline-summary').listeners.click[0]({target: {
    closest: selector => selector === '[data-pipeline-help-step]' ? {dataset: {pipelineHelpStep: 'BASELINE'}} : null
  }});
  assert.equal(app.element('pipeline-help-title').textContent, 'Baseline subtraction');
  app.element('pipeline-step-help').scrollTop = 300;
  await app.element('pipeline-summary').listeners.click[0]({target: {
    closest: selector => selector === '[data-pipeline-help-overview]' ? {dataset: {pipelineHelpOverview: ''}} : null
  }});
  assert.equal(app.element('pipeline-help-title').textContent, 'How the pipeline works');
  assert.match(app.element('pipeline-help-details').innerHTML, /Mean Power Spectrum/);
  assert.equal(app.element('pipeline-step-help').scrollTop, 0);
  assert.equal(app.element('pipeline-editor').value, before);
  assert.equal(app.calls.length, requests);
});

test('full pipeline guide survives a config response without overview help', async () => {
  const app = await workspace({overviewAvailable: false});
  const guide = app.element('pipeline-help-details').innerHTML;
  assert.match(app.element('pipeline-help-description').textContent, /input to the next step/);
  for (const text of ['Summed Power Spectrum', 'Calibrated Spectrum', 'Mean Power Spectrum',
    'during acquisition', 'reprocessing the spectrum files', 'does not overwrite', 'original raw IQ samples']) {
    assert.ok(guide.includes(text), `Guide should explain ${text}`);
  }
  await app.element('pipeline-summary').listeners.click[0]({target: {
    closest: selector => selector === '[data-pipeline-help-step]' ? {dataset: {pipelineHelpStep: 'BASELINE'}} : null
  }});
  await app.element('pipeline-summary').listeners.click[0]({target: {
    closest: selector => selector === '[data-pipeline-help-overview]' ? {dataset: {pipelineHelpOverview: ''}} : null
  }});
  assert.equal(app.element('pipeline-help-details').innerHTML, guide);
});


test('Save Pipeline Configuration saves the selected profile and refreshes Reset', async () => {
  const app = await workspace();
  const edited = {default: [{step: 'BASELINE', params: {context: 'cal', method: 'flatten', ranges_pct: [[0,10],[90,100]]}}]};
  app.element('pipeline-editor').value = JSON.stringify(edited);
  app.element('pipeline-editor').listeners.input[0]();
  await app.element('pipeline-save').listeners.click[0]();
  const sent = app.calls.find(call => call.url.endsWith('/pipeline')).body;
  assert.equal(sent.profile, 'default');
  assert.deepEqual(sent.pipeline, edited);
  assert.match(app.element('pipeline-save-status').textContent, /default\/PipelineConfig.json/);
  app.element('pipeline-editor').value = '{}';
  app.element('pipeline-reset').listeners.click[0]();
  assert.deepEqual(JSON.parse(app.element('pipeline-editor').value), edited);
  app.element('pipeline-editor').value = '{';
  app.element('pipeline-editor').listeners.input[0]();
  assert.equal(app.element('pipeline-save').disabled, true);
});
