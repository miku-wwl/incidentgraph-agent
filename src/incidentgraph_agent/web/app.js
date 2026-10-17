"use strict";

const state = {
  catalog: null,
  selectedService: null,
  selectedIncident: null,
  currentInvestigation: null,
  currentReview: null,
  currentActionStatus: null,
  highlightedEdges: new Set(),
  toastTimer: null,
  writeBusy: false,
  actionLookupRequest: 0,
};

const byId = (id) => document.getElementById(id);
const node = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
};
const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function apiGet(path) {
  const response = await fetch(path, { method: "GET", cache: "no-store", credentials: "same-origin" });
  const payload = await response.json();
  if (!response.ok) throw Object.assign(new Error(payload.error?.message || "The local request failed."), { code: payload.error?.code });
  return payload;
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-IncidentGraph-Console": "1" },
    body: JSON.stringify({ ...body, request_id: crypto.randomUUID() }),
  });
  const payload = await response.json();
  if (!response.ok) throw Object.assign(new Error(payload.error?.message || "The local request failed."), { code: payload.error?.code });
  return payload;
}

function notify(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3400);
}

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  document.querySelectorAll(".nav-item").forEach((button) => {
    const selected = button.dataset.view === name;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function showError(container, message) {
  container.replaceChildren(node("div", "error-state", message));
}

function clearActionContext(message, { clearIds = false } = {}) {
  state.currentReview = null;
  state.currentActionStatus = null;
  byId("action-controls").replaceChildren();
  byId("action-state").className = "state-chip neutral";
  byId("action-state").textContent = "NO RECORD LOADED";
  byId("action-review").replaceChildren(node("div", "empty-state", message));
  byId("unknown-banner").classList.add("hidden");
  if (clearIds) {
    byId("run-id").value = "";
    byId("action-id").value = "";
  }
}

function addProperty(container, label, value) {
  const row = node("div", "property-row");
  row.append(node("span", "", label), node("span", "", value ?? "—"));
  container.append(row);
}

async function loadHealth() {
  try {
    const health = await apiGet("/api/health");
    const status = byId("system-status");
    const connected = health.status === "ready" && health.graph.redis_ping === "PONG";
    status.textContent = connected ? "FalkorDB connected" : "FalkorDB unavailable";
    status.className = `system-status ${connected ? "connected" : "unavailable"}`;
    byId("model-name").textContent = health.model;
  } catch (error) {
    const status = byId("system-status");
    status.textContent = `FalkorDB unavailable · ${error.message}`;
    status.className = "system-status unavailable";
  }
}

async function loadCatalog() {
  const canvas = byId("graph-canvas");
  canvas.replaceChildren(node("div", "empty-state", "Loading verified graph records…"));
  try {
    state.catalog = await apiGet("/api/catalog");
    renderMetrics();
    renderGraph();
    populateIncidents();
  } catch (error) {
    showError(canvas, error.message);
  }
}

function renderMetrics() {
  const metrics = [
    ["SERVICES", state.catalog.services.length, ""],
    ["DEPENDENCY EDGES", state.catalog.dependencies.length, "teal"],
    ["INCIDENTS", state.catalog.incidents.length, ""],
    ["INCIDENT LINKS", state.catalog.incident_associations.length, ""],
  ];
  const root = byId("graph-metrics");
  root.replaceChildren();
  metrics.forEach(([label, value, color]) => {
    const card = node("div", "metric-card");
    card.append(node("span", "", label), node("b", color, value));
    root.append(card);
  });
}

function svgEl(tag, attributes = {}) {
  const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, String(value)));
  return item;
}

function renderGraph() {
  if (!state.catalog) return;
  const root = byId("graph-canvas");
  root.replaceChildren();
  const svg = svgEl("svg", { class: "graph-svg", viewBox: "0 0 820 450", role: "img", "aria-label": "FalkorDB service and incident relationship graph" });
  const defs = svgEl("defs");
  const marker = svgEl("marker", { id: "arrow", markerWidth: "8", markerHeight: "8", refX: "7", refY: "4", orient: "auto", markerUnits: "strokeWidth" });
  marker.append(svgEl("path", { d: "M0,0 L8,4 L0,8 z", fill: "#718698" }));
  const highlightMarker = svgEl("marker", { id: "arrow-highlight", markerWidth: "8", markerHeight: "8", refX: "7", refY: "4", orient: "auto", markerUnits: "strokeWidth" });
  highlightMarker.append(svgEl("path", { d: "M0,0 L8,4 L0,8 z", fill: "#51d7c2" }));
  defs.append(marker, highlightMarker);
  svg.append(defs);

  const services = state.catalog.services;
  const incidents = state.catalog.incidents;
  const records = [
    ...services.map((record) => ({ ...record, type: "service" })),
    ...incidents.map((record) => ({ ...record, type: "incident" })),
  ];
  const positions = new Map();
  const centerX = 410;
  const centerY = 224;
  const radiusX = Math.min(320, 120 + records.length * 21);
  const radiusY = Math.min(155, 58 + records.length * 10);
  records.forEach((record, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(records.length, 1);
    positions.set(record.id, { x: centerX + Math.cos(angle) * radiusX, y: centerY + Math.sin(angle) * radiusY });
  });

  [...state.catalog.dependencies, ...state.catalog.incident_associations].forEach((edge) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return;
    const key = `${edge.source}|${edge.target}`;
    const line = svgEl("line", { x1: source.x, y1: source.y, x2: target.x, y2: target.y, class: `graph-edge${edge.type === "INVOLVES" ? " incident" : ""}${state.highlightedEdges.has(key) ? " highlight" : ""}` });
    if (edge.type === "INVOLVES") line.setAttribute("stroke-dasharray", "4 5");
    svg.append(line);
  });

  records.forEach((record) => {
    const point = positions.get(record.id);
    const selected = record.id === state.selectedService || record.id === state.selectedIncident;
    const group = svgEl("g", { class: `graph-node ${record.type === "incident" ? "incident-node" : ""}${selected ? " selected" : ""}`, tabindex: "0", role: "button", "aria-label": `${record.type} ${record.id}` });
    if (record.type === "incident") {
      group.append(svgEl("rect", { x: point.x - 63, y: point.y - 20, width: 126, height: 40, rx: 7 }));
    } else {
      group.append(svgEl("circle", { cx: point.x, cy: point.y, r: 27 }));
    }
    const title = svgEl("text", { x: point.x, y: point.y - 1 });
    title.textContent = record.id;
    const kind = svgEl("text", { x: point.x, y: point.y + 12, class: "node-type" });
    kind.textContent = record.type === "incident" ? "INCIDENT" : record.criticality.toUpperCase();
    group.append(title, kind);
    const select = () => selectRecord(record);
    group.addEventListener("click", select);
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); } });
    svg.append(group);
  });
  root.append(svg);
}

function selectRecord(record) {
  state.highlightedEdges.clear();
  const properties = byId("selected-properties");
  properties.replaceChildren();
  if (record.type === "incident") {
    state.selectedIncident = record.id;
    state.selectedService = null;
    byId("selected-title").textContent = record.id;
    byId("selected-description").textContent = "Incident record observed in FalkorDB. Associated services are shown as incident relationships.";
    addProperty(properties, "TYPE", "Incident");
    addProperty(properties, "STATUS", record.status);
    addProperty(properties, "STARTED", record.started_at);
    addProperty(properties, "SUMMARY", record.summary);
    byId("dependencies-button").disabled = true;
    byId("blast-button").disabled = true;
    byId("incident-select").value = record.id;
  } else {
    state.selectedService = record.id;
    state.selectedIncident = null;
    byId("selected-title").textContent = record.id;
    byId("selected-description").textContent = "Service record observed in FalkorDB. DEPENDS_ON arrows point from the requiring service to its dependency.";
    addProperty(properties, "TYPE", "Service");
    addProperty(properties, "NAME", record.name);
    addProperty(properties, "ENVIRONMENT", record.environment);
    addProperty(properties, "CRITICALITY", record.criticality);
    byId("dependencies-button").disabled = false;
    byId("blast-button").disabled = false;
  }
  byId("graph-paths").replaceChildren(node("span", "muted", "Choose a graph query to show verified paths."));
  renderGraph();
}

function populateIncidents() {
  const select = byId("incident-select");
  select.replaceChildren(node("option", "", "Select an incident"));
  state.catalog.incidents.forEach((incident) => {
    const option = node("option", "", `${incident.id} · ${incident.status}`);
    option.value = incident.id;
    select.append(option);
  });
  byId("investigate-button").disabled = state.catalog.incidents.length === 0;
}

function renderPaths(container, paths, label) {
  container.replaceChildren();
  if (!paths?.length) {
    container.append(node("span", "muted", "No verified paths returned."));
    return;
  }
  paths.forEach((path, index) => {
    const item = node("div", "path-item");
    item.append(node("b", "", `${label} ${index + 1} · DEPTH ${path.depth}`));
    item.append(node("span", "", (path.service_ids || []).join("  →  ")));
    container.append(item);
    const ids = path.service_ids || [];
    for (let part = 0; part < ids.length - 1; part += 1) state.highlightedEdges.add(`${ids[part]}|${ids[part + 1]}`);
  });
  renderGraph();
}

async function runGraphQuery(kind) {
  if (!state.selectedService) return;
  const box = byId("graph-paths");
  state.highlightedEdges.clear();
  box.replaceChildren(node("span", "muted", "Querying FalkorDB…"));
  try {
    const payload = await apiGet(`/api/${kind}?service_id=${encodeURIComponent(state.selectedService)}`);
    const paths = kind === "dependencies" ? payload.paths : payload.affected_services.flatMap((service) => service.paths);
    renderPaths(box, paths, kind === "dependencies" ? "DEPENDENCY PATH" : "BLAST RADIUS PATH");
    if (kind === "blast-radius") notify(`${payload.total_distinct_affected_services} affected services verified from graph paths.`);
  } catch (error) { showError(box, error.message); }
}

function renderInvestigation(result) {
  const root = byId("investigation-result");
  root.replaceChildren();
  if (result.status !== "complete" || result.task_completed !== true || result.evidence_complete !== true || result.raw_model_prose_used !== false) {
    byId("investigation-state").className = "state-chip danger";
    byId("investigation-state").textContent = result.status === "incomplete" ? "INCOMPLETE" : "FAILED";
    const message = result.error?.message || "The graph investigation did not produce complete trusted evidence.";
    root.append(node("div", "error-state", message));
    byId("propose-button").disabled = true;
    state.currentInvestigation = null;
    return;
  }
  state.currentInvestigation = result;
  byId("investigation-state").className = "state-chip success";
  byId("investigation-state").textContent = "EVIDENCE COMPLETE";
  const summary = node("div", "evidence-block full-span");
  summary.append(node("h3", "", `DETERMINISTIC SUMMARY · ${result.model}`));
  summary.append(node("p", "", result.summary));
  const grid = node("div", "evidence-grid");
  grid.append(summary);
  const observed = node("div", "evidence-block");
  observed.append(node("h3", "", "OBSERVED INCIDENT"));
  observed.append(node("p", "", `${result.incident.id} · ${result.incident.status}\n${result.incident.summary}`));
  grid.append(observed);
  const impact = node("div", "evidence-block");
  impact.append(node("h3", "", "POTENTIAL IMPACT · GRAPH PATHS"));
  (result.potentially_impacted_services || []).forEach((service) => {
    const row = node("div", "evidence-row");
    row.append(node("b", "", service.service_id));
    row.append(node("span", "", ` · minimum depth ${service.minimum_depth}`));
    impact.append(row);
    (service.evidence_paths || []).forEach((path) => {
      const sub = node("div", "evidence-row", (path.service_ids || []).join(" → "));
      impact.append(sub);
    });
  });
  grid.append(impact);
  const timeline = node("div", "evidence-block");
  timeline.append(node("h3", "", "RELATED DEPLOYMENTS & ALERTS"));
  [...(result.related_deployments || []).map((item) => ({ ...item, kind: "DEPLOYMENT" })), ...(result.related_alerts || []).map((item) => ({ ...item, kind: "ALERT" }))].forEach((item) => {
    const row = node("div", "evidence-row");
    row.append(node("b", "", `${item.kind} · ${item.id}`));
    row.append(node("span", "", ` · ${item.service_id} · ${item.deployed_at || item.timestamp}`));
    timeline.append(row);
  });
  grid.append(timeline);
  const causality = node("div", "evidence-block");
  causality.append(node("h3", "", "CAUSALITY STATUS"));
  causality.append(node("p", "", `UNPROVEN · ${result.causality.note}`));
  grid.append(causality);
  const trace = node("div", "evidence-block full-span");
  trace.append(node("h3", "", "READ-ONLY TOOL TRACE & EVIDENCE REFERENCES"));
  trace.append(node("p", "", `${result.tool_trace.tool}(${JSON.stringify(result.tool_trace.arguments)}) · ${result.tool_trace.status}`));
  const refs = node("div", "ref-list");
  result.evidence_refs.forEach((ref) => refs.append(node("span", "ref-tag", ref)));
  trace.append(refs);
  grid.append(trace);
  const digest = node("div", "evidence-block full-span");
  digest.append(node("h3", "", "SNAPSHOT DIGEST · OBSERVED AT"));
  digest.append(node("p", "", `${result.evidence_digest} · ${result.observed_at}`));
  grid.append(digest);
  root.append(grid);
  byId("propose-button").disabled = false;
}

async function watchJob(jobId, target, onComplete, operation) {
  target.classList.add("running");
  target.textContent = `${operation} is running. Keep this page open; an interrupted request may need status inspection.`;
  while (true) {
    await delay(850);
    let job;
    try { job = await apiGet(`/api/jobs/${encodeURIComponent(jobId)}`); }
    catch (error) {
      target.classList.remove("running");
      if (operation === "Execution") {
        target.textContent = `Execution acknowledgement is unavailable. Outcome may be ambiguous for Action ${byId("action-id").value}. Inspect its persisted status and receipt; do not retry.`;
      } else target.textContent = error.message;
      return;
    }
    if (job.state === "queued" || job.state === "running") continue;
    target.classList.remove("running");
    if (job.state === "failed") {
      const text = `${job.error?.code || "failed"}: ${job.error?.message || "The local operation failed."}`;
      target.textContent = operation === "Execution" ? `${text} If dispatch may have started, inspect the Action status and receipt; do not retry.` : text;
      return;
    }
    target.textContent = `${operation} finished. Inspect the result below before choosing another stage.`;
    onComplete(job.result || {});
    return;
  }
}

async function startOperation(path, body, target, operation, onComplete) {
  if (state.writeBusy) return;
  state.writeBusy = true;
  target.classList.remove("running");
  target.textContent = "Submitting one explicit local operation…";
  try {
    const response = await apiPost(path, body);
    if (response.duplicate) notify("Duplicate request returned its existing job; no second operation was queued.");
    await watchJob(response.job.job_id, target, onComplete, operation);
  } catch (error) {
    target.textContent = error.message;
    if (operation === "Execution") notify("Execution acknowledgement failed; inspect persisted Action state and receipt. Do not retry blindly.");
    if (operation === "Plan proposal") byId("propose-button").disabled = false;
  } finally {
    state.writeBusy = false;
  }
}

function showReview(review) {
  state.currentReview = review;
  state.currentActionStatus = null;
  byId("action-state").className = `state-chip ${review.status === "AwaitingApproval" ? "warning" : review.status === "Approved" ? "success" : review.status === "Rejected" ? "danger" : "neutral"}`;
  byId("action-state").textContent = review.status.toUpperCase();
  const root = byId("action-review");
  root.replaceChildren();
  const snapshotFreshness = review.snapshot_fresh === true ? "FRESH" : review.snapshot_fresh === false ? "STALE" : "UNKNOWN";
  const snapshotAge = Number.isFinite(review.snapshot_age_seconds) ? `${review.snapshot_age_seconds}s` : "age unavailable";
  const facts = node("div", "review-grid");
  [["RUN ID", review.run_id], ["INCIDENT", review.incident_id], ["PLAN DIGEST", review.plan_digest], ["EXACT TARGET", review.target], ["EVIDENCE DIGEST", review.evidence_digest], ["RISK", review.risk_class], ["SNAPSHOT", `${snapshotFreshness} · ${snapshotAge}`], ["CAUSALITY", review.causality_status || "unproven"]].forEach(([label, value]) => {
    const card = node("div", "review-card");
    card.append(node("span", "", label), node("b", "", value ?? "—"));
    facts.append(card);
  });
  root.append(facts);
  const plan = node("div", "review-plan");
  plan.append(node("div", "eyebrow", "COMPLETE RUNTIME PLAN"));
  plan.append(node("pre", "", JSON.stringify(review.plan, null, 2)));
  root.append(plan);
  const paths = node("div", "review-paths");
  (review.verified_dependency_paths || []).forEach((path, index) => {
    paths.append(node("div", "path-item", `VERIFIED PATH ${index + 1} · ${(path.service_ids || []).join(" → ")}`));
  });
  if (paths.childElementCount) root.append(paths);
  byId("run-id").value = review.run_id;
  renderActionControls();
}

function showActionStatus(payload) {
  state.currentActionStatus = payload;
  state.currentReview = null;
  const action = payload.action || {};
  const status = payload.status || action.state || "UNKNOWN";
  byId("action-state").className = `state-chip ${status === "Succeeded" ? "success" : status === "Unknown" ? "danger" : status === "Failed" || status === "Rejected" ? "danger" : "warning"}`;
  byId("action-state").textContent = String(status).toUpperCase();
  byId("action-id").value = action.action_id || byId("action-id").value;
  byId("audit-action-id").value = action.action_id || byId("audit-action-id").value;
  const root = byId("action-review");
  root.replaceChildren();
  const facts = node("div", "review-grid");
  [["ACTION ID", action.action_id], ["STATE", status], ["EVIDENCE DIGEST", payload.evidence_digest], ["RECEIPT VERIFIED", payload.receipt_verified], ["EFFECT OBSERVED", payload.effect_observed], ["SNAPSHOT FRESH", payload.snapshot_fresh]].forEach(([label, value]) => {
    const card = node("div", "review-card");
    card.append(node("span", "", label), node("b", "", value ?? "—"));
    facts.append(card);
  });
  root.append(facts);
  const detail = node("div", "review-plan");
  detail.append(node("div", "eyebrow", "PERSISTED ACTION SNAPSHOT"));
  detail.append(node("pre", "", JSON.stringify(action, null, 2)));
  root.append(detail);
  byId("unknown-banner").classList.toggle("hidden", status !== "Unknown");
  renderActionControls();
}

function renderActionControls() {
  const root = byId("action-controls");
  root.replaceChildren();
  const review = state.currentReview;
  const status = state.currentActionStatus?.status || review?.status;
  if (status === "Unknown") {
    root.append(node("div", "unknown-inline", "Outcome uncertain. Inspect persisted status and receipt. Do not retry this Action."));
    return;
  }
  if (status === "Succeeded" || status === "Failed" || status === "Rejected") return;
  if (status === "AwaitingApproval" && review) {
    const box = confirmationBox("APPROVE PLAN", `APPROVE ${review.plan_digest}`, "Approve Plan", async (value) => {
      if (value !== `APPROVE ${review.plan_digest}`) return notify("The phrase must exactly match the reviewed Plan digest.");
      const id = byId("run-id").value.trim();
      await startOperation("/api/actions/decision", { run_id: id, decision: "approve", confirmation: value }, byId("action-job"), "Plan approval", (result) => { state.currentReview = { ...review, status: result.status }; byId("action-state").textContent = String(result.status || "").toUpperCase(); byId("action-state").className = "state-chip success"; renderActionControls(); });
    });
    root.append(box);
    const reject = node("button", "button danger", "Reject Plan");
    reject.type = "button";
    reject.addEventListener("click", () => {
      const input = root.querySelector("input");
      if (input.value !== `REJECT ${review.plan_digest}`) return notify(`Type exactly: REJECT ${review.plan_digest}`);
      startOperation("/api/actions/decision", { run_id: byId("run-id").value.trim(), decision: "reject", confirmation: input.value }, byId("action-job"), "Plan rejection", (result) => { state.currentReview = { ...review, status: result.status }; byId("action-state").textContent = String(result.status || "").toUpperCase(); renderActionControls(); });
      input.value = "";
    });
    root.append(reject);
    const phrase = root.querySelector(".phrase");
    phrase.textContent = `APPROVE ${review.plan_digest}  ·  or type REJECT ${review.plan_digest}`;
    return;
  }
  if (status === "Approved" && review) {
    const button = node("button", "button primary", "Admit approved Run as Action");
    button.type = "button";
    button.addEventListener("click", () => startOperation("/api/actions/admit", { run_id: byId("run-id").value.trim() }, byId("action-job"), "Admission", (result) => {
      if (result.action?.action_id) { byId("action-id").value = result.action.action_id; byId("audit-action-id").value = result.action.action_id; }
      byId("action-state").textContent = String(result.status || "").toUpperCase();
      state.currentReview = null;
      state.currentActionStatus = { status: result.status, action: result.action };
      renderActionControls();
    }));
    root.append(button);
    return;
  }
  const current = state.currentActionStatus;
  const actionId = current?.action?.action_id || byId("action-id").value.trim();
  if (status === "Pending" && actionId) {
    root.append(confirmationBox("SEPARATE EXECUTION AUTHORIZATION", `AUTHORIZE ${actionId}`, "Authorize Action", async (value) => {
      if (value !== `AUTHORIZE ${actionId}`) return notify("The phrase must exactly match this Action ID.");
      await startOperation("/api/actions/authorize", { action_id: actionId, confirmation: value }, byId("action-job"), "Authorization", (result) => { byId("action-state").textContent = String(result.status || "").toUpperCase(); state.currentActionStatus = { ...current, status: result.status, action: result.action }; renderActionControls(); });
    }));
    return;
  }
  if (status === "Authorized" && actionId) {
    root.append(confirmationBox("SEPARATE EXECUTION CONFIRMATION", `EXECUTE ${actionId}`, "Execute Action", async (value) => {
      if (value !== `EXECUTE ${actionId}`) return notify("The phrase must exactly match this Action ID.");
      await startOperation("/api/actions/execute", { action_id: actionId, confirmation: value }, byId("action-job"), "Execution", (result) => {
        byId("action-state").textContent = String(result.status || "").toUpperCase();
        state.currentActionStatus = { status: result.status, action: result.action };
        renderActionControls();
        if (result.status === "Unknown") byId("unknown-banner").classList.remove("hidden");
      });
    }));
  }
}

function confirmationBox(title, phraseText, buttonLabel, onConfirm) {
  const box = node("div", "confirmation-box");
  box.append(node("div", "eyebrow", title));
  box.append(node("span", "phrase", phraseText));
  box.append(node("label", "field-label", "Type the complete phrase above"));
  const input = node("input");
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = "Manual confirmation required";
  box.append(input);
  const actions = node("div", "confirmation-actions");
  const confirm = node("button", "button primary", buttonLabel);
  confirm.type = "button";
  confirm.addEventListener("click", () => {
    const value = input.value;
    input.value = "";
    onConfirm(value);
  });
  actions.append(confirm);
  box.append(actions);
  return box;
}

async function loadReview() {
  const id = byId("run-id").value.trim();
  if (!id) return notify("Enter a Run ID first.");
  const request = ++state.actionLookupRequest;
  clearActionContext("Loading the exact Runtime Plan and snapshot…");
  try {
    const review = await apiGet(`/api/review?run_id=${encodeURIComponent(id)}`);
    if (request !== state.actionLookupRequest || byId("run-id").value.trim() !== id) return;
    showReview(review);
  } catch (error) {
    if (request === state.actionLookupRequest && byId("run-id").value.trim() === id) showError(byId("action-review"), error.message);
  }
}

async function loadActionStatus() {
  const id = byId("action-id").value.trim() || byId("audit-action-id").value.trim();
  if (!id) return notify("Enter an Action ID first.");
  const request = ++state.actionLookupRequest;
  clearActionContext("Reading durable Runtime state and receipt…");
  try {
    const result = await apiGet(`/api/status?action_id=${encodeURIComponent(id)}`);
    const currentId = byId("action-id").value.trim() || byId("audit-action-id").value.trim();
    if (request !== state.actionLookupRequest || currentId !== id) return;
    showActionStatus(result);
    showAuditStatus(result);
  } catch (error) {
    const currentId = byId("action-id").value.trim() || byId("audit-action-id").value.trim();
    if (request === state.actionLookupRequest && currentId === id) {
      showError(byId("action-review"), error.message);
      showError(byId("audit-action-result"), error.message);
    }
  }
}

function showAuditStatus(payload) {
  const root = byId("audit-action-result");
  root.replaceChildren();
  const status = payload.status || "Unknown";
  const chip = node("span", `state-chip ${status === "Succeeded" ? "success" : status === "Unknown" || status === "Failed" ? "danger" : "warning"}`, status.toUpperCase());
  root.append(chip);
  const details = node("div", "review-grid");
  [["ACTION ID", payload.action?.action_id], ["EVIDENCE DIGEST", payload.evidence_digest], ["RECEIPT VERIFIED", payload.receipt_verified], ["EFFECT OBSERVED", payload.effect_observed]].forEach(([label, value]) => {
    const card = node("div", "review-card"); card.append(node("span", "", label), node("b", "", value ?? "—")); details.append(card);
  });
  root.append(details);
  byId("unknown-banner").classList.toggle("hidden", status !== "Unknown");
}

async function loadTickets() {
  const root = byId("tickets-list");
  root.replaceChildren(node("div", "empty-state", "Reading the local ticket store…"));
  try {
    const data = await apiGet("/api/tickets");
    byId("ticket-count").textContent = String(data.count);
    root.replaceChildren();
    if (!data.tickets?.length) { root.append(node("div", "empty-state", "No local incident tickets are persisted.")); return; }
    data.tickets.forEach((ticket) => {
      const card = node("article", "ticket-card");
      const top = node("div", "ticket-top");
      top.append(node("b", "", ticket.ticket_id || "Ticket record"));
      top.append(node("span", "state-chip success", "PERSISTED"));
      card.append(top);
      const fields = node("div", "ticket-fields");
      const entries = [
        ["INCIDENT", ticket.incident_id], ["ACTION", ticket.action_id || "Inspect Action status to link by effect key"],
        ["EVIDENCE DIGEST", ticket.evidence_digest], ["EFFECT KEY", ticket.effect_key],
        ["CREATED", ticket.created_at], ["RECEIPT", ticket.receipt_id || ticket.receipt_reference],
        ["PAYLOAD DIGEST", ticket.payload_digest], ["RECEIPT VERIFIED", ticket.receipt_verified],
      ];
      entries.forEach(([label, value]) => {
        if (value === undefined || value === null) return;
        const field = node("div", "ticket-field"); field.append(node("span", "", label), node("b", "", value)); fields.append(field);
      });
      card.append(fields);
      root.append(card);
    });
  } catch (error) { showError(root, error.message); }
}

async function runInvestigation() {
  const incidentId = byId("incident-select").value;
  if (!incidentId) return notify("Select an incident from the live graph first.");
  byId("investigate-button").disabled = true;
  byId("investigation-result").replaceChildren(node("div", "empty-state", "The local model is selecting a trusted read-only graph tool. CPU inference can take time."));
  byId("investigation-state").className = "state-chip warning";
  byId("investigation-state").textContent = "RUNNING";
  state.currentInvestigation = null;
  await startOperation("/api/investigations", { incident_id: incidentId }, byId("investigation-job"), "Investigation", (result) => renderInvestigation(result));
  byId("investigate-button").disabled = false;
}

async function proposeAction() {
  if (!state.currentInvestigation?.investigation_id) return notify("A complete trusted investigation is required.");
  const request = ++state.actionLookupRequest;
  byId("propose-button").disabled = true;
  clearActionContext("Preparing a new Plan. Load its Run ID to inspect the complete Plan and evidence.", { clearIds: true });
  showView("actions");
  await startOperation("/api/actions/propose", { investigation_id: state.currentInvestigation.investigation_id }, byId("action-job"), "Plan proposal", (result) => {
    if (request !== state.actionLookupRequest) return;
    const runId = result.run?.run_id;
    if (runId) byId("run-id").value = runId;
    byId("action-state").className = "state-chip warning";
    byId("action-state").textContent = String(result.status || "AWAITING_APPROVAL").toUpperCase();
    byId("action-review").replaceChildren(node("div", "success-state", `Run ${runId || "created"} is awaiting human review. Select Review to inspect the full Plan and evidence.`));
  });
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
byId("reload-graph").addEventListener("click", loadCatalog);
byId("dependencies-button").addEventListener("click", () => runGraphQuery("dependencies"));
byId("blast-button").addEventListener("click", () => runGraphQuery("blast-radius"));
byId("incident-select").addEventListener("change", (event) => { state.selectedIncident = event.target.value || null; });
byId("investigate-button").addEventListener("click", runInvestigation);
byId("propose-button").addEventListener("click", proposeAction);
byId("review-button").addEventListener("click", loadReview);
byId("status-button").addEventListener("click", loadActionStatus);
byId("audit-status-button").addEventListener("click", loadActionStatus);
byId("refresh-tickets").addEventListener("click", loadTickets);
byId("run-id").addEventListener("input", () => {
  state.actionLookupRequest += 1;
  clearActionContext("Run ID changed. Load that Run to review its exact Plan.");
});
["action-id", "audit-action-id"].forEach((id) => byId(id).addEventListener("input", () => {
  state.actionLookupRequest += 1;
  clearActionContext("Action ID changed. Inspect that Action to review its persisted state and receipt.");
}));

loadHealth();
loadCatalog();
