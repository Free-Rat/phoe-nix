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
    - Uses AI model (via OpenCode API or Azure Cognitive Services)

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
→ Service Bus Topic
→ Analysis Agent
→ Decision Agent
→ Service Bus Queue
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
- We will use OpenCode Go API as AI model provider (if possible)
- We will use Azure cloud services (Azure Functions, blob storage, Queue Storage/Service Bus, Cosmos DB)
- We will use terraform as IaC
- We will use Python as a main language

## Component to Azure Service Mapping

| Component     | Azure Service               |
| ------------- | --------------------------- |
| Log Storage   | Blob Storage                |
| Event Trigger | Azure Functions             |
| Messaging     | Service Bus (Topic + Queue) |
| AI Processing | Azure OpenAI / OpenCode API |
| Database      | Cosmos DB                   |
| Compute       | Azure Functions / AKS       |

## To implement

- [ ] Log Service (on node)
- [ ] Upload Authorization Service (Token Service)
- [ ] Log Router / Normalizer
- [ ] Analysis Agent (AI-powered)
- [ ] Decision Agent (?)
- [ ] Local Agent (Local Agent on Node)
