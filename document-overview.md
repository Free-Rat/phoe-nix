# phoe-nix — Document Overview

> **Scope.** This document is a deep technical reference for the entire `phoe-nix` repository as it stands today. It covers every Python module, every service, every Azure resource, every Terraform module, and every operator script. For each item it explains: *why it exists*, *how it works*, *how it makes decisions*, and *what it connects to*.
>
> **Audience.** An engineer who is reading the docs to understand the system end-to-end before making changes or extending it.
>
> **Source of truth.** All descriptions below are derived directly from the source code, Terraform files, and config in the repo. They are not speculative. Where the source is known to have a gap, the gap is called out under "Known issues / notes" for that section.

---

## Table of contents

1. [System overview](#1-system-overview)
2. [The data plane (end-to-end flow)](#2-the-data-plane-end-to-end-flow)
3. [Shared message contracts — `schemas/`](#3-shared-message-contracts--schemas)
4. [Node-side collector — `log_service/`](#4-node-side-collector--log_service)
5. [Cloud-side: token authorization — `token_service/`](#5-cloud-side-token-authorization--token_service)
6. [Cloud-side: log normalization — `log_router/`](#6-cloud-side-log-normalization--log_router)
7. [Cloud-side: AI analysis — `analysis_agent/`](#7-cloud-side-ai-analysis--analysis_agent)
8. [Cloud-side: decision policy — `decision_agent/`](#8-cloud-side-decision-policy--decision_agent)
9. [Node-side: repair loop — `local_agent/`](#9-node-side-repair-loop--local_agent)
10. [Local simulator — `simulator/`](#10-local-simulator--simulator)
11. [Azure infrastructure — `infrastructure/`](#11-azure-infrastructure--infrastructure)
12. [Operator scripts — `scripts/`](#12-operator-scripts--scripts)
13. [Cross-reference: who calls whom](#13-cross-reference-who-calls-whom)
14. [Cross-reference: every Python module at a glance](#14-cross-reference-every-python-module-at-a-glance)
15. [Cross-reference: every Azure resource at a glance](#15-cross-reference-every-azure-resource-at-a-glance)

---

## 1. System overview

`phoe-nix` is a multi-service, event-driven pipeline for **self-healing NixOS VMs**. It collects operational evidence, analyzes it in the cloud, decides on a remediation, and executes the fix on the node itself. The repository today implements both:

- a **structured cloud pipeline** for log ingestion, AI analysis, and remediation decisioning;
- a **node-side repair loop** (`local_agent`) that consumes those decisions, optionally rewrites `configuration.nix` in a shared Git repo, and runs `nixos-rebuild test` before `nixos-rebuild switch`.

The pipeline is built on:

- **Azure Functions** for the four cloud-side services,
- **Azure Service Bus topics** for asynchronous message routing,
- **Azure Blob Storage** for raw log batch transport,
- **Azure Cosmos DB** for audit and per-node state,
- **Azure Key Vault** for secrets,
- **OpenCode** (HTTP API) for cloud-side analysis,
- **Ollama** (HTTP, on the VM host) for node-side config reasoning,
- **Git + `nixos-rebuild`** as the actual repair primitive.

The repository also ships a **simulator** that replaces every Azure dependency with an in-memory fake so the whole pipeline (plus several failure scenarios) can be exercised in a single `uv run` invocation.

---

## 2. The data plane (end-to-end flow)

### 2.1 Log ingestion path

1. `log_service` (on a NixOS VM) tails the systemd journal.
2. It batches journal entries and asks `token_service` (Azure Function) for a short-lived, path-scoped blob SAS URL.
3. `token_service` validates the caller's node identity and shared API key, fetches the storage account key from Key Vault, and returns a write-only SAS URL whose blob path is `logs/<node_id>/<uuid>`.
4. `log_service` uploads the JSON batch payload (`LogBatch`) to that URL in Blob Storage container `logs`.
5. `log_router` (Azure Function, blob trigger on `logs/{name}`) reads the blob, normalizes each journal entry into a `schemas.NormalizedLog`, and publishes one Service Bus message per entry to topic `analysis-input` (subscription `analysis-agent`).
6. `analysis_agent` (Azure Function, Service Bus trigger) consumes each message. It dispatches on the `source` field: `source == "local_agent"` is parsed as `schemas.Observation`; anything else is parsed as `schemas.NormalizedLog`. It builds a prompt, calls OpenCode, normalizes the response, and publishes a `schemas.AnalysisResult` to topic `analysis-results` (subscription `decision-agent`).
7. `decision_agent` (Azure Function, Service Bus trigger) validates the `AnalysisResult`, runs a rule-based decision engine that produces a `schemas.Decision`, upserts an audit document into Cosmos DB, and publishes the `Decision` to topic `final-decisions` (subscription `local-agent`).
8. `local_agent` (daemon on the VM) consumes the `Decision` from the subscription and executes it: either a direct shell command, or the Git-backed `apply_config` repair loop that edits `configuration.nix` and runs `nixos-rebuild test` then `switch`.

### 2.2 Observation path (parallel channel)

`local_agent` continuously samples its own node state (`failed_units`, `restart_counts`, `disk_usage`, `cpu`/`memory` buckets, `uptime`). When the state hash changes meaningfully, it publishes a `schemas.Observation` directly to topic `analysis-input`. From there, the same `analysis_agent → decision_agent → local_agent` chain follows.

### 2.3 Topic / subscription matrix (canonical)

| Topic | Subscription | Producer | Consumer |
|---|---|---|---|
| `analysis-input` | `analysis-agent` | `log_router`, `local_agent` observations | `analysis_agent` |
| `analysis-results` | `decision-agent` | `analysis_agent` | `decision_agent` |
| `final-decisions` | `local-agent` | `decision_agent` | `local_agent` |

### 2.4 Shared message contracts (summary)

All cross-service messages are Pydantic models in `schemas/`. Full details are in [§3](#3-shared-message-contracts--schemas). Quick reference:

- `NormalizedLog` — one journal entry, plus blob provenance.
- `Observation` — `local_agent`'s snapshot of the node.
- `AnalysisContext` — corroborating/contradicting evidence.
- `AnalysisResult` — output of the AI analysis.
- `Decision` — concrete remediation intent for the node.
- `ExecutionResult` — what happened when the decision was executed.
- `NodeState` — generic node health snapshot (used by observation and execution docs).

---

## 3. Shared message contracts — `schemas/`

### Purpose / Why it exists

`schemas` is the shared contract package for the pipeline's cross-service messages and runtime state snapshots. It keeps message shapes consistent across the cloud pipeline, the node-side agent, and the simulator so that services can serialize and validate the same Python objects. The package contains no network code and no Azure bindings; it exists only to define Pydantic models and a shared import surface.

### How it works

The package re-exports the model classes from `src/schemas/__init__.py` so other packages can import them directly from `schemas`. Every model is a `pydantic.BaseModel` with declarative field definitions. Validation happens at model construction time: `Literal` fields restrict allowed values, type hints constrain the JSON shape, and `Field(ge=..., le=...)` constrains numeric bounds. Some models use default values or default factories to keep construction simple and to provide stable fields for downstream consumers. There is no runtime routing, storage, or side-effect logic in this package.

### Module-by-module breakdown

#### `src/schemas/__init__.py`

- **Public surface:** `AnalysisContext`, `AnalysisResult`, `Decision`, `ExecutionResult`, `NodeState`, `NormalizedLog`, `Observation`.
- **Logic:** re-exports the shared models through `__all__` for convenient imports.

#### `src/schemas/observation.py`

- **Public surface:** `Observation` — local-agent observation message.
- **Logic:**
  - `source` is fixed to the literal `"local_agent"`.
  - `observation_type` is limited to `"periodic_state"` or `"state_change"`.
  - `severity_hint` is limited to `"critical"`, `"warning"`, `"info"`.
  - Carries a nested `NodeState` snapshot and a human-readable `message`.

#### `src/schemas/normalized_log.py`

- **Public surface:** `NormalizedLog` — normalized journal entry.
- **Logic:**
  - `source` defaults to `"log_router"`.
  - Contains the node ID, timestamp, message, optional systemd metadata, and the source blob path.
  - `schema_version` defaults to `"1.0"`.

#### `src/schemas/node_state.py`

- **Public surface:** `NodeState` — snapshot of node health and remediation-related state.
- **Logic:**
  - Tracks generation numbers, failed units, restart counts, disk usage, memory/CPU usage, uptime, and the last remediation timestamp.
  - Default factories for `failed_units`, `restart_counts`, and `disk_usage` so those collections are always present and mutable per instance.

#### `src/schemas/analysis_result.py`

- **Public surface:** `AnalysisContext`, `AnalysisResult` — AI/analysis output.
- **Logic:**
  - `AnalysisContext` stores corroborating and contradicting observation summaries plus an optional `NodeState` snapshot.
  - `AnalysisResult.source_type` is constrained to `"log_router"` or `"local_agent"`.
  - `severity` is constrained to `"critical"`, `"warning"`, or `"info"`.
  - `confidence` is bounded to `0.0..1.0`.
  - `context` defaults to an empty `AnalysisContext`.

#### `src/schemas/decision.py`

- **Public surface:** `Decision` — remediation decision message.
- **Logic:**
  - Carries `decision_id`, `analysis_id`, action, command string, severity, confidence, summaries, remediation text, idempotency key, and timestamp.
  - `confidence` is bounded to `0.0..1.0`.
  - `command` defaults to an empty string, which allows non-command decisions to exist (notably `apply_config`).

#### `src/schemas/execution_result.py`

- **Public surface:** `ExecutionResult` — record of an executed remediation action.
- **Logic:**
  - Records the command, exit code, stdout, stderr, success flag, timing, and post-execution `NodeState`.
  - Includes an `observation_summary` so the execution can be linked back to what the node saw.

### Inputs and outputs

- **Input:** Python code in other packages constructs these models from raw JSON, Service Bus bodies, or internal state.
- **Output:** Pydantic model instances and serialized JSON for cross-service messages.
- **External resources touched:** none — this package does not talk to Azure, Key Vault, Service Bus, or Blob Storage.

### Connections

- **Calls:** only `pydantic` and standard library typing/datetime types.
- **Called by:** every other service package and the simulator.
- **Topics/containers/keys:** none directly.

### Decisions and branching

- There is no runtime decision engine. All behavior is declarative validation:
  - `Literal` fields restrict allowed values,
  - `Field(ge=..., le=...)` constrains numeric bounds,
  - default values and default factories define which fields are optional and what shape they take when omitted.
- Any branch-like behavior comes from Pydantic deciding whether an input payload can be parsed into a valid model.

### Configuration

- **Required env vars:** none.
- **Optional env vars:** none.
- **Config loading:** none — the package is pure model definitions.
- **Packaging:** `pyproject.toml` requires Python `>=3.11` and depends only on `pydantic`.

### Known issues / notes

- The package intentionally has no runtime logic, so it will not enforce transport-level concerns like authentication, retries, or deduplication.
- Any compatibility guarantee is only as strong as the services that use these models consistently.
- `schema_version` is present on most models, but version negotiation is not implemented inside this package.

---

## 4. Node-side collector — `log_service/`

### Purpose / Why it exists

`log_service` runs on a NixOS node and turns live `systemd` journal traffic into durable upload batches. It exists because shipping individual log lines would be noisy, chatty, and fragile; batching reduces request overhead and makes retries practical. It tails the journal directly so it can capture what the node is doing in real time, and it spools failed batches locally so transient network, token-service, or blob-upload failures do not lose evidence.

### How it works

- `main()` parses the optional `-s/--services` CLI flag and uses it to filter journal entries by `_SYSTEMD_UNIT=<name>.service`.
- `load_config()` reads runtime settings from environment variables and builds a `LogServiceConfig` instance.
- A `BatchUploader` is created with that config, and the process immediately calls `uploader.flush()` once at startup to replay any existing spooled payloads before collecting new data.
- A `systemd.journal.Reader()` is configured at `LOG_INFO` level, positioned at the current tail via `seek_tail()` and `get_previous()`, and then polled with `select.poll()`.
- On each journal append event, `main()` iterates through entries, prints the entry message to stdout via `process_entry()`, and passes a raw `dict(entry)` to `BatchUploader.add_entry()`.
- Batches are flushed in three cases: when the batch size threshold is reached, when the flush interval has elapsed, or during shutdown.
- `BatchUploader.flush()` first tries to drain any locally spooled payloads, then uploads the current in-memory batch.
- If all retries for the current payload fail, that payload is written to the spool directory as a JSON file and retried on the next flush.
- Shutdown is handled with SIGINT and SIGTERM; `signal_handler()` flips the global `running` flag and the loop performs a final flush before exiting.

### Module-by-module breakdown

#### `src/log_service/__init__.py`

- **Public surface:** none.
- **Logic:** empty package marker.

#### `src/log_service/main.py`

- **Public surface:**
  - `parse_args()` — parses command-line options.
  - `signal_handler(signum, frame)` — handles SIGINT/SIGTERM and stops the main loop.
  - `process_entry(entry, *, config)` — prints the current log message and realtime timestamp if `MESSAGE` is present.
  - `main()` — orchestrates journal reading, buffering, flush timing, and shutdown.
- **Logic:**
  - `parse_args()` defines a single option: `-s/--services`, `nargs="+"`, to filter by one or more service names.
  - `signal_handler()` maps the signal number to a name with `signal.Signals(signum).name` and sets the module-level `running` flag to `False`.
  - `process_entry()` reads `MESSAGE` from the journal entry, returns early if it is empty, and prints `__REALTIME_TIMESTAMP` plus the message text. The `config` parameter is accepted but not used.
  - `main()` calls `load_config()`, builds `BatchUploader(config=config)`, and registers SIGINT/SIGTERM handlers.
  - The journal reader is configured with `journal.LOG_INFO`; when service filters are provided, it adds one `_SYSTEMD_UNIT=<service>.service` match per service name.
  - The poll loop uses `timeout_ms = max(int(config.flush_interval_seconds * 1000), 1000)` so it waits at least one second between poll checks.
  - On a timeout, `main()` calls `uploader.flush()` only if `uploader.flush_due()` is true.
  - On `journal.APPEND`, each entry is processed, buffered, and flushed immediately if `uploader.add_entry()` returns `True`.
  - Any exception while processing a single journal entry is caught and printed as `failed to buffer log entry: ...`; the loop continues.
  - `uploader.flush()` is called once before the loop and once after the loop so startup spooling and shutdown buffering are both handled.

#### `src/log_service/config.py`

- **Public surface:**
  - `LogServiceConfig(BaseModel)` — validated runtime configuration.
  - `load_config(env=None) -> LogServiceConfig` — reads env vars.
- **Logic:**
  - Required: `TOKEN_SERVICE_URL` and `NODE_ID`.
  - Optional: `NODE_API_KEY` (if absent, no `X-API-Key` header is sent).
  - Tunables and defaults:
    - `UPLOAD_TIMEOUT_SECONDS=10`
    - `BATCH_SIZE=100`
    - `FLUSH_INTERVAL_SECONDS=30`
    - `MAX_RETRIES=3`
    - `RETRY_BASE_DELAY_SECONDS=1`
    - `SPOOL_DIRECTORY=/tmp/phoe-nix-log-service`
  - Validation: `upload_timeout_seconds>0`, `batch_size>=1`, `flush_interval_seconds>0`, `1<=max_retries<=10`, `retry_base_delay_seconds>0`.
  - `load_config()` accepts an explicit `env` mapping for testability; otherwise it uses `os.environ`.

#### `src/log_service/models.py`

- **Public surface:**
  - `StorageTokenResponse(BaseModel)` — typed `token_service` response.
  - `LogBatch(BaseModel)` — JSON payload shape uploaded to blob storage.
- **Logic:**
  - `StorageTokenResponse` contains `sas_url`, `blob_path`, and `expires_at`.
  - `LogBatch` contains `node_id`, `entries` (`Field(default_factory=list)`), and `uploaded_at` (UTC timestamp at serialization time).

#### `src/log_service/storage.py`

- **Public surface:**
  - `build_log_payload(entries, *, node_id) -> bytes` — converts buffered journal entries into JSON bytes.
  - `upload_log_payload(sas_url, payload, *, timeout_seconds) -> None` — uploads a payload to blob storage.
- **Logic:**
  - `build_log_payload()` creates a `LogBatch` with the given `node_id`, copies each entry into a plain `dict` with stringified keys, stamps `uploaded_at = datetime.now(UTC)`, and returns `batch.model_dump_json().encode("utf-8")`.
  - `upload_log_payload()` has two code paths:
    - If `sas_url` starts with `mockblob+http://` or `mockblob+https://`, it strips the `mockblob+` prefix, builds a `urllib.request.Request(..., method="PUT")`, and uploads through `urllib.request.urlopen(...)`. This is the local/test path.
    - Otherwise it uses `azure.storage.blob.BlobClient.from_blob_url(sas_url)` and calls `upload_blob(payload, overwrite=True, timeout=timeout_seconds)`. This is the real Azure Blob Storage path.
  - The module does not perform retry logic or spool management; it only serializes and uploads.

#### `src/log_service/uploader.py`

- **Public surface:**
  - `BatchUploader` — buffering, retry, and spooling coordinator.
  - `BatchUploader.add_entry(entry) -> bool` — appends one journal entry to the in-memory buffer; returns `True` when the batch threshold is reached.
  - `BatchUploader.flush_due() -> bool` — reports whether a buffered batch is old enough to flush.
  - `BatchUploader.flush() -> bool` — drains spooled payloads and uploads the current batch.
  - `BatchUploader.load_spooled_payloads() -> list[dict]` — reads spooled JSON payloads for inspection.
- **Logic:**
  - Constructor stores `config`, `token_requester`, `payload_uploader`, `sleep`, and `monotonic`; initializes `buffer`; records `last_flush_at`; ensures the spool directory exists.
  - `add_entry()` returns `True` when `len(self.buffer) >= self.config.batch_size`; `main()` uses that return value to trigger an immediate flush.
  - `flush_due()` returns `True` only when the buffer is non-empty and the elapsed monotonic time exceeds `config.flush_interval_seconds`.
  - `flush()` calls `_drain_spool()` first, then uploads the current buffer if any entries remain.
  - `_upload_with_retry()` performs token request + upload with exponential backoff:
    - initial delay = `config.retry_base_delay_seconds`
    - delay doubles after each failed attempt
    - attempts run from `1` to `config.max_retries`
  - It catches all exceptions from token acquisition or upload; there is no separate 4xx/5xx branch.
  - On the final failed attempt, `_upload_with_retry()` returns `False` and `flush()` spools the current payload.
  - `_spool_payload()` writes one file per failed batch using `uuid4()` and the name pattern `<uuid>.json` inside `config.spool_directory`.
  - `_drain_spool()` iterates `sorted(self.spool_directory.glob("*.json"))`, reads each file, tries to upload it with the same retry logic, and stops at the first failure so later spool files are not skipped ahead of the failed one.
  - Successful replay deletes the spool file with `unlink()`.
  - `load_spooled_payloads()` reads remaining spool files as JSON but does not remove them.

#### `src/log_service/token_client.py`

- **Public surface:**
  - `TokenServiceError(Exception)` — wrapper for token-service failures.
  - `build_token_request_headers(node_id, node_api_key) -> dict` — builds the request headers.
  - `parse_storage_token_response(body) -> StorageTokenResponse` — parses and validates the response.
  - `request_storage_token(token_service_url, *, node_id, node_api_key, timeout_seconds) -> StorageTokenResponse` — performs the HTTP request.
- **Logic:**
  - `build_token_request_headers()` always sends `Content-Type: application/json` and `X-Node-ID: <node_id>`. If `node_api_key` is not `None`, it also sends `X-API-Key: <value>`.
  - `request_storage_token()` POSTs JSON body `{"node_id": node_id}` to the configured URL.
  - The response body is parsed as JSON and validated into `StorageTokenResponse`.
  - `HTTPError` becomes `TokenServiceError(f"token service returned HTTP {error.code}")`.
  - `URLError` becomes `TokenServiceError("token service is unreachable")`.

### Inputs and outputs

- **Inputs:** live `systemd` journal entries; optional service names from `-s/--services`; env vars for token-service URL, node identity, retry policy, timeout, and spool directory.
- **Outputs:** JSON batch payload (`node_id`, `entries`, `uploaded_at`); uploads to Azure Blob Storage using a SAS URL; locally spooled `*.json` batch files under `config.spool_directory` when retries fail; console output for live journal messages, startup/shutdown notices, and entry-buffering errors.

### Connections

- **Calls:** `token_service` over HTTP, Azure Blob Storage via the SAS URL returned by `token_service`, the local filesystem for spool persistence and replay.
- **Called by:** the node-side `systemd` service that launches `log_service` on a NixOS node; repo validation / live-operator tooling such as `scripts/check-deployment.sh` and `scripts/run-live-azure-vm-e2e.py` when they exercise the node path.
- **Containers/keys:** blob container `logs`; blob path shape `<node_id>/<uuid>` as returned by `token_service`; optional identity header `X-API-Key` from `NODE_API_KEY`.

### Decisions and branching

`main()` decides when to flush based on three conditions:

1. `BatchUploader.add_entry()` returns `True` because the in-memory batch reached `BATCH_SIZE`.
2. `BatchUploader.flush_due()` returns `True` because the flush interval elapsed while the buffer is non-empty.
3. Shutdown is requested by SIGINT/SIGTERM.

`BatchUploader.flush()` always drains spooled batches first, then tries the current batch. `_upload_with_retry()` retries any exception from token acquisition or blob upload up to `MAX_RETRIES` times, sleeping `RETRY_BASE_DELAY_SECONDS` and doubling the delay after each failure. There is no explicit 4xx-vs-5xx branch. If retries are exhausted, the current payload is written to the spool directory and removed from memory. `_drain_spool()` processes spool files in sorted filename order and stops at the first failed replay.

### Configuration

- **Required env vars:** `TOKEN_SERVICE_URL`, `NODE_ID`.
- **Optional env vars:** `NODE_API_KEY`, `UPLOAD_TIMEOUT_SECONDS`, `BATCH_SIZE`, `FLUSH_INTERVAL_SECONDS`, `MAX_RETRIES`, `RETRY_BASE_DELAY_SECONDS`, `SPOOL_DIRECTORY`.
- **Package metadata:** `pyproject.toml` exposes the console script `log_service = log_service.main:main`; dependencies are `azure-storage-blob`, `pydantic`, and `systemd-python`; targets Python `>=3.14` and uses `hatchling`.

### Known issues / notes

- `NODE_ID` is mandatory; `load_config()` reads it directly from the environment and will fail fast if it is absent.
- `NODE_API_KEY` is optional, but when set the service always sends it to `token_service` as `X-API-Key`.
- `process_entry()` only prints the journal message; it does not enrich or transform the payload.
- The startup flush replays any pre-existing spool files before new journal entries are uploaded.
- The tests confirm: threshold-triggered flush, replay-before-current-buffer ordering, spool-on-exhaustion, token request header construction, typed token response parsing, payload serialization, and `mockblob+http://` upload support for local/test environments.
- **Cloud-connectivity gap:** the rendered VM env currently omits `NODE_ID`, so the live `log_service` path will fail at startup until that env is fixed (see `connectivity-analysis.md`).

---

## 5. Cloud-side token authorization — `token_service/`

### Purpose / Why it exists

`token_service` exists to issue short-lived, write-only SAS URLs for node log uploads. It is the authorization gate between a node-side uploader and Azure Blob Storage: the service validates the caller, reads the storage account key from Key Vault, and returns a blob-scoped upload URL instead of exposing long-lived storage credentials. In the current pipeline it is the first cloud-side service in the log ingestion path, and it only solves upload authorization for log batches. The proof-of-concept repair direction does not change this service's role; it remains a least-privilege credential broker for log upload.

### How it works

- Azure Functions binds `src/token_service/main.py` through `src/token_service/function.json` as an HTTP POST endpoint at route `/token`.
- `main(req)` loads configuration, reads the raw request body and headers, and passes them to `handle_token_request()`.
- `handle_token_request()` parses the JSON request body into `TokenRequest`; the body must contain a non-empty `node_id`.
- It calls `authenticate_node_request()` to verify that the request headers and body agree on the node identity and that the shared API key is correct.
- If authentication succeeds, it reads the storage account key from Key Vault via `read_secret_value()`.
- With the account key in hand, it calls `issue_upload_token()` to build a unique blob path under `logs/<node_id>/<uuid>`, generate a write-only SAS token, and return a `TokenResponse`.
- The response is serialized as JSON with `Content-Type: application/json`.
- Error mapping is explicit: `401` for auth failures, `400` for malformed requests, `500` for missing config or unexpected failures, and the generic error path avoids leaking internal details.
- `run_cli()` is a small local smoke-test helper that builds a sample request using `local-dev` and prints the JSON response body.

### Module-by-module breakdown

#### `src/token_service/__init__.py`

- **Public surface:** none (`__all__ = []`).
- **Logic:** package marker only.

#### `src/token_service/main.py`

- **Public surface:**
  - `main(req: func.HttpRequest) -> func.HttpResponse` — Azure Functions entrypoint.
  - `run_cli() -> None` — local smoke-test command.
- **Logic:**
  - Calls `load_config()` once per invocation.
  - Forwards raw request bytes and headers to `handle_token_request()`.
  - Converts the internal tuple response into a real `func.HttpResponse`.
  - The CLI path builds a synthetic request with matching `x-node-id` and `x-api-key` headers.

#### `src/token_service/app.py`

- **Public surface:**
  - `HttpResult` — tuple subclass with `status_code`, `body`, and `headers` properties.
  - `json_response(status_code, payload)` — wraps a Pydantic response model as a JSON HTTP result.
  - `parse_json_body(raw_body)` — decodes and parses the incoming request body.
  - `handle_token_request(...)` — orchestrates validation, auth, Key Vault access, and SAS issuance.
- **Logic:**
  - Rejects empty bodies before JSON parsing.
  - Validates the request body against `TokenRequest` before any side effects.
  - Uses dependency injection for `read_storage_account_key` and `issue_token` so the core flow stays testable.
  - Maps `AuthenticationError → 401`, `ValueError → 400`, `KeyError → 500` with a missing-config message, and any other exception to a generic `500`.
  - Keeps side effects at the edges: parsing/auth first, secret lookup second, SAS generation last.

#### `src/token_service/config.py`

- **Public surface:**
  - `TokenServiceConfig` — Pydantic model for service configuration.
  - `load_config(env=None) -> TokenServiceConfig` — loads config from `os.environ` or a supplied mapping.
- **Logic:**
  - Required variables: `STORAGE_ACCOUNT_NAME`, `KEYVAULT_NAME`, `NODE_API_KEY`.
  - Optional defaults: `LOGS_CONTAINER_NAME="logs"`, `STORAGE_ACCOUNT_KEY_SECRET="StorageAccountKey"`, `TOKEN_TTL_MINUTES="5"`.
  - `token_ttl_minutes` is constrained to `1..60`.
  - Invalid `TOKEN_TTL_MINUTES` values fail immediately at the `int()` cast.

#### `src/token_service/models.py`

- **Public surface:**
  - `TokenRequest` — body with `node_id: str` (at least one character).
  - `TokenResponse` — success payload with `sas_url`, `blob_path`, `expires_at`.
  - `ErrorResponse` — error payload with `error: str`.
- **Logic:** plain Pydantic containers with no custom methods.

#### `src/token_service/auth.py`

- **Public surface:**
  - `AuthenticationError` — raised on auth failure.
  - `authenticate_node_request(headers, *, expected_api_key, requested_node_id) -> None` — validates node identity and API key.
- **Logic:**
  - Normalizes header keys to lowercase.
  - Requires `x-node-id` to match the request body `node_id` exactly.
  - Requires `x-api-key` to match the configured shared key.
  - Uses a very simple POC authentication model with no per-node token issuance or signature validation.

#### `src/token_service/keyvault.py`

- **Public surface:**
  - `build_vault_url(vault_name)` — formats the Key Vault URL.
  - `read_secret_value(vault_name, secret_name)` — reads a secret using `DefaultAzureCredential`.
- **Logic:** uses `azure.identity.DefaultAzureCredential()` so the same code can use managed identity in Azure and local developer auth when available; creates an `azure.keyvault.secrets.SecretClient` and returns the secret value.

#### `src/token_service/sas_generator.py`

- **Public surface:**
  - `build_blob_name(node_id, blob_id)` — builds `<node_id>/<uuid>`.
  - `build_blob_path(container_name, blob_name)` — builds `<container>/<blob_name>`.
  - `build_blob_url(account_name, container_name, blob_name)` — builds the blob HTTPS URL.
  - `build_upload_sas_token(...)` — creates a write-only SAS token.
  - `issue_upload_token(...)` — returns the full `TokenResponse`.
- **Logic:**
  - Generates the blob name from the node ID and a UUID, so every request gets a unique target blob.
  - Computes `expires_at` as `now + token_ttl_minutes`.
  - Generates SAS with `BlobSasPermissions(write=True)` only; the token is intentionally write-scoped.
  - Returns the final URL as `https://<account>.blob.core.windows.net/<container>/<blob>?<sas>`.

### Inputs and outputs

- **Input:** HTTP POST to `/token` with JSON body `{"node_id": "<node>"}` and headers `x-node-id` and `x-api-key`.
- **Output:** JSON `TokenResponse` with `sas_url`, `blob_path`, and `expires_at`, or JSON `ErrorResponse` on failure.
- **External resources touched:** Azure Key Vault for `StorageAccountKey`; Blob Storage is only referenced through SAS generation, not accessed directly.

### Connections

- **Calls:** Azure Key Vault via `azure.keyvault.secrets.SecretClient`; Azure Storage SDK via `generate_blob_sas()`; Azure Identity via `DefaultAzureCredential()`.
- **Called by:** `log_service` during log upload authorization; the package's local smoke-test CLI.
- **Topics/containers/keys:** blob container `logs` by default; Key Vault secret `StorageAccountKey` by default; env vars `STORAGE_ACCOUNT_NAME`, `KEYVAULT_NAME`, `NODE_API_KEY`, `LOGS_CONTAINER_NAME`, `STORAGE_ACCOUNT_KEY_SECRET`, `TOKEN_TTL_MINUTES`.

### Decisions and branching

The service makes decisions in three places:

1. `TokenRequest` validation decides whether the body is well-formed enough to proceed.
2. `authenticate_node_request()` decides whether the caller's identity is accepted.
3. `handle_token_request()` branches on `AuthenticationError`, `ValueError`, `KeyError`, and a generic catch-all to choose the HTTP status code.

`issue_upload_token()` does not branch on policy beyond TTL and blob identity; it deterministically builds a single write-only SAS for one blob.

### Configuration

- **Required env vars:** `STORAGE_ACCOUNT_NAME`, `KEYVAULT_NAME`, `NODE_API_KEY`.
- **Optional with defaults:** `LOGS_CONTAINER_NAME=logs`, `STORAGE_ACCOUNT_KEY_SECRET=StorageAccountKey`, `TOKEN_TTL_MINUTES=5`.
- **Config loading:** `load_config()` reads from `os.environ` by default, but accepts a mapping for tests.
- **Packaging:** `pyproject.toml` declares dependencies on `azure-functions`, `azure-identity`, `azure-keyvault-secrets`, `azure-storage-blob`, and `pydantic`; `flake.nix` provides a dev shell with Python 3.11, `uv`, and `git`.

### Known issues / notes

- Authentication is intentionally simple and uses a shared API key plus a header/body node match; it is not per-node cryptographic authentication.
- `load_config()` casts `TOKEN_TTL_MINUTES` with `int()` and does not guard against invalid values.
- Missing required environment variables fail fast during config loading.
- The service only grants write access to one blob; it does not upload data itself.

---

## 6. Cloud-side log normalization — `log_router/`

### Purpose / Why it exists

`log_router` exists to convert raw uploaded log batches into normalized analysis messages. It is the bridge between Azure Blob Storage and the Service Bus analysis pipeline: the service is triggered by a blob upload, parses the batch, and publishes one `NormalizedLog` message per journal entry to topic `analysis-input`. Its job is to make the blob payload easier for downstream agents to consume and to preserve blob provenance on each normalized record. It does not perform AI analysis or remediation; it only transforms and forwards data.

### How it works

- Azure Functions binds `src/log_router/main.py` through `src/log_router/function.json` as a blob trigger on `logs/{name}` using `LOGS_STORAGE_CONNECTION`.
- When a blob is created or updated under that path, Azure passes an `InputStream` into `main(inputblob)`.
- `main()` reads `SERVICEBUS_CONNECTION` and `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME` from the environment, reads the blob bytes, and passes them to `publish_messages()`.
- `publish_messages()` calls `normalize_blob()` to convert the raw payload into a list of `NormalizedLog` objects.
- It then opens a `ServiceBusClient` from the connection string, gets a topic sender, and sends one `ServiceBusMessage` per normalized log.
- Each Service Bus message uses JSON content, and the `message_id` is derived from the blob path plus the entry index (`<blob_path>:<index>`).
- `run_cli()` is a local smoke-test helper that builds a one-entry sample payload and prints the normalized JSON output.
- Any parsing or normalization error propagates out of the function and causes the Azure Function invocation to fail.

### Module-by-module breakdown

#### `src/log_router/__init__.py`

- **Public surface:** none (`__all__ = []`).
- **Logic:** package marker only.

#### `src/log_router/main.py`

- **Public surface:**
  - `publish_messages(*, connection_string, topic_name, blob_path, payload) -> int` — normalizes a blob payload and publishes Service Bus messages.
  - `main(inputblob: func.InputStream) -> None` — Azure Functions blob-trigger entrypoint.
  - `run_cli() -> None` — local smoke-test command.
- **Logic:**
  - Reads the full blob stream into memory with `inputblob.read()`.
  - Relies on environment variables rather than a config module.
  - Uses `ServiceBusClient.from_connection_string()` for direct Service Bus publishing.
  - Creates a new `ServiceBusMessage` for each normalized log with `content_type="application/json"`.
  - Uses a deterministic `message_id` based on blob path and index to help with downstream deduplication.

#### `src/log_router/normalizer.py`

- **Public surface:**
  - `parse_blob_payload(payload: bytes) -> tuple[str, list[dict]]` — parses the top-level blob JSON.
  - `timestamp_from_journal(raw_timestamp) -> datetime` — converts journal timestamps to UTC datetimes.
  - `normalize_entry(entry, *, node_id, blob_path) -> NormalizedLog` — converts one raw journal entry.
  - `normalize_blob(payload: bytes, *, blob_path: str) -> list[NormalizedLog]` — normalizes the whole batch.
- **Logic:**
  - Requires top-level JSON with `node_id` and `entries`.
  - `entries` must be a list; otherwise the function raises `ValueError`.
  - Converts `__REALTIME_TIMESTAMP` either from microseconds since epoch or from ISO-8601 text with `Z` rewritten to `+00:00`.
  - Requires a non-empty `MESSAGE` field on each journal entry.
  - Converts `PRIORITY` to `int` when present.
  - Copies `_SYSTEMD_UNIT`, `_HOSTNAME`, and `SYSLOG_IDENTIFIER` when present; missing values become `None`.
  - Attaches the blob path to every normalized record so downstream services can trace provenance.

### Inputs and outputs

- **Input:** Azure Blob Storage trigger input from `logs/{name}` containing a JSON batch payload.
- **Output:** one `ServiceBusMessage` per normalized journal entry on the `analysis-input` topic.
- **Intermediate data:** `NormalizedLog` objects with node ID, timestamp, message, unit, priority, hostname, source identifier, and blob path.
- **External resources touched:** the source blob via the trigger binding and Azure Service Bus via the output publisher.

### Connections

- **Calls:** Azure Service Bus via `ServiceBusClient` and `ServiceBusMessage`; the shared `schemas.NormalizedLog` model.
- **Called by:** Azure Blob Storage through the blob trigger binding; the package's local smoke-test CLI.
- **Topics/containers/keys:** blob trigger path `logs/{name}`; Service Bus topic `analysis-input`; env vars `LOGS_STORAGE_CONNECTION`, `SERVICEBUS_CONNECTION`, `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME`.

### Decisions and branching

The main branching happens in normalization:

- if `entries` is not a list, the blob is rejected;
- if a journal entry has no `MESSAGE`, that entry causes a failure;
- timestamps are parsed as either microseconds or ISO-8601 text;
- optional fields are preserved when present and set to `None` when absent.

There is no retry policy or dead-letter handling in this module; any exception is left to the Azure Functions runtime and Service Bus infrastructure.

### Configuration

- **Required env vars:** `SERVICEBUS_CONNECTION`, `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME`, `LOGS_STORAGE_CONNECTION`.
- **Optional with defaults:** none in the Python code.
- **Config loading:** direct `os.environ[...]` access in `main()`; missing values raise `KeyError` immediately.
- **Packaging:** `pyproject.toml` declares dependencies on `azure-functions`, `azure-servicebus`, `pydantic`, and the local `schemas` package via a path source.

### Known issues / notes

- A malformed blob payload or a single malformed entry raises an exception for the whole function invocation. There is no partial success or per-entry quarantine.
- `normalize_blob()` assumes the blob body is JSON and that the payload shape matches the uploader's batch format.
- The service does not perform any auth checks of its own; trust is delegated to the storage upload path and the trigger binding.
- **Cloud-connectivity gap:** there are no structured `analysis_failed` events emitted for malformed blobs; failures rely on Azure retry/DLQ behavior (see `connectivity-analysis.md`).

---

## 7. Cloud-side AI analysis — `analysis_agent/`

### Purpose / Why it exists

`analysis_agent` is the cloud-side interpretation stage in the pipeline. It sits between normalized node evidence (`log_router` output or `local_agent` observations) and downstream remediation decisions, turning raw operational data into a structured `AnalysisResult` that the next stage can consume. Its job is to centralize LLM-based diagnosis, keep the rest of the system schema-driven, and preserve the original message identity so the analysis can be audited and correlated later. The current implementation is still structured-output oriented: it asks OpenCode for JSON and then normalizes that response into the shared schema.

### How it works

The Azure Function is triggered by Service Bus messages on topic `analysis-input`, subscription `analysis-agent` (`function.json`). The Azure Functions host calls `main(msg)` in `main.py` with the incoming `func.ServiceBusMessage`.

`main()` loads runtime settings with `load_config()`, extracts the incoming message id from Service Bus metadata (`msg.metadata["MessageId"]` when available, otherwise `msg.message_id`, otherwise `"unknown"`), and hands the raw message body to `analyze_message()`.

`analyze_message()` performs the core pipeline:

1. `parse_message()` decodes the JSON body and branches on the payload `source` field.
   - `source == "local_agent"` → validate as `schemas.Observation`
   - anything else → validate as `schemas.NormalizedLog`
2. `build_prompt()` in `prompt_builder.py` formats the schema instance into a model prompt.
3. `read_secret_value()` in `keyvault.py` reads the OpenCode API key from Azure Key Vault using `DefaultAzureCredential`.
4. `call_opencode_api()` in `ai_client.py` sends the prompt to the OpenCode HTTP API.
5. `parse_analysis_response()` converts the model response into a validated `AnalysisResult`.

The OpenCode request path is deliberately defensive:

- If the configured API URL ends in `/chat/completions`, the request payload is OpenAI-compatible (`model` + `messages` with a system prompt and a user prompt).
- Otherwise it sends a simpler `{ "prompt": ... }` payload.
- Headers always include `Content-Type: application/json`, `Authorization: Bearer ...`, `Accept: application/json`, and a custom `User-Agent: phoe-nix-analysis-agent/0.1`.

Response handling also has a fallback path:

- `extract_response_text()` tries to peel text out of common response envelopes (`choices[0].message.content`, `response`, `output`, `output_text`, `content`, `text`).
- `parse_analysis_response()` first expects JSON from the model.
- If the response is not valid JSON, it falls back to a text-derived `AnalysisResult` with warning severity and generic fields derived from the raw text.
- Missing fields are filled in before Pydantic validation.
- If the final payload still fails schema validation, `OpenCodeError` is raised.

After analysis succeeds, `main()` publishes a single Service Bus message to `analysis-results` using `publish_analysis_result()`. The outgoing message keeps the original message id, uses `content_type=application/json`, and sets `application_properties.message_kind=analysis_result`.

### Module-by-module breakdown

#### `analysis_agent/__init__.py`

- **Public surface:** none.
- **Logic:** package initializer only; `__all__ = []`.

#### `analysis_agent/config.py`

- **Public surface:**
  - `AnalysisAgentConfig` — Pydantic model for runtime configuration.
  - `load_config(env=None)` — reads settings from `os.environ` or an injected mapping.
- **Logic:**
  - Required: `SERVICEBUS_CONNECTION` and `KEYVAULT_NAME`.
  - `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME=analysis-results`.
  - `OPENCODE_API_KEY_SECRET=OpenCodeApiKey`.
  - `OPENCODE_API_URL=https://opencode.ai/zen/go/v1/chat/completions`.
  - `OPENCODE_MODEL=deepseek-v4-flash`.
  - `AI_TIMEOUT_SECONDS > 0` (validated by `Field(gt=0)`).

#### `analysis_agent/keyvault.py`

- **Public surface:**
  - `build_vault_url(vault_name)` — builds `https://<vault_name>.vault.azure.net`.
  - `read_secret_value(vault_name, secret_name)` — fetches a Key Vault secret value.
- **Logic:** uses `SecretClient` with `DefaultAzureCredential`; no caching — each call creates a client and performs a lookup.

#### `analysis_agent/prompt_builder.py`

- **Public surface:**
  - `build_log_prompt(message)` — prompt for `NormalizedLog` inputs.
  - `build_observation_prompt(message)` — prompt for `Observation` inputs.
  - `build_prompt(message)` — dispatches to the correct prompt based on message type.
- **Logic:**
  - Both prompts serialize the input with `model_dump(mode="json")`, pretty-print with `indent=2`, and sort keys.
  - The log prompt is more constrained: it names allowed values for `error_type`, `severity`, and `suggested_action`, and explicitly asks for `analysis_text` plus a concrete `remediation_hint`.
  - The observation prompt focuses on degraded state, recurring failures, and resource pressure, and tells the model to return `no_action` when the observation is informational only.
  - The prompt text is designed to produce a single JSON object, not markdown.

#### `analysis_agent/ai_client.py`

- **Public surface:**
  - `OpenCodeError` — raised when the final parsed analysis payload cannot be validated.
  - `build_request_payload(api_url, model, prompt)` — builds the HTTP request body.
  - `build_request_headers(api_key)` — builds the OpenCode request headers.
  - `extract_response_text(response_body)` — extracts model text from common JSON envelopes.
  - `strip_markdown_fence(raw_text)` — removes surrounding triple-backtick fences.
  - `parse_json_object(raw_text)` — parses a JSON object from model output.
  - `call_opencode_api(api_url, api_key, model, prompt, timeout_seconds, urlopen=request.urlopen)` — performs the HTTP POST.
  - `normalize_confidence(value)` — coerces confidence labels/numbers into `0.0..1.0`.
  - `parse_analysis_response(raw_response, node_id, original_message_id, source_type, fallback_unit, now_factory=None)` — converts model output into `AnalysisResult`.
- **Logic:**
  - `_uses_chat_completions()` switches the payload format when the URL ends with `/chat/completions`.
  - `build_request_payload()` embeds a system instruction that asks for exactly one JSON object and no markdown.
  - `extract_response_text()` understands multiple wrapper shapes, including OpenAI-style `choices[0].message.content`.
  - `parse_analysis_response()` has a text fallback when the response is not JSON: it synthesizes a warning-level analysis from the raw text.
  - It fills missing values for `analysis_text`, `root_cause`, `suggested_action`, `remediation_hint`, `schema_version`, `error_type`, `severity`, `node_id`, `original_message_id`, `source_type`, `affected_unit`, `confidence`, and `timestamp` before final validation.
  - `confidence` accepts numeric values or labels like `high`, `medium`, and `low`.

#### `analysis_agent/message_handler.py`

- **Public surface:**
  - `AnalysisInput` — dataclass that captures the parsed input type and fallback metadata.
  - `parse_message(raw_body)` — decodes and validates the incoming Service Bus payload.
  - `analyze_message(raw_body, message_id, config, read_secret_value, model_caller=call_opencode_api)` — orchestrates parsing, prompting, model call, and schema normalization.
- **Logic:**
  - `parse_message()` uses the payload `source` field to branch:
    - `local_agent` → parse as `Observation`
    - otherwise → parse as `NormalizedLog`
  - For `NormalizedLog`, it carries `unit` forward as `fallback_unit` so the downstream analysis can preserve the affected service name.
  - `analyze_message()` is dependency-injected for testing: `read_secret_value` and `model_caller` can be replaced.
  - The function never writes to Service Bus itself; it only returns the validated `AnalysisResult`.

#### `analysis_agent/main.py`

- **Public surface:**
  - `publish_analysis_result(connection_string, topic_name, message_id, result_body)` — Service Bus topic publisher.
  - `main(msg)` — Azure Function entrypoint.
  - `run_cli()` — prints a sample message for local inspection.
- **Logic:**
  - `publish_analysis_result()` uses `ServiceBusClient.from_connection_string()` and a topic sender.
  - The outgoing Service Bus message is marked `application_properties={"message_kind": "analysis_result"}`.
  - `main()` preserves the original message id from the incoming message metadata when available.
  - `run_cli()` is only a helper for package/script usage; it does not execute the pipeline.

### Inputs and outputs

- **Input topic:** `analysis-input` / subscription `analysis-agent`.
- **Accepted input shapes:**
  - `schemas.NormalizedLog` from `log_router` (required: `node_id`, `timestamp`, `message`, `blob_path`; common: `unit`, `priority`, `hostname`, `source` defaulting to `log_router`).
  - `schemas.Observation` from `local_agent` (required: `source` `local_agent`, `node_id`, `observation_type`, `timestamp`, `node_state`, `message`, `severity_hint`).
- **Output topic:** `analysis-results`.
- **Output body shape:** `schemas.AnalysisResult` JSON with `schema_version`, `node_id`, `original_message_id`, `source_type`, `error_type`, `severity`, `root_cause`, `suggested_action`, `affected_unit`, `confidence`, `context`, `analysis_text`, `remediation_hint`, `raw_ai_response`, `timestamp`. The output message keeps the original input message id and sets `content_type=application/json` plus `message_kind=analysis_result`.

### Connections

- **Calls:** Azure Key Vault (`KEYVAULT_NAME` + `OpenCodeApiKey`), OpenCode HTTP API, Azure Service Bus topic sender for `analysis-results`.
- **Called by:** Azure Functions Service Bus trigger on `analysis-input`, and the simulator path that calls `analyze_message()` directly.
- **Touches:** input subscription `analysis-agent`, output topic `analysis-results`, Key Vault secret `OpenCodeApiKey`.

### Decisions and branching

- Branches on `source`:
  - `local_agent` → parse as `Observation`
  - everything else → parse as `NormalizedLog`
- If the model returns non-JSON text, the code synthesizes a fallback analysis instead of failing immediately.
- If the final payload cannot satisfy `AnalysisResult`, the function raises `OpenCodeError`.
- The request header uses a custom `User-Agent` string (`phoe-nix-analysis-agent/0.1`) rather than relying on the default urllib agent.
- There is no retry loop or backoff logic in this module; failures are left to the Azure Functions / Service Bus runtime.

### Configuration

- **Required env vars:** `SERVICEBUS_CONNECTION`, `KEYVAULT_NAME`.
- **Optional with defaults:** `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME=analysis-results`, `OPENCODE_API_KEY_SECRET=OpenCodeApiKey`, `OPENCODE_API_URL=https://opencode.ai/zen/go/v1/chat/completions`, `OPENCODE_MODEL=deepseek-v4-flash`, `AI_TIMEOUT_SECONDS=30`.
- **Config loading:** `load_config(env=None)`, reads from the provided mapping or `os.environ`.

### Known issues / notes

- The module is tolerant of non-JSON model output, but not of a final payload that still fails schema validation.
- Anything not explicitly marked `source == "local_agent"` is treated as a log payload.
- `message_id` falls back to `"unknown"` if Azure Functions metadata is missing.
- The service is intentionally not a standalone daemon; it runs only inside the Azure Functions host.
- The package exposes a CLI entrypoint (`analysis_agent = analysis_agent.main:run_cli`) for local inspection, not for production execution.
- **Cloud-connectivity gap:** no structured `analysis_failed` events are emitted for invalid AI responses; failures rely on Azure retry/DLQ behavior.

---

## 8. Cloud-side decision policy — `decision_agent/`

### Purpose / Why it exists

`decision_agent` is the policy-to-action translation stage of the cloud pipeline. It consumes `AnalysisResult` messages, turns them into a `Decision`, stores that decision in Cosmos DB for auditability, and publishes the final remediation intent to `local_agent`. In the current code, the module is still command-oriented (`rollback`, `rebuild`, `restart_service`, `no_action`), but it already carries the information needed for the next stage to move toward config-level repair. That makes it the handoff point between analysis and execution.

### How it works

The Azure Function is triggered by Service Bus messages on topic `analysis-results`, subscription `decision-agent` (`function.json`). The Azure Functions host calls `main(msg)` in `main.py`.

`main()` loads configuration with `load_config()`, decodes the Service Bus body as UTF-8, parses it as JSON, and validates it against `schemas.AnalysisResult`. It then passes the result to `process_analysis_result()`.

`process_analysis_result()` is a thin orchestrator:

1. `build_decision()` in `decision_engine.py` maps the analysis into a deterministic `Decision`.
2. `build_decision_document()` turns that `Decision` into a Cosmos DB document.
3. `upsert_decision_document()` in `cosmos.py` writes the document to the configured Cosmos container.
4. The resulting `Decision` is returned to `main()`.

After the Cosmos write succeeds, `main()` publishes the decision to the `final-decisions` topic with `publish_decision()`. The message body is `Decision` JSON, the `message_id` is the generated `decision_id`, `content_type=application/json`, and `application_properties.message_kind=decision`.

The decision engine is deterministic and mostly rule-based:

- It normalizes action aliases (for example `no action required`, `none`, and `no_action_required` all become `no_action`).
- It maps known actions to concrete commands.
- It suppresses shell commands when the analysis text or remediation hint already looks like a Nix assignment.
- It upgrades the action to `apply_config` in that case, leaving `command` empty so the downstream agent can do config-level repair instead of a shell-only action.
- `restart_service` requires `affected_unit`; otherwise it raises `ValueError`.

The Cosmos document is the `Decision` payload plus a Cosmos `id` field equal to `decision_id`.

### Module-by-module breakdown

#### `decision_agent/__init__.py`

- **Public surface:** none.
- **Logic:** package initializer only.

#### `decision_agent/config.py`

- **Public surface:**
  - `DecisionAgentConfig` — Pydantic model for runtime configuration.
  - `load_config(env=None)` — reads settings from `os.environ` or an injected mapping.
- **Logic:**
  - Required: `SERVICEBUS_CONNECTION`, `COSMOSDB_ENDPOINT`, `COSMOSDB_DATABASE_NAME`.
  - `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME=analysis-results`.
  - `SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME=final-decisions`.
  - `COSMOSDB_DECISIONS_CONTAINER_NAME=decisions`.
  - Uses `COSMOSDB_KEY` if present; otherwise `cosmos_key` is `None` and the Cosmos client falls back to managed identity.

#### `decision_agent/cosmos.py`

- **Public surface:**
  - `upsert_decision_document(endpoint, database_name, container_name, document, key=None)` — writes the decision document into Cosmos DB.
- **Logic:**
  - Uses `CosmosClient(url=..., credential=...)`.
  - Chooses authentication based on whether `key` is provided:
    - `key` present → use that value as the credential
    - `key` absent → use `DefaultAzureCredential`
  - Calls `upsert_item(document)` on the target container.

#### `decision_agent/decision_engine.py`

- **Public surface:**
  - `contains_nix_assignment(text)` — detects Nix-style assignments in text.
  - `normalize_suggested_action(suggested_action)` — canonicalizes action names and aliases.
  - `build_command(analysis_result)` — converts the analysis into a shell command when appropriate.
  - `build_idempotency_key(analysis_result, command)` — computes a deterministic SHA-256 key.
  - `build_decision(analysis_result, now_factory=None, uuid_factory=None)` — produces a `Decision`.
  - `build_decision_document(decision)` — produces the Cosmos DB document shape.
- **Logic:**
  - `ACTION_ALIASES` normalizes variants like `restartservice`, `noaction`, `none`, and `applyconfig`.
  - `build_command()` maps actions to commands:
    - `rollback` → `nixos-rebuild switch --rollback`
    - `rebuild` → `nixos-rebuild switch`
    - `restart_service` → `systemctl restart <affected_unit>`
    - `no_action` → empty command
    - `apply_config` → empty command
  - If `analysis_text` or `remediation_hint` contains a Nix assignment pattern, `build_command()` returns an empty command and `build_decision()` converts the action to `apply_config`.
  - `build_decision()` sets `analysis_id` to `AnalysisResult.original_message_id`, copies severity/confidence through unchanged, and stores both human-readable summary text and remediation text.
  - `build_idempotency_key()` is deterministic over `node_id`, `suggested_action`, `command`, and `original_message_id`.
  - `build_decision_document()` adds `id = decision_id` so Cosmos has a stable document id.

#### `decision_agent/app.py`

- **Public surface:**
  - `process_analysis_result(analysis_result, config, write_document=upsert_decision_document)` — orchestration for decision creation and Cosmos persistence.
- **Logic:**
  - Builds the `Decision` first, then writes the Cosmos document, then returns the `Decision`.
  - The persistence function is dependency-injected for tests and simulator use.

#### `decision_agent/main.py`

- **Public surface:**
  - `publish_decision(connection_string, topic_name, decision_body, message_id)` — Service Bus topic publisher.
  - `main(msg)` — Azure Function entrypoint.
  - `run_cli()` — prints a sample analysis payload for local inspection.
- **Logic:**
  - `main()` validates the incoming JSON body with `AnalysisResult.model_validate()` before any decision logic runs.
  - The outgoing Service Bus message uses the generated `decision_id` as `message_id`.
  - `publish_decision()` sets `content_type=application/json` and `application_properties={"message_kind": "decision"}`.
  - `run_cli()` is only a helper and does not execute the pipeline.

### Inputs and outputs

- **Input topic:** `analysis-results` / subscription `decision-agent`.
- **Accepted input shape:** `schemas.AnalysisResult` JSON. Required fields: `node_id`, `original_message_id`, `source_type`, `severity`, `root_cause`, `suggested_action`, `confidence`, `analysis_text`, `raw_ai_response`, `timestamp`. `affected_unit` is optional in the schema, but `restart_service` requires it in the decision engine.
- **Output topic:** `final-decisions`.
- **Output body shape:** `schemas.Decision` JSON with `schema_version`, `decision_id`, `node_id`, `analysis_id`, `action`, `command`, `severity`, `confidence`, `analysis_summary`, `remediation_text`, `idempotency_key`, `timestamp`.
- **Cosmos output:** a document identical to the `Decision` payload plus `id = decision_id`.

### Connections

- **Calls:** Azure Cosmos DB (`upsert_item` on the decisions container), Azure Service Bus topic sender for `final-decisions`.
- **Called by:** Azure Functions Service Bus trigger on `analysis-results`, and the simulator path that calls `process_analysis_result()` directly.
- **Touches:** input subscription `decision-agent`; output topic `final-decisions`; Cosmos database from `COSMOSDB_DATABASE_NAME`; Cosmos container `decisions` by default.
- **Authentication:** Cosmos can use `COSMOSDB_KEY` or managed identity through `DefaultAzureCredential`.

### Decisions and branching

- `normalize_suggested_action()` accepts many textual variants and reduces them to canonical actions.
- `build_command()` only emits shell commands for the supported concrete actions.
- If the analysis or remediation text contains a Nix assignment, the engine switches to `apply_config` and suppresses a shell command entirely.
- `restart_service` is only valid when `affected_unit` is set; otherwise the engine raises `ValueError`.
- There is no explicit retry counter or safety workflow in this module; the logic is purely rule-based and deterministic.
- The idempotency key is computed and stored, but this code path does not check it before upserting or publishing.

### Configuration

- **Required env vars:** `SERVICEBUS_CONNECTION`, `COSMOSDB_ENDPOINT`, `COSMOSDB_DATABASE_NAME`.
- **Optional with defaults:** `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME=analysis-results`, `SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME=final-decisions`, `COSMOSDB_KEY` (optional; if absent, managed identity is used), `COSMOSDB_DECISIONS_CONTAINER_NAME=decisions`.
- **Config loading:** `load_config(env=None)`, reads from the provided mapping or `os.environ`.

### Known issues / notes

- The module is still command-oriented; `apply_config` is represented as an empty command rather than a direct config-edit operation.
- `build_command()` raises on unsupported actions and on `restart_service` without `affected_unit`.
- Cosmos persistence is a simple upsert with no explicit retry wrapper or transactional grouping.
- `analysis_id` is tied to the incoming analysis message id, not a separate analysis document id.
- The package also exposes a CLI entrypoint (`decision_agent = decision_agent.main:run_cli`) for local inspection.

---

## 9. Node-side repair loop — `local_agent/`

### Purpose / Why it exists

`local_agent` is the node-side runtime that turns a NixOS VM into an active participant in the healing loop. It watches the machine, packages observations, consumes remediation decisions from the cloud pipeline, and either executes a direct command or runs the Git-backed `apply_config` repair flow against the shared config repository. In the proof-of-concept direction, it is the place where repair authority moves onto the disposable VM: the local agent uses a host-side Ollama model, edits `configuration.nix`, validates with `nixos-rebuild test`, and only then promotes with `nixos-rebuild switch`. The code is designed so the cloud provides context and routing, while the node owns the actual repair execution and reporting.

### How it works

At runtime the package runs as a small daemon with three concurrent workers:

1. **Observe worker** — reads live node state from the VM, hashes the meaningful parts of that state, and publishes an `Observation` to the `analysis-input` topic when the state changed enough to matter.
2. **Decision worker** — consumes messages from the `final-decisions` topic and routes each `Decision` into one of three paths: skip, run a direct command, or run the config-repair loop.
3. **Persist worker** — flushes queued Cosmos documents so the node can record observations, node state, execution results, config snapshots, repair traces, and service-status events.

The normal repair flow for a `Decision` with `action == "apply_config"` or `action == "rebuild"` is:

- receive the decision from Service Bus
- verify it targets the local `node_id`
- enforce remediation safety checks (`cooldown_seconds`, `max_remediations_per_hour`, and `ongoing_remediation`)
- refresh the persistent clone of `https://github.com/Free-Rat/phoe-nix-config`
- read the current `configuration.nix`
- build a repair prompt from the decision, current node state, current config, and the last rebuild failure if any
- send the prompt to Ollama on the host
- parse the model response into full config text
- write the proposed config into the repo
- run `nixos-rebuild test`
- on success, run `nixos-rebuild switch`
- on switch success, commit and push the repair back to the shared repo
- persist the execution result, node state, config snapshots, repair traces, and a service-status record

If `nixos-rebuild test` fails, the loop retries with the failure text appended to the next prompt. If `nixos-rebuild switch` fails, the loop stops and returns failure. If `git push` fails, the repo is refreshed and the next attempt starts from a clean checkout. The local Ollama client is only used inside this repair loop; it does not replace the cloud analysis pipeline, it refines the cloud decision into a concrete config change.

### Module-by-module breakdown

#### `src/local_agent/__init__.py`

- **Public surface:** none.
- **Logic:** `__all__ = []`, so the package root does not re-export symbols. Consumers import concrete modules directly.

#### `src/local_agent/main.py`

- **Public surface:**
  - `run_cli()`: command-line entry point used by the `local_agent` console script.
  - `run_once(decision_payload: dict)`: helper used by tests/manual checks to execute one observation + decision pass with injected dependencies.
- **Logic:**
  - Loads settings with `load_config()`.
  - If `LOCAL_AGENT_RUN_MODE=daemon`, it runs `run_daemon(config=...)` and prints the JSON result.
  - Otherwise it prints a sample `Observation` built from a synthetic `NodeState` with `nginx.service` failures and restart counts.
  - `run_once()` wires fake dependencies: a node-state reader, command runner, Ollama generator, and persistence no-op.

#### `src/local_agent/config.py`

- **Public surface:**
  - `LocalAgentConfig`: Pydantic model for runtime configuration.
  - `load_config(env=None)`: loads the model from environment variables.
- **Logic:**
  - `_parse_bool()` normalizes string booleans like `1/0`, `true/false`, `yes/no`, and `on/off`.
  - `servicebus_enabled` defaults to true only when `SERVICEBUS_CONNECTION` is non-empty, unless `SERVICEBUS_ENABLED` overrides it.
  - `cosmos_enabled` defaults to true only when both `COSMOSDB_ENDPOINT` and `COSMOSDB_DATABASE_NAME` are present, unless `COSMOSDB_ENABLED` overrides it.
  - `node_id` defaults to `localhost`.
  - The service bus topic defaults are `analysis-input` and `final-decisions`; the local subscription default is `local-agent`.
  - The Ollama endpoint defaults to `http://10.0.2.2:11434` and the model defaults to `gemma3:4b`.
  - `repair_max_attempts`, `observe_interval_seconds`, `cooldown_seconds`, and the decision poll backoff values are all configured here.
  - `repo_refresh_seconds` is present in the config model, but the current runtime code does not consume it.

#### `src/local_agent/runtime.py`

- **Public surface:**
  - `PersistRequest`: queued Cosmos write request.
  - `RuntimeDependencies`: injectable dependency bundle for tests/manual runs.
  - `LocalAgentRuntime`: runtime state holder for config, node state, and the persistence queue.
  - `observe_once(runtime)`: one observation cycle.
  - `persist_pending(runtime)`: flushes queued Cosmos writes.
  - `handle_decision(runtime, decision_payload)`: validates and executes one decision.
  - `decision_worker(runtime, stop_after_idle_cycles=None)`: Service Bus consume loop for `final-decisions`.
  - `observe_worker(runtime, iterations=None)`: repeated observation loop.
  - `persist_worker(runtime, stop_after_idle_cycles=None)`: queue-flush loop.
  - `run_daemon(config, dependencies=None, observe_iterations=None, decision_idle_cycles=None, persist_idle_cycles=None)`: starts the three workers concurrently.
  - `run_runtime_once(config, decision_payloads=None, dependencies=None)`: single-pass helper used by tests and manual execution.
- **Logic:**
  - `LocalAgentRuntime.__post_init__()` creates the initial state with `new_state(node_id)` and an `asyncio.Queue` for persistence requests.
  - `observe_once()` reads the current node state, compares it with the last meaningful observation hash, and only publishes when the hash changed.
  - Observation publishes go to `analysis-input` with a message id of `<node_id>:<timestamp>`; publish failures are swallowed, but Cosmos writes are still queued.
  - `persist_pending()` drops queued documents when Cosmos is disabled or misconfigured; otherwise it calls `upsert_document()` for each request.
  - `handle_decision()` branches in this order:
    1. reject decisions for another node
    2. skip `no_action`
    3. execute direct commands when `decision.command` is present
    4. for `apply_config` / `rebuild`, enforce safety limits and run the repair loop
    5. otherwise return an error that no executable repair plan exists
  - `decision_worker()` receives at most one message at a time, tracks idle cycles, and applies exponential backoff on repeated receive failures.
  - Message bodies are normalized by `_message_body_to_payload()` so the worker can handle dict payloads, SDK messages with `get_body()`, raw bytes, and generator-backed Service Bus bodies.
  - `_message_correlation_id()` prefers the Service Bus message id, then falls back to `decision_id` in the payload.
  - For real Service Bus messages, completion is attempted after successful handling; completion failure is recorded as a service-status event.
  - `run_daemon()` starts observe/decision/persist loops together and returns summary counts.

#### `src/local_agent/bus_client.py`

- **Public surface:**
  - `publish_message(...)`: publishes a JSON message to a Service Bus topic.
  - `build_topic_receiver(...)`: creates a Service Bus client and subscription receiver.
  - `receive_messages(...)`: receives up to `max_message_count` messages from a topic subscription.
  - `complete_message(...)`: completes a message on the subscription receiver.
- **Logic:**
  - Supports a mock HTTP backend when the connection string starts with `Endpoint=mock://`.
  - Real publishing uses `azure.servicebus.ServiceBusClient` and `ServiceBusMessage`.
  - Real receive/complete operations open a client and receiver through `get_subscription_receiver()`.
  - **Known issue:** the receive and complete helpers each create their own receiver lifecycle; connectivity analysis notes that this can break message settlement on the live Service Bus path.

#### `src/local_agent/state.py`

- **Public surface:**
  - `LocalAgentState`: in-memory runtime state.
  - `build_observation_hash(node_state)`: canonical hash of meaningful node-state fields.
  - `has_significant_change(previous_hash, node_state)`: compares the last hash to current state.
  - `within_cooldown(node_state, cooldown_seconds, now)`: checks remediation cooldown.
  - `can_apply_remediation(state, cooldown_seconds, max_remediations_per_hour, now)`: safety gate for repair.
  - `record_remediation(state, now, node_state_after)`: updates counters and timestamps after a remediation.
  - `start_remediation(state)`: marks the runtime as actively remediating.
  - `update_node_state(state, node_state)`: replaces the tracked node state and observation hash.
  - `new_state(node_id)`: creates a fresh runtime state.
- **Logic:**
  - The observation hash only includes meaningful fields: failed units, positive restart counts, disk usage, and CPU/memory usage buckets.
  - Uptime and last remediation timestamp are intentionally ignored for change detection.
  - CPU and memory usage are only bucketed once they reach the alert threshold (`80%`) and are rounded down to 5% steps.
  - `record_remediation()` also stamps `last_remediation_timestamp` into the tracked `NodeState`.
  - `new_state()` accepts `node_id` but currently ignores it.

#### `src/local_agent/system_state.py`

- **Public surface:**
  - `run_command(command, timeout_seconds=30)`: thin subprocess helper.
  - `collect_node_state(command_runner=run_command)`: reads live node state from the host.
- **Logic:**
  - Reads `systemctl --failed --plain --no-legend` and extracts failed unit names.
  - Reads `/proc/uptime` and converts the first value to `uptime_seconds`.
  - Returns a `schemas.NodeState` with the collected fields populated.

#### `src/local_agent/monitor.py`

- **Public surface:**
  - `summarize_node_state(node_state)`: builds a human-readable summary string.
  - `infer_severity(node_state)`: maps the current state to `warning` or `info`.
  - `build_observation(node_id, node_state, observation_type)`: creates an `Observation` message.
  - `should_publish_observation(previous_hash, node_state)`: decides whether to publish.
  - `current_state_hash(node_state)`: convenience wrapper around the state hash.
- **Logic:**
  - Observation messages use `source="local_agent"` and UTC timestamps.
  - Summaries include failed units, restart counts, and disk usage when present.
  - Severity is `warning` when failed units exist, otherwise `info`.
  - Only meaningful state changes trigger publication.

#### `src/local_agent/executor.py`

- **Public surface:**
  - `CommandResult`: subprocess result wrapper.
  - `validate_decision_target(decision, node_id)`: checks whether the decision targets this node.
  - `derive_execution_command(decision)`: extracts the runnable command string.
  - `run_subprocess(command, timeout_seconds=30)`: shell command runner.
  - `execute_decision(...)`: runs a direct-command decision and returns the next state, execution result, and error.
- **Logic:**
  - Direct commands are executed with `shell=True`.
  - `apply_config` does **not** get a command from this module; if `decision.command` is empty and the action is not `no_action`, the decision is rejected as having no executable plan.
  - Remediation safety limits are enforced before execution.
  - The runtime marks remediation active before running and records the result afterward.
  - Success clears failed units in the derived post-execution node state; failure preserves them.
  - `build_execution_result()` from `reporter.py` is used to produce the final execution document.

#### `src/local_agent/repair_planner.py`

- **Public surface:**
  - `RepairAttempt`: captures one prompt / model response / rebuild attempt.
  - `RepairOutcome`: summarizes the overall repair loop result.
  - `build_repair_prompt(...)`: constructs the Ollama prompt for a repair attempt.
  - `extract_config_text(response_text)`: parses a full `configuration.nix` replacement from model output.
  - `sync_local_hardware_configuration(repo_path)`: copies `/etc/nixos/hardware-configuration.nix` into the repo when needed.
  - `execute_repair_loop(...)`: runs the full repair loop.
- **Logic:**
  - The prompt includes the decision summary, remediation text, node state JSON, the current config, and the previous test failure if one exists.
  - The model may return raw Nix, a fenced `nix` block, a fenced `json` block, or JSON containing `updated_config_text`.
  - The loop refreshes the repo before the first attempt and again after a failed push.
  - Each attempt writes the proposed config, runs `nixos-rebuild test`, and only on success runs `nixos-rebuild switch`.
  - On switch success it calls `commit_and_push()` with commit message `phoe-nix repair <decision_id>`.
  - On push failure it records the failure text and retries from a freshly refreshed repo.
  - The function returns a structured `RepairOutcome` with the attempts, repo revisions, stdout/stderr, and the final config text.

#### `src/local_agent/ollama_client.py`

- **Public surface:**
  - `OllamaError`: runtime error type for Ollama failures.
  - `generate_text(...)`: performs the Ollama `/api/generate` request and returns the response text.
- **Logic:**
  - Sends `{"model": ..., "prompt": ..., "stream": false}` to `POST /api/generate`.
  - Raises `OllamaError` on HTTP errors, connection errors, invalid JSON, an `error` field in the response, or an empty `response` field.
  - The prompt assembly happens upstream in `repair_planner.py`.

#### `src/local_agent/git_repo.py`

- **Public surface:**
  - `GitCommandResult`: Git subprocess result wrapper.
  - `run_git_command(...)`: generic Git runner.
  - `ensure_repo(...)`: clones the repo if the path does not exist.
  - `refresh_repo(...)`: fetches, hard-resets, and cleans the repo.
  - `read_config_text(...)`: reads `configuration.nix`.
  - `write_config_text(...)`: writes `configuration.nix`.
  - `current_revision(...)`: returns `git rev-parse HEAD`.
  - `commit_and_push(...)`: stages, commits, and pushes the config file.
- **Logic:**
  - The persistent clone is expected at `config_repo_path` and defaults to `/var/lib/phoe-nix-config`.
  - `refresh_repo()` always resets to `origin/<branch>` and removes untracked files, so the repair loop starts from a clean tree.
  - `commit_and_push()` treats `nothing to commit` as non-fatal and still attempts push.
  - Any non-zero clone/fetch/reset/clean failure raises `RuntimeError`.

#### `src/local_agent/persistence.py`

- **Public surface:**
  - `upsert_document(...)`: writes a document to Cosmos DB or the mock backend.
- **Logic:**
  - For `mock+http://` or `mock+https://` endpoints it POSTs JSON to the mock service path `/databases/<db>/containers/<container>/upsert`.
  - Real Cosmos writes use `CosmosClient(url=endpoint, credential=key or DefaultAzureCredential())`.
  - The document is upserted into the container named by the runtime config.

#### `src/local_agent/reporter.py`

- **Public surface:**
  - `summarize_execution(node_state_after)`: human-readable outcome summary.
  - `build_execution_result(...)`: creates a `schemas.ExecutionResult`.
  - `build_node_state_document(node_id, node_state)`: Cosmos document for the current node state.
  - `build_observation_document(observation)`: Cosmos document for an observation.
  - `build_config_snapshot_document(...)`: records before/after config text for one attempt.
  - `build_repair_trace_document(...)`: records the full repair attempt trace.
  - `build_service_status_document(...)`: records service progress and error status.
  - `current_time()`: UTC timestamp helper.
- **Logic:**
  - `build_execution_result()` generates a UUID execution id and sets `success` from the exit code.
  - `build_node_state_document()` and `build_observation_document()` attach stable `id` fields for Cosmos.
  - Config snapshots store repo revisions, path, and before/after text.
  - Repair traces store the prompt, model response, test/switch results, and push outcome.
  - Service-status events carry a stage (`observation`, `decision`, or `repair`), a status, a correlation id, and a timestamp.

#### `src/local_agent/manual_integration.py`

- **Public surface:**
  - `_manual_command_runner()`: returns a simulated or real rebuild command runner depending on `LOCAL_AGENT_MANUAL_REAL_REBUILD`.
  - `run_manual_integration()`: executes the manual repair-flow demonstration.
  - `main()`: prints the JSON summary.
- **Logic:**
  - Creates a temporary repo unless `LOCAL_AGENT_MANUAL_REPO_PATH` is set.
  - Refreshes the config repo, reads the current config, and constructs a synthetic `Decision` for `apply_config`.
  - By default simulates one failed `nixos-rebuild test` followed by a successful retry and switch.
  - Can use a real rebuild and/or real push when `LOCAL_AGENT_MANUAL_REAL_REBUILD=1` or `LOCAL_AGENT_MANUAL_REAL_PUSH=1`.
  - Returns a compact JSON report with repo path, revisions, attempt count, success, and config excerpts.

#### `pyproject.toml` and `flake.nix`

- **`pyproject.toml`:** defines the package name (`local_agent`), Python requirement (`>=3.14`), dependencies (`azure-cosmos`, `azure-identity`, `azure-servicebus`, `pydantic`, `schemas`), and console scripts (`local_agent`, `local_agent_manual_integration`).
- **`flake.nix`:** builds a Python 3.14 virtual environment called `local-agent-env`, exposes the `local_agent` app, and provides a dev shell with `git`, `python314`, and `uv`.

### Inputs and outputs

**Inputs**
- Service Bus messages from `final-decisions`.
- Environment variables for node identity, backends, repo paths, Ollama, and safety limits.
- The local config repo clone at `CONFIG_REPO_PATH`.
- The local Ollama HTTP endpoint.
- The host system state (failed units, uptime, and any other state provided to the node-state model).
- Journal/system activity indirectly through `system_state.collect_node_state()`.

**Outputs**
- `Observation` messages published to `analysis-input`.
- Cosmos documents for observations, execution results, node state, config snapshots, repair traces, and service-status events.
- Modified files in the persistent config repo, especially `configuration.nix`.
- `nixos-rebuild test` and `nixos-rebuild switch` invocations.
- Git commits and pushes back to the shared config repository.

### Connections

**What it calls**
- Service Bus publish/receive/complete operations.
- Cosmos DB upserts.
- Ollama HTTP `POST /api/generate`.
- `git` commands for clone/fetch/reset/clean/add/commit/push.
- `nixos-rebuild test` and `nixos-rebuild switch`.
- The local filesystem, including `/var/lib/phoe-nix-config` and `/etc/nixos/hardware-configuration.nix`.

**What calls it**
- The Service Bus trigger path from the `final-decisions` topic/subscription.
- `scripts/run-live-azure-vm-e2e.py` and `scripts/run-guest-manual-integration.sh` during live/manual validation.
- `systemd` on the VM when deployed as the daemonized runtime.
- The console scripts exposed by `pyproject.toml`.

### Decisions and branching

`local_agent` does not invent the cloud decision; it routes and executes it.

- If `decision.node_id` does not match the local `node_id`, the message is skipped.
- If `decision.action == "no_action"`, it is acknowledged as a no-op.
- If `decision.command` is present, the runtime uses the direct-command path in `executor.py`.
- If the command is empty and the action is `apply_config` or `rebuild`, the runtime runs the repair loop.
- If the decision has no executable command and is not a repair action, it is rejected.
- Before any remediation, the runtime checks:
  - whether another remediation is already running
  - whether the node is still inside cooldown
  - whether the hourly remediation cap has been reached
- In the repair loop, Ollama output is accepted only if it can be parsed into complete config text.
- The repair loop retries until it either succeeds, exhausts `repair_max_attempts`, or hits an unrecoverable switch/push failure.

### Configuration

**Required for a live Azure-backed run**
- `NODE_ID`
- `SERVICEBUS_CONNECTION`
- `COSMOSDB_ENDPOINT`
- `COSMOSDB_DATABASE_NAME`

**Optional / defaulted by `config.py`**
- `SERVICEBUS_ENABLED` (defaults from whether `SERVICEBUS_CONNECTION` is set)
- `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME` (`analysis-input`)
- `SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME` (`final-decisions`)
- `SERVICEBUS_SUBSCRIPTION_LOCAL_AGENT` (`local-agent`)
- `COSMOSDB_ENABLED` (defaults from whether Cosmos endpoint and database are present)
- `COSMOSDB_KEY`
- `COSMOSDB_OBSERVATIONS_CONTAINER_NAME` (`observations`)
- `COSMOSDB_EXECUTION_RESULTS_CONTAINER_NAME` (`execution-results`)
- `COSMOSDB_NODE_STATE_CONTAINER_NAME` (`node-state-current`)
- `COSMOSDB_CONFIG_SNAPSHOTS_CONTAINER_NAME` (`config-snapshots`)
- `COSMOSDB_REPAIR_TRACES_CONTAINER_NAME` (`repair-traces`)
- `COSMOSDB_SERVICE_STATUS_CONTAINER_NAME` (`service-status`)
- `CONFIG_REPO_URL` (`https://github.com/Free-Rat/phoe-nix-config`)
- `CONFIG_REPO_BRANCH` (`main`)
- `CONFIG_REPO_PATH` (`/var/lib/phoe-nix-config`)
- `CONFIG_FILE_PATH` (`configuration.nix`)
- `REPO_REFRESH_SECONDS` (`300`)
- `OLLAMA_BASE_URL` (`http://10.0.2.2:11434`)
- `OLLAMA_MODEL` (`gemma3:4b`)
- `OLLAMA_TIMEOUT_SECONDS` (`60`)
- `REPAIR_MAX_ATTEMPTS` (`3`)
- `REBUILD_TEST_COMMAND` (`nixos-rebuild test`)
- `REBUILD_SWITCH_COMMAND` (`nixos-rebuild switch`)
- `OBSERVE_INTERVAL_SECONDS` (`60`)
- `COOLDOWN_SECONDS` (`300`)
- `MAX_REMEDIATIONS_PER_HOUR` (`3`)
- `DECISION_POLL_BASE_SECONDS` (`0.05`)
- `DECISION_POLL_MAX_SECONDS` (`1.0`)

**Entry-point and manual-integration env vars**
- `LOCAL_AGENT_RUN_MODE` (`daemon` switches `main.py` into daemon mode; anything else prints a sample observation)
- `LOCAL_AGENT_MANUAL_REPO_PATH`
- `LOCAL_AGENT_MANUAL_ANALYSIS_SUMMARY`
- `LOCAL_AGENT_MANUAL_REMEDIATION_TEXT`
- `LOCAL_AGENT_MANUAL_REAL_REBUILD`
- `LOCAL_AGENT_MANUAL_REAL_PUSH`

### Known issues / notes

- `bus_client.receive_messages()` and `bus_client.complete_message()` open separate receiver lifecycles; connectivity analysis flags this as a likely settlement problem on the live Service Bus path.
- The current topology still uses a shared `final-decisions` subscription name (`local-agent` by default); connectivity analysis notes this can be problematic for multiple nodes.
- `repo_refresh_seconds` is present in configuration but is not yet used by the current runtime.
- The default Ollama model is `gemma3:4b`; deployments may override it through the environment.
- The mock backends are intentional: Service Bus supports `Endpoint=mock://...`, and Cosmos supports `mock+http(s)://...`.
- `last_repo_refresh_at` exists on `LocalAgentRuntime` but is not yet consumed by the current code path.
- **Cloud-connectivity gap:** the rendered VM env currently omits `NODE_ID`, so the live `local_agent` path defaults to `localhost` while scripts commonly target `nixos`, which can cause decisions to be silently skipped.

---

## 10. Local simulator — `simulator/`

### Purpose / Why it exists

The `simulator` package exists to exercise the repository's real service code without Azure infrastructure. It replaces Blob Storage, Service Bus, Cosmos DB, Key Vault, OpenCode, and the VM-side local-agent execution path with in-memory or local fakes so the pipeline can be tested end-to-end. It is useful when unit tests are too narrow but a live Azure deployment is too slow, expensive, or fragile for routine validation. It covers both the cloud-side log/analysis/decision pipeline and the observation-driven local-agent repair path, including several failure cases that are hard to stage in a real environment. The package is also the repo's operator-friendly smoke test: `uv run simulate_pipeline` prints a JSON summary of the pipeline run.

### How it works

- `simulator.__init__` adds every repository `src/` directory to `sys.path`, so the simulator imports the real packages from the monorepo instead of separate installed wheels.
- `simulator.fixtures.build_environment()` assembles a `LocalPipelineEnvironment` dataclass that wires together fake Blob Storage, Service Bus, Cosmos, Key Vault, a fake config repo, a fake local agent, and config objects for the real service code.
- `simulator.pipeline.run_pipeline()` drives the normal log path in order: `token_service` → `log_service` → `log_router` → `analysis_agent` → `decision_agent` → `local_agent`.
- `simulator.pipeline.run_observation_pipeline()` skips the log upload path and starts from a synthetic `Observation`, then sends it through `analysis_agent` and `decision_agent` before simulating local-agent execution.
- `simulator.pipeline` also includes explicit failure simulators for token lookup failure, blob-upload retry/recovery, malformed blobs, and invalid OpenCode output.
- `simulator.cli.main()` is the console entrypoint exposed by `simulate_pipeline`; it runs several scenarios back-to-back and prints a JSON object with the results.

### Module-by-module breakdown

#### `simulator/src/simulator/__init__.py`

- **Public surface:** none exported; `__all__ = []`.
- **Logic:**
  - `_add_repo_sources_to_path()` resolves the repo root and prepends each service's `src/` directory to `sys.path`.
  - The inserted paths are `analysis_agent/src`, `decision_agent/src`, `local_agent/src`, `log_router/src`, `log_service/src`, `schemas/src`, and `token_service/src`.
  - This makes the simulator import the real in-repo packages without needing installation.

#### `simulator/src/simulator/cli.py`

- **Public surface:** `main()`.
- **Logic:**
  - There are no argparse flags or subcommands; the CLI is a fixed entrypoint.
  - It builds a fresh environment for each scenario with `build_environment()` and sample fixtures from `sample_log_entries()` / `sample_observation()`.
  - It runs six scenarios: full log pipeline, observation pipeline, token failure, upload retry, malformed blob, and invalid AI response.
  - It prints a single JSON document with sorted keys and indentation for easy inspection.

#### `simulator/src/simulator/pipeline.py`

- **Public surface:** `LocalPipelineEnvironment`, `issue_token_via_service()`, `upload_payload_to_fake_blob_storage()`, `build_batch_uploader()`, `publish_normalized_logs()`, `process_analysis_topic()`, `process_decision_topic()`, `process_local_agent()`, `publish_observation()`, `analyze_result_from_payload()`, `simulate_opencode_response()`, `invalid_opencode_response()`, `run_pipeline()`, `run_observation_pipeline()`, `simulate_token_failure()`, `simulate_upload_retry_and_recovery()`, `simulate_malformed_log_blob()`, `simulate_invalid_ai_response()`.
- **Logic:**
  - `LocalPipelineEnvironment` is the glue object that carries configs plus all fake dependencies into each stage.
  - `issue_token_via_service()` calls the real `token_service.handle_token_request()` and replaces the returned SAS URL with a deterministic fake `https://blob.local/...` URL so the fake blob store can consume it.
  - `build_batch_uploader()` constructs the real `log_service.uploader.BatchUploader`, but swaps in the fake token requester, fake uploader, and a no-op sleep function.
  - `publish_normalized_logs()` reads a blob payload from `FakeBlobStorage`, passes it through `log_router.normalize_blob()`, and publishes each normalized record to `analysis-input` with a `message_kind=normalized_log` application property.
  - `process_analysis_topic()` iterates over messages in the fake `analysis-input` topic, calls the real `analysis_agent.message_handler.analyze_message()`, and republishes the result to `analysis-results`.
  - `process_decision_topic()` reads `analysis-results`, converts the payload into a real `AnalysisResult`, runs `decision_agent.app.process_analysis_result()`, stores the audit document in fake Cosmos, and publishes the resulting `Decision` to `final-decisions`.
  - `process_local_agent()` constructs a real `LocalAgentRuntime` with fake runtime dependencies, then feeds each decision into `handle_decision()` and `persist_pending()`; it uses a fake LLM response and a fake command runner so the repair loop can complete deterministically.
  - `simulate_opencode_response()` returns a successful remediation JSON payload when the prompt mentions service failure / restart / `nginx.service`; otherwise it returns a benign `no_action` analysis.
  - `invalid_opencode_response()` deliberately returns invalid structured output (`severity="fatal"`, `confidence=1.2`) to test validation failures.
  - `run_pipeline()` returns counters plus captured fake topic messages and Cosmos containers (`decisions`, `execution-results`, `config-snapshots`, `repair-traces`).
  - `run_observation_pipeline()` mirrors the same downstream processing but starts from an `Observation` instead of uploaded logs.
  - `simulate_token_failure()` makes the token requester raise `RuntimeError("token service unavailable")`, so the uploader spools payloads locally.
  - `simulate_upload_retry_and_recovery()` makes the first blob upload fail, then succeed on the next flush, exercising retry and recovery.
  - `simulate_malformed_log_blob()` uploads an invalid blob shape (`entries: [{}]`) and expects normalization to fail.
  - `simulate_invalid_ai_response()` swaps in `invalid_opencode_response()` and expects analysis processing to fail.

#### `simulator/src/simulator/fakes.py`

- **Public surface:** `FakeBlobObject`, `FakeBlobStorage`, `FakeServiceBusMessage`, `FakeServiceBus`, `FakeCosmosContainer`, `FakeCosmos`, `FakeLocalAgent`, `FakeConfigRepo`, `FakeKeyVault`.
- **Logic:**
  - `FakeBlobStorage.upload(blob_path, payload)` stores bytes in an in-memory dictionary and returns a fake SAS URL.
  - `FakeBlobStorage.read(blob_path)` returns the stored payload bytes.
  - `FakeServiceBus.publish(...)` appends a `FakeServiceBusMessage` to an in-memory topic list.
  - `FakeServiceBus.topic_messages(topic_name)` returns a copy of the stored messages for inspection.
  - `FakeCosmos.upsert(container_name, document)` stores documents in a per-container list, replacing documents with the same `id`.
  - `FakeCosmos.container_items(container_name)` exposes the stored documents for assertions and summaries.
  - `FakeLocalAgent.execute(command, execution_result)` records executed commands and their execution-result payloads.
  - `FakeConfigRepo.refresh()`, `read()`, `write(content)`, `revision()`, and `push()` mimic the Git-backed config repo the real local agent edits.
  - `FakeKeyVault.read(vault_name, secret_name)` returns a secret value from an in-memory mapping.
  - `FakeServiceBusMessage.json()` parses the JSON body back into a Python object for convenience.

#### `simulator/src/simulator/fixtures.py`

- **Public surface:** `build_environment()`, `sample_log_entries()`, `sample_observation()`.
- **Logic:**
  - `build_environment()` creates a temporary spool directory and wires the entire `LocalPipelineEnvironment` with deterministic local values.
  - The token-service config uses `storage_account_name="blob"`, `logs_container_name="logs"`, `keyvault_name="kv-local"`, `storage_account_key_secret="StorageAccountKey"`, `node_api_key="secret"`, and `token_ttl_minutes=5`.
  - The log-service config uses `token_service_url="http://token.local/api/token"`, `node_id="nixos-node-01"`, `node_api_key="secret"`, `upload_timeout_seconds=5.0`, `batch_size=100`, `flush_interval_seconds=30.0`, `max_retries=3`, `retry_base_delay_seconds=0.01`, and the temporary spool directory.
  - The analysis-agent config points at `analysis-results`, uses `kv-local`, secret name `OpenCodeApiKey`, and a fake OpenCode URL.
  - The decision-agent config points at `analysis-results`, `final-decisions`, Cosmos endpoint `https://cosmos.local`, database `project-healer`, and container `decisions`.
  - The local-agent config uses the same service bus and Cosmos settings, `node_id="nixos-node-01"`, and `config_repo_path` set to the temporary directory.
  - The fake key vault contains `StorageAccountKey` and `OpenCodeApiKey`.
  - `sample_log_entries()` returns two journal entries for `nginx.service` failure.
  - `sample_observation()` returns an `Observation` with a `NodeState` that includes generation history, failed units, restart counts, and disk usage hints.

#### `simulator/pyproject.toml`

- **Public surface:** n/a (package metadata).
- **Logic:**
  - Declares `requires-python = ">=3.14"`.
  - Depends on `azure-cosmos`, `azure-identity`, `azure-servicebus`, `azure-storage-blob`, `pydantic`, and `typing-extensions`.
  - Exposes the console script `simulate_pipeline = "simulator.cli:main"`.
  - Uses `hatchling` for build/install metadata and `src/` as the source layout.

### What scenarios are covered

- Log ingestion happy path.
- Observation-only happy path.
- Token-service failure with local spooling.
- Blob upload retry and recovery.
- Malformed uploaded log blob.
- Invalid OpenCode / AI response.
- Current local-agent config-repair flow driven by a `Decision` and the Git-backed `apply_config` path.

### Known gaps

- Service Bus subscription lifecycle is not modeled: no locks, completion, abandon, delivery counts, lock expiry, or DLQ.
- `topic_messages()` is read-only inspection; it does not consume messages.
- `process_local_agent()` bypasses the real message-receive/settlement worker path.
- Multi-node routing is not modeled; the real final-decisions subscription fan-out problem is not represented.
- Azure Function host/indexing behavior and app settings validation are not modeled.
- Key Vault semantics are not modeled.
- Structured failure events are not modeled for malformed blobs or invalid AI output; the simulator raises exceptions directly.
- The fake Service Bus and Cosmos stores are in-memory/local-only, so persistence and concurrency behavior differ from Azure.

---

## 11. Azure infrastructure — `infrastructure/`

### Purpose / Why it exists

This directory provisions the Azure-side runtime for the current cloud pipeline: the resource group, Cosmos DB, Blob Storage, Service Bus, Key Vault, Application Insights, a Linux App Service plan, a function-app storage account, and the four Azure Function Apps for `token_service`, `log_router`, `analysis_agent`, and `decision_agent`. The repo is split into four Terraform modules so stateful resources can be created first, then consumed by later modules without a large monolithic plan. The apply order `01-networking → 02-cosmos → 03-blob-storage → 04-stateless` matches those dependencies: the resource group exists first, then Cosmos DB and Blob Storage are created inside it, and only then are the stateless services wired to those backing resources. In the current code, `01-networking` is only the shared resource-group foundation; there is no VNet/subnet/NSG/private-endpoint layer yet. The directory also includes the operator runbooks and deployment helpers used to apply, destroy, package, deploy, and smoke-test the stack.

### Module 01 — Networking

**What it creates**
- `azurerm_resource_group.main`
- Resource group name: `rg-${var.project_name}-${var.environment}`
- Location: `var.location`
- Tags from `local.tags`

**What it does not create**
- No VNet, subnets, NSGs, private endpoints, route tables, or load balancers are defined in `main.tf`.

**Variables**
- `environment` (default `dev`)
- `location` (default `polandcentral`)
- `project_name` (default `project-healer`)

**Outputs**
- `resource_group_name`
- `resource_group_location`
- `resource_group_id`

**Connections to other modules**
- Later modules resolve the same RG name pattern (`rg-${project_name}-${environment}`) via data sources rather than direct Terraform state wiring.
- The RG output values match the naming convention used by `02-cosmos`, `03-blob-storage`, `04-stateless`, and the helper scripts.

### Module 02 — Cosmos DB

**What it creates**
- `data.azurerm_resource_group.main` lookup
- `azurerm_cosmosdb_account.main`
  - Name: `cosmos-${project_name}-${environment}`
  - Kind: `GlobalDocumentDB`
  - Offer type: `Standard`
  - Consistency: `Session`
  - Geo location: single region at `var.location`
  - If `var.cosmosdb_offer_type == "Serverless"`, it adds the `EnableServerless` capability
- `azurerm_cosmosdb_sql_database.main`
  - Name: `project-healer`
- SQL containers, all with partition key `/node_id` and `partition_key_version = 2`:
  - `observations`
  - `node-state-current`
  - `decisions`
  - `execution-results`
  - `config-snapshots`
  - `repair-traces`
  - `service-status`

**What it does not create**
- No explicit throughput/autoscale blocks.
- No explicit `indexing_policy` blocks.
- No TTL, unique-key, or container-specific lifecycle rules in the Terraform.

**Variables**
- `environment` (default `dev`)
- `project_name` (default `project-healer`)
- `location` (default `polandcentral`)
- `cosmosdb_offer_type` (default `Serverless`; validation allows `Standard` or `Serverless`)

**Outputs**
- `cosmosdb_endpoint`
- `cosmosdb_database_name`
- `cosmosdb_account_id`
- `cosmosdb_account_name`

**Connections**
- `04-stateless` reads the Cosmos account and database by name and injects the endpoint/key/database values into the `decision` Function App.
- `local_agent` uses the same database name and container names for persistence (`observations`, `node-state-current`, `decisions`, `execution-results`, `config-snapshots`, `repair-traces`, `service-status`).
- `scripts/check-deployment.sh` validates the Cosmos account and all seven SQL containers.

### Module 03 — Blob Storage

**What it creates**
- `data.azurerm_resource_group.main` lookup
- `azurerm_storage_account.logs`
  - Name: `st${project_name}${environment}` with hyphens removed
  - SKU: Standard, LRS
  - TLS minimum: `TLS1_2`
  - Public nested items disabled
  - Blob properties:
    - `versioning_enabled = false`
    - delete retention policy = `var.log_retention_days`
- `azurerm_storage_container.logs`
  - Name: `logs`
  - Access: `private`
- `azurerm_storage_management_policy.log_cleanup`
  - Rule name: `cleanup-old-logs`
  - Applies to `logs/` prefix and `blockBlob`
  - Deletes base blobs and snapshots after `var.log_retention_days`

**What it does not create**
- No storage network rules, CORS rules, private endpoints, or queue/file/table resources.

**Variables**
- `environment` (default `dev`)
- `project_name` (default `project-healer`)
- `location` (default `polandcentral`)
- `log_retention_days` (default `30`)

**Outputs**
- `logs_storage_account_name`
- `logs_storage_account_id`
- `logs_container_name`

**Connections**
- `token_service` uses the storage account name, container name, and Key Vault secret name for the storage account key.
- `log_service` uploads log batches into the `logs` container through a SAS URL issued by `token_service`.
- `log_router` binds to the `logs` container with `LOGS_STORAGE_CONNECTION`.
- `scripts/check-deployment.sh` verifies the storage account exists.

### Module 04 — Stateless (Functions + Service Bus)

**What it creates**
- Data lookups:
  - `data.azurerm_resource_group.main`
  - `data.azurerm_client_config.current`
  - `data.azurerm_cosmosdb_account.main`
  - `data.azurerm_cosmosdb_sql_database.main`
  - `data.azurerm_storage_account.logs`
- Service Bus:
  - `azurerm_servicebus_namespace.main`
  - `azurerm_servicebus_topic.analysis_input` (`analysis-input`)
  - `azurerm_servicebus_topic.analysis_results` (`analysis-results`)
  - `azurerm_servicebus_topic.final_decisions` (`final-decisions`)
  - `azurerm_servicebus_subscription.analysis_agent` (`analysis-agent`)
  - `azurerm_servicebus_subscription.decision_agent` (`decision-agent`)
  - `azurerm_servicebus_subscription.local_agent` (`local-agent`)
  - `azurerm_servicebus_namespace_authorization_rule.shared_access` (`SharedAccessPolicy`)
- Key Vault:
  - `azurerm_key_vault.main`
  - Access policy for the shared function identity (`azurerm_key_vault_access_policy.func`)
  - Access policy for the current Terraform operator (`azurerm_key_vault_access_policy.current_operator`)
  - Secrets:
    - `OpenCodeApiKey`
    - `ServiceBusConnection`
    - `StorageAccountKey`
    - `LogsStorageConnection`
- Monitoring and runtime hosting:
  - `azurerm_application_insights.main`
  - `azurerm_user_assigned_identity.func`
  - `azurerm_service_plan.main` (Linux, `Y1`)
  - `azurerm_storage_account.func` (function host storage)
- Function Apps:
  - `azurerm_linux_function_app.token`
  - `azurerm_linux_function_app.router`
  - `azurerm_linux_function_app.analysis`
  - `azurerm_linux_function_app.decision`
- RBAC assignments for the shared identity:
  - `Storage Blob Data Contributor` on the logs storage account
  - `Storage Account Contributor` on the function storage account
  - `Azure Service Bus Data Sender` on the Service Bus namespace
  - `Azure Service Bus Data Receiver` on the Service Bus namespace
  - `Cosmos DB Operator` on the Cosmos account
  - `Key Vault Secrets User` on the Key Vault

**What it does not create**
- No VNet integration, private endpoints, or dedicated networking for the Function Apps or Key Vault.
- No deployment slots.
- No queue or subscription filters beyond the named subscriptions above.

**Variables**
- `environment` (default `dev`)
- `project_name` (default `project-healer`)
- `location` (default `swedencentral`)
- `opencode_api_key` (sensitive)
- `node_api_key` (sensitive; validated non-empty)
- `opencode_api_url` (default `https://opencode.ai/zen/go/v1/chat/completions`)
- `opencode_model` (default `deepseek-v4-flash`)
- `servicebus_sku` (default `Standard`; validation allows `Standard` or `Premium`)

**Outputs**
- `servicebus_namespace_name`
- `servicebus_connection_string` (sensitive)
- `key_vault_name`
- `function_app_names`
- `token_function_url`
- `analysis_opencode_config`
- `managed_identity_principal_id`
- `application_insights_connection_string` (sensitive)

**Topic / subscription naming table**

| Topic | Subscription | Producer(s) | Consumer |
|---|---|---|---|
| `analysis-input` | `analysis-agent` | `log_router`, `local_agent` observations | `analysis_agent` |
| `analysis-results` | `decision-agent` | `analysis_agent` | `decision_agent` |
| `final-decisions` | `local-agent` | `decision_agent` | `local_agent` |

**Function App app settings**

| Function App | Key app settings |
|---|---|
| `token` | `AZURE_CLIENT_ID`, `STORAGE_ACCOUNT_NAME`, `LOGS_CONTAINER_NAME`, `STORAGE_ACCOUNT_KEY_SECRET`, `NODE_API_KEY`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, `KEYVAULT_NAME` |
| `router` | `AZURE_CLIENT_ID`, `SERVICEBUS_CONNECTION` (Key Vault reference), `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME`, `LOGS_STORAGE_CONNECTION` (Key Vault reference), `APPLICATIONINSIGHTS_CONNECTION_STRING`, `KEYVAULT_NAME` |
| `analysis` | `AZURE_CLIENT_ID`, `SERVICEBUS_CONNECTION` (Key Vault reference), `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME`, `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME`, `KEYVAULT_NAME`, `OPENCODE_API_KEY_SECRET`, `OPENCODE_API_URL`, `OPENCODE_MODEL`, `APPLICATIONINSIGHTS_CONNECTION_STRING` |
| `decision` | `AZURE_CLIENT_ID`, `SERVICEBUS_CONNECTION` (Key Vault reference), `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME`, `SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME`, `COSMOSDB_ENDPOINT`, `COSMOSDB_KEY`, `COSMOSDB_DATABASE_NAME`, `KEYVAULT_NAME`, `APPLICATIONINSIGHTS_CONNECTION_STRING` |

**Connections**
- This module consumes the RG from `01-networking`, the Cosmos DB from `02-cosmos`, and the logs storage account from `03-blob-storage`.
- The shared user-assigned identity is attached to every Function App and is the identity used for Key Vault reference resolution.
- The `token` app gets the logs storage name/container plus the `StorageAccountKey` secret name; `analysis` gets the OpenCode secret name and model/URL; `router` and `analysis` both consume the Service Bus connection secret; `decision` receives Cosmos connection values directly.
- The Service Bus topics here define the message path used by `token_service`/`log_router`/`analysis_agent`/`decision_agent` and the VM-side `local_agent`.
- `scripts/deploy-functions.sh` deploys code into these four Function Apps by name.

### Deploy / apply / destroy scripts

**`infrastructure/apply.sh`**
- Runs from `infrastructure/`.
- Iterates in this order: `01-networking`, `02-cosmos`, `03-blob-storage`, `04-stateless`.
- For each module it runs `terraform init` and `terraform apply -auto-approve`.
- The order matters because later modules read the resource group and backing services created earlier.

**`infrastructure/destroy.sh`**
- Runs the same Terraform modules in reverse order.
- Order: `04-stateless`, `03-blob-storage`, `02-cosmos`, `01-networking`.
- For each module it runs `terraform init` and `terraform destroy -auto-approve`.
- This protects stateful resources by removing consumers before data stores.

**`infrastructure/commands.md`**
- The operator runbook for bring-up.
- Describes the expected flow: enter the infra shell, apply Terraform, deploy the Function App code, render VM env files, copy them onto the VM, and run smoke checks.
- It also documents the Azure login/subscription prerequisites and the derived naming conventions.

**`infrastructure/flake.nix`**
- Defines the infrastructure dev shell.
- Installs `git`, `curl`, `zip`, `azure-cli`, and `terraform`.
- Automatically sources the repo-root `.env` file when present, so the shell picks up `TF_VAR_opencode_api_key`, `TF_VAR_node_api_key`, and other derived names used by the scripts.

**`scripts/deploy-functions.sh`**
- Takes `<resource-group> <environment> <service>...` with services `token`, `router`, `analysis`, `decision`.
- Maps each service to the correct Function App name:
  - `func-${PROJECT_NAME}-${ENV}-token`
  - `func-${PROJECT_NAME}-${ENV}-router`
  - `func-${PROJECT_NAME}-${ENV}-analysis`
  - `func-${PROJECT_NAME}-${ENV}-decision`
- Builds zip packages under `.build/functions/`.
- Uses `uv export --format requirements-txt` to generate dependencies, copies the service `src/` tree, adds `host.json`, and includes `schemas` for the non-token services that need shared models.
- Deploys each zip with `az functionapp deployment source config-zip --build-remote true`.
- Calls `syncfunctiontriggers` after each deployment.
- The deployed runtime is Python 3.11, matching the Function App configuration in Terraform.

**`scripts/check-deployment.sh`**
- Reports the live status of the Azure side and the VM side.
- Verifies:
  - Azure login / subscription access
  - the resource group
  - Service Bus namespace, topics, and subscriptions
  - Cosmos account and all SQL containers
  - logs storage account and function-host storage account
  - Key Vault and key secrets
  - Application Insights
  - Linux App Service plan
  - all four Function Apps
  - local zip artifacts under `.build/functions/`
  - VM qemu process, SSH port, and repo files
- It supports human-readable and JSON output.

**`scripts/phase4-verify.sh`**
- Smoke-tests the decision receive path.
- Confirms `final-decisions` and the `local-agent` subscription exist.
- Optionally checks `/etc/phoe-nix/local-agent.env` on the VM.
- Publishes a `no_action` decision and tells the operator what journal command to run to confirm receipt.

**`scripts/phase5-verify.sh`**
- Exercises the repair loop end-to-end.
- Confirms the `final-decisions/local-agent` subscription, VM SSH reachability, `SERVICEBUS_ENABLED=1`, Ollama reachability, and the config repo path.
- Publishes an `apply_config` decision, then watches Cosmos `service-status` for `decision/received` and a terminal status.
- Prints the journal command needed to inspect the VM-side repair loop.

**`infrastructure/render-vm-env.sh`**
- Fetches Azure values and renders two VM env files: `log-service.env` and `local-agent.env`.
- Requires `NODE_API_KEY` / `TF_VAR_node_api_key` and Azure CLI login.
- Derives `SB_NAMESPACE` from the tenant suffix when not explicitly set.
- Reads the Service Bus connection string from `SharedAccessPolicy` and the token function key from Azure Functions.
- Can optionally fetch Cosmos endpoint/key with `--cosmos on|off`.
- Writes:
  - `log-service.env` with `TOKEN_SERVICE_URL` and `NODE_API_KEY`
  - `local-agent.env` with Service Bus settings, Cosmos settings, repo path/settings, rebuild commands, Ollama config, cooldown limits, and polling intervals
- The script is the bridge between cloud infrastructure and the VM-side runtime.

### Key Vault and secrets

- `OpenCodeApiKey` stores the OpenCode API key from `var.opencode_api_key`.
- `ServiceBusConnection` stores the Service Bus namespace primary connection string for the `SharedAccessPolicy` rule.
- `StorageAccountKey` stores the primary access key for the logs storage account.
- `LogsStorageConnection` stores the primary connection string for the logs storage account.
- The function apps do not all read secrets the same way:
  - `token_service` is configured with `KEYVAULT_NAME` and `STORAGE_ACCOUNT_KEY_SECRET`, then reads the secret at runtime.
  - `analysis_agent` uses `KEYVAULT_NAME` and `OPENCODE_API_KEY_SECRET`, then reads the secret at runtime.
  - `log_router` and `analysis_agent` consume `SERVICEBUS_CONNECTION` and `LOGS_STORAGE_CONNECTION` through Key Vault references in app settings.
  - `decision_agent` receives Cosmos values directly in app settings.
- `azurerm_key_vault.main` allows the shared function identity to `Get` and `List` secrets, and the current Terraform operator can also manage secrets.

### Identity

- A single user-assigned managed identity, `azurerm_user_assigned_identity.func`, is attached to all four Function Apps.
- Every Function App sets `key_vault_reference_identity_id` to that identity so Key Vault references resolve under that principal.
- The identity is granted:
  - blob data access on the logs storage account
  - contributor access on the function host storage account
  - Service Bus send and receive on the namespace
  - Cosmos DB Operator on the Cosmos account
  - Key Vault Secrets User on the Key Vault
- `azurerm_key_vault_access_policy.func` gives the same identity `Get` and `List` permissions for secret lookup.

### Known issues / notes

- `connectivity-analysis.md` flags that the rendered VM env omits `NODE_ID`, which can break `log_service` startup and node matching.
- The same analysis flags the shared `final-decisions/local-agent` subscription as unsafe for multi-node deployments.
- The Function Apps rely on `application_stack.python_version = "3.11"`, but no `FUNCTIONS_WORKER_RUNTIME=python` setting is present in app settings.
- `render-vm-env.sh` currently writes an Ollama model override that may not match the in-code default; the repo notes a model-override mismatch risk.
- The infrastructure intentionally keeps Key Vault network access simple (`default_action = "Allow"`, `bypass = "AzureServices"`) rather than using private endpoints.
- There are no VNet/private-endpoint networking resources yet, so the current stack is not network-isolated.
- Default locations are not uniform across modules: `01-networking`, `02-cosmos`, and `03-blob-storage` default to `polandcentral`, while `04-stateless` defaults to `swedencentral`; override `location` if you want a single region.

---

## 12. Operator scripts — `scripts/`

This section catalogs every script in `scripts/`, grouped by purpose.

### Test / simulator entrypoints

#### `scripts/test.sh`

- **Purpose:** Run the unit-test suite for every service package and the simulator from one repo-level command.
- **What it does:**
  - Iterates over `schemas`, `token_service`, `log_service`, `log_router`, `analysis_agent`, `decision_agent`, `local_agent`, and `simulator`.
  - Uses `nix develop` when a service has a flake and `nix` is available; otherwise falls back to `nix shell` or plain `uv`.
  - Runs `uv sync --refresh` before executing tests so the environment matches the package metadata.
  - Uses `python -m unittest discover -s tests -p 'test*.py'` for each package with a `tests/` directory.
  - Switches to Python 3.14 for `local_agent` and `simulator`; uses Python 3.11 for the other packages.
- **Prerequisites:** `git`, `uv`, and either `nix` or a working host `uv` environment; each service package must have a valid `pyproject.toml` or flake when the script expects it.

#### `scripts/simulate-deployment.sh`

- **Purpose:** Run the simulator package as the repo's end-to-end deployment smoke test.
- **What it does:**
  - Resolves the repo root and enters `simulator/`.
  - Prefers `nix develop` when available; otherwise uses `nix shell` or host `uv`.
  - Runs `uv sync --refresh` and then `uv run simulate_pipeline`.
  - Prints the simulator's JSON summary to stdout.
- **Prerequisites:** `nix` or `uv`, plus the simulator package metadata and dependencies.

#### `scripts/run-mock-simulation.sh`

- **Purpose:** Orchestrate the mocked Azure + VM simulation on a remote host.
- **What it does:**
  - Syncs the local `phoe-nix` repo and the config repo to a remote host with `rsync`.
  - Starts `scripts/mock_azure.py` on the remote host if it is not already running.
  - Boots a fresh guest VM via the config repo's `run-vm.sh` if needed.
  - Prepares a guest source tree containing `local_agent`, `log_service`, `schemas`, the mock env files, and helper scripts.
  - Seeds a bare Git origin for the config repo, generates/sanitizes `hardware-configuration.nix`, and launches the updated local-agent daemon with `start-updated-local-agent.sh`.
  - Publishes a mock decision with `publish-mock-decision.py`, waits for Cosmos execution results, and prints `execution-results`, `repair-traces`, `service-status`, and `analysis-input` from the mock server.
- **Prerequisites:** SSH access to the remote host, `rsync`, `sshpass`, `nix`, the VM launch tooling in the config repo, and a remote host that can run the mock server and VM.

### Deployment / infra

#### `scripts/deploy-functions.sh`

- **Purpose:** Package and deploy the Azure Function apps for token/router/analysis/decision.
- **What it does:**
  - Accepts `<resource-group> <environment> <service> [<service> ...]`.
  - Maps service names to directories and function app names (`func-${PROJECT_NAME}-${ENVIRONMENT}-...`).
  - Runs `uv export --no-hashes --format requirements-txt` to generate a deployment requirements file.
  - Copies each function's `src/` tree into `.build/functions/<service>/`, writes a `host.json`, and for router/analysis/decision also copies `schemas/src/schemas/` into the staging bundle.
  - Filters out editable `schemas` references from the exported requirements before zipping.
  - Deploys each zip with `az functionapp deployment source config-zip --build-remote true`.
  - Calls the Azure `syncfunctiontriggers` REST endpoint after deployment.
- **Prerequisites:** `az` CLI logged in, `zip`, `uv`, and either `nix` or a working host Python environment; the function apps and resource group must already exist.

#### `scripts/check-deployment.sh`

- **Purpose:** Report live deployment status for cloud resources, the VM, and built code artifacts.
- **What it does:**
  - Supports `--json`, `--quick`, `--clear-cache`, and `--help`.
  - Caches Azure bearer tokens to avoid repeated `az account get-access-token` calls.
  - Checks the resource group, Service Bus namespace/topics/subscriptions, Cosmos DB account and containers, log storage, function storage, Key Vault secrets, Application Insights, App Service plan, and deployed function apps.
  - Checks local artifacts and VM readiness: `.build/functions/*.zip`, a `qemu-kvm` process named `nixos`, SSH reachability, and the rendered config repo state.
  - Emits a grouped human-readable report or a machine-readable JSON report.
- **Prerequisites:** `az`, `curl`, `jq`, `git`, `pgrep`, `timeout`, and SSH access for the VM checks; intended to be run from `infrastructure/` or any shell with the repo root available.

#### `scripts/phase4-verify.sh`

- **Purpose:** Smoke-test the VM-side `final-decisions` receive path.
- **What it does:**
  - Accepts `--node-id`, `--ssh`, `--ssh-port`, `--env-path`, `--service`, `--action`, `--severity`, `--confidence`, `--decision-id`, `--analysis-id`, `--skip-vm-check`, and `--help`.
  - Derives the Service Bus namespace from `AZURE_TENANT_SUFFIX` or `az account show --query tenantId -o tsv`.
  - Verifies that the `final-decisions` topic and `local-agent` subscription exist.
  - Optionally SSHes into the VM and checks `/etc/phoe-nix/local-agent.env`, `SERVICEBUS_ENABLED=1`, `SERVICEBUS_CONNECTION`, and `systemctl is-active local_agent`.
  - Calls `scripts/publish-test-decision.sh` to publish a `no_action` (or caller-specified) `Decision`.
  - Prints the `journalctl` command to run on the VM and explains what to look for.
- **Prerequisites:** Azure CLI login, a reachable SSH target, and a running `local_agent` service if the VM check is enabled.

#### `scripts/phase5-verify.sh`

- **Purpose:** Trigger and observe the real local-agent repair loop end-to-end.
- **What it does:**
  - Accepts `--decision-id`, `--action`, `--remediation-text`, `--analysis-summary`, `--watch-seconds`, `--skip-vm-check`, `--dry-run`, and `--help`.
  - Validates that `ACTION` is one of `no_action`, `apply_config`, `restart_service`, `rebuild`, or `rollback`.
  - Derives the Service Bus namespace like Phase 4 and verifies the `final-decisions` topic and `local-agent` subscription.
  - Optionally SSHes into the VM, checks the env file, confirms Ollama is reachable from the VM, and checks that `configuration.nix` exists in the config repo.
  - Publishes an `apply_config` decision with `publish-test-decision.sh`.
  - Polls Cosmos `service-status` for `decision/received` and then a terminal state (`completed`, `skipped`, `failed`, or `blocked`).
  - Uses `az cosmosdb keys list` plus a temporary `nix-shell` Python environment with `azure-cosmos` to query Cosmos.
  - Prints a final journal command for inspecting the local-agent logs on the host.
- **Prerequisites:** Azure CLI login, SSH access to the VM, Cosmos access, and a live `local_agent` + Ollama path; this script intentionally drives the mutation path.

### Live Azure / VM E2E

#### `scripts/run-live-azure-vm-e2e.py`

- **Purpose:** Exercise the real Azure pipeline and the real VM repair loop in one run.
- **What it does:**
  - Parses a large operator-oriented CLI: `--project-name`, `--env`, `--resource-group`, `--servicebus-namespace`, `--cosmos-account`, `--cosmos-database`, `--token-app`, `--node-id`, `--node-api-key`, `--vm-ssh-target`, `--vm-ssh-port`, `--vm-env-path`, `--vm-service-name`, `--vm-sudo-password`, `--expected-ollama-model`, `--package`, `--package-candidates`, `--blob-timeout`, `--topic-timeout`, `--repair-timeout`, and `--skip-smoke-test`.
  - Loads `.env`, resolves Azure naming conventions, and requires `NODE_API_KEY` or `TF_VAR_node_api_key`.
  - Optionally runs the repo's Azure smoke test before starting the live run.
  - Verifies the VM is reachable, `local_agent` is active, the env file is configured, Ollama is reachable, the expected model is present, and the config repo contains the target file.
  - Selects a package that is absent from both the current VM and the config file so the test can safely add it.
  - Creates temporary debug subscriptions, uploads a fake log batch through `token_service`, waits for `log_router → analysis-input`, waits for `analysis_agent → analysis-results`, and waits for `decision_agent → final-decisions`.
  - Polls Cosmos `service-status`, `execution-results`, and `repair-traces`, then re-reads the remote Git repo state to confirm the repair changed the repo HEAD and the config file.
  - Cleans up the debug subscriptions in a `finally` block.
- **Prerequisites:** Azure CLI login, working Azure resources, VM SSH access, Cosmos DB access, a reachable Ollama endpoint from the VM, a writable config repo on the VM, and the repo's Python/Azure SDK environment.

#### `scripts/run-live-azure-vm-e2e.sh`

- **Purpose:** Run `run-live-azure-vm-e2e.py` inside a repo-managed Python environment.
- **What it does:**
  - Prefers `nix develop` in the `simulator/` flake.
  - Falls back to `uv sync --refresh` and `uv run` if Nix is unavailable.
  - Passes all command-line arguments through to the Python script.
- **Prerequisites:** The simulator environment can provide the Azure SDK dependencies required by the live script.

#### `scripts/run-live-ollama-pipeline.py`

- **Purpose:** Exercise the live Azure log pipeline while running the analysis step locally against Ollama.
- **What it does:**
  - Parses `--entry-point` (`blob` or `analysis-input`), `--case` (`cowsay` or `sshd`), `--node-id`, `--hostname`, `--project-name`, `--env`, `--resource-group`, `--token-app`, `--analysis-app`, `--servicebus-namespace`, `--node-api-key`, `--ollama-url`, `--model`, `--analysis-timeout`, `--message-timeout`, `--blob-message-timeout`, and `--keep-analysis-app-running`.
  - Loads `.env`, resolves Azure naming conventions, and requires a node API key.
  - Creates debug subscriptions on `analysis-input`, `analysis-results`, and `final-decisions`.
  - Optionally stops the deployed `analysis_agent` Function App so the local Ollama-driven analysis path does not race with the cloud function.
  - Either uploads a fake blob through `token_service` or publishes a synthetic normalized log directly to `analysis-input`.
  - Calls the real `analysis_agent.message_handler.analyze_message()` locally with a custom `call_ollama_api()` that talks to Ollama, then republishes the `AnalysisResult` to Azure.
  - Waits for the decision from `decision_agent` and prints the remediation hint / action.
  - Restarts the analysis app and deletes debug subscriptions when needed.
- **Prerequisites:** Azure resources, Service Bus access, a reachable Ollama endpoint, and permission to stop/start the deployed analysis Function App.

### Local-agent helpers

#### `scripts/manual-local-agent-integration.sh`

- **Purpose:** Run the local-agent manual integration entrypoint from the repo environment.
- **What it does:**
  - Uses `nix shell` when available, otherwise falls back to host `uv`.
  - Runs `uv run local_agent_manual_integration` from `local_agent/`.
- **Prerequisites:** `local_agent` must be installable in the current environment.

#### `scripts/run-guest-manual-integration.sh`

- **Purpose:** Run the local-agent manual integration on the guest VM using the service's runtime environment.
- **What it does:**
  - Reads the `ExecStart` path from `systemctl show local_agent --property=ExecStart --value`.
  - Extracts the interpreter path, `PYTHONPATH`, and `PATH` from the generated runner script.
  - Sources `/etc/phoe-nix/local-agent.env.defaults` and `/etc/phoe-nix/local-agent.env`, preserving any existing overrides for `GIT_SSH_COMMAND`, `CONFIG_REPO_URL`, `CONFIG_REPO_BRANCH`, `REBUILD_TEST_COMMAND`, and `REBUILD_SWITCH_COMMAND`.
  - Sets `NODE_ID` and `LOCAL_AGENT_MANUAL_REPO_PATH` defaults.
  - Executes `local_agent.manual_integration.run_manual_integration()` and prints JSON.
- **Prerequisites:** The `local_agent` systemd service must be installed on the guest, and the env files must be readable.

#### `scripts/start-updated-local-agent.sh`

- **Purpose:** Start the local-agent daemon from the source tree staged on the guest.
- **What it does:**
  - Changes into `NODE_SOURCE_ROOT` (default `/home/user/phoe-nix-node-src`).
  - Sources `scripts/mock-local-agent.env`.
  - Exports `NODE_ID`, `LOCAL_AGENT_RUN_MODE=daemon`, and a `PYTHONPATH` that points at `local_agent/src` and `schemas/src`.
  - Executes a pinned Python interpreter with `python -m local_agent.main`.
- **Prerequisites:** The guest staging tree exists, the mock env file is present, and the pinned Python environment is available.

#### `scripts/verify-vm-repo-write.sh`

- **Purpose:** Verify that the VM can write back to the config repo over Git/SSH.
- **What it does:**
  - Clones `CONFIG_REPO_URL` (default `git@github.com:Free-Rat/phoe-nix-config.git`) into a temporary directory.
  - Creates a throwaway branch, appends a timestamped line to `README.md` (or creates one), commits it, pushes it, verifies the branch exists remotely, and then deletes the branch.
  - Prints the verified branch name.
- **Prerequisites:** Git access to the repo, SSH auth, and push/delete permission on the remote branch.

#### `scripts/sanitize-hardware-config.py`

- **Purpose:** Remove the `/nix/store` filesystem block from a generated hardware configuration.
- **What it does:**
  - Reads a single file path argument.
  - Skips the block that starts at `fileSystems."/nix/store" =`.
  - Tracks brace depth until the block ends, then writes the filtered file back.
- **Prerequisites:** A valid generated `hardware-configuration.nix` path is passed as argv[1].

### Mock helpers

#### `scripts/mock_azure.py`

- **Purpose:** Provide a tiny HTTP server that stands in for token issuance, Blob Storage, Service Bus, and Cosmos DB in the mock simulation.
- **What it does:**
  - Exposes CLI flags `--host`, `--port`, and `--state-dir`.
  - Stores state under `state-dir` in `blobs/`, `cosmos/`, and `servicebus/` directories.
  - Handles `POST /token`, `PUT /blob/...`, `POST /servicebus/topics/<topic>/publish`, `POST /servicebus/topics/<topic>/subscriptions/<sub>/receive`, `POST /cosmos/databases/<db>/containers/<container>/upsert`, `POST /reset`, `GET /health`, `GET /servicebus/topics/<topic>`, and `GET /cosmos/databases/<db>/containers/<container>`.
  - Keeps topic messages in memory and tracks per-subscription receive offsets.
  - Writes blobs and Cosmos documents to disk so the simulation can inspect them later.
- **Prerequisites:** Python 3, ability to bind the chosen host/port, and a writable state directory.

#### `scripts/publish-mock-decision.py`

- **Purpose:** Publish a synthetic `Decision` into the mock Service Bus server.
- **What it does:**
  - Accepts `--base-url`, `--topic`, `--node-id`, `--decision-id`, `--analysis-id`, `--analysis-summary`, and `--remediation-text`.
  - Builds a fixed `Decision` payload with `action="apply_config"`, `severity="critical"`, `confidence=0.9`, an empty `command`, and a UTC timestamp.
  - Sends the JSON body to `/servicebus/topics/<topic>/publish` with `urllib.request`.
- **Prerequisites:** A reachable `mock_azure.py` instance at the given base URL.

#### `scripts/publish-test-decision.sh`

- **Purpose:** Publish a schema-valid `Decision` to the real Azure Service Bus topic.
- **What it does:**
  - Supports `--node-id`, `--decision-id`, `--analysis-id`, `--action`, `--severity`, `--confidence`, `--summary`, `--remediation`, `--topic`, `--body-file`, and `--help`.
  - Validates `ACTION` against the allowed set and `SEVERITY` against `critical|warning|info`.
  - Resolves `SB_NAMESPACE` from `AZURE_TENANT_SUFFIX` or `az account show` when needed.
  - Resolves the Service Bus connection string from `SERVICEBUS_CONNECTION` or from the `SharedAccessPolicy` authorization rule.
  - Writes the decision JSON to a temp file, validates that the required fields and ranges are present, and publishes it with a small Python script using `azure-servicebus`.
  - Runs the publisher through `nix-shell -p python313.withPackages (ps: [ ps.azure-servicebus ])` so the host Python environment does not need the SDK installed.
- **Prerequisites:** Azure CLI login or an existing `SERVICEBUS_CONNECTION`, plus `nix-shell` and the Azure Service Bus Python SDK.

#### `scripts/mock-local-agent.env`

- **Purpose:** Provide a mock VM environment for `local_agent` during the simulated integration run.
- **What it contains:**
  - `SERVICEBUS_ENABLED=1`
  - `SERVICEBUS_CONNECTION=Endpoint=mock://10.0.2.2:8088`
  - `COSMOSDB_ENABLED=1`
  - `COSMOSDB_ENDPOINT=mock+http://10.0.2.2:8088/cosmos`
  - `COSMOSDB_KEY=mock-key`
  - `COSMOSDB_DATABASE_NAME=project-healer`
  - `CONFIG_REPO_URL=/home/user/phoe-nix-config-origin.git`
  - `CONFIG_REPO_BRANCH=main`
  - `CONFIG_REPO_PATH=/var/lib/phoe-nix-config-repo`
  - `OLLAMA_BASE_URL=http://10.0.2.2:11434`
  - `OLLAMA_MODEL=gemma4:e4b`
  - `REBUILD_TEST_COMMAND=...nixos-rebuild test...`
  - `REBUILD_SWITCH_COMMAND=...nixos-rebuild switch...`
- **Prerequisites:** This file is consumed by the mock VM launch scripts and the guest local-agent runner.

#### `scripts/mock-log-service.env`

- **Purpose:** Provide a minimal mock log-service configuration.
- **What it contains:**
  - `TOKEN_SERVICE_URL=http://10.0.2.2:8088/token`
  - `BATCH_SIZE=1`
  - `FLUSH_INTERVAL_SECONDS=2`
- **Prerequisites:** The mock token service is reachable at the local mock Azure endpoint.

---

## 13. Cross-reference: who calls whom

This is a single-glance view of the call graph, including every transport mechanism (HTTP, Service Bus, Blob trigger, Cosmos, Key Vault, Ollama, Git, `nixos-rebuild`, subprocess).

| Caller | Callee | Mechanism | Notes |
|---|---|---|---|
| `log_service` (Node) | `token_service` | HTTP POST `/token` | Headers `X-Node-ID`, optional `X-API-Key` |
| `log_service` (Node) | Azure Blob Storage | SAS URL PUT | URL returned by `token_service`; path `logs/<node_id>/<uuid>` |
| Blob trigger on `logs/{name}` | `log_router` | Azure Functions binding | Connection `LOGS_STORAGE_CONNECTION` |
| `log_router` | Azure Service Bus topic `analysis-input` | SDK publish | One message per `NormalizedLog` |
| `local_agent` observations | Azure Service Bus topic `analysis-input` | SDK publish | `source="local_agent"`, message id `<node_id>:<timestamp>` |
| Service Bus subscription `analysis-agent` | `analysis_agent` | Azure Functions trigger | Topic `analysis-input` |
| `analysis_agent` | Azure Key Vault | `DefaultAzureCredential` + `SecretClient` | Secret `OpenCodeApiKey` |
| `analysis_agent` | OpenCode HTTP API | `urllib POST` | URL is `OPENCODE_API_URL` |
| `analysis_agent` | Azure Service Bus topic `analysis-results` | SDK publish | `message_kind=analysis_result` |
| Service Bus subscription `decision-agent` | `decision_agent` | Azure Functions trigger | Topic `analysis-results` |
| `decision_agent` | Azure Cosmos DB | `CosmosClient.upsert_item` | Container `decisions`, partition `/node_id` |
| `decision_agent` | Azure Service Bus topic `final-decisions` | SDK publish | `message_kind=decision`, message id = `decision_id` |
| Service Bus subscription `local-agent` | `local_agent` decision worker | SDK receive/complete | Topic `final-decisions` |
| `local_agent` | Ollama HTTP | `POST /api/generate` | URL `OLLAMA_BASE_URL`; prompt built in `repair_planner.py` |
| `local_agent` | `git` CLI | subprocess | `clone` / `fetch` / `reset` / `clean` / `add` / `commit` / `push` |
| `local_agent` | `nixos-rebuild` | subprocess | `test` then `switch` |
| `local_agent` | `/etc/nixos/hardware-configuration.nix` | filesystem read | Copied into repo by `sync_local_hardware_configuration` |
| `local_agent` | Azure Cosmos DB | `CosmosClient.upsert_item` | Containers: `observations`, `execution-results`, `node-state-current`, `config-snapshots`, `repair-traces`, `service-status` |
| `token_service` | Azure Key Vault | `DefaultAzureCredential` + `SecretClient` | Secret `StorageAccountKey` |
| `token_service` | Azure Storage SDK | `generate_blob_sas()` | Local computation, no network call to storage |
| `scripts/run-live-azure-vm-e2e.py` | All of the above (read) | Azure CLI + SDK | End-to-end operator check |
| `scripts/run-live-ollama-pipeline.py` | `analysis_agent` core + Ollama | In-process + HTTP | Runs analysis locally, republishes to Azure |
| `scripts/mock_azure.py` | Mock HTTP endpoints | Python `http.server` | Stands in for token, blob, service bus, cosmos |
| `simulator` | Real service cores + fakes | In-process | The full pipeline in one Python process |
| `infrastructure/render-vm-env.sh` | Azure CLI | `az` | Renders VM env files from deployed state |
| `scripts/check-deployment.sh` | Azure CLI | `az` | Reads live state and reports |

---

## 14. Cross-reference: every Python module at a glance

| Package | Module | Public surface (short) | Documented in |
|---|---|---|---|
| `schemas` | `__init__` | model re-exports | §3 |
| `schemas` | `observation.py` | `Observation` | §3 |
| `schemas` | `normalized_log.py` | `NormalizedLog` | §3 |
| `schemas` | `node_state.py` | `NodeState` | §3 |
| `schemas` | `analysis_result.py` | `AnalysisContext`, `AnalysisResult` | §3 |
| `schemas` | `decision.py` | `Decision` | §3 |
| `schemas` | `execution_result.py` | `ExecutionResult` | §3 |
| `token_service` | `__init__` | — | §5 |
| `token_service` | `main.py` | `main`, `run_cli` | §5 |
| `token_service` | `app.py` | `HttpResult`, `json_response`, `parse_json_body`, `handle_token_request` | §5 |
| `token_service` | `config.py` | `TokenServiceConfig`, `load_config` | §5 |
| `token_service` | `models.py` | `TokenRequest`, `TokenResponse`, `ErrorResponse` | §5 |
| `token_service` | `auth.py` | `AuthenticationError`, `authenticate_node_request` | §5 |
| `token_service` | `keyvault.py` | `build_vault_url`, `read_secret_value` | §5 |
| `token_service` | `sas_generator.py` | `build_blob_name`, `build_blob_path`, `build_blob_url`, `build_upload_sas_token`, `issue_upload_token` | §5 |
| `log_service` | `__init__` | — | §4 |
| `log_service` | `main.py` | `parse_args`, `signal_handler`, `process_entry`, `main` | §4 |
| `log_service` | `config.py` | `LogServiceConfig`, `load_config` | §4 |
| `log_service` | `models.py` | `StorageTokenResponse`, `LogBatch` | §4 |
| `log_service` | `storage.py` | `build_log_payload`, `upload_log_payload` | §4 |
| `log_service` | `uploader.py` | `BatchUploader` (+ methods) | §4 |
| `log_service` | `token_client.py` | `TokenServiceError`, `build_token_request_headers`, `parse_storage_token_response`, `request_storage_token` | §4 |
| `log_router` | `__init__` | — | §6 |
| `log_router` | `main.py` | `publish_messages`, `main`, `run_cli` | §6 |
| `log_router` | `normalizer.py` | `parse_blob_payload`, `timestamp_from_journal`, `normalize_entry`, `normalize_blob` | §6 |
| `analysis_agent` | `__init__` | — | §7 |
| `analysis_agent` | `config.py` | `AnalysisAgentConfig`, `load_config` | §7 |
| `analysis_agent` | `keyvault.py` | `build_vault_url`, `read_secret_value` | §7 |
| `analysis_agent` | `prompt_builder.py` | `build_log_prompt`, `build_observation_prompt`, `build_prompt` | §7 |
| `analysis_agent` | `ai_client.py` | `OpenCodeError`, `build_request_payload`, `build_request_headers`, `extract_response_text`, `strip_markdown_fence`, `parse_json_object`, `call_opencode_api`, `normalize_confidence`, `parse_analysis_response` | §7 |
| `analysis_agent` | `message_handler.py` | `AnalysisInput`, `parse_message`, `analyze_message` | §7 |
| `analysis_agent` | `main.py` | `publish_analysis_result`, `main`, `run_cli` | §7 |
| `decision_agent` | `__init__` | — | §8 |
| `decision_agent` | `config.py` | `DecisionAgentConfig`, `load_config` | §8 |
| `decision_agent` | `cosmos.py` | `upsert_decision_document` | §8 |
| `decision_agent` | `decision_engine.py` | `contains_nix_assignment`, `normalize_suggested_action`, `build_command`, `build_idempotency_key`, `build_decision`, `build_decision_document` | §8 |
| `decision_agent` | `app.py` | `process_analysis_result` | §8 |
| `decision_agent` | `main.py` | `publish_decision`, `main`, `run_cli` | §8 |
| `local_agent` | `__init__` | — | §9 |
| `local_agent` | `main.py` | `run_cli`, `run_once` | §9 |
| `local_agent` | `config.py` | `LocalAgentConfig`, `load_config` | §9 |
| `local_agent` | `runtime.py` | `PersistRequest`, `RuntimeDependencies`, `LocalAgentRuntime`, `observe_once`, `persist_pending`, `handle_decision`, `decision_worker`, `observe_worker`, `persist_worker`, `run_daemon`, `run_runtime_once` | §9 |
| `local_agent` | `bus_client.py` | `publish_message`, `build_topic_receiver`, `receive_messages`, `complete_message` | §9 |
| `local_agent` | `state.py` | `LocalAgentState`, `build_observation_hash`, `has_significant_change`, `within_cooldown`, `can_apply_remediation`, `record_remediation`, `start_remediation`, `update_node_state`, `new_state` | §9 |
| `local_agent` | `system_state.py` | `run_command`, `collect_node_state` | §9 |
| `local_agent` | `monitor.py` | `summarize_node_state`, `infer_severity`, `build_observation`, `should_publish_observation`, `current_state_hash` | §9 |
| `local_agent` | `executor.py` | `CommandResult`, `validate_decision_target`, `derive_execution_command`, `run_subprocess`, `execute_decision` | §9 |
| `local_agent` | `repair_planner.py` | `RepairAttempt`, `RepairOutcome`, `build_repair_prompt`, `extract_config_text`, `sync_local_hardware_configuration`, `execute_repair_loop` | §9 |
| `local_agent` | `ollama_client.py` | `OllamaError`, `generate_text` | §9 |
| `local_agent` | `git_repo.py` | `GitCommandResult`, `run_git_command`, `ensure_repo`, `refresh_repo`, `read_config_text`, `write_config_text`, `current_revision`, `commit_and_push` | §9 |
| `local_agent` | `persistence.py` | `upsert_document` | §9 |
| `local_agent` | `reporter.py` | `summarize_execution`, `build_execution_result`, `build_node_state_document`, `build_observation_document`, `build_config_snapshot_document`, `build_repair_trace_document`, `build_service_status_document`, `current_time` | §9 |
| `local_agent` | `manual_integration.py` | `_manual_command_runner`, `run_manual_integration`, `main` | §9 |
| `simulator` | `__init__` | — | §10 |
| `simulator` | `cli.py` | `main` | §10 |
| `simulator` | `pipeline.py` | many helpers; see §10 | §10 |
| `simulator` | `fakes.py` | `FakeBlobObject`, `FakeBlobStorage`, `FakeServiceBusMessage`, `FakeServiceBus`, `FakeCosmosContainer`, `FakeCosmos`, `FakeLocalAgent`, `FakeConfigRepo`, `FakeKeyVault` | §10 |
| `simulator` | `fixtures.py` | `build_environment`, `sample_log_entries`, `sample_observation` | §10 |
| Scripts (Python) | `scripts/mock_azure.py` | tiny HTTP server | §12 |
| Scripts (Python) | `scripts/publish-mock-decision.py` | mock SB publisher | §12 |
| Scripts (Python) | `scripts/sanitize-hardware-config.py` | Nix filter | §12 |
| Scripts (Python) | `scripts/run-live-azure-vm-e2e.py` | live E2E operator | §12 |
| Scripts (Python) | `scripts/run-live-ollama-pipeline.py` | live pipeline with local Ollama | §12 |

---

## 15. Cross-reference: every Azure resource at a glance

| Module | Resource (Terraform) | Name pattern | Purpose | Documented in |
|---|---|---|---|---|
| 01 | `azurerm_resource_group.main` | `rg-${project}-${env}` | Shared RG foundation | §11 |
| 02 | `azurerm_cosmosdb_account.main` | `cosmos-${project}-${env}` | Cosmos account | §11 |
| 02 | `azurerm_cosmosdb_sql_database.main` | `project-healer` | Single SQL DB | §11 |
| 02 | `azurerm_cosmosdb_sql_container.observations` | `observations` | Local-agent observations | §11 |
| 02 | `azurerm_cosmosdb_sql_container.node_state_current` | `node-state-current` | Current node-state snapshots | §11 |
| 02 | `azurerm_cosmosdb_sql_container.decisions` | `decisions` | Decision audit documents | §11 |
| 02 | `azurerm_cosmosdb_sql_container.execution_results` | `execution-results` | ExecutionResult records | §11 |
| 02 | `azurerm_cosmosdb_sql_container.config_snapshots` | `config-snapshots` | Before/after config text | §11 |
| 02 | `azurerm_cosmosdb_sql_container.repair_traces` | `repair-traces` | Per-attempt repair traces | §11 |
| 02 | `azurerm_cosmosdb_sql_container.service_status` | `service-status` | Service stage/status events | §11 |
| 03 | `azurerm_storage_account.logs` | `st${project}${env}` | Logs storage account | §11 |
| 03 | `azurerm_storage_container.logs` | `logs` | Raw log batches | §11 |
| 03 | `azurerm_storage_management_policy.log_cleanup` | `cleanup-old-logs` | Retention cleanup | §11 |
| 04 | `azurerm_servicebus_namespace.main` | `sb-${project}-${env}` | Service Bus namespace | §11 |
| 04 | `azurerm_servicebus_topic.analysis_input` | `analysis-input` | Log + observation input | §11 |
| 04 | `azurerm_servicebus_topic.analysis_results` | `analysis-results` | AI output | §11 |
| 04 | `azurerm_servicebus_topic.final_decisions` | `final-decisions` | Remediation intent | §11 |
| 04 | `azurerm_servicebus_subscription.analysis_agent` | `analysis-agent` | Consumer for `analysis-input` | §11 |
| 04 | `azurerm_servicebus_subscription.decision_agent` | `decision-agent` | Consumer for `analysis-results` | §11 |
| 04 | `azurerm_servicebus_subscription.local_agent` | `local-agent` | Consumer for `final-decisions` | §11 |
| 04 | `azurerm_servicebus_namespace_authorization_rule.shared_access` | `SharedAccessPolicy` | SAS policy used for env rendering | §11 |
| 04 | `azurerm_key_vault.main` | `kv-${project}-${env}-${rand}` | Secret store | §11 |
| 04 | `azurerm_key_vault_secret.opencode_api_key` | `OpenCodeApiKey` | OpenCode auth | §11 |
| 04 | `azurerm_key_vault_secret.servicebus_connection` | `ServiceBusConnection` | SB connection string | §11 |
| 04 | `azurerm_key_vault_secret.storage_account_key` | `StorageAccountKey` | Logs storage key | §11 |
| 04 | `azurerm_key_vault_secret.logs_storage_connection` | `LogsStorageConnection` | Logs storage connection string | §11 |
| 04 | `azurerm_application_insights.main` | `appi-${project}-${env}` | Telemetry | §11 |
| 04 | `azurerm_user_assigned_identity.func` | `id-${project}-${env}-func` | Shared function identity | §11 |
| 04 | `azurerm_service_plan.main` | `asp-${project}-${env}` | Linux Y1 hosting plan | §11 |
| 04 | `azurerm_storage_account.func` | `st${project}${env}func` | Function host storage | §11 |
| 04 | `azurerm_linux_function_app.token` | `func-${project}-${env}-token` | `token_service` hosting | §11 |
| 04 | `azurerm_linux_function_app.router` | `func-${project}-${env}-router` | `log_router` hosting | §11 |
| 04 | `azurerm_linux_function_app.analysis` | `func-${project}-${env}-analysis` | `analysis_agent` hosting | §11 |
| 04 | `azurerm_linux_function_app.decision` | `func-${project}-${env}-decision` | `decision_agent` hosting | §11 |
| 04 | RBAC: `Storage Blob Data Contributor` on logs SA | — | Function identity for blob reads | §11 |
| 04 | RBAC: `Storage Account Contributor` on func SA | — | Function identity for function storage | §11 |
| 04 | RBAC: `Azure Service Bus Data Sender` on SB NS | — | Function identity for SB publish | §11 |
| 04 | RBAC: `Azure Service Bus Data Receiver` on SB NS | — | Function identity for SB receive | §11 |
| 04 | RBAC: `Cosmos DB Operator` on Cosmos account | — | Function identity for Cosmos reads | §11 |
| 04 | RBAC: `Key Vault Secrets User` on Key Vault | — | Function identity for KV references | §11 |

---

*End of document.*
