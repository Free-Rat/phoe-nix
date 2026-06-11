# Rest Implementation Plan

This file is a remaining-work plan, not a source of truth for current behavior. For the current system state, see `README.md` and `current-state.md`. If this document conflicts with the code or those summaries, the implemented code wins. For the proof-of-concept future direction, `proof-of-concept-direction.md` takes precedence over older wording here.

## Verified Baseline

The repository already has:

- the structured cloud pipeline and local simulator
- the topic flow `analysis-input` -> `analysis-results` -> `final-decisions`
- `local_agent` observation building, node-state tracking, daemon runtime, Service Bus publish/consume wiring, Cosmos persistence, and a Git-backed repair loop
- a repair loop that refreshes `https://github.com/Free-Rat/phoe-nix-config`, edits `configuration.nix`, runs `nixos-rebuild test` before `nixos-rebuild switch`, uses host-side Ollama, and commits/pushes successful repairs
- persisted `local_agent` records for execution results, config snapshots, repair traces, node-state documents, and service-status records

That means the original phase-6 runtime work is no longer purely future work; it is already largely implemented and should not be repeated below as a pending milestone.

## Remaining Work

### 1. Frontend / TUI

Goal: a small local operator view of the pipeline and repair loop.

Planned work:

- define the minimal read model for uploaded blobs, normalized logs, analysis results, decisions, observations, execution results, and node state
- use Cosmos-backed projections for durable state/history plus lightweight service-status records for live stage visibility
- keep the UI simple and local

Still open:

- exact screen set
- correlation keys across entities
- operator config format
- how much raw text versus structured data the UI should expose

### 2. Ops and Resilience

Goal: make failures visible, bounded, and easy to reason about.

Planned work:

- normalize retry/backoff behavior across HTTP, Service Bus, Cosmos DB, and local execution
- add explicit degraded records for analysis-stage failures; this is not implemented yet
- keep DLQ as the terminal path for unprocessable messages
- add idempotency so repeated deliveries do not repeat the same effective repair
- add logs, metrics, and small dashboards for manual operations
- make the TUI show when analysis was reached but no valid `AnalysisResult` was produced

Failure cases still worth handling explicitly:

- OpenCode transport, service, and response failures
- Service Bus parse and publish failures
- local execution failures

### 3. Packaging and Deployment

Goal: make the node-side runtime easy to run on a VM.

Planned work:

- finish packaging and service-install polish around the already-implemented runtime
- document operator install/update steps
- keep deployment script-driven until there is a reason to automate more

### 4. CI/CD and Validation

Goal: automate checks without hiding deployment decisions.

Planned work:

- run unit tests and simulator tests in GitHub Actions
- add per-service checks where they are practical
- add opt-in live Azure smoke tests for Blob, Service Bus, and Cosmos flows
- keep manual deployment as the default for now

### 5. Documentation

Goal: keep the repo operable.

Planned work:

- update architecture diagrams to match the separated topic flow
- document runtime config and deployment steps
- document failure modes and operator runbooks
- document the chosen visibility model for the TUI

## Open Design Questions

- persistence shape for node state history: latest snapshot, append-only history, or both
- exact schema for observations, node snapshots, execution timelines, and service-status records
- exact balance between structured schemas and raw text in `AnalysisResult` and `Decision`
- final TUI read path and operator config format
- exact degraded-analysis record shape and when it is written

## What Not To Repeat As Future Work

Do not restate the basic coordinator loop, Ollama hookup, Git-backed repair loop, or `nixos-rebuild test` / `switch` flow as pending work unless the implementation changes again.
