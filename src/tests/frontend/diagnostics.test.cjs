const test = require('node:test');
const assert = require('node:assert/strict');
const { init, traceDocument } = require('../../ui/frontend_prototype/diagnostics.js');
class Element {
  constructor() { this.listeners = {}; this.hidden = true; this.textContent = ''; this.innerHTML = ''; this.srcdoc = ''; this.scrollTop = 0; this.scrollHeight = 1000; this.clientHeight = 200; this.scrolls = 0; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  emit(name) { return this.listeners[name]?.(); }
  scrollIntoView() { this.scrolls++; }
}
function harness() {
  const elements = new Map(), calls = [];
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const h = { get: (id) => get('status-' + id), calls, error: null, pending: null, blobs: new Map(), revoked: [] };
  let documentId = 0;
  const objectURLs = { createObjectURL(blob) { const url = 'blob:test-' + (++documentId); h.blobs.set(url, blob); return url; }, revokeObjectURL(url) { h.blobs.delete(url); h.revoked.push(url); } };
  const fetcher = async (url) => {
    calls.push(url);
    if (url === '/api/web/profile') return { ok: true, json: async () => ({ status: 'success', payload: { profile: 'local' } }) };
    if (h.pending?.url && url.includes(h.pending.url)) return h.pending.promise;
    if (h.error) return { ok: false, json: async () => ({ status: 'error', message: h.error.message, code: h.error.code }) };
    const kind = url.includes('/trace?') ? 'trace' : 'log';
    const entity = new URL(url, 'http://localhost').searchParams.get('entity');
    return { ok: true, json: async () => ({ status: 'success', payload: { profile: 'local', file: kind + '/' + entity,
      modified_at: '2026-11-06T09:00:00Z', shared_log: kind === 'log', truncated: kind === 'log',
      content: kind === 'log' ? '<script>log contents</script>' : '<html><head></head><body><details><summary>' + entity + '</summary>trace</details></body></html>' } }) };
  };
  h.viewer = init({ getElementById: get }, { setTimeout, clearTimeout, addEventListener() {}, URL: objectURLs }, fetcher);
  return h;
}
const item = (type, id) => ({ key: type + ':' + id, type, id });

test('trace lookup uses launch entity ID even when the model ID differs', async () => {
  const h = harness();
  await h.viewer.open({ ...item('tm', 'telmgr001'), app: { arguments: { entity_id: 'tm001' } } }, 'trace');
  assert.ok(h.calls.includes('/api/diagnostics/tm/trace?entity=tm001&profile=local'));
  await h.viewer.open({ ...item('tm', 'telmgr001'), app: { arguments: { entity_id: '<undefined>' } } }, 'trace');
  assert.ok(h.calls.includes('/api/diagnostics/tm/trace?entity=%3Cundefined%3E&profile=local'));
});

test('log and trace panes load independently and refresh only on demand', async () => {
  const h = harness();
  assert.equal(h.calls.length, 0);
  await h.viewer.open(item('tm', 'tm001'), 'trace');
  const trace = h.get('trace-content').src;
  const html = await h.blobs.get(trace).text();
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /<details>/);
  await h.viewer.open(item('dig', 'dig001'), 'log');
  assert.equal(h.get('log-pane').hidden, false);
  assert.equal(h.get('trace-pane').hidden, false);
  assert.equal(h.get('trace-content').src, trace);
  assert.equal(h.get('log-content').textContent, '<script>log contents</script>');
  assert.equal(h.get('log-content').innerHTML, '');
  assert.match(h.get('log-meta').textContent, /Shared log/);
  assert.match(h.get('log-meta').textContent, /latest 512 KiB/);
  assert.match(h.get('log-download').href, /profile=local&download=1/);
  const scrolls = h.get('log-pane').scrolls;
  h.get('log-content').scrollTop = 40;
  await h.get('log-refresh').emit('click');
  assert.equal(h.get('log-content').scrollTop, 40);
  assert.equal(h.get('log-pane').scrolls, scrolls);
  h.get('log-close').emit('click');
  assert.equal(h.get('log-pane').hidden, true);
  assert.equal(h.get('trace-pane').hidden, false);
  h.get('trace-close').emit('click');
  assert.equal(h.get('trace-pane').hidden, true);
  assert.equal(h.get('trace-content').src, 'about:blank');
  assert.ok(h.revoked.includes(trace));
});

test('late file responses cannot replace a different application or reopen a closed pane', async () => {
  const h = harness();
  let resolve;
  h.pending = { url: '/tm/trace?', promise: new Promise((done) => { resolve = done; }) };
  const first = h.viewer.open(item('tm', 'tm001'), 'trace');
  await new Promise(setImmediate);
  await h.viewer.open(item('dig', 'dig003'), 'trace');
  const current = h.get('trace-content').src;
  resolve({ ok: true, json: async () => ({ status: 'success', payload: { content: 'old trace' } }) });
  await first;
  assert.equal(h.get('trace-content').src, current);
  assert.match(h.get('trace-title').textContent, /dig003/);
  let finish;
  h.pending = { url: '/tm/log?', promise: new Promise((done) => { finish = done; }) };
  const second = h.viewer.open(item('tm', 'tm001'), 'log');
  await new Promise(setImmediate);
  h.viewer.closeAll();
  finish({ ok: true, json: async () => ({ status: 'success', payload: { content: 'old log' } }) });
  await second;
  assert.equal(h.get('log-pane').hidden, true);
  assert.equal(h.get('trace-pane').hidden, true);
  assert.equal(h.get('log-content').textContent, '');
});

test('missing and oversized diagnostic files give useful messages and can recover', async () => {
  const h = harness();
  h.error = { code: 'not_found', message: 'No trace file is available' };
  await h.viewer.open(item('tm', 'tm001'), 'trace');
  assert.equal(h.get('trace-pane').hidden, false);
  assert.equal(h.get('trace-content').hidden, true);
  assert.match(h.get('trace-meta').textContent, /No trace/);
  assert.equal(h.get('trace-download').hidden, true);
  h.error = { code: 'file_too_large', message: 'Download this trace to view it' };
  await h.get('trace-refresh').emit('click');
  assert.equal(h.get('trace-download').hidden, false);
  h.error = null;
  await h.get('trace-refresh').emit('click');
  assert.equal(h.get('trace-content').hidden, false);
});

test('trace documents retain expandable markup and restrict external resources', () => {
  const source = '<html><head><style>body{color:red}</style></head><body><details>message</details></body></html>';
  const result = traceDocument(source);
  assert.match(result, /default-src 'none'/);
  assert.match(result, /connect-src 'none'/);
  assert.match(result, /form-action 'none'/);
  assert.match(result, /<details>message<\/details>/);
  assert.match(traceDocument('<details>message</details>'), /Content-Security-Policy/);
});
