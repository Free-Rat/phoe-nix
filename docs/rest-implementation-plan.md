# Rest Implementation Plan

This document captures the remaining implementation work after the current backend pipeline prototype, plus the architecture decisions already made and the open decisions still owned by the project author.

The latest direction update is now captured in `../proof-of-concept-direction.md`. Where this file conflicts with that newer proof-of-concept direction, the proof-of-concept document should win.

## Confirmed Decisions

- `local_agent` will be a single long-running process with internal async workers.
- `local_agent` will use connection strings for Service Bus and Cosmos DB access in the first implementation.
- Cloud services will keep using the OpenCode Go API.
- Frontend will be local-only for now.
- Frontend will be single-user and have no authentication.
- Frontend direction should stay simple and is likely better as a TUI than a web UI.
- Service Bus flow should use separate topics for analysis input, analysis results, and final decisions.
- The concrete topic names are `analysis-input`, `analysis-results`, and `final-decisions`.
- Local-agent observations should also be persisted.
- OpenCode failures should create degraded operator-visible records in addition to normal DLQ behavior.
- Local-agent execution failures should retry up to 3 times.
- The proof of concept allows arbitrary config changes on disposable VMs.
- `local_agent` is expected to become the primary config-repair agent and may use local LLM reasoning.
- `local_agent` should use Ollama running on the VM host, reached over private HTTP from the guest VM.
- The local repair target is `https://github.com/Free-Rat/phoe-nix-config`, editing `configuration.nix`.
- The repair loop should run `nixos-rebuild test` before `nixos-rebuild switch` and feed failures back into the local LLM.
- Successful repairs should be committed and pushed back to the shared config repository.
- The config repository should be refreshed before each new decision and every 5 minutes.
- Frontend visibility will use a mixed approach: Cosmos-backed read models plus lightweight service status records.
- Deployment should stay manual through explicit scripts and operator action.
- Manual live Azure testing is enough for now.
- Priority order is:
  1. `local_agent` production runtime
  2. frontend/TUI
  3. ops/resilience
  4. CI/CD
  5. docs

## Open Architecture Decisions

These remain intentionally open and should be decided before implementation in the affected phase.

- Persistence shape for node state: latest snapshot only, append-only history, or both.
- Exact schema for persisted observations, node snapshots, execution timelines, and service status records.
- Exact balance between structured schemas and raw text in `AnalysisResult`/`Decision` for the proof of concept.
- Final TUI read path and operator config format.
- Exact degraded-analysis record shape and when it is written.

## Phase 6: `local_agent` Production Runtime

Goal: turn the current reusable `local_agent` core into a real on-node daemon.

### Milestone 6.1: Runtime Coordinator

- Implement a single process main loop with async workers for observe, execute, and report flows.
- Add startup wiring for config, state store, message clients, and shutdown handling.
- Keep worker boundaries explicit so each loop can be tested independently.

### Milestone 6.2: Real Node Inspection

- Replace placeholder state gathering with real host inspection.
- Collect current generation, failed units, restart counts, disk usage, uptime, and remediation state.
- Preserve a stable normalized node-state model for downstream consumers.

### Milestone 6.3: Real Messaging

- Publish observations to the `analysis-input` topic.
- Consume final decisions from the `final-decisions` topic.
- Add message acknowledgement, retry/backoff, and idempotency handling.

### Milestone 6.4: Reporting And Persistence

- Persist execution results.
- Persist observations.
- Persist current node-state snapshot, and optionally state history depending on the chosen model.
- Persist enough decision-execution timeline data for the TUI to show where a node is in the pipeline.

### Milestone 6.5: Proof-Of-Concept Config Repair

- Allow `local_agent` to consume decision plus analysis context.
- Add a local LLM-driven config editing loop through host-side Ollama.
- Add local checkout management for `phoe-nix-config`.
- Add `git pull`/refresh before each decision and every 5 minutes.
- Add `nixos-rebuild test` correction loops before `switch`.
- Add commit/push handling for successful repairs, including conflict resolution retries.
- Capture before/after config state, rebuild output, and retry traces.
- Keep the implementation demo-friendly rather than production-safe.

### Milestone 6.6: Packaging And Service Install

- Finish `flake.nix` packaging for `local_agent`.
- Add a `systemd` service definition and operator-facing install/update instructions.
- Verify graceful stop, restart behavior, and spool/recovery behavior where applicable.

## Phase 7: Frontend / TUI

Goal: a local operator tool that shows the whole pipeline working end to end.

### Milestone 7.1: Data Model For Visibility

- Define which entities the tool shows: uploaded blobs, normalized logs, analysis results, decisions, observations, execution results, and node state.
- Define correlation keys across those entities so one incident can be traced through the full pipeline.
- Add lightweight service-status records so the operator can see transitions like observation published, analysis started, analysis finished, decision emitted, decision received, and execution reported.

### Milestone 7.2: Read Path

- Use the mixed approach already chosen:
  - Cosmos-backed projections for durable state and history
  - lightweight service-status records for pipeline-stage visibility
- Prefer the smallest shape that still exposes live pipeline progress clearly.

### Milestone 7.3: TUI Screens

- Pipeline overview screen
- Node state screen
- Observation stream screen
- Incident-to-decision trace screen
- Execution results screen
- Error/DLQ visibility screen

### Milestone 7.4: Local Runtime

- Add a simple local run command.
- Make the tool usable without Azure-hosted frontend infrastructure.

## Phase 8: Ops And Resilience

Goal: make failures visible, bounded, and recoverable.

### Milestone 8.1: Retries And Backoff

- Normalize retry policy across HTTP, Service Bus, Cosmos DB, and local execution.
- Keep retry counts and delay curves explicit in config.

### Milestone 8.2: Failure Recording

- Record degraded operator-visible analysis failures when OpenCode fails.
- Preserve raw failure context for operator review.
- Keep DLQ as the terminal path for unprocessable messages.
- Make the TUI able to show that a message reached analysis but failed before a valid `AnalysisResult` was produced.

#### Failure Types To Handle Explicitly

- OpenCode transport failure: timeout, DNS failure, TLS failure, connection reset.
- OpenCode service failure: HTTP 5xx or provider outage.
- OpenCode response failure: invalid JSON, wrong schema, missing required fields.
- Service Bus processing failure: message cannot be parsed or downstream publish fails.
- Local execution failure: command exits non-zero, times out, or produces a safety-check rejection.

### Milestone 8.3: Idempotency

- Enforce stable ids/keys for analysis results, decisions, execution results, and repeated message delivery.
- Prevent repeated local execution of the same effective decision.

### Milestone 8.4: Operational Visibility

- Add structured logs and metrics.
- Define the minimal alerts and dashboards needed for manual operations.
- Make the TUI the first operator-facing visibility layer for demos and manual runs.

## Recommended Persistence Shape

Unless another decision replaces it, use both current snapshots and append-only history.

### Durable Records

- `observations`: append-only local-agent observations.
- `node-state-current`: one latest snapshot per node.
- `node-state-history`: optional append-only snapshots or major state transitions.
- `analysis-results`: validated AI outputs.
- `analysis-failures`: degraded records for analysis-stage failures.
- `decisions`: final decision records.
- `execution-results`: append-only command/remediation outcomes.
- `config-snapshots`: before/after `configuration.nix` snapshots or diffs.
- `repair-traces`: local LLM prompts, responses, and rebuild-test attempts.
- `service-status`: lightweight stage/status records for pipeline visibility.

### Purpose Of Each Record Type

- `observations`: explain what the node saw and why analysis started.
- `node-state-current`: show the current operator view of each node quickly.
- `node-state-history`: explain trends over time and support presentations/debugging.
- `analysis-results`: show what the AI concluded from logs or observations.
- `analysis-failures`: show where the reasoning stage failed without hiding it in logs only.
- `decisions`: provide auditability of chosen remediations.
- `execution-results`: prove what actually happened on the node.
- `config-snapshots`: show exactly what changed in `configuration.nix`.
- `repair-traces`: explain why the local repair loop made each attempt and how `test` failures influenced retries.
- `service-status`: make pipeline progress visible even when a durable business object has not yet been produced.

## Current Open Design Question

The main remaining design question is not whether config changes are allowed. They are.

The remaining implementation work is now operational: build the daemon runtime, connect it to host-side Ollama, and make the Git-backed repair loop observable and reliable enough for demos.

## Phase 9: CI/CD

Goal: automate validation while keeping deployment manual.

### Milestone 9.1: Validation Workflows

- Run unit tests and simulator tests in GitHub Actions.
- Add per-service checks where they are realistic.

### Milestone 9.2: Manual Deployment Tooling

- Keep deployment script-driven.
- Ensure scripts are explicit about which services are being updated.
- Keep node updates manual or triggered through explicit operator-approved decisions.

## Phase 10: Docs

Goal: make the system operable and understandable.

- Update architecture diagrams to reflect separated topics.
- Document runtime config and deployment steps.
- Document failure modes and operator runbooks.
- Document the chosen visibility model for the TUI.
