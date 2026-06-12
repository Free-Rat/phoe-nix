# Service Connectivity Analysis

Date: 2026-06-12

This document reviews whether the services connect to each other correctly across code, schemas, Azure Function bindings, Terraform settings, scripts, and simulator wiring.

## Intended Flow

Log ingestion path:

1. `log_service` reads journal entries.
2. `log_service` requests a scoped upload SAS from `token_service`.
3. `log_service` uploads a JSON log batch to Blob Storage container `logs`.
4. `log_router` is triggered by blobs under `logs/{name}`.
5. `log_router` normalizes entries into `NormalizedLog` and publishes to Service Bus topic `analysis-input`.
6. `analysis_agent` consumes `analysis-input` via subscription `analysis-agent`.
7. `analysis_agent` calls OpenCode and publishes `AnalysisResult` to `analysis-results`.
8. `decision_agent` consumes `analysis-results` via subscription `decision-agent`.
9. `decision_agent` stores a Cosmos decision record and publishes `Decision` to `final-decisions`.
10. `local_agent` consumes `final-decisions` via subscription `local-agent` and runs remediation.
11. `local_agent` persists observations, node state, execution results, config snapshots, repair traces, and service status to Cosmos.

Observation path:

1. `local_agent` builds `Observation` messages from local node state.
2. It publishes observations to `analysis-input`.
3. The same `analysis_agent -> decision_agent -> local_agent` path follows.

## Code-Level Connections

### `log_service -> token_service`

`log_service` requires `TOKEN_SERVICE_URL`, `NODE_ID`, and optional `NODE_API_KEY` in `log_service/src/log_service/config.py:20-23`.

`log_service` sends body `{"node_id": ...}` plus `X-Node-ID` and `X-API-Key` headers from `log_service/src/log_service/token_client.py:12-39`.

Connectivity status: code path is coherent, but rendered VM env omits `NODE_ID`, so the live VM path will fail until fixed.

### `token_service -> Blob Storage`

`token_service` validates shared key and body/header node match in `token_service/src/token_service/auth.py:20-27`.

It creates blob names as `<node_id>/<uuid>` and returns blob paths as `logs/<node_id>/<uuid>` in `token_service/src/token_service/sas_generator.py:10-67`.

Terraform creates the `logs` container in `infrastructure/03-blob-storage/main.tf:25-29`.

Connectivity status: path naming is consistent for the default `logs` container. Identity binding is weak but not a wiring break.

### Blob Storage -> `log_router`

`log_router/src/log_router/function.json:5-10` binds to `path: logs/{name}` and `connection: LOGS_STORAGE_CONNECTION`.

Terraform supplies `LOGS_STORAGE_CONNECTION` in `infrastructure/04-stateless/main.tf:259-266`.

Connectivity status: likely correct. Verify live that `{name}` captures virtual directory paths like `node-id/uuid`.

### `log_router -> analysis-input`

`log_router/src/log_router/main.py:10-32` publishes normalized logs to the topic in `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME`.

Terraform sets this to `analysis-input` in `infrastructure/04-stateless/main.tf:259-263`.

Connectivity status: topic name is consistent.

### `analysis-input -> analysis_agent`

Terraform creates topic `analysis-input` and subscription `analysis-agent` in `infrastructure/04-stateless/main.tf:34-58`.

`analysis_agent/src/analysis_agent/function.json:5-11` consumes `%SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME%` and hardcoded subscription `analysis-agent` using `SERVICEBUS_CONNECTION`.

Terraform supplies matching settings in `infrastructure/04-stateless/main.tf:295-304`.

Connectivity status: topic/subscription names are consistent.

### `analysis_agent -> analysis-results`

`analysis_agent/src/analysis_agent/main.py:11-38` publishes to `config.analysis_results_topic_name`.

Terraform sets `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME=analysis-results` in `infrastructure/04-stateless/main.tf:295-299`.

Connectivity status: topic name is consistent.

### `analysis-results -> decision_agent`

Terraform creates topic `analysis-results` and subscription `decision-agent` in `infrastructure/04-stateless/main.tf:40-66`.

`decision_agent/src/decision_agent/function.json:5-11` consumes `%SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME%` and hardcoded subscription `decision-agent`.

Connectivity status: topic/subscription names are consistent.

### `decision_agent -> final-decisions`

`decision_agent/src/decision_agent/main.py:11-37` publishes decisions to `final-decisions`.

Terraform creates `final-decisions` in `infrastructure/04-stateless/main.tf:46-50` and sets `SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME` in `infrastructure/04-stateless/main.tf:334-338`.

Connectivity status: topic name is consistent.

### `final-decisions -> local_agent`

Terraform creates one subscription `local-agent` in `infrastructure/04-stateless/main.tf:68-74`.

`local_agent` defaults to topic `final-decisions` and subscription `local-agent` in `local_agent/src/local_agent/config.py:22-24`.

`local_agent/src/local_agent/runtime.py:421-473` receives and completes messages.

Connectivity status: single-node topic/subscription names align, but receive/complete lifecycle is likely broken and multi-node routing is unsafe.

## Topic And Subscription Consistency

| Flow | Topic | Subscription | Status |
|---|---|---|---|
| log/router and observations to analysis | `analysis-input` | `analysis-agent` | Consistent |
| analysis to decisions | `analysis-results` | `decision-agent` | Consistent |
| decisions to local agent | `final-decisions` | `local-agent` | Consistent for one node, unsafe for multiple nodes |

The topic names are consistent across Terraform, function bindings, service configs, simulator fixtures, and scripts. The main problem is the unfiltered shared `final-decisions/local-agent` subscription.

## Payload Compatibility

### `LogBatch -> NormalizedLog`

`log_service` uploads `LogBatch(node_id, entries, uploaded_at)`. `log_router` expects `node_id` and `entries` and builds `NormalizedLog` from journal fields.

Status: compatible, but one malformed entry drops the full batch today.

### `NormalizedLog -> AnalysisResult`

`NormalizedLog.source` defaults to `log_router`. `analysis_agent` treats messages with `source != local_agent` as logs.

Status: compatible.

### `Observation -> AnalysisResult`

`Observation.source` defaults to `local_agent`. `analysis_agent` branches on that value.

Status: compatible.

### `AnalysisResult -> Decision`

`decision_agent` validates Service Bus body as `AnalysisResult` and emits `Decision`.

Status: compatible, but `Decision.analysis_id` currently uses `AnalysisResult.original_message_id`, not a true analysis result ID.

### `Decision -> local_agent`

`local_agent` validates incoming payload with `Decision.model_validate()`.

Status: compatible, but `apply_config` can fail earlier in `decision_agent`, direct commands are unsafe, and idempotency is not enforced.

## Confirmed Connectivity Bugs And Mismatches

### P0: Rendered VM Env Omits `NODE_ID`

References:

- `log_service/src/log_service/config.py:20-23`
- `local_agent/src/local_agent/config.py:81`
- `infrastructure/render-vm-env.sh:136-158`

Impact: `log_service` fails at startup with missing `NODE_ID`. `local_agent` defaults to `localhost`, while scripts commonly target `nixos`, so decisions may never match the node.

Fix: render the same explicit `NODE_ID` into both env files.

### P0: `local_agent` Service Bus Settlement Is Likely Invalid

References:

- `local_agent/src/local_agent/bus_client.py:54-86`
- `local_agent/src/local_agent/runtime.py:421-473`

Impact: successful decisions may not complete; messages can redeliver and re-run repairs.

Fix: receive, process, and settle on the same live receiver.

### P0/P1: Shared `final-decisions/local-agent` Subscription

References:

- `infrastructure/04-stateless/main.tf:68-74`
- `infrastructure/render-vm-env.sh:139-143`
- `local_agent/src/local_agent/runtime.py:178-185`

Impact: multi-node consumers compete on one subscription. Wrong-node decisions can be skipped and completed by the wrong node.

Fix: one filtered subscription per node; set `node_id` Service Bus application property; do not complete wrong-node messages.

### P1: Function Apps Likely Miss Runtime App Settings

References:

- `infrastructure/04-stateless/main.tf:222-230`
- `infrastructure/04-stateless/main.tf:259-266`
- `infrastructure/04-stateless/main.tf:295-305`
- `infrastructure/04-stateless/main.tf:334-344`

No `FUNCTIONS_WORKER_RUNTIME=python` appears in app settings. `application_stack.python_version` may not be sufficient for Azure Functions indexing/runtime behavior.

Fix: explicitly set `FUNCTIONS_WORKER_RUNTIME=python` and consider `FUNCTIONS_EXTENSION_VERSION=~4` for every Function App.

### P1: Rendered Ollama Model Mismatch

References:

- `local_agent/src/local_agent/config.py:41-43`
- `local_agent/src/local_agent/config.py:87-89`
- `infrastructure/render-vm-env.sh:154-155`

Impact: rendered VM env overrides the code default with likely invalid/untested `gemma4:e4b`.

Fix: render `${OLLAMA_MODEL:-gemma3:4b}` and verify the model is installed.

### P1: Structured Failure Events Missing

References:

- `log_router/src/log_router/normalizer.py:7-49`
- `analysis_agent/src/analysis_agent/ai_client.py:137-192`

Malformed blobs and invalid AI outputs rely on Azure retry/DLQ behavior instead of explicit status records.

Fix: partial normalization with structured errors; publish/persist `analysis_failed` records.

### P2: Token Identity Is Weak

References:

- `token_service/src/token_service/auth.py:20-27`
- `log_router/src/log_router/normalizer.py:7-13`

Impact: any holder of shared node key can upload as any node, and log-router trusts payload `node_id` rather than binding it to the blob path.

Fix: per-node credentials; strict node ID validation; optionally validate blob path prefix matches payload node ID.

## Missing Environment/App Settings

Required fixes:

- Add `NODE_ID` to `log-service.env`.
- Add `NODE_ID` to `local-agent.env`.
- Add `FUNCTIONS_WORKER_RUNTIME=python` to all Function Apps.
- Consider explicit `FUNCTIONS_EXTENSION_VERSION=~4`.

Optional/defaulted settings are acceptable today:

- `TOKEN_TTL_MINUTES`
- `AI_TIMEOUT_SECONDS`
- `COSMOSDB_DECISIONS_CONTAINER_NAME`
- local-agent Cosmos container names

## Azure Binding Notes

- Blob trigger path `logs/{name}` should be live-verified with nested blob names like `node-id/uuid`.
- Service Bus trigger topic/subscription settings are internally consistent.
- `log_router` uses SDK publishing rather than an output binding; this is fine if dependencies and `SERVICEBUS_CONNECTION` deploy correctly.

## Simulator Connectivity Gaps

The simulator currently bypasses important live mechanics:

- no subscriptions, locks, completion, abandon, delivery counts, lock expiry, or DLQ;
- no Azure Function host/indexing/app-setting validation;
- no Key Vault reference behavior;
- `topic_messages()` does not consume messages;
- `process_local_agent()` bypasses `decision_worker()`;
- no multi-node final-decision routing;
- no structured checks for malformed blob or invalid AI failure events.

Fix: implement subscription-aware fake Service Bus and make local-agent simulator processing use `decision_worker()`.

## Prioritized Connectivity Fix Plan

1. Render one consistent `NODE_ID` into all VM env files.
2. Fix local-agent Service Bus receive/complete lifecycle.
3. Replace shared `final-decisions/local-agent` subscription with per-node filtered subscriptions.
4. Add Function runtime app settings in Terraform.
5. Align rendered Ollama model.
6. Add structured cloud failure records for malformed blobs and invalid AI responses.
7. Improve simulator to model real Service Bus lifecycle and multi-node routing.
8. Bind node credentials to node identity and validate payload/blob path consistency.
