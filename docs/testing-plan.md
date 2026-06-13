# Testing Plan

## Scope

Phoe-nix currently has a structured cloud-side pipeline, a node-side `local_agent` runtime, and an in-process simulator. Test the implemented flow first; label future POC ideas as planned material.

## Canonical verification commands

- `bash scripts/test.sh` — main repo test command. It runs unittest discovery for each service package that has a `tests/` directory (`token_service`, `log_service`, `log_router`, `analysis_agent`, `decision_agent`, `local_agent`, `simulator`). `schemas/` does not have a standalone test suite today.
- `bash scripts/simulate-deployment.sh` — in-process end-to-end exercise of the current pipeline. Use this when changing message shapes, routing, analysis/decision flow, or `local_agent` repair behavior.
- `bash infrastructure/smoke-test-poc.sh --node-id nixos` — optional live Azure smoke check after deployment. It requires Azure CLI, deployed resources, and `NODE_API_KEY` / `TF_VAR_node_api_key`.
- `bash scripts/run-live-azure-vm-e2e.sh` — full live POC check for the real blob -> router -> analysis(OpenCode) -> decision -> VM `local_agent` repair path. This is a mutating operator script: it can change the VM config repo and push a real commit.

## What to cover in tests

### Contracts and payloads

- Validate valid and invalid payloads, required-field omissions, and round-trips for shared message shapes.
- When a shared schema changes, update both the producing and consuming package tests plus the simulator assertions.

### Service unit tests

- `token_service`: node/API-key auth, invalid JSON, node-id mismatch, and SAS path/scope/expiry generation.
- `log_service`: batch flush threshold, retry exhaustion, spool write/replay, and payload serialization.
- `log_router`: blob payload parsing, journal-field normalization, timestamp parsing, missing `MESSAGE` rejection, and priority conversion.
- `analysis_agent`: OpenAI-compatible request shape, response extraction, markdown-fence stripping, fallback from raw text, missing-field defaults, and confidence normalization.
- `decision_agent`: suggested-action normalization, `restart_service` command mapping, `apply_config` staying command-free, and audit-document shape.
- `local_agent`: observation publishing, Service Bus receive/complete helpers, cooldown and remediation limits, `no_action` / wrong-node gating, `apply_config` repair-loop retries, and persistence/reporting documents.

### Simulator coverage

Keep the scenarios short and representative:

- log happy path
- observation happy path
- token failure -> spool
- upload failure -> retry recovery
- malformed blob
- invalid AI response
- repair flow through `apply_config`

### Live Azure smoke checks

Use only after a deploy:

- function apps and deployed functions are visible
- Service Bus topics/subscriptions exist
- Cosmos account is reachable
- analysis settings match the expected OpenCode endpoint/model
- token service returns a SAS payload for an authenticated node request
- for the full POC path, `bash scripts/run-live-azure-vm-e2e.sh` can also watch the matching `analysis-input`, `analysis-results`, `final-decisions`, Cosmos `service-status`, `execution-results`, and the VM repo revision

## Practical rules

- For ordinary code changes, run `bash scripts/test.sh`.
- If you changed message formats or cross-service behavior, also run `bash scripts/simulate-deployment.sh`.
- Do not make the live Azure smoke check part of every local edit loop.

## Definition of done

A change is ready when:

1. the relevant package tests pass
2. simulator coverage still matches the current pipeline
3. any Azure-facing change has a documented manual smoke-check path
4. future-only behavior is clearly labeled as planned
