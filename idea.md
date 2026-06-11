# Project Idea

## Scope Note

This document separates two things:

- **Observed repository reality**: what is already implemented in this repo today.
- **Planned direction**: the proof-of-concept we still want to build.

## 1) Observed repository reality

`phoe-nix` is already a multi-service NixOS self-healing pipeline with a local simulator.

### Implemented today
- `token_service` issues short-lived, path-scoped SAS upload URLs.
- `log_service` tails systemd journal entries, batches them, retries uploads, and spools failed batches locally.
- `log_router` normalizes uploaded log batches and publishes them to Service Bus.
- `analysis_agent` consumes logs and observations, calls OpenCode, and publishes analysis results.
- `decision_agent` turns analysis into remediation intent, stores audit records in Cosmos DB, and publishes decisions.
- `schemas` provides the shared Pydantic message contracts.
- `simulator` exercises the implemented service cores without Azure.
- `local_agent` already contains the core observation, execution, and reporting logic, the daemon runtime, and the Git-backed repair loop.

### Current pipeline shape
1. `log_service` collects logs and uploads them using a SAS URL from `token_service`.
2. `log_router` normalizes the uploaded batch.
3. `analysis_agent` analyzes the normalized data.
4. `decision_agent` stores a decision and publishes remediation intent.
5. `local_agent` consumes decisions and can execute the repair loop.

## 2) Planned direction

The next step is a more agentic proof of concept: `local_agent` becomes the primary config-repair engine for disposable NixOS VMs.

### Core idea
1. A node observes a problem.
2. Logs or observations enter the cloud pipeline.
3. Cloud services produce diagnosis and remediation context.
4. `local_agent` receives that context.
5. The local agent uses a local LLM on the VM host to interpret the issue against the current configuration.
6. The local agent updates the shared config repository if needed.
7. The local agent runs `nixos-rebuild test`.
8. If the test fails, the failure output is used for another repair attempt.
9. If the test succeeds, the local agent runs `nixos-rebuild switch`.
10. The local agent reports what changed and what happened.

### Planned topic flow
- `analysis-input`: normalized logs and local observations
- `analysis-results`: analysis output from `analysis_agent`
- `final-decisions`: remediation decisions for `local_agent`

### Planned local-agent responsibilities
- keep a persistent clone of `https://github.com/Free-Rat/phoe-nix-config`
- pull before acting on a new decision
- refresh regularly so nodes converge on shared fixes
- edit `configuration.nix` when repair is needed
- run `nixos-rebuild test` before `switch`
- keep repair traces, command output, and before/after config state
- handle push retries and merge conflicts
- report final node state

## 3) Constraints and supporting technology

This project is intended to satisfy the course requirements for:
- a microservices architecture
- asynchronous communication
- SaaS/cloud services
- serverless or Kubernetes-style deployment
- Infrastructure as Code
- CI/CD
- an architecture diagram
- a minimal frontend for visibility

The current repository direction uses:
- Azure Blob Storage
- Azure Functions
- Azure Service Bus topics
- Azure Cosmos DB
- Azure Key Vault
- the OpenCode Go API

## 4) Explicit non-goals for now

This proof of concept is not trying to be production-ready.

Not prioritized yet:
- strict safety or approval workflows
- guaranteed deterministic repairs
- multi-node rollback orchestration
- heavy abstraction around local config changes
- a polished frontend beyond basic pipeline visibility

## 5) Why this direction

The value of the proof of concept is the closed loop:

- nodes produce evidence
- cloud services analyze and route it
- the local agent repairs NixOS configuration
- the system observes the result and continues

That is the direction the remaining implementation should follow.
