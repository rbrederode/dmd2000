const { test } = require('node:test');
const assert = require('node:assert/strict');
const { init } = require('../../ui/frontend_prototype/auth.js');
global.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
function harness() {
  const calls = [], redirects = [], handlers = {};
  let expired = false;
  const versions = [{ textContent: '' }, { textContent: '' }];
  const identity = { version: 'v1.1.0-531', username: 'ray', roles: ['admin'], csrf_token: 'session-csrf', can_edit: true };
  const doc = { querySelectorAll: (selector) => selector === '[data-suite-version]' ? versions : [], getElementById: () => ({ addEventListener: (event, fn) => { handlers[event] = fn; } }) };
  const win = { location: { href: 'http://localhost:8765/#status', origin: 'http://localhost:8765', pathname: '/', search: '', hash: '#status', assign: (url) => redirects.push(url) },
    dispatchEvent: () => {}, fetch: async (input, options) => { calls.push({ input, options }); return { ok: !expired, status: expired ? 401 : 200, json: async () => identity }; } };
  return { doc, win, calls, redirects, handlers, versions, expire: () => { expired = true; } };
}
test('session client adds CSRF only to same-origin API mutations', async () => {
  const h = harness();
  await init(h.doc, h.win);
  await h.win.fetch('/api/web/dishes', { method: 'POST', headers: { 'If-Match': 'version' }, body: '{}' });
  const write = h.calls.at(-1).options;
  assert.equal(write.headers.get('X-CSRFToken'), 'session-csrf');
  assert.equal(write.headers.get('If-Match'), 'version');
  assert.equal(write.headers.get('X-Webhook-Token'), null);
  await h.win.fetch('/api/web/dishes');
  assert.equal(h.calls.at(-1).options.headers.get('X-CSRFToken'), null);
  await h.win.fetch('https://other.test/api/data', { method: 'POST' });
  assert.equal(h.calls.at(-1).options.headers, undefined);
  assert.equal(h.calls.filter(c => c.input === '/api/auth/me').length, 1);
  assert.equal(h.win.DmdSession.username, 'ray');
  assert.deepEqual(h.versions.map(node => node.textContent), ['v1.1.0-531', 'v1.1.0-531']);
});
test('expired API sessions redirect once and logout uses POST with CSRF', async () => {
  const h = harness();
  await init(h.doc, h.win);
  await h.handlers.click();
  assert.equal(h.calls.at(-1).input, '/logout');
  assert.equal(h.calls.at(-1).options.method, 'POST');
  assert.equal(h.calls.at(-1).options.headers['X-CSRFToken'], 'session-csrf');
  assert.equal(h.redirects[0], '/login');
  h.expire();
  await h.win.fetch('/api/web/profile');
  await h.win.fetch('/api/web/profile');
  assert.equal(h.redirects.length, 2);
  assert.equal(h.redirects[1], '/login?next=%2F%23status');
});
