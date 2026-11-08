/* Live desired/actual digitiser controls; all writes go through TM. */
(function () {
  const unwrap = value => value && typeof value === 'object' && 'value' in value ? value.value : value;
  const escape = value => String(value ?? '—').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const FIELDS = [
    ['load_active', 'Load', 'bool'], ['gain', 'Gain [dB]', 'number', 0, 100],
    ['center_freq', 'Centre frequency [Hz]', 'number', 0], ['sample_rate', 'Sample rate [Hz]', 'number', 0],
    ['bandwidth', 'Bandwidth [Hz]', 'number', 0], ['freq_correction', 'Frequency correction [ppm]', 'integer', -1000, 1000],
    ['channels', 'Channels', 'integer', 0], ['scan_duration', 'Scan duration [s]', 'integer', 0],
  ];
  function scanning(value) {
    value = unwrap(value);
    return !(value == null || value === false || value === 0 || value === '' || ['off', 'false', '0'].includes(String(value).toLowerCase()));
  }
  function actual(item, key) {
    return unwrap((['channels', 'scan_duration'].includes(key) && item.scanModel ? item.scanModel : item.model)?.[key]);
  }
  function initial(item) {
    const values = {};
    for (const [key, , type] of FIELDS) {
      const value = actual(item, key);
      values[key] = value == null ? '' : type === 'bool' ? String(value === true) : String(value);
    }
    values.scanning = String(scanning(item.model.scanning));
    return { values, busy: false, result: null };
  }
  function read(draft, field) {
    const definition = FIELDS.find(([key]) => key === field), raw = draft.values[field];
    if (field === 'scanning' || definition?.[2] === 'bool') {
      if (!['true', 'false'].includes(raw)) throw new Error('Choose ' + (definition?.[1] || 'Scanning'));
      return raw === 'true';
    }
    const [, label, type, min, max] = definition;
    const value = Number(raw);
    if (raw == null || String(raw).trim() === '' || !Number.isFinite(value)
        || value < min || (max != null && value > max) || (type === 'integer' && !Number.isInteger(value))
        || (['center_freq', 'sample_rate', 'bandwidth', 'channels', 'scan_duration'].includes(field) && value <= 0)) {
      throw new Error('Enter a valid ' + label.toLowerCase());
    }
    return value;
  }
  function render(item, draft = initial(item), snapshot, session) {
    const disabled = session?.can_edit === false || draft.busy || !snapshot || snapshot.stale || snapshot.tm_connected !== true
      || unwrap(item.model.tm_connected) !== 'ESTABLISHED';
    const running = scanning(item.model.scanning);
    const input = (key, label, type) => type === 'bool'
      ? `<select class="zk-select" data-digitiser-field="${key}" aria-label="Desired ${label}"${disabled || (key !== 'scanning' && running) ? ' disabled' : ''}><option value="">Choose…</option>${[['false', 'OFF'], ['true', 'ON']].map(([value, text]) => `<option value="${value}"${draft.values[key] === value ? ' selected' : ''}>${text}</option>`).join('')}</select>`
      : `<input class="zk-input" type="text" inputmode="decimal" data-digitiser-field="${key}" aria-label="Desired ${label}" value="${escape(draft.values[key])}"${disabled || running ? ' disabled' : ''}>`;
    const show = (value, bool = false) => value == null ? '—' : bool ? value ? 'ON' : 'OFF' : String(value);
    const rows = FIELDS.map(([key, label, type]) => `<tr><th scope="row">${label}</th><td>${input(key, label, type)}</td><td>${escape(show(actual(item, key), type === 'bool'))}</td><td><button type="button" class="zk-btn is-sm" data-digitiser-action="${key}"${disabled || running ? ' disabled' : ''}>Set</button></td></tr>`).join('');
    const args = item.app.arguments || {};
    const diagnostics = [['SDR type', item.model.sdr_type], ['Bandpass filter', unwrap(item.model.bpf_type)],
      ['Temperature [°C]', item.model.temp_reading?.temperature], ['Temperature limit [°C]', item.model.temp_max],
      ['Observation', typeof item.model.scanning === 'object' ? item.model.scanning?.obs_id : null], ['Local host', args.local_host]];
    return `<div class="digitiser-live-layout" data-digitiser-control-app="${escape(item.key)}"><section class="digitiser-live-controls"><h4>Property configuration</h4><div class="status-table-scroll"><table class="zk-tbl digitiser-control-table"><thead><tr><th>Setting</th><th>Desired</th><th>Actual</th><th></th></tr></thead><tbody>${rows}`
      + `<tr><th scope="row">Scanning</th><td>${input('scanning', 'Scanning', 'bool')}</td><td>${scanning(item.model.scanning) ? 'ON' : 'OFF'}</td><td><button type="button" class="zk-btn is-sm" data-digitiser-action="scanning"${disabled ? ' disabled' : ''}>Set</button></td></tr></tbody></table></div>`
      + `<div class="digitiser-live-actions"><button type="button" class="zk-btn is-primary" data-digitiser-action="apply"${disabled || running ? ' disabled' : ''}>${draft.busy ? 'Sending…' : 'Apply settings'}</button><button type="button" class="zk-btn" data-digitiser-action="actual"${draft.busy ? ' disabled' : ''}>Use actual values</button></div>`
      + '<p class="zk-muted digitiser-control-note">Changes go through TM. Actual values update from application reports. Channels and scan duration are reported by SDP. Scanning starts with the reported hardware settings; apply tuning changes first.</p>'
      + `<p class="digitiser-control-result${draft.result?.status === 'error' ? ' is-error' : ''}" role="status" aria-live="polite">${draft.result ? escape(draft.result.time + ' · ' + draft.result.status + ': ' + draft.result.message) : ''}</p></section>`
      + '<section class="digitiser-live-info"><h4>Driver and status</h4><dl class="status-kv">' + diagnostics.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(show(value))}</dd></div>`).join('') + '</dl></section></div>';
  }
  function init(doc, win, fetcher, changed) {
    const root = doc.getElementById('status-detail-content'), drafts = new Map();
    let current = null, snapshot = null;
    const draftFor = item => { if (!drafts.has(item.key)) drafts.set(item.key, initial(item)); return drafts.get(item.key); };
    const result = (draft, status, message) => { draft.result = { status, message, time: new Date().toISOString().slice(0, 19).replace('T', ' ') + ' UTC' }; changed(); };
    function view(item, data) { current = item; snapshot = data; return render(item, draftFor(item), data, win.DmdSession); }
    async function send(action) {
      if (!current) return;
      const item = current, draft = draftFor(item);
      if (action === 'actual') {
        if (!draft.busy) { draft.values = initial(item).values; draft.result = null; changed(); }
        return;
      }
      if (win.DmdSession?.can_edit === false || draft.busy || snapshot?.stale || snapshot?.tm_connected !== true
          || unwrap(item.model.tm_connected) !== 'ESTABLISHED') return;
      if (action !== 'scanning' && scanning(item.model.scanning)) return;
      let body;
      try {
        if (action === 'apply') body = Object.fromEntries(FIELDS.map(([key]) => [key, read(draft, key)]));
        else if (action === 'scanning') {
          body = { scanning: read(draft, 'scanning') };
          if (body.scanning) Object.assign(body, { channels: read(draft, 'channels'), scan_duration: read(draft, 'scan_duration') });
        } else if (FIELDS.some(([key]) => key === action)) body = { [action]: read(draft, action) };
        else return;
      } catch (error) { result(draft, 'error', error.message); return; }
      draft.busy = true; result(draft, 'pending', 'Sending configuration to TM…');
      const controller = new AbortController(), timer = win.setTimeout(() => controller.abort(), 35000);
      try {
        const response = await fetcher('/api/digitiser-control/' + encodeURIComponent(item.id), {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body), signal: controller.signal });
        const answer = await response.json();
        if (!['success', 'error'].includes(answer.status) || typeof answer.message !== 'string') throw new Error('Invalid response');
        const status = response.ok && answer.status === 'success' ? 'success' : 'error';
        result(draft, status, (answer.code ? '(' + answer.code + ') ' : '') + answer.message
          + (status === 'success' ? ' Actions scheduled; awaiting fresh application reports.' : ''));
      } catch (_) { result(draft, 'error', 'No valid response received. Outcome unknown; check the digitiser before retrying.'); }
      finally { win.clearTimeout(timer); draft.busy = false; changed(); }
    }
    for (const eventName of ['input', 'change']) root.addEventListener(eventName, event => {
      const field = event.target.dataset?.digitiserField;
      if (current && (field === 'scanning' || FIELDS.some(([key]) => key === field))) draftFor(current).values[field] = event.target.value;
    });
    root.addEventListener('click', event => {
      const action = event.target.closest('[data-digitiser-action]')?.dataset.digitiserAction;
      if (action) return send(action);
    });
    return { view, send, drafts, clearSelection() { current = null; snapshot = null; } };
  }
  const api = { init, render, actual, scanning };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.DmdDigitiserStatus = api;
})();
