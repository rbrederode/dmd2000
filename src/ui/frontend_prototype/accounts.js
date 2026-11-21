/* Administrator-created accounts in the shared console user database. */
(function () {
  function init(doc, win) {
    if (!win.DmdAuthReady) return;
    const $ = (id) => doc.getElementById('account-' + id);
    const form = $('form');
    if (!form) return;
    const field = (name) => form.elements.namedItem(name);
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    let users = [], selected = null, busy = false;
    function show(user = null) {
      selected = user?.id ?? null;
      field('username').value = user?.username || '';
      field('username').readOnly = !!user;
      field('role').value = user?.roles[0] || 'viewer';
      field('password').value = ''; field('password').required = !user;
      field('active').checked = user?.active ?? true;
      $('title').textContent = user ? user.username : 'New user';
      $('save').textContent = user ? 'Save account' : 'Create user';
    }
    async function load() {
      const response = await win.fetch('/api/admin/users', { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || 'Could not load users');
      users = body.users;
      $('rows').innerHTML = users.map((user) => `<tr tabindex="0" data-user-id="${user.id}" aria-label="Edit ${esc(user.username)}"><td>${esc(user.username)}</td><td>${esc(user.roles.join(', '))}</td><td>${user.active ? 'Yes' : 'No'}</td></tr>`).join('');
    }
    function select(event) {
      const row = event.target.closest('[data-user-id]');
      if (row && !busy) show(users.find((user) => user.id === Number(row.dataset.userId)));
    }
    $('rows').addEventListener('click', select);
    $('rows').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(event); } });
    $('new').addEventListener('click', () => { if (!busy) show(); });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      const creating = selected == null;
      const body = { username: field('username').value, role: field('role').value, active: field('active').checked };
      if (field('password').value) body.password = field('password').value;
      busy = true; $('save').disabled = true;
      try {
        const response = await win.fetch('/api/admin/users' + (creating ? '' : '/' + selected), {
          method: creating ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || 'Could not save account');
        field('password').value = ''; show(result.user);
        await load(); $('message').textContent = 'Saved account ' + result.user.username + '.';
      } catch (error) { $('message').textContent = error.message; }
      finally { busy = false; $('save').disabled = false; }
    });
    win.DmdAuthReady.then(async (identity) => {
      $('summary').textContent = identity.username + ' — ' + identity.roles.join(', ');
      $('viewer').hidden = identity.can_manage_users;
      $('admin').hidden = !identity.can_manage_users;
      $('message').textContent = identity.can_manage_users ? 'Create, enable or disable accounts and set their permissions.' : 'You are signed in.';
      if (identity.can_manage_users) { show(); await load(); }
    }).catch((error) => { $('message').textContent = error.message; });
  }
  if (typeof document !== 'undefined') init(document, window);
})();
