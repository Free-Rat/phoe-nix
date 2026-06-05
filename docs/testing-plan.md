# Testing Plan

## Goals

Catch correctness bugs, integration mismatches, and obvious repair-loop failures before anything reaches a real node or Azure environment.

Because the current target is a proof of concept on disposable VMs, testing should prioritize traceability and loop behavior over production-grade safety guarantees.

## Test Layers

### 1. Schema Contract Tests

- Validate every shared schema with minimal and maximal payloads.
- Validate timestamp parsing, enum rejection, optional field defaults, and backward-compatible field additions.
- Add round-trip tests: model -> JSON -> model.

### 2. Pure Function Unit Tests

For every service, test pure helpers with normal and adversarial inputs.

`token_service`
- auth header case-insensitivity
- node/body mismatch
- missing body
- SAS scope, expiry, uniqueness, permission narrowing

`log_service`
- empty entry filtering
- batch threshold flush
- interval flush
- shutdown flush
- spool file replay ordering
- retry exhaustion
- stale spool plus new buffer interaction

`log_router`
- malformed batch payload shape
- missing `MESSAGE`
- missing timestamp
- invalid priority
- unicode content
- large batches

`analysis_agent`
- prompt generation for log and observation sources
- AI JSON wrapped in markdown fences
- invalid AI JSON
- missing required AI fields
- unsupported enum values from AI
- fallback unit behavior
- richer raw-text analysis output if the schema is relaxed

`decision_agent`
- command mapping for each action
- invalid `restart_service` without target unit
- `no_action` audit behavior
- idempotency-key stability
- looser remediation-intent payloads if command-only decisions are relaxed

`local_agent`
- cooldown logic
- remediation-per-hour ceiling
- target-node filtering
- command whitelist enforcement
- state-hash change detection
- reporter summaries with and without failures remaining
- config edit capture and reporting
- rebuild retry loop behavior
- decision plus analysis-context handling

### 3. Service Boundary Tests

- HTTP request/response handling in `token_service`
- blob-trigger style parsing in `log_router`
- Service Bus payload parsing in `analysis_agent`, `decision_agent`, and `local_agent`
- Cosmos document shape for decision and execution-result writes

### 4. Simulator Integration Tests

Required scenarios:

- log happy path
- observation happy path
- token failure -> spool
- upload failure -> retry recovery
- malformed blob
- invalid AI response
- repeated decision delivery to local agent
- `no_action` decision path
- wrong-node decision ignored by local agent
- cooldown-triggered skip

### 5. Cross-Service Contract Tests

- `log_service` batch payload accepted by `log_router`
- `log_router` output accepted by `analysis_agent`
- `analysis_agent` output accepted by `decision_agent`
- `decision_agent` output accepted by `local_agent`
- decision plus linked analysis context accepted by `local_agent`

### 6. Live Azure Integration Tests

When Azure resources are available, add a manually-triggered suite for:

- Token Service HTTP request against deployed function
- real blob upload using returned SAS
- blob-triggered router publish to Service Bus
- analysis message consumed and converted into decision payload
- decision document visible in Cosmos DB

These should be opt-in and require explicit credentials and target environment selection.

## Edge Cases Still Worth Adding Tests For

- duplicate Service Bus deliveries
- clock skew around cooldown/expiry windows
- empty log batches
- oversized command output in execution results
- malformed but partially parseable AI response
- Cosmos transient write failures
- Service Bus publish retries and idempotency
- invalid Nix config produced during a repair attempt
- repeated failed repairs on the same node

## CI Strategy

On every push or PR:

- run `bash scripts/test.sh`
- run simulator scenarios

On manual or protected-environment workflows:

- run live Azure integration suite

## Definition Of Done

A pipeline stage is not complete until:

1. unit tests cover normal and failure cases
2. simulator coverage proves inter-service compatibility
3. docs describe failure behavior and operational expectations
4. live integration coverage exists or is explicitly documented as pending
