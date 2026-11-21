/* Digitiser definitions use the shared Admin profile and WEB bridge. */
(function () {
  const TEXT = ['dig_id', 'local_host', 'app_cmd_host', 'sdr_type', 'bpf_type', 'temp_type'];
  const NUMBERS = ['app_cmd_port', 'gain', 'sample_rate', 'bandwidth', 'center_freq', 'freq_correction', 'channels', 'temp_max'];
  const OPTIONS = ['sdr_config', 'temp_config'];
  const PINS = ['gpio_pin_power', 'gpio_pin_load'];
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const defaults = () => ({ dig_id: '', local_host: null, app_cmd_host: '127.0.0.1', app_cmd_port: 60006,
    sdr_type: 'rtlsdr', sdr_config: {}, gain: 0, sample_rate: 0, bandwidth: 0, center_freq: 0,
    freq_correction: 0, channels: 0, bpf_type: 'UNKNOWN', bpf_config: null,
    temp_type: 'none', temp_config: {}, temp_max: 20 });

  function init(doc, win, admin = win.DmdAdmin) {
    const $ = (id) => doc.getElementById('dig-' + id);
    if (!$('workspace') || !admin) return;
    const form = $('form');
    const field = (name) => form.elements.namedItem(name);
    const state = { profile: '', items: [], selected: null, original: null, baseline: '',
      etag: null, loaded: false, busy: false, isNew: false };
    function snapshot() {
      return JSON.stringify(Object.fromEntries([...TEXT, ...NUMBERS, ...OPTIONS, ...PINS].map((key) => [key, field(key).value])
        .concat([['bpf_enabled', field('bpf_enabled').checked]])));
    }
    const dirty = () => !form.hidden && snapshot() !== state.baseline;
    function update() {
      const processing = state.busy || admin.isBusy();
      const locked = processing || win.DmdSession?.can_edit === false;
      $('fields').disabled = locked;
      $('new').disabled = locked || !state.loaded || !state.etag;
      $('save').disabled = locked || form.hidden || (!state.isNew && !dirty());
      $('delete').hidden = form.hidden || state.isNew;
      $('delete').disabled = locked || !state.etag;
      $('dirty').hidden = !dirty();
      const bare = field('bpf_type').value === 'SAWBIRD_H1_BAREBONES';
      field('bpf_enabled').disabled = !bare;
      PINS.forEach((key) => { field(key).disabled = !bare || !field('bpf_enabled').checked; });
      field('temp_config').disabled = field('temp_type').value === 'none';
      $('rows').querySelectorAll('[data-digitiser-id]').forEach((row) => {
        row.tabIndex = processing ? -1 : 0; row.setAttribute('aria-disabled', String(processing));
      });
    }
    function busy(value) {
      state.busy = value; $('workspace').setAttribute('aria-busy', String(value));
      admin.update();
    }
    function finite(value) {
      if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('Numbers must be finite');
      if (value && typeof value === 'object') Object.values(value).forEach(finite);
    }
    function options(key) {
      try {
        const value = JSON.parse(field(key).value || '{}');
        if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Use a JSON object');
        finite(value);
        return value;
      } catch (error) {
        const message = (key === 'sdr_config' ? 'SDR' : 'Temperature sensor') + ' options must be a valid JSON object with finite numbers.';
        field(key).setCustomValidity(message); field(key).reportValidity();
        throw new Error(message);
      }
    }
    function payload() {
      const data = {};
      TEXT.forEach((key) => { data[key] = field(key).value.trim() || null; });
      NUMBERS.forEach((key) => { data[key] = field(key).value === '' ? null : Number(field(key).value); });
      OPTIONS.forEach((key) => { data[key] = options(key); });
      data.bpf_config = data.bpf_type === 'SAWBIRD_H1_BAREBONES' && field('bpf_enabled').checked
        ? Object.fromEntries(PINS.map((key) => [key, field(key).value === '' ? null : Number(field(key).value)])) : null;
      return data;
    }
    function discard() { return !dirty() || win.confirm('Discard unsaved digitiser changes?'); }
    function renderRows() {
      const term = $('filter').value.trim().toLowerCase();
      const visible = state.items.filter((item) => [item.dig_id, item.local_host, item.sdr_type].join(' ').toLowerCase().includes(term));
      $('count').textContent = '(' + state.items.length + ')';
      $('list-profile').textContent = state.profile ? 'Config Profile: ' + state.profile : 'No Config Profile loaded';
      if (state.profile) $('list-profile').innerHTML = 'Config Profile: <strong>' + escape(state.profile) + '</strong>';
      $('rows').innerHTML = visible.map((item) => `<tr data-digitiser-id="${escape(item.dig_id)}" tabindex="0" aria-label="Edit ${escape(item.dig_id)}" aria-selected="${item.dig_id === state.selected && !state.isNew}" class="${item.dig_id === state.selected && !state.isNew ? 'is-selected' : ''}"><td><strong>${escape(item.dig_id)}</strong><small>${escape(item.local_host || 'No host configured')}</small></td><td>${escape(item.sdr_type)}</td><td>${escape(item.sample_rate)} Hz</td></tr>`).join('')
        || '<tr><td colspan="3" class="zk-muted">' + (state.items.length ? 'No digitisers match your filter.' : 'No digitisers in this Config Profile. Choose New digitiser to create one.') + '</td></tr>';
      update();
    }
    function hide() {
      form.hidden = true; state.original = null; state.selected = null; state.isNew = false;
      $('editor-title').textContent = 'Choose a digitiser';
      $('editor-subtitle').textContent = 'Select a digitiser or create a new definition.';
      update();
    }
    function reset(profile) {
      state.profile = profile; state.items = []; state.etag = null; state.loaded = false;
      hide(); renderRows();
    }
    function show(item, isNew = false) {
      state.original = JSON.parse(JSON.stringify(item)); state.isNew = isNew;
      TEXT.concat(NUMBERS).forEach((key) => { field(key).value = item[key] ?? ''; });
      OPTIONS.forEach((key) => {
        field(key).value = JSON.stringify(item[key] || {}, null, 2); field(key).setCustomValidity('');
      });
      field('bpf_enabled').checked = item.bpf_config != null;
      PINS.forEach((key) => { field(key).value = item.bpf_config?.[key] ?? ''; });
      field('dig_id').readOnly = !isNew;
      form.hidden = false;
      $('editor-title').textContent = isNew ? 'New digitiser' : item.dig_id;
      $('editor-subtitle').textContent = 'Config Profile: ' + state.profile;
      if (state.profile) $('editor-subtitle').innerHTML = 'Config Profile: <strong>' + escape(state.profile) + '</strong>';
      $('save').textContent = isNew ? 'Create digitiser' : 'Save digitiser';
      state.baseline = snapshot(); renderRows();
    }
    function check(result) {
      if (result.payload.profile !== state.profile) throw Object.assign(new Error('Profile changed'), { code: 'profile_changed' });
      if (!result.etag) throw new Error('WEB did not provide a configuration version. Refresh before editing.');
      state.etag = result.etag;
      return result.payload;
    }
    async function read(id) {
      const result = check(await admin.request('digitisers/' + encodeURIComponent(id), { profile: state.profile }));
      state.selected = id; show(result.digitiser);
    }
    async function list(preferred) {
      state.items = [...check(await admin.request('digitisers', { profile: state.profile })).digitisers]
        .sort((a, b) => a.dig_id.localeCompare(b.dig_id));
      renderRows();
      const selected = state.items.find((item) => item.dig_id === preferred) || state.items[0];
      if (selected) await read(selected.dig_id); else hide();
      state.loaded = true;
    }
    async function activate(profile) {
      if (profile !== state.profile) reset(profile);
      if (state.loaded) {
        admin.message('Config Profile: ' + state.profile + '. Select a digitiser to edit.'); return;
      }
      busy(true); admin.message('Loading digitisers…');
      try {
        await list(state.selected); admin.connected(true);
        admin.message('Loaded ' + state.items.length + ' digitisers from ', false, state.profile);
      } finally { busy(false); }
    }
    async function save(event) {
      event.preventDefault();
      if (state.busy || admin.isBusy() || !form.reportValidity()) return;
      let data;
      try { data = payload(); } catch (error) { admin.failure(error); return; }
      if (!state.etag) { admin.message('Refresh before saving a digitiser.', true); return; }
      const isNew = state.isNew;
      busy(true); admin.message('Saving digitiser…');
      let saved = false;
      try {
        const result = check(await admin.request(isNew ? 'digitisers' : 'digitisers/' + encodeURIComponent(state.selected), {
          method: isNew ? 'POST' : 'PATCH', body: data, profile: state.profile, etag: state.etag,
        }));
        saved = true; state.selected = result.digitiser.dig_id; show(result.digitiser);
        await list(state.selected); admin.connected(true);
        admin.message('Saved ' + state.selected + ' to Config Profile: ' + state.profile + '. Changes apply on startup or the supported RESYNC flow.');
      } catch (error) {
        if (saved) admin.message('Digitiser saved, but the list could not be refreshed. ' + error.message, true);
        else admin.failure(error);
      } finally { busy(false); }
    }
    async function remove() {
      if (state.busy || admin.isBusy() || state.isNew || !state.selected || !state.etag) return;
      const id = state.selected;
      if (!win.confirm('Delete ' + id + ' from Config Profile: ' + state.profile + '? This removes its stored digitiser definition.'
        + (dirty() ? ' Unsaved edits will also be discarded.' : ''))) return;
      busy(true); admin.message('Deleting ' + id + '…');
      let deleted = false;
      try {
        check(await admin.request('digitisers/' + encodeURIComponent(id), { method: 'DELETE', profile: state.profile, etag: state.etag }));
        deleted = true; state.items = state.items.filter((item) => item.dig_id !== id);
        hide(); renderRows(); await list(); admin.connected(true);
        admin.message('Deleted ' + id + ' from Config Profile: ' + state.profile + '.');
      } catch (error) {
        if (deleted) admin.message('Digitiser deleted, but the list could not be refreshed. ' + error.message, true);
        else admin.failure(error);
      } finally { busy(false); }
    }
    async function selectRow(event) {
      const row = event.target.closest('[data-digitiser-id]');
      if (!row || state.busy || admin.isBusy() || (row.dataset.digitiserId === state.selected && !state.isNew) || !discard()) return;
      busy(true);
      try { await read(row.dataset.digitiserId); admin.message('Editing ' + state.selected + '.'); }
      catch (error) { admin.failure(error); } finally { busy(false); }
      if (event.type === 'keydown') Array.from($('rows').querySelectorAll('[data-digitiser-id]'))
        .find((item) => item.dataset.digitiserId === row.dataset.digitiserId)?.focus();
    }
    $('rows').addEventListener('click', selectRow);
    $('rows').addEventListener('keydown', (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) { event.preventDefault(); return selectRow(event); }
    });
    $('filter').addEventListener('input', renderRows);
    $('new').addEventListener('click', () => {
      if (state.busy || admin.isBusy() || !discard()) return;
      show(defaults(), true); admin.message('Enter a unique digitiser ID and its configuration.'); field('dig_id').focus();
    });
    $('cancel').addEventListener('click', () => {
      if (state.busy || admin.isBusy() || !discard()) return;
      if (state.isNew) { hide(); renderRows(); } else show(state.original);
      admin.message('Unsaved digitiser edits cancelled.');
    });
    $('delete').addEventListener('click', remove);
    form.addEventListener('submit', save);
    form.addEventListener('input', () => { OPTIONS.forEach((key) => field(key).setCustomValidity('')); update(); });
    form.addEventListener('change', update);
    for (const [type, config] of [['sdr_type', 'sdr_config'], ['temp_type', 'temp_config']]) {
      field(type).addEventListener('change', () => { field(config).value = '{}'; field(config).setCustomValidity(''); update(); });
    }
    field('bpf_type').addEventListener('change', () => {
      field('bpf_enabled').checked = false; PINS.forEach((key) => { field(key).value = ''; }); update();
    });
    const editor = { dirty, isBusy: () => state.busy, update, reset, activate, payload, save, state };
    admin.register('digitisers', editor);
    return editor;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, defaults };
  if (typeof document !== 'undefined') init(document, window);
})();
