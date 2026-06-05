# Current State

## Overview

The repository currently implements most of the backend pipeline end to end, with both real service packages and a local simulator that exercises the full flow without Azure.

The implemented code is still closer to a structured analysis-and-command pipeline than to the newer proof-of-concept direction. The intended direction is documented in `proof-of-concept-direction.md`: `local_agent` should evolve into the main config-repair agent using cloud context plus local LLM reasoning.

Implemented backend path:

1. `token_service`
2. `log_service`
3. `log_router`
4. `analysis_agent`
5. `decision_agent`
6. `local_agent` core logic
7. `simulator` for end-to-end local validation

The main things still missing are the real `local_agent` runtime, the proof-of-concept config-repair loop against the shared config repository, and a minimal frontend/TUI for pipeline visibility.

## Full Flow

### 1. Log Upload Authorization: `token_service`

- Accepts a request from a node asking for permission to upload logs.
- Validates:
  - `x-node-id`
  - `x-api-key`
  - request body `node_id`
- Reads the storage account key from Key Vault.
- Generates a blob path like `logs/<node_id>/<uuid>`.
- Returns a SAS URL scoped to exactly that blob path.

Purpose:

- least-privilege upload
- one upload token per blob
- nodes cannot overwrite each other’s paths

Core files:

- `token_service/src/token_service/auth.py`
- `token_service/src/token_service/sas_generator.py`
- `token_service/src/token_service/app.py`

### 2. On-Node Log Collection: `log_service`

- Intended to run on the NixOS node.
- Reads systemd journal entries.
- Buffers them in memory instead of uploading one by one.
- Flushes when:
  - batch size is reached
  - flush interval expires
  - process shuts down
- For each batch:
  - asks `token_service` for a SAS URL
  - uploads the batch payload to blob storage
- If upload fails:
  - retries with backoff
  - if retries still fail, spools the batch to local disk for replay later

Current payload shape is a batch:

- `node_id`
- `entries`
- `uploaded_at`

Core files:

- `log_service/src/log_service/main.py`
- `log_service/src/log_service/uploader.py`
- `log_service/src/log_service/storage.py`
- `log_service/src/log_service/token_client.py`

Important note:

- the service logic is implemented and tested
- `nix run` packaging still needs cleanup
- the Python logic itself works

### 3. Blob Trigger Normalization: `log_router`

- Triggered when a blob appears in `logs/`
- Reads the uploaded batch
- Parses each raw journal entry
- Converts each entry into a shared `NormalizedLog` schema
- Publishes one normalized message per log entry to Service Bus topic `analysis-input`

What normalization adds:

- `node_id`
- parsed timestamp
- service/unit
- priority
- hostname
- source identifier
- original blob path

Core files:

- `log_router/src/log_router/normalizer.py`
- `log_router/src/log_router/main.py`

### 4. AI Analysis Stage: `analysis_agent`

- Consumes messages from topic `analysis-input`
- Supports two input types:
  - `NormalizedLog` from `log_router`
  - `Observation` from `local_agent`
- Detects type by schema/source
- Builds a source-specific prompt
- Reads OpenCode API key from Key Vault
- Calls the OpenCode API
- The current implementation validates/parses the returned JSON into `AnalysisResult`
- The intended proof-of-concept direction can relax this toward richer raw analysis text
- Publishes analysis output to topic `analysis-results`

This stage is where raw operational evidence becomes diagnosis. Today that diagnosis is strongly structured; the intended proof-of-concept direction allows it to become more text-forward so `local_agent` can interpret it locally.

Output contains things like:

- `error_type`
- `severity`
- `root_cause`
- `suggested_action`
- `affected_unit`
- `confidence`

Core files:

- `analysis_agent/src/analysis_agent/prompt_builder.py`
- `analysis_agent/src/analysis_agent/ai_client.py`
- `analysis_agent/src/analysis_agent/message_handler.py`
- `analysis_agent/src/analysis_agent/main.py`

### 5. Decision Stage: `decision_agent`

- Consumes analysis output from `analysis-results`
- The current implementation maps analysis into a concrete NixOS action
- The intended proof-of-concept direction shifts toward looser remediation intent plus analysis context for `local_agent`
- Builds a `Decision`
- Writes the decision to Cosmos DB for audit
- Publishes the final decision payload to topic `final-decisions`

Current example mappings:

- `rollback` -> `nixos-rebuild switch --rollback`
- `rebuild` -> `nixos-rebuild switch`
- `restart_service` -> `systemctl restart <unit>`
- `no_action` -> empty command

Core files:

- `decision_agent/src/decision_agent/decision_engine.py`
- `decision_agent/src/decision_agent/app.py`
- `decision_agent/src/decision_agent/main.py`
- `decision_agent/src/decision_agent/cosmos.py`

Direction note:

- the intended topic split is `analysis-input`, `analysis-results`, and `final-decisions`
- the current implementation has not fully caught up to that split yet

### 6. Local Execution Logic: `local_agent`

This package now exists and implements the core logic, but not yet the full production daemon/runtime.

It currently provides:

Monitor logic:

- turns node state into `Observation`
- summarizes failures/restarts/disk usage
- detects whether state changed enough to publish

State logic:

- stores local safety state
- tracks:
  - last remediation time
  - remediations this hour
  - whether remediation is ongoing
  - observation hash

Executor logic:

- validates a decision is targeted to this node
- the current implementation checks command safety and executes an injected runner
- the intended proof-of-concept direction is broader: `local_agent` should receive decision plus analysis context, pull the shared config repo, edit `configuration.nix`, run `nixos-rebuild test` correction loops, run `switch`, push successful changes, and report the full repair trace
- creates an `ExecutionResult`

Reporter logic:

- builds execution result payloads
- builds node-state documents for persistence

So `local_agent` is currently a reusable engine, not yet a full always-on service loop or the intended config-repair agent described in `proof-of-concept-direction.md`.

Core files:

- `local_agent/src/local_agent/state.py`
- `local_agent/src/local_agent/monitor.py`
- `local_agent/src/local_agent/executor.py`
- `local_agent/src/local_agent/reporter.py`
- `local_agent/src/local_agent/main.py`

Still missing for `local_agent`:

- long-running coordinator loop
- real host inspection
- real Service Bus receive loop
- real Cosmos write loop
- Nix/systemd deployment integration
- local LLM-driven config editing loop
- config repository clone/pull/push workflow
- `nixos-rebuild test` correction loop before `switch`
- reporting of before/after config state and rebuild traces

### Shared Contracts: `schemas`

All services communicate through shared Pydantic models.

Important ones:

- `NormalizedLog`
- `Observation`
- `AnalysisResult`
- `Decision`
- `ExecutionResult`
- `NodeState`

This is the contract layer across services.

Location:

- `schemas/src/schemas/`

## Simulator

The simulator is the current best “real deployment” substitute.

It runs the actual service cores in process using fake infrastructure:

- fake Blob Storage
- fake Service Bus
- fake Cosmos DB
- fake Key Vault
- fake OpenCode API
- fake local agent execution sink

It validates the real flow:

- token issuance
- batched upload
- blob normalization
- analysis
- decision creation
- local execution
- execution-result persistence

It also covers failure cases:

- token failure -> spool instead of upload
- upload retry/recovery
- malformed blob
- invalid AI response

So the simulator is not mock-only unit testing. It is a local end-to-end orchestration of the real service logic.

Core files:

- `simulator/src/simulator/pipeline.py`
- `simulator/src/simulator/fakes.py`
- `simulator/src/simulator/fixtures.py`
- `simulator/src/simulator/cli.py`

## Testing Status

There are now multiple layers of testing.

Unit/service tests:

- `token_service`
- `log_service`
- `log_router`
- `analysis_agent`
- `decision_agent`
- `local_agent`

Integration/simulation tests:

- simulator happy path for logs
- simulator happy path for observations
- token failure path
- upload retry path
- malformed blob path
- invalid AI response path

Main test command:

- `bash scripts/test.sh`

Simulation command:

- `bash scripts/simulate-deployment.sh`

## Implemented vs Missing

Implemented:

- shared schemas
- token issuance flow
- batched log upload flow
- log normalization flow
- AI analysis flow
- decision generation flow
- local-agent core logic
- Cosmos decision audit writes
- end-to-end local simulation
- documentation and testing plans

Still missing:

- frontend
- real long-running `local_agent` runtime
- real Azure live integration tests
- more ops hardening:
  - DLQ handling
  - production retries around all cloud writes
  - dashboards/alerts
- final deployment/architecture artifacts

## Current Mental Model

The system can be thought of in three layers.

Collection:

- `log_service`
- `token_service`

Cloud reasoning:

- `log_router`
- `analysis_agent`
- `decision_agent`

Node action:

- `local_agent`

And one cross-cutting layer:

- `schemas`

And one safety/testing layer:

- `simulator`

## Useful Follow-Up Topics

Useful follow-up explanations:

1. the exact message formats between services
2. how the simulator runs each step internally
3. what remains to make `local_agent` production-ready
