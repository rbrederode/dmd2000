/* Engineering console, backed by WEB's shared TM snapshot cache. */
(function () {
  const weatherView = typeof module !== 'undefined' && module.exports ? require('./weather_status.js') : window.DmdWeatherStatus;
  const digitiserView = typeof module !== 'undefined' && module.exports ? require('./digitiser_status.js') : window.DmdDigitiserStatus;
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const unwrap = (value) => value && typeof value === 'object' && 'value' in value ? value.value : value;
  function display(value) {
    value = unwrap(value);
    if (value == null || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'On' : 'Off';
    if (Array.isArray(value)) return value.length ? value.map(display).join(', ') : '—';
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  function timestamp(value) {
    value = unwrap(value);
    if (!value) return '—';
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? display(value) : dt.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
  }
  function oldUpdate(value, now = Date.now()) {
    const raw = unwrap(value);
    return !!raw && now - new Date(raw).getTime() > 120000;
  }
  function lastUpdate(value, now = Date.now()) {
    const old = oldUpdate(value, now);
    return `<span class="status-update${old ? ' is-old' : ''}"${old ? ' title="Last update is more than 2 minutes old"' : ''}>${escape(timestamp(value))}</span>`;
  }
  const percent = (value) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) + '%' : '—';
  const healthClass = (value) => ({ OK: 'is-live', DEGRADED: 'is-warn', FAILED: 'is-bad' }[unwrap(value)] || 'is-empty');
  const chip = (value) => `<span class="zk-chip ${healthClass(value)}">${escape(display(value))}</span>`;
  const endpoint = (host, port) => host && port ? host + ':' + port : '—';
  function tmDisconnected(telescope, type, model) {
    if (type === 'tm') return false;
    const connection = type === 'dig' ? model?.tm_connected
      : telescope.tel_mgr?.[type + '_connected'] ?? model?.tm_connected;
    return unwrap(connection) === 'NOT_ESTABLISHED';
  }
  function activeExecutions(model) {
    const observations = model.oda?.obs_store?.obs_list;
    if (!Array.isArray(observations)) return null;
    // Match models.obs.ACTIVE_OBSERVATION_STATES; IDLE may be queued for resources.
    return observations.filter((obs) => obs && ['CONFIGURING', 'READY', 'SCANNING'].includes(unwrap(obs.obs_state)))
      .sort((a, b) => String(a.obs_id).localeCompare(String(b.obs_id)));
  }
  function applications(model) {
    const definitions = [
      ['tm', 'Telescope Manager', model.tel_mgr], ['dm', 'Dish Manager', model.dsh_mgr],
      ['sdp', 'Science Data Processor', model.sdp], ['ws', 'Weather Station', model.wtr_stn],
      ['web', 'Web Server', model.web_svr],
      ...[...(model.dig_store?.dig_list || [])].sort((a, b) => String(a.dig_id).localeCompare(String(b.dig_id)))
        .map((item) => ['dig', 'Digitiser', item]),
    ];
    return definitions.filter(([, , item]) => item && typeof item === 'object').map(([type, label, item]) => {
      const app = item.app || {};
      const id = item.dig_id || item.sdp_id || item.id || type.toUpperCase();
      return { key: type + ':' + id, id, type, label, model: item, app,
        scanModel: type === 'dig' ? model.sdp?.dig_store?.dig_list?.find(dig => dig.dig_id === id) : null,
        weatherStore: type === 'ws' ? model.dsh_mgr?.weather_store : null, entityLinks: entityConnections(type, item, model),
        profile: app.arguments?.profile ?? null,
        health: unwrap(app.health) || 'UNKNOWN',
        state: app.app_running === false ? 'STOPPED' : type === 'dig' ? (item.scanning ? 'SCANNING' : 'IDLE')
          : app.app_running === true ? 'RUNNING' : 'UNKNOWN',
        updated: Object.hasOwn(app, 'last_update') ? app.last_update : item.last_update };
    });
  }
  function kv(pairs) {
    return '<dl class="status-kv">' + pairs.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(display(value))}</dd></div>`).join('') + '</dl>';
  }
  function entityConnections(type, item, telescope) {
    if (type === 'dm') {
      return [...(item.dish_store?.dish_list || [])].sort((a, b) => String(a.dsh_id).localeCompare(String(b.dsh_id)))
        .map(dish => {
          const config = dish.driver_config || {}, driver = unwrap(dish.driver_type), motion = driver === 'MOTION';
          const network = ['MD01', 'MOTION'].includes(driver);
          const lastSuccess = unwrap(dish.last_comms_success_dt);
          const hasSuccess = typeof lastSuccess === 'string' && Number.isFinite(Date.parse(lastSuccess));
          // These drivers open sockets on demand. Report whether controller
          // communication succeeds, rather than whether a socket stays open.
          const state = !network || oldUpdate(dish.last_update) ? 'UNKNOWN'
            : dish.driver_failures > 0 ? 'NOT_ESTABLISHED'
              : hasSuccess && dish.driver_failures === 0 ? 'ESTABLISHED'
                : Object.hasOwn(dish, 'last_comms_success_dt') && !lastSuccess ? 'NOT_ESTABLISHED' : 'UNKNOWN';
          return { peer: dish.dsh_id, host: (motion ? config.imu_host : config.host) || null,
            port: (motion ? config.imu_port : config.port) ?? null, lastSuccess: dish.last_comms_success_dt ?? null,
            state, notApplicable: driver === 'DRIFT', role: network ? 'Client - On demand' : 'Unknown' };
        });
    }
    if (type === 'tm' || type === 'sdp') {
      const digitiserStore = type === 'sdp' ? item.dig_store : telescope.dig_store;
      return [...(digitiserStore?.dig_list || [])].sort((a, b) => String(a.dig_id).localeCompare(String(b.dig_id)))
        .map(dig => ({ peer: dig.dig_id, state: unwrap(dig[type + '_connected']) || 'UNKNOWN', host: dig.app?.arguments?.local_host || null, port: dig.app?.arguments?.[type + '_port'] ?? item.app?.arguments?.dig_port ?? null }));
    }
    if (type === 'dig' && 'sdr_connected' in item) return [{ peer: 'SDR', state: unwrap(item.sdr_connected) || 'UNKNOWN', host: item.sdr_config?.host || null, port: item.sdr_config?.port ?? null }];
    return [];
  }
  function connections(item) {
    const args = item.app.arguments || {};
    return Object.entries(item.model).filter(([key]) => key.endsWith('_connected')
      && !(['tm', 'sdp'].includes(item.type) && key === 'dig_connected')
      && !(item.type === 'dig' && key === 'sdr_connected')).map(([key, value]) => {
      const peer = key.slice(0, -10);
      const options = peer === 'sdr' ? item.model.sdr_config || {} : {};
      return { peer: peer.toUpperCase(), state: unwrap(value), endpoint: endpoint(args[peer + '_host'] || options.host, args[peer + '_port'] || options.port) };
    });
  }
  function localRole(item, peer) {
    const role = item.app.interface_roles?.[peer.toLowerCase()];
    if (role === 'CLIENT') return 'Client - Persistent';
    if (role === 'SERVER') return 'Server';
    // SDR drivers are hardware/file interfaces rather than registered TCP endpoints.
    return peer.toLowerCase() === 'sdr' && !role ? '—' : 'Unknown';
  }
  function consolidatedConnections(model, stale = false) {
    const apps = applications(model);
    const byId = new Map(apps.map(item => [item.id.toLowerCase(), item]));
    const byType = new Map(apps.filter(item => item.type !== 'dig').map(item => [item.type, item]));
    const pairs = new Map();
    for (const item of apps) {
      const args = item.app.arguments || {};
      const unavailable = stale || oldUpdate(item.updated) || tmDisconnected(model, item.type, item.model);
      const links = connections(item).map(link => ({ ...link, role: localRole(item, link.peer) }));
      for (const link of item.entityLinks) {
        const peerType = ['tm', 'sdp'].includes(item.type) ? 'dig' : link.peer;
        const role = link.role || localRole(item, peerType);
        links.push({ ...link, role, endpoint: link.notApplicable ? 'Not Applicable'
          : role === 'Server' ? endpoint(args[peerType.toLowerCase() + '_host'], args[peerType.toLowerCase() + '_port'])
            : endpoint(link.host, link.port) });
      }
      for (const link of links) {
        const peer = byId.get(link.peer.toLowerCase()) || byType.get(link.peer.toLowerCase());
        // Unresolved hardware peers belong to their reporting app, so two SDRs
        // or controllers cannot collapse merely because their labels match.
        const key = peer ? JSON.stringify([item.key, peer.key].sort())
          : JSON.stringify([item.key, 'entity:' + link.peer]);
        const row = { application: item.id, ...link, peer: peer?.id || link.peer,
          state: link.notApplicable ? 'Not Applicable' : unavailable ? 'UNKNOWN' : link.state || 'UNKNOWN' };
        const previous = pairs.get(key);
        if (!previous) { pairs.set(key, row); continue; }
        // Prefer the client's view for a stable direction and destination address.
        const quality = value => (value.role.startsWith('Client') ? 4 : value.role === 'Server' ? 2 : 0)
          + (value.endpoint !== '—' ? 1 : 0);
        const preferred = quality(row) > quality(previous) ? row : previous;
        const states = [previous.state, row.state];
        const state = states.includes('NOT_ESTABLISHED') ? 'NOT_ESTABLISHED'
          : states.includes('ESTABLISHED') ? 'ESTABLISHED' : 'UNKNOWN';
        pairs.set(key, { ...preferred, state,
          endpoint: preferred.endpoint === '—' ? (preferred === row ? previous.endpoint : row.endpoint) : preferred.endpoint });
      }
    }
    return [...pairs.values()];
  }
  function persistentAppConnections(model, stale = false) {
    const appIds = new Set(applications(model).map(item => item.id.toLowerCase()));
    const appTypes = new Set(['tm', 'dm', 'sdp', 'ws', 'web']);
    return consolidatedConnections(model, stale).filter(link =>
      link.role !== 'Client - On demand' && !link.notApplicable
      && (appIds.has(link.peer.toLowerCase()) || appTypes.has(link.peer.toLowerCase())));
  }
  function consolidatedConnectionTable(model, stale) {
    const rows = persistentAppConnections(model, stale);
    return rows.map(link => `<tr><td>${escape(link.application)}</td><td>${escape(link.peer)}</td><td>${escape(link.role)}</td>`
      + `<td><span class="zk-chip ${link.state === 'ESTABLISHED' ? 'is-live' : link.state === 'NOT_ESTABLISHED' ? 'is-warn' : 'is-empty'}">${escape(link.state)}</span></td>`
      + `<td>${escape(link.endpoint)}</td></tr>`).join('') || '<tr><td colspan="5">No persistent application connections reported</td></tr>';
  }
  function entityTable(item, entities) {
    if (item.type === 'dm') {
      return `<div class="status-table-scroll status-dish-connections-scroll" data-status-scroll-app="${escape(item.key)}" tabindex="0" role="region" aria-label="Dish entity connections"><table class="zk-tbl"><thead><tr><th>Entity</th><th>Local role</th><th>Configured host</th><th>Server port</th><th title="Time of the last valid response from the controller; retained after later failures">Last successful communication UTC</th></tr></thead><tbody>`
        + entities.map(link => `<tr><td>${escape(link.peer)}</td>`
          + (link.notApplicable ? '<td>—</td><td colspan="3">Not Applicable</td>'
            : `<td>${escape(link.role)}</td><td>${escape(display(link.host))}</td><td>${escape(display(link.port))}</td><td>${escape(timestamp(link.lastSuccess))}</td>`)
          + '</tr>').join('')
        + (entities.length ? '' : '<tr><td colspan="5">No dishes reported</td></tr>') + '</tbody></table></div>';
    }
    return '<div class="status-table-scroll"><table class="zk-tbl"><thead><tr><th>Entity</th><th title="TCP role of the selected app, relative to this entity. A dash means the driver has no declared TCP role.">Local role</th><th>Connection</th><th title="Configured address from the entity model; not a separately recorded socket peer address">Configured host</th><th title="Configured listening port of the server this entity connects to; not its dynamic source port">Server port</th></tr></thead><tbody>'
      + entities.map(link => `<tr><td>${escape(link.peer)}</td><td>${localRole(item, ['tm', 'sdp'].includes(item.type) ? 'dig' : link.peer)}</td><td><span class="zk-chip ${link.state === 'ESTABLISHED' ? 'is-live' : link.state === 'UNKNOWN' ? 'is-empty' : 'is-warn'}">${escape(display(link.state))}</span></td><td>${escape(display(link.host))}</td><td>${escape(display(link.port))}</td></tr>`).join('')
      + (entities.length ? '' : '<tr><td colspan="5">No entity connections reported</td></tr>') + '</tbody></table></div>';
  }
  function specific(item) {
    const model = item.model, args = item.app.arguments || {};
    if (item.type === 'tm') {
      const allocations = model.allocations?.alloc_list;
      return '<div class="status-table-scroll"><table class="zk-tbl status-allocations"><thead><tr><th>Index</th><th>Resource Type</th><th>Resource ID</th><th>Allocated Type</th><th>Allocated ID</th><th>State</th><th>Expiry UTC</th></tr></thead><tbody>'
        + (Array.isArray(allocations) && allocations.length ? allocations.map((allocation, index) => {
          const state = unwrap(allocation.state);
          const style = { ACTIVE: 'is-live', REQUESTED: 'is-warn', RELEASED: 'is-empty' }[state] || 'is-empty';
          const expiry = unwrap(allocation.expires) === null ? 'No expiry' : timestamp(allocation.expires);
          return `<tr><td>[${index}]</td><td>${escape(display(allocation.resource_type))}</td><td>${escape(display(allocation.resource_id))}</td><td>${escape(display(allocation.allocated_type))}</td><td>${escape(display(allocation.allocated_id))}</td><td><span class="zk-chip ${style}">${escape(display(state))}</span></td><td>${escape(expiry)}</td></tr>`;
        }).join('') : `<tr><td colspan="7">${Array.isArray(allocations) ? 'No resource allocations' : 'Resource allocations unavailable'}</td></tr>`)
        + '</tbody></table></div>';
    }
    if (item.type === 'dig') return digitiserView.render(item);
    if (item.type === 'sdp') return kv([
      ['Scans created', model.scans_created], ['Scans in progress', model.scans_wip],
      ['Scans completed', model.scans_completed], ['Scans aborted', model.scans_aborted],
      ['Processing scans', model.processing_scans?.length], ['Pipeline maps', Object.keys(model.pipeline_config?.steps_map || {})],
      ['Scan store', args.scan_store_dir],
    ]);
    if (item.type === 'ws') return kv([
      ['Driver', model.driver_type], ['Polling period [ms]', model.driver_config?.driver_poll_period],
      ['Driver failures', model.driver_failures], ['Simulation mode', model.sim_mode],
    ]);
    if (item.type === 'web') return kv([
      ['HTTP endpoint', endpoint(args.http_host, args.http_port)], ['HTTP workers', args.http_threads],
      ['TM endpoint', endpoint(args.tm_host, args.tm_port)], ['Config Profile', args.profile],
    ]);
    const dishes = [...(model.dish_store?.dish_list || [])].sort((a, b) => String(a.dsh_id).localeCompare(String(b.dsh_id)));
    const monitoring = unwrap(model.weather_store?.weather_enabled);
    const alarms = dishes.map(dish => unwrap(dish.weather_alarm));
    const weather = alarms.some(alarm => alarm === true) ? ['ALARM', 'is-bad']
      : monitoring === false ? ['Not monitored', 'is-empty']
      : alarms.length && alarms.every(alarm => alarm === false) ? ['Clear', 'is-live'] : ['—', 'is-empty'];
    return `<dl class="status-kv status-weather-summary"><div><dt>Weather monitoring</dt><dd>${escape(display(monitoring))}</dd></div><div><dt>Weather</dt><dd><span class="zk-chip ${weather[1]}">${weather[0]}</span></dd></div></dl>`;
  }
  function dishesTable(model) {
    const dishes = [...(model?.dish_store?.dish_list || [])].sort((a, b) => String(a.dsh_id).localeCompare(String(b.dsh_id)));
    const degrees = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) + '°' : '—';
    const columns = [['Feed', 'feed_type'], ['Driver type', 'driver_type'], ['Mode', 'mode'], ['Pointing', 'pointing_state'],
      ['Actual AltAz', dish => [degrees(dish.pointing_altaz?.alt), degrees(dish.pointing_altaz?.az)].join(', ')],
      ['Capability', 'capability'], ['Driver Failures', 'driver_failures']];
    return '<p class="zk-muted status-dishes-help">Select a dish to inspect its pointing and control Mode, Capability or STOP.</p>'
      + '<div class="status-table-scroll"><table class="zk-tbl status-dish-table"><thead><tr><th>Dish / Description</th>'
      + columns.map(([title]) => `<th>${title}</th>`).join('') + '<th>Health</th><th>Last error UTC / Message</th></tr></thead><tbody>'
      + dishes.map(dish => `<tr data-status-dish="${escape(dish.dsh_id)}" tabindex="0" aria-label="Inspect dish ${escape(dish.dsh_id)}"><td><strong>${escape(dish.dsh_id)}</strong><br><span class="zk-muted">${escape(dish.short_desc || '')}</span></td>`
        + columns.map(([, field]) => `<td>${escape(typeof field === 'function' ? field(dish) : display(dish[field]))}</td>`).join('')
        + `<td>${chip(dish.health)}</td><td class="status-dish-error">${dish.last_err_msg ? escape(timestamp(dish.last_err_dt)) + '<br>' + escape(dish.last_err_msg) : '—'}</td></tr>`).join('')
      + (dishes.length ? '' : `<tr><td colspan="${columns.length + 3}">No dishes reported</td></tr>`) + '</tbody></table></div>';
  }

  function digitisersTable(model, stale = false) {
    const columns = ['ID', 'SDR Type', 'Gain', 'Centre frequency', 'Sample rate',
      'Bandwidth', 'Scanning', 'Temp Reading', 'Max Temp',
      'SDR EEPROM', 'Health', 'Last error UTC / Message'];
    const items = applications(model).filter(item => item.type === 'dig');
    const numeric = (value, unit) => typeof value === 'number' && Number.isFinite(value) ? String(value) + (unit ? ' ' + unit : '') : '—';
    const megahertz = value => typeof value === 'number' && Number.isFinite(value) ? numeric(value / 1000000, 'MHz') : '—';
    return '<p class="zk-muted status-dishes-help">Select a digitiser to inspect its settings and controls.</p>'
      + '<div class="status-table-scroll"><table class="zk-tbl status-digitiser-table"><thead><tr>'
      + columns.map(label => {
        const split = label.indexOf(' ');
        return `<th aria-label="${escape(label)}">${split < 0 ? escape(label)
          : escape(label.slice(0, split)) + '<br>' + escape(label.slice(split + 1))}</th>`;
      }).join('') + '</tr></thead><tbody>'
      + items.map(item => {
        const dig = item.model;
        const unavailable = stale || oldUpdate(item.updated) || tmDisconnected(model, 'dig', dig);
        const load = unwrap(dig.load_active);
        const scanning = dig.scanning == null ? '—' : !digitiserView.scanning(dig.scanning) ? 'OFF'
          : load === true ? 'LOAD' : load === false ? 'SKY' : '—';
        const values = [display(dig.sdr_type), numeric(dig.gain, 'dB'), megahertz(dig.center_freq), megahertz(dig.sample_rate),
          megahertz(dig.bandwidth), scanning,
          numeric(dig.temp_reading?.temperature, '°C')];
        const error = dig.app?.last_err_msg || dig.last_err_msg;
        const errorTime = dig.app?.last_err_msg ? dig.app.last_err_dt : dig.last_err_dt;
        return `<tr data-status-digitiser="${escape(item.key)}" tabindex="0" aria-label="Inspect ${escape(item.id)}"><td><strong>${escape(item.id)}</strong></td>`
          + values.map(value => `<td>${unavailable ? '<span class="zk-chip status-unknown">UNKNOWN</span>' : escape(value)}</td>`).join('')
          + `<td>${escape(numeric(dig.temp_max, '°C'))}</td><td class="status-digitiser-eeprom">${escape(display(dig.sdr_eeprom && Object.keys(dig.sdr_eeprom).length ? dig.sdr_eeprom : null))}</td>`
          + `<td>${unavailable || item.health === 'UNKNOWN' ? '<span class="zk-chip status-unknown">UNKNOWN</span>' : chip(item.health)}</td>`
          + `<td class="status-dish-error">${error ? escape(timestamp(errorTime)) + '<br>' + escape(error) : '—'}</td></tr>`;
      }).join('') + (items.length ? '' : `<tr><td colspan="${columns.length}">No digitisers reported</td></tr>`)
      + '</tbody></table></div>';
  }

  function detail(item, processorsOpen = false, commandPanel = '', digitiserPanel = null) {
    const app = item.app;
    const links = connections(item);
    const entities = item.entityLinks || [];
    const processors = Array.isArray(app.processors) ? app.processors : [];
    const processorCount = app.num_processors ?? (Array.isArray(app.processors) ? processors.length : null);
    const error = app.last_err_msg || item.model.last_err_msg;
    const errorTime = app.last_err_msg ? app.last_err_dt : item.model.last_err_dt;
    return '<h3>Last error' + (error ? '<span class="status-last-error-time">' + escape(timestamp(errorTime)) + '</span>' : '')
      + '</h3><div class="status-last-error' + (error ? '' : ' is-clear') + '">' + escape(error || 'No error reported') + '</div>'
      + '<div class="status-connection-summary">' + kv([
        ['Interfaces', app.interfaces], ['Command endpoint', endpoint(app.app_cmd_host, app.app_cmd_port)],
      ]) + commandPanel + '</div><div class="status-diagnostics-grid"><section class="status-app-connections"><h3>App Connections</h3><div class="status-table-scroll"><table class="zk-tbl"><thead><tr><th>Peer</th><th title="TCP role of the selected app: a client initiates a connection; a server accepts it. Unknown means the app has not reported its role.">Local role</th><th>Connection</th><th>Endpoint</th></tr></thead><tbody>'
      + links.map((link) => `<tr><td>${escape(link.peer)}</td><td>${localRole(item, link.peer)}</td><td><span class="zk-chip ${link.state === 'ESTABLISHED' ? 'is-live' : 'is-warn'}">${escape(display(link.state))}</span></td><td>${escape(link.endpoint)}</td></tr>`).join('')
      + (links.length ? '' : '<tr><td colspan="4">No connection information reported</td></tr>') + '</tbody></table></div>'
      + '<div class="status-processor-summary">'
      + [['queue', 'Queue size', app.queue_size], ['count', 'Processors', processorCount]].map(([key, label, value]) =>
        `<button type="button" class="status-processor-link" data-status-processors="${key}" aria-controls="status-processor-table" aria-expanded="${processorsOpen}"><span>${label}</span><strong>${escape(display(value))}</strong></button>`).join('')
      + '</div></section>' + (item.type === 'ws' ? '<section class="weather-driver-details"><h3>Weather Station</h3>' + specific(item) + '</section>'
        : '<section class="status-entity-connections"><h3>Entity Connections</h3>' + entityTable(item, entities) + '</section>') + '</div>'
      + `<section id="status-processor-table" class="status-processors"${processorsOpen ? '' : ' hidden'}><h3>Processors</h3>`
      + '<div class="status-table-scroll"><table class="zk-tbl"><thead><tr><th>Name</th><th>Current event</th><th>Processing time [ms]</th></tr></thead><tbody>'
      + processors.map(processor => `<tr><td>${escape(processor.name)}</td><td class="status-processor-event">${escape(processor.current_event || 'Idle')}</td><td>${escape(display(processor.processing_time_ms))}</td></tr>`).join('')
      + (processors.length ? '' : '<tr><td colspan="3">No processor snapshot reported</td></tr>') + '</tbody></table></div></section>'
      + (item.type === 'ws' ? weatherView.dashboard(item)
        : '<h3>' + (item.type === 'tm' ? 'Resource Allocations' : escape(item.label)) + '</h3>' + (digitiserPanel ?? specific(item)));

  }

  function init(doc, win, fetcher = fetch) {
    const $ = (id) => doc.getElementById('status-' + id);
    if (!$('workspace')) return;
    const state = { data: null, apps: [], selected: null, loading: false, commandBusy: null, connectionsOpen: false, dishesOpen: false, digitisersOpen: false };
    const refreshMarkup = $('refresh').innerHTML;
    const confirmedFlags = new Map();
    const expandedProcessors = new Set();
    const appCommands = new Map();
    const percentageTrends = new Map();
    let trendStorageKey = null;
    const percentageValues = item => ({
      availability: item.app.availability,
      reliability: item.app.reliability?.reliability ?? item.app.reliability?.reliability_availability,
    });
    function updatePercentageTrends() {
      const storageKey = 'dmd-percentage-trends:' + (state.data.profile || 'default');
      if (trendStorageKey !== storageKey) {
        percentageTrends.clear(); trendStorageKey = storageKey;
        try {
          const saved = JSON.parse(win.localStorage?.getItem(storageKey) || '[]');
          if (Array.isArray(saved)) for (const entry of saved) {
            if (!Array.isArray(entry)) continue;
            const [key, value] = entry;
            if (typeof key === 'string' && Number.isFinite(value?.value)
                && [null, 'up', 'down', 'unchanged'].includes(value.direction)) percentageTrends.set(key, value);
          }
        } catch (_) { /* Trends still work when browser storage is unavailable. */ }
      }
      const present = new Set();
      for (const item of state.apps) {
        const unavailable = state.data.stale || oldUpdate(item.updated)
          || tmDisconnected(state.data.telescope_model, item.type, item.model);
        for (const [metric, value] of Object.entries(percentageValues(item))) {
          const key = item.key + ':' + metric;
          present.add(key);
          if (unavailable) continue; // Hide stale values without forgetting their last trend.
          if (typeof value !== 'number' || !Number.isFinite(value)) {
            percentageTrends.delete(key);
            continue;
          }
          const previous = percentageTrends.get(key), report = unwrap(item.updated);
          // Polling the same retained report must preserve its trend.
          if (previous && previous.report === report && previous.value === value) continue;
          // Equal reports keep the direction of the last actual change.
          const direction = previous ? (value > previous.value ? 'up' : value < previous.value ? 'down'
            : previous.direction || 'unchanged') : null;
          percentageTrends.set(key, { value, report, direction });
        }
      }
      for (const key of percentageTrends.keys()) if (!present.has(key)) percentageTrends.delete(key);
      try { win.localStorage?.setItem(trendStorageKey, JSON.stringify([...percentageTrends])); }
      catch (_) { /* Browser storage may be unavailable. */ }
    }
    function percentageCell(item, metric, unavailable) {
      if (unavailable) return '<td><span class="zk-chip status-unknown">UNKNOWN</span></td>';
      const value = percentageValues(item)[metric];
      const formatted = percent(value);
      const direction = formatted === '—' ? null : percentageTrends.get(item.key + ':' + metric)?.direction;
      const arrow = direction ? `<span class="status-percentage-trend is-${direction}" role="img" aria-label="${direction === 'unchanged' ? 'Unchanged' : 'Trending ' + direction}" title="${direction === 'unchanged' ? 'Unchanged' : 'Trending ' + direction}">${{ up: '↑', down: '↓', unchanged: '↔' }[direction]}</span>` : '';
      return `<td>${formatted}${arrow}</td>`;
    }
    function commandState(item) {
      if (!appCommands.has(item.key)) appCommands.set(item.key, { options: [], selected: '', value: '', loading: false, loaded: false, error: '', result: null });
      return appCommands.get(item.key);
    }
    function commandPanel(item) {
      const draft = commandState(item), option = draft.options.find(option => option.id === draft.selected);
      const disabled = win.DmdSession?.can_edit === false || state.commandBusy !== null || draft.loading;
      const result = draft.result;
      return `<div class="status-app-command" data-status-command-panel="${escape(item.key)}"><label for="status-app-command">Command</label><div class="status-app-command-controls">`
        + `<select id="status-app-command" class="zk-select" data-status-command="select"${disabled ? ' disabled' : ''}><option value="">${draft.loading ? 'Loading commands…' : 'Select command…'}</option>`
        + draft.options.map(option => `<option value="${escape(option.id)}"${option.id === draft.selected ? ' selected' : ''}>${escape(option.label)}</option>`).join('') + '</select>'
        + (option?.takes_value ? `<input class="zk-input" data-status-command="value" aria-label="Command value" placeholder="${option.cmd === 'stop' ? 'Dish ID' : 'Value' + (option.value_required ? ' (required)' : ' (optional)')}" value="${escape(draft.value)}"${option.value_required ? ' required' : ''}${disabled ? ' disabled' : ''}>` : '')
        + `<button type="button" class="zk-btn" data-status-command="send"${disabled || !option ? ' disabled' : ''}>${state.commandBusy?.startsWith(item.key + ':') ? 'Sending…' : 'Send'}</button>`
        + `<p class="status-app-command-result status-app-command-output${result?.status === 'error' ? ' is-error' : ''}${!result ? ' zk-muted' : ''}" role="status" aria-live="polite" aria-label="${result ? 'Command response' : 'Command description'}"${result || option?.description ? ' tabindex="0"' : ''}>${result ? escape(result.time + ' · ' + result.status + (result.code ? ' (' + result.code + ')' : '') + ': ' + result.message) : escape(option?.description || '')}</p></div>`
        + (draft.error ? `<p class="status-app-command-result is-error" role="status">${escape(draft.error)} <button type="button" class="zk-btn is-sm" data-status-command="reload">Retry</button></p>` : '')
        + '</div>';
    }
    async function loadCommands(item) {
      const draft = commandState(item);
      if (draft.loading) return;
      draft.loading = true; draft.error = ''; render();
      const controller = new AbortController(), timer = win.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetcher('/api/commands?app=' + encodeURIComponent(item.type), { cache: 'no-store', signal: controller.signal });
        const body = await response.json();
        if (!response.ok || body.status !== 'success' || !Array.isArray(body.payload?.commands)) throw new Error('Commands unavailable');
        draft.options = body.payload.commands; draft.loaded = true;
        if (!draft.options.some(option => option.id === draft.selected)) { draft.selected = ''; draft.value = ''; }
      } catch (_) { draft.error = 'Could not load available commands.'; }
      finally { win.clearTimeout(timer); draft.loading = false; render(); }
    }
    function flagValue(item, command) {
      const reported = unwrap(item.app[command === 'trace' ? 'app_tracing' : 'app_debug']);
      const key = item.key + ':' + command, confirmed = confirmedFlags.get(key);
      // A different timestamp can still be a report from before the command.
      // Keep the acknowledged setting until both the snapshot and app report
      // are newer than that acknowledgement, including rapid toggle changes.
      const reportTime = Date.parse(unwrap(item.updated));
      const snapshotTime = Date.parse(unwrap(state.data?.snapshot_at));
      if (confirmed && !state.data?.stale && reportTime > confirmed.confirmedAt
          && snapshotTime > confirmed.confirmedAt) confirmedFlags.delete(key);
      return confirmedFlags.get(key)?.value ?? reported;
    }
    function flagControl(item, command) {
      const value = flagValue(item, command), property = command === 'trace' ? 'tracing' : 'debugging';
      const file = command === 'trace' ? 'trace' : 'log';
      const disabled = win.DmdSession?.can_edit === false || state.commandBusy !== null || typeof value !== 'boolean';
      return `<div class="status-flag-control"><button class="status-toggle" type="button" role="switch" aria-checked="${value === true}" data-status-toggle="${command}" aria-label="Toggle ${property} for ${escape(item.id)}" title="${typeof value === 'boolean' ? 'Turn ' + property + (value ? ' OFF' : ' ON') : 'Status unavailable'}"${disabled ? ' disabled' : ''}></button><a class="status-file-link" href="#status-${file}-pane" data-status-file="${file}" aria-label="View ${file} for ${escape(item.id)}">View</a></div>`;
    }
    const files = win.DmdDiagnostics?.init(doc, win, fetcher);
    const dishPane = win.DmdDishStatus?.init(doc, win, fetcher);
    const digitiserControls = digitiserView.init(doc, win, fetcher, () => render());
    let renderedRows = null, renderedDetail = null, renderedCommand = null, renderedDetailKey = null;
    function setText(key, value) {
      const node = doc.querySelector('[data-status-value="' + key + '"]');
      if (node) node.textContent = display(value);
      if (node && key === 'observation.state') {
        node.className = 'zk-chip ' + (['SCANNING', 'READY'].includes(unwrap(value)) ? 'is-live' : 'is-empty');
      }
    }
    function overview() {
      const model = state.data.telescope_model;
      const summaryKeys = ['tm.state', 'dish.state', 'digitiser.state', 'weather.state', 'executions.count'];
      for (const key of summaryKeys) {
        doc.querySelector('[data-status-value="' + key + '"]')?.classList.toggle('status-summary-unavailable', false);
      }
      const dishes = model.dsh_mgr?.dish_store?.dish_list || [];
      const dish = dishes[0] || {};
      const dishCounts = Object.fromEntries(['OK', 'DEGRADED', 'FAILED'].map(health =>
        [health, dishes.filter(item => unwrap(item.health) === health).length]));
      const dishHealth = dishCounts.FAILED ? 'FAILED' : dishCounts.DEGRADED ? 'DEGRADED' : 'OK';
      const dm = model.dsh_mgr?.app || {};
      const dmAged = oldUpdate(dm.last_update);
      const dmUnknown = dmAged || tmDisconnected(model, 'dm', model.dsh_mgr);
      const dmUnavailable = dmUnknown || unwrap(dm.health) === 'FAILED';
      const dmContactMessage = dmAged
        ? 'No contact for ' + Math.floor((Date.now() - new Date(unwrap(dm.last_update)).getTime()) / 60000) + ' minutes'
        : 'DM not connected';
      const digitisers = model.dig_store?.dig_list || [];
      const digitiserHealth = item => {
        if (tmDisconnected(model, 'dig', item) || oldUpdate(item.app?.last_update)) return 'UNKNOWN';
        const health = unwrap(item.app?.health);
        if (health === 'FAILED') return 'FAILED';
        if (item.app?.app_running !== true || !['OK', 'DEGRADED'].includes(health)) return 'UNKNOWN';
        return health;
      };
      const digitiserCounts = { FAILED: 0, UNKNOWN: 0, DEGRADED: 0, OK: 0 };
      for (const item of digitisers) digitiserCounts[digitiserHealth(item)]++;
      const digitiserHealthState = Object.keys(digitiserCounts).find(health => digitiserCounts[health] > 0);
      const weather = model.dsh_mgr?.weather_store?.weather_summaries?.[0] || {};
      const appCounts = { FAILED: 0, UNKNOWN: 0, DEGRADED: 0, OK: 0 };
      for (const item of state.apps) {
        const health = oldUpdate(item.updated) || tmDisconnected(model, item.type, item.model) ? 'UNKNOWN' : item.health;
        appCounts[Object.hasOwn(appCounts, health) ? health : 'UNKNOWN']++;
      }
      const appHealth = Object.keys(appCounts).find(health => appCounts[health] > 0);
      setText('tm.state', appHealth ? appCounts[appHealth] + ' ' + appHealth : null);
      const appState = doc.querySelector('[data-status-value="tm.state"]');
      appState?.classList.toggle('status-summary-failed', ['FAILED', 'UNKNOWN'].includes(appHealth));
      appState?.classList.toggle('status-summary-warning', appHealth === 'DEGRADED');
      appState?.classList.toggle('status-summary-ok', appHealth === 'OK');
      const reportedLinks = persistentAppConnections(model, state.data.stale);
      const established = reportedLinks.filter(link => link.state === 'ESTABLISHED').length;
      const notEstablished = reportedLinks.filter(link => link.state === 'NOT_ESTABLISHED').length;
      setText('tm.detail', 'App Connections');
      const connectionsButton = $('connections-open');
      if (connectionsButton) connectionsButton.hidden = state.data.tm_connected === false || !!state.data.stale;
      const appDetail = doc.querySelector('[data-status-value="tm.detail"]');
      if (appDetail) appDetail.hidden = true;
      setText('tm.connections', 'Established ' + established + ' · Not Established ' + notEstablished);
      const appConnections = doc.querySelector('[data-status-value="tm.connections"]');
      if (appConnections) appConnections.hidden = false;
      setText('dish.heading', dmUnavailable ? 'Dish Manager' : 'Dishes');
      setText('dish.state', dmUnavailable ? (dmUnknown ? 'UNKNOWN' : 'FAILED')
        : dishes.length ? dishCounts[dishHealth] + ' ' + dishHealth : null);
      setText('dish.detail', '');
      const dishState = doc.querySelector('[data-status-value="dish.state"]');
      const dishDetail = doc.querySelector('[data-status-value="dish.detail"]');
      if (dishState) dishState.hidden = false;
      if (dishDetail) dishDetail.hidden = true;
      const operationalModes = dishes.filter(item => ['OPERATE_FULL', 'OPERATE_DEGRADED'].includes(unwrap(item.capability)))
        .map(item => unwrap(item.mode));
      setText('dish.modes', 'Standby ' + operationalModes.filter(mode => ['STANDBY_LP', 'STANDBY_FP'].includes(mode)).length
        + ' · Stow ' + operationalModes.filter(mode => mode === 'STOW').length
        + ' · Operate ' + operationalModes.filter(mode => mode === 'OPERATE').length);
      const dishModes = doc.querySelector('[data-status-value="dish.modes"]');
      if (dmUnknown) setText('dish.modes', dmContactMessage);
      if (dishModes) dishModes.hidden = !dmUnknown && (dmUnavailable || !dishes.length);
      const dishOperational = doc.querySelector('[data-status-value="dish.operational"]');
      if (dishOperational) dishOperational.hidden = !dmUnknown && (dmUnavailable || !dishes.length);
      dishState?.classList.toggle('status-summary-warning', !dmUnavailable && dishes.length > 0 && dishHealth === 'DEGRADED');
      dishState?.classList.toggle('status-summary-failed', dmUnavailable || (dishes.length > 0 && dishHealth === 'FAILED'));
      dishState?.classList.toggle('status-summary-ok', !dmUnavailable && dishes.length > 0 && dishHealth === 'OK');
      dishState?.classList.toggle('status-manager-unavailable', dmUnavailable);
      setText('digitiser.state', digitiserHealthState ? digitiserCounts[digitiserHealthState] + ' ' + digitiserHealthState : null);
      const scanning = digitisers.filter(item => digitiserHealth(item) !== 'UNKNOWN' && item.scanning).length;
      const connectedSdrs = digitisers.filter(item => digitiserHealth(item) !== 'UNKNOWN'
        && unwrap(item.sdr_connected) === 'ESTABLISHED').length;
      setText('digitiser.detail', 'Connected ' + connectedSdrs + ' · Scanning ' + scanning);
      const digitiserLabel = doc.querySelector('[data-status-value="digitiser.label"]');
      if (digitiserLabel) digitiserLabel.hidden = false;
      const digitiserDetail = doc.querySelector('[data-status-value="digitiser.detail"]');
      digitiserDetail?.classList.toggle('status-summary-divider', true);
      const digitiserState = doc.querySelector('[data-status-value="digitiser.state"]');
      digitiserState?.classList.toggle('status-summary-warning', digitiserHealthState === 'DEGRADED');
      digitiserState?.classList.toggle('status-summary-failed', ['FAILED', 'UNKNOWN'].includes(digitiserHealthState));
      digitiserState?.classList.toggle('status-summary-ok', digitiserHealthState === 'OK');
      const weatherStore = model.dsh_mgr?.weather_store || {};
      const metrics = weatherStore.weather_metrics?.[weather.ws_id];
      const station = model.wtr_stn;
      const weatherUnknown = !station || station.app?.app_running !== true
        || oldUpdate(station.app?.last_update) || tmDisconnected(model, 'ws', station)
        || unwrap(station.dm_connected) === 'NOT_ESTABLISHED'
        || unwrap(model.dsh_mgr?.ws_connected) === 'NOT_ESTABLISHED';
      const weatherAlarm = unwrap(weatherStore.weather_enabled) !== false
        && (typeof metrics?.alarm_triggered === 'boolean' ? metrics.alarm_triggered
          : (model.dsh_mgr?.dish_store?.dish_list || []).some(item => unwrap(item.weather_alarm) === true));
      setText('weather.state', weatherUnknown ? 'UNKNOWN' : weatherAlarm ? 'ALARM' : 'SAFE');
      const weatherState = doc.querySelector('[data-status-value="weather.state"]');
      weatherState?.classList.toggle('status-summary-failed', weatherUnknown);
      weatherState?.classList.toggle('weather-alarm-active', !weatherUnknown && weatherAlarm);
      weatherState?.classList.toggle('status-summary-ok', !weatherUnknown && !weatherAlarm);
      const windValue = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—';
      setText('weather.detail', 'Wind');
      setText('weather.wind', 'Avg ' + windValue(metrics?.avg_wind) + ' m/s · Gust '
        + windValue(metrics?.max_wind) + ' m/s');
      const weatherWind = doc.querySelector('[data-status-value="weather.wind"]');
      if (weatherWind) weatherWind.hidden = false;
      const executions = activeExecutions(model);
      setText('executions.count', executions === null ? null : executions.length + ' in progress');
      const sdpUnknown = !model.sdp || model.sdp.app?.app_running === false
        || tmDisconnected(model, 'sdp', model.sdp) || oldUpdate(model.sdp?.app?.last_update);
      const scanCount = key => !sdpUnknown && Number.isInteger(model.sdp?.[key]) && model.sdp[key] >= 0 ? model.sdp[key] : 'UNKNOWN';
      $('executions').innerHTML = '<li class="zk-muted">Scans</li><li class="zk-muted">'
        + (sdpUnknown ? 'SDP not connected.' : 'WIP ' + scanCount('scans_wip')
          + ' · Aborted ' + scanCount('scans_aborted') + ' · Completed ' + scanCount('scans_completed')) + '</li>';
      if (state.data.tm_connected === false || state.data.stale) {
        if (appConnections) appConnections.hidden = true;
        if (dishModes) dishModes.hidden = true;
        if (dishOperational) dishOperational.hidden = true;
        if (weatherWind) weatherWind.hidden = true;
        const received = new Date(unwrap(state.data.received_at || state.data.snapshot_at)).getTime();
        const seconds = Math.max(state.data.age_seconds || 0, Number.isFinite(received) ? (Date.now() - received) / 1000 : 0);
        const message = seconds > 120 ? 'No contact for ' + Math.floor(seconds / 60) + ' minutes' : 'Lost contact with TM';
        setText('dish.heading', 'Dish Manager');
        for (const key of summaryKeys) {
          setText(key, 'UNKNOWN');
          const node = doc.querySelector('[data-status-value="' + key + '"]');
          node?.classList.toggle('status-summary-unavailable', true);
          if (node) node.hidden = false;
        }
        for (const key of ['tm.detail', 'dish.detail', 'digitiser.detail', 'weather.detail']) {
          setText(key, message);
          const node = doc.querySelector('[data-status-value="' + key + '"]');
          if (node) node.hidden = false;
        }
        digitiserDetail?.classList.toggle('status-summary-divider', false);
        if (digitiserLabel) digitiserLabel.hidden = true;
        $('executions').innerHTML = '<li class="zk-muted">' + escape(message) + '</li>';
      }
    }
    function render() {
      if (state.data) overview();
      const connectionsPane = $('connections-pane');
      if (connectionsPane) connectionsPane.hidden = !state.connectionsOpen;
      $('connections-open')?.setAttribute?.('aria-expanded', String(state.connectionsOpen));
      if (state.connectionsOpen && state.data) {
        const html = consolidatedConnectionTable(state.data.telescope_model, state.data.stale);
        if ($('connections-rows').innerHTML !== html) $('connections-rows').innerHTML = html;
      }
      const now = Date.now();
      const focused = doc.activeElement?.closest?.('[data-app-key]')?.dataset.appKey;
      const focusedFile = doc.activeElement?.dataset?.statusFile;
      const focusedToggle = doc.activeElement?.dataset?.statusToggle;
      const rows = state.apps.map(item => {
        const stale = oldUpdate(item.updated, now) || tmDisconnected(state.data.telescope_model, item.type, item.model);
        const active = (item.app.processors || []).filter(processor => processor.current_event
          && typeof processor.processing_time_ms === 'number' && Number.isFinite(processor.processing_time_ms)
          && processor.processing_time_ms >= 0);
        const processing = active.length
          ? (active.reduce((total, processor) => total + processor.processing_time_ms, 0) / active.length).toFixed(2) + ' ms' : '—';
        const unknown = '<span class="zk-chip status-unknown">UNKNOWN</span>';
        const cell = value => `<td>${value === 'UNKNOWN' ? unknown : escape(value)}</td>`;
        return `<tr data-app-key="${escape(item.key)}" tabindex="0" aria-label="Inspect ${escape(item.id)}" aria-selected="${item.key === state.selected}" class="${item.key === state.selected ? 'is-selected' : ''}"><td>${escape(item.label)}</td><td>${escape(item.id)}</td><td>${escape(display(item.profile))}</td><td>${stale || item.health === 'UNKNOWN' ? unknown : chip(item.health)}</td><td>${lastUpdate(item.updated, now)}</td>${cell(stale ? 'UNKNOWN' : display(item.app.queue_size))}${cell(stale ? 'UNKNOWN' : processing)}${percentageCell(item, 'availability', stale || state.data.stale)}${percentageCell(item, 'reliability', stale || state.data.stale)}<td>${flagControl(item, 'trace')}</td><td>${flagControl(item, 'debug')}</td></tr>`;
      }).join('') || '<tr><td colspan="11">No applications reported</td></tr>';
      if (renderedRows !== rows) {
        $('apps').innerHTML = rows;
        renderedRows = rows;
        // Keep keyboard focus without pulling a reader back up to the table.
        const row = focused && Array.from($('apps').querySelectorAll('[data-app-key]')).find((row) => row.dataset.appKey === focused);
        (focusedFile ? row?.querySelector('[data-status-file="' + focusedFile + '"]')
          : focusedToggle ? row?.querySelector('[data-status-toggle="' + focusedToggle + '"]') : row)?.focus({ preventScroll: true });
      }
      const selected = state.apps.find((item) => item.key === state.selected);
      if (selected?.type !== 'dig') digitiserControls.clearSelection();
      if (!selected) state.selected = null;
      if ($('dishes-pane')) $('dishes-pane').hidden = !state.dishesOpen;
      $('dishes-open')?.setAttribute?.('aria-expanded', String(state.dishesOpen));
      if (state.dishesOpen && state.data) {
        const content = dishesTable(state.data.telescope_model.dsh_mgr);
        if ($('dishes-content').innerHTML !== content) $('dishes-content').innerHTML = content;
        $('dishes-freshness').textContent = state.data.stale
          || tmDisconnected(state.data.telescope_model, 'dm', state.data.telescope_model.dsh_mgr)
          || oldUpdate(state.data.telescope_model.dsh_mgr?.app?.last_update) ? 'Stale Dish Manager data' : '';
      }
      dishPane?.update(state.dishesOpen ? state.data : null);
      if ($('digitisers-pane')) $('digitisers-pane').hidden = !state.digitisersOpen;
      $('digitisers-open')?.setAttribute?.('aria-expanded', String(state.digitisersOpen));
      if (state.digitisersOpen && state.data) {
        const content = digitisersTable(state.data.telescope_model, state.data.stale);
        if ($('digitisers-content').innerHTML !== content) $('digitisers-content').innerHTML = content;
      }
      $('detail').hidden = !selected;
      $('workspace').classList.toggle('has-selection', !!selected);
      if (selected) {
        $('detail-title').textContent = selected.label + ' — ' + selected.id;
        $('detail-subtitle').textContent = state.data?.stale ? 'Stale snapshot' : '';
        $('detail-subtitle').className = state.data?.stale ? 'zk-chip is-warn snapshot-warning' : '';
        $('detail-subtitle').hidden = !state.data?.stale;
        const panel = commandPanel(selected);
        const content = detail(selected, expandedProcessors.has(selected.key), panel,
          selected.type === 'dig' ? digitiserControls.view(selected, state.data) : null);
        if (renderedDetail !== content) {
          const processorFocus = doc.activeElement?.closest?.('[data-status-processors]')?.dataset.statusProcessors;
          const commandFocus = doc.activeElement?.dataset?.statusCommand;
          const digitiserFocus = doc.activeElement?.dataset?.digitiserField;
          const digitiserSelection = digitiserFocus && doc.activeElement.selectionStart != null
            ? [doc.activeElement.selectionStart, doc.activeElement.selectionEnd] : null;
          const previousControls = $('detail-content').querySelector?.('[data-digitiser-control-app]');
          const previousInputs = previousControls?.dataset.digitiserControlApp === selected.key
            ? Array.from(previousControls.querySelectorAll('[data-digitiser-field]')) : [];
          const selection = commandFocus === 'value' ? [doc.activeElement.selectionStart, doc.activeElement.selectionEnd] : null;
          const previousPanel = renderedCommand === panel && $('detail-content').querySelector?.('[data-status-command-panel]');
          const dishScroll = $('detail-content').querySelector?.('.status-dish-connections-scroll');
          const keepDishScroll = dishScroll?.dataset.statusScrollApp === selected.key;
          const weatherHistory = renderedDetailKey === selected.key && $('detail-content').querySelector?.('.weather-alarm-history');
          $('detail-content').innerHTML = content;
          for (const input of previousInputs) {
            const replacement = $('detail-content').querySelector('[data-digitiser-field="' + input.dataset.digitiserField + '"]');
            if (replacement) {
              input.disabled = replacement.disabled;
              if (input.value !== replacement.value) input.value = replacement.value;
              replacement.replaceWith(input);
            }
          }
          if (weatherHistory) {
            const nextHistory = $('detail-content').querySelector?.('.weather-alarm-history');
            if (nextHistory) nextHistory.open = weatherHistory.open;
          }
          if (keepDishScroll) {
            const nextScroll = $('detail-content').querySelector?.('.status-dish-connections-scroll');
            if (nextScroll) { nextScroll.scrollTop = dishScroll.scrollTop; nextScroll.scrollLeft = dishScroll.scrollLeft; }
          }
          if (previousPanel) $('detail-content').querySelector('[data-status-command-panel]')?.replaceWith(previousPanel);
          renderedDetail = content;
          renderedDetailKey = selected.key;
          renderedCommand = panel;
          if (processorFocus) $('detail-content').querySelector?.('[data-status-processors="' + processorFocus + '"]')?.focus({ preventScroll: true });
          if (digitiserFocus) {
            const input = $('detail-content').querySelector?.('[data-digitiser-field="' + digitiserFocus + '"]');
            input?.focus({ preventScroll: true });
            if (digitiserSelection) input?.setSelectionRange?.(...digitiserSelection);
          }
          if (commandFocus) {
            const control = $('detail-content').querySelector?.('[data-status-command="' + commandFocus + '"]');
            control?.focus({ preventScroll: true });
            if (selection) control?.setSelectionRange?.(...selection);
          }
        }
      }
    }
    function badge(text, style) {
      $('connection').textContent = text;
      $('connection').hidden = style === 'is-live';
      ['is-live', 'is-warn', 'is-bad', 'is-empty'].forEach((key) => $('connection').classList.toggle(key, key === style));
    }
    async function refresh(fresh = false) {
      if (state.loading) return;
      state.loading = true; $('refresh').disabled = true;
      if (fresh) $('refresh').textContent = 'Requesting TM snapshot…';
      const controller = new AbortController(); const timeout = win.setTimeout(() => controller.abort(), fresh ? 35000 : 5000);
      try {
        const response = await fetcher('/api/web/telescope/model' + (fresh ? '?refresh=1' : ''), { cache: 'no-store', signal: controller.signal });
        const envelope = await response.json();
        if (!response.ok || envelope.status !== 'success' || !envelope.payload?.telescope_model) throw new Error(envelope.message || 'Telescope model unavailable');
        state.data = envelope.payload; state.apps = applications(state.data.telescope_model);
        win.DmdSiderealClock?.setConfig(state.data.telescope_model.config);
        const stale = state.data.stale || state.data.tm_connected === false;
        state.data.stale = stale;
        updatePercentageTrends();
        $('freshness').className = stale ? 'zk-chip is-warn snapshot-warning' : 'zk-muted';
        badge(state.data.tm_connected === false ? 'TM disconnected' : stale ? 'TM snapshot stale' : 'TM snapshot available', stale ? 'is-warn' : 'is-live');
        $('freshness').textContent = (stale ? 'Stale snapshot. ' : '') + 'TM Config Profile: ' + display(state.data.profile)
          + ' · Snapshot: ' + timestamp(state.data.snapshot_at) + ' · Received ' + Math.round(state.data.age_seconds || 0)
          + ' seconds ago. WEB polls TM every 30 seconds.' + (state.data.last_error ? ' ' + state.data.last_error : '');
        render();
      } catch (error) {
        badge('Live status unavailable', 'is-bad');
        $('freshness').className = 'zk-chip is-warn snapshot-warning';
        $('freshness').textContent = error.message + (state.data ? ' Showing the last snapshot as stale.' : ' Waiting for a TM snapshot.');
        if (state.data) { state.data.stale = true; render(); }
      } finally {
        win.clearTimeout(timeout); state.loading = false; $('refresh').disabled = false;
        $('refresh').innerHTML = refreshMarkup;
      }
    }
    function select(event) {
      const row = event.target.closest('[data-app-key]');
      if (!row) return;
      const control = event.target.closest('[data-status-toggle]');
      if (control?.dataset.statusToggle) return sendCommand(row.dataset.appKey, control.dataset.statusToggle);
      const link = event.target.closest('[data-status-file]');
      if (link?.dataset.statusFile) {
        event.preventDefault();
        const item = state.apps.find((app) => app.key === row.dataset.appKey);
        if (!item) return;
        state.selected = null; render();
        files?.open(item, link.dataset.statusFile);
        return;
      }
      files?.closeAll();
      state.selected = row.dataset.appKey;
      state.dishesOpen = state.apps.find(app => app.key === state.selected)?.type === 'dm';
      render();
      const item = state.apps.find(app => app.key === state.selected);
      if (item && !commandState(item).loaded) loadCommands(item);
      $('detail').scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    }
    function response(item, status, message, code) {
      commandState(item).result = { status, message, code, time: timestamp(new Date().toISOString()) };
      $('command-response').hidden = false;
      $('command-app').textContent = item.id;
      $('command-time').textContent = timestamp(new Date().toISOString());
      $('command-status').textContent = status + (code ? ' (' + code + ')' : '');
      $('command-message').textContent = message;
      $('command-response').classList.toggle('is-error', status === 'error');
    }
    async function sendCommand(key, cmd) {
      const item = state.apps.find(app => app.key === key);
      if (!item || !['trace', 'debug'].includes(cmd) || state.commandBusy !== null || win.DmdSession?.can_edit === false) return;
      const previous = flagValue(item, cmd);
      if (typeof previous !== 'boolean') return;
      const value = previous ? 'OFF' : 'ON';
      state.commandBusy = key + ':' + cmd;
      response(item, 'pending', 'Sending ' + cmd.toUpperCase() + (value ? ' ' + value : '') + '…');
      render();
      const controller = new AbortController(), timer = win.setTimeout(() => controller.abort(), 35000);
      try {
        const result = await fetcher('/api/commands', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ app: item.type === 'dig' ? item.id : item.type, cmd, ...(value ? { value } : {}) }) });
        const body = await result.json();
        if (!['success', 'error'].includes(body.status) || typeof body.message !== 'string') throw new Error('Invalid response');
        if (result.ok && body.status === 'success') confirmedFlags.set(key + ':' + cmd, { value: value === 'ON', confirmedAt: Date.now() });
        response(item, body.status, body.message, body.code);
      } catch (_) {
        response(item, 'error', 'No valid response was received. Command outcome is unknown; check the application before retrying.', 'outcome_unknown');
      } finally {
        win.clearTimeout(timer); state.commandBusy = null; render();
      }
    }
    async function sendSelectedCommand(item) {
      const draft = commandState(item), option = draft.options.find(option => option.id === draft.selected);
      if (!option || draft.loading || state.commandBusy !== null || win.DmdSession?.can_edit === false) return;
      const value = option.takes_value ? draft.value.trim() : option.value;
      if (option.value_required && !value) {
        response(item, 'error', option.cmd === 'stop' ? 'Enter a dish ID.' : 'Enter a command value.', 'invalid_request'); render(); return;
      }
      state.commandBusy = item.key + ':' + option.cmd;
      response(item, 'pending', 'Sending ' + option.label + '…'); render();
      const controller = new AbortController(), timer = win.setTimeout(() => controller.abort(), 35000);
      try {
        const result = await fetcher('/api/commands', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ app: item.type === 'dig' ? item.id : item.type, cmd: option.cmd,
            ...(value !== undefined && value !== '' ? { value } : {}) }) });
        const body = await result.json();
        if (!['success', 'error'].includes(body.status) || typeof body.message !== 'string') throw new Error('Invalid response');
        const status = result.ok && body.status === 'success' ? 'success' : 'error';
        if (status === 'success' && ['trace', 'debug'].includes(option.cmd) && ['ON', 'OFF'].includes(value)) {
          confirmedFlags.set(item.key + ':' + option.cmd, { value: value === 'ON', confirmedAt: Date.now() });
        }
        response(item, status, body.message, body.code);
      } catch (_) {
        response(item, 'error', 'No valid response was received. Command outcome is unknown; check the application before retrying.', 'outcome_unknown');
      } finally { win.clearTimeout(timer); state.commandBusy = null; render(); }
    }
    win.addEventListener('dmd:session', render);
    $('digitisers-open')?.addEventListener('click', () => {
      files?.closeAll(); state.selected = null; state.dishesOpen = false; state.digitisersOpen = true;
      render(); $('digitisers-pane')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    });
    $('digitisers-close')?.addEventListener('click', () => { state.digitisersOpen = false; render(); });
    const selectDigitiser = event => {
      const row = event.target.closest('[data-status-digitiser]');
      if (!row) return;
      const item = state.apps.find(app => app.key === row.dataset.statusDigitiser);
      if (!item) return;
      state.selected = item.key; render();
      if (!commandState(item).loaded) loadCommands(item);
      $('detail').scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    };
    $('digitisers-content')?.addEventListener('click', selectDigitiser);
    $('digitisers-content')?.addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && !event.repeat) { event.preventDefault(); selectDigitiser(event); }
    });
    $('dishes-open')?.addEventListener('click', () => {
      files?.closeAll(); state.selected = null; state.dishesOpen = true;
      render(); $('dishes-pane')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    });
    $('dishes-close')?.addEventListener('click', () => { state.dishesOpen = false; render(); });
    const selectDish = event => {
      const row = event.target.closest('[data-status-dish]');
      if (row) dishPane?.open(row.dataset.statusDish, state.data);
    };
    $('dishes-content')?.addEventListener('click', selectDish);
    $('dishes-content')?.addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && !event.repeat) { event.preventDefault(); selectDish(event); }
    });
    $('connections-open')?.addEventListener('click', () => {
      if (!state.data || state.data.stale || state.data.tm_connected === false) return;
      state.connectionsOpen = !state.connectionsOpen;
      render();
      if (state.connectionsOpen) $('connections-pane')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    });
    $('connections-close')?.addEventListener('click', () => {
      state.connectionsOpen = false; render(); $('connections-open')?.focus?.({ preventScroll: true });
    });
    $('apps').addEventListener('click', select);
    $('apps').addEventListener('keydown', (event) => {
      if (event.target.closest('[data-status-toggle]')?.dataset.statusToggle) return;
      if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) { event.preventDefault(); select(event); }
    });
    $('detail-content').addEventListener('click', event => {
      const command = event.target.closest('[data-status-command]')?.dataset.statusCommand;
      const selected = state.apps.find(item => item.key === state.selected);
      if (command === 'send' && selected) return sendSelectedCommand(selected);
      if (command === 'reload' && selected) return loadCommands(selected);
      const processorControl = event.target.closest('[data-status-processors]');
      if (processorControl && state.selected) {
        if (expandedProcessors.has(state.selected)) expandedProcessors.delete(state.selected);
        else expandedProcessors.add(state.selected);
        render();
        return;
      }
      const row = event.target.closest('[data-status-dish]');
      if (row) dishPane?.open(row.dataset.statusDish, state.data);
    });
    $('detail-content').addEventListener('change', event => {
      const item = state.apps.find(item => item.key === state.selected);
      if (item && event.target.dataset?.statusCommand === 'select' && state.commandBusy === null) {
        const draft = commandState(item);
        draft.selected = event.target.value; draft.value = ''; draft.result = null; render();
      }
    });
    $('detail-content').addEventListener('input', event => {
      const item = state.apps.find(item => item.key === state.selected);
      if (item && event.target.dataset?.statusCommand === 'value') commandState(item).value = event.target.value;
    });
    $('detail-content').addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && !event.repeat) {
        const row = event.target.closest('[data-status-dish]');
        if (row) { event.preventDefault(); dishPane?.open(row.dataset.statusDish, state.data); }
      }
    });
    $('detail-close').addEventListener('click', () => { state.selected = null; render(); });
    $('refresh').addEventListener('click', () => refresh(true));
    refresh();
    const timer = win.setInterval(refresh, 5000);
    win.addEventListener('pagehide', () => win.clearInterval(timer));
    return { refresh, state };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, applications, detail, dishesTable, digitisersTable, connections, consolidatedConnections, persistentAppConnections, display, timestamp, lastUpdate, percent };
  if (typeof document !== 'undefined') init(document, window);
})();
