# Phoe-nix Plan

## Goal

Finish the proof-of-concept self-healing loop on disposable VMs.

The intended end state is:

1. a node emits logs or observations
2. cloud services analyze and route the issue
3. `local_agent` receives remediation context
4. `local_agent` updates `configuration.nix` from the shared config repo
5. `local_agent` runs `nixos-rebuild test`
6. if needed, `local_agent` retries with Ollama feedback
7. `local_agent` runs `nixos-rebuild switch`
8. successful config changes are pushed back to the shared repo
9. the full repair trace is persisted and visible

## Current Architecture

1. `log_service` uploads journal batches to Blob Storage through `token_service`
2. `log_router` publishes normalized entries to `analysis-input`
3. `analysis_agent` uses OpenCode Go API and publishes analysis output to `analysis-results`
4. `decision_agent` stores decisions in Cosmos DB and publishes remediation intent to `final-decisions`
5. `local_agent` now has:
   - a daemon/runtime path
   - a Service Bus receive loop
   - a Git-backed repair planner
   - an Ollama client
   - config snapshot and repair trace persistence helpers
6. VM-side NixOS services now exist for both `log_service` and `local_agent`

## Fixed Decisions

- Proof of concept only; nodes are disposable VMs
- `local_agent` is a single long-running process with internal async workers
- Cloud authentication for `local_agent` uses connection strings
- Service Bus topics are `analysis-input`, `analysis-results`, and `final-decisions`
- Cloud-side AI stays on OpenCode Go API
- Node-side repair uses Ollama on the VM host
- VM-to-host Ollama access uses direct private HTTP
- Shared config repo is `https://github.com/Free-Rat/phoe-nix-config`
- Main editable file is `configuration.nix`
- Before each new decision, and every 5 minutes, `local_agent` refreshes the config repo
- Repair loop is `nixos-rebuild test` first, then `nixos-rebuild switch`
- Failed `test` output is fed back into the local repair loop
- Successful changes should be committed and pushed back to the shared config repo
- If push hits a merge conflict, `local_agent` should pull, attempt to resolve, rerun `test`, and push again
- Frontend should be a minimal local TUI showing the pipeline and state transitions

## What Is Already Done

### Backend services

- `token_service` implemented and tested
- `log_service` implemented and tested
- `log_router` implemented and tested
- `analysis_agent` implemented and tested
- `decision_agent` implemented and tested
- shared schemas implemented and updated for richer POC context

### `local_agent` code

- runtime coordinator module exists
- observe/persist/decision worker logic exists
- Service Bus polling helpers exist
- Git repo helper module exists
- Ollama client exists
- repair planner exists
- config snapshots / repair traces / service-status document builders exist
- manual integration entrypoint exists

### Simulator

- simulator now uses the repair-planner-based path for `local_agent`
- simulator persists repair traces and config snapshots

### VM / NixOS wiring

- `phoe-services.nix` exists in `phoe-nix-config`
- `flake.nix` in `phoe-nix-config` imports `phoe-services.nix`
- `run-vm.sh` builds with `--impure`
- VM service units exist for:
  - `log_service`
  - `local_agent`

### Validation already performed

- `bash scripts/test.sh` passes
- simulator passes with direct fallback command
- VM builds successfully from `phoe-nix-config`
- VM boots successfully
- verified over SSH that:
  - `log_service.service` is active
  - `local_agent.service` is active
- verified after delay that current `local_agent` instance remains running with `NRestarts=0`

## Current State Of The VM Setup

The VM service setup is working as a proof-of-concept runtime shell, but not yet as a full live repair pipeline.

What is true right now:

- `log_service` runs under `systemd`
- `local_agent` runs under `systemd`
- `local_agent` is resilient to missing or invalid Azure endpoints and stays alive
- the VM can reach the host network model expected by the Ollama configuration (`http://10.0.2.2:11434`)

What is still placeholder in the VM:

- `SERVICEBUS_CONNECTION` is intentionally fake
- `COSMOSDB_ENDPOINT` is intentionally fake
- `TOKEN_SERVICE_URL` is intentionally fake
- no real Azure connectivity has been configured in the VM service env yet

## Remaining Work

### Phase 1: Real VM Integration

Goal: move the VM services from “alive with placeholders” to “actually integrated”.

Do next:

1. replace fake `SERVICEBUS_CONNECTION` in `phoe-services.nix` with a real value or a local stub path
2. replace fake `COSMOSDB_ENDPOINT` with a real value or disable persistence cleanly for VM-only runs
3. decide whether `log_service` in the VM should:
   - talk to a real deployed `token_service`
   - or use a local stub uploader path for VM demos
4. verify `local_agent` can actually reach Ollama on the host from inside the VM

Files:

- `/home/freerat/projects/phoe-nix-config/phoe-services.nix`
- `/home/freerat/projects/phoe-nix-config/configuration.nix`

### Phase 2: Real Ollama-Driven Repair From The VM

Goal: run the repair loop for real from inside the VM.

Do next:

1. SSH into the VM
2. verify Ollama reachability from the guest:
   - `curl http://10.0.2.2:11434/api/tags`
3. run the manual integration entrypoint in the VM context
4. confirm the local model produces a full replacement `configuration.nix`
5. confirm the `nixos-rebuild test` retry path works with a real or simulated failure

Success condition:

- at least one end-to-end repair attempt runs inside the VM using host Ollama

### Phase 3: Shared Config Repo Push/Pull Behavior

Goal: validate the repo-backed workflow for real.

Do next:

1. point `CONFIG_REPO_PATH` at the VM state directory checkout
2. confirm initial clone/pull works in the VM
3. confirm successful repair commits and pushes back to `phoe-nix-config`
4. simulate a remote change and verify refresh/retry behavior
5. improve merge-conflict handling if the current refresh-and-retry loop is not enough

Notes:

- current code retries by refreshing the repo and re-planning
- it does not yet perform sophisticated semantic conflict resolution

### Phase 4: Real Daemon Behavior

Goal: make `local_agent` a robust long-running worker, not just a working proof.

Do next:

1. add proper Service Bus message settlement/error handling
2. add explicit shutdown handling for daemon mode
3. add better retry/backoff around Azure calls
4. add repo refresh scheduling into the daemon loop rather than only into repair execution
5. make service-status events more complete for live troubleshooting

Files:

- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/bus_client.py`

### Phase 5: Persistence Completion

Goal: make persisted data useful for a TUI and debugging.

Persist and verify:

- `observations`
- `node-state-current`
- `analysis-results`
- `analysis-failures`
- `decisions`
- `execution-results`
- `config-snapshots`
- `repair-traces`
- `service-status`

Do next:

1. confirm container names match deployed Cosmos containers
2. verify writes succeed with real credentials
3. ensure failed repair attempts still persist trace documents

### Phase 6: Minimal TUI

Goal: present the pipeline clearly during demos.

Show:

- nodes and latest state
- latest observations
- analysis results
- decisions
- execution results
- repair traces
- service-status timeline through the pipeline

This should be built only after real persistence is flowing.

## Known Gaps

- `local_agent` VM service is currently configured to stay up even when Azure endpoints are invalid; this is good for runtime verification but not yet real integration
- `log_service` VM unit is running, but its upload path is not yet wired to a real token/blob flow in the VM
- `local_agent` flake packaging is not the path currently used by the VM; the VM uses direct source wrappers instead
- no real Azure-backed end-to-end repair from the VM has been validated yet
- no TUI exists yet

## Suggested Next Commands

For continuation, the most useful commands are:

1. rebuild and boot the VM:

```bash
cd /home/freerat/projects/phoe-nix-config
nix build .#vm --no-write-lock-file --impure
./result/bin/run-nixos-vm
```

2. check service state in the VM:

```bash
ssh -p 2222 user@localhost "systemctl status log_service local_agent --no-pager"
```

3. test host Ollama reachability from the VM:

```bash
ssh -p 2222 user@localhost "curl http://10.0.2.2:11434/api/tags"
```

4. run manual local-agent integration from the VM or host environment with real settings.

## Testing Status

Automated:

- `bash scripts/test.sh` passes

Simulator:

- planner-based simulator path passes

Manual:

- VM build succeeded
- VM boot succeeded
- `log_service` active in VM
- `local_agent` active in VM

## Non-Goals For Now

- production safety guarantees
- public exposure of the local Ollama service to Azure
- strict bounded config-edit schemas
- human approval workflows
- sophisticated multi-node coordination

## Reference Docs

- `proof-of-concept-direction.md`
- `current-state.md`
- `docs/rest-implementation-plan.md`
