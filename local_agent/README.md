# Local Agent

Runs on a node, publishes observations, consumes remediation decisions, and reports execution results plus updated node state.

In the intended proof-of-concept direction, `local_agent` is not just a command runner. It should become the primary config-repair agent: it receives decision plus analysis context, uses a local LLM through Ollama on the VM host, updates `configuration.nix` in `https://github.com/Free-Rat/phoe-nix-config`, runs `nixos-rebuild test` correction loops, then runs `nixos-rebuild switch`, pushes successful changes back to Git, and reports the result. See `../proof-of-concept-direction.md`.

## Modules

- `config.py`: runtime settings
- `state.py`: in-memory safety and state tracking
- `monitor.py`: observation building and state summarization
- `executor.py`: current decision execution logic; expected to evolve into config-edit, rebuild-test, and Git-backed repair orchestration for the proof of concept
- `reporter.py`: `ExecutionResult` and node-state document creation
- `bus_client.py`: thin Service Bus adapter
- `main.py`: small runnable entrypoints

## Current Scope

The package implements the current core local-agent logic and tests. It does not yet contain the long-running daemon loop, live system inspection integration, Ollama-backed repair planning, or the Git-backed proof-of-concept config-repair flow.

## Test

```bash
bash scripts/test.sh
```
