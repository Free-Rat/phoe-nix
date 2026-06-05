# Phoe-nix Plan

## Goal

Finish the proof-of-concept self-healing loop on disposable VMs.

Cloud services continue to use the OpenCode Go API.
On-node repair uses `local_agent` plus Ollama running on the VM host.

## Current Architecture

1. `log_service` uploads journal batches to Blob Storage through `token_service`
2. `log_router` publishes normalized entries to `analysis-input`
3. `analysis_agent` uses OpenCode Go API and publishes analysis output to `analysis-results`
4. `decision_agent` stores decisions in Cosmos DB and publishes remediation intent to `final-decisions`
5. `local_agent` will consume those decisions, update the shared config repository, run `nixos-rebuild test`, retry repairs if needed, run `switch`, push successful changes, and report the full trace

## Fixed Decisions

- Proof of concept only; nodes are disposable VMs
- `local_agent` is a single long-running process with internal async workers
- Cloud authentication for `local_agent` uses connection strings
- Service Bus topics are `analysis-input`, `analysis-results`, and `final-decisions`
- Cloud-side AI stays on OpenCode Go API
- Node-side repair uses Ollama on the VM host
- VM-to-host Ollama access should use direct private HTTP, not a public endpoint
- Shared config repo is `https://github.com/Free-Rat/phoe-nix-config`
- Main editable file is `configuration.nix`
- Before each new decision, and every 5 minutes, `local_agent` refreshes the config repo
- Repair loop is `nixos-rebuild test` first, then `nixos-rebuild switch`
- Failed `test` output is fed back into the local repair loop
- Successful changes should be committed and pushed back to the shared config repo
- If push hits a merge conflict, `local_agent` should pull, attempt to resolve, rerun `test`, and push again
- Frontend should be a minimal local TUI showing the pipeline and state transitions

## What Is Already Done

- `token_service` implemented and tested
- `log_service` implemented and tested
- `log_router` implemented and tested
- `analysis_agent` implemented and tested
- `decision_agent` implemented and tested
- shared schemas implemented and updated for richer POC context
- simulator updated to cover config-repair style decisions

## Remaining Work

### Phase 1: `local_agent` Daemon Runtime

Goal: turn the current reusable `local_agent` core into the real on-node coordinator.

Implement:

- async main coordinator
- observe worker
- decision-consume worker
- report/persist worker
- graceful shutdown and drain behavior
- real host inspection for node state
- real Service Bus receive/publish wiring
- real Cosmos DB persistence wiring

Files likely affected:

- `local_agent/src/local_agent/main.py`
- `local_agent/src/local_agent/bus_client.py`
- new runtime/orchestration module(s)
- new host-inspection module(s)

### Phase 2: Host Ollama Integration

Goal: let `local_agent` use the host machine's Ollama service for repair planning.

Implement:

- local Ollama client module
- config for Ollama base URL and model name
- prompt builder for node repair
- fake Ollama implementation for tests

Expected behavior:

- host runs Ollama
- guest VM reaches it over private HTTP
- `local_agent` sends analysis context, decision text, node state, current config, and previous test failures

### Phase 3: Shared Config Repo Workflow

Goal: make node repair operate on the shared Git repository instead of ad hoc local files.

Implement:

- local checkout manager for `https://github.com/Free-Rat/phoe-nix-config`
- pull before each new decision
- periodic refresh every 5 minutes
- read and write `configuration.nix`
- commit successful repairs
- push successful repairs
- conflict handling path:
  - pull latest
  - attempt to resolve
  - rerun `nixos-rebuild test`
  - push again

### Phase 4: Repair Loop

Goal: close the autonomous config-repair loop.

Implement:

1. receive `Decision`
2. load linked analysis context
3. refresh shared config repo
4. read current `configuration.nix`
5. ask Ollama for updated full-file config content
6. write candidate config
7. run `nixos-rebuild test`
8. if it fails, feed failure output back into Ollama and retry
9. if it succeeds, run `nixos-rebuild switch`
10. commit and push the successful change
11. report the full trace

### Phase 5: Persistence And Visibility

Goal: make the pipeline and repair loop inspectable.

Persist at least:

- `observations`
- `node-state-current`
- `analysis-results`
- `analysis-failures`
- `decisions`
- `execution-results`
- `config-snapshots`
- `repair-traces`
- `service-status`

Minimum service-status events:

- observation published
- analysis started
- analysis completed
- analysis failed
- decision published
- decision received
- repo refreshed
- repair attempt started
- rebuild test failed
- rebuild test passed
- switch completed
- config pushed
- repair failed

### Phase 6: Minimal TUI

Goal: present the pipeline clearly during demos.

Show:

- nodes and latest state
- latest observations
- analysis results
- decisions
- execution results
- repair traces
- service-status timeline through the pipeline

## Testing Plan

### Automated

- unit tests for new `local_agent` runtime modules
- unit tests for Ollama client and response parsing
- unit tests for Git checkout/refresh/push behavior
- unit tests for repair retry logic with `nixos-rebuild test` failures
- simulator tests for config-repair style decisions

### Manual

- real VM with host Ollama access
- real clone of `phoe-nix-config`
- real `nixos-rebuild test` failure and correction loop
- real successful push after repair
- real merge-conflict scenario between two nodes

## Suggested Implementation Order

1. build `local_agent` daemon runtime
2. add Git repo management
3. add Ollama client and repair planner
4. add `test -> retry -> switch -> push` loop
5. add persistence for config snapshots and repair traces
6. build the minimal TUI

## Non-Goals For Now

- production safety guarantees
- public exposure of the local Ollama service to Azure
- strict bounded config-edit schemas
- human approval workflows
- sophisticated multi-node coordination

## Reference Docs

- `proof-of-concept-direction.md`
- `current-state.md`
- `docs/rest-implementation-plan.md`
