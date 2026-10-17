"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP_PATH = path.join(
  __dirname,
  "..",
  "src",
  "incidentgraph_agent",
  "web",
  "app.js",
);
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");

class FakeClassList {
  constructor(element) {
    this.element = element;
  }

  values() {
    return new Set(this.element.className.split(/\s+/).filter(Boolean));
  }

  add(...values) {
    const next = this.values();
    values.forEach((value) => next.add(value));
    this.element.className = [...next].join(" ");
  }

  remove(...values) {
    const next = this.values();
    values.forEach((value) => next.delete(value));
    this.element.className = [...next].join(" ");
  }

  contains(value) {
    return this.values().has(value);
  }

  toggle(value, force) {
    const present = this.contains(value);
    const shouldAdd = force === undefined ? !present : Boolean(force);
    if (shouldAdd) this.add(value);
    else this.remove(value);
    return shouldAdd;
  }
}

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.className = "";
    this.classList = new FakeClassList(this);
    this.value = "";
    this.disabled = false;
    this.type = "";
    this.autocomplete = "";
    this.spellcheck = false;
    this._textContent = "";
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get childElementCount() {
    return this.children.length;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this._textContent = "";
    this.children = [...children];
  }

  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(callback);
    this.listeners.set(type, callbacks);
  }

  dispatch(type, event = {}) {
    const callbacks = this.listeners.get(type) || [];
    return callbacks.map((callback) =>
      callback({ target: this, preventDefault() {}, ...event }),
    );
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  querySelector(selector) {
    const wanted = selector.toLowerCase();
    const visit = (element) => {
      if (wanted === "input" && element.tagName === "input") return element;
      if (wanted === ".phrase" && element.className.split(/\s+/).includes("phrase")) {
        return element;
      }
      for (const child of element.children) {
        const found = visit(child);
        if (found) return found;
      }
      return null;
    };
    return visit(this);
  }
}

function element(tagName) {
  const item = new FakeElement();
  item.tagName = tagName.toLowerCase();
  return item;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(payload, ok = true) {
  return { ok, json: async () => payload };
}

function actionPayload(actionId, status) {
  return {
    status,
    evidence_digest: `evidence-${actionId}`,
    receipt_verified: false,
    effect_observed: false,
    snapshot_fresh: true,
    action: { action_id: actionId, state: status },
  };
}

function reviewPayload(runId, status) {
  return {
    run_id: runId,
    status,
    plan_digest: `digest-${runId}`,
    plan: { intent: "create local ticket" },
    incident_id: "incident-1",
    target: "local-ticket-store",
    evidence_digest: `evidence-${runId}`,
    risk_class: "LOW",
    snapshot_fresh: true,
    snapshot_age_seconds: 1,
    causality_status: "unproven",
    verified_dependency_paths: [],
  };
}

function createHarness() {
  const elements = new Map();
  const statuses = new Map();
  const deferredStatuses = new Map();
  const statusRequests = new Map();
  const reviews = new Map();
  const operationQueues = new Map();
  const jobs = new Map();
  const posts = [];
  let requestId = 0;
  let timerId = 0;

  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };

  const fetch = async (input, options = {}) => {
    const url = new URL(String(input), "http://incidentgraph.test");
    const method = options.method || "GET";

    if (method === "POST") {
      const body = JSON.parse(options.body);
      const queue = operationQueues.get(url.pathname) || [];
      assert.ok(queue.length, `No queued mocked operation for ${url.pathname}`);
      const job = queue.shift();
      posts.push({ path: url.pathname, body, jobId: job.jobId });
      return response({ duplicate: false, job: { job_id: job.jobId } });
    }

    if (url.pathname === "/api/health") {
      return response({ status: "ready", graph: { redis_ping: "PONG" }, model: "test" });
    }
    if (url.pathname === "/api/catalog") {
      return response({
        services: [],
        dependencies: [],
        incidents: [],
        incident_associations: [],
      });
    }
    if (url.pathname === "/api/review") {
      const runId = url.searchParams.get("run_id");
      const payload = reviews.get(runId);
      if (!payload) return response({ error: { message: "Run not found" } }, false);
      return response(payload);
    }
    if (url.pathname === "/api/status") {
      const actionId = url.searchParams.get("action_id");
      statusRequests.set(actionId, (statusRequests.get(actionId) || 0) + 1);
      const waiting = deferredStatuses.get(actionId) || [];
      if (waiting.length) return waiting.shift().promise;
      return response(statuses.get(actionId) || actionPayload(actionId, "Pending"));
    }
    if (url.pathname.startsWith("/api/jobs/")) {
      const jobId = decodeURIComponent(url.pathname.slice("/api/jobs/".length));
      const job = jobs.get(jobId);
      assert.ok(job, `Unexpected job poll: ${jobId}`);
      job.polls += 1;
      return job.completion.promise;
    }
    throw new Error(`Unexpected mocked request: ${method} ${url.pathname}${url.search}`);
  };

  const document = {
    getElementById: getElement,
    createElement: element,
    createElementNS: (_namespace, tagName) => element(tagName),
    querySelectorAll: () => [],
  };
  const window = {
    setTimeout(callback, milliseconds) {
      const id = ++timerId;
      if (milliseconds <= 1000) queueMicrotask(callback);
      return id;
    },
    clearTimeout() {},
  };
  const sandbox = {
    document,
    window,
    fetch,
    crypto: { randomUUID: () => `request-${++requestId}` },
    console: { log() {}, warn() {}, error() {} },
  };
  vm.runInNewContext(APP_SOURCE, sandbox, { filename: APP_PATH });

  const env = {
    context: sandbox,
    element: getElement,
    posts,
    queueOperation(pathname, jobId) {
      const job = { jobId, polls: 0, completion: deferred() };
      jobs.set(jobId, job);
      const queue = operationQueues.get(pathname) || [];
      queue.push(job);
      operationQueues.set(pathname, queue);
      return job;
    },
    setStatus(actionId, status) {
      statuses.set(actionId, actionPayload(actionId, status));
    },
    statusRequestCount(actionId) {
      return statusRequests.get(actionId) || 0;
    },
    deferStatus(actionId) {
      const pending = deferred();
      const queue = deferredStatuses.get(actionId) || [];
      queue.push(pending);
      deferredStatuses.set(actionId, queue);
      return pending;
    },
    setReview(runId, status) {
      reviews.set(runId, reviewPayload(runId, status));
    },
    async settle() {
      for (let index = 0; index < 8; index += 1) {
        await new Promise((resolve) => setImmediate(resolve));
      }
    },
    async waitFor(predicate, message) {
      for (let index = 0; index < 100; index += 1) {
        if (predicate()) return;
        await new Promise((resolve) => setImmediate(resolve));
      }
      assert.fail(message || "Timed out waiting for mocked browser operation.");
    },
    async loadAction(actionId, status) {
      this.setStatus(actionId, status);
      getElement("action-id").value = actionId;
      await sandbox.loadActionStatus();
    },
    async loadRun(runId, status) {
      this.setReview(runId, status);
      getElement("run-id").value = runId;
      await sandbox.loadReview();
    },
    change(id, value) {
      const input = getElement(id);
      input.value = value;
      input.dispatch("input");
    },
    controls() {
      return getElement("action-controls");
    },
    button(label) {
      const visit = (item) => {
        if (item.tagName === "button" && item.textContent === label) return item;
        for (const child of item.children) {
          const found = visit(child);
          if (found) return found;
        }
        return null;
      };
      return visit(getElement("action-controls"));
    },
    async startConfirmation(label, phrase, jobId, endpoint) {
      this.queueOperation(endpoint, jobId);
      const input = getElement("action-controls").querySelector("input");
      assert.ok(input, `Expected confirmation input for ${label}`);
      const phraseElement = getElement("action-controls").querySelector(".phrase");
      assert.ok(phraseElement, `Expected confirmation phrase for ${label}`);
      input.value = phrase;
      const button = this.button(label);
      assert.ok(button, `Expected button ${label}`);
      button.dispatch("click");
      await this.waitFor(
        () => jobs.get(jobId).polls > 0,
        `Expected ${jobId} to be polled after ${label}`,
      );
      return jobs.get(jobId);
    },
    async finishJob(job, payload) {
      job.completion.resolve(response(payload));
      await this.settle();
    },
    async failJobPoll(job, error = new Error("poll unavailable")) {
      job.completion.reject(error);
      await this.settle();
    },
  };

  return env;
}

function notification(env) {
  return env.element("toast").textContent;
}

async function probeAuthorizationRace() {
  const env = createHarness();
  await env.loadAction("action-A", "Pending");
  const job = await env.startConfirmation(
    "Authorize Action",
    "AUTHORIZE action-A",
    "job-auth-A",
    "/api/actions/authorize",
  );
  env.change("action-id", "action-B");
  await env.finishJob(job, {
    state: "succeeded",
    result: { status: "Authorized", action: { action_id: "action-A", state: "Authorized" } },
  });
  return {
    selectedAction: env.element("action-id").value,
    displayedState: env.element("action-state").textContent,
    executeControlRestored: Boolean(env.button("Execute Action")),
    toast: notification(env),
    jobStatus: env.element("action-job").textContent,
  };
}

async function probeApprovalRace() {
  const env = createHarness();
  await env.loadRun("run-A", "AwaitingApproval");
  const job = await env.startConfirmation(
    "Approve Plan",
    "APPROVE digest-run-A",
    "job-approve-A",
    "/api/actions/decision",
  );
  env.change("run-id", "run-B");
  await env.finishJob(job, { state: "succeeded", result: { status: "Approved" } });
  return {
    selectedRun: env.element("run-id").value,
    displayedState: env.element("action-state").textContent,
    admitControlRestored: Boolean(env.button("Admit approved Run as Action")),
    planContainsRunA: env.element("action-review").textContent.includes("run-A"),
    toast: notification(env),
  };
}

async function testStaleAuthorization() {
  const env = createHarness();
  await env.loadAction("action-A", "Pending");
  const job = await env.startConfirmation(
    "Authorize Action",
    "AUTHORIZE action-A",
    "job-stale-auth",
    "/api/actions/authorize",
  );
  env.change("action-id", "action-B");
  await env.finishJob(job, {
    state: "succeeded",
    result: { status: "Authorized", action: { action_id: "action-A", state: "Authorized" } },
  });
  assert.equal(env.element("action-id").value, "action-B");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.match(notification(env), /previously selected Action action-A/i);
  assert.match(notification(env), /persisted status and receipt/i);
  assert.match(env.element("action-job").textContent, /Action action-A/i);
  assert.equal(
    env.posts.filter((post) => post.path === "/api/actions/authorize").length,
    1,
  );
  assert.equal(env.posts.filter((post) => post.path === "/api/actions/execute").length, 0);
}

async function testStaleApproval() {
  const env = createHarness();
  await env.loadRun("run-A", "AwaitingApproval");
  const job = await env.startConfirmation(
    "Approve Plan",
    "APPROVE digest-run-A",
    "job-stale-approval",
    "/api/actions/decision",
  );
  env.change("run-id", "run-B");
  await env.finishJob(job, { state: "succeeded", result: { status: "Approved" } });
  assert.equal(env.element("run-id").value, "run-B");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.doesNotMatch(env.element("action-review").textContent, /run-A/);
  assert.match(notification(env), /previously selected Run run-A/i);
}

async function testStaleRejection() {
  const env = createHarness();
  await env.loadRun("run-A", "AwaitingApproval");
  const job = env.queueOperation("/api/actions/decision", "job-stale-rejection");
  const input = env.controls().querySelector("input");
  input.value = "REJECT digest-run-A";
  const reject = env.button("Reject Plan");
  assert.ok(reject);
  reject.dispatch("click");
  await env.waitFor(() => job.polls > 0);
  env.change("run-id", "run-B");
  await env.finishJob(job, { state: "succeeded", result: { status: "Rejected" } });
  assert.equal(env.element("run-id").value, "run-B");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.match(notification(env), /previously selected Run run-A/i);
}

async function testStaleAdmission() {
  const env = createHarness();
  await env.loadRun("run-A", "Approved");
  const job = env.queueOperation("/api/actions/admit", "job-stale-admission");
  const admit = env.button("Admit approved Run as Action");
  assert.ok(admit);
  admit.dispatch("click");
  await env.waitFor(() => job.polls > 0);
  env.change("run-id", "run-B");
  await env.finishJob(job, {
    state: "succeeded",
    result: {
      status: "Pending",
      action: { action_id: "action-from-run-A", state: "Pending" },
    },
  });
  assert.equal(env.element("run-id").value, "run-B");
  assert.equal(env.element("action-id").value, "");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.match(notification(env), /Run run-A/i);
  assert.match(notification(env), /action-from-run-A/i);
}

async function testStaleProposal() {
  const env = createHarness();
  vm.runInNewContext(
    "state.currentInvestigation = { investigation_id: 'investigation-A' }",
    env.context,
  );
  const job = env.queueOperation("/api/actions/propose", "job-stale-proposal");
  env.element("propose-button").dispatch("click");
  await env.waitFor(() => job.polls > 0);
  env.change("run-id", "run-B");
  await env.finishJob(job, {
    state: "succeeded",
    result: { status: "AwaitingApproval", run: { run_id: "run-A" } },
  });
  assert.equal(env.element("run-id").value, "run-B");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.match(notification(env), /Run run-A/i);
}

async function testStaleExecutionCompletion() {
  const env = createHarness();
  await env.loadAction("action-A", "Authorized");
  const job = await env.startConfirmation(
    "Execute Action",
    "EXECUTE action-A",
    "job-stale-execution",
    "/api/actions/execute",
  );
  env.change("action-id", "action-B");
  await env.finishJob(job, {
    state: "succeeded",
    result: { status: "Succeeded", action: { action_id: "action-A", state: "Succeeded" } },
  });
  assert.equal(env.element("action-id").value, "action-B");
  assert.equal(env.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(env.controls().childElementCount, 0);
  assert.match(notification(env), /previously selected Action action-A/i);
  assert.match(notification(env), /inspect.*persisted status and receipt/i);
  assert.doesNotMatch(notification(env), /cancelled/i);
  assert.equal(env.posts.filter((post) => post.path === "/api/actions/execute").length, 1);
}

async function testNormalLifecycleGates() {
  const env = createHarness();
  await env.loadRun("run-normal", "AwaitingApproval");
  assert.ok(env.button("Approve Plan"));
  assert.equal(Boolean(env.button("Admit approved Run as Action")), false);

  const approvalJob = await env.startConfirmation(
    "Approve Plan",
    "APPROVE digest-run-normal",
    "job-normal-approval",
    "/api/actions/decision",
  );
  await env.finishJob(approvalJob, {
    state: "succeeded",
    result: { status: "Approved" },
  });
  assert.ok(env.button("Admit approved Run as Action"));
  assert.equal(Boolean(env.button("Authorize Action")), false);

  const admissionJob = env.queueOperation("/api/actions/admit", "job-normal-admission");
  env.button("Admit approved Run as Action").dispatch("click");
  await env.waitFor(() => admissionJob.polls > 0);
  await env.finishJob(admissionJob, {
    state: "succeeded",
    result: {
      status: "Pending",
      action: { action_id: "action-normal", state: "Pending" },
    },
  });
  assert.equal(env.element("action-id").value, "action-normal");
  assert.ok(env.button("Authorize Action"));
  assert.equal(Boolean(env.button("Execute Action")), false);

  const authorizationJob = await env.startConfirmation(
    "Authorize Action",
    "AUTHORIZE action-normal",
    "job-normal-authorization",
    "/api/actions/authorize",
  );
  await env.finishJob(authorizationJob, {
    state: "succeeded",
    result: {
      status: "Authorized",
      action: { action_id: "action-normal", state: "Authorized" },
    },
  });
  assert.ok(env.button("Execute Action"));

  const executionJob = await env.startConfirmation(
    "Execute Action",
    "EXECUTE action-normal",
    "job-normal-execution",
    "/api/actions/execute",
  );
  await env.finishJob(executionJob, {
    state: "succeeded",
    result: {
      status: "Succeeded",
      action: { action_id: "action-normal", state: "Succeeded" },
    },
  });
  assert.equal(env.element("action-state").textContent, "SUCCEEDED");
  assert.equal(env.controls().childElementCount, 0);
  assert.deepEqual(
    env.posts.map((post) => post.path),
    [
      "/api/actions/decision",
      "/api/actions/admit",
      "/api/actions/authorize",
      "/api/actions/execute",
    ],
  );
}

async function testOverlappingStatusRequests() {
  const env = createHarness();
  env.element("action-id").value = "action-A";
  const oldStatus = env.deferStatus("action-A");
  const olderRequest = env.context.loadActionStatus();
  await env.waitFor(() => env.statusRequestCount("action-A") === 1);
  assert.equal(env.element("action-review").textContent.includes("Reading durable"), true);

  env.change("action-id", "action-B");
  env.setStatus("action-B", "Pending");
  await env.context.loadActionStatus();
  assert.equal(env.element("action-id").value, "action-B");
  assert.equal(env.element("action-state").textContent, "PENDING");
  oldStatus.resolve(response(actionPayload("action-A", "Succeeded")));
  await olderRequest;
  assert.equal(env.element("action-id").value, "action-B");
  assert.equal(env.element("action-state").textContent, "PENDING");
  assert.match(env.element("action-review").textContent, /action-B/);
}

async function testStaleUnknownAndPollError() {
  const unknownEnv = createHarness();
  await unknownEnv.loadAction("action-A", "Authorized");
  const unknownJob = await unknownEnv.startConfirmation(
    "Execute Action",
    "EXECUTE action-A",
    "job-unknown-execution",
    "/api/actions/execute",
  );
  unknownEnv.change("action-id", "action-B");
  await unknownEnv.finishJob(unknownJob, {
    state: "succeeded",
    result: { status: "Unknown", action: { action_id: "action-A", state: "Unknown" } },
  });
  assert.equal(unknownEnv.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(unknownEnv.controls().childElementCount, 0);
  assert.match(notification(unknownEnv), /uncertain|unknown|could not be confirmed/i);
  assert.match(notification(unknownEnv), /action-A/i);
  assert.doesNotMatch(notification(unknownEnv), /cancelled/i);

  const failedEnv = createHarness();
  await failedEnv.loadAction("action-A", "Authorized");
  const failedJob = await failedEnv.startConfirmation(
    "Execute Action",
    "EXECUTE action-A",
    "job-poll-error-execution",
    "/api/actions/execute",
  );
  failedEnv.change("action-id", "action-B");
  await failedEnv.failJobPoll(failedJob);
  assert.equal(failedEnv.element("action-state").textContent, "NO RECORD LOADED");
  assert.equal(failedEnv.controls().childElementCount, 0);
  assert.match(notification(failedEnv), /could not be confirmed|uncertain/i);
  assert.match(notification(failedEnv), /action-A/i);
  assert.match(notification(failedEnv), /status and receipt/i);
  assert.doesNotMatch(notification(failedEnv), /cancelled/i);
  assert.equal(
    failedEnv.posts.filter((post) => post.path === "/api/actions/execute").length,
    1,
  );
}

async function main() {
  if (process.argv.includes("--probe")) {
    const authorization = await probeAuthorizationRace();
    const approval = await probeApprovalRace();
    process.stdout.write(
      `${JSON.stringify({ authorization, approval }, null, 2)}\n`,
    );
    return;
  }

  await testStaleAuthorization();
  await testStaleApproval();
  await testStaleRejection();
  await testStaleAdmission();
  await testStaleProposal();
  await testStaleExecutionCompletion();
  await testNormalLifecycleGates();
  await testOverlappingStatusRequests();
  await testStaleUnknownAndPollError();
  process.stdout.write("Frontend async race harness: all 9 scenarios passed.\n");
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
