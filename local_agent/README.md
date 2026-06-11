# Local Agent

`local_agent` is the node-side runtime. It gathers live node state, publishes observations, consumes `final-decisions`, applies remediation, and records the resulting execution and state documents.

This package already includes the daemon workers, live system inspection, Service Bus wiring, Cosmos persistence, and the Git-backed `apply_config` repair path. The longer-term proof of concept is to make this runtime the primary config-repair engine on disposable VMs.

## Implemented today

- observation building and change detection
- node-state tracking and remediation safety limits
- Service Bus publish/consume helpers for `analysis-input` and `final-decisions`
- Cosmos DB persistence for observations, node state, execution results, config snapshots, repair traces, and service-status records
- legacy direct-command decisions
- the current Git-backed repair loop for `apply_config` decisions:
  - refresh `https://github.com/Free-Rat/phoe-nix-config`
  - read and rewrite `configuration.nix`
  - use host-side Ollama to propose edits
  - run `nixos-rebuild test`, retry on failure, then run `nixos-rebuild switch`
  - commit and push successful repairs
- daemon-style runtime workers plus one-shot helpers used by tests and manual checks

## Planned direction

The longer-term proof of concept is to make `local_agent` the primary config-repair engine on disposable VMs. In that mode, cloud analysis provides context and the node-side agent owns the repair loop. See `../proof-of-concept-direction.md`.

## Module map

- `config.py`: environment-backed runtime settings
- `state.py`: node state and remediation cooldown tracking
- `monitor.py`: observation construction and publish decisions
- `system_state.py`: live node-state collection
- `executor.py`: direct-command execution and safety checks
- `repair_planner.py`: prompt building, config rewriting, rebuild/retry, commit/push
- `git_repo.py`: repository refresh and Git helpers
- `ollama_client.py`: Ollama HTTP client
- `persistence.py`: Cosmos document writes
- `reporter.py`: observation, execution, and state document builders
- `bus_client.py`: Service Bus publish/receive helpers
- `runtime.py`: observe / decide / persist orchestration and daemon loop
- `main.py`: small CLI entry points for sample and daemon modes

## Running

`main.py` switches to daemon mode when `LOCAL_AGENT_RUN_MODE=daemon`; otherwise it prints a sample observation.

## Validation

```bash
bash scripts/test.sh
```
