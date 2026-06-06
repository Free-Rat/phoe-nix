# Audit Plan

## Goal

Make the proof of concept work with the minimum amount of code while improving code quality, modularity, and functional-style boundaries.

## Principles

1. Prefer one clear contract across code, tests, and infrastructure.
2. Prefer pure helpers with side effects pushed to the edges.
3. Remove duplicate or alternative execution paths when one is enough.
4. Fail loudly on critical persistence and routing errors.
5. Keep changes small and practical for the current POC.

## Priority 0: Contract Alignment

### P0.1 Service Bus topic names

Problem:

1. Code, docs, and Terraform use different topic names.
2. This can make the real Azure deployment behave differently from the simulator.

Changes:

1. Standardize on `analysis-input`, `analysis-results`, and `final-decisions`.
2. Update Python config loaders to use the same env vars.
3. Update Terraform topics, subscriptions, and app settings.

Success criteria:

1. All services read and write the same topic names.
2. Terraform provisions the same topology the Python services expect.

### P0.2 Cosmos container and document contract

Problem:

1. Terraform provisions too few containers.
2. Partition key field names do not match Python document shapes.

Changes:

1. Provision all containers used by the current POC.
2. Align partition key paths to current Python snake_case fields.
3. Keep document builders minimal rather than adding translation layers unless necessary.

Target containers:

1. `observations`
2. `node-state-current`
3. `decisions`
4. `execution-results`
5. `config-snapshots`
6. `repair-traces`
7. `service-status`

Success criteria:

1. Current Python documents can be upserted into provisioned containers without schema mismatch.

## Priority 1: Make Failures Visible

### P1.1 Stop swallowing persistence failures in `local_agent`

Problem:

1. The simulator can report execution success while dropping execution traces and persistence.
2. Hidden failures reduce trust in the POC.

Changes:

1. Remove silent exception swallowing from critical persistence paths.
2. Let focused verification fail if persistence wiring is wrong.
3. Keep optional external integrations soft only where the POC explicitly requires degraded mode.

Success criteria:

1. Repair and execution persistence either succeeds or fails visibly.

### P1.2 Tighten simulator expectations

Problem:

1. The simulator currently proves flow shape but not enough correctness.

Changes:

1. Ensure simulated repair execution persists execution results and traces.
2. Add or update tests so missing persistence becomes a failing condition.

Success criteria:

1. Simulator output includes persisted execution records when a repair runs.

## Priority 2: Simplify `local_agent`

### P2.1 Remove the alternate direct config mutation execution path

Problem:

1. `local_agent` currently has two repair models.
2. One path bypasses the intended `nixos-rebuild test` before `switch` flow.

Changes:

1. Remove the direct Nix-assignment shell command path from `executor.py`.
2. Route config-style repairs through `repair_planner.py` only.

Success criteria:

1. Config repairs always follow the repo-backed `test -> switch -> push` flow.

### P2.2 Remove unnecessary dynamic imports and incidental complexity

Problem:

1. `__import__` is used where normal imports are enough.
2. This adds noise with no POC value.

Changes:

1. Replace dynamic imports in `local_agent.main` and `manual_integration.py` with direct imports.

Success criteria:

1. The entrypoints remain functional with simpler code.

## Priority 3: Infrastructure Simplification

### P3.1 Reduce contract drift in Terraform

Changes:

1. Update `04-stateless` app settings to use the unified topic env vars.
2. Update subscriptions to match the final topic topology.
3. Keep the resource graph explicit and minimal.

### P3.2 Optional follow-up simplification

Changes:

1. Consider collapsing the one-resource `01-networking` module in a later cleanup.
2. Consider replacing repeated Function App blocks with `for_each` only after the POC contract is stable.

## Priority 4: Secondary Code Cleanup

Changes:

1. Remove legacy env var fallbacks that preserve old topic naming.
2. Consider simplifying `token_service.app.HttpResult` if it remains unused as an abstraction.
3. Keep broad public-boundary exception handling only where it protects external callers intentionally.

## Implementation Order

1. Write this plan.
2. Align topic names in Python and Terraform.
3. Align Cosmos containers and document contracts.
4. Remove silent persistence failure behavior.
5. Simplify `local_agent` repair execution flow.
6. Run focused simulator and CLI verification.

## Verification Plan

1. Run `simulate_pipeline` and confirm persisted repair data appears.
2. Run `log_service --help`.
3. Run `local_agent` sample mode.
4. If toolchain availability permits, validate Terraform modules after the contract updates.

## Definition Of Done

1. Real code and infrastructure contracts match.
2. The simulator no longer reports false-positive success for dropped persistence.
3. `local_agent` has one clear config-repair path.
4. The POC remains small and working.
