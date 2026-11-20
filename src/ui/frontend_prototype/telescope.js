/* Observatory attributes are shared across every Config Profile. */
(function () {
  const FIELDS = ['name', 'latitude', 'longitude', 'height'];
  function init(doc, win, admin = win.DmdAdmin) {
    const $ = name => doc.getElementById('telescope-' + name);
    if (!$('workspace') || !admin) return;
    const form = $('form');
    const field = name => form.elements.namedItem(name);
    const state = {loaded:false, busy:false, etag:null, original:null, baseline:''};
    const snapshot = () => JSON.stringify(FIELDS.map(name => field(name).value));
    const dirty = () => state.loaded && snapshot() !== state.baseline;
    function update() {
      const locked = state.busy || admin.isBusy() || win.DmdSession?.can_edit === false;
      $('fields').disabled = locked || !state.loaded;
      $('save').disabled = locked || !dirty() || !state.etag;
      $('cancel').disabled = locked || !dirty();
      $('dirty').hidden = !dirty();
    }
    function busy(value) {
      state.busy = value;
      $('workspace').setAttribute('aria-busy', String(value));
      admin.update();
    }
    function show(config) {
      state.original = {...config};
      FIELDS.forEach(name => field(name).value = config[name] ?? '');
      state.baseline = snapshot(); state.loaded = true; form.hidden = false;
      update();
    }
    function reset() {
      state.loaded = false; state.etag = null; state.original = null;
      form.hidden = true; update();
    }
    async function activate() {
      if (state.loaded) {
        admin.message('Telescope configuration applies across all Config Profiles.');
        return;
      }
      busy(true); admin.message('Loading shared Telescope configuration…');
      try {
        const result = await admin.request('telescope/config');
        if (!result.payload?.config || !result.etag) throw new Error('WEB returned invalid Telescope configuration.');
        state.etag = result.etag; show(result.payload.config);
        admin.connected(true);
        admin.message('Telescope configuration applies across all Config Profiles.');
      } finally { busy(false); }
    }
    async function save(event) {
      event.preventDefault();
      if (state.busy || admin.isBusy() || win.DmdSession?.can_edit === false || !state.loaded || !dirty() || !form.reportValidity()) return;
      const data = {name:field('name').value.trim()};
      if (!data.name) { admin.message('Enter an observatory name.', true); return; }
      for (const name of FIELDS.slice(1)) {
        const raw = field(name).value.trim();
        data[name] = raw ? Number(raw) : null;
        if (data[name] !== null && !Number.isFinite(data[name])) { admin.message('Coordinates and height must be finite numbers.', true); return; }
      }
      busy(true); admin.message('Saving shared Telescope configuration…');
      try {
        const result = await admin.request('telescope/config', {method:'PUT', body:data, etag:state.etag});
        if (!result.payload?.config || !result.etag) throw new Error('Save was not confirmed. Refresh to check Telescope configuration before retrying.');
        state.etag = result.etag; show(result.payload.config);
        admin.connected(true);
        admin.message('Telescope configuration saved for all Config Profiles. Select TM and click RESYNC to apply the changes.');
      } catch (error) { admin.failure(error); }
      finally { busy(false); }
    }
    $('cancel').addEventListener('click', () => {
      if (state.busy || admin.isBusy() || !dirty() || !win.confirm('Discard unsaved Telescope changes?')) return;
      show(state.original); admin.message('Unsaved Telescope changes cancelled.'); admin.update();
    });
    form.addEventListener('input', () => admin.update());
    form.addEventListener('change', () => admin.update());
    form.addEventListener('submit', save);
    admin.register('telescope', {reset, activate, update, dirty, isBusy:() => state.busy});
    return {state, save, activate, reset, dirty};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {init};
  if (typeof document !== 'undefined') init(document, window);
})();
