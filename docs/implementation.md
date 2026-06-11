# Implementation Notes

## Repository status

Phoe-nix currently implements a cloud-side ingestion / analysis / decision pipeline plus a local deployment simulator. The node-side `local_agent` runtime is also implemented: it publishes observations, consumes final decisions, enforces safety limits, and can run the current Git-backed `apply_config` repair path.

This document describes what is implemented today and what is still planned.

## Implemented today

### Cloud-side pipeline

- `token_service` validates node identity, reads the storage account key from Key Vault, and issues a short-lived write-only SAS URL for one blob path.
- `log_service` tails the systemd journal, batches entries, retries uploads, and spools failed batches locally for replay.
- `log_router` parses uploaded batches, normalizes each entry into `schemas.NormalizedLog`, and publishes one Service Bus message per entry on `analysis-input`.
- `analysis_agent` consumes `NormalizedLog` and `Observation` messages from `analysis-input`, calls OpenCode, and publishes `AnalysisResult` JSON to `analysis-results`; if the model output is not valid JSON, it falls back to a text-derived result.
- `decision_agent` consumes `analysis-results`, turns analysis into the current command-oriented remediation payload, stores an audit record in Cosmos DB, and publishes `Decision` messages to `final-decisions`.
- `schemas` provides the shared Pydantic contracts used across services.

### Node-side runtime

- `local_agent` builds observations, consumes decisions from `final-decisions`, and persists node-state and execution data.
- It still supports legacy direct-command decisions.
- For `apply_config` decisions, it refreshes `https://github.com/Free-Rat/phoe-nix-config`, edits `configuration.nix`, runs `nixos-rebuild test` with retries, then runs `nixos-rebuild switch`.
- Successful repairs are committed and pushed back to Git.
- The runtime records execution results, config snapshots, repair traces, node-state documents, and service-status documents in Cosmos DB.
- The package includes both daemon-style runtime entrypoints and one-shot helpers used by tests and manual checks.

### Simulator and validation

- `simulator` runs the real service cores in process with fake Blob Storage, Service Bus, Cosmos DB, Key Vault, OpenCode, and local-agent execution.
- It covers happy paths plus token issuance failures, upload retry / recovery, malformed blobs, invalid AI responses, and repair-loop traces.
- Repo-level validation still centers on:
  - `bash scripts/test.sh`
  - `bash scripts/simulate-deployment.sh`

## Planned direction

The next proof-of-concept step is to make `local_agent` the primary config-repair engine on disposable VMs, with cloud analysis providing context rather than a fully prescribed patch. The intended topic flow remains `analysis-input` -> `analysis-results` -> `final-decisions`.

Future work should be treated as planned, including broader operator-facing visibility tooling and any additional production hardening around the VM repair loop.
