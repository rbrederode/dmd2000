/* Queued/running observations from WEB's shared TM snapshot cache. */
(function () {
  const unwrap = value => value && typeof value === 'object' && 'value' in value ? value.value : value;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const show = value => { value = unwrap(value); return value == null || value === '' ? '—' : String(value); };
  const obsState = value => ({ 0: 'EMPTY', 2: 'IDLE', 3: 'CONFIGURING', 4: 'READY', 5: 'SCANNING', 6: 'ABORTED', 9: 'FAULT' }[unwrap(value)] || unwrap(value));
  const scanState = value => ({ 0: 'EMPTY', 1: 'WIP', 2: 'ABORTED', 3: 'COMPLETE' }[unwrap(value)] || unwrap(value));
  const active = new Set(['CONFIGURING', 'READY', 'SCANNING']);
  const dateValue = value => value == null || unwrap(value) === '' ? NaN : Date.parse(unwrap(value));
  function timestamp(value) {
    const parsed = dateValue(value);
    return Number.isFinite(parsed) ? new Date(parsed).toISOString().slice(0, 19).replace('T', ' ') : '—';
  }
  function duration(value) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
    const seconds = Math.round(value);
    return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(part => String(part).padStart(2, '0')).join(':');
  }
  function scansFor(obs) {
    return (Array.isArray(obs.target_scans) ? obs.target_scans : []).flatMap(set =>
      (Array.isArray(set?.scans) ? set.scans : []).filter(Boolean).map((scan, index) => ({
        scan, targetIndex: scan.tgt_idx ?? set.tgt_idx,
        current: !obs._history && active.has(obsState(obs.obs_state)) && set.tgt_idx === obs.tgt_idx && index === obs.tgt_scan
          && scanState(scan.status) !== 'COMPLETE',
      })));
  }
  function completed(obs) {
    const scans = scansFor(obs);
    const targets = obs.target_configs;
    return (Array.isArray(targets) && targets.length > 0 && Number.isInteger(obs.tgt_idx) && obs.tgt_idx >= targets.length)
      || (scans.length > 0 && scans.every(({ scan }) => scanState(scan.status) === 'COMPLETE'));
  }
  function observations(model, now = Date.now()) {
    const list = model?.oda?.obs_store?.obs_list;
    if (!Array.isArray(list)) return null;
    return list.filter(obs => {
      if (!obs?.obs_id) return false;
      const status = obsState(obs.obs_state);
      const end = dateValue(obs.scheduling_block_end);
      if (status === 'ABORTED') return Number.isFinite(end) && end > now;
      if (completed(obs)) return false;
      if (active.has(status)) return true;
      return ['EMPTY', 'IDLE'].includes(status) && (!Number.isFinite(end) || end > now);
    }).sort((a, b) => {
      const running = Number(active.has(obsState(b.obs_state))) - Number(active.has(obsState(a.obs_state)));
      const startA = dateValue(a.scheduling_block_start), startB = dateValue(b.scheduling_block_start);
      return running || (Number.isFinite(startA) ? startA : Infinity) - (Number.isFinite(startB) ? startB : Infinity)
        || String(a.obs_id).localeCompare(String(b.obs_id));
    });
  }
  function chip(value) {
    const style = ['SCANNING', 'WIP'].includes(value) ? 'is-live' : value === 'ABORTED' || value === 'FAULT' ? 'is-bad'
      : ['CONFIGURING', 'READY'].includes(value) ? 'is-warn' : value === 'COMPLETE' ? 'is-live' : 'is-empty';
    return `<span class="zk-chip ${style}">${escape(show(value))}</span>`;
  }
  function controlOperation(obs, now = Date.now()) {
    const status = obsState(obs.obs_state);
    if (active.has(status)) return 'abort';
    if (status === 'ABORTED' && dateValue(obs.scheduling_block_end) > now) return 'restart';
    const started = (obs.tgt_idx > 0 || obs.tgt_scan > 0 || scansFor(obs).some(({scan}) =>
      scanState(scan.status) !== 'EMPTY' || scan.read_start != null || scan.read_end != null || scan.tgt_acq_dt != null));
    return ['EMPTY', 'IDLE'].includes(status) && !started ? 'delete' : null;
  }
  function controlButton(obs, controls) {
    const operation = controlOperation(obs);
    if (!operation || !controls || obs._history) return '';
    const pending = controls.pending?.has(obs.obs_id);
    const label = {delete:'Delete',abort:'Abort',restart:'Restart'}[operation];
    const title = operation === 'restart' ? 'Restart this observation from the first scan' : operation === 'delete'
      ? 'Remove this unstarted observation from the schedule; saved files are retained' : 'Abort this executing observation';
    return `<div class="schedule-controls"><button type="button" class="zk-btn is-primary is-sm" data-schedule-control="${operation}" data-observation-id="${escape(obs.obs_id)}" title="${title}"${controls.disabled || pending ? ' disabled' : ''}>${pending ? 'Awaiting TM…' : label}</button></div>`;
  }
  function historyObservations(entries, model, now = Date.now()) {
    const live = new Set((observations(model, now) || []).map(obs => obs.obs_id));
    const unique = new Map();
    for (const entry of entries) {
      if (!entry?.id || live.has(entry.id)) continue;
      const previous = unique.get(entry.id);
      if (!previous || Number(entry.revision || 0) > Number(previous.revision || 0)) unique.set(entry.id, entry);
    }
    return [...unique.values()].map(entry => ({ ...entry, _history: true, obs_id: entry.id, dsh_id: entry.dish,
      obs_state: entry.state, disk_completed: entry.completed,
      file_key: (entry.folder || '.') + '/' + entry.filename,
    })).sort((a, b) => {
      const time = obs => dateValue(obs.started_at) || dateValue(obs.scheduling_block_start) || dateValue(obs.created) || 0;
      return time(b) - time(a) || String(a.obs_id).localeCompare(String(b.obs_id));
    });
  }
  function historyStatus(obs) {
    if (obs.error) return 'UNAVAILABLE';
    const saved = obsState(obs.obs_state);
    if (obs.disk_completed || completed(obs) || ['COMPLETE', 'COMPLETED'].includes(saved)) return 'COMPLETE';
    return ['ABORTED', 'FAULT'].includes(saved) ? saved : 'INCOMPLETE';
  }
  function scanDetails(obs, source = {}) {
    const scans = scansFor(obs);
    if (!scans.length) return '<p class="zk-muted schedule-no-scans">' + (obs._history ? 'The saved observation contains no scan records.' : 'TM has not prepared the scan list for this observation yet.') + '</p>';
    const finished = scans.filter(({ scan }) => scanState(scan.status) === 'COMPLETE').length;
    return `<div class="schedule-scan-heading"><strong>Scans</strong><span class="zk-muted">${finished} of ${scans.length} complete</span><progress class="schedule-scan-progress" value="${finished}" max="${scans.length}" aria-label="Scan completion" aria-valuetext="${finished} of ${scans.length} scans complete"></progress></div>`
      + `<div class="schedule-scan-scroll" tabindex="0" role="region" aria-label="Scans for ${escape(obs.obs_id)}" data-schedule-scan-scroll="${escape(obs.obs_id)}">`
      + '<table class="zk-tbl schedule-scans"><thead><tr><th>Scan<br><small>Target / frequency / iteration</small></th><th>Target</th><th>Type</th><th>Status</th><th>Digitiser</th><th>Frequency [MHz]</th><th>Gain [dB]</th><th>Duration [s]</th><th>First sample (UTC)</th><th>Last sample (UTC)</th><th title="View final-sample signal display">Signal</th><th title="Download completed scan spectrum">SPR</th></tr></thead><tbody>'
      + scans.map(({ scan, targetIndex, current }) => {
        const target = obs.targets?.find(item => item?.tgt_idx === targetIndex);
        const type = ({ 0: 'UNKNOWN', 1: 'SKY', 2: 'LOAD', 3: 'TSYS', 4: 'GAIN' }[unwrap(scan.scan_type)] || unwrap(scan.scan_type));
        const frequency = typeof scan.center_freq === 'number' && Number.isFinite(scan.center_freq) ? (scan.center_freq / 1e6).toFixed(3) : '—';
        let download = '', signal = '';
        const indices = [targetIndex, scan.freq_scan, scan.scan_iter];
        if (scanState(scan.status) === 'COMPLETE' && obs.obs_id && indices.every(value => Number.isInteger(value) && value >= 0)) {
          const fileId = source.filename ? source.filename.replace(/-obs\.json$/, '') : obs.obs_id;
          const query = new URLSearchParams();
          if (source.folder != null) query.set('folder', source.folder);
          if (source.profile) query.set('profile', source.profile);
          if (source.directory != null) { query.delete('folder'); query.delete('profile'); query.set('directory', source.directory); }
          const url = (source.directory != null ? '/api/opt/observations/' : '/api/web/observations/') + encodeURIComponent(fileId) + '/scans/' + indices.join('/') + '/spr'
            + (query.size ? '?' + query.toString() : '');
          const signalUrl = url.replace(/\/spr(?=\?|$)/, '/signal');
          signal = `<button type="button" class="zk-icon-btn schedule-scan-signal" data-scan-signal="${escape(signalUrl)}" data-scan-label="${escape(obs.obs_id + ' · ' + indices.join(' / '))}" aria-label="View signal for scan ${indices.join(' / ')}" title="View signal"><svg class="ico" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 3v18h18M5 15h3l3-9 4 12 3-5h3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg></button>`;
          download = `<button type="button" class="zk-icon-btn schedule-scan-download" data-scan-download="${escape(url)}" aria-label="Download SPR CSV for scan ${indices.join(' / ')}" title="Download SPR CSV"><svg class="ico" aria-hidden="true"><use href="#i-download" /></svg></button>`;
        }
        return `<tr${current ? ' class="is-current"' : ''}><td class="zk-mono">${escape([targetIndex, scan.freq_scan, scan.scan_iter].map(show).join(' / '))}${current ? '<span class="schedule-current">Current</span>' : ''}</td>`
          + `<td class="schedule-scan-target" title="${escape(target?.id || '')}">${escape(show(target?.id))}</td><td>${escape(show(type))}</td><td>${chip(scanState(scan.status))}</td>`
          + `<td>${escape(show(scan.dig_id))}</td><td>${frequency}</td><td>${escape(show(scan.gain))}</td><td>${escape(show(scan.duration))}</td>`
          + `<td class="schedule-date">${timestamp(scan.read_start)}</td><td class="schedule-date">${timestamp(scan.read_end)}</td><td class="schedule-scan-action">${signal}</td><td class="schedule-scan-action">${download}</td></tr>`;
      }).join('') + '</tbody></table></div>';
  }
  function rows(list, expanded, namespace = 'live', details = new Map(), profile = null, source = {}) {
    return list.map((obs, index) => {
      const key = obs._history ? obs.file_key : obs.obs_id;
      const open = expanded.has(key), id = 'schedule-' + namespace + '-scans-' + index;
      const observingDuration = obs.estimated_observation_duration || duration(obs.total_integration_time);
      const detail = details.get(key);
      const contents = !obs._history ? (open ? scanDetails(obs, { profile }) : '')
        : detail?.observation ? scanDetails({ ...detail.observation, _history: true }, { filename: obs.filename, folder: obs.folder, profile, ...source })
        : detail?.error ? `<p class="zk-muted">${escape(detail.error)} <button type="button" class="zk-btn is-sm" data-history-retry="${escape(key)}">Retry</button></p>`
        : '<p class="zk-muted" role="status">Loading saved scans…</p>';
      return `<tr class="schedule-observation${open ? ' is-expanded' : ''}" data-schedule-observation="${escape(key)}">`
        + `<td><button type="button" class="schedule-toggle" data-schedule-toggle="${escape(key)}" aria-expanded="${open}"${open ? ` aria-controls="${id}"` : ''} title="${escape(obs.obs_id)}"><span aria-hidden="true">${open ? '▾' : '▸'}</span><span>${escape(obs.obs_id)}</span></button></td>`
        + `<td class="schedule-title" title="${escape(obs.title || '')}">${escape(show(obs.title))}</td><td>${escape(show(obs.dsh_id))}</td><td${obs._history ? ` title="Saved execution state: ${escape(show(obs.obs_state))}"` : ''}>${chip(obs._history ? historyStatus(obs) : obsState(obs.obs_state))}</td>`
        + `<td class="schedule-date">${timestamp(obs.scheduling_block_start)}</td><td class="schedule-date">${timestamp(obs.scheduling_block_end)}</td>`
        + `<td class="zk-mono">${escape(observingDuration)}</td><td class="schedule-date">${timestamp(obs.created)}</td><td class="schedule-contact" title="${escape(obs.user_email || '')}"><div class="schedule-contact-content"><span class="schedule-contact-text">${escape(show(obs.user_email))}</span>${controlButton(obs, source.controls)}</div></td></tr>`
        + (open ? `<tr class="schedule-expanded" id="${id}"><td colspan="9">${contents}</td></tr>` : '');
    }).join('');
  }
  function init(doc, win, fetcher = fetch) {
    const $ = id => doc.getElementById('schedule-' + id);
    if (!$('observations')) return;
    const state = { data: null, expanded: new Set(), loading: false, stopped: false,
      downloads: new Set(), pendingControls: new Map(),
      history: { entries: null, expanded: new Set(), details: new Map(), loading: false, profile: null, generation: 0, checkedAt: 0 } };
    const refreshMarkup = $('refresh').innerHTML;
    function paint(body, region, html) {
      const scroll = region.scrollTop, horizontal = region.scrollLeft;
      const nested = new Map(Array.from(body.querySelectorAll('[data-schedule-scan-scroll]'))
        .map(node => [node.dataset.scheduleScanScroll, [node.scrollTop, node.scrollLeft]]));
      const focused = doc.activeElement?.dataset?.scheduleToggle;
      if (body.innerHTML !== html) {
        body.innerHTML = html;
        for (const node of body.querySelectorAll('[data-schedule-scan-scroll]')) {
          const position = nested.get(node.dataset.scheduleScanScroll);
          if (position) [node.scrollTop, node.scrollLeft] = position;
        }
        if (focused) Array.from(body.querySelectorAll('[data-schedule-toggle]'))
          .find(node => node.dataset.scheduleToggle === focused)?.focus({ preventScroll: true });
      }
      region.scrollTop = scroll; region.scrollLeft = horizontal;
    }
    function renderHistory() {
      if (!$('history-observations')) return;
      const history = state.history;
      const list = history.entries && historyObservations(history.entries, state.data?.telescope_model);
      $('history-count').textContent = list == null ? '—' : String(list.length);
      if (list) {
        const keys = new Set(list.map(obs => obs.file_key));
        for (const key of history.expanded) if (!keys.has(key)) history.expanded.delete(key);
      }
      const html = list == null ? '<tr><td colspan="9" class="zk-muted">Waiting for the saved observation catalogue.</td></tr>'
        : list.length ? rows(list, history.expanded, 'history', history.details, history.profile)
        : '<tr><td colspan="9" class="zk-muted">No historic observations were found outside the live queue.</td></tr>';
      paint($('history-observations'), $('history-scroll'), html);
    }
    function render() {
      const list = observations(state.data?.telescope_model);
      $('count').textContent = list == null ? '—' : String(list.length);
      if (list) {
        const ids = new Set(list.map(obs => obs.obs_id));
        for (const id of state.expanded) if (!ids.has(id)) state.expanded.delete(id);
      }
      const html = list == null ? '<tr><td colspan="9" class="zk-muted">Waiting for the observation queue from TM.</td></tr>'
        : list.length ? rows(list, state.expanded, 'live', new Map(), state.data?.profile, {controls:{
            disabled:state.data?.stale || state.data?.tm_connected === false || win.DmdSession?.can_edit === false,
            pending:state.pendingControls}}) : '<tr><td colspan="9" class="zk-muted">No observations are queued or running.</td></tr>';
      paint($('observations'), $('scroll'), html);
      renderHistory();
    }
    async function diskRequest(url, profile) {
      const controller = new AbortController(), timeout = win.setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetcher(url, { cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
          ...(profile ? { headers: { 'X-Config-Profile': profile } } : {}) });
        const body = await response.json();
        if (!response.ok || body.status !== 'success' || !body.payload) throw new Error(body.message || 'Saved observations unavailable');
        return body.payload;
      } finally { win.clearTimeout(timeout); }
    }
    async function loadScans(key) {
      const history = state.history;
      const entry = history.entries?.find(item => (item.folder || '.') + '/' + item.filename === key);
      if (!entry || history.details.get(key)?.loading || history.details.get(key)?.observation) return;
      const pending = { loading: true, revision: entry.revision };
      const generation = history.generation;
      history.details.set(key, pending); renderHistory();
      try {
        const filenameId = entry.filename.replace(/-obs\.json$/, '');
        const payload = await diskRequest('/api/web/observations/' + encodeURIComponent(filenameId)
          + '?folder=' + encodeURIComponent(entry.folder || '.'), history.profile);
        if (!payload.observation || payload.observation._type !== 'ObsModel') throw new Error('Saved scan metadata is invalid');
        pending.observation = payload.observation;
      } catch (error) { pending.error = error.message; }
      finally {
        pending.loading = false;
        if (!state.stopped && generation === history.generation && history.details.get(key) === pending) renderHistory();
      }
    }
    async function refreshHistory(force = false) {
      if (!$('history-observations')) return;
      const history = state.history;
      if (state.stopped || history.loading || (!force && Date.now() - history.checkedAt < 30000)) return;
      history.loading = true; $('history-refresh').disabled = true;
      try {
        const profile = await diskRequest('/api/web/profile');
        if (typeof profile.profile !== 'string') throw new Error('The archive Config Profile is unavailable');
        if (state.stopped) return;
        if (history.profile !== profile.profile) {
          history.generation++; history.profile = profile.profile;
          history.entries = null; history.details.clear(); history.expanded.clear(); renderHistory();
        }
        const payload = await diskRequest('/api/web/observations', history.profile);
        if (!Array.isArray(payload.observations)) throw new Error('Saved observation catalogue is invalid');
        if (state.stopped) return;
        history.entries = payload.observations;
        const revisions = new Map(history.entries.map(entry => [(entry.folder || '.') + '/' + entry.filename, entry.revision]));
        for (const [key, detail] of history.details) if (!revisions.has(key) || revisions.get(key) !== detail.revision) history.details.delete(key);
        $('history-freshness').textContent = 'Archive Config Profile: ' + history.profile + ' · ' + show(profile.scan_store_dir)
          + ' · Read from disk: ' + timestamp(new Date().toISOString()) + ' UTC.';
        renderHistory();
        for (const key of history.expanded) if (!history.details.has(key)) loadScans(key);
      } catch (error) {
        if (!state.stopped) $('history-freshness').textContent = error.message
          + (history.entries ? ' Showing the last disk catalogue; it may be out of date.' : ' No disk catalogue loaded.');
      } finally {
        history.checkedAt = Date.now(); history.loading = false; $('history-refresh').disabled = false;
      }
    }
    async function refresh(fresh = false) {
      if (state.loading || state.stopped) return;
      state.loading = true; $('refresh').disabled = true;
      if (fresh) $('refresh').textContent = 'Requesting TM snapshot…';
      const controller = new AbortController(), timeout = win.setTimeout(() => controller.abort(), fresh ? 35000 : 5000);
      try {
        const response = await fetcher('/api/web/telescope/model' + (fresh ? '?refresh=1' : ''), { cache: 'no-store', credentials: 'same-origin', signal: controller.signal });
        const body = await response.json();
        if (!response.ok || body.status !== 'success' || !body.payload?.telescope_model) throw new Error(body.message || 'Telescope model unavailable');
        if (state.stopped) return;
        state.data = body.payload;
        for (const [id, pending] of state.pendingControls) {
          const item = state.data.telescope_model?.oda?.obs_store?.obs_list?.find(obs => obs.obs_id === id);
          const status = obsState(item?.obs_state);
          const applied = pending.operation === 'delete' ? !item : pending.operation === 'abort'
            ? status === 'ABORTED' : item && status !== 'ABORTED';
          if (pending.accepted && (applied || Date.now() > pending.deadline)) {
            state.pendingControls.delete(id);
            const result = $('action-result');
            result.className = 'schedule-download-result' + (applied ? '' : ' is-error');
            result.textContent = applied ? `${id}: ${pending.operation} confirmed by TM.`
              : `${id}: TM has not confirmed the change. Refresh the schedule before retrying.`;
          }
        }
        state.data.stale = state.data.stale || state.data.tm_connected === false;
        $('freshness').className = state.data.stale ? 'zk-chip is-warn snapshot-warning' : 'zk-muted';
        $('connection').hidden = !state.data.stale;
        $('connection').textContent = state.data.tm_connected === false ? 'TM disconnected' : state.data.stale ? 'TM snapshot stale' : 'TM snapshot available';
        $('connection').className = 'zk-chip ' + (state.data.stale ? 'is-warn' : 'is-live');
        $('freshness').textContent = (state.data.stale ? 'Stale snapshot. ' : '') + 'TM Config Profile: ' + show(state.data.profile)
          + ' · Snapshot: ' + timestamp(state.data.snapshot_at) + ' UTC · Received ' + Math.round(state.data.age_seconds || 0)
          + ' seconds ago. WEB polls TM every 30 seconds.' + (state.data.last_error ? ' ' + state.data.last_error : '');
        render();
      } catch (error) {
        if (state.stopped) return;
        if (state.data) state.data.stale = true;
        $('freshness').className = 'zk-chip is-warn snapshot-warning';
        $('connection').hidden = false;
        $('connection').textContent = 'Schedule unavailable'; $('connection').className = 'zk-chip is-bad';
        $('freshness').textContent = error.message + (state.data ? ' Showing the last snapshot as stale.' : ' Waiting for a TM snapshot.');
        render();
      } finally {
        win.clearTimeout(timeout); state.loading = false; $('refresh').disabled = false;
        $('refresh').innerHTML = refreshMarkup;
      }
    }
    let signalGeneration = 0, signalObjectUrl = null, signalController = null;
    async function controlObservation(id, operation) {
      if (state.pendingControls.has(id)) return;
      const result = $('action-result');
      const item = state.data?.telescope_model?.oda?.obs_store?.obs_list?.find(obs => obs.obs_id === id);
      if (state.data?.stale || state.data?.tm_connected === false || win.DmdSession?.can_edit === false || !item || controlOperation(item) !== operation) return;
      const pending = {operation,accepted:false,deadline:Date.now()+60000};
      state.pendingControls.set(id,pending); render();
      result.hidden = false; result.className='schedule-download-result'; result.textContent='Sending '+operation+' request…';
      const controller=new AbortController(), timeout=win.setTimeout(()=>controller.abort(),35000);
      try {
        const response=await fetcher('/api/observation-control/'+encodeURIComponent(id),{
          method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({operation}),signal:controller.signal});
        const body=await response.json();
        if (!response.ok || body.status !== 'success') throw new Error(body.message || 'Observation control request failed');
        pending.accepted=true;
        result.textContent=body.message+' Requesting a fresh TM snapshot…';
        await refresh();
      } catch (error) {
        state.pendingControls.delete(id);
        result.className='schedule-download-result is-error';
        result.textContent=error.name==='AbortError' || error instanceof TypeError || error instanceof SyntaxError
          ? 'No valid response received. The outcome is unknown; refresh the schedule before retrying.' : error.message;
      } finally {win.clearTimeout(timeout);render();}
    }
    function clearSignal() {
      signalGeneration++;
      signalController?.abort(); signalController = null;
      $('signal-image').hidden = true; $('signal-image').removeAttribute('src');
      $('signal-download').hidden = true; $('signal-download').removeAttribute('href');
      if (signalObjectUrl) win.URL.revokeObjectURL(signalObjectUrl);
      signalObjectUrl = null;
    }
    async function previewSignal(button) {
      clearSignal();
      const generation = signalGeneration, dialog = $('signal-dialog');
      $('signal-title').textContent = 'Signal: ' + (button.dataset.scanLabel || 'Completed scan');
      $('signal-message').textContent = 'Loading final-sample signal display…';
      if (!dialog.open) dialog.showModal();
      signalController = new AbortController();
      const controller = signalController;
      const timeout = win.setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetcher(button.dataset.scanSignal, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (!response.ok || response.headers.get('Content-Type')?.split(';')[0] !== 'image/png') {
          let message = 'The server did not return a PNG image.';
          try { message = (await response.json()).message || message; } catch (_) { /* Non-JSON failure. */ }
          throw new Error(message);
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
        const filename = (encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || plain?.[2] || 'scan-sigfig.png').split(/[\\/]/).pop();
        if (!filename.toLowerCase().endsWith('.png')) throw new Error('The server returned an invalid PNG filename.');
        const blob = await response.blob();
        if (generation !== signalGeneration) return;
        signalObjectUrl = win.URL.createObjectURL(blob);
        const image = $('signal-image');
        image.onload = () => { if (generation === signalGeneration) $('signal-message').textContent = ''; };
        image.onerror = () => {
          if (generation !== signalGeneration) return;
          clearSignal(); $('signal-message').textContent = 'The saved signal image could not be displayed.';
        };
        image.src = signalObjectUrl; image.hidden = false;
        image.alt = 'Final-sample signal display for ' + (button.dataset.scanLabel || 'completed scan');
        const download = $('signal-download');
        download.href = signalObjectUrl; download.download = filename; download.hidden = false;
      } catch (error) {
        if (generation === signalGeneration) $('signal-message').textContent = 'Signal preview failed: ' + error.message;
      } finally { win.clearTimeout(timeout); }
    }
    $('signal-close').addEventListener('click', () => $('signal-dialog').close());
    $('signal-dialog').addEventListener('close', clearSignal);
    async function downloadScan(button, historic, resultElement = null) {
      const url = button.dataset.scanDownload;
      if (!url || state.downloads.has(url)) return;
      const result = resultElement || $(historic ? 'history-download-result' : 'download-result');
      state.downloads.add(url); button.disabled = true;
      result.hidden = false; result.className = 'zk-muted schedule-download-result'; result.textContent = 'Downloading SPR CSV…';
      try {
        const response = await fetcher(url, { credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok || response.headers.get('Content-Type')?.split(';')[0] !== 'text/csv') {
          let message = 'The server did not return a CSV file.';
          try { message = (await response.json()).message || message; } catch (_) { /* Keep a clear error for non-JSON replies. */ }
          throw new Error(message);
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
        const filename = (encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || plain?.[2] || 'scan-spr.csv').split(/[\\/]/).pop();
        if (!filename.toLowerCase().endsWith('.csv')) throw new Error('The server returned an invalid CSV filename.');
        const blob = await response.blob();
        const objectUrl = win.URL.createObjectURL(blob), link = doc.createElement('a');
        link.href = objectUrl; link.download = filename;
        doc.body.appendChild(link); link.click(); link.remove();
        win.setTimeout(() => win.URL.revokeObjectURL(objectUrl), 1000);
        result.textContent = 'Downloaded ' + filename;
      } catch (error) {
        result.className = 'schedule-download-result is-error';
        result.textContent = 'SPR download failed: ' + error.message;
      } finally { state.downloads.delete(url); button.disabled = false; }
    }
    $('observations').addEventListener('click', event => {
      const control=event.target.closest('[data-schedule-control]');
      if (control) { controlObservation(control.dataset.observationId,control.dataset.scheduleControl); return; }
      const signal = event.target.closest('[data-scan-signal]');
      if (signal) { previewSignal(signal); return; }
      const download = event.target.closest('[data-scan-download]');
      if (download) { downloadScan(download, false); return; }
      const row = event.target.closest('[data-schedule-observation]');
      if (!row) return;
      const id = row.dataset.scheduleObservation;
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      render();
    });
    $('history-observations')?.addEventListener('click', event => {
      const signal = event.target.closest('[data-scan-signal]');
      if (signal) { previewSignal(signal); return; }
      const download = event.target.closest('[data-scan-download]');
      if (download) { downloadScan(download, true); return; }
      const retry = event.target.closest('[data-history-retry]');
      if (retry) { loadScans(retry.dataset.historyRetry); return; }
      const row = event.target.closest('[data-schedule-observation]');
      if (!row) return;
      const key = row.dataset.scheduleObservation, history = state.history;
      if (history.expanded.has(key)) history.expanded.delete(key);
      else { history.expanded.add(key); loadScans(key); }
      renderHistory();
    });
    $('refresh').addEventListener('click', () => { refresh(true); refreshHistory(true); });
    $('history-refresh')?.addEventListener('click', () => refreshHistory(true));
    win.addEventListener('dmd:view', event => { if (event.detail === 'schedule') { refresh(); refreshHistory(true); } });
    refresh(); refreshHistory(true);
    let pollTick = 0;
    const timer = win.setInterval(() => {
      pollTick++;
      if (state.pendingControls.size || pollTick % 5 === 0) refresh();
      if (pollTick % 5 === 0) refreshHistory();
    }, 1000);
    win.addEventListener('pagehide', () => { state.stopped = true; clearSignal(); win.clearInterval(timer); });
    win.addEventListener('dmd:session',render);
    return { state, refresh, refreshHistory, loadScans, downloadScan, previewSignal, controlObservation };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, observations, scansFor, completed, historyObservations, historyStatus, rows, scanDetails, timestamp, duration, controlOperation };
  if (typeof document !== 'undefined') {
    window.DmdSchedule = { rows, historyObservations, historyStatus, scanDetails };
    window.DmdScheduleController = init(document, window);
  }
})();
