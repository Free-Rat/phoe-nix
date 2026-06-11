# Audit Plan

## Scope

This plan is for the current repository state, not the earlier architecture proposal. The backend pipeline, topic names, and Cosmos containers are already aligned in code and Terraform; the remaining audit work should focus on the gaps that are still open.

## Current Baseline

- Cloud pipeline: `token_service` -> `log_service` -> `log_router` -> `analysis_agent` -> `decision_agent`
- Service Bus topics: `analysis-input`, `analysis-results`, `final-decisions`
- Cosmos containers already provisioned for the current POC: `observations`, `node-state-current`, `decisions`, `execution-results`, `config-snapshots`, `repair-traces`, `service-status`
- `simulator` covers the implemented cloud-side flow and local-agent execution paths in process
- `local_agent` already has observation building, node-state tracking, persistence workers, Service Bus wiring, and the Git-backed repair daemon/runtime; the audit should now verify its behavior, durability, and visibility

## Priority 0: Verify Contract Drift

Goal: confirm the repo stays aligned with the deployed contract shape.

Check:

1. Service Bus env vars, topic names, and subscription names still match the code, Terraform, and deployment scripts.
2. Cosmos container names and partition-key assumptions still match the Python document shapes.
3. Any new fallback or compatibility path is intentional and documented, not accidental drift.

Success criteria:

- A fresh audit finds no mismatch between the current code, Terraform, and runtime environment variables.

## Priority 1: Verify the `local_agent` Repair Loop

Problem:

- The repo already has the repair loop, but the audit should make sure its current behavior is auditable end to end.
- Legacy direct-command behavior still exists alongside the Git-backed repair flow, so the active path needs to stay explicit and auditable.

Audit and tighten:

1. The observe -> decision -> repair -> test -> switch -> report loop.
2. Persistence of execution results, repair traces, config snapshots, node-state records, and service-status records.
3. Visibility of rebuild failures and persistence failures; do not allow critical failures to disappear silently.
4. The boundary between the intended Git-backed repair path and any legacy direct-command path.

Success criteria:

- A decision can be traced end to end through the repair loop without hidden failures.
- Each attempt leaves durable before/after evidence in Cosmos DB.

## Priority 2: Keep Simulator and Live Deployment Honest

Problem:

- The simulator is the fastest validation path, but it must continue to mirror the deployed contract rather than inventing its own shape.

Audit and tighten:

1. Simulator coverage for the same topics, message types, and persistence outputs used by the current deployment.
2. Azure smoke checks for topic existence, subscriptions, and end-to-end message flow when a live deployment is available.
3. Validation scripts that exercise the repo’s current deployment path instead of manual one-off checks.

Recommended validation:

- `bash scripts/test.sh`
- `bash scripts/simulate-deployment.sh`
- `bash infrastructure/smoke-test-poc.sh` after an Azure deploy

## Future Work

These items are useful, but they are not blockers for the current POC contract and should stay clearly marked as future work:

1. Remove any remaining legacy env-var fallbacks only after no deployed config depends on them.
2. Simplify dynamic imports or helper abstractions only if they still exist in active paths.
3. Consider Terraform refactors such as module consolidation or `for_each` only after the current contract is stable.
4. Add a minimal TUI or dashboard for visibility once the repair loop itself is solid.

## Audit Output Format

For each audit pass, record:

1. What still matches the current repo and deployment shape.
2. What drift or ambiguity remains.
3. Which checks were run.
4. Which items are future work only.

## Definition Of Done

- The current contract stays aligned across code, Terraform, and deployment scripts.
- The `local_agent` repair loop is auditable end to end.
- Simulator and live-deployment checks agree on the same topic and persistence contract.
- Future work stays clearly separated from current POC requirements.
