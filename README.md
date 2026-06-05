# phoe-nix

Self-healing NixOS pipeline built from small services.

Current implementation and intended proof-of-concept direction are not identical yet. The current codebase already implements a structured cloud pipeline, while the intended next step is a more agentic proof of concept where `local_agent` uses cloud analysis plus local LLM reasoning to attempt config-level self-repair on disposable VMs. See `proof-of-concept-direction.md`.

## Implemented Services

- `token_service`: issues short-lived, path-scoped blob upload SAS URLs
- `log_service`: tails systemd journal, batches log entries, retries uploads, and spools failed batches locally
- `log_router`: normalizes uploaded log batches and publishes normalized messages to Service Bus
- `analysis_agent`: consumes normalized logs or observations, builds prompts, calls the OpenCode API, and emits `AnalysisResult`
- `decision_agent`: converts analysis into remediation intent and stores decision audit records in Cosmos DB
- `local_agent`: publishes observations, consumes decisions plus analysis context, and is intended to become the on-node config-repair agent
- `schemas`: shared Pydantic message models used across services

## Pipeline

1. `log_service` collects journal entries and uploads batches to Blob Storage using a SAS URL from `token_service`.
2. `log_router` is triggered by uploaded blobs and emits normalized log messages to Service Bus topic `analysis-input`.
3. `analysis_agent` consumes those messages, builds an AI prompt, calls OpenCode, and publishes analysis output to topic `analysis-results`.
4. `decision_agent` consumes analysis results, stores a decision record in Cosmos DB, and publishes remediation intent to topic `final-decisions`.
5. `local_agent` is intended to receive both the decision and the related analysis context, repair `configuration.nix` from the shared config repo with help from a local Ollama model, run `nixos-rebuild test` and then `switch`, push successful changes back to Git, and report the outcome.

## Design Style

Each service keeps side effects at the edges:

- parsing and transformation live in pure helper functions
- Azure SDK calls are isolated in small adapter functions
- `main.py` files stay thin and mainly translate Azure bindings into the tested core code

## Test

```bash
bash scripts/test.sh
```

## Simulate Deployment

```bash
bash scripts/simulate-deployment.sh
```

The simulator now covers:

- log-to-decision happy path
- observation-to-decision happy path
- local-agent decision consumption
- token failure spooling
- upload retry/recovery
- malformed log blob handling
- invalid AI response handling

## Direction

- Proof-of-concept target: disposable VMs, arbitrary config changes allowed, local repair loop prioritized over production safety
- Pipeline visibility target: a minimal local TUI should show evidence moving through the whole system
- Topic target: `analysis-input`, `analysis-results`, `final-decisions`
- Local repair target: `https://github.com/Free-Rat/phoe-nix-config` with `configuration.nix` as the main editable file
- Local model target: Ollama on the VM host, reached over private HTTP from the guest VM

## Deploy Azure Functions

```bash
bash scripts/deploy-functions.sh <resource-group> <environment> token router analysis decision
```

## Service Docs

- `token_service/README.md`
- `log_service/README.md`
- `log_router/README.md`
- `analysis_agent/README.md`
- `decision_agent/README.md`
- `local_agent/README.md`
- `simulator/README.md`
- `docs/implementation.md`
- `docs/testing-plan.md`
- `docs/remaining-work.md`
- `proof-of-concept-direction.md`

## Current Gaps

- `local_agent` runtime, Ollama-backed repair loop, and config-repo push/pull workflow are not implemented yet
- `log_service` logic is implemented, but its existing `nix run` packaging path still needs flake-level cleanup
