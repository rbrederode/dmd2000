(function () {
  const tabs = Array.from(document.querySelectorAll("[data-tab]"));
  const views = Array.from(document.querySelectorAll("[data-view]"));
  const currentView = document.querySelector("[data-current-view]");
  const drawer = document.querySelector("[data-drawer]");
  const scrim = document.querySelector(".zk-scrim");
  const drawerNav = document.querySelector(".zk-drawer__nav");

  function setActive(id) {
    tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.tab === id));
    views.forEach((view) => view.classList.toggle("is-active", view.dataset.view === id));
    const active = tabs.find((tab) => tab.dataset.tab === id);
    if (currentView && active) currentView.textContent = active.textContent.trim();
    closeDrawer();
  }

  function openDrawer() {
    drawer.hidden = false;
    scrim.hidden = false;
  }

  function closeDrawer() {
    drawer.hidden = true;
    scrim.hidden = true;
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => setActive(tab.dataset.tab));
    const clone = tab.cloneNode(true);
    clone.addEventListener("click", () => setActive(tab.dataset.tab));
    drawerNav.appendChild(clone);
  });

  document.querySelectorAll("[data-jump]").forEach((button) => {
    button.addEventListener("click", () => setActive(button.dataset.jump));
  });

  document.querySelectorAll("[data-drawer-open]").forEach((button) => {
    button.addEventListener("click", openDrawer);
  });
  document.querySelectorAll("[data-drawer-close]").forEach((button) => {
    button.addEventListener("click", closeDrawer);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });

  function resolveTheme(pref) {
    if (pref !== "system") return pref;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(pref) {
    document.documentElement.setAttribute("data-theme", resolveTheme(pref));
    localStorage.setItem("dmd-theme", pref);
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.themeChoice === pref);
    });
  }

  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
  });
  applyTheme(localStorage.getItem("dmd-theme") || "system");

  document.querySelectorAll("[data-admin-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.adminTab;
      document.querySelectorAll("[data-admin-tab]").forEach((tab) => {
        tab.classList.toggle("is-active", tab.dataset.adminTab === id);
      });
      document.querySelectorAll("[data-admin-panel]").forEach((panel) => {
        panel.classList.toggle("is-active", panel.dataset.adminPanel === id);
      });
    });
  });

  const search = document.querySelector("#target-search");
  const targetRows = Array.from(document.querySelectorAll("#target-table tr"));
  if (search) {
    search.addEventListener("input", () => {
      const value = search.value.trim().toLowerCase();
      targetRows.forEach((row) => {
        row.hidden = value && !row.textContent.toLowerCase().includes(value);
      });
    });
  }

  const submit = document.querySelector("#submit-observation");
  if (submit) {
    submit.addEventListener("click", () => {
      submit.textContent = "Submitted";
      submit.classList.remove("is-primary");
      window.setTimeout(() => {
        submit.innerHTML = '<svg class="ico"><use href="#i-send" /></svg>Submit';
        submit.classList.add("is-primary");
      }, 1400);
    });
  }

  function setStatusText(key, value) {
    const el = document.querySelector(`[data-status-value="${key}"]`);
    if (el && value != null && value !== "") el.textContent = value;
  }

  function chipClass(healthClass) {
    return `zk-chip ${healthClass || "is-empty"}`;
  }

  function renderSubsystems(subsystems) {
    const body = document.querySelector("[data-status-subsystems]");
    if (!body || !Array.isArray(subsystems) || subsystems.length === 0) return;
    body.innerHTML = subsystems.map((item) => `
      <tr>
        <td>${escapeHtml(item.name || "-")}</td>
        <td><span class="${chipClass(item.health_class)}">${escapeHtml(item.health || "UNKNOWN")}</span></td>
        <td>${escapeHtml(item.state || "UNKNOWN")}</td>
        <td class="zk-mono">${escapeHtml(item.last_update || "-")}</td>
        <td>${escapeHtml(item.endpoint || "-")}</td>
      </tr>
    `).join("");
  }

  function renderStatus(data) {
    const summary = data.summary || {};
    Object.entries(summary).forEach(([group, values]) => {
      setStatusText(`${group}.state`, values && values.state);
      setStatusText(`${group}.detail`, values && values.detail);
    });
    const observation = data.observation || {};
    Object.entries(observation).forEach(([key, value]) => {
      setStatusText(`observation.${key}`, value);
    });
    renderSubsystems(data.subsystems);
  }

  async function refreshStatus() {
    try {
      const response = await fetch("data/status.json", { cache: "no-store" });
      if (!response.ok) return;
      renderStatus(await response.json());
    } catch (_) {
      // Keep the indicative static values when no UI driver has written data yet.
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  refreshStatus();
  window.setInterval(refreshStatus, 5000);
})();
