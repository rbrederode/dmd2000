/* Browser-local, UTC and informational local mean sidereal clocks; no network requests. */
(function () {
  function getLocalMeanSiderealHours(milliseconds, longitude) {
    if (!Number.isFinite(milliseconds) || !Number.isFinite(longitude) || Math.abs(longitude) > 180) return null;
    // USNO's approximate GMST formula, using UTC as an approximation to UT1/TT:
    // https://aa.usno.navy.mil/faq/GAST
    const jd = milliseconds / 86400000 + 2440587.5;
    const midnight = Math.floor(jd - .5) + .5;
    const days = midnight - 2451545;
    const hours = (jd - midnight) * 24;
    const centuries = (jd - 2451545) / 36525;
    const gmst = 6.697375 + .065709824279 * days + 1.0027379 * hours + .0000258 * centuries * centuries;
    const local = gmst + longitude / 15;
    return ((local % 24) + 24) % 24;
  }

  function formatHours(hours) {
    if (hours === null || !Number.isFinite(hours)) return '—';
    const seconds = Math.floor((((hours % 24) + 24) % 24) * 3600);
    return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
      .map(value => String(value).padStart(2, '0')).join(':');
  }

  function init(doc, win) {
    const clock = doc.getElementById('observatory-lst');
    if (!clock) return null;
    const localClock = doc.getElementById('browser-local-time');
    const utcClock = doc.getElementById('browser-utc-time');
    let site = null;
    function render() {
      const now = win.Date ? win.Date.now() : Date.now();
      const date = new Date(now);
      const format = parts => parts.map(value => String(value).padStart(2, '0')).join(':');
      if (localClock) {
        localClock.textContent = 'Local ' + format([date.getHours(), date.getMinutes(), date.getSeconds()]);
        localClock.title = 'Browser local time · ' + Intl.DateTimeFormat().resolvedOptions().timeZone;
      }
      if (utcClock) {
        utcClock.textContent = 'UTC ' + format([date.getUTCHours(), date.getUTCMinutes(), date.getUTCSeconds()]);
        utcClock.title = 'Coordinated Universal Time · browser clock';
      }
      const hours = site ? getLocalMeanSiderealHours(now, site.longitude) : null;
      clock.textContent = 'LST ' + formatHours(hours);
      clock.title = site
        ? `${site.name} · longitude ${site.longitude}° (east positive). Local mean sidereal time, calculated from the browser's UTC clock; approximate (UTC used for UT1).`
        : 'Local sidereal time unavailable: waiting for the observatory longitude from TM (config/telescope.json).';
    }
    function setConfig(config) {
      site = config && typeof config.longitude === 'number' && Number.isFinite(config.longitude) && Math.abs(config.longitude) <= 180
        ? {name: config.name || 'Observatory', longitude: config.longitude} : null;
      render();
    }
    render();
    const timer = win.setInterval(render, 1000);
    doc.addEventListener('visibilitychange', render);
    win.addEventListener('pageshow', render);
    // Keep the timer for bfcache restores; navigation to a new document disposes it.
    return {setConfig, render, stop: () => win.clearInterval(timer)};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {getLocalMeanSiderealHours, formatHours, init};
  if (typeof document !== 'undefined') window.DmdSiderealClock = init(document, window);
})();
