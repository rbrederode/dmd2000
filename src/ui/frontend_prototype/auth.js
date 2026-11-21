/* Session identity and CSRF handling for the private UI listener. */
(function () {
  function init(doc, win) {
    const original = win.fetch.bind(win);
    let redirecting = false;
    function signIn() {
      if (!redirecting) {
        redirecting = true;
        win.location.assign('/login?next=' + encodeURIComponent(win.location.pathname + win.location.search + win.location.hash));
      }
    }
    const ready = original('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' }).then(async (response) => {
      if (response.status === 401) { signIn(); throw new Error('Sign in to continue'); }
      if (!response.ok) throw new Error('Could not load your console session');
      const identity = await response.json();
      win.DmdSession = identity;
      doc.querySelectorAll('[data-suite-version]').forEach((node) => { node.textContent = identity.version || '—'; });
      doc.querySelectorAll('.zk-email').forEach((node) => { node.textContent = identity.username; });
      doc.querySelectorAll('.zk-role').forEach((node) => { node.textContent = identity.roles.join(', '); });
      doc.querySelectorAll('.zk-avatar').forEach((node) => { node.textContent = identity.username.slice(0, 2).toUpperCase(); });
      win.dispatchEvent(new CustomEvent('dmd:session', { detail: identity }));
      return identity;
    });
    win.DmdAuthReady = ready;
    // Handle the initial failure even if no view has made an API call yet.
    ready.catch(() => { doc.querySelectorAll('.zk-email').forEach((node) => { node.textContent = 'Session unavailable'; }); });
    win.fetch = async (input, options = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, win.location.href);
      if (url.origin !== win.location.origin || !url.pathname.startsWith('/api/')) return original(input, options);
      const identity = await ready;
      const headers = new Headers(options.headers || input.headers || {});
      const method = String(options.method || input.method || 'GET').toUpperCase();
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRFToken', identity.csrf_token);
      const response = await original(input, { ...options, headers, credentials: 'same-origin' });
      if (response.status === 401) signIn();
      return response;
    };
    doc.getElementById('ui-logout')?.addEventListener('click', async () => {
      try {
        const identity = await ready;
        const response = await original('/logout', { method: 'POST', credentials: 'same-origin',
          headers: { 'X-CSRFToken': identity.csrf_token, 'Content-Type': 'application/json' }, body: '{}' });
        if (response.ok || response.status === 401) win.location.assign('/login');
      } catch (_) { /* Retain the current page if logout could not be confirmed. */ }
    });
    return ready;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { init };
  if (typeof document !== 'undefined') init(document, window);
})();
