/* Live dish inspection. Polls only update reported values, never desired inputs. */
(function () {
  const MODES = ['STARTUP', 'SHUTDOWN', 'STANDBY_LP', 'STANDBY_FP', 'MAINTENANCE', 'STOW', 'CONFIG', 'OPERATE', 'UNKNOWN'];
  const CAPABILITIES = ['UNAVAILABLE', 'STANDBY', 'CONFIGURING', 'OPERATE_DEGRADED', 'OPERATE_FULL', 'UNKNOWN'];
  const unwrap = value => value && typeof value === 'object' && 'value' in value ? value.value : value;
  const escape = value => String(value ?? '—').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const show = value => { value = unwrap(value); return value == null ? '—' : String(value); };
  const time = value => { const date = new Date(unwrap(value)); return value && !Number.isNaN(+date) ? date.toISOString().slice(0, 19).replace('T', ' ') + ' UTC' : '—'; };
  const degrees = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '—';
  // Zenith-distance projection: north up, east right, horizon at radius 104.
  function polarPoint(position) {
    const alt = position?.alt, az = position?.az;
    if (typeof alt !== 'number' || typeof az !== 'number' || !Number.isFinite(alt) || !Number.isFinite(az)
        || alt < 0 || alt > 90) return null;
    const angle = (((az % 360) + 360) % 360) * Math.PI / 180;
    const radius = 104 * (90 - alt) / 90;
    return { x: 140 + radius * Math.sin(angle), y: 140 - radius * Math.cos(angle) };
  }
  function polarPlot(dish, stale = false, history = []) {
    const actual = polarPoint(dish.pointing_altaz), desired = polarPoint(dish.desired_altaz);
    const trail = unwrap(dish.pointing_state) === 'SLEW' && actual ? history.slice(-6).map(polarPoint).filter(Boolean) : [];
    const trailMarkup = trail.length > 1
      ? `<polyline class="dish-polar-trail" points="${trail.map(point => point.x.toFixed(3) + ',' + point.y.toFixed(3)).join(' ')}"><title>Recent slew path, oldest to newest</title></polyline>`
        + trail.slice(0, -1).map((point, index) => `<circle class="dish-polar-history-point" cx="${point.x.toFixed(3)}" cy="${point.y.toFixed(3)}" r="2.5" opacity="${((index + 1) / trail.length).toFixed(2)}" />`).join('')
      : '';
    const coordinate = position => 'Alt ' + degrees(position?.alt) + '°, Az ' + degrees(position?.az) + '°';
    const missing = (label, position) => label + (typeof position?.alt === 'number' && position.alt < 0 && position.alt >= -90
      && typeof position?.az === 'number' && Number.isFinite(position.az) ? ': below horizon' : ': unavailable');
    const marker = (point, position, label, type) => {
      if (!point) return '';
      const x = point.x.toFixed(3), y = point.y.toFixed(3), description = escape(label + ': ' + coordinate(position));
      const shape = type === 'actual' ? `<circle cx="${x}" cy="${y}" r="4.5" />`
        : `<path d="M ${x} ${(point.y - 9).toFixed(3)} l 9 9 l -9 9 l -9 -9 Z" />`;
      return `<g class="dish-polar-${type}" tabindex="0" role="img" aria-label="${description}"><title>${description}</title>${shape}</g>`;
    };
    let grid = '';
    for (let az = 0; az < 360; az += 45) {
      const point = polarPoint({ alt: 0, az });
      grid += `<line x1="140" y1="140" x2="${point.x.toFixed(3)}" y2="${point.y.toFixed(3)}" />`;
    }
    for (const alt of [0, 30, 60]) grid += `<circle cx="140" cy="140" r="${104 * (90 - alt) / 90}" />`;
    return '<figure class="dish-polar' + (stale ? ' is-stale' : '') + '">'
      + '<svg viewBox="0 0 280 280" role="img" aria-label="Dish pointing: north at top, azimuth clockwise, altitude 90 degrees at centre and 0 degrees at horizon">'
      + '<g class="dish-polar-grid">' + grid + '</g>'
      + '<g class="dish-polar-labels"><text x="140" y="18">N · 0°</text><text x="263" y="144">E</text><text x="140" y="270">S · 180°</text><text x="17" y="144">W</text>'
      + '<text x="162" y="51">0°</text><text x="158" y="82">30°</text><text x="154" y="116">60°</text><text x="140" y="160">90°</text></g>'
      + trailMarkup + marker(desired, dish.desired_altaz, 'Desired', 'desired') + marker(actual, dish.pointing_altaz, 'Actual', 'actual')
      + '</svg><figcaption><span class="dish-polar-key actual">● Actual</span><span class="dish-polar-key desired">◇ Desired</span>'
      + '<span class="dish-polar-note">Altitude rings · horizon to zenith</span>'
      + (trail.length > 1 ? `<span class="dish-polar-note">Slew trail · previous ${trail.length - 1} position${trail.length === 2 ? '' : 's'}</span>` : '')
      + (!actual ? '<span class="dish-polar-note">' + missing('Actual', dish.pointing_altaz) + '</span>' : '')
      + (!desired ? '<span class="dish-polar-note">' + missing('Desired', dish.desired_altaz) + '</span>' : '')
      + (stale ? '<span class="dish-polar-note">Stale snapshot</span>' : '') + '</figcaption></figure>';
  }
  function info(dish) {
    const pec = dish.tgt_id ? (dish.tgt_pec || []).find(item => item.tgt_id === dish.tgt_id) : null;
    const pairs = [['Target ID', dish.tgt_id],
      ['Pointing measured UTC', time(dish.pointing_altaz_dt)], ['Target acquired UTC', time(dish.tgt_acq_dt)],
      ['PEC altitude RMS [arcsec]', pec?.alt_rms], ['PEC azimuth RMS [arcsec]', pec?.az_rms],
      ['Altitude velocity [°/s]', dish.velocity_altaz?.alt], ['Azimuth velocity [°/s]', dish.velocity_altaz?.az],
      ['Weather station', dish.ws_id], ['Polling period [ms]', dish.driver_poll_period]];
    pairs.push(['Diameter [m]', dish.diameter], ['F/D', dish.fd_ratio], ['Digitiser', dish.dig_id],
      ['Latitude [°]', dish.latitude], ['Longitude [°]', dish.longitude], ['Height [m]', dish.height]);
    return '<dl class="status-kv">' + pairs.map(([label, value]) => '<div><dt>' + escape(label) + '</dt><dd>' + escape(show(value)) + '</dd></div>').join('') + '</dl>';
  }
  function init(doc, win, fetcher = fetch) {
    const $ = id => doc.getElementById('dish-status-' + id);
    if (!$('pane')) return;
    const state = { id: null, data: null, settingBusy: false, stopBusy: false, generation: 0, responseSequence: 0 };
    let trailDish = null, trailTarget = null, slewHistory = [], lastMeasurement = null;
    const dish = () => state.data?.telescope_model?.dsh_mgr?.dish_store?.dish_list?.find(item => item.dsh_id === state.id);
    function slewTrail(current, stale) {
      const target = unwrap(current?.tgt_id);
      if (!current || current.dsh_id !== trailDish || target !== trailTarget || unwrap(current.pointing_state) !== 'SLEW') {
        slewHistory = []; lastMeasurement = null;
        trailDish = current?.dsh_id; trailTarget = target;
      }
      if (!current || unwrap(current.pointing_state) !== 'SLEW' || stale) return slewHistory;
      if (!polarPoint(current.pointing_altaz)) { slewHistory = []; lastMeasurement = null; return slewHistory; }
      const measured = Date.parse(unwrap(current.pointing_altaz_dt));
      if (Number.isFinite(measured) && lastMeasurement !== null && measured <= lastMeasurement) return slewHistory;
      if (Number.isFinite(measured)) lastMeasurement = measured;
      const { alt, az } = current.pointing_altaz, previous = slewHistory[slewHistory.length - 1];
      if (!previous || previous.alt !== alt || ((previous.az - az) % 360) !== 0) {
        slewHistory.push({ alt, az });
        slewHistory = slewHistory.slice(-6); // Current position plus five earlier positions.
      }
      return slewHistory;
    }
    function controls() {
      const readOnly = win.DmdSession?.can_edit === false;
      for (const field of ['mode', 'capability']) {
        $(field).disabled = readOnly || state.settingBusy || !dish() || state.data?.stale || !state.data?.tm_connected;
        $(field + '-send').disabled = $(field).disabled || !$(field).value;
      }
      // A stale TM snapshot does not prevent the independent DM STOP path.
      $('stop').disabled = readOnly || state.stopBusy || !dish();
      $('stop').textContent = state.stopBusy ? 'Sending STOP…' : 'STOP ' + (state.id || 'dish') + ' movement';
    }
    function update(data) {
      state.data = data;
      const current = dish();
      const trail = slewTrail(current, data?.stale);
      $('pane').hidden = !current;
      for (const row of doc.querySelectorAll?.('[data-status-dish]') || []) {
        const selected = !!current && row.dataset.statusDish === state.id;
        row.classList.toggle('is-selected', selected);
        row.setAttribute('aria-selected', String(selected));
      }
      if (!current) { controls(); return; }
      $('title').textContent = 'Dish — ' + current.dsh_id + (current.short_desc ? ' · ' + current.short_desc : '');
      $('freshness').textContent = (data.stale ? 'Stale snapshot · ' : '') + 'TM profile: ' + show(data.profile) + ' · Dish updated ' + time(current.last_update);
      $('error').textContent = current.last_err_msg || 'No error reported';
      $('error-time').textContent = current.last_err_msg ? time(current.last_err_dt) : '';
      for (const axis of ['alt', 'az']) {
        $(axis + '-desired').textContent = degrees(current.desired_altaz?.[axis]);
      }
      $('info').innerHTML = info(current);
      const plot = polarPlot(current, data.stale, trail);
      if ($('polar').innerHTML !== plot) $('polar').innerHTML = plot;
      controls();
    }
    function open(id, data) {
      if (state.id !== id) {
        state.id = id; state.data = data; state.generation++;
        $('result').textContent = '';
        const current = dish();
        for (const [field, values] of [['mode', MODES], ['capability', CAPABILITIES]]) {
          $(field).innerHTML = '<option value="">Choose…</option>' + values.map(value => `<option value="${value}">${value}</option>`).join('');
          $(field).value = values.includes(unwrap(current?.[field])) ? unwrap(current[field]) : '';
        }
      }
      update(data); $('pane').scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }
    function result(status, message, id) {
      $('result').textContent = id + ' · ' + time(new Date().toISOString()) + ' · ' + status + ': ' + message;
      $('result').classList.toggle('is-error', status === 'error');
    }
    async function send(field) {
      const stopping = field === 'stop';
      if (!dish() || win.DmdSession?.can_edit === false || (stopping ? state.stopBusy : state.settingBusy || state.data?.stale || !state.data?.tm_connected)) return;
      const id = state.id, generation = state.generation, sequence = ++state.responseSequence;
      const value = stopping ? id : $(field).value;
      if (!value) return;
      const busy = stopping ? 'stopBusy' : 'settingBusy';
      state[busy] = true; controls();
      result('pending', stopping ? 'Sending STOP to Dish Manager…' : 'Requesting ' + field + ' ' + value + '…', id);
      const controller = new AbortController(), timer = win.setTimeout(() => controller.abort(), 35000);
      try {
        const response = await fetcher(stopping ? '/api/commands' : '/api/dish-control/' + encodeURIComponent(id), {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify(stopping ? { app: 'dm', cmd: 'stop', value: id } : { [field]: value }) });
        const body = await response.json();
        if (!['success', 'error'].includes(body.status) || typeof body.message !== 'string') throw new Error('Invalid response');
        if (state.generation === generation && state.responseSequence === sequence) result(response.ok ? body.status : 'error',
          (body.code ? '(' + body.code + ') ' : '') + body.message + (response.ok && body.status === 'success' && !stopping ? ' Awaiting a fresh dish report.' : ''), id);
      } catch (_) {
        if (state.generation === generation && state.responseSequence === sequence) result('error', 'No valid response received. Outcome unknown; check the dish before retrying.', id);
      } finally {
        win.clearTimeout(timer); state[busy] = false; controls();
      }
    }
    for (const field of ['mode', 'capability']) {
      $(field).addEventListener('change', controls);
      $(field + '-send').addEventListener('click', () => send(field));
    }
    $('stop').addEventListener('click', () => send('stop'));
    $('close').addEventListener('click', () => { state.id = null; state.generation++; update(state.data); });
    win.addEventListener('dmd:session', controls);
    return { state, open, update, send };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, info, degrees, polarPoint, polarPlot };
  if (typeof window !== 'undefined') window.DmdDishStatus = { init };
})();
