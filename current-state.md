# Current State

## Snapshot

`phoe-nix` currently has a working backend pipeline plus a local simulator. The cloud-side stages still use a structured log/analysis/decision flow, and `local_agent` already includes the daemon runtime and Git-backed repair loop with host-side Ollama. The next direction is to make that repair loop the primary on-node self-healing path on disposable VMs.

## Implemented today

### Cloud pipeline

- `token_service` issues short-lived, path-scoped SAS upload URLs.
- `log_service` tails systemd journal entries, batches them, retries uploads, and spools failed batches locally.
- `log_router` normalizes uploaded batches into `schemas.NormalizedLog` and publishes them to `analysis-input`.
- `analysis_agent` consumes `NormalizedLog` and `Observation` messages, calls OpenCode, validates the response into `AnalysisResult`, and publishes to `analysis-results`.
- `decision_agent` consumes `analysis-results`, turns analysis into remediation intent, stores audit records in Cosmos DB, and publishes `Decision` messages to `final-decisions`.
- `schemas` provides the shared Pydantic contracts.

### Node-side execution

- `local_agent` includes observation building from node state, node-state tracking, remediation safety limits, a daemon runtime with observe/decision/persistence workers, Service Bus publishing to `analysis-input`, and Service Bus consumption of `final-decisions`.
- The current local-agent path still supports direct commands for legacy decision actions, but the `apply_config` path drives the Git-backed repair loop.
- The repair loop refreshes `https://github.com/Free-Rat/phoe-nix-config`, edits `configuration.nix`, runs `nixos-rebuild test`, retries on failure, then runs `nixos-rebuild switch`.
- Successful repairs are committed and pushed, and the runtime persists execution results, config snapshots, repair traces, node-state documents, and service-status records to Cosmos.
- The local repair path uses Ollama on the host via the configured private HTTP endpoint.

### Simulator and tests

- `simulator` runs the real service cores in process with fake Blob Storage, Service Bus, Cosmos DB, Key Vault, OpenCode, and local-agent execution.
- It covers happy paths plus token failure, upload retry/recovery, malformed blobs, invalid AI responses, and repair-loop traces.
- Main validation commands:
  - `bash scripts/test.sh`
  - `bash scripts/simulate-deployment.sh`

## Direction

The remaining direction is to make `local_agent` the default config-repair engine for disposable VMs, with better demo visibility and less emphasis on the older command-only model.

## Where to look

- `README.md`
- `proof-of-concept-direction.md`
- `local_agent/README.md`
- `simulator/README.md`
