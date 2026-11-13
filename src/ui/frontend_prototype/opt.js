/* Local observation analysis; all scientific calculations run in Python. */
(() => {
  const $ = (id) => document.getElementById(id === "profile" ? "dish-profile" : `opt-${id}`);
  if (!$("observation-rows")) return;
  const state = { observation: null, excluded: new Set(), result: null, job: null,
    completedJob: null, pointingRequest: null, pointingDisplay: "sky", busy: false, starting: false, opening: false, connected: false, dirty: false,
    animationTimingObservation: null, animationIntervalEdited: false, terminalRequest: null, velocitySelections: new Map(),
    savingPipeline: false, configured: false, initialising: false, finding: false, listSequence: 0, observationEntries: [],
    expandedObservations: new Set(), observationDetails: new Map(), list: "inputs", selectedInput: null, selectedAggregate: null, view: "signal", log: "" };
  window.DmdOpt = {isBusy: () => state.busy || state.opening || state.initialising || state.savingPipeline};
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const number = (value, decimals = 1) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(decimals);
  let preferences = {};
  try { preferences = JSON.parse(localStorage.getItem("dmd-opt-workspace") || "{}"); } catch (_) { /* Fresh workspace. */ }
  function remember() {
    preferences.directory = $("directory").value;
    preferences.observation = $("observation").value;
    preferences.profile = $("profile").value;
    preferences.settings = settings();
    preferences.velocityRangeVersion = 1;
    if (state.observation) {
      preferences.exclusions ||= {};
      preferences.exclusions[`${state.observation.directory}/${state.observation.id}`] = [...state.excluded];
    }
    try { localStorage.setItem("dmd-opt-workspace", JSON.stringify(preferences)); } catch (_) { /* Storage may be unavailable. */ }
  }
  function settings() {
    const text = $("contours").value.trim();
    return { baseline: $("baseline").value, baseline_vmin: String($("vmin").value).trim() ? Number($("vmin").value) : null,
      baseline_vmax: String($("vmax").value).trim() ? Number($("vmax").value) : null,
      frequency_min_mhz: $("fmin").value.trim() ? Number($("fmin").value) : null,
      frequency_max_mhz: $("fmax").value.trim() ? Number($("fmax").value) : null, contour_smoothing: Number($("smoothing").value),
      contours: text ? text.split(/[,\s]+/).map(Number) : null };
  }
  function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
  function setConnected(connected) {
    const changed = state.connected !== connected;
    state.connected = connected;
    $("connection").textContent = connected ? "OPT connected" : "OPT Disconnected";
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
      throw new Error("OPT Disconnected. Check that the OPT service is running.");
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
  let pipelines = {};
  let availableSteps = [];
  let pipelineOverview = null;
  const defaultPipelineHelp = {
    title: $("pipeline-help-title").textContent,
    overview: $("pipeline-help-description").textContent,
    details: $("pipeline-help-details").innerHTML,
  };
  function pipelineValue() {
    const text = $("pipeline-editor").value.trim();
    return text ? JSON.parse(text) : null;
  }
  const pipelineDefaults = {
    RFI_FLAG: {threshold: 5, window_size: 21},
    QA: {window_frac: .2, smooth_window: 10, noise_estimator: "median_mad"},
    BASELINE: {method: "flatten", ranges_pct: [[0, 10], [90, 100]]},
  };
  const pipelineStepNames = {DC_SPIKE: "DC spike removal", LOAD: "Load calibration", BANDPASS_FILTER: "Bandpass filter", RFI_FLAG: "RFI flagging", QA: "Quality assessment", BASELINE: "Baseline subtraction", GAIN_CAL: "Gain calibration", TSYS_CAL: "System temperature calibration"};
  const parameterLabels = {context: "Context", threshold: "Threshold (× MAD)", window_size: "Window (channels)",
    enabled: "Enabled", ranges_pct: "Ranges (%)", window_frac: "Window fraction", smooth_window: "Smoothing (channels)", noise_estimator: "Noise estimator", method: "Method"};
  function pipelineFields(key, index, step, contextOnly = false) {
    const params = {...(pipelineDefaults[step.step] || {}), ...step.params};
    params.context ||= step.step === "BASELINE" ? "cal" : "mpr";
    return [["context", params.context], ...Object.entries(params).filter(([name]) => name !== "enabled" && name !== "context")].filter(([name]) => (name === "context") === contextOnly).map(([name, value]) => {
      const attrs = `data-pipeline-group="${esc(key)}" data-pipeline-index="${index}" data-pipeline-param="${esc(name)}"`;
      const title = `${step.step}: ${name}`;
      if (name === "ranges_pct" && Array.isArray(value) && value.every(pair => Array.isArray(pair) && pair.length === 2)) {
        return `<fieldset class="opt-pipeline-ranges"><legend>${step.step === "BASELINE" ? "Baseline windows (% of usable channels)" : "Passband ranges (% of channels)"}</legend>${value.map(([low, high], range) => `<span class="opt-pipeline-range"><label>Min <input class="zk-input" type="number" min="0" max="100" step="any" value="${esc(low)}" ${attrs} data-pipeline-range="${range}" data-pipeline-bound="0" /></label><label>Max <input class="zk-input" type="number" min="0" max="100" step="any" value="${esc(high)}" ${attrs} data-pipeline-range="${range}" data-pipeline-bound="1" /></label><button type="button" class="zk-btn is-sm" ${attrs} data-pipeline-remove-range="${range}" aria-label="Remove range ${range+1} from ${esc(step.step)}" ${value.length < 2 ? "disabled" : ""}>×</button></span>`).join("")}<button class="zk-btn is-sm" type="button" ${attrs} data-pipeline-add-range>Add range</button></fieldset>`;
      }
      const choices = name === "context" ? (step.step === "BASELINE" ? ["cal"] : ["spr", "cal", "mpr"])
        : name === "method" && step.step === "BASELINE" ? ["linear", "constant", "flatten", "none"]
        : name === "noise_estimator" && step.step === "QA" ? ["median_mad", "mean_rms"] : null;
      let input;
      if (choices) input = `<select class="zk-select" ${attrs}>${choices.map(choice => `<option ${choice === value ? "selected" : ""} value="${esc(choice)}">${esc(choice)}</option>`).join("")}</select>`;
      else if (typeof value === "boolean") input = `<input type="checkbox" ${attrs} data-pipeline-type="boolean" ${value ? "checked" : ""} />`;
      else {
        const numeric = typeof value === "number";
        const limits = name === "window_size" ? 'min="1" step="2"' : name === "smooth_window" ? 'min="1" step="1"' : name === "window_frac" ? 'min="0" max="1" step="any"' : name === "threshold" ? 'min="0" step="any"' : 'step="any"';
        input = `<input class="zk-input" type="${numeric ? "number" : "text"}" ${numeric ? limits : ""} ${attrs} data-pipeline-type="${numeric ? "number" : typeof value === "string" ? "string" : "json"}" value="${esc(typeof value === "object" ? JSON.stringify(value) : value)}" />`;
      }
      if (contextOnly) return input.replace("<select ", `<select aria-label="Context for ${esc(step.step)}" `);
      return `<label class="opt-pipeline-param" title="${esc(title)}">${esc(parameterLabels[name] || name.replaceAll("_", " "))}${input}</label>`;
    }).join("");
  }
  function highlightPipelineHelp() {
    $("pipeline-summary").querySelectorAll("[data-pipeline-help-step], [data-pipeline-help-overview]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.pipelineHelpOverview !== undefined ? !state.pipelineHelpStep : button.dataset.pipelineHelpStep === state.pipelineHelpStep));
    });
  }
  function showPipelineOverview() {
    state.pipelineHelpStep = null;
    $("pipeline-step-help").scrollTop = 0;
    $("pipeline-help-title").textContent = pipelineOverview?.title || defaultPipelineHelp.title;
    $("pipeline-help-description").textContent = pipelineOverview?.overview || defaultPipelineHelp.overview;
    $("pipeline-help-details").innerHTML = pipelineOverview
      ? (pipelineOverview.stages || []).map(stage => `<h4>${esc(stage.title)}</h4><p>${esc(stage.description)}</p>`).join("")
        + '<h4>When changes take effect and how to return to SPR</h4>' + (pipelineOverview.notes || []).map(note => `<p>${esc(note)}</p>`).join("")
      : defaultPipelineHelp.details;
    highlightPipelineHelp();
  }
  function pipelineSummary() {
    state.pipelineInvalid = false;
    try {
      const pipeline = pipelineValue();
      $("pipeline-summary").innerHTML = pipeline ? Object.entries(pipeline).map(([key, steps]) => {
        const missing = availableSteps.filter(candidate => !steps.some(step => step.step === candidate.step));
        const rows = [...steps.map((step, index) => ({step, index, missing: false})).filter(({step}) => step.step !== "NOP"), ...missing.map(step => ({step, index: -1, missing: true}))];
        const contexts = ["spr", "cal", "mpr"];
        const contextOf = step => step.params?.context || availableSteps.find(candidate => candidate.step === step.step)?.params.context || "mpr";
        rows.sort((left, right) => {
          const rank = row => { const value = contexts.indexOf(contextOf(row.step)); return value < 0 ? contexts.length : value; };
          return rank(left) - rank(right) || Number(left.missing) - Number(right.missing);
        });
        return `<div class="opt-pipeline-group"><strong>${key === "default" ? "Pipeline Steps" : `Pipeline Steps · ${esc(key)}`}</strong><button type="button" class="opt-pipeline-help-button" data-pipeline-help-overview aria-label="Help for Pipeline Steps" aria-controls="opt-pipeline-step-help" aria-pressed="${!state.pipelineHelpStep}">?</button><div class="opt-pipeline-table-scroll"><table class="zk-tbl opt-pipeline-table" aria-label="Processing steps for ${esc(key)}"><thead><tr><th scope="col">Enabled</th><th scope="col">Step</th><th scope="col">Context</th><th scope="col">Parameters</th></tr></thead><tbody>${rows.map(({step, index, missing}) => {
          const active = !missing && step.params?.enabled !== false;
          const name = pipelineStepNames[step.step] || step.step.replaceAll("_", " ").toLowerCase();
          return `<tr data-pipeline-row-step="${esc(step.step)}" data-pipeline-row-context="${esc(contextOf(step))}" class="${active ? "" : "opt-pipeline-step-inactive"}" ${active ? "" : "data-pipeline-inactive"}><td><input type="checkbox" aria-label="Enable ${esc(name)}" data-pipeline-enabled="${esc(key)}" data-pipeline-index="${index}" data-pipeline-step="${esc(step.step)}" ${active ? "checked" : ""} /></td><td title="${esc(availableSteps.find(candidate => candidate.step === step.step)?.description || step.step)}"><span class="opt-pipeline-step-caption"><span>${esc(name)}</span><button type="button" class="opt-pipeline-help-button" data-pipeline-help-step="${esc(step.step)}" aria-label="Help for ${esc(name)}" aria-controls="opt-pipeline-step-help" aria-pressed="${state.pipelineHelpStep === step.step}">?</button></span>${missing ? '<small class="zk-muted">Not configured</small>' : ""}</td><td>${pipelineFields(key, index, step, true)}</td><td><div class="opt-pipeline-parameters">${pipelineFields(key, index, step) || '<span class="zk-muted">—</span>'}</div></td></tr>`;
        }).join("") || '<tr><td colspan="4">No steps</td></tr>'}</tbody></table></div></div>`;

      }).join("") : "No processing pipeline is available for the selected Config Profile.";
    } catch (_) { state.pipelineInvalid = true; $("pipeline-summary").textContent = "Pipeline JSON needs correction before processing."; }
  }
  function resetPipeline() {
    $("pipeline-save-status").textContent = "";
    const pipeline = pipelines[$("profile").value];
    $("pipeline-editor").value = pipeline ? JSON.stringify(pipeline, null, 2) : "";
    pipelineSummary();
  }
  function pipelineMatchesResults() {
    if (!state.result?.pipeline_config) return true;
    try {
      const snapshot = state.result.pipeline_config.steps_map;
      const applied = Object.fromEntries(Object.entries(snapshot).map(([key, steps]) => [key, steps.map(step => ({step: typeof step.step === "object" ? step.step.value : step.step, params: step.params}))]));
      const ordered = value => Array.isArray(value) ? value.map(ordered) : value && typeof value === "object"
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value;
      return JSON.stringify(ordered(pipelineValue())) === JSON.stringify(ordered(applied));
    } catch (_) { return false; }
  }
  function inputsMatchResults() {
    if (!state.result || !state.observation) return false;
    return pipelineMatchesResults() && (state.observation.file_id || state.observation.id) === $("observation").value && state.result.observation === state.observation.id && state.result.directory === state.observation.directory && state.result.profile === $("profile").value && JSON.stringify([...state.excluded].sort()) === JSON.stringify([...state.result.excluded].sort());
  }
  function markDirty() {
    if (state.result) state.dirty = true;
    remember(); renderControls();
  }
  const sweepFields = ["lab-lon-min", "lab-lon-max", "lab-lon-step", "lab-lat-min", "lab-lat-max", "lab-lat-step"];
  function labSelection() {
    const selection = { aggregate_id: selectedAggregate()?.id, beam: Number($("lab-beam").value) };
    if ($("lab-sweep").checked) {
      const longitude = ["min", "max", "step"].map(key => Number($("lab-lon-" + key).value));
      const latitude = ["min", "max", "step"].map(key => Number($("lab-lat-" + key).value));
      let count = 1;
      for (const [values, limit] of [[longitude, 180], [latitude, 90]]) {
        const [low, high, step] = values;
        if (values.some(value => !Number.isFinite(value)) || step <= 0 || low > high || low < -limit || high > limit) throw new Error("Check LAB sweep offsets and positive steps.");
        count *= Math.floor((high - low) / step + 1e-9) + 1;
      }
      if (count > 2500) throw new Error("LAB sweep exceeds 2500 candidates; increase the step or narrow the ranges.");
      selection.sweep = { longitude, latitude };
    }
    return selection;
  }
  function renderLabControls() {
    const eligible = state.view === "velocity" && selectedAggregate()?.kind === "SKY" && !$("overlay").checked
      && !!selectedAggregate()?.views.velocity;
    const locked = state.busy || state.opening || state.initialising || !state.connected || !inputsMatchResults() || window.DmdSession?.can_edit === false;
    $("lab-controls").hidden = !eligible;
    $("terminal-label").hidden = !eligible;
    $("terminal").disabled = locked || !eligible;
    $("lab-sweep-controls").hidden = !eligible || !$("lab").checked;
    $("lab").disabled = $("lab-beam").disabled = locked;
    $("lab-sweep").disabled = locked || !eligible || !$("lab").checked;
    for (const id of sweepFields) $(id).disabled = locked || !eligible || !$("lab").checked || !$("lab-sweep").checked;
    $("lab-find").disabled = locked || !eligible || !$("lab").checked || !$("lab-sweep").checked;
  }
  function velocitySelection() {
    return {lab_comparison: $("lab").checked ? labSelection() : null,
      terminal_velocity: $("terminal").value !== "none" ? {aggregate_id: selectedAggregate()?.id, method: $("terminal").value} : null};
  }
  function velocitySelectionKey() {
    return JSON.stringify([state.result?.directory, state.result?.observation, selectedAggregate()?.id]);
  }
  function restoreVelocitySelection() {
    const selection = state.velocitySelections.get(velocitySelectionKey());
    $("lab").checked = !!selection?.lab_comparison;
    $("terminal").value = selection?.terminal_velocity?.method || "none";
    const lab = selection?.lab_comparison;
    if (lab) {
      $("lab-beam").value = lab.beam;
      $("lab-sweep").checked = !!lab.sweep;
      if (lab.sweep) for (const [axis, field] of [["longitude", "lon"], ["latitude", "lat"]]) {
        ["min", "max", "step"].forEach((key, index) => { $("lab-" + field + "-" + key).value = lab.sweep[axis][index]; });
      }
    }
  }
  function useCachedVelocitySelection() {
    const row = selectedAggregate();
    const selection = velocitySelection();
    state.velocitySelections.set(velocitySelectionKey(), selection);
    if (!selection.lab_comparison && !selection.terminal_velocity) return true;
    if (state.dirty) return false;
    const product = Object.values(row?.velocity_products || {}).find(item =>
      (!selection.lab_comparison || JSON.stringify(item.selection.lab_comparison) === JSON.stringify(selection.lab_comparison))
      && (!selection.terminal_velocity || JSON.stringify(item.selection.terminal_velocity) === JSON.stringify(selection.terminal_velocity)));
    if (product) {
      for (const key of ["lab", "lab_terminal", "velocity_terminal"]) delete row.views[key];
      Object.assign(row.views, product.views);
      for (const key of ["terminal_velocity", "lab_comparison"]) {
        delete row[key]; if (product[key]) row[key] = product[key];
      }
      return true;
    }
    const lab = row?.lab_comparison || (state.result?.lab_comparison?.aggregate_id === row?.id ? state.result.lab_comparison : null);
    const labCached = !selection.lab_comparison || (!!row?.views.lab && lab?.beam_fwhm_deg === selection.lab_comparison.beam
      && JSON.stringify(lab.sweep_ranges || null) === JSON.stringify(selection.lab_comparison.sweep || null));
    const terminalCached = !selection.terminal_velocity || (row?.terminal_velocity?.method === selection.terminal_velocity.method
      && !!row?.views.velocity_terminal && (!selection.lab_comparison || !!row.views.lab_terminal));
    return labCached && terminalCached;
  }
  async function updateVelocitySelection() {
    if (useCachedVelocitySelection()) { renderView(); return; }
    await processObservation(true, true);
  }
  function renderAnalysisControls() {
    const locked = state.busy || state.opening || state.initialising || !state.connected;
    const velocity = state.view === "velocity" && (!state.result || $("overlay").checked || selectedAggregate()?.kind === "SKY");
    const lv = state.view === "lv";
    const frequency = state.view === "frequency" && !$("frequency-overlay").checked && selectedAggregate()?.kind === "SKY";
    const baselineBounds = velocity || lv;
    const controls = {
      baseline: [velocity || lv || frequency, "Applies to Frequency, Velocity and L–V; Total Power follows the spectral view used for Update plots."],
      vmin: [baselineBounds, "Sets Velocity and L–V limits and baseline window bounds; blank shows the full recorded range."],
      vmax: [baselineBounds, "Sets Velocity and L–V limits and baseline window bounds; blank shows the full recorded range."],
      fmin: [frequency, "Sets the selected Frequency plot minimum and baseline window bound in MHz; blank uses available channels."],
      fmax: [frequency, "Sets the selected Frequency plot maximum and baseline window bound in MHz; blank uses available channels."],
      smoothing: [lv, "Applies only to the L–V contour overlay; 0 disables smoothing."],
      contours: [lv, "Applies only to the L–V contour overlay; blank selects automatic levels."],
    };
    for (const [id, [applicable, description]] of Object.entries(controls)) {
      $(id).disabled = locked || !applicable;
      $(id).title = description + (applicable ? "" : " Select the corresponding tab to change this setting.");
    }
    $("replot").disabled = window.DmdSession?.can_edit === false || locked || state.pipelineInvalid || !(velocity || lv || frequency) || !inputsMatchResults() || !state.dirty;
    $("replot").title = !(velocity || lv || frequency) ? "Select single-aggregate Frequency, Velocity or L–V to update analysis plots"
      : inputsMatchResults() ? "Update baseline and contour views from cached spectra" : "Process the current input selection first";
  }
  function renderPointingControls() {
    if (!state.result?.views.animation) state.pointingDisplay = "sky";
    const aggregate = selectedAggregate();
    const available = !!state.result?.pointing_cache && !!aggregate;
    const locked = state.busy || state.opening || state.initialising || state.savingPipeline || !state.connected || window.DmdSession?.can_edit === false;
    $("pointing-controls").hidden = state.view !== "pointing";
    $("pointing-progress").hidden = !state.pointingRequest;
    $("pointing-render").disabled = locked || !available || !inputsMatchResults();
    $("pointing-render").hidden = !!aggregate?.views.pointing;
    const animationReady = !!state.result?.views.animation;
    const timingObservation = JSON.stringify([state.result?.directory, state.result?.observation]);
    if (state.animationTimingObservation !== timingObservation) {
      state.animationTimingObservation = timingObservation;
      state.animationIntervalEdited = false;
    }
    if (!state.animationIntervalEdited) $("animation-interval").value = state.result?.animation_interval_ms ?? state.result?.animation_default_interval_ms ?? 100;
    $("animation-interval").disabled = locked || !available;
    const interval = Number($("animation-interval").value);
    const timingChanged = animationReady && interval !== (state.result.animation_interval_ms ?? 100);
    const validInterval = Number.isInteger(interval) && interval >= 100 && interval <= 500;
    $("animation-generate").disabled = locked || !available || !inputsMatchResults() || !validInterval || (animationReady && !!state.result?.pointing_charts_complete && !timingChanged);
    $("animation-generate").textContent = state.pointingRequest?.animation
      ? "Generating Animation…"
      : timingChanged ? "Regenerate Animation" : animationReady ? state.result.pointing_charts_complete ? "Animation ready" : "Prepare sky views" : "Generate Animation";
    $("pointing-show-sky").setAttribute("aria-pressed", state.pointingDisplay === "sky");
    $("pointing-show-animation").setAttribute("aria-pressed", state.pointingDisplay === "animation");
    $("pointing-show-animation").hidden = !animationReady;
    $("pointing-show-sky").hidden = !animationReady;
    $("animation-timing").hidden = !animationReady;
    if (animationReady) {
      const actualInterval = state.result.animation_interval_ms ?? 100;
      const frames = state.result.animation_frames;
      $("animation-timing").textContent = `Saved animation: ${actualInterval} ms/frame${frames ? ` · ${frames} frames · ${(frames * actualInterval / 1000).toFixed(1)} s per loop` : ""}.`;
    }
    const summary = aggregate?.pointing_summary;
    $("pointing-summary").textContent = summary
      ? `${summary.scans} physical scans · ${summary.first_references} first pointing references · ${summary.target_midpoints} target-at-midpoint estimates · ${summary.omitted} omitted. Markers follow scan time.`
      : available ? "Markers use each physical scan's first recorded pointing and timestamp. Older tracking/drift scans may use a labelled target-at-midpoint estimate."
      : state.result ? "Reprocess this observation once to cache per-scan MPR spectra and pointing references."
      : "Process the observation to enable sky pointings and animation.";
    const name = state.result?.views.animation;
    $("animation-download").hidden = !name;
    if (name && state.completedJob) {
      $("animation-download").href = artifact(name, true);
      $("animation-download").download = name;
    }
  }
  async function generatePointing(animation = false) {
    const aggregate = selectedAggregate();
    if (!aggregate || state.busy) return;
    const interval_ms = Number($("animation-interval").value);
    if (animation && (!Number.isInteger(interval_ms) || interval_ms < 100 || interval_ms > 500)) {
      error("Frame interval must be a whole number between 100 and 500 ms."); return;
    }
    aggregate.view_errors && delete aggregate.view_errors.pointing;
    state.pointingRequest = {aggregateId: aggregate.id, animation, interval_ms};
    $("pointing-progress").querySelector("progress").value = 0;
    $("pointing-progress").querySelector("span").textContent = animation ? "Preparing animation…" : "Preparing sky view…";
    state.busy = true; state.starting = true;
    error(); renderControls();
    try {
      const data = await api("pointing", {job: state.completedJob, aggregate_id: aggregate.id, animation,
        ...(animation ? {interval_ms} : {})});
      state.job = data.id;
      $("progress").hidden = false;
      poll(data.id);
    } catch (failure) {
      aggregate.view_errors ||= {};
      aggregate.view_errors.pointing = failure.message;
      state.busy = false; state.pointingRequest = null;
      error(failure.message);
    } finally { state.starting = false; renderControls(); if (!state.busy) renderView(); }
  }
  function renderControls() {
    const locked = state.busy || state.opening || state.initialising || state.savingPipeline;
    for (const id of ["find", "directory", "observation", "include", "exclude", "baseline", "vmin", "vmax", "fmin", "fmax", "smoothing", "contours"]) $(id).disabled = locked || !state.connected;
    const skyIncluded = state.observation?.scans.some((s) => s.kind === "SKY" && s.status === "COMPLETE" && !state.excluded.has(s.id));
    $("find").disabled ||= state.finding;
    $("process").disabled = window.DmdSession?.can_edit === false || locked || !state.connected || !skyIncluded || state.pipelineInvalid || state.finding || !!window.DmdAdmin?.isBusy() || (state.configured && !pipelines[$("profile").value]) || (state.observation?.file_id || state.observation?.id) !== $("observation").value;
    $("process").textContent = state.busy ? "Processing…" : state.result ? "Reprocess observation" : "Process observation";
    $("cancel").hidden = !state.busy;
    $("cancel").disabled = window.DmdSession?.can_edit === false || state.starting || !state.connected;
    for (const id of ["pipeline-editor", "pipeline-reset"]) $(id).disabled = locked || !state.connected || window.DmdSession?.can_edit === false;
    $("pipeline-save").disabled = locked || !state.connected || state.pipelineInvalid || window.DmdSession?.can_edit === false || !pipelines[$("profile").value];
    $("pipeline-save").textContent = state.savingPipeline ? "Saving…" : "Save Pipeline Configuration";
    $("pipeline-save").title = `Save to ${$("profile").value}/PipelineConfig.json`;
    $("pipeline-summary").querySelectorAll("input, select, button").forEach(control => { if (control.dataset.pipelineHelpStep !== undefined || control.dataset.pipelineHelpOverview !== undefined) { control.disabled = false; return; } control.disabled = (control.dataset.pipelineEnabled === undefined && !!control.closest("[data-pipeline-inactive]")) || (control.dataset.pipelineRemoveRange !== undefined && control.parentElement?.parentElement?.querySelectorAll(".opt-pipeline-range").length < 2) || locked || !state.connected || window.DmdSession?.can_edit === false; });
    window.DmdAdmin?.update();
    renderAnalysisControls();
    renderPointingControls();
    $("stale").hidden = !state.dirty;
    $("input-count").textContent = state.observation?.scans.length || 0;
    $("aggregate-count").textContent = state.result?.aggregates.length || 0;
    const included = state.observation?.scans.filter((s) => !state.excluded.has(s.id)) || [];
    $("observation-title").textContent = state.observation?.id || "No observation loaded";
    $("summary").textContent = state.observation ? `${included.length} included · ${state.excluded.size} excluded · ${number(included.reduce((n, s) => n + s.duration_s, 0), 0)} s recorded · ${state.result?.aggregates.length || 0} completed aggregates` : "Open a recorded observation to review its scans.";
    renderLabControls();
    $("export").hidden = !state.completedJob;
    if (state.completedJob) $("export").href = artifact("results.zip", true);
    renderObservationSelection();
    document.querySelectorAll("[data-opt-list]").forEach((button) => button.classList.toggle("is-active", button.dataset.optList === state.list));
    $("include").hidden = $("exclude").hidden = state.list !== "inputs";
    document.querySelector(".opt-list-actions .zk-muted").textContent = state.list === "inputs" ? "Checkboxes control processing." : "Select a completed aggregate to inspect.";
  }
  function targetInfo(row) {
    if (row?.pointing || row?.target_coordinates) return row;
    return state.observation?.scans.find(input => input.target_id === row?.target_id
      && (input.pointing || input.target_coordinates)) || row || {};
  }
  function pointingLabel(row) {
    const mode = targetInfo(row).pointing;
    return mode === "NON_SIDEREAL_TRACK" ? "NON-SIDEREAL TRACK" : mode?.replaceAll("_", " ") || "";
  }
  function targetDescription(row, decimals = 3) {
    const info = targetInfo(row), coordinates = info.target_coordinates;
    let description = "";
    if (coordinates?.kind === "body") description = `Body: ${coordinates.id}`;
    else if (coordinates?.kind === "altaz") description = `Alt=${number(coordinates.alt_deg, decimals)}°, Az=${number(coordinates.az_deg, decimals)}°`;
    else if (coordinates?.kind === "equatorial") description = `RA=${number(coordinates.ra_deg, decimals)}°, Dec=${number(coordinates.dec_deg, decimals)}° · Galactic l=${number(coordinates.longitude, decimals)}°, b=${number(coordinates.latitude, decimals)}°`;
    else if (coordinates?.kind === "galactic") description = `Galactic l=${number(coordinates.longitude, decimals)}°, b=${number(coordinates.latitude, decimals)}°`;
    else if (info.longitude != null && info.latitude != null) description = `Galactic l=${number(info.longitude, decimals)}°, b=${number(info.latitude, decimals)}°`;
    if (!description) return "";
    return ["OFFSET_SCAN", "FIVE_POINT_SCAN"].includes(info.pointing) ? `Target centre · ${description}` : description;
  }
  function rows() {
    const query = $("search").value.toLowerCase().trim();
    const kind = $("kind").value;
    return (state.list === "inputs" ? state.observation?.scans || [] : state.result?.aggregates || []).filter((row) => {
      if (kind === "SKY" && row.kind !== "SKY") return false;
      if (kind === "calibration" && row.kind === "SKY") return false;
      return !query || `${row.id} target ${row.target_id} frequency ${row.freq_scan} ${row.kind} ${row.digitiser} ${pointingLabel(row)} ${targetDescription(row)}`.toLowerCase().includes(query);
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
      return `${header}<div class="opt-scan-row ${selected ? "is-selected" : ""} ${excluded ? "is-excluded" : ""}">${input ? `<input type="checkbox" data-opt-include="${esc(row.id)}" aria-label="Include scan ${esc(row.id)}" ${excluded ? "" : "checked"} ${state.busy || row.status !== "COMPLETE" ? "disabled" : ""} />` : ""}<button type="button" class="opt-row-button" data-opt-row="${esc(row.id)}" aria-pressed="${selected}"><strong>Frequency ${row.freq_scan} <span class="opt-row-kind">${esc(row.kind)}</span>${pointingLabel(row) ? ` <span class="opt-row-pointing">${esc(pointingLabel(row))}</span>` : ""}</strong><small>${esc(detail)}</small><small>${number(row.frequency_mhz, 3)} MHz</small>${targetDescription(row) ? `<small class="opt-row-target">${esc(targetDescription(row))}</small>` : ""}</button></div>`;
    }).join("");
  }
  function selectedAggregate() { return state.result?.aggregates.find((row) => row.id === state.selectedAggregate); }
  function selectedInput() { return state.observation?.scans.find((row) => row.id === state.selectedInput); }
  function selectRow(id) {
    state.pointingDisplay = "sky";
    if (state.list === "aggregates") state.selectedAggregate = id;
    else {
      if (!state.result) state.view = "details";
      state.selectedInput = id;
      const input = selectedInput();
      const aggregate = state.result?.aggregates.find((row) => row.target_id === input.target_id && row.freq_scan === input.freq_scan && row.digitiser === input.digitiser && row.kind === input.kind);
      state.selectedAggregate = aggregate?.id || null;
    }
    restoreVelocitySelection();
    if (!$("overlay").checked && selectedAggregate()?.kind === "SKY") useCachedVelocitySelection();
    renderList(); renderView();
    if (state.view === "velocity" && !$("overlay").checked && !state.busy && !state.dirty && inputsMatchResults() && !useCachedVelocitySelection()) {
      updateVelocitySelection().catch(failure => error(failure.message));
    }
  }
  function artifact(name, download = false) { return `/api/opt/jobs/${state.completedJob}/files/${encodeURIComponent(name)}${download ? "?download=1" : ""}`; }
  function renderView() {
    $("viewer").classList.toggle("is-pointing", state.view === "pointing");
    $("placeholder").querySelector(".opt-empty-steps").hidden = state.view === "pointing" && !!state.result?.pointing_cache;
    renderAnalysisControls();
    renderPointingControls();
    const aggregate = selectedAggregate();
    const source = state.list === "inputs" ? selectedInput() : aggregate;
    const showPointingDetails = state.view === "pointing" && state.pointingDisplay === "sky" && !!aggregate?.views.pointing;
    $("viewer").classList.toggle("has-pointing-details", showPointingDetails);
    $("pointing-details").hidden = !showPointingDetails;
    if (showPointingDetails) {
      $("pointing-details").innerHTML = aggregate.pointing_details
        ? aggregate.pointing_details.map(section => `<section><h3>${esc(section.title)}</h3><dl>${section.rows.map(row => `<div><dt>${esc(row.label)}</dt><dd>${esc(row.value)}</dd></div>`).join("")}</dl></section>`).join("")
        : '<p class="zk-muted">Loading recorded position details…</p>';
      if (!aggregate.pointing_details && state.result?.pointing_cache && !aggregate.view_errors?.pointing
          && !state.busy && inputsMatchResults() && window.DmdSession?.can_edit !== false) generatePointing(false);
    }
    $("selected-title").textContent = source ? `Target ${source.target_id} · Frequency ${source.freq_scan} · ${source.kind}${pointingLabel(source) ? ` · ${pointingLabel(source)}` : ""}` : state.result ? "Observation-wide analysis" : "Select a scan";
    $("selected-detail").textContent = source ? `${source.digitiser} · ${number(source.frequency_mhz, 3)} MHz${targetDescription(source) ? ` · ${targetDescription(source)}` : ""}` : "Completed aggregates are available after processing.";
    if (source?.pipeline_baseline_applied) $("selected-detail").textContent += " · Pipeline baseline subtraction applied";
    document.querySelectorAll("[data-opt-view]").forEach((button) => { const active = button.dataset.optView === state.view; button.classList.toggle("is-active", active); button.setAttribute("aria-selected", active); });
    $("overlay-label").hidden = state.view !== "velocity";
    $("frequency-overlay-label").hidden = state.view !== "frequency";
    $("timeline-overlay-label").hidden = state.view !== "timeline";
    $("timeline-resolution-label").hidden = state.view !== "timeline";
    $("timeline-mean-label").hidden = state.view !== "timeline";
    renderLabControls();

    $("details").hidden = state.view !== "details" || !source;
    $("plot").hidden = true; $("image-link").hidden = true;
    if (state.view === "details" && source) {
      $("placeholder").hidden = true;
      const labels = { id: "Identifier", scan_id: "Scan ID", target_id: "Target", freq_scan: "Frequency scan", digitiser: "Digitiser", kind: "Type", pointing: "Pointing mode", status: "Status", longitude: "Galactic longitude [deg]", latitude: "Galactic latitude [deg]", frequency_mhz: "Frequency [MHz]", duration_s: "Duration [s]", iterations: "Integrated iterations", input_scans: "Input scan records", integration_s: "Integration [s]", usable_channels: "Usable channels", channels: "Total channels" };
      $("details").innerHTML = `<table>${Object.entries(labels).filter(([key]) => source[key] !== undefined).map(([key, label]) => `<tr><th>${label}</th><td>${esc(source[key] ?? "Unavailable")}</td></tr>`).join("")}</table>`;
      return;
    }
    let name;
    if (state.view === "signal") name = aggregate?.views.signal;
    else if (state.view === "pointing") name = state.pointingDisplay === "animation" ? state.result?.views.animation : aggregate?.views.pointing;
    else if (state.view === "timeline") {
      let key = $("timeline-resolution").value === "seconds" ? "timeline_seconds" : "timeline";
      if (!$("timeline-mean").checked) key += "_no_mean";
      name = $("timeline-overlay").checked ? state.result?.views[key] : aggregate?.views[key];
    }
    else if (state.view === "frequency") name = $("frequency-overlay").checked ? state.result?.views.frequency : aggregate?.views.frequency;
    else if (state.view === "velocity" && !$("overlay").checked) {
      const measured = $("terminal").value !== "none" && aggregate?.terminal_velocity?.method === $("terminal").value;
      name = $("lab").checked && aggregate?.views.lab
        ? measured && aggregate.views.lab_terminal ? aggregate.views.lab_terminal : aggregate.views.lab
        : measured && aggregate?.views.velocity_terminal ? aggregate.views.velocity_terminal : aggregate?.views.velocity;
    }
    else name = state.result?.views[state.view];
    if (name && state.completedJob) {
      const url = artifact(name);
      $("plot").src = url;
      $("plot").alt = `${state.view} analysis for ${aggregate?.scan_id || state.observation?.id}`;
      $("plot").hidden = false; $("placeholder").hidden = true;
      $("image-link").href = url; $("image-link").hidden = false;
    } else {
      $("placeholder").hidden = false;
      let title = state.result ? "This view is not available" : "From recorded scans to a clearer view";
      const globalView = state.view === "lv" || (state.view === "velocity" && $("overlay").checked)
        || (state.view === "frequency" && $("frequency-overlay").checked)
        || (state.view === "timeline" && $("timeline-overlay").checked);
      const viewError = (globalView ? state.result?.view_errors : aggregate?.view_errors)?.[state.view];
      if (state.view === "pointing" && state.result?.pointing_cache && !viewError) title = "Generating sky view…";
      const description = viewError || (state.view === "pointing"
        ? state.result?.pointing_cache ? "Generating the selected aggregate's sky view from cached scan references…" : state.result ? "Reprocess this observation once to enable the Pointing view and animation cache." : "Process the observation to enable sky pointings and animation."
        : state.view === "frequency" && aggregate?.kind === "SKY" && !$("frequency-overlay").checked
        ? "This result has no individual Frequency image. Reprocess the observation to generate it. The all-SKY Frequency image is a separate plot."
        : state.view === "timeline" && state.result && !$("timeline-mean").checked
        ? "Update the Frequency or Velocity plots, or reprocess this observation, to generate Total Power views without the mean line."
        : state.view === "timeline" && state.result && $("timeline-resolution").value === "seconds"
        ? "Select an aggregate or reprocess this observation to generate per-second Total Power plots."
        : state.view === "timeline" && state.result && !$("timeline-overlay").checked
        ? "Select an aggregate. Reprocess older observations to generate individual Total Power plots."
        : state.result ? (["velocity", "frequency"].includes(state.view) && aggregate?.kind !== "SKY" ? "Frequency and velocity spectra apply to SKY aggregates. Select a SKY row or show all SKY aggregates." : "Select an aggregate, or inspect the processing log for missing data or pointing information.") : "Choose an observation, review the included scans, then process the complete batch. Select any aggregate to inspect its signal.");
      if (state.view === "pointing" && state.result?.pointing_cache && aggregate && !viewError
          && !state.busy && inputsMatchResults() && window.DmdSession?.can_edit !== false) generatePointing(false);
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
    const region = $("observation-rows").parentElement?.parentElement;
    const scroll = region ? [region.scrollTop, region.scrollLeft] : null;
    const scanScroll = new Map(Array.from($("observation-rows").querySelectorAll('[data-schedule-scan-scroll]'))
      .map(node => [node.dataset.scheduleScanScroll, [node.scrollTop, node.scrollLeft]]));
    const query = $("observation-search").value.toLowerCase().trim();
    const entries = state.observationEntries.filter((item) => !query || `${item.name} ${item.id} ${item.dish || ""} ${Object.keys(item.completed_types || {}).join(" ")}`.toLowerCase().includes(query));
    $("observation-count").textContent = state.finding ? "Loading…" : `${entries.length} of ${state.observationEntries.length} observations`;
    $("observation-rows").innerHTML = entries.length ? entries.map((item, index) => {
      const obs = { ...item, title: item.title || item.name, obs_id: item.id, dsh_id: item.dish,
        obs_state: item.obs_state || item.state || "IDLE", disk_completed: item.completed,
        _history: true, file_key: item.id, filename: item.id + "-obs.json", folder: "." };
      const radio = `<input type="radio" name="opt-observation-selection" data-opt-select="${esc(item.id)}" aria-label="Select observation ${esc(item.id)}" ${item.error ? "disabled" : ""} />`;
      return window.DmdSchedule.rows([obs], state.expandedObservations, 'results-' + index,
        state.observationDetails, null, { directory: $("directory").value })
        .replace('data-schedule-observation=', `data-opt-observation="${esc(item.id)}" data-unavailable="${!!item.error}" data-schedule-observation=`)
        .replace('<td><button type="button" class="schedule-toggle"', `<td class="opt-results-id">${radio}<button type="button" class="schedule-toggle"`);
    }).join("") : `<tr><td colspan="9" class="opt-empty-small">${state.finding ? "Reading observation metadata…" : query ? "No observations match this filter." : "No observation metadata found in this directory."}</td></tr>`;
    renderObservationSelection();
    for (const node of $("observation-rows").querySelectorAll('[data-schedule-scan-scroll]')) {
      const position = scanScroll.get(node.dataset.scheduleScanScroll);
      if (position) [node.scrollTop, node.scrollLeft] = position;
    }
    if (scroll) [region.scrollTop, region.scrollLeft] = scroll;
  }
  async function reviewObservation(id) {
    const existing = state.observationDetails.get(id);
    if (existing?.summary) return existing.summary;
    if (existing?.loading) return existing.promise;
    const folder = $("directory").value, sequence = state.listSequence;
    const detail = { loading: true };
    state.observationDetails.set(id, detail);
    detail.promise = (async () => {
      try {
        const summary = await api("open", { directory: folder, observation: id });
        if (sequence !== state.listSequence || folder !== $("directory").value) return null;
        detail.summary = summary;
        detail.observation = summary.metadata;
        return summary;
      } catch (failure) {
        detail.error = failure.message;
        throw failure;
      } finally {
        detail.loading = false;
        if (sequence === state.listSequence && folder === $("directory").value) renderObservations();
      }
    })();
    renderObservations();
    return detail.promise;
  }
  async function chooseObservation(id) {
    if (!state.connected || state.busy || state.opening || state.finding) return;
    const entry = state.observationEntries.find((item) => item.id === id);
    if (!entry || entry.error) return;
    $("observation").value = id;
    preferences.observation = id;
    state.expandedObservations.add(id);
    await openObservation();
  }
  async function findObservations(preferred) {
    const sequence = ++state.listSequence;
    state.finding = true;
    state.observationEntries = [];
    state.observationDetails.clear(); state.expandedObservations.clear();
    $("observation").value = "";
    $("observation-browser").open = true;
    error(); renderObservations(); renderControls();
    try {
      const data = await api(`observations?directory=${encodeURIComponent($("directory").value)}`);
      if (sequence !== state.listSequence) return;
      $("directory").value = data.directory;
      state.observationEntries = data.entries || data.observations.map((id) => ({ id, name: id, error: null }));
      const selectable = state.observationEntries.filter((item) => !item.error);
      $("observation").value = selectable.find((item) => item.id === preferred)?.id || "";
    } catch (failure) {
      if (sequence === state.listSequence) throw failure;
    } finally {
      if (sequence === state.listSequence) { state.finding = false; renderObservations(); renderControls(); }
    }
    if (sequence === state.listSequence && $("observation").value) await chooseObservation($("observation").value);
  }
  async function openObservation() {
    if (!$("observation").value) throw new Error("Choose a directory containing observation metadata first.");
    state.opening = true; renderControls(); error();
    try {
      const id = $("observation").value, folder = $("directory").value;
      const observation = await reviewObservation(id);
      if (!observation || id !== $("observation").value || folder !== $("directory").value) return;
      if (state.observation?.id !== observation.id || state.observation?.directory !== observation.directory || state.observation?.file_id !== observation.file_id) {
        state.result = null; state.completedJob = null; state.selectedAggregate = null; state.dirty = false;
        $("warnings").hidden = true;
      }
      state.observation = observation;
      $("observation-browser").open = true;
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
      if (state.pointingRequest) {
        $("pointing-progress").querySelector("progress").value = data.progress || 0;
        $("pointing-progress").querySelector("span").textContent = data.message;
      }
      $("log-status").textContent = data.state;
      state.log = data.log || "";
      $("log-output").textContent = state.log || "Starting the processing worker…";
      if (data.state === "running") { window.setTimeout(() => poll(jobId), 1000); return; }
      state.busy = false;
      if (data.state === "ready") {
        const previousScan = selectedAggregate()?.scan_id;
        if (!state.result) state.view = "signal";
        state.result = data.result; state.completedJob = jobId;
        if (state.terminalRequest && data.result.aggregates.find(row => row.id === state.terminalRequest.aggregate_id)?.terminal_velocity?.method !== state.terminalRequest.method) {
          error("The server did not return the requested terminal-velocity measurement. Check the processing log; if the server was running before this update, restart it and refresh the browser.");
        }
        if (state.pointingRequest?.animation && data.result.views.animation
            && (data.result.animation_interval_ms ?? 100) !== state.pointingRequest.interval_ms) {
          error(`The server returned an animation at ${data.result.animation_interval_ms ?? 100} ms/frame instead of ${state.pointingRequest.interval_ms} ms/frame. Restart the web server and refresh the browser, then regenerate the animation.`);
        }
        if (state.pointingRequest?.animation && data.result.views.animation) state.pointingDisplay = "animation";
        if (!state.pointingRequest) state.dirty = false;
        state.list = "aggregates";
        state.selectedAggregate = state.result.aggregates.find((row) => row.scan_id === previousScan)?.id || state.result.aggregates.find((row) => row.kind === "SKY")?.id || state.result.aggregates[0]?.id;
        if ($("lab").checked && !selectedAggregate()?.views.lab) $("lab").checked = false;
        if (!selectedAggregate()?.terminal_velocity) $("terminal").value = "none";
        const warnings = state.result.warnings || [];
        $("warnings").textContent = warnings.join(" "); $("warnings").hidden = !warnings.length;
      } else if (data.state === "failed") {
        error(data.message);
        if (state.pointingRequest) {
          const row = state.result?.aggregates.find(item => item.id === state.pointingRequest.aggregateId);
          if (row) { row.view_errors ||= {}; row.view_errors.pointing = data.message; }
        }
      }
      else $("progress").querySelector("span").textContent = "Processing cancelled. Previous completed results are retained.";
      if (data.state === "cancelled" && state.pointingRequest) {
        const row = state.result?.aggregates.find(item => item.id === state.pointingRequest.aggregateId);
        if (row) { row.view_errors ||= {}; row.view_errors.pointing = "Pointing generation cancelled. Use Generate sky view to retry."; }
      }
      state.pointingRequest = null;
      state.terminalRequest = null;
      renderControls(); renderList(); renderView();
    } catch (failure) {
      if (jobId !== state.job) return;
      if (!state.connected) {
        $("progress").querySelector("span").textContent = "OPT Disconnected — waiting to reconnect…";
        if (state.pointingRequest) $("pointing-progress").querySelector("span").textContent = "Disconnected — waiting to reconnect…";
        window.setTimeout(() => poll(jobId), 3000);
      } else {
        state.busy = false;
        state.terminalRequest = null;
        if (state.pointingRequest) {
          const row = state.result?.aggregates.find(item => item.id === state.pointingRequest.aggregateId);
          if (row) { row.view_errors ||= {}; row.view_errors.pointing = failure.message; }
          state.pointingRequest = null;
          renderView();
        }
        renderControls(); error(failure.message);
      }
    }
  }
  async function processObservation(analysisOnly = false, selectedOnly = false) {
    const analysis = settings();
    analysis.total_power_basis = ["velocity", "lv"].includes(state.view) ? "velocity"
      : state.view === "frequency" ? "frequency" : state.result?.settings?.total_power_basis || "frequency";
    if ([analysis.baseline_vmin, analysis.baseline_vmax].some(value => value !== null && !Number.isFinite(value)) ||
        (analysis.baseline_vmin !== null && analysis.baseline_vmax !== null && analysis.baseline_vmin >= analysis.baseline_vmax) ||
        !Number.isFinite(analysis.contour_smoothing) || analysis.contour_smoothing < 0) throw new Error("Check the fitting limits and contour smoothing values.");
    if (analysis.baseline !== "none" && ((analysis.baseline_vmin !== null && analysis.baseline_vmin >= 0) ||
        (analysis.baseline_vmax !== null && analysis.baseline_vmax <= 0))) throw new Error("Automatic baseline windows require velocity minimum < 0 < maximum.");
    if ([analysis.frequency_min_mhz, analysis.frequency_max_mhz].some(value => value !== null && (!Number.isFinite(value) || value <= 0)) ||
        (analysis.frequency_min_mhz !== null && analysis.frequency_max_mhz !== null && analysis.frequency_min_mhz >= analysis.frequency_max_mhz)) throw new Error("Check the positive frequency limits in MHz; minimum must be less than maximum.");
    if (analysis.contours && analysis.contours.some((level, i, list) => !Number.isFinite(level) || level <= 0 || (i && level <= list[i - 1]))) throw new Error("Enter positive, increasing contour levels separated by commas.");
    if (analysisOnly && $("lab").checked && (!Number.isFinite(Number($("lab-beam").value)) || Number($("lab-beam").value) <= 0 || Number($("lab-beam").value) > 60)) throw new Error("LAB beam FWHM must be greater than 0 and at most 60 degrees.");
    if (analysisOnly && $("lab").checked) labSelection();
    if (analysisOnly && state.view === "velocity" && !$("overlay").checked) state.velocitySelections.set(velocitySelectionKey(), velocitySelection());
    state.terminalRequest = analysisOnly && state.view === "velocity" && !$("overlay").checked
      && $("terminal").value !== "none" && selectedAggregate()?.kind === "SKY"
      ? {aggregate_id: selectedAggregate().id, method: $("terminal").value} : null;
    error();
    state.busy = true; state.starting = true; renderControls(); renderList();
    let data;
    try {
      data = analysisOnly
        ? await api("reanalyse", { job: state.completedJob, settings: analysis,
            ...(selectedOnly && !state.dirty ? {selection_only: true} : {}),
            ...($("lab").checked && !$("overlay").checked && selectedAggregate()?.kind === "SKY"
              ? { lab_comparison: labSelection() } : {}),
            ...(state.terminalRequest ? { terminal_velocity: state.terminalRequest } : {}) })
        : await api("process", { directory: state.observation.directory, observation: state.observation.file_id || state.observation.id,
            profile: $("profile").value, excluded: [...state.excluded], settings: analysis,
            ...(pipelineValue() ? {pipeline: pipelineValue()} : {}) });
    } catch (failure) { state.busy = false; state.terminalRequest = null; renderList(); throw failure; }
    finally { state.starting = false; renderControls(); }
    state.job = data.id; $("progress").hidden = false;
    remember(); renderControls(); renderList(); poll(data.id);
  }
  const action = (handler) => async (event) => { try { await handler(event); } catch (failure) { error(failure.message); } };
  $("pointing-show-sky").addEventListener("click", () => { state.pointingDisplay = "sky"; renderView(); });
  $("pointing-show-animation").addEventListener("click", () => { state.pointingDisplay = "animation"; renderView(); });
  $("pointing-render").addEventListener("click", () => generatePointing(false));
  $("animation-generate").addEventListener("click", () => generatePointing(true));
  $("animation-interval").addEventListener("input", () => { state.animationIntervalEdited = true; renderPointingControls(); });
  $("find").addEventListener("click", action(() => findObservations(preferences.observation)));
  $("observation-search").addEventListener("input", renderObservations);
  $("observation-rows").addEventListener("click", async (event) => {
    const signal = event.target.closest('[data-scan-signal]');
    if (signal) { window.DmdScheduleController.previewSignal(signal); return; }
    const download = event.target.closest('[data-scan-download]');
    if (download) { window.DmdScheduleController.downloadScan(download, false, $("download-result")); return; }
    if (event.target.closest('[data-opt-select]')) return;
    const retry = event.target.closest('[data-history-retry]');
    const row = event.target.closest('[data-opt-observation]');
    const id = retry?.dataset.historyRetry || row?.dataset.optObservation;
    if (!id || state.busy || state.finding) return;
    if (retry) { state.observationDetails.delete(id); error(); }
    if (!retry && state.expandedObservations.has(id)) state.expandedObservations.delete(id);
    else {
      state.expandedObservations.add(id);
      try { await reviewObservation(id); } catch (failure) { error(failure.message); }
    }
    renderObservations();
  });
  $("observation-rows").addEventListener("change", async (event) => {
    const selection = event.target.closest('[data-opt-select]');
    if (selection) {
      try { await chooseObservation(selection.dataset.optSelect); } catch (failure) { error(failure.message); renderControls(); }
    }
  });
  $("process").addEventListener("click", action(() => processObservation(false)));
  $("replot").addEventListener("click", action(() => processObservation(true)));
  $("cancel").addEventListener("click", action(async () => { await api(`jobs/${state.job}/cancel`, {}); }));
  $("directory").addEventListener("change", action(() => findObservations(preferences.observation)));
  // WEB owns the header selector. Apply changes only after its selection succeeds.
  window.addEventListener?.("dmd:profile", action(async (event) => {
    resetPipeline();
    $("directory").value = event.detail.scan_store_dir || state.defaultDirectory || $("directory").value;
    state.observation = null; state.selectedInput = null; state.excluded.clear();
    $("observation").value = ""; preferences.observation = "";
    if (state.configured) markDirty(); else renderControls();
    renderList();
    if (state.configured) {
      await findObservations();
      remember();
    }
  }));
  window.addEventListener?.("dmd:profile-state", renderControls);
  function validatePipelineParam(step, name, value) {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Enter a finite number for " + name);
    if (name === "ranges_pct" && (!Array.isArray(value) || !value.length || value.some(pair => !Array.isArray(pair) || pair.length !== 2 || pair.some(v => typeof v !== "number" || !Number.isFinite(v)) || pair[0] < 0 || pair[0] >= pair[1] || pair[1] > 100))) throw new Error("Each range must satisfy 0 ≤ minimum < maximum ≤ 100.");
    if (step === "RFI_FLAG" && name === "window_size" && (!Number.isInteger(value) || value < 1 || value % 2 !== 1)) throw new Error("RFI window must be a positive odd number of channels.");
    if (name === "threshold" && value <= 0) throw new Error("Threshold must be greater than zero.");
    if (name === "window_frac" && !(value > 0 && value < 1)) throw new Error("Window fraction must be between zero and one.");
    if (name === "smooth_window" && (!Number.isInteger(value) || value < 1)) throw new Error("Smoothing window must be a positive integer.");
  }
  function commitPipeline(pipeline) {
    $("pipeline-save-status").textContent = "";
    $("pipeline-editor").value = JSON.stringify(pipeline, null, 2);
    error(); pipelineSummary(); markDirty();
  }
  $("pipeline-summary").addEventListener("change", action(event => {
    const control = event.target;
    const key = control.dataset.pipelineGroup ?? control.dataset.pipelineEnabled;
    if (key === undefined) return;
    state.pipelineInvalid = true; renderControls();
    const pipeline = pipelineValue();
    let step = pipeline[key][Number(control.dataset.pipelineIndex)];
    if (!step) {
      if (!control.checked || control.dataset.pipelineEnabled === undefined) { state.pipelineInvalid = false; renderControls(); return; }
      const candidate = availableSteps.find(entry => entry.step === control.dataset.pipelineStep);
      if (!candidate) throw new Error("This pipeline step is unavailable.");
      step = {step: candidate.step, params: JSON.parse(JSON.stringify(candidate.params))};
      // Preserve configured ordering, placing new steps before later stages.
      const contexts = ["spr", "cal", "mpr"];
      const order = availableSteps.map(entry => entry.step);
      const position = pipeline[key].findIndex(existing => contexts.indexOf(existing.params?.context) > contexts.indexOf(step.params.context)
        || (existing.params?.context === step.params.context && order.indexOf(existing.step) > order.indexOf(step.step)));
      pipeline[key].splice(position < 0 ? pipeline[key].length : position, 0, step);
    }
    const params = step.params ||= {};
    const name = control.dataset.pipelineParam;
    if (!name) params.enabled = control.checked;
    else {
      let value;
      if (control.dataset.pipelineRange !== undefined) {
        if (!control.value.trim()) throw new Error("Enter a range boundary.");
        value = JSON.parse(JSON.stringify(params[name] ?? pipelineDefaults[step.step][name]));
        value[Number(control.dataset.pipelineRange)][Number(control.dataset.pipelineBound)] = Number(control.value);
      } else if (control.dataset.pipelineType === "boolean") value = control.checked;
      else if (control.dataset.pipelineType === "number") {
        if (!control.value.trim()) throw new Error("Enter a number for " + name);
        value = Number(control.value);
      } else if (control.dataset.pipelineType === "json") value = JSON.parse(control.value);
      else value = control.value;
      validatePipelineParam(step.step, name, value);
      params[name] = value;
    }
    commitPipeline(pipeline);
  }));
  $("pipeline-summary").addEventListener("click", action(event => {
    const overview = event.target.closest("[data-pipeline-help-overview]");
    if (overview?.dataset.pipelineHelpOverview !== undefined) { showPipelineOverview(); return; }
    const help = event.target.closest("[data-pipeline-help-step]");
    if (help?.dataset.pipelineHelpStep !== undefined) {
      const step = help.dataset.pipelineHelpStep;
      state.pipelineHelpStep = step;
      $("pipeline-step-help").scrollTop = 0;
      $("pipeline-help-title").textContent = pipelineStepNames[step] || step.replaceAll("_", " ").toLowerCase();
      const candidate = availableSteps.find(candidate => candidate.step === step);
      const details = candidate?.help;
      $("pipeline-help-description").textContent = details?.overview || candidate?.description || "No description has been provided for this step.";
      $("pipeline-help-details").innerHTML = details
        ? '<h4>Parameters</h4><dl>' + Object.entries(details.parameters || {}).map(([name, description]) => `<div><dt>${esc(parameterLabels[name] || name.replaceAll("_", " "))}</dt><dd>${esc(description)}</dd></div>`).join("") + '</dl>'
          + (details.notes?.length ? '<h4>Using this step</h4>' + details.notes.map(note => `<p>${esc(note)}</p>`).join("") : "")
        : "";
      highlightPipelineHelp();
      return;
    }
    const control = event.target.closest("[data-pipeline-add-range], [data-pipeline-remove-range]");
    if (!control || control.disabled) return;
    const pipeline = pipelineValue(), step = pipeline[control.dataset.pipelineGroup][Number(control.dataset.pipelineIndex)];
    const name = control.dataset.pipelineParam;
    step.params ||= {};
    const ranges = JSON.parse(JSON.stringify(step.params[name] ?? pipelineDefaults[step.step][name]));
    if (control.dataset.pipelineRemoveRange !== undefined) ranges.splice(Number(control.dataset.pipelineRemoveRange), 1);
    else ranges.push([0, 100]);
    step.params[name] = ranges;
    commitPipeline(pipeline);
  }));
  $("pipeline-editor").addEventListener("input", () => { $("pipeline-save-status").textContent = ""; pipelineSummary(); markDirty(); });
  $("pipeline-save").addEventListener("click", action(async () => {
    const profile = $("profile").value, pipeline = pipelineValue();
    state.savingPipeline = true;
    error(); $("pipeline-save-status").textContent = ""; renderControls();
    try {
      const saved = await api("pipeline", {profile, pipeline});
      pipelines[profile] = saved.pipeline;
      if ($("profile").value === profile) {
        $("pipeline-editor").value = JSON.stringify(saved.pipeline, null, 2);
        pipelineSummary();
        $("pipeline-save-status").textContent = saved.message;
      }
    } finally { state.savingPipeline = false; renderControls(); }
  }));
  $("pipeline-reset").addEventListener("click", () => { resetPipeline(); markDirty(); });
  ["baseline", "vmin", "vmax", "fmin", "fmax", "smoothing", "contours"].forEach((id) => $(id).addEventListener("change", markDirty));
  document.querySelectorAll("[data-opt-list]").forEach((button) => button.addEventListener("click", () => { state.list = button.dataset.optList; renderControls(); renderList(); renderView(); }));
  document.querySelectorAll("[data-opt-view]").forEach((button) => button.addEventListener("click", () => { state.view = button.dataset.optView; renderView(); }));
  $("timeline-overlay").addEventListener("change", renderView);
  $("timeline-resolution").addEventListener("change", renderView);
  $("timeline-mean").addEventListener("change", renderView);
  $("frequency-overlay").addEventListener("change", renderView);
  $("overlay").addEventListener("change", action(async () => {
    if ($("overlay").checked) { $("lab").checked = false; $("terminal").value = "none"; renderView(); }
    else {
      restoreVelocitySelection();
      if (!state.dirty && inputsMatchResults()) await updateVelocitySelection();
      else renderView();
    }
  }));
  $("terminal").addEventListener("change", action(updateVelocitySelection));
  $("lab").addEventListener("change", action(updateVelocitySelection));
  $("lab-sweep").addEventListener("change", action(async () => {
    renderLabControls();
    if (!$("lab-sweep").checked && $("lab").checked) await updateVelocitySelection();
  }));
  $("lab-find").addEventListener("click", action(updateVelocitySelection));
  $("lab-beam").addEventListener("change", action(async () => {
    if ($("lab").checked) await updateVelocitySelection();
  }));
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
      state.defaultDirectory = config.roots[0];
      const selection = window.DmdAdmin?.selection?.();
      $("directory").value = selection?.profile
        ? (selection.scan_store_dir || state.defaultDirectory)
        : preferences.directory || state.defaultDirectory;
      // The shared header selection is authoritative; saved Results preferences do not switch WEB.
      if (!window.DmdAdmin) {
        $("profile").replaceChildren(...config.profiles.map((profile) => new Option(profile, profile)));
        if (config.profiles.includes(preferences.profile)) $("profile").value = preferences.profile;
        else if (config.profiles.includes("default")) $("profile").value = "default";
      }
      pipelines = config.pipelines || {};
      availableSteps = (config.available_steps || []).filter(step => step.step !== "NOP");
      pipelineOverview = config.pipeline_overview || null;
      if (!state.pipelineHelpStep) showPipelineOverview();
      resetPipeline();
      if (preferences.settings) {
        const saved = preferences.settings;
        // Each page load starts with the pipeline result; extra subtraction is opt-in.
        $("baseline").value = "none";
        $("fmin").value = saved.frequency_min_mhz ?? ""; $("fmax").value = saved.frequency_max_mhz ?? "";
        $("vmin").value = preferences.velocityRangeVersion === 1 ? saved.baseline_vmin ?? "" : "";
        $("vmax").value = preferences.velocityRangeVersion === 1 ? saved.baseline_vmax ?? "" : "";
        $("smoothing").value = saved.contour_smoothing ?? 2; $("contours").value = saved.contours?.join(", ") || "";
      }
      renderControls(); await findObservations(preferences.observation);
    } catch (failure) {
      error(state.connected ? failure.message : `${failure.message} Run PYTHONPATH=src python -m ui.opt_service --data-root ~/samples from the repository root, then open http://127.0.0.1:8765/#results.`);
    } finally { state.initialising = false; renderControls(); }
  }
  function sizePipelineHelp() {
    const table = $("pipeline-summary"), help = $("pipeline-step-help");
    if (!table?.getBoundingClientRect || !help?.getBoundingClientRect) return;
    // In the stacked mobile layout the panel has no table beside it to align with.
    if (window.matchMedia?.("(max-width: 1000px)").matches) {
      help.style.removeProperty("--pipeline-help-max-height");
      return;
    }
    const height = table.getBoundingClientRect().bottom - help.getBoundingClientRect().top;
    // Allow a little more of the guide below the adjacent table before scrolling.
    if (height > 0) help.style.setProperty("--pipeline-help-max-height", `${height + 64}px`);
    else help.style.removeProperty("--pipeline-help-max-height");
  }
  if (window.ResizeObserver) {
    const observer = new window.ResizeObserver(sizePipelineHelp);
    observer.observe($("pipeline-summary"));
  }
  window.addEventListener?.("resize", sizePipelineHelp);
  $("pipeline-panel")?.addEventListener("toggle", sizePipelineHelp);
  window.addEventListener?.("dmd:session", renderControls);
  if (window.location.hash === "#results") document.querySelector('[data-tab="results"]')?.click();
  initialise();
  window.setTimeout(heartbeat, 5000);
})();
