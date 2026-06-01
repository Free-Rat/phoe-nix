# Phoe-nix: Implementation Plan

## Project Structure

```
phoe-nix/
├── infrastructure/          # Terraform IaC (done)
├── log_service/             # On-node log collector (stub exists)
├── token_service/           # Upload authorization — NEW
├── log_router/              # Azure Function — NEW
├── analysis_agent/          # Azure Function — NEW
├── decision_agent/          # Azure Function — NEW
├── local_agent/             # On-node remediation — NEW
├── frontend/                # Streamlit UI — NEW
├── schemas/                 # Shared message schemas — NEW
├── idea.md                  # Project specification & decisions
├── PLAN.md                  # This file
└── README.md
```

Each service directory will contain its own `pyproject.toml`, source code, tests, and deployment configuration.

---

## Phase 0: Project Hygiene

**Goal:** Establish code standards and shared schema before writing any service logic.

### 0.1 Add `ruff` configuration

- Add `ruff` config at repo root (`pyproject.toml` or `ruff.toml`) with:
  - Line length: 120
  - Python target: 3.11 (Azure Functions runtime)
  - Rules: pycodestyle, pyflakes, isort, unused imports
- Add `ruff format` as the formatter
- Each service's `pyproject.toml` will reference the root config

### 0.2 Create `schemas/` directory

Define shared JSON/message schemas used across all services:

- `schemas/normalized_log.py` — Message format for normalized log entries (Router → Analysis Agent)
- `schemas/analysis_result.py` — Analysis output format (Analysis Agent → Decision Agent)
- `schemas/decision.py` — Decision format (Decision Agent → Local Agent)
- `schemas/execution_result.py` — Execution result format (Local Agent → Cosmos DB)
- `schemas/__init__.py` — Re-exports all schemas

Each schema will be a Pydantic model with versioning, so services can evolve independently.

### 0.3 Fix typos

- Rename `infrastructure/commnads.md` → `infrastructure/commands.md`

---

## Phase 1: Token Service (Upload Authorization)

**Goal:** Nodes can request short-lived, path-scoped, write-only SAS tokens to upload logs to Blob Storage.

**Depends on:** Phase 0 (schemas), existing Terraform infrastructure.

### 1.1 Service structure

```
token_service/
├── pyproject.toml
├── src/
│   └── token_service/
│       ├── __init__.py
│       ├── main.py            # Azure Function entry point
│       ├── function.json      # HTTP trigger binding
│       ├── sas_generator.py   # SAS token + path creation logic
│       └── auth.py            # Node authentication
└── tests/
    ├── __init__.py
    └── test_sas_generator.py
```

### 1.2 Azure Function: HTTP trigger

- **Route:** `POST /api/token`
- **Request body:** `{ "node_id": "nixos-node-01" }`
- **Response:** `{ "sas_url": "https://<account>.blob.core.windows.net/logs/<node_id>/<uuid>?<sas_token>", "blob_path": "logs/<node_id>/<uuid>", "expires_at": "<ISO8601>" }`

### 1.3 SAS token generation (`sas_generator.py`)

Key behavior:
1. Retrieve the storage account key from Key Vault via Managed Identity (env var `KEYVAULT_NAME` is already set in Terraform)
2. Generate a unique blob path: `logs/{node_id}/{uuid}` — this is the only path the node can write to
3. Create a SAS token scoped to that exact path with:
   - **Permissions:** Write only (`w`)
   - **Expiry:** 5 minutes from now
   - **Resource type:** Blob (`b`)
   - **No list, read, or delete permissions**
4. Return the full SAS URL to the client

The path scoping ensures:
- Node A can never overwrite Node B's logs (different paths)
- A compromised token only grants write access to a single blob path
- The Log Router can trigger on new blobs under `logs/`

### 1.4 Node authentication (`auth.py`)

- Initial approach: API key validation (simple header-based auth)
- Future: Managed Identity or Azure AD integration
- The `node_id` in the request must match the authenticated identity

### 1.5 Terraform integration

The Token Service Azure Function app is already provisioned in `04-stateless/main.tf`:
- Function app name: `func-${local.name_prefix}-token`
- App settings already include: `STORAGE_ACCOUNT_NAME`, `LOGS_CONTAINER_NAME`, `KEYVAULT_NAME`

### 1.6 Testing

- Unit tests for SAS token generation (verify scope, expiry, write-only permissions)
- Unit tests for path uniqueness
- Integration test against live Azure (upload a blob using the generated SAS URL, verify it succeeds)

---

## Phase 2: Log Service (Complete Implementation)

**Goal:** The on-node service reads systemd journal logs and uploads batches to Azure Blob Storage using SAS tokens from the Token Service.

**Depends on:** Phase 1 (Token Service must be deployed and accessible).

### 2.1 Replace stubs in `log_service/src/log_service/main.py`

Current stubs:
```python
def get_storage_token() -> str:
    return "example_token"

def save_to_storage(token: str) -> bool:
    return True
```

Replace with real implementation:

- `get_storage_token(node_id: str) -> tuple[str, str]` — HTTP call to Token Service, returns `(sas_url, blob_path)`
- `upload_to_storage(sas_url: str, data: bytes) -> bool` — Upload log batch to Azure Blob using the SAS URL

### 2.2 Batching strategy

Instead of uploading one journal entry per HTTP call:
1. Accumulate log entries in an in-memory buffer
2. Trigger upload when either:
   - Buffer size exceeds threshold (e.g., 100 entries)
   - Time interval elapsed (e.g., 30 seconds)
   - Graceful shutdown signal received
3. Request a new SAS token for each batch (each batch gets a unique path)

### 2.3 Retry logic

- Exponential backoff on transient failures (network, 5xx from Token Service)
- If SAS token expired mid-batch, request a new one and retry
- If Token Service is unreachable, buffer logs locally and retry later
- Maximum retry count: 3

### 2.4 Dependencies to add

Update `pyproject.toml`:
- `azure-storage-blob` — for blob upload using SAS URL
- `requests` or `aiohttp` — for HTTP call to Token Service
- Keep `systemd-python`

### 2.5 Nix flake update

- Update `log_service/flake.nix` to include new Python dependencies
- Ensure `nix develop` and `nix run .` still work

### 2.6 Testing

- Unit tests for batching logic (mock Azure calls)
- Unit tests for retry logic
- Integration test: full loop with Token Service → Blob Storage

---

## Phase 3: Log Router / Normalizer

**Goal:** When a new blob lands in Azure Blob Storage, parse and normalize the log entries, then publish them to the Service Bus `analysis` topic.

**Depends on:** Phase 2 (Log Service uploading blobs to storage).

### 3.1 Service structure

```
log_router/
├── pyproject.toml
├── src/
│   └── log_router/
│       ├── __init__.py
│       ├── main.py            # Azure Function entry point (blob trigger)
│       ├── function.json      # Blob trigger binding
│       └── normalizer.py      # Log parsing and normalization
└── tests/
    ├── __init__.py
    └── test_normalizer.py
```

### 3.2 Azure Function: Blob trigger

- **Trigger:** New blob in `logs` container
- **Input:** Raw systemd journal entries (JSON format from `systemd.journal.Reader`)
- **Processing:**
  1. Read blob content
  2. Extract node_id from blob path: `logs/{node_id}/{uuid}` → `node_id`
  3. Parse each journal entry
  4. Normalize into structured format using `schemas/normalized_log.py`
  5. Publish one message per entry (or batch of entries) to Service Bus Topic `analysis`

### 3.3 Normalization (`normalizer.py`)

Input (raw journal entry from `systemd.journal.Reader`):
```json
{
  "__REALTIME_TIMESTAMP": "1234567890123456",
  "MESSAGE": "Failed to start nginx.service",
  "_SYSTEMD_UNIT": "nginx.service",
  "PRIORITY": "3",
  "_HOSTNAME": "nixos-node-01",
  "__MONOTONIC_TIMESTAMP": "123456789",
  "SYSLOG_IDENTIFIER": "systemd"
}
```

Output (normalized, using `schemas/normalized_log.py`):
```json
{
  "schema_version": "1.0",
  "node_id": "nixos-node-01",
  "timestamp": "2026-01-01T00:00:00Z",
  "unit": "nginx.service",
  "priority": 3,
  "message": "Failed to start nginx.service",
  "hostname": "nixos-node-01",
  "source": "systemd",
  "blob_path": "logs/nixos-node-01/abc-123"
}
```

### 3.4 Service Bus publishing

- Use Managed Identity for authentication (env var `SERVICEBUS_CONNECTION` from Key Vault)
- Publish to topic name from env var `SERVICEBUS_TOPIC_ANALYSIS_NAME`
- Each message gets a `message_id` for idempotency
- Set `content_type` to `application/json`

### 3.5 Testing

- Unit tests for parsing and normalization logic
- Unit tests for edge cases (malformed entries, missing fields, non-UTF-8)
- Integration test: upload a test blob → verify Service Bus message

---

## Phase 4: Analysis Agent

**Goal:** Consume normalized log messages from the `analysis` topic, analyze them using the OpenCode Go API, and produce structured analysis results.

**Depends on:** Phase 3 (Log Router publishing to `analysis` topic).

### 4.1 Service structure

```
analysis_agent/
├── pyproject.toml
├── src/
│   └── analysis_agent/
│       ├── __init__.py
│       ├── main.py            # Azure Function entry point (Service Bus trigger)
│       ├── function.json      # Service Bus trigger binding
│       ├── ai_client.py      # OpenCode Go API client
│       └── prompt_builder.py # Prompt construction
└── tests/
    ├── __init__.py
    ├── test_ai_client.py
    └── test_prompt_builder.py
```

### 4.2 Azure Function: Service Bus trigger

- **Trigger:** New message on Service Bus Topic `analysis`, subscription `analysis-agent`
- **Processing:**
  1. Deserialize the normalized log message
  2. Build a structured prompt for the AI model
  3. Call OpenCode Go API
  4. Parse the AI response into a structured analysis result
  5. Publish the analysis result to Service Bus Topic `decision`

### 4.3 AI prompt strategy (`prompt_builder.py`)

The prompt should instruct the AI to:
- Identify the type of error (service failure, configuration error, dependency issue, etc.)
- Assess severity (critical, warning, info)
- Identify root cause
- Suggest remediation actions (rollback, restart, rebuild)
- All specific to NixOS context (declarative configuration, generations, `nixos-rebuild`)

System prompt template:
```
You are an expert NixOS system administrator. Analyze the following log entry 
and determine if it indicates a configuration error or system issue.

For each issue found, provide:
1. error_type: one of [service_failure, config_error, dependency_issue, disk_issue, network_issue, other]
2. severity: one of [critical, warning, info]
3. root_cause: brief description of the root cause
4. suggested_action: one of [rollback, restart_service, rebuild, no_action]
5. confidence: float between 0.0 and 1.0

Respond in JSON format matching the AnalysisResult schema.
```

### 4.4 OpenCode Go API client (`ai_client.py`)

- Retrieve API key from Key Vault via Managed Identity (env var `KEYVAULT_NAME`, secret name from `OPENCODE_API_KEY_SECRET`)
- Call the OpenCode Go API with the constructed prompt
- Parse JSON response into `schemas/analysis_result.py` model
- On API failure: push message to DLQ (do not retry indefinitely)
- No fallback provider — OpenCode Go API only, per project decision

### 4.5 Output format (`schemas/analysis_result.py`)

```json
{
  "schema_version": "1.0",
  "node_id": "nixos-node-01",
  "original_log_id": "message-id-from-bus",
  "error_type": "service_failure",
  "severity": "critical",
  "root_cause": "nginx failed to start due to invalid configuration",
  "suggested_action": "rollback",
  "confidence": 0.92,
  "raw_ai_response": "...",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### 4.6 Testing

- Unit tests for prompt builder (various log types)
- Unit tests for AI client response parsing (mock OpenCode API)
- Unit tests for edge cases (empty response, invalid JSON, timeout)
- Integration test with real OpenCode API (manual, not in CI)

---

## Phase 5: Decision Agent

**Goal:** Consume analysis results, convert them into concrete NixOS remediation decisions, and publish them to the `decision` topic for local agents to execute.

**Depends on:** Phase 4 (Analysis Agent producing analysis results).

### 5.1 Service structure

```
decision_agent/
├── pyproject.toml
├── src/
│   └── decision_agent/
│       ├── __init__.py
│       ├── main.py            # Azure Function entry point (Service Bus trigger)
│       ├── function.json      # Service Bus trigger binding
│       └── decision_engine.py # Maps analysis → NixOS actions
└── tests/
    ├── __init__.py
    └── test_decision_engine.py
```

### 5.2 Azure Function: Service Bus trigger

- **Trigger:** New message on Service Bus Topic `analysis` (processed by Analysis Agent, but the Decision Agent consumes from a separate flow — either a dedicated subscription or the Analysis Agent publishes to `decision` topic directly)

**Architecture decision:** The Analysis Agent publishes its analysis result to Service Bus Topic `decision`. The Decision Agent consumes from the `decision-agent` subscription on the `decision` topic. This decouples analysis from decision-making.

### 5.3 Decision engine (`decision_engine.py`)

Mapping from analysis to NixOS action:

| `suggested_action` from AI | NixOS Command | Description |
|---|---|---|
| `rollback` | `nixos-rebuild switch --rollback` | Rollback to previous generation |
| `restart_service` | `systemctl restart <unit>` | Restart the failing service |
| `rebuild` | `nixos-rebuild switch` | Rebuild with current config |
| `no_action` | (no action) | Log only, no remediation |

The decision also includes:
- `target_node`: which NixOS node should execute this
- `command`: the exact shell command to run
- `timeout`: maximum execution time in seconds
- `idempotency_key`: for deduplication

### 5.4 Output format (`schemas/decision.py`)

```json
{
  "schema_version": "1.0",
  "decision_id": "uuid",
  "node_id": "nixos-node-01",
  "analysis_id": "original-analysis-message-id",
  "action": "rollback",
  "command": "nixos-rebuild switch --rollback",
  "severity": "critical",
  "confidence": 0.92,
  "idempotency_key": "hash(node_id+action+timestamp)",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

### 5.5 Cosmos DB storage

- Write each decision to Cosmos DB container `decisions` (for audit trail)
- Use `decision_id` as the document ID
- Include all fields from the decision schema

### 5.6 Testing

- Unit tests for decision engine (all mappings)
- Unit tests for idempotency key generation
- Unit tests for Cosmos DB write (mock client)
- Integration test: full flow from analysis to decision

---

## Phase 6: Local Agent (On-Node Remediation)

**Goal:** Run on each NixOS machine, pull decisions from Service Bus, and execute remediation commands directly (no dry-run mode).

**Depends on:** Phase 5 (Decision Agent publishing to `decision` topic).

### 6.1 Service structure

```
local_agent/
├── pyproject.toml
├── flake.nix                 # Nix build + systemd service
├── src/
│   └── local_agent/
│       ├── __init__.py
│       ├── main.py            # Long-running service main loop
│       ├── bus_client.py      # Service Bus subscription client
│       ├── executor.py        # Command execution
│       └── reporter.py        # Report results to Cosmos DB
└── tests/
    ├── __init__.py
    ├── test_executor.py
    └── test_reporter.py
```

### 6.2 Main loop (`main.py`)

1. On startup: read `node_id` from config or hostname
2. Connect to Service Bus subscription `local-agent` on topic `decision`
3. Filter messages by `node_id` (using Service Bus SQL filter: `node_id = '<this_node>'`)
4. For each received message:
   a. Validate the decision schema
   b. Execute the command via `executor.py`
   c. Report the result via `reporter.py`
   c. Complete the message (mark as processed)
5. On SIGINT/SIGTERM: drain pending messages, then exit

### 6.3 Command executor (`executor.py`)

- Execute commands via `subprocess.run()` with timeout
- Supported commands (whitelist):
  - `nixos-rebuild switch --rollback`
  - `systemctl restart <unit>`
  - `nixos-rebuild switch`
- Capture stdout, stderr, and return code
- No dry-run mode — **decisions are executed immediately**
- Timeout: configurable, default 120 seconds

### 6.4 Result reporter (`reporter.py`)

Write execution result to Cosmos DB container `execution-results`:

```json
{
  "execution_id": "uuid",
  "decision_id": "original-decision-id",
  "node_id": "nixos-node-01",
  "command": "nixos-rebuild switch --rollback",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:05Z",
  "success": true
}
```

### 6.5 Nix flake and systemd service

- Build as a Nix package using `flake.nix`
- Define a `systemd.service` unit for automatic startup
- Configuration via `settings.json` or environment variables:
  - `SERVICEBUS_CONNECTION` — from Key Vault (or local config for on-node)
  - `NODE_ID` — defaults to hostname
  - `COSMOSDB_ENDPOINT` — for result reporting

### 6.6 Testing

- Unit tests for command executor (mock `subprocess.run`)
- Unit tests for result reporter (mock Cosmos DB client)
- Unit tests for main loop (mock Service Bus client)
- Integration test on a NixOS VM (manual)

---

## Phase 7: Frontend (Streamlit)

**Goal:** Minimal monitoring dashboard showing system state, incidents, decisions, and execution results.

**Depends on:** Cosmos DB containing data from Phases 5-6.

### 7.1 Service structure

```
frontend/
├── pyproject.toml
├── src/
│   └── frontend/
│       ├── __init__.py
│       ├── app.py            # Streamlit main
│       ├── cosmos_client.py  # Cosmos DB read client
│       └── pages/
│           ├── nodes.py      # Node status page
│           ├── incidents.py  # Incidents page
│           ├── decisions.py  # Decisions page
│           └── results.py    # Execution results page
└── tests/
```

### 7.2 Dashboard pages

**Nodes page:**
- List all registered NixOS nodes
- Status (healthy / degraded / unknown)
- Last log upload timestamp

**Incidents page:**
- Recent log incidents
- Severity filter (critical, warning, info)
- Node filter

**Decisions page:**
- Recent remediation decisions
- Action type (rollback, restart, rebuild, no_action)
- Confidence score
- Decision status (pending, executed, failed)

**Results page:**
- Execution results
- Success/failure rate
- Command output logs

### 7.3 Deployment

- Run as a containerized Streamlit app
- Deploy to Azure App Service or Container Apps
- Basic authentication (Streamlit native or Azure AD)

### 7.4 Testing

- Unit tests for Cosmos DB queries (mock client)
- Visual testing of Streamlit pages (manual)

---

## Phase 8: Error Handling & Resilience

**Goal:** Make the system production-ready with proper error handling, retries, and monitoring.

**Cross-cutting — applied incrementally as each service is built.**

### 8.1 Retry policies

- All Service Bus consumers: exponential backoff (1s, 2s, 4s, 8s) with max 5 retries
- All HTTP calls (Token Service, OpenCode API): exponential backoff with max 3 retries
- Blob upload: 3 retries with exponential backoff

### 8.2 Dead-letter queue (DLQ) processing

- Service Bus subscriptions already have `max_delivery_count = 5` and `dead_lettering_on_message_expiration = true` (configured in Terraform)
- Create a DLQ monitoring Function or manual dashboard
- Never silently drop messages

### 8.3 Idempotency

- All write operations use idempotency keys:
  - Log batches: `blob_path` as natural key
  - Analysis results: `message_id` from Service Bus
  - Decisions: `decision_id` (UUID)
  - Execution results: `execution_id` (UUID)
- Cosmos DB upsert pattern: use `id` field as the document ID

### 8.4 OpenCode API failure handling

- On transient failure (5xx, timeout): retry up to 3 times with backoff
- On persistent failure: push message to DLQ
- No fallback provider — OpenCode Go API only, per project decision
- Alert via Application Insights when DLQ grows

### 8.5 Application Insights dashboards and alerts

- Dashboard: message flow through each pipeline stage
- Dashboard: error rates per service
- Alert: DLQ message count exceeds threshold
- Alert: API latency exceeds threshold
- Alert: node heartbeat missing for > 5 minutes

---

## Phase 9: CI/CD Pipeline

**Goal:** Automated build, test, and deploy for all services.

### 9.1 GitHub Actions workflows

**`infra.yml` — Terraform:**
- On push to `main` (paths: `infrastructure/**`): `terraform plan`
- On merge to `main`: `terraform apply` (with manual approval)

**`services.yml` — Azure Functions:**
- On push to `main` (paths: `token_service/**`, `log_router/**`, `analysis_agent/**`, `decision_agent/**`):
  - `ruff check` + `ruff format --check`
  - `pytest`
  - `az functionapp deployment source zip-deploy`

**`log-service.yml` — Log Service:**
- On push to `main` (paths: `log_service/**`):
  - `nix build`
  - `nix flake check`

**`local-agent.yml` — Local Agent:**
- On push to `main` (paths: `local_agent/**`):
  - `nix build`
  - `nix flake check`

**`frontend.yml` — Frontend:**
- On push to `main` (paths: `frontend/**`):
  - `ruff check` + `pytest`
  - Container build + push

### 9.2 Nix-based reproducible builds

- Each on-node service (log_service, local_agent) gets a `flake.nix`
- Each Azure Function gets a `requirements.txt` for zip deploy
- All builds are reproducible via Nix

### 9.3 Deployment automation

- Azure Functions: zip deploy via GitHub Actions
- Log Service: Nix build artifact deployed to NixOS nodes
- Local Agent: Nix build artifact deployed as systemd service
- Frontend: container image pushed to Azure Container Registry

---

## Phase 10: Documentation & Architecture Diagram

**Goal:** Complete project documentation as required by the course.

### 10.1 Architecture diagram

- Create draw.io diagram showing full data flow:
  ```
  NixOS Node → Token Service → SAS URL
  NixOS Node → Blob Storage (with SAS)
  Blob Storage → Log Router (blob trigger)
  Log Router → Service Bus Topic: analysis
  Analysis Agent → OpenCode Go API
  Analysis Agent → Service Bus Topic: decision (analysis result)
  Decision Agent → Service Bus Topic: decision (final decision)
  Local Agent → NixOS (execute command)
  Local Agent → Cosmos DB (report result)
  Cosmos DB → Frontend (Streamlit dashboard)
  ```
- Include all Azure services, data flows, and security boundaries

### 10.2 Update `idea.md`

- Mark all completed items in "To implement" and "TODO" sections

### 10.3 API documentation

- Document each Azure Function HTTP endpoint
- Document Service Bus message schemas

### 10.4 Deployment guide

- Step-by-step guide for deploying the full system
- Include Terraform apply, Function deployment, NixOS agent installation

---

## Dependency Chain (Execution Order)

```
Phase 0 (project setup)
    ↓
Phase 1 (Token Service)
    ↓
Phase 2 (Log Service — real uploads)
    ↓
Phase 3 (Log Router — blob trigger)
    ↓
Phase 4 (Analysis Agent — AI processing)
    ↓
Phase 5 (Decision Agent — NixOS action mapping)
    ↓
Phase 6 (Local Agent — on-node execution)
    ↓
Phase 7 (Frontend — Streamlit dashboard)
    ↓
Phase 8 (Error handling — cross-cutting, iterative)
    ↓
Phase 9 (CI/CD — can start incrementally alongside Phase 1)
    ↓
Phase 10 (Documentation — after everything works)
```

**Parallelization opportunities:**
- Phase 9 (CI/CD) can start incrementally — set up linting CI during Phase 0
- Phase 8 (error handling) is cross-cutting — apply iteratively as each service is built
- Phase 10 (docs) can have the architecture diagram started during Phase 0

---

## Architecture Decisions Summary

| Decision | Choice | Rationale |
|---|---|---|
| Project structure | Flat sibling directories at root level | Simplicity, clear boundaries |
| Token Service SAS scoping | Path-scoped to `logs/{node_id}/{uuid}` | Security: node can only write to its own path |
| SAS token permissions | Write-only, 5-minute expiry | Least privilege |
| Local Agent mode | No dry-run — executes immediately | Per project decision |
| AI provider | OpenCode Go API only | Per project decision; custom providers may be added later |
| AI fallback | None — push to DLQ on API failure | Keep it simple; no alternative provider yet |
| Shared schemas | Pydantic models in `schemas/` directory | Type safety, versioning, reusability |
| Python version | 3.11 for Azure Functions, 3.14 for on-node services | Azure Functions runtime + Nix OS |
| Package management | uv + pyproject.toml | Consistent with log_service convention |
| Linting/formatting | ruff | Fast, all-in-one Python linter + formatter |