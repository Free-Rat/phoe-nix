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

    - Runs on each NixOS machine
    - Pulls decisions from queue
    - Applies configuration changes via:
        - nixos-rebuild switch
        - rollback generations
    - Reports execution result

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

- [ ] Log Service (on node)
- [ ] Upload Authorization Service (Token Service)
- [ ] Log Router / Normalizer
- [ ] Analysis Agent (AI-powered – OpenCode Go API)
- [ ] Decision Agent (?)
- [ ] Local Agent (Local Agent on Node)

## TODO

- [ ] Error handling strategy (retry policies, DLQ processing, idempotency, API fallback)
- [ ] Nix-based CI/CD pipeline (build, test, deploy)
- [ ] Evaluate if Azure OpenAI fallback is needed
- [ ] Define alerting rules and dashboards in Application Insights
