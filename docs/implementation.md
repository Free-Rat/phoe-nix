# Implementation Notes

This repository now implements the cloud-side log ingestion and remediation decision pipeline through five connected packages: `schemas`, `token_service`, `log_service`, `log_router`, `analysis_agent`, and `decision_agent`.

It also includes `simulator`, a local deployment harness that runs the same service cores in process so the full pipeline can be validated without Azure.

The implemented code still reflects a more structured cloud-analysis pipeline than the intended proof-of-concept direction. The target direction is documented in `proof-of-concept-direction.md` and shifts more repair autonomy into `local_agent`.

## Functional Structure

Each service follows the same shape:

1. `config.py` loads environment-backed settings.
2. Pure helpers transform data into or out of shared schema models.
3. Small adapter modules talk to Azure SDKs or HTTP APIs.
4. `main.py` is a thin Azure Function or CLI boundary.

This keeps the code testable because most tests only validate pure functions and orchestration with injected fakes.

## Shared Schemas

The `schemas` package is the contract between services. The most important models are:

- `NormalizedLog`: normalized log entry sent from `log_router` to `analysis_agent`
- `Observation`: local-agent observation format, already supported by `analysis_agent`
- `AnalysisResult`: current validated AI analysis output, likely to become more text-forward in the proof of concept
- `Decision`: current remediation decision and audit payload, likely to evolve toward looser remediation intent plus analysis context
- `ExecutionResult` and `NodeState`: future-facing models for the local agent and frontend

## Service Boundaries

### `token_service`

- validates node identity
- reads the storage account key from Key Vault
- issues a write-only SAS URL scoped to one blob path

### `log_service`

- tails systemd journal
- batches entries into one blob payload
- retries failed uploads
- spools failed batches to disk for later replay

### `log_router`

- parses uploaded log batches
- normalizes each entry into `NormalizedLog`
- publishes one Service Bus message per entry

### `analysis_agent`

- parses either `NormalizedLog` or `Observation`
- builds a source-specific prompt
- calls the OpenCode API
- currently validates the returned JSON as `AnalysisResult`
- in the proof of concept, may shift toward richer freeform analysis text

### `decision_agent`

- currently converts `AnalysisResult` into a concrete NixOS command
- in the proof of concept, should move toward remediation intent for the local repair agent
- writes the decision to Cosmos DB with a stable audit document
- republishes the final decision payload for downstream consumers

### `simulator`

- executes the implemented services in real deployment order
- uses in-memory fakes for Blob Storage, Service Bus, Cosmos DB, Key Vault, and OpenCode
- validates that the current cloud-side pipeline works end to end
- simulates local-agent execution of final decisions and stores execution results
- should later simulate config-level repair attempts driven by decision plus analysis context
- includes failure scenarios for token issuance, upload recovery, malformed blobs, and invalid AI output

## Current Limits

- current topic wiring is older than the intended design. The target split is `analysis-input`, `analysis-results`, and `final-decisions`.
- `local_agent` runtime, proof-of-concept config repair, and the frontend/TUI remain future phases.
- `log_service` still has a Nix packaging issue on its `nix run` path even though its tested Python logic is implemented.
