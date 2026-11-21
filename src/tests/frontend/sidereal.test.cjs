const test = require('node:test');
const assert = require('node:assert/strict');
const {getLocalMeanSiderealHours, formatHours, init} = require('../../ui/frontend_prototype/sidereal.js');

test('local mean sidereal time agrees with independent Astropy/IAU2006 UT1 references', () => {
  // Generated with astropy Time(scale='ut1').sidereal_time('mean', model='IAU2006').
  // UTC is intentionally used as UT1 here, isolating algorithm accuracy from DUT1.
  for (const [date, longitude, expected] of [
    ['2000-01-01T12:00:00Z', 0, 18.697374828693153],
    ['2026-11-15T12:00:00Z', -2.305485, 15.485602990285777],
    ['2026-06-15T23:59:59Z', 151.2093, 3.6988951920174022],
    ['1990-01-01T00:00:00Z', -180, 18.69224190633937]
  ]) {
    const actual = getLocalMeanSiderealHours(Date.parse(date), longitude);
    assert.ok(Math.abs(actual - expected) * 3600 < .1, `${date}: ${actual}`);
  }
  assert.equal(getLocalMeanSiderealHours(Date.parse('2026-11-15T13:00:00+01:00'), -2.305485),
    getLocalMeanSiderealHours(Date.parse('2026-11-15T12:00:00Z'), -2.305485));
});

test('formatting wraps at midnight and unavailable coordinates never imply Greenwich', () => {
  assert.equal(formatHours(24), '00:00:00');
  assert.equal(formatHours(25.5), '01:30:00');
  assert.equal(formatHours(null), '—');
  for (const longitude of [null, undefined, NaN, Infinity, 181, -181, '0']) {
    assert.equal(getLocalMeanSiderealHours(0, longitude), null);
  }
});

test('header ticks locally, recovers from suspended tabs and applies new snapshot locations', () => {
  const clock = {}, localClock = {}, utcClock = {};
  const docEvents = {}, winEvents = {};
  let now = Date.parse('2026-11-15T12:00:00Z'), tick, stopped = false;
  const doc = {getElementById: id => id === 'observatory-lst' ? clock : id === 'browser-local-time' ? localClock : utcClock, addEventListener: (name, callback) => docEvents[name] = callback};
  const win = {Date: {now: () => now}, setInterval(callback, ms) {assert.equal(ms, 1000); tick = callback; return 1;},
    clearInterval() {stopped = true;}, addEventListener: (name, callback) => winEvents[name] = callback,
    fetch() {throw new Error('The clock must not fetch server data');}};
  const controller = init(doc, win);
  assert.equal(clock.textContent, 'LST —');
  assert.equal(utcClock.textContent, 'UTC 12:00:00');
  const local = new Date(now);
  assert.equal(localClock.textContent, 'Local ' + [local.getHours(),local.getMinutes(),local.getSeconds()].map(value=>String(value).padStart(2,'0')).join(':'));
  controller.setConfig({name:'Reference Observatory', longitude:-2.305485});
  assert.equal(clock.textContent, 'LST 15:29:08');
  assert.match(clock.title, /Reference Observatory.*-2.305485°.*mean sidereal/);
  now += 600000; // Timer throttling/sleep: calculate the current time, do not increment.
  tick();
  assert.equal(utcClock.textContent, 'UTC 12:10:00');
  assert.equal(clock.textContent, 'LST ' + formatHours(getLocalMeanSiderealHours(now, -2.305485)));
  now += 3600000;
  docEvents.visibilitychange();
  assert.equal(clock.textContent, 'LST ' + formatHours(getLocalMeanSiderealHours(now, -2.305485)));
  controller.setConfig({name:'East site', longitude:15});
  assert.match(clock.title, /East site/);
  assert.equal(clock.textContent, 'LST ' + formatHours(getLocalMeanSiderealHours(now, 15)));
  now += 60000; winEvents.pageshow();
  assert.equal(clock.textContent, 'LST ' + formatHours(getLocalMeanSiderealHours(now, 15)));
  controller.setConfig({longitude:null});
  assert.equal(clock.textContent, 'LST —');
  controller.stop(); assert.equal(stopped, true);
});
