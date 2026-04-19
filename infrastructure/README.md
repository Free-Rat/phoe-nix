# Infrastructure Overview

This document describes the Azure resources

## 1. High-level architecture

The solution is event-driven and cloud-native. Nodes request temporary upload permission, upload logs directly to Azure Blob Storage, and the rest of the pipeline processes the logs asynchronously.

```text
NixOS Node Log Service -> Token Service -> SAS token
NixOS Node Log Service -> Azure Blob Storage -> Azure Function (Router)
Azure Function -> Service Bus Topic:analysis -> Analysis Agent -> Decision Agent -> Service Bus Topic:decision -> NixOS Node Local Agent
NixOS Node Local Agent -> Cosmos DB
```

## 2. Azure resources

### 2.1 Resource Group

**Purpose:** Logical container for all resources used by the project.

**Contains:**

* Storage Account
* Azure Functions
* Service Bus Namespace
* Cosmos DB Account
* Key Vault
* Application Insights
* App Service / Container App for frontend and/or agent services

**Notes:**

* Separate resource groups can be used for `dev` and `prod` environments.
* Keep naming consistent, for example: `rg-project-healer-dev`.

---

### 2.2 Storage Account (Blob Storage)

**Purpose:** Stores raw logs uploaded by NixOS nodes.

**Usage:**

* Nodes upload log files directly using a short-lived SAS token.
* Blob Storage triggers downstream processing through an Azure Function.

**Recommended configuration:**

* Private access if possible
* Container such as `logs`
* Lifecycle rules for log retention and cleanup
* Versioning disabled unless needed

**Data stored:**

* Raw log files
* Optional processed artifacts

---

### 2.3 Token Service

**Purpose:** Issues temporary upload permissions for nodes.

**Implementation options:**

* Azure Functions
* Azure Container Apps
* App Service

**Responsibilities:**

* Authenticate the node
* Generate SAS tokens for a specific blob or container
* Limit token lifetime
* Limit permissions to write-only access

**Why it exists:**

* Prevents direct public access to Blob Storage
* Removes the need for a central log-ingestion service

---

### 2.4 Azure Functions

**Purpose:** Serverless compute for event-driven processing.

**Suggested functions:**

* **Token Function**: generates SAS tokens
* **Router Function**: triggered when a new blob is created, normalizes log metadata and sends messages to Service Bus

**Triggers:**

* HTTP trigger for token generation
* Blob trigger or Event Grid trigger for new log files

**Benefits:**

* Low operational overhead
* Easy scaling
* Good fit for asynchronous pipelines

---

### 2.5 Azure Service Bus

**Purpose:** Asynchronous communication backbone.

**Recommended topology:**

* A single Service Bus namespace with multiple topics:
  * **Topic `analysis`** – log processing events
  * **Topic `decision`** – actionable decisions
* No Queue Storage is needed; topics with subscriptions cover all communication patterns.

**Usage:**

* Router publishes normalized log events to the `analysis` topic
* Analysis Agent subscribes to the `analysis` topic and publishes results to the `decision` topic
* Decision Agent subscribes to the `decision` topic
* Local Agent pulls decisions from the `decision` topic subscription

**Direct connection:**

* Get the connection string via a Shared Access Policy.
* Services authenticate using Managed Identity where possible; fall back to connection string from Key Vault only when necessary.

**Why Service Bus:**

* Reliable delivery
* Retry support
* Dead-letter queues
* Decouples pipeline stages

---

### 2.6 Analysis Agent

**Purpose:** Consumes log events and detects anomalies or configuration issues.

**Implementation options:**

* Azure Functions
* Container App
* AKS workload

**Responsibilities:**

* Parse normalized logs
* Detect suspicious or invalid configuration changes
* Call AI model provider if needed
* Produce structured analysis output

---

### 2.7 Decision Agent

**Purpose:** Converts analysis results into a remediation decision.

**Responsibilities:**

* Decide whether the node should rollback, restart a service, or reapply configuration
* Create a safe, structured action request
* Store the decision for auditing

**Output:**

* Action message sent to Service Bus Queue

---

### 2.8 Action Agent

**Purpose:** Runs on each NixOS node and applies the selected fix.

**Responsibilities:**

* Read remediation commands from the queue
* Execute safe actions on the local machine
* Apply NixOS rollback or rebuild
* Report success or failure back to Cosmos DB

**Important note:**

* The agent should only execute predefined actions, not arbitrary commands.

---

### 2.9 Cosmos DB

**Purpose:** Stores system state, decisions, execution results, and audit history.

**Data stored:**

* Node state
* Detected incidents
* Decision history
* Action execution results
* Timestamps and correlation IDs

**Why Cosmos DB:**

* Flexible schema
* Fast reads/writes
* Good fit for event and state records

---

### 2.10 Key Vault

**Purpose:** Secure storage for secrets and credentials.

**Stored items:**

* Service Bus connection strings (Shared Access Policy), if Managed Identity is not used for a given service
* Cosmos DB secrets, if needed
* OpenCode Go API key
* Storage account keys, if SAS generation requires them

**Access pattern:**

* Azure Functions and other services access secrets in Key Vault via Managed Identity.
* No secrets are embedded in code or configuration files.

**Recommended practice:**

* Prefer Managed Identity for all Azure-internal communication
* Store only external secrets (e.g., OpenCode Go API key) in Key Vault

---

### 2.11 Managed Identity

**Purpose:** Identity mechanism for Azure services to authenticate without embedded secrets.

**Used by:**

* Azure Functions
* Token Service
* Frontend backend
* Any service accessing Storage, Service Bus, or Cosmos DB

**Benefits:**

* Improves security
* Reduces secret management burden

---

### 2.12 Application Insights / Azure Monitor (later)

**Purpose:** Observability and diagnostics.

**Tracks:**

* Function execution logs
* Service Bus message flow
* Failures and retries
* Latency and throughput
* Agent health

**Why it matters:**

* Helps demonstrate the distributed nature of the system
* Useful for debugging and evaluation

**TODO:** Define alerting rules and dashboards.

---

### 2.13 Frontend hosting (later)

**Purpose:** Minimal UI for monitoring the system.

**Implementation options:**

* Azure App Service
* Azure Container Apps
* Streamlit hosted in a container

**UI features:**

* Node status
* Detected incidents
* Applied fixes
* Recent logs and decisions

---

### 2.14 Container Registry (optional) (later)

**Purpose:** Stores container images if any service is containerized.

**Used for:**

* Analysis Agent
* Decision Agent
* Action Agent
* Streamlit frontend

**Alternative:**

* Not required if everything runs as Azure Functions

---

### 2.15 Azure AI service – OpenCode Go API

**Purpose:** AI-assisted log analysis and remediation suggestions.

**Provider:**

* OpenCode Go API is the AI provider.
* The API key is stored in Azure Key Vault.
* Azure Functions access the key via Managed Identity – no hardcoded secrets.

**TODO:** Evaluate whether Azure OpenAI is needed as a fallback provider.

---

## 3. Data flow between resources

1. A NixOS node asks the Token Service for a temporary upload token.
2. The Token Service returns a short-lived SAS token.
3. The node uploads logs directly to Blob Storage.
4. Blob creation triggers an Azure Function.
5. The Router Function normalizes the event and sends it to Service Bus Topic `analysis`.
6. The Analysis Agent consumes the message, calls OpenCode Go API (key from Key Vault via Managed Identity), and detects issues.
7. The Decision Agent creates a remediation plan and publishes to Topic `decision`.
8. The Local Agent on the node pulls the decision and applies the fix.
9. Results are stored in Cosmos DB and shown in the frontend.

## 4. Security considerations

* Use short-lived SAS tokens only.
* Restrict SAS permissions to write-only.
* Prefer Managed Identity over access keys.
* Keep secrets in Key Vault; access via Managed Identity.
* Restrict Action Agent to a small set of safe, predefined remediation commands.
* Use separate environments for development and production.
* Direct connections to Service Bus use the connection string (Shared Access Policy); wherever possible, prefer Managed Identity instead.

## 5. CI/CD

* CI/CD will be delivered by Nix.
* Implementation will be done in a later phase.

**TODO:** Define the Nix-based CI/CD pipeline (build, test, deploy stages).

## 6. Error handling

**TODO:** Define error handling strategy for the pipeline:

* Retry policies for each agent (Analysis, Decision, Local).
* Dead-letter queue processing and alerting.
* Idempotency guarantees.
* Fallback behaviour when OpenCode Go API is unavailable.

## 7. Suggested naming convention

Example:

* `rg-project-healer-dev`
* `stprojecthealerdev`
* `sb-project-healer-dev`
* `cosmos-project-healer-dev`
* `kv-project-healer-dev`
* `func-project-healer-dev`

## 8. Summary

This infrastructure supports a fully event-driven distributed system where NixOS nodes can upload logs securely, the cloud pipeline can analyze them asynchronously, and local agents can apply self-healing actions automatically.


