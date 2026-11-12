/* RESYNC controls share the configuration editors' profile and busy state. */
(function () {
  const FIXED = ['tm', 'dm', 'sdp', 'ws', 'web'];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  function init(doc, win, admin = win.DmdAdmin, fetcher = win.fetch?.bind(win)) {
    const $ = id => doc.getElementById('config-resync-' + id);
    if (!$('button') || !admin) return;
    const state = { profile: '', busy: false, loading: false, message: '', status: '', generation: 0 };
    function options(digitisers = []) {
      const previous = $('app').value;
      const apps = [...FIXED.map(id => ({ id, label: id.toUpperCase() })), ...digitisers.map(dig => ({ id: dig.dig_id, label: dig.dig_id }))
        .filter(item => typeof item.id === 'string').sort((a, b) => a.id.localeCompare(b.id))];
      $('app').innerHTML = '<option value="">Select app…</option>' + apps.map(item => `<option value="${escape(item.id)}">${escape(item.label)}</option>`).join('');
      $('app').value = apps.some(item => item.id === previous) ? previous : '';
    }
    function update() {
      const dirty = admin.isDirty?.() || false;
      if (dirty && !state.busy) { state.message = ''; state.status = ''; }
      const readOnly = win.DmdSession?.can_edit === false;
      $('app').disabled = state.busy || state.loading || admin.isBusy() || readOnly;
      $('button').disabled = !state.profile || !$('app').value || dirty || state.loading || admin.isBusy() || readOnly;
      $('button').textContent = state.busy ? 'Sending…' : 'RESYNC';
      for (const status of ['success', 'error', 'pending']) $('button').classList.toggle('is-' + status, status === state.status);
      const message = state.message || (dirty ? 'Save changes before RESYNC.' : 'Reloads the app’s launch Config Profile. Restart the app to use a different Config Profile.');
      if ($('result').textContent !== message) $('result').textContent = message;
    }
    async function reset(profile) {
      const generation = ++state.generation;
      state.profile = profile; state.loading = false; state.message = ''; state.status = ''; options();
      if (!profile) { update(); return; }
      state.loading = true; update();
      try {
        const data = await admin.request('digitisers', { profile });
        if (generation === state.generation) options(data.payload.digitisers || []);
      } catch (_) {
        if (generation === state.generation) state.message = 'Digitiser list unavailable; standard apps remain available.';
      } finally {
        if (generation === state.generation) { state.loading = false; admin.update(); }
      }
    }
    async function send() {
      const target = $('app').value;
      if (!target || !state.profile || state.loading || admin.isBusy() || admin.isDirty?.() || win.DmdSession?.can_edit === false) return;
      state.busy = true; state.status = 'pending'; state.message = 'Sending RESYNC to ' + target + '…'; admin.update();
      const controller = new AbortController();
      const timeout = win.setTimeout(() => controller.abort(), 35000);
      try {
        const response = await fetcher('/api/commands', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ app: target, cmd: 'resync' }), signal: controller.signal });
        const body = await response.json();
        if (!['success', 'error'].includes(body.status) || typeof body.message !== 'string') throw new Error('Invalid response');
        state.status = response.ok && body.status === 'success' ? 'success' : 'error';
        state.message = target + ' · ' + new Date().toISOString().slice(0, 19).replace('T', ' ') + ' UTC · '
          + state.status + (body.code ? ' (' + body.code + ')' : '') + ': ' + body.message;
      } catch (_) {
        state.status = 'error';
        state.message = target + ': No valid response received. RESYNC outcome is unknown; check the application before retrying.';
      } finally {
        win.clearTimeout(timeout); state.busy = false; admin.update();
      }
    }
    $('app').addEventListener('change', () => { state.message = ''; state.status = ''; update(); });
    $('button').addEventListener('click', send);
    admin.register('commands', { reset, update, dirty: () => false, isBusy: () => state.busy || state.loading });
    return { send, reset, state };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init };
  if (typeof document !== 'undefined') init(document, window);
})();
