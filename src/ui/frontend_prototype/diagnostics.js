/* On-demand diagnostic files; independent of the five-second status refresh. */
(function () {
  function traceDocument(content) {
    const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">`;
    // The iframe has an opaque sandbox origin. Keep trace-local expand/collapse
    // controls, while denying external resources and access to the parent UI.
    return /<head\b[^>]*>/i.test(content) ? content.replace(/<head\b[^>]*>/i, (head) => head + policy)
      : '<!doctype html><html><head>' + policy + '</head><body>' + content + '</body></html>';
  }
  function init(doc, win, fetcher) {
    const panes = {};
    for (const kind of ['log', 'trace']) {
      const $ = (name) => doc.getElementById('status-' + kind + '-' + name);
      if (!$('pane')) continue;
      let selected = null, revision = 0, controller = null, loaded = false, documentURL = null;
      function releaseDocument() {
        if (documentURL) { win.URL.revokeObjectURL(documentURL); documentURL = null; }
      }
      function cancel() { revision++; controller?.abort(); controller = null; }
      function clear() {
        loaded = false;
        if (kind === 'log') $('content').textContent = '';
        else { $('content').src = 'about:blank'; releaseDocument(); }
        $('content').hidden = true; $('download').hidden = true;
        $('meta').textContent = '';
      }
      async function json(url, signal) {
        const response = await fetcher(url, { cache: 'no-store', credentials: 'same-origin', signal });
        let body;
        try { body = await response.json(); } catch (_) { throw new Error('The file service returned an unreadable response.'); }
        if (!response.ok || body.status !== 'success' || !body.payload) throw Object.assign(new Error(body.message || 'Could not load this file'), { code: body.code });
        return body.payload;
      }
      async function load() {
        if (!selected) return;
        cancel();
        const request = revision, item = selected;
        const requestController = new AbortController(); controller = requestController;
        const timeout = win.setTimeout(() => requestController.abort(), 20000);
        const signal = requestController.signal;
        $('refresh').disabled = true; $('meta').textContent = 'Loading ' + kind + '…';
        try {
          const profile = await json('/api/web/profile', signal);
          if (request !== revision) return;
          const args = item.app?.arguments;
          const entity = args && 'entity_id' in args ? (args.entity_id ?? '') : item.id;
          const url = '/api/diagnostics/' + encodeURIComponent(item.type) + '/' + kind
            + '?entity=' + encodeURIComponent(entity) + '&profile=' + encodeURIComponent(profile.profile);
          $('download').href = url + '&download=1';
          const data = await json(url, signal);
          if (request !== revision) return;
          if (typeof data.content !== 'string') throw new Error('The file service returned invalid contents.');
          const modified = new Date(data.modified_at);
          const time = Number.isNaN(modified.getTime()) ? 'unknown' : modified.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
          $('meta').textContent = 'Config Profile: ' + data.profile + ' · ' + data.file + ' · Updated ' + time
            + (data.truncated ? ' · Showing the latest 512 KiB; download for the full log.' : '')
            + (data.shared_log ? ' · Shared log for all digitiser instances.' : '');
          $('download').hidden = false;
          if (kind === 'log') {
            const log = $('content'), top = log.scrollTop;
            const atEnd = !loaded || log.scrollHeight - log.clientHeight - top < 20;
            log.textContent = data.content; log.hidden = false;
            log.scrollTop = atEnd ? log.scrollHeight : top;
          } else {
            releaseDocument();
            documentURL = win.URL.createObjectURL(new Blob([traceDocument(data.content)], { type: 'text/html;charset=utf-8' }));
            $('content').hidden = false;
            $('content').src = documentURL;
          }
          loaded = true;
        } catch (error) {
          if (request !== revision) return;
          $('meta').textContent = error.name === 'AbortError' ? 'Loading the file timed out. Try Refresh.' : error.message;
          if (loaded) $('meta').textContent += ' Showing the previously loaded file.';
          $('download').hidden = error.code !== 'file_too_large';
        } finally {
          win.clearTimeout(timeout);
          if (request === revision) { controller = null; $('refresh').disabled = false; }
        }
      }
      function close() { cancel(); selected = null; clear(); $('pane').hidden = true; }
      function open(item) {
        cancel(); selected = item; clear();
        $('title').textContent = (kind === 'log' ? 'Log' : 'Trace') + ' — ' + item.id;
        $('pane').hidden = false;
        $('pane').scrollIntoView?.({ block: 'start', behavior: 'smooth' });
        return load();
      }
      $('refresh').addEventListener('click', load);
      $('close').addEventListener('click', close);
      win.addEventListener('pagehide', () => { cancel(); releaseDocument(); });
      panes[kind] = { open, close };
    }
    return { open: (item, kind) => panes[kind]?.open(item), closeAll: () => Object.values(panes).forEach((pane) => pane.close()) };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init, traceDocument };
  if (typeof window !== 'undefined') window.DmdDiagnostics = { init };
})();
