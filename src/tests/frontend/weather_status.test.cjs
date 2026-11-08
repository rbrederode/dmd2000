const test = require('node:test');
const assert = require('node:assert/strict');
const { chart, attributes, dashboard, data, duration } = require('../../ui/frontend_prototype/weather_status.js');
const { applications, detail } = require('../../ui/frontend_prototype/status.js');

function fixture() {
  const now = Date.now();
  const sample = (seconds, wind, ws_id = 'ws001') => ({ ws_id, obs_time: { value: new Date(now + seconds * 1000).toISOString() },
    wind_speed: wind, temperature: 0, humidity: 50, pressure: 1010, precipitation: 0 });
  const store = { retention_period: 300, summary_window: 60, threshold_timeout: 30,
    threshold_wind_avg: 10, threshold_wind_gust: 15, threshold_wind_count: 3, weather_enabled: true,
    weather_metrics: { ws001: { sample_count: 30, window_secs: 30, latest_age_sec: 0.7,
      avg_wind: 2.5, max_wind: 7, gust_count: 0, timeout_triggered: false, wind_avg_triggered: false,
      gust_triggered: false, gust_count_triggered: false, alarm_triggered: false, reported_at: sample(0, 0).obs_time } },
    weather_history: [sample(0, 6.26), sample(-2, 3), sample(-1, 0), sample(0, 90, 'ws002')],
    weather_summaries: [{ ws_id: 'ws001', sample_secs: 60, sample_count: 61, wind_avg: 4, wind_max: 8, wind_rms: 5,
      last_sample_time: sample(0, 0).obs_time }] };
  const model = { wtr_stn: { id: 'ws001', app: {}, driver_poll_period: 1000, tm_connected: { value: 'ESTABLISHED' } },
    dsh_mgr: { app: {}, weather_store: store } };
  return { item: applications(model).find(item => item.type === 'ws'), store, model, sample };
}

test('weather replaces the WS entity pane with a wind graph, thresholds and actual summary count', () => {
  const { item } = fixture();
  const html = detail(item);
  assert.doesNotMatch(html, /Entity Connections|status-entity-connections/);
  assert.match(html, /App Connections/);
  assert.match(html, /<h3>Wind Speed<\/h3>/);
  assert.match(html, /class="weather-wind-line"/);
  assert.match(html, /Average threshold 10.00 m\/s/);
  assert.match(html, /Gust threshold 15.00 m\/s/);
  assert.match(html, /Wind speed 6.26 m\/s/);
  assert.match(html, /Window \[s\]<\/dt><dd>60/);
  assert.match(html, /Wind samples<\/dt><dd>61/);
  assert.match(html, /Seconds/);
  assert.doesNotMatch(html, /90\.00 m\/s|NaN|Infinity/);
  assert.equal(data(item).samples.length, 3);
});

test('anemometer dashboard compares authoritative alarm-window actuals against thresholds', () => {
  const { item } = fixture();
  const html = dashboard(item);
  assert.match(html, /Weather Attributes/);
  assert.match(html, /<th>Threshold<\/th><th>Actual<\/th>/);
  assert.match(html, /0.70 s/);
  assert.match(html, /2.50 m\/s/); // DM's 30-second alarm average, not the 60-second summary average.
  assert.match(html, /weather-actual is-live">0<\/span>/);
  assert.match(html, /30 s window/);
  assert.match(html, /weather-actual is-live">OK<\/span>/);
  assert.doesNotMatch(html, /Latest reading|Recent readings|Temperature|Humidity|Precipitation|Pressure|Air quality|UV index|Cloud cover/);
  assert.ok(html.indexOf('Weather Attributes') < html.indexOf('Wind Speed'));
});

test('missing, old and zero-count weather data are displayed without fabricated observations', () => {
  const { item, store, sample } = fixture();
  store.weather_history = [sample(-60, 0)];
  assert.match(chart(item), /Wind speed 0.00 m\/s/);
  assert.doesNotMatch(chart(item), /weather-data-old/); // A cached reading alone does not change DM's alarm state.
  store.weather_metrics.ws001.timeout_triggered = true;
  assert.match(chart(item), /reports a weather data timeout/);
  store.weather_history = [];
  store.weather_summaries[0].sample_count = 0;
  let html = dashboard(item);
  assert.match(html, /No recent wind readings/);
  assert.match(html, /Average \[m\/s\]<\/dt><dd>—/);
  store.weather_summaries = [];
  assert.match(attributes(item), /No rolling summary/);
});

test('weather charts handle absent thresholds, invalid samples and escaped station data', () => {
  const { item, store, sample } = fixture();
  store.threshold_wind_avg = null; store.threshold_wind_gust = undefined;
  store.weather_history.push(sample(1, null), sample(2, 4));
  let html = chart(item);
  assert.doesNotMatch(html, /weather-threshold|NaN|Infinity/);
  assert.match(html, /Wind speed 4.00 m\/s/);
  // A missing wind reading breaks the line rather than inventing a connecting measurement.
  assert.equal((html.match(/<path class="weather-wind-line" d="([^"]+)"/)[1].match(/M/g) || []).length, 2);
  store.weather_history = [{ ws_id: 'ws002', obs_time: sample(0, 0).obs_time, wind_speed: 50 }];
  assert.match(chart(item), /individual readings have not reached this view/);
});

test('summary-only snapshots distinguish missing forwarded history from no observations', () => {
  const { item, store } = fixture();
  delete store.weather_history;
  store.weather_data = [];
  let html = chart(item);
  assert.match(html, /Wind summary available \(61 samples\)/);
  assert.match(html, /Restart Dish Manager and Telescope Manager/);
  assert.doesNotMatch(html, /No recent wind readings/);
  assert.match(attributes(item), /Wind samples<\/dt><dd>61/);
  store.weather_history = [];
  html = chart(item);
  assert.match(html, /individual readings have not reached this view/);
  assert.doesNotMatch(html, /Restart Dish Manager/);
});

test('threshold violations, disabled monitoring and month durations remain explicit', () => {
  const { item, store } = fixture();
  Object.assign(store.weather_metrics.ws001, { gust_count: 4, gust_count_triggered: true, alarm_triggered: true });
  assert.match(attributes(item), /weather-actual is-bad">4<\/span>/);
  assert.match(attributes(item), /weather-actual is-bad">ALARM<\/span>/);
  store.weather_enabled = false;
  assert.match(attributes(item), /weather-actual is-empty">Not monitored<\/span>/);
  assert.equal(duration(1501), '01:01:01:00');
  assert.equal(duration(0), '00:00:00:00');
  assert.equal(duration(null), '—');
  store.weather_metrics = {};
  assert.match(attributes(item), /Alarm-window actuals unavailable/);
  assert.match(attributes(item), /weather-actual is-empty">—<\/span>/);
});
