# Remaining Work

## Short Answer

Yes. There is still work left outside the frontend, and the biggest remaining work is now the proof-of-concept `local_agent` repair loop rather than only production hardening.

## Remaining Areas Outside Frontend

### 1. Local Agent Proof-Of-Concept Runtime

The `local_agent` package now has tested core logic, but still needs:

- a long-running coordinator loop
- real Service Bus subscription/publish wiring
- real Cosmos writes from the package runtime
- real node-state collection from the host system
- packaging as a deployable systemd-managed service on NixOS
- local analysis-context consumption
- arbitrary config editing flow for proof-of-concept repairs
- rebuild/test/report loop with before/after config capture

### 2. Live Azure Integration Tests

The repo now has strong local simulation coverage, but not real cloud verification. Remaining work:

- deploy-time smoke tests for each function
- blob-trigger validation in Azure
- Service Bus flow verification in Azure
- Cosmos persistence verification in Azure

### 3. Pipeline Visibility And Operations

Still incomplete:

- DLQ monitoring
- explicit publish retry policy wrappers for Service Bus/Cosmos
- Application Insights dashboards and alert definitions
- duplicate-delivery/idempotency hardening for the local agent
- lightweight per-stage status records for the TUI

### 4. CI/CD Expansion

The repo has one services workflow, but still needs:

- local-agent specific workflow
- infra plan/apply workflow
- deploy workflows with environment protections
- manual live-integration workflow

### 5. Documentation Completion

Still missing:

- architecture diagram artifact
- deployment guide for full stack including node install
- API/message contract reference per deployed service

## Implementation Plan

### Phase A: Finish Local Agent Runtime And Repair Loop

1. Add real coordinator loop in `local_agent/main.py`.
2. Add real bus receive/publish helpers and Cosmos upsert helpers.
3. Add system inspection adapters for failed units, generations, and disk usage.
4. Add local LLM-driven repair flow using decision plus analysis context.
5. Add before/after config capture and rebuild reporting.
6. Add Nix/service packaging documentation and install flow.

### Phase B: Azure Verification

1. Add scripts for smoke-testing deployed function endpoints.
2. Add a manual integration test suite gated by environment variables.
3. Add result assertions for Blob, Service Bus, and Cosmos.

### Phase C: Operational Hardening

1. Add retry wrappers for publish/write paths.
2. Add idempotency handling for repeated decision execution.
3. Add DLQ monitoring documentation and dashboards.

### Phase D: Docs, TUI, and Delivery

1. Write deployment guide.
2. Add architecture diagram source/artifact.
3. Add minimal local TUI for pipeline visibility.
4. Expand CI workflows.

## What I Executed In This Pass

- implemented the `local_agent` core package
- wired simulator coverage through the decision-execution stage
- documented the complete testing plan

The next highest-value executable work is Phase A: finishing the real long-running `local_agent` runtime and turning it into the proof-of-concept config-repair agent.
