/* Local observation analysis; all scientific calculations run in Python. */
(() => {
  const $ = (id) => document.getElementById(`opt-${id}`);
  if (!$("open")) return;
  const state = { observation: null, excluded: new Set(), result: null, job: null,
    completedJob: null, busy: false, starting: false, opening: false, connected: false, dirty: false,
    configured: false, initialising: false, finding: false, listSequence: 0, observationEntries: [],
    list: "inputs", selectedInput: null, selectedAggregate: null, view: "signal", log: "" };
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const number = (value, decimals = 1) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(decimals);
  let preferences = {};
  try { preferences = JSON.parse(localStorage.getItem("dmd-opt-workspace") || "{}"); } catch (_) { /* Fresh workspace. */ }
  function remember() {
    preferences.directory = $("directory").value;
    preferences.observation = $("observation").value;
    preferences.profile = $("profile").value;
    preferences.settings = settings();
    if (state.observation) {
      preferences.exclusions ||= {};
      preferences.exclusions[`${state.observation.directory}/${state.observation.id}`] = [...state.excluded];
    }
    try { localStorage.setItem("dmd-opt-workspace", JSON.stringify(preferences)); } catch (_) { /* Storage may be unavailable. */ }
  }
  function settings() {
    const text = $("contours").value.trim();
    return { baseline: $("baseline").value, baseline_vmin: Number($("vmin").value),
      baseline_vmax: Number($("vmax").value), contour_smoothing: Number($("smoothing").value),
      contours: text ? text.split(/[,\s]+/).map(Number) : null };
  }
  function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
  function setConnected(connected) {
    const changed = state.connected !== connected;
    state.connected = connected;
    $("connection").textContent = connected ? "Local OPT connected" : "OPT Disconnected";
    $("connection").classList.toggle("is-live", connected);
    $("connection").classList.toggle("is-disconnected", !connected);
    if (changed) renderControls();
  }
  async function api(path, body, timeout = 30000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    let response;
    try {
      response = await fetch(`/api/opt/${path}`, body === undefined ? { cache: "no-store", signal: controller.signal } : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    } catch (_) {
      setConnected(false);
      throw new Error("OPT Disconnected. Check that the local OPT service is running.");
    } finally { window.clearTimeout(timer); }
    let data;
    try { data = await response.json(); } catch (_) {
      setConnected(false);
      throw new Error(`The OPT server returned HTTP ${response.status}. Start the prototype with python -m ui.opt_service.`);
    }
    setConnected(true);
    if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status})`);
    return data;
  }
  async function heartbeat() {
    try {
      await api("health", undefined, 4000);
      if (!state.configured && !state.initialising) await initialise();
    } catch (_) { setConnected(false); }
    window.setTimeout(heartbeat, 5000);
  }
  function inputsMatchResults() {
    if (!state.result || !state.observation) return false;
    return state.result.observation === state.observation.id && state.result.directory === state.observation.directory && state.result.profile === $("profile").value && JSON.stringify([...state.excluded].sort()) === JSON.stringify([...state.result.excluded].sort());
  }
  function markDirty() {
    if (state.result) state.dirty = true;
    remember(); renderControls();
  }
  function renderControls() {
    const locked = state.busy || state.opening || state.initialising;
    for (const id of ["find", "open", "directory", "observation", "profile", "include", "exclude", "baseline", "vmin", "vmax", "smoothing", "contours"]) $(id).disabled = locked || !state.connected;
    const skyIncluded = state.observation?.scans.some((s) => s.kind === "SKY" && s.status === "COMPLETE" && !state.excluded.has(s.id));
    $("find").disabled ||= state.finding;
    $("open").disabled ||= state.finding || !$("observation").value;
    $("process").disabled = window.DmdSession?.can_edit === false || locked || !state.connected || !skyIncluded;
    $("process").textContent = state.busy ? "Processing…" : state.result ? "Reprocess observation" : "Process observation";
    $("cancel").hidden = !state.busy;
    $("cancel").disabled = window.DmdSession?.can_edit === false || state.starting || !state.connected;
    $("replot").disabled = window.DmdSession?.can_edit === false || locked || !state.connected || !inputsMatchResults() || !state.dirty;
    $("replot").title = inputsMatchResults() ? "Update baseline and contour views from cached spectra" : "Process the current input selection first";
    $("stale").hidden = !state.dirty;
    $("input-count").textContent = state.observation?.scans.length || 0;
    $("aggregate-count").textContent = state.result?.aggregates.length || 0;
    const included = state.observation?.scans.filter((s) => !state.excluded.has(s.id)) || [];
    $("observation-title").textContent = state.observation?.id || "No observation loaded";
    $("summary").textContent = state.observation ? `${included.length} included · ${state.excluded.size} excluded · ${number(included.reduce((n, s) => n + s.duration_s, 0), 0)} s recorded · ${state.result?.aggregates.length || 0} completed aggregates` : "Open a recorded observation to review its scans.";
    $("export").hidden = !state.completedJob;
    if (state.completedJob) $("export").href = artifact("results.zip", true);
    renderObservationSelection();
    document.querySelectorAll("[data-opt-list]").forEach((button) => button.classList.toggle("is-active", button.dataset.optList === state.list));
    $("include").hidden = $("exclude").hidden = state.list !== "inputs";
    document.querySelector(".opt-list-actions .zk-muted").textContent = state.list === "inputs" ? "Checkboxes control processing." : "Select a completed aggregate to inspect.";
  }
  function rows() {
    const query = $("search").value.toLowerCase().trim();
    const kind = $("kind").value;
    return (state.list === "inputs" ? state.observation?.scans || [] : state.result?.aggregates || []).filter((row) => {
      if (kind === "SKY" && row.kind !== "SKY") return false;
      if (kind === "calibration" && row.kind === "SKY") return false;
      return !query || `${row.id} target ${row.target_id} frequency ${row.freq_scan} ${row.kind} ${row.digitiser}`.toLowerCase().includes(query);
    });
  }
  function renderList() {
    const items = rows();
    if (!items.length) {
      $("scan-list").innerHTML = `<div class="opt-empty-small">${state.list === "aggregates" && !state.result ? "Process the observation to browse completed aggregates." : state.observation ? "No scans match this filter." : "Open an observation to review its scans."}</div>`;
      return;
    }
    let lastGroup = null;
    $("scan-list").innerHTML = items.map((row) => {
      const group = `Target ${row.target_id} · ${row.digitiser}`;
      const header = group === lastGroup ? "" : `<div class="opt-scan-group">${esc(group)}</div>`;
      lastGroup = group;
      const input = state.list === "inputs";
      const selected = row.id === (input ? state.selectedInput : state.selectedAggregate);
      const excluded = input && state.excluded.has(row.id);
      const detail = input ? `Iteration ${row.iteration} · ${number(row.duration_s, 0)} s · ${row.status}` : `${row.iterations} iterations · ${number(row.integration_s, 0)} s · ${row.usable_channels}/${row.channels} channels`;
      return `${header}<div class="opt-scan-row ${selected ? "is-selected" : ""} ${excluded ? "is-excluded" : ""}">${input ? `<input type="checkbox" data-opt-include="${esc(row.id)}" aria-label="Include scan ${esc(row.id)}" ${excluded ? "" : "checked"} ${state.busy || row.status !== "COMPLETE" ? "disabled" : ""} />` : ""}<button type="button" class="opt-row-button" data-opt-row="${esc(row.id)}" aria-pressed="${selected}"><strong>Frequency ${row.freq_scan} <span class="opt-row-kind">${esc(row.kind)}</span></strong><small>${esc(detail)}</small><small>${number(row.frequency_mhz, 3)} MHz · l=${number(row.longitude, 2)}°</small></button></div>`;
    }).join("");
  }
  function selectedAggregate() { return state.result?.aggregates.find((row) => row.id === state.selectedAggregate); }
  function selectedInput() { return state.observation?.scans.find((row) => row.id === state.selectedInput); }
  function selectRow(id) {
    if (state.list === "aggregates") state.selectedAggregate = id;
    else {
      if (!state.result) state.view = "details";
      state.selectedInput = id;
      const input = selectedInput();
      const aggregate = state.result?.aggregates.find((row) => row.target_id === input.target_id && row.freq_scan === input.freq_scan && row.digitiser === input.digitiser && row.kind === input.kind);
      state.selectedAggregate = aggregate?.id || null;
    }
    renderList(); renderView();
  }
  function artifact(name, download = false) { return `/api/opt/jobs/${state.completedJob}/files/${encodeURIComponent(name)}${download ? "?download=1" : ""}`; }
  function renderView() {
    const aggregate = selectedAggregate();
    const source = state.list === "inputs" ? selectedInput() : aggregate;
    $("selected-title").textContent = source ? `Target ${source.target_id} · frequency ${source.freq_scan} · ${source.kind}` : state.result ? "Observation-wide analysis" : "Select a scan";
    $("selected-detail").textContent = source ? `${source.digitiser} · ${number(source.frequency_mhz, 3)} MHz · Galactic l=${number(source.longitude, 3)}°, b=${number(source.latitude, 3)}°` : "Completed aggregates are available after processing.";
    document.querySelectorAll("[data-opt-view]").forEach((button) => { const active = button.dataset.optView === state.view; button.classList.toggle("is-active", active); button.setAttribute("aria-selected", active); });
    $("overlay-label").hidden = state.view !== "velocity";
    $("details").hidden = state.view !== "details" || !source;
    $("plot").hidden = true; $("image-link").hidden = true;
    if (state.view === "details" && source) {
      $("placeholder").hidden = true;
      const labels = { id: "Identifier", scan_id: "Scan ID", target_id: "Target", freq_scan: "Frequency scan", digitiser: "Digitiser", kind: "Type", status: "Status", longitude: "Galactic longitude [deg]", latitude: "Galactic latitude [deg]", frequency_mhz: "Frequency [MHz]", duration_s: "Duration [s]", iterations: "Integrated iterations", input_scans: "Input scan records", integration_s: "Integration [s]", usable_channels: "Usable channels", channels: "Total channels" };
      $("details").innerHTML = `<table>${Object.entries(labels).filter(([key]) => source[key] !== undefined).map(([key, label]) => `<tr><th>${label}</th><td>${esc(source[key] ?? "Unavailable")}</td></tr>`).join("")}</table>`;
      return;
    }
    let name;
    if (state.view === "signal") name = aggregate?.views.signal;
    else if (state.view === "velocity" && !$("overlay").checked) name = aggregate?.views.velocity;
    else name = state.result?.views[state.view];
    if (name && state.completedJob) {
      const url = artifact(name);
      $("plot").src = url;
      $("plot").alt = `${state.view} analysis for ${aggregate?.scan_id || state.observation?.id}`;
      $("plot").hidden = false; $("placeholder").hidden = true;
      $("image-link").href = url; $("image-link").hidden = false;
    } else {
      $("placeholder").hidden = false;
      const title = state.result ? "This view is not available" : "From recorded scans to a clearer view";
      const description = state.result ? (state.view === "velocity" && aggregate?.kind !== "SKY" ? "Velocity spectra apply to SKY aggregates. Select a SKY row or show all SKY aggregates." : "Select an aggregate, or inspect the processing log for missing data or pointing information.") : "Choose an observation, review the included scans, then process the complete batch. Select any aggregate to inspect its signal.";
      $("placeholder").querySelector("h2").textContent = title;
      $("placeholder").querySelector("p").textContent = description;
    }
  }
  function duration(seconds) {
    if (seconds == null) return "—";
    const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
    const remainder = Math.round(seconds % 60 * 10) / 10;
    return [hours ? `${hours}h` : "", minutes ? `${minutes}m` : "", remainder || (!hours && !minutes) ? `${remainder}s` : ""].filter(Boolean).join(" ");
  }
  function renderObservationSelection() {
    const selected = $("observation").value;
    const entry = state.observationEntries.find((item) => item.id === selected);
    $("observation-choice").textContent = state.finding ? "Loading observations…" : entry?.name || "Choose from the list below";
    $("observation-choice").title = entry?.id || "";
    $("observation-rows").querySelectorAll("[data-opt-observation]").forEach((row) => {
      const active = row.dataset.optObservation === selected;
      row.classList.toggle("is-selected", active);
      row.querySelector("input").checked = active;
      row.querySelector("input").disabled = !state.connected || state.busy || state.opening || state.finding || row.dataset.unavailable === "true";
    });
  }
  function renderObservations() {
    const query = $("observation-search").value.toLowerCase().trim();
    const entries = state.observationEntries.filter((item) => !query || `${item.name} ${item.id} ${item.dish || ""} ${Object.keys(item.completed_types || {}).join(" ")}`.toLowerCase().includes(query));
    $("observation-count").textContent = state.finding ? "Loading…" : `${entries.length} of ${state.observationEntries.length} observations`;
    $("observation-rows").innerHTML = entries.length ? entries.map((item) => {
      const types = Object.entries(item.completed_types || {}).sort(([a], [b]) => a === "SKY" ? -1 : b === "SKY" ? 1 : a.localeCompare(b)).map(([kind, count]) => `${kind} ${count}`).join(" · ");
      return `<tr data-opt-observation="${esc(item.id)}" data-unavailable="${!!item.error}" class="${item.error ? "is-unavailable" : ""}"><td><input type="radio" name="opt-observation-selection" aria-label="Select observation ${esc(item.id)}" ${item.error ? "disabled" : ""} /></td><td title="${esc(item.name)} — ${esc(item.id)}"><strong>${esc(item.name)}</strong><small>${esc(item.error || item.id)}</small></td><td>${esc(item.dish || "—")}</td><td>${item.error || item.completed_scans == null ? "Unavailable" : `${item.completed_scans} / ${item.total_scans}`}</td><td title="${esc(types)}">${esc(types || "—")}</td><td title="${item.completed_integration_s == null ? "" : `${item.completed_integration_s} seconds, all completed scan types`}">${duration(item.completed_integration_s)}</td></tr>`;
    }).join("") : `<tr><td colspan="6" class="opt-empty-small">${state.finding ? "Reading observation metadata…" : query ? "No observations match this filter." : "No observation metadata found in this directory."}</td></tr>`;
    renderObservationSelection();
  }
  function chooseObservation(id) {
    if (!state.connected || state.busy || state.opening || state.finding) return;
    const entry = state.observationEntries.find((item) => item.id === id);
    if (!entry || entry.error) return;
    $("observation").value = id;
    preferences.observation = id;
    renderControls();
  }
  async function findObservations(preferred) {
    const sequence = ++state.listSequence;
    state.finding = true;
    state.observationEntries = [];
    $("observation").value = "";
    $("observation-browser").open = true;
    error(); renderObservations(); renderControls();
    try {
      const data = await api(`observations?directory=${encodeURIComponent($("directory").value)}`);
      if (sequence !== state.listSequence) return;
      $("directory").value = data.directory;
      state.observationEntries = data.entries || data.observations.map((id) => ({ id, name: id, error: null }));
      const selectable = state.observationEntries.filter((item) => !item.error);
      $("observation").value = selectable.find((item) => item.id === preferred)?.id || selectable[0]?.id || "";
    } catch (failure) {
      if (sequence === state.listSequence) throw failure;
    } finally {
      if (sequence === state.listSequence) { state.finding = false; renderObservations(); renderControls(); }
    }
  }
  async function openObservation() {
    if (!$("observation").value) throw new Error("Choose a directory containing observation metadata first.");
    state.opening = true; renderControls(); error();
    try {
      const observation = await api("open", { directory: $("directory").value, observation: $("observation").value });
      if (state.observation?.id !== observation.id || state.observation?.directory !== observation.directory) {
        state.result = null; state.completedJob = null; state.selectedAggregate = null; state.dirty = false;
        $("warnings").hidden = true;
      }
      state.observation = observation;
      $("observation-browser").open = false;
      const saved = preferences.exclusions?.[`${observation.directory}/${observation.id}`] || [];
      state.excluded = new Set(observation.scans.filter((s) => saved.includes(s.id) || s.status !== "COMPLETE").map((s) => s.id));
      state.selectedInput = observation.scans[0]?.id || null;
      state.list = "inputs";
      if (!state.result) state.view = "details";
      remember(); renderList(); renderView();
    } finally { state.opening = false; renderControls(); }
  }
  async function poll(jobId) {
    try {
      const data = await api(`jobs/${jobId}`);
      if (jobId !== state.job) return;
      $("progress").querySelector("progress").value = data.progress || 0;
      $("progress").querySelector("span").textContent = data.message;
      $("log-status").textContent = data.state;
      state.log = data.log || "";
      $("log-output").textContent = state.log || "Starting the processing worker…";
      if (data.state === "running") { window.setTimeout(() => poll(jobId), 1000); return; }
      state.busy = false;
      if (data.state === "ready") {
        const previousScan = selectedAggregate()?.scan_id;
        if (!state.result) state.view = "signal";
        state.result = data.result; state.completedJob = jobId; state.dirty = false;
        state.list = "aggregates";
        state.selectedAggregate = state.result.aggregates.find((row) => row.scan_id === previousScan)?.id || state.result.aggregates.find((row) => row.kind === "SKY")?.id || state.result.aggregates[0]?.id;
        const warnings = state.result.warnings || [];
        $("warnings").textContent = warnings.join(" "); $("warnings").hidden = !warnings.length;
      } else if (data.state === "failed") error(data.message);
      else $("progress").querySelector("span").textContent = "Processing cancelled. Previous completed results are retained.";
      renderControls(); renderList(); renderView();
    } catch (failure) {
      if (jobId !== state.job) return;
      if (!state.connected) {
        $("progress").querySelector("span").textContent = "OPT Disconnected — waiting to reconnect…";
        window.setTimeout(() => poll(jobId), 3000);
      } else { state.busy = false; renderControls(); error(failure.message); }
    }
  }
  async function processObservation(analysisOnly = false) {
    const analysis = settings();
    if (!Number.isFinite(analysis.baseline_vmin) || !Number.isFinite(analysis.baseline_vmax) || analysis.baseline_vmin >= analysis.baseline_vmax || !Number.isFinite(analysis.contour_smoothing) || analysis.contour_smoothing < 0) throw new Error("Check the fitting limits and contour smoothing values.");
    if (analysis.contours && analysis.contours.some((level, i, list) => !Number.isFinite(level) || level <= 0 || (i && level <= list[i - 1]))) throw new Error("Enter positive, increasing contour levels separated by commas.");
    error();
    state.busy = true; state.starting = true; renderControls(); renderList();
    let data;
    try {
      data = analysisOnly
        ? await api("reanalyse", { job: state.completedJob, settings: analysis })
        : await api("process", { directory: state.observation.directory, observation: state.observation.id,
            profile: $("profile").value, excluded: [...state.excluded], settings: analysis });
    } catch (failure) { state.busy = false; renderList(); throw failure; }
    finally { state.starting = false; renderControls(); }
    state.job = data.id; $("progress").hidden = false;
    remember(); renderControls(); renderList(); poll(data.id);
  }
  const action = (handler) => async () => { try { await handler(); } catch (failure) { error(failure.message); } };
  $("find").addEventListener("click", action(() => findObservations(preferences.observation)));
  $("open").addEventListener("click", action(openObservation));
  $("observation-search").addEventListener("input", renderObservations);
  $("observation-rows").addEventListener("click", (event) => {
    const row = event.target.closest("[data-opt-observation]");
    if (row) chooseObservation(row.dataset.optObservation);
  });
  $("observation-rows").addEventListener("change", (event) => {
    const row = event.target.closest("[data-opt-observation]");
    if (row) chooseObservation(row.dataset.optObservation);
  });
  $("process").addEventListener("click", action(() => processObservation(false)));
  $("replot").addEventListener("click", action(() => processObservation(true)));
  $("cancel").addEventListener("click", action(async () => { await api(`jobs/${state.job}/cancel`, {}); }));
  $("directory").addEventListener("change", action(() => findObservations(preferences.observation)));
  $("profile").addEventListener("change", markDirty);
  ["baseline", "vmin", "vmax", "smoothing", "contours"].forEach((id) => $(id).addEventListener("change", markDirty));
  document.querySelectorAll("[data-opt-list]").forEach((button) => button.addEventListener("click", () => { state.list = button.dataset.optList; renderControls(); renderList(); renderView(); }));
  document.querySelectorAll("[data-opt-view]").forEach((button) => button.addEventListener("click", () => { state.view = button.dataset.optView; renderView(); }));
  $("overlay").addEventListener("change", renderView);
  $("search").addEventListener("input", renderList); $("kind").addEventListener("change", renderList);
  $("scan-list").addEventListener("click", (event) => { const button = event.target.closest("[data-opt-row]"); if (button) selectRow(button.dataset.optRow); });
  $("scan-list").addEventListener("change", (event) => {
    if (!event.target.matches("[data-opt-include]") || state.busy || state.opening) return;
    const id = event.target.dataset.optInclude;
    if (event.target.checked) state.excluded.delete(id); else state.excluded.add(id);
    markDirty(); renderList();
  });
  function moveSelection(direction) {
    const items = rows(); if (!items.length) return;
    const selected = state.list === "inputs" ? state.selectedInput : state.selectedAggregate;
    const index = items.findIndex((row) => row.id === selected);
    selectRow(items[Math.max(0, Math.min(items.length - 1, index + direction))].id);
    const button = $("scan-list").querySelector(`[data-opt-row][aria-pressed="true"]`);
    button?.focus(); button?.scrollIntoView({ block: "nearest" });
  }
  $("previous").addEventListener("click", () => moveSelection(-1)); $("next").addEventListener("click", () => moveSelection(1));
  $("scan-list").addEventListener("keydown", (event) => { if (["ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); moveSelection(event.key === "ArrowUp" ? -1 : 1); } });
  $("include").addEventListener("click", () => { if (state.observation) { state.excluded = new Set(state.observation.scans.filter((s) => s.status !== "COMPLETE").map((s) => s.id)); markDirty(); renderList(); } });
  $("exclude").addEventListener("click", () => { if (state.observation) { state.excluded = new Set(state.observation.scans.map((s) => s.id)); markDirty(); renderList(); } });
  $("plot").addEventListener("error", () => error("The plot image could not be loaded. The local result session may no longer be available."));
  async function initialise() {
    if (state.initialising) return;
    state.initialising = true; renderControls();
    try {
      const config = await api("config");
      state.configured = true;
      $("directories").replaceChildren(...config.directories.map((path) => new Option(path, path)));
      $("directory").value = preferences.directory || config.roots[0];
      $("profile").replaceChildren(...config.profiles.map((profile) => new Option(profile, profile)));
      if (config.profiles.includes(preferences.profile)) $("profile").value = preferences.profile;
      else if (config.profiles.includes("default")) $("profile").value = "default";
      if (preferences.settings) {
        const saved = preferences.settings;
        $("baseline").value = saved.baseline || "linear";
        $("vmin").value = saved.baseline_vmin ?? -150; $("vmax").value = saved.baseline_vmax ?? 150;
        $("smoothing").value = saved.contour_smoothing ?? 2; $("contours").value = saved.contours?.join(", ") || "";
      }
      renderControls(); await findObservations(preferences.observation);
    } catch (failure) {
      error(state.connected ? failure.message : `${failure.message} Run PYTHONPATH=src python -m ui.opt_service --data-root ~/samples from the repository root, then open http://127.0.0.1:8765/#results.`);
    } finally { state.initialising = false; renderControls(); }
  }
  window.addEventListener?.("dmd:session", renderControls);
  if (window.location.hash === "#results") document.querySelector('[data-tab="results"]')?.click();
  initialise();
  window.setTimeout(heartbeat, 5000);
})();
