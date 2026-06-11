# Remaining Work

This document tracks what is still open after the current backend pipeline, simulator, and `local_agent` repair loop. It intentionally avoids repeating `current-state.md` and `proof-of-concept-direction.md`.

## Priority 1: Operator visibility

`local_agent` already has a daemon runtime, Service Bus wiring, Cosmos persistence, and a Git-backed repair loop. The missing piece is a small operator-facing view of that data.

Remaining work:

- define the minimal read model for incidents, node state, observations, decisions, execution results, and service-status events
- build a local-only TUI or equivalent status view around those records
- show end-to-end pipeline progress, including failures, not just happy paths

## Priority 2: Operational hardening

The repo still needs better behavior around repeated messages and failure surfacing.

Remaining work:

- normalize retry/backoff across HTTP, Service Bus, Cosmos DB, and local execution
- handle duplicate deliveries and repeated decisions without double-applying repairs
- add explicit degraded/error records for analysis failures and other unprocessable stages
- make DLQ and other failure states visible to operators instead of only in logs

## Priority 3: Live Azure validation

The repo already has smoke checks and manual integration scripts, but they are still lighter-weight than a full cloud flow check.

Remaining work:

- extend the existing live checks to exercise the real Blob -> Router -> Analysis -> Decision -> Local Agent path
- assert the downstream Cosmos writes and message handoffs, not only resource existence
- keep the live suite manual and environment-gated for now

## Priority 4: Packaging and install

`local_agent` is runnable from the repo, but the on-node deployment story is not finished.

Remaining work:

- finish the Nix/systemd service packaging story for `local_agent`
- add operator-facing install, update, and restart instructions
- document the expected runtime environment files and required secrets

## Priority 5: Documentation cleanup

Keep docs aligned with the implemented runtime instead of re-describing planned work.

Remaining work:

- update diagrams and runbooks only where they still diverge from the current code
- avoid adding new docs that duplicate `current-state.md`, `README.md`, or `proof-of-concept-direction.md`
