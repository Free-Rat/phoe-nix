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
├── local_agent/             # On-node agentic participant (observe + execute + report) — NEW
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
- `schemas/observation.py` — Local agent observation format (Local Agent → Analysis topic → Analysis Agent)
- `schemas/analysis_result.py` — Analysis output format (Analysis Agent → Decision Agent)
- `schemas/decision.py` — Decision format (Decision Agent → Local Agent)
- `schemas/execution_result.py` — Execution result format (Local Agent → Cosmos DB)
- `schemas/node_state.py` — Local node state snapshot (maintained by Local Agent, included in observations and reports)
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

**Goal:** Consume messages from the `analysis` topic — both normalized log entries from the Log Router and observations from Local Agents — analyze them using the OpenCode Go API, and produce structured analysis results.

**Depends on:** Phase 3 (Log Router publishing to `analysis` topic).

**Note:** The Analysis Agent receives two types of messages on the same `analysis` topic:
1. **Normalized log entries** from the Log Router (triggered by blob uploads)
2. **Local agent observations** from Local Agents running on NixOS nodes (proactive state reports)

Both use the same topic but are distinguished by a `source` field (`log_router` or `local_agent`).

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
│       ├── prompt_builder.py # Prompt construction
│       └── message_handler.py # Route messages by source type
└── tests/
    ├── __init__.py
    ├── test_ai_client.py
    ├── test_prompt_builder.py
    └── test_message_handler.py
```

### 4.2 Azure Function: Service Bus trigger

- **Trigger:** New message on Service Bus Topic `analysis`, subscription `analysis-agent`
- **Processing:**
  1. Deserialize the message
  2. Determine source type from `source` field (`log_router` or `local_agent`)
  3. Build a context-appropriate prompt for the AI model:
     - For `log_router` messages: analyze the log entry for errors
     - For `local_agent` observations: analyze node state patterns and anomalies
  4. Call OpenCode Go API
  5. Parse the AI response into a structured analysis result
  6. Publish the analysis result to Service Bus Topic `decision`

### 4.3 AI prompt strategy (`prompt_builder.py`)

The prompt builder handles two distinct input types:

**For normalized log entries (source: `log_router`):**
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

**For local agent observations (source: `local_agent`):**
```
You are an expert NixOS system administrator. The following is a proactive 
observation from a NixOS node, including its current system state and any 
patterns the local agent detected. Analyze this observation for:
1. Whether the node's state indicates a problem (degraded services, repeated restarts, disk pressure)
2. Whether the node's observation corroborates or contradicts recent log analysis
3. What remediation action, if any, should be taken

Consider the node's full context: current generation, failed services, recent 
restart counts, and disk usage.

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
  "original_message_id": "message-id-from-bus",
  "source_type": "log_router | local_agent",
  "error_type": "service_failure",
  "severity": "critical",
  "root_cause": "nginx failed to start due to invalid configuration",
  "suggested_action": "rollback",
  "confidence": 0.92,
  "context": {
    "corroborating_observations": [],
    "contradicting_observations": [],
    "node_state_at_analysis_time": {}
  },
  "raw_ai_response": "...",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

The `context` field allows the Analysis Agent to include references to local agent observations that informed or contradicted the analysis. This creates the **shared knowledge** aspect — observations from nodes enrich the analysis beyond what logs alone provide.

### 4.6 Testing

- Unit tests for prompt builder (various log types AND observation types)
- Unit tests for message handler routing (log_router vs local_agent messages)
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

## Phase 6: Local Agent (On-Node Agentic Participant)

**Goal:** Run on each NixOS machine as a genuine agent in the distributed system — not just a command executor. The local agent operates in three modes: **Observe** (proactively monitor and publish state), **Execute** (pull and apply decisions from the cloud), and **Report** (report rich execution context back to the pipeline).

**Why an agent, not an executor:** A simple pull-and-execute program is a unidirectional pipeline endpoint — it receives commands and returns exit codes. The local agent is a **participant in the shared knowledge pipeline**: it proactively contributes observations that enrich the analysis agent's understanding, and it reports full system context after execution so the pipeline gets smarter over time. This makes Phoe-nix a genuinely **agentic distributed system**, not just a pipeline.

**Depends on:** Phase 5 (Decision Agent publishing to `decision` topic). Also publishes observations to the `analysis` topic (bidirectional communication).

### 6.1 Three modes of operation

#### Mode 1: Observe (proactive)

The local agent continuously monitors its node and publishes observations to the Service Bus `analysis` topic — the same topic the Log Router writes to. This means observations flow into the Analysis Agent alongside normalized logs, enriching the AI's understanding with local context.

Examples of observations:
- "nginx has restarted 5 times in the last hour"
- "disk usage on / is at 92%"
- "current NixOS generation is 47, previous was 46"
- "3 services are in failed state: nginx, postgresql, docker"

The Analysis Agent treats these observations the same way it treats normalized logs — as input to the AI. The AI now has both **remote evidence** (logs) and **local context** (observations), producing better decisions.

#### Mode 2: Execute (reactive)

Pull decisions from the `decision` topic (subscription `local-agent`) and execute them immediately. No dry-run mode — if the pipeline says rollback, the node rolls back.

#### Mode 3: Report (feedback loop)

After executing a decision, the local agent reports not just success/failure, but the **full system context**: current NixOS generation, which services are running, disk usage, etc. This closes the feedback loop — the pipeline can verify whether the remediation actually helped.

### 6.2 Service structure

```
local_agent/
├── pyproject.toml
├── flake.nix                 # Nix build + systemd service
├── src/
│   └── local_agent/
│       ├── __init__.py
│       ├── main.py            # Coordinator: runs all three modes concurrently
│       ├── monitor.py         # Mode 1: Observe — proactive state monitoring
│       ├── executor.py        # Mode 2: Execute — pull and apply decisions
│       ├── reporter.py        # Mode 3: Report — rich execution results to Cosmos DB
│       ├── bus_client.py      # Service Bus publish + subscribe
│       └── state.py           # Local node state tracker
└── tests/
    ├── __init__.py
    ├── test_monitor.py
    ├── test_executor.py
    ├── test_reporter.py
    └── test_state.py
```

### 6.3 Main coordinator (`main.py`)

The main loop runs all three modes concurrently using `asyncio`:

1. **On startup:**
   - Read `node_id` from config or hostname
   - Initialize local state tracker (`state.py`)
   - Connect to Service Bus (two channels: publish to `analysis`, subscribe to `decision`)
   - Connect to Cosmos DB (for reporting execution results)
2. **Run three concurrent tasks:**
   - `monitor.py` — periodic observation publishing (every 60 seconds, or on state change)
   - `executor.py` — continuous decision consumption from `decision` topic subscription
   - `reporter.py` — on-demand execution result publishing (triggered after execution)
3. **On SIGINT/SIGTERM:** drain pending messages, publish final state, then exit

### 6.4 Monitor: Observe mode (`monitor.py`)

The monitor proactively collects local node state and publishes observations to the `analysis` topic. This is what makes the local agent a *participant* in the knowledge pipeline, not just a recipient.

**What it monitors:**
- Current NixOS generation and boot generation
- Failed systemd units (`systemctl --failed`)
- Service restart counts (units that restarted more than N times in the last hour)
- Disk usage on `/` and `/nix`
- Memory and CPU usage
- Uptime since last reboot
- Whether any previous remediation was applied recently (cooldown to avoid oscillation)

**Observation publishing logic:**
- Publish a full node observation every 60 seconds (configurable)
- Immediately publish if a significant state change is detected (e.g., a new service failure)
- Do not publish if nothing changed since the last observation (dedup by state hash)
- Observations go to Service Bus Topic `analysis` with `source = "local_agent"`
- The Analysis Agent consumes these observations alongside logs from the Log Router

**Observation message format (`schemas/observation.py`):**

```json
{
  "schema_version": "1.0",
  "source": "local_agent",
  "node_id": "nixos-node-01",
  "observation_type": "periodic_state | state_change",
  "timestamp": "2026-01-01T00:00:00Z",
  "node_state": {
    "current_generation": 47,
    "previous_generation": 46,
    "boot_generation": 47,
    "failed_units": ["nginx.service", "postgresql.service"],
    "restart_counts": {
      "nginx.service": 5,
      "docker.service": 2
    },
    "disk_usage": {
      "/": "72%",
      "/nix": "85%"
    },
    "memory_usage_percent": 65,
    "cpu_usage_percent": 12,
    "uptime_seconds": 86400,
    "last_remediation_timestamp": "2026-01-01T00:00:00Z"
  },
  "message": "nginx has restarted 5 times in the last hour; postgresql is in failed state",
  "severity_hint": "warning"
}
```

**Why this matters:** The Analysis Agent can now reason about patterns it couldn't see from logs alone. For example:
- Logs say "nginx failed" → Analysis says "rollback"
- But observation says "current generation 47, nginx has been failing since generation 45" → Analysis says "rollback to generation 44" — a *better* decision than just "rollback to 46"
- Or observation says "disk at 95%" → Analysis says "disk issue, not config issue, no rollback needed"

This is the **shared knowledge pipeline** in action — each node contributes local expertise, and the pipeline produces smarter decisions.

### 6.5 Executor: Execute mode (`executor.py`)

Pull decisions from the `decision` topic and execute them on the local NixOS machine.

- Subscribe to Service Bus subscription `local-agent` on topic `decision`
- Filter messages by `node_id` (using Service Bus SQL filter: `node_id = '<this_node>'`)
- For each decision:
  1. Validate the decision schema
  2. Check local state: is a remediation already in progress? Is the decision still relevant? (cross-reference with `state.py`)
  3. Execute the command via `subprocess.run()` with timeout
  4. Capture stdout, stderr, return code, and execution time
  5. **Immediately report** via `reporter.py`

**Supported commands (whitelist):**

| Decision action | NixOS Command |
|---|---|
| `rollback` | `nixos-rebuild switch --rollback` |
| `restart_service` | `systemctl restart <unit>` |
| `rebuild` | `nixos-rebuild switch` |
| `switch_generation` | `nixos-rebuild switch --profile /nix/var/nix/profiles/system/<gen>` |

**Safety guardrails:**
- Cooldown period (default: 5 minutes) — don't apply two remediations to the same node in quick succession
- Maximum 3 remediations per hour per node (prevent oscillation)
- If current generation is already the target, skip execution and report "already applied"
- No dry-run mode — **decisions are executed immediately** (per project decision)

### 6.6 Reporter: Report mode (`reporter.py`)

After executing a decision, report not just success/failure, but the **full system context**. This creates the feedback loop that makes the pipeline self-improving.

**Execution result format (`schemas/execution_result.py`):**

```json
{
  "schema_version": "1.0",
  "execution_id": "uuid",
  "decision_id": "original-decision-id",
  "node_id": "nixos-node-01",
  "action": "rollback",
  "command": "nixos-rebuild switch --rollback",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "success": true,
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:05Z",
  "node_state_after": {
    "current_generation": 46,
    "previous_generation": 47,
    "failed_units": [],
    "disk_usage": { "/": "71%", "/nix": "85%" },
    "uptime_seconds": 86405
  },
  "observation_summary": "Rollback successful. All 2 previously failing units (nginx, postgresql) are now active."
}
```

The `node_state_after` field is the key difference from a simple executor. The pipeline can now:
- Verify that the remediation actually fixed the problem (`failed_units` went from `["nginx", "postgresql"]` to `[]`)
- Update the node's state in Cosmos DB for the frontend to display
- Feed the result back into future analysis (the Analysis Agent can learn from successful and failed remediations)

**Where results are stored:**
- Cosmos DB container `execution-results` — for audit trail and frontend display
- Cosmos DB container `node-state` — update the node's current state (overwrites previous state for this node)

### 6.7 Local state tracker (`state.py`)

Maintains an in-memory representation of the node's current state, shared by all three modes:

```python
class NodeState:
    node_id: str
    current_generation: int
    previous_generation: int
    boot_generation: int
    failed_units: list[str]
    restart_counts: dict[str, int]  # unit_name -> restart count in last hour
    disk_usage: dict[str, str]
    ongoing_remediation: bool
    last_remediation_timestamp: str | None
    remediations_this_hour: int
```

- Refreshed from the system every 30 seconds
- Used by `monitor.py` to detect state changes
- Used by `executor.py` to check cooldown and dedup
- Used by `reporter.py` to include `node_state_after` in results

### 6.8 Service Bus: bidirectional communication

The local agent uses two Service Bus channels:

1. **Publish** observations to Topic `analysis` (subscription: `analysis-agent` reads these)
   - This is the same topic the Log Router writes to
   - Messages are distinguished by `source` field: `"log_router"` vs `"local_agent"`
   - The Analysis Agent handles both types

2. **Subscribe** to Topic `decision` (subscription: `local-agent`)
   - Filter by `node_id` using Service Bus SQL filter
   - Decisions are targeted to specific nodes

```
                    ┌─────────────────┐
                    │ Service Bus      │
                    │ Topic: analysis  │
Log Router ────────►│                  │
                    │                  ├──────► Analysis Agent
Local Agent ───────►│                  │
(observations)     └─────────────────┘

                    ┌─────────────────┐
                    │ Service Bus      │
Decision Agent ────►│ Topic: decision   │
                    │                  ├──────► Local Agent
                    └─────────────────┘
```

### 6.9 Nix flake and systemd service

- Build as a Nix package using `flake.nix`
- Define a `systemd.service` unit for automatic startup
- Configuration via `settings.json` or environment variables:
  - `SERVICEBUS_CONNECTION` — Service Bus connection string
  - `NODE_ID` — defaults to hostname
  - `COSMOSDB_ENDPOINT` — for result reporting
  - `OBSERVE_INTERVAL_SECONDS` — how often to publish observations (default: 60)
  - `COOLDOWN_SECONDS` — minimum time between remediations (default: 300)
  - `MAX_REMEDIATIONS_PER_HOUR` — safety limit (default: 3)

### 6.10 Testing

- Unit tests for monitor (mock system state reads, verify observation format)
- Unit tests for executor (mock `subprocess.run`, verify command whitelist, cooldown logic)
- Unit tests for reporter (mock Cosmos DB client, verify `node_state_after` is included)
- Unit tests for state tracker (verify state change detection, dedup)
- Unit tests for Service Bus client (verify publish to `analysis`, subscribe to `decision`)
- Integration test on a NixOS VM (manual)

---

## Phase 7: Frontend (Streamlit)

**Goal:** Monitoring dashboard showing system state, observations, incidents, decisions, execution results, and the shared knowledge pipeline in action.

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
│           ├── nodes.py      # Node status + state (from local agent observations)
│           ├── observations.py # Local agent observations (shared knowledge)
│           ├── incidents.py  # Log-sourced incidents
│           ├── decisions.py  # Decisions (from decision agent)
│           └── results.py    # Execution results (from local agent reports)
└── tests/
```

### 7.2 Dashboard pages

**Nodes page:**
- List all registered NixOS nodes
- Current state per node: NixOS generation, failed units, disk usage, uptime
- Status (healthy / degraded / unknown) — derived from local agent observations
- Last observation timestamp
- Last log upload timestamp

**Observations page (new — shows the shared knowledge pipeline):**
- Local agent observations over time
- Filter by node, severity, observation type
- Correlation view: show which observations led to which analysis results and decisions
- This page demonstrates the bidirectional communication that makes Phoe-nix agentic

**Incidents page:**
- Recent log incidents (from normalized logs)
- Severity filter (critical, warning, info)
- Node filter

**Decisions page:**
- Recent remediation decisions
- Action type (rollback, restart, rebuild, no_action)
- Confidence score
- Decision status (pending, executed, failed)
- Correlation: which observations and logs led to this decision

**Results page:**
- Execution results with full `node_state_after` context
- Success/failure rate
- Whether remediation actually fixed the problem (compare `failed_units` before vs after)
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

- Create draw.io diagram showing full data flow — **note the bidirectional arrows from the local agent**:

  ```
  ┌─────────────────────────────────────────────────────────────────┐
  │                        Azure Cloud                              │
  │                                                                 │
  │  ┌────────────┐     ┌────────────┐     ┌──────────────────┐   │
  │  │   Token     │     │    Blob    │     │   Cosmos DB      │   │
  │  │  Service    │────►│  Storage   │────►│ (state, decisions,│   │
  │  └────────────┘     │   (logs)   │     │  results, nodes) │   │
  │                     └────────────┘     └──────────────────┘   │
  │                            │                    ▲              │
  │                            ▼                    │              │
  │                     ┌────────────┐     ┌──────────────┐       │
  │                     │Log Router  │     │   Decision    │       │
  │                     │(normalize) │     │    Agent      │       │
  │                     └─────┬──────┘     └──────┬───────┘       │
  │                           │                   ▲               │
  │                           ▼                   │               │
  │                     ┌─────────────────────────┐               │
  │                     │  Service Bus: analysis   │               │
  │                     └───────────┬─────────────┘               │
  │                                 ▲                             │
  │                                 │                             │
  │                     ┌───────────┴─────────────┐               │
  │                     │    Analysis Agent       │               │
  │                     │  (OpenCode Go API)      │               │
  │                     └─────────────────────────┘               │
  │                                                                 │
  │                     ┌─────────────────────────┐               │
  │                     │  Service Bus: decision    │               │
  │                     └─────────────────────────┘               │
  └─────────────────────────────────────────────────────────────────┘
                           ▲           │
                  observations         decisions
                           │           ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                       NixOS Node                                │
  │                                                                 │
  │  ┌────────────┐     ┌────────────┐     ┌──────────────────┐   │
  │  │Log Service  │     │   Local    │◀────│   Local Agent    │   │
  │  │(collect &   │     │   Agent    │     │  (observe +      │   │
  │  │ upload logs)│     │(execute)   │     │   report)        │   │
  │  └────────────┘     └────────────┘     └──────────────────┘   │
  │                            │                    │              │
  │                            ▼                    ▼              │
  │                     NixOS system commands     Service Bus     │
  │                     (nixos-rebuild, etc.)     (observations)  │
  └─────────────────────────────────────────────────────────────────┘
  ```

  Key data flows (bidirectional):
  1. NixOS Node → Token Service → SAS URL
  2. NixOS Node → Blob Storage (log upload with SAS)
  3. Blob Storage → Log Router (blob trigger) → Service Bus: analysis
  4. **Local Agent → Service Bus: analysis** (proactive observations — this is what makes it agentic)
  5. Analysis Agent → Service Bus: decision (analysis results)
  6. Decision Agent → Service Bus: decision (final decisions)
  7. Service Bus: decision → Local Agent (execute decisions)
  8. Local Agent → Cosmos DB (execution results + node state)
  9. Cosmos DB → Frontend (Streamlit dashboard)

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
| Local Agent design | Agentic participant (observe + execute + report) | Not a dumb executor — contributes observations to shared knowledge pipeline, creates bidirectional communication |
| Local Agent modes | Observe, Execute, Report | Three concurrent modes: proactive monitoring, decision execution, rich context reporting |
| Local Agent observation flow | Publishes to `analysis` topic | Same topic as Log Router; messages distinguished by `source` field |
| Local Agent execution | No dry-run — executes immediately | Per project decision |
| Local Agent safety | Cooldown period (5min), hourly remediation limit (3), generation dedup | Prevent oscillation and runaway remediation |
| AI provider | OpenCode Go API only | Per project decision; custom providers may be added later |
| AI fallback | None — push to DLQ on API failure | Keep it simple; no alternative provider yet |
| Shared schemas | Pydantic models in `schemas/` directory | Type safety, versioning, reusability |
| Python version | 3.11 for Azure Functions, 3.14 for on-node services | Azure Functions runtime + Nix OS |
| Package management | uv + pyproject.toml | Consistent with log_service convention |
| Linting/formatting | ruff | Fast, all-in-one Python linter + formatter |