/* Profile dish editor. Credentials stay in the local UI service. */
(function () {
  const DRIVER_DEFAULTS = {
    DRIFT: { alt: 90, az: 0 },
    MD01: { host: 'localhost', port: 65000, stow_alt: 80, stow_az: 0,
      offset_alt: 0, offset_az: 0, min_alt: 0, max_alt: 90, min_az: 0, max_az: 360,
      resolution: 0.1, rotation_speed: 1.5, rate_limit: 1, short_way: false },
    MOTION: { imu_id: 'imu001', imu_host: '127.0.0.1', imu_port: 52500,
      resolution: 1, stow_alt: 90, stow_az: 0, rate_limit: 0.1 },
  };
  const NUMBERS = ['diameter', 'fd_ratio', 'latitude', 'longitude', 'height', 'driver_poll_period'];
  const TEXT = ['dsh_id', 'short_desc', 'dig_id', 'ws_id', 'feed_type', 'driver_type'];
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  async function requestApi(resource, { method = 'GET', body, profile, etag } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 16000);
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (profile) headers['X-Config-Profile'] = profile;
    if (etag) headers['If-Match'] = etag;
    try {
      const response = await fetch('/api/web/' + resource, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
      });
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('The console returned an unreadable response. Refresh to check any submitted save.'); }
      if (!response.ok || data.status !== 'success') {
        throw Object.assign(new Error(data.message || data.error || 'WEB request failed'), { code: data.code, status: response.status });
      }
      return { payload: data.payload, etag: response.headers.get('ETag') };
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) {
        throw Object.assign(new Error(method === 'GET' ? 'Could not connect to WEB. Try Refresh when the service is available.'
          : 'Change was not confirmed. Refresh to check whether it completed before trying again.'), { code: 'web_unavailable' });
      }
      throw error;
    } finally { clearTimeout(timer); }
  }

  function init(doc, win, api = requestApi) {
    const $ = (id) => doc.getElementById('dish-' + id);
    if (!$('workspace')) return;
    const form = $('form');
    const field = (name) => form.elements.namedItem(name);
    const state = { profile: '', scanStoreDir: null, dishes: [], selected: null, original: null,
      etag: null, baseline: '', isNew: false, busy: false, loaded: false };
    const driverInputs = new Map();
    const peers = new Map();
    let activeResource = 'dishes';
    let dishesReady = false;
    const anyBusy = () => state.busy || [...peers.values()].some((editor) => editor.isBusy());
    const anyDirty = () => dirty() || [...peers.values()].some((editor) => editor.dirty());
    const discardAll = () => !anyDirty() || win.confirm('Discard unsaved configuration changes?');

    function payload() {
      const data = {};
      TEXT.forEach((key) => { data[key] = field(key).value.trim() || null; });
      NUMBERS.forEach((key) => { data[key] = field(key).value === '' ? null : Number(field(key).value); });
      data.driver_config = {};
      driverInputs.forEach((input, key) => {
        data.driver_config[key] = input.type === 'checkbox' ? input.checked
          : input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value.trim();
      });
      return data;
    }
    function dirty() { return !form.hidden && JSON.stringify(payload()) !== state.baseline; }
    function update() {
      const readOnly = win.DmdSession?.can_edit === false;
      const processing = anyBusy();
      $('dirty').hidden = !dirty();
      $('save').disabled = readOnly || processing || form.hidden || (!state.isNew && !dirty());
      $('delete').hidden = form.hidden || state.isNew;
      $('delete').disabled = readOnly || processing || !state.etag;
      $('fields').disabled = readOnly || processing;
      $('profile').disabled = readOnly || anyBusy() || !!win.DmdOpt?.isBusy() || !state.loaded;
      $('refresh').disabled = anyBusy();
      (doc.querySelectorAll?.('[data-admin-tab]') || []).forEach((tab) => { tab.disabled = anyBusy(); });
      peers.forEach((editor) => editor.update());
      $('new').disabled = readOnly || processing || !state.loaded || !state.etag;
      $('rows').querySelectorAll('[data-dish-id]').forEach((row) => {
        row.tabIndex = processing ? -1 : 0;
        row.setAttribute('aria-disabled', String(processing));
      });
    }
    function busy(value) {
      state.busy = value;
      if (win.dispatchEvent && win.CustomEvent) win.dispatchEvent(new win.CustomEvent('dmd:profile-state'));
      $('workspace').setAttribute('aria-busy', String(value)); update();
    }
    function message(text, error = false, profile = null) {
      $('message').textContent = profile === null ? text : text + 'Config Profile: ' + profile + '.';
      if (profile !== null) $('message').innerHTML = escape(text) + 'Config Profile: <strong>' + escape(profile) + '</strong>.';
      $('message').classList.toggle('is-error', error);
      $('message').setAttribute('role', error ? 'alert' : 'status');
    }
    function connected(value) {
      $('connection').textContent = value ? 'WEB connected' : 'WEB disconnected';
      $('connection').classList.toggle('is-live', value);
      $('connection').classList.toggle('is-error', !value);
    }
    function failure(error) {
      if (['web_unavailable', 'not_configured', 'invalid_response'].includes(error.code)) connected(false);
      message(error.code === 'version_conflict'
        ? 'This configuration changed since you opened it. Your edits are kept here; refresh and review the latest version before saving.'
        : error.code === 'profile_changed'
          ? 'Another session selected a different Config Profile. Your edits are kept here; refresh before continuing.'
          : error.message, true);
    }
    function discard() { return !dirty() || win.confirm('Discard unsaved dish changes?'); }
    function renderRows() {
      const term = $('filter').value.trim().toLowerCase();
      const dishes = state.dishes.filter((dish) => [dish.dsh_id, dish.short_desc, dish.driver_type].join(' ').toLowerCase().includes(term));
      $('count').textContent = '(' + state.dishes.length + ')';
      $('list-profile').textContent = state.profile ? 'Config Profile: ' + state.profile : 'No Config Profile loaded';
      if (state.profile) $('list-profile').innerHTML = 'Config Profile: <strong>' + escape(state.profile) + '</strong>';
      $('rows').innerHTML = dishes.map((dish) => `<tr data-dish-id="${escape(dish.dsh_id)}" tabindex="0" aria-label="Edit ${escape(dish.dsh_id)}" aria-selected="${dish.dsh_id === state.selected && !state.isNew}" class="${dish.dsh_id === state.selected && !state.isNew ? 'is-selected' : ''}"><td><strong>${escape(dish.dsh_id)}</strong><small>${escape(dish.short_desc || 'No description')}</small></td><td>${escape(dish.diameter)} m</td><td>${escape(dish.driver_type)}</td></tr>`).join('')
        || '<tr><td colspan="3" class="zk-muted">' + (state.dishes.length ? 'No dishes match your filter.' : 'No dishes in this Config Profile. Choose New dish to create one.') + '</td></tr>';
      update();
    }
    function renderDriver(type, config = {}) {
      driverInputs.clear();
      $('driver-fields').replaceChildren();
      Object.entries({ ...DRIVER_DEFAULTS[type], ...config }).forEach(([key, value]) => {
        const label = doc.createElement('label');
        label.className = 'zk-field';
        label.append(key.replaceAll('_', ' '));
        const input = doc.createElement('input');
        input.className = 'zk-input';
        input.name = 'driver_' + key;
        input.type = typeof value === 'boolean' ? 'checkbox' : typeof value === 'number' ? 'number' : 'text';
        if (input.type === 'checkbox') input.checked = value;
        else { input.value = value ?? ''; input.required = true; }
        if (input.type === 'number') input.step = key === 'port' || key === 'imu_port' ? '1' : 'any';
        label.append(input);
        $('driver-fields').append(label);
        driverInputs.set(key, input);
      });
    }
    function show(dish, isNew = false) {
      state.original = JSON.parse(JSON.stringify(dish));
      state.isNew = isNew;
      TEXT.concat(NUMBERS).forEach((key) => { field(key).value = dish[key] ?? ''; });
      field('dsh_id').readOnly = !isNew;
      renderDriver(dish.driver_type, dish.driver_config);
      form.hidden = false;
      $('editor-title').textContent = isNew ? 'New dish' : dish.dsh_id;
      $('editor-subtitle').textContent = 'Config Profile: ' + state.profile;
      if (state.profile) $('editor-subtitle').innerHTML = 'Config Profile: <strong>' + escape(state.profile) + '</strong>';
      $('save').textContent = isNew ? 'Create dish' : 'Save dish';
      state.baseline = JSON.stringify(payload());
      renderRows();
    }
    function hideEditor() {
      form.hidden = true; state.original = null; state.selected = null; state.isNew = false;
      $('editor-title').textContent = 'Choose a dish';
      $('editor-subtitle').textContent = 'Select a dish to view its configuration, or create a new one.';
      update();
    }
    async function readDish(id) {
      const result = await api('dishes/' + encodeURIComponent(id), { profile: state.profile });
      if (result.payload.profile !== state.profile) throw Object.assign(new Error('Profile changed'), { code: 'profile_changed' });
      if (!result.etag) throw new Error('WEB did not provide a configuration version. Refresh before editing.');
      state.etag = result.etag; state.selected = id;
      show(result.payload.dish);
    }
    async function list(preferred) {
      const result = await api('dishes', { profile: state.profile });
      if (result.payload.profile !== state.profile) throw Object.assign(new Error('Profile changed'), { code: 'profile_changed' });
      state.dishes = [...result.payload.dishes].sort((a, b) => a.dsh_id.localeCompare(b.dsh_id));
      state.etag = result.etag;
      const selected = state.dishes.find((dish) => dish.dsh_id === preferred) || state.dishes[0];
      renderRows();
      if (selected) await readDish(selected.dsh_id); else hideEditor();
      dishesReady = true;
    }
    function resetResources(profile, scanStoreDir = null) {
      state.profile = profile; state.scanStoreDir = scanStoreDir; state.dishes = []; state.etag = null; dishesReady = false;
      hideEditor(); renderRows();
      peers.forEach((editor) => editor.reset(profile));
    }
    async function loadActive(preferred) {
      if (activeResource === 'dishes') {
        await list(preferred);
        message('Loaded ' + state.dishes.length + ' dishes from ', false, state.profile);
      } else if (peers.has(activeResource)) {
        await peers.get(activeResource).activate(state.profile);
      } else message('Loaded Config Profile: ' + state.profile + '.');
    }
    let publishedProfile = null;
    function notifyProfile() {
      if (publishedProfile === state.profile) return;
      publishedProfile = state.profile;
      if (win.dispatchEvent && win.CustomEvent) win.dispatchEvent(new win.CustomEvent('dmd:profile', { detail: { profile: state.profile, scan_store_dir: state.scanStoreDir } }));
    }
    async function refresh() {
      if (anyBusy() || win.DmdOpt?.isBusy() || !discardAll()) return;
      busy(true); message('Loading configuration…');
      try {
        const result = await api('profiles');
        const preferred = result.payload.profile === state.profile ? state.selected : null;
        $('profile').replaceChildren(...result.payload.profiles.map((profile) => {
          const option = doc.createElement('option');
          option.value = profile.profile; option.textContent = profile.profile + (profile.available ? '' : ' (unavailable)');
          option.disabled = !profile.available;
          return option;
        }));
        resetResources(result.payload.profile, result.payload.scan_store_dir);
        $('profile').value = state.profile;
        state.loaded = true;
        notifyProfile();
        await loadActive(preferred); connected(true);
      } catch (error) { failure(error); } finally { busy(false); }
    }
    async function switchProfile() {
      const name = $('profile').value;
      if (anyBusy() || win.DmdOpt?.isBusy() || !discardAll()) { $('profile').value = state.profile; return; }
      busy(true); message('Selecting Config Profile…');
      try {
        const result = await api('profile', { method: 'PUT', body: { profile: name } });
        resetResources(result.payload.profile, result.payload.scan_store_dir);
        notifyProfile();
        await loadActive(); connected(true);
      } catch (error) { $('profile').value = state.profile; failure(error); } finally { busy(false); }
    }
    async function activateResource(name) {
      if (anyBusy()) return;
      activeResource = name;
      if (!state.loaded) return refresh();
      if (name === 'dishes' && dishesReady) {
        message('Config Profile: ' + state.profile + '. Select a dish to edit.'); return;
      }
      busy(true);
      try { await loadActive(state.selected); }
      catch (error) { failure(error); } finally { busy(false); }
    }
    const admin = {
      request: api, profile: () => state.profile, selection: () => ({profile: state.profile, scan_store_dir: state.scanStoreDir}), isBusy: anyBusy, isDirty: anyDirty,
      update, message, connected, failure,
      register(name, editor) {
        peers.set(name, editor);
        editor.reset(state.profile);
        if (state.loaded && activeResource === name) activateResource(name);
      },
      activate: activateResource,
    };
    win.DmdAdmin = admin;
    async function save(event) {
      event.preventDefault();
      if (state.busy || !form.reportValidity()) return;
      if (!state.etag) { message('Refresh the dish list before saving.', true); return; }
      const data = payload(); const isNew = state.isNew;
      busy(true); message('Saving dish…');
      let saved = false;
      try {
        const result = await api(isNew ? 'dishes' : 'dishes/' + encodeURIComponent(state.selected), {
          method: isNew ? 'POST' : 'PATCH', body: data, profile: state.profile, etag: state.etag,
        });
        saved = true; state.etag = result.etag; state.selected = result.payload.dish.dsh_id;
        show(result.payload.dish);
        await list(state.selected); connected(true);
        message('Saved ' + state.selected + ' to Config Profile: ' + state.profile + '. Dish Manager loads this on startup or RESYNC.');
      } catch (error) {
        if (saved) message('Dish saved, but the list could not be refreshed. ' + error.message, true);
        else failure(error);
      } finally { busy(false); }
    }
    async function deleteDish() {
      if (state.busy || state.isNew || !state.selected || !state.etag) return;
      const id = state.selected;
      const prompt = 'Delete ' + id + ' from Config Profile: ' + state.profile + '? This removes its stored dish definition.'
        + (dirty() ? ' Unsaved edits to this dish will also be discarded.' : '');
      if (!win.confirm(prompt)) return;
      busy(true); message('Deleting ' + id + '…');
      let deleted = false;
      try {
        const result = await api('dishes/' + encodeURIComponent(id), {
          method: 'DELETE', profile: state.profile, etag: state.etag,
        });
        deleted = true; state.etag = result.etag;
        state.dishes = state.dishes.filter((dish) => dish.dsh_id !== id);
        hideEditor(); renderRows();
        await list(); connected(true);
        message('Deleted ' + id + ' from Config Profile: ' + state.profile + '. Dish Manager loads this on startup or RESYNC.');
      } catch (error) {
        if (deleted) message('Dish deleted, but the list could not be refreshed. ' + error.message, true);
        else failure(error);
      } finally { busy(false); }
    }
    $('delete').addEventListener('click', deleteDish);
    $('refresh').addEventListener('click', refresh);
    $('profile').addEventListener('change', switchProfile);
    $('filter').addEventListener('input', renderRows);
    async function selectRow(event) {
      const row = event.target.closest('[data-dish-id]');
      if (!row || state.busy || (row.dataset.dishId === state.selected && !state.isNew) || !discard()) return;
      busy(true);
      try { await readDish(row.dataset.dishId); message('Editing ' + state.selected + '.'); }
      catch (error) { failure(error); } finally { busy(false); }
      if (event.type === 'keydown') {
        Array.from($('rows').querySelectorAll('[data-dish-id]'))
          .find((item) => item.dataset.dishId === row.dataset.dishId)?.focus();
      }
    }
    $('rows').addEventListener('click', selectRow);
    $('rows').addEventListener('keydown', (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
        event.preventDefault();
        return selectRow(event);
      }
    });
    $('new').addEventListener('click', () => {
      if (state.busy || !discard()) return;
      show({ dsh_id: '', short_desc: null, diameter: 0, fd_ratio: 0, latitude: 0, longitude: 0,
        height: 0, feed_type: 'NONE', dig_id: null, ws_id: null, driver_type: 'DRIFT',
        driver_poll_period: 1000, driver_config: { ...DRIVER_DEFAULTS.DRIFT } }, true);
      message('Enter a unique dish ID and its configuration.'); field('dsh_id').focus();
    });
    $('cancel').addEventListener('click', () => {
      if (state.busy || !discard()) return;
      if (state.isNew) {
        hideEditor(); renderRows(); message('New dish cancelled. Select a dish or choose New dish.');
      } else { show(state.original); message('Unsaved edits cancelled.'); }
    });
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    field('driver_type').addEventListener('change', () => { renderDriver(field('driver_type').value); update(); });
    form.addEventListener('submit', save);
    win.addEventListener('beforeunload', (event) => { if (anyDirty()) { event.preventDefault(); event.returnValue = ''; } });
    win.addEventListener('dmd:session', update);
    win.addEventListener('dmd:admin-tab', (event) => activateResource(event.detail));
    win.addEventListener('dmd:view', (event) => { if (event.detail === 'admin' && !state.loaded) refresh(); });
    if ($('profile').dataset?.globalProfile !== undefined || doc.querySelector('[data-view="admin"]')?.classList.contains('is-active')) refresh();
    return { refresh, switchProfile, save, payload, dirty, state, admin };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, requestApi, DRIVER_DEFAULTS };
  if (typeof document !== 'undefined') init(document, window);
})();
