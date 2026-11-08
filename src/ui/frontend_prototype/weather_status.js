/* Weather displays use DM's retained readings and full-resolution summaries. */
(function () {
  const unwrap = value => value && typeof value === 'object' && 'value' in value ? value.value : value;
  const escape = value => String(value ?? '—').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const numeric = value => number(value) ? value.toFixed(2) : '—';
  const millis = value => { const raw = unwrap(value); return number(raw) ? raw : raw ? Date.parse(raw) : NaN; };
  const timestamp = value => Number.isFinite(millis(value)) ? new Date(millis(value)).toISOString().slice(0, 19).replace('T', ' ') + ' UTC' : '—';
  const kv = pairs => '<dl class="status-kv weather-values">' + pairs.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('') + '</dl>';
  function data(item) {
    const store = item.weatherStore || {};
    const history = Array.isArray(store.weather_history) && store.weather_history.length ? store.weather_history : store.weather_data;
    const samples = (Array.isArray(history) ? history : []).filter(sample => sample.ws_id === item.id && Number.isFinite(millis(sample.obs_time)))
      .sort((a, b) => millis(a.obs_time) - millis(b.obs_time));
    return { store, samples, summary: (store.weather_summaries || []).find(summary => summary.ws_id === item.id),
      metrics: store.weather_metrics?.[item.id] };
  }
  function missingHistory(store, summary) {
    if (!(summary?.sample_count > 0)) return null;
    return `Wind summary available (${summary.sample_count} samples), but individual readings have not reached this view.`
      + (!Object.prototype.hasOwnProperty.call(store, 'weather_history')
        ? ' Restart Dish Manager and Telescope Manager to enable weather history, then wait for the next status refresh.' : '');
  }
  function chart(item) {
    const { store, samples, summary, metrics } = data(item);
    const readings = samples.filter(sample => number(sample.wind_speed) && sample.wind_speed >= 0);
    const thresholds = [['Average threshold', store.threshold_wind_avg, 'average'], ['Gust threshold', store.threshold_wind_gust, 'gust']]
      .filter(([, value]) => number(value) && value >= 0);
    let html = '<section class="status-weather-panel"><h3>Wind Speed</h3>';
    if (!readings.length) {
      html += '<p class="zk-muted">' + escape(samples.length ? 'The received readings contain no valid wind speeds.'
        : missingHistory(store, summary) || 'No recent wind readings available for this station.') + '</p>';
    } else {
      const end = millis(samples[samples.length - 1].obs_time);
      const duration = number(store.retention_period) && store.retention_period > 0 ? store.retention_period : 300;
      const start = end - duration * 1000;
      const maximum = Math.max(1, ...readings.map(sample => sample.wind_speed), ...thresholds.map(([, value]) => value));
      const top = Math.ceil(maximum * 1.08), left = 52, right = 704, upper = 18, bottom = 270;
      const x = time => left + (time - start) / (end - start) * (right - left);
      const y = speed => bottom - speed / top * (bottom - upper);
      const coordinate = value => value.toFixed(2);
      let grid = '', ticks = '';
      for (let index = 0; index <= 5; index++) {
        const speed = top * index / 5, xpos = left + (right - left) * index / 5, ypos = y(speed);
        grid += `<line x1="${left}" y1="${coordinate(ypos)}" x2="${right}" y2="${coordinate(ypos)}"/><line x1="${coordinate(xpos)}" y1="${upper}" x2="${coordinate(xpos)}" y2="${bottom}"/>`;
        ticks += `<text x="44" y="${coordinate(ypos + 4)}" text-anchor="end">${numeric(speed)}</text><text x="${coordinate(xpos)}" y="289" text-anchor="middle">${Math.round(duration * index / 5)}</text>`;
      }
      const visible = samples.filter(sample => millis(sample.obs_time) >= start);
      let path = '', previous = null;
      const gap = Math.max(10000, (item.model.driver_poll_period || 1000) * 3);
      for (const sample of visible) {
        if (!number(sample.wind_speed) || sample.wind_speed < 0) { previous = null; continue; }
        const measured = millis(sample.obs_time);
        path += (previous === null || measured - previous > gap ? 'M' : 'L') + coordinate(x(measured)) + ',' + coordinate(y(sample.wind_speed)) + ' ';
        previous = measured;
      }
      const latest = readings[readings.length - 1];
      html += '<figure class="weather-chart"><svg viewBox="0 0 720 320" role="img" aria-label="Recent wind speed in metres per second, with average and gust thresholds">'
        + '<g class="weather-chart-grid">' + grid + '</g><g class="weather-chart-labels">' + ticks
        + '<text x="378" y="313" text-anchor="middle">Seconds</text><text transform="translate(14 144) rotate(-90)" text-anchor="middle">Wind speed [m/s]</text></g>'
        + thresholds.map(([, value, style]) => `<line class="weather-threshold ${style}" x1="${left}" y1="${coordinate(y(value))}" x2="${right}" y2="${coordinate(y(value))}"/>`).join('')
        + `<path class="weather-wind-line" d="${path.trim()}"/>`
        + visible.filter(sample => number(sample.wind_speed) && sample.wind_speed >= 0).map(sample => `<circle class="weather-wind-point" cx="${coordinate(x(millis(sample.obs_time)))}" cy="${coordinate(y(sample.wind_speed))}" r="2"><title>${escape(timestamp(sample.obs_time) + ' · ' + numeric(sample.wind_speed) + ' m/s')}</title></circle>`).join('')
        + '</svg><figcaption><span class="weather-key wind">Wind speed ' + numeric(latest.wind_speed) + ' m/s</span>'
        + thresholds.map(([label, value, style]) => `<span class="weather-key ${style}">${label} ${numeric(value)} m/s</span>`).join('')
        + `<span class="weather-chart-window">${escape(timestamp(start))} – ${escape(timestamp(end))} · ${visible.length} displayed readings</span></figcaption></figure>`;
      if (metrics?.timeout_triggered === true) html += '<p class="weather-data-old">Dish Manager reports a weather data timeout.</p>';
    }
    return html + '</section>';
  }
  function duration(minutes) {
    if (!number(minutes) || minutes < 0) return '—';
    const total = Math.round(minutes * 60), days = Math.floor(total / 86400);
    return [days, Math.floor(total / 3600) % 24, Math.floor(total / 60) % 60, total % 60].map(value => String(value).padStart(2, '0')).join(':');
  }
  function attributes(item) {
    const { store, summary, metrics } = data(item);
    const station = (store.weather_stations || []).find(station => station.ws_id === item.id);
    const valid = metrics?.sample_count > 0;
    const actual = (value, flag, known = true) => `<span class="weather-actual ${!known ? 'is-empty' : flag === true ? 'is-bad' : flag === false ? 'is-live' : 'is-empty'}">${escape(value)}</span>`;
    const withUnit = (value, unit) => number(value) ? numeric(value) + ' ' + unit : '—';
    const row = (label, threshold, value) => `<tr><th scope="row">${label}</th><td><span class="weather-limit">${escape(threshold)}</span></td><td>${value}</td></tr>`;
    const enabled = unwrap(store.weather_enabled);
    const alarm = enabled === false ? 'Not monitored' : metrics?.alarm_triggered === true ? 'ALARM' : valid && metrics?.alarm_triggered === false ? 'OK' : '—';
    let html = '<section class="weather-attributes"><h3>Weather Attributes</h3><p class="weather-station-label">' + escape(item.id)
      + (station?.location ? ' · ' + escape(station.location) : '') + '</p>'
      + '<table class="weather-attributes-table"><thead><tr><th>Attribute</th><th>Threshold</th><th>Actual</th></tr></thead><tbody>'
      + row('Timeout / Age', withUnit(store.threshold_timeout, 's'), actual(withUnit(metrics?.latest_age_sec, 's'), metrics?.timeout_triggered, number(metrics?.latest_age_sec)))
      + row('Wind average', withUnit(store.threshold_wind_avg, 'm/s'), actual(valid ? withUnit(metrics.avg_wind, 'm/s') : '—', metrics?.wind_avg_triggered, valid))
      + row('Wind gust', withUnit(store.threshold_wind_gust, 'm/s'), actual(valid ? withUnit(metrics.max_wind, 'm/s') : '—', metrics?.gust_triggered, valid))
      + row('Gust count', store.threshold_wind_count ?? '—', actual(valid ? metrics.gust_count : '—', metrics?.gust_count_triggered, valid))
      + row('Samples', number(metrics?.window_secs) ? metrics.window_secs + ' s window' : '—', actual(metrics?.sample_count ?? '—', valid ? false : undefined))
      + row('Alarm', enabled === true ? 'Monitoring on' : enabled === false ? 'Monitoring off' : '—', actual(alarm, metrics?.alarm_triggered, enabled !== false && alarm !== '—'))
      + '</tbody></table><p class="weather-report-time">DM update: ' + escape(timestamp(metrics?.reported_at)) + '</p>';
    if (!metrics) html += '<p class="zk-muted weather-metrics-missing">Alarm-window actuals unavailable. Restart DM and TM to load weather metrics.</p>';
    if (summary) {
      const hasWind = summary.sample_count > 0;
      html += '<h4>Rolling wind summary</h4>' + kv([
        ['Window [s]', summary.sample_secs ?? '—'], ['Wind samples', summary.sample_count ?? '—'],
        ['Average [m/s]', hasWind ? numeric(summary.wind_avg) : '—'], ['Maximum [m/s]', hasWind ? numeric(summary.wind_max) : '—'],
        ['RMS [m/s]', hasWind ? numeric(summary.wind_rms) : '—'], ['Last sample UTC', timestamp(summary.last_sample_time)],
      ]);
    } else html += '<p class="zk-muted">No rolling summary available for this station.</p>';
    html += '<details class="weather-alarm-history"><summary>DM alarm history · trailing month</summary>' + kv([
      ['Last alarm UTC', timestamp(store.trigger_dt)], ['Alarm count', store.last_mth_alarm_count ?? '—'],
      ['Time active', duration(store.last_mth_alarm_activated)], ['Time clear', duration(store.last_mth_alarm_deactivated)],
      ['Mean time to recovery', duration(store.last_mth_alarm_mttr)], ['Mean time to alarm', duration(store.last_mth_alarm_mtta)],
    ]) + '<p class="zk-muted">Durations: DD:HH:MM:SS · updated hourly</p></details>';
    return html + '</section>';
  }
  const dashboard = item => '<div class="weather-dashboard">' + attributes(item) + chart(item) + '</div>';
  const api = { chart, attributes, dashboard, data, duration };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.DmdWeatherStatus = api;
})();
