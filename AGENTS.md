# Hackathon Agent Instructions — Reuse Agentic Intent Gate

## Purpose

This repository is a **Hackathon application that consumes an existing Python Agent Runtime**. Build the competition-specific product, **not another agent execution framework**.

**Shared runtime:** [Agentic Intent Gate](https://github.com/miku-wwl/agentic-intent-gate)<br>
**Python distribution:** `agentic-intent-gate`<br>
**Public integration module:** `agentic_intent_gate.runtime`<br>
**Qualified release:** `v0.2.0` (verified annotated Git tag; Python 3.13; MIT)<br>
**Pinned release commit:** `7261fdd2f47d2b15fabe8faac527eeee37338eb1`

The annotated tag `v0.2.0` peels to the pinned commit above. This is a **Git release, not a PyPI publication**. Use the immutable full commit SHA, **not** a moving `main` branch. When upgrading in the future, verify the tag, peeled commit, installed version, and lockfile. Never embed private Git credentials or external-service secrets in URLs, code, screenshots, or logs.

**Template use:** Copy this document into a **new consumer repository** as its root `AGENTS.md`. Add the competition's own scope, security rules, environment constraints, and human-approval requirements. If that repository already has `AGENTS.md`, **merge** the instructions instead of overwriting its product-specific rules. Do not copy the core repository's developer-only root `AGENTS.md` into a consumer project.

## Dependency Setup

Declare this exact dependency in the consumer's existing `pyproject.toml` and update its lockfile:

```toml
[project]
dependencies = [
  "agentic-intent-gate @ git+https://github.com/miku-wwl/agentic-intent-gate.git@7261fdd2f47d2b15fabe8faac527eeee37338eb1",
]
```

**Merge the dependency entry**; do not replace an existing `[project]` section or other dependencies blindly. Verify the installed distribution reports `agentic-intent-gate==0.2.0`, resolves to the pinned source commit, and is imported from the installed environment, **not an adjacent source checkout**. Use the [v0.2.0 consumer handoff](https://github.com/miku-wwl/agentic-intent-gate/blob/main/docs/V0.2.0-CONSUMER-HANDOFF.md) and [adapter guide](https://github.com/miku-wwl/agentic-intent-gate/blob/v0.2.0/docs/ADAPTER_GUIDE.md) as the detailed integration references.

## Reuse the Core

Within its supported scope, Agentic Intent Gate already provides:

- Strict structured Plan validation and trusted, bounded tool schemas.
- Persistent Runs and exact Plan-to-Approval binding.
- Independent Action execution Authorization, separate from Plan Approval.
- Explicit trusted single-tool `ToolExecutor` registration.
- Single-step `SIDE_EFFECT` Action lifecycle.
- SQLite-backed durable Action state, claim, audit events and receipt references.
- CAS/concurrency controls, stable effect keys, binding identity and idempotent command handling.
- Fail-closed recovery and terminal ambiguous/`Unknown` outcomes (no blind retry).
- Trusted configurable adapter execution budgets: default **5 seconds**, explicit maximum **15 seconds**.

**Do not copy, fork, reimplement, monkeypatch, or bypass these core mechanisms in the consumer.** Use the published public API and a fixed dependency revision.

## Public Integration Entry Point

Inspect the **installed v0.2.0 API** and the core repository's `examples/consumer/app.py` before implementing integration. Supported imports include:

```python
from agentic_intent_gate.runtime import (
    ToolBinding,
    ToolCapability,
    ExecutableRegistry,
    CapabilityVerifier,
    BoundCapabilityAuthorization,
    execution_budget_binding,
    open_runtime,
)
```

Check the actual constructor/function signatures; do not invent methods. Register trusted executors at startup with exact tool/version/target/adapter identity, reviewed binding revision, canonical execution scope, strict argument schema and appropriate independent authorization. Model output and untrusted HTTP input cannot register code or select trusted identities.

### Trusted Execution Budget (v0.2.0)

- `open_runtime(..., execution_timeout_seconds=5)` is the **backward-compatible default**. Finite built-in `int`/`float` values in `(0, 15]` are supported. Booleans, invalid types, NaN, infinity, non-positive values and values over 15 are rejected.
- For values **above 5**, trusted host code must explicitly opt in, use a **fresh reviewed base `ToolBinding` revision**, and apply `execution_budget_binding(base_binding, 15)` to produce a canonical binding revision ending in `.exec-15s`. The complete revision must fit the 64-character limit.
- Pass `execution_timeout_seconds=15` to `open_runtime` **only after the complete reviewed binding, capability, registry, verifier, policy and consumer effect/receipt identity are consistently configured**. Do not let model output, tool arguments, browser parameters, or API clients choose or increase this value.
- Keep the extended profile's binding revision, scope and persisted consumer configuration **stable across restarts**. Use a fresh UUIDv4 scope and **separate private gate/ledger/receipt-store identity** for a new extended profile.
- **Do not silently elevate old Actions or grants.** Never migrate, relabel, replay, or reuse legacy Approval, Authorization, effect keys or receipt identities under a longer budget. Create a new Plan/Run, obtain fresh human Approval, admit a new Action and obtain separate Authorization. Existing default-profile Actions retain their original identities; previously Failed/Unknown terminal Actions must not be reset or replayed.
- Extended Actions use `action-compiler-v3` / `action-spec-v3` and bind the reviewed budget profile into their identity. Old v1/v2 Actions cannot gain a longer budget merely by changing host configuration or using a similar binding name. Keep prior ledgers available for historical audit; v0.1.0 software cannot interpret v3 extended Actions.
- The configured adapter deadline starts **after durable Gateway claim**. The Gateway's synchronous claim and finish work are outside the adapter timer. A caller-supplied validated **monotonic** deadline may only **shorten**, never extend, this budget. Late outcomes, cancellation, lost acknowledgment and uncertain writes can still become `Unknown`.
- Provider requests, independent effect verification and adapter-side durable receipt persistence must fit within the adapter budget. External effects cannot necessarily be cancelled after dispatch. Avoid detached background execution, automatic retries, and recovery replay; **15 seconds is not a guaranteed completion time or exactly-once remote-effect guarantee**.
- The bundled Runtime Action HTTP routes retain a **10-second parent deadline**. If the consumer needs a full 15-second adapter allowance, use a supported public Gateway invocation within an appropriately budgeted **consumer-owned request envelope**; do not use private Runtime internals or assume the bundled HTTP endpoint grants 15 seconds.
- Consumer-specific constraints, including Sandbox-only execution, allowed targets, spend limits, HTTP timeouts, durable dispatch fencing and payment-provider policies, belong **in the consumer application**, not in the shared Runtime.

**Default rule:** Unless a reviewed use case needs an extended budget, omit the new argument and retain **5 seconds**.

## Competition-Specific Responsibilities

Implement **only** the product-specific requirements:

1. Product concept, competition integrations, frontend and demo experience.
2. Trusted consumer tools, bounded argument schemas, concrete executors and receipt stores.
3. External service/API/MCP clients, where genuinely needed.
4. Consumer authentication, authorization, credentials and environment controls.
5. A suitable consumer `ModelProvider` when model-driven Plan generation is required.
6. Product tests, evaluation, deployment, documentation and submission.

**Demo boundaries:** The core's bundled local LLM prompt is tailored to `service-demo/service_id`, and its UI Action-admission controls remain Demo-specific. A non-Demo consumer must provide suitable planning and its own UI or use supported API admission paths; the bundled Demo UI does **not** automatically support arbitrary tools. The reference consumer's scripted approval is test/example consent, not proof of authenticated human approval.

## Supported Scope and Non-Goals

v0.2.0 supports **one explicitly registered trusted `SIDE_EFFECT` tool per Runtime composition and single-step execution**. It is **not** a general-purpose multi-agent engine. Do not assume general `READ_ONLY` tool execution, multi-tool/multi-step workflow orchestration, dynamic plugin discovery, arbitrary MCP execution, production multi-user authentication, distributed HA, malicious-Python isolation, or guaranteed exactly-once remote side effects.

If the competition requires unsupported functionality, identify the gap before building. Prefer a thin product-owned layer where safe. Request a separately reviewed core extension only when unavoidable; never silently bypass the core's safety gates.

## Mandatory Safety Boundaries

- Model output proposes a Plan; it **never** chooses trusted credentials, executors, scopes, execution budgets or grant decisions.
- Verify exact tool identity, schema, target, binding revision, execution scope and trusted profile before compilation and execution.
- **Human Approval and execution Authorization are separate gates**. Approval alone never dispatches an effect; do not fabricate or inherit consent from earlier Actions.
- Never substitute a different Plan's Approval, skip durable claiming, or weaken owner fencing and authorization checks.
- Treat ambiguous post-dispatch effects as `Unknown`; **no automatic retry, unsafe reconciliation, duplicate POST or recovery replay**. Distinguish proven pre-dispatch no-effect failures from uncertain remote effects.
- Respect the provider's documented idempotency semantics; a provider request ID is **not** a universal exactly-once guarantee.
- Keep secrets, capability tokens, private Git credentials, personal data and private state outside source, docs, screenshots and CI logs.
- Do not initiate real money movements, financial API mutations or other irreversible third-party effects without fresh, explicit, operation-specific human authorization.

## Acceptance Criteria

An external consumer using the exact pinned, **installed** package, **without modifying core source**, must demonstrate:

**Validated Plan → Persistent Run → Exact Human Approval → Admission → Independent Authorization → Durable Action Claim → Consumer Executor → Verified Effect → Durable Receipt**

Tests should cover successful execution and persistence; unknown tool, wrong target/binding/scope, missing authorization, stale approval, duplicate/concurrent/conflicting effects, and ambiguous outcome. For extended profiles, additionally test default/15-second configuration, invalid budgets, wrong-profile denial, old-Action non-escalation, shortening caller deadlines, pre-dispatch expiry, lost acknowledgments, late receipts and process restart/recovery **without a second effect**. Verify actual durable state rather than only printed success. Synthetic planning is acceptable for offline tests; genuine external side effects need separate permission.

## Agent Work Protocol

Before changing code:

1. Read this `AGENTS.md`, other local instructions and actual competition requirements.
2. Verify the exact pinned dependency, installed public API, package version and lockfile.
3. Read the shared Runtime's `examples/consumer/app.py`, v0.2.0 handoff and adapter guide.
4. Propose the smallest **consumer-specific** implementation and targeted tests.
5. Flag missing capabilities rather than inventing public APIs or modifying the shared Runtime.

During development, preserve existing project state and security constraints. Keep changes scoped; use safe synthetic tests by default. **Do not commit, push, tag, release, deploy or run real external effects unless separately instructed.**

**Guiding rule: Build the Hackathon application; reuse the pinned Agentic Intent Gate Runtime.**
