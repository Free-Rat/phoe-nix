# Project Description

## main idea

Create a distributed system that enables machines (nodes) to self-balance and self-heal from OS configuration errors using declarative configuration (NixOS) and AI-driven decision-making.

## context

Project for Projektowanie Systemów Rozproszonych (Designing Distributed Systems).

### requirements

- The project must be implemented based on a microservices architecture (minimum 3 nodes).
- Asynchronous communication must be used in at least one place.
- Use of SaaS services within any cloud (e.g., Azure Cognitive Services).
- Architecture should be serverless or based on Kubernetes (or similar technology).
- A minimal frontend (e.g. Streamlit).
- Infrastructure as Code (e.g. Terraform, ARM).
- CI/CD pipeline (e.g. GitHub Actions, Azure DevOps).
- An architecture diagram (e.g. draw.io).

## idea for implementation

We will use power of NixOS and AI Agents to controll os.
The pipeline will:

1. gather logs (log-ingestion-service)
2. store them
3. normalize them (log-router)
4. put them in log_queue
5. analize processing them (analysis-agent)
6. prouce decision (decision-agent)
7. put them to decision_queue
8. change configuration based on them (local-agents)
9. store changes

### Microservices Breakdown

0. Log Service (on node)

    - Gets a token from Upload Authorization Service
    - Collects logs from NixOS nodes
    - Pushes raw logs to Azure Blob Storage

1. Upload Authorization Service (Token Service)

    - Responsibilities:
        - Authenticates node
        - Generates SAS token
        - Limits:
            - write-only access
            - specific blob/container
            - short expiration (e.g., 5 minutes)

2. Log Router / Normalizer

    - Triggered by new blobs (Azure Function)
    - Parses and normalizes logs into structured format
    - Publishes messages to Service Bus Topic

3. Analysis Agent (AI-powered)

    - Consumes messages asynchronously
    - Detects anomalies / misconfigurations
    - Uses OpenCode Go API as the AI model provider
    - The API key is stored in Azure Key Vault and accessed via Managed Identity

4. Decision Agent

    - Converts analysis into actionable decisions
    - Example:
        - “Rollback configuration”
        - “Rebuild system with previous generation”
        - “Restart service”

5. Local Agent (on Node)

    - Runs on each NixOS machine as a genuine agent — not just a command executor
    - Three modes of operation:
        - **Observe**: proactively monitors local state (services, disk, NixOS generation) and publishes observations to the `analysis` topic alongside logs
        - **Execute**: pulls decisions from the `decision` topic and applies remediation commands immediately (no dry-run)
        - **Report**: after execution, reports not just success/failure but full node context (current generation, failed services, disk usage) — closing the feedback loop
    - Creates a **shared knowledge pipeline**: nodes contribute local expertise that enriches the AI's analysis, producing better decisions than logs alone

6. State Storage
    - Stores:
        - decisions
        - applied changes
        - system states
    - Use Cosmos DB

### pipeline in servicies

Log Upload
→ Azure Blob Storage
→ Log Router (Azure Function)
→ Service Bus Topic `analysis`
→ Analysis Agent (calls OpenCode Go API)
→ Decision Agent
→ Service Bus Topic `decision`
→ Local Agent (on node)
→ Cosmos DB

### architecture

[Node Agent]
   ↓ (request SAS)
[Token Service]
   ↓
[SAS URL]

[Node Agent]
   ↓ (direct upload)
[Azure Blob Storage]
   ↓ (event trigger)
[Azure Function - Router]
   ↓
[Service Bus Topic]
   ↓
[Analysis Agent]
   ↓
[Decision Agent]
   ↓
[Queue]
   ↓
[Local Agent]
   ↓
[Cosmos DB]

## Architecture Decision Record

- We will use NixOS
- We will use OpenCode Go API as the AI model provider (not "if possible" – confirmed)
- The OpenCode Go API key will be stored in Azure Key Vault; Azure Functions access it via Managed Identity
- We will use Azure cloud services (Azure Functions, Blob Storage, Service Bus, Cosmos DB)
- We will use a single Service Bus namespace with multiple topics (`analysis`, `decision`) – no Queue Storage is needed
- Direct connection to Service Bus uses the connection string (Shared Access Policy); prefer Managed Identity where possible
- We will use Terraform as IaC
- We will use Python as the main language
- CI/CD will be delivered by Nix; implementation will be done in a later phase
- Project structure: flat sibling directories at root level (no nested monorepo)
- Token Service generates path-scoped SAS tokens: `logs/{node_id}/{uuid}` — write-only, 5-minute expiry, no list/read/delete permissions
- Local Agent design is agentic (observe + execute + report), not a simple pull-and-execute program — it participates bidirectionally in the knowledge pipeline
- OpenCode Go API is the only AI provider (no fallback); custom providers may be added later
- Shared message schemas use Pydantic models in a top-level `schemas/` directory
- Linting/formatting: ruff (all-in-one Python linter + formatter)

## Component to Azure Service Mapping

| Component     | Azure Service               |
| ------------- | --------------------------- |
| Log Storage   | Blob Storage                |
| Event Trigger | Azure Functions             |
| Messaging     | Service Bus (Topic only)    |
| AI Processing | OpenCode Go API             |
| Database      | Cosmos DB                   |
| Compute       | Azure Functions / AKS       |
| Secrets       | Azure Key Vault             |

## To implement

- [ ] **Phase 0:** Project setup — ruff config, shared schemas directory, fix typos
- [ ] **Phase 1:** Token Service — path-scoped SAS token generation (write-only, 5-min expiry, `logs/{node_id}/{uuid}`)
- [ ] **Phase 2:** Log Service — replace stubs with real Token Service call + Azure Blob upload with batching and retry
- [ ] **Phase 3:** Log Router / Normalizer — blob-triggered Azure Function, parse + normalize logs, publish to Service Bus
- [ ] **Phase 4:** Analysis Agent — Service Bus-triggered, call OpenCode Go API, produce structured analysis results
- [ ] **Phase 5:** Decision Agent — map analysis to NixOS actions, publish to `decision` topic, store in Cosmos DB
- [ ] **Phase 6:** Local Agent — agentic on-node participant (observe → publish to `analysis` topic, execute decisions, report rich context back to Cosmos DB and Service Bus)
- [ ] **Phase 7:** Frontend — Streamlit dashboard (nodes, incidents, decisions, results)
- [ ] **Phase 8:** Error handling & resilience — retry policies, DLQ processing, idempotency, Application Insights dashboards
- [ ] **Phase 9:** CI/CD — GitHub Actions workflows for all services
- [ ] **Phase 10:** Documentation & architecture diagram

See [PLAN.md](./PLAN.md) for detailed implementation specifics per phase.

## TODO

- [ ] Error handling strategy (retry policies, DLQ processing, idempotency)
- [ ] Nix-based CI/CD pipeline (build, test, deploy)
- [ ] Define alerting rules and dashboards in Application Insights
- [x] ~~Evaluate if Azure OpenAI fallback is needed~~ — Not needed; OpenCode Go API only, per decision
