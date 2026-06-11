# phoe-nix

Phoe-nix is a multi-service NixOS self-healing pipeline.

Today the repo implements a structured cloud-side analysis pipeline plus a local simulator, and `local_agent` already includes the daemon/runtime and Git-backed repair loop. The next direction is to make that repair loop the primary config-repair engine for disposable VMs. See `proof-of-concept-direction.md`.

## What exists today

- `token_service`: issues short-lived, path-scoped SAS upload URLs
- `log_service`: tails the journal, batches entries, retries uploads, and spools failed batches locally
- `log_router`: normalizes uploaded log batches and publishes them for analysis
- `analysis_agent`: consumes logs and observations, calls OpenCode, and emits analysis results
- `decision_agent`: turns analysis into remediation intent and stores an audit record in Cosmos DB
- `local_agent`: observation, execution, and reporting logic; daemon workers; Cosmos persistence; Service Bus wiring; Git-backed `apply_config` repair path
- `schemas`: shared Pydantic message models
- `simulator`: in-process end-to-end exercise of the implemented service cores

The simulator is the best repo-level way to validate the current pipeline without Azure.

## Current flow

1. `log_service` collects journal entries and uploads batched payloads through a SAS URL from `token_service`.
2. `log_router` normalizes uploaded batches and emits analysis-ready messages.
3. `analysis_agent` calls OpenCode and publishes analysis output.
4. `decision_agent` writes an auditable decision and publishes remediation intent.
5. `local_agent` consumes decisions and can execute the Git-backed repair loop; the longer-term goal is to make this the primary on-node repair path.

## Direction

- target topic flow: `analysis-input`, `analysis-results`, `final-decisions`
- `local_agent` remains the main repair engine on disposable VMs
- repairs target `https://github.com/Free-Rat/phoe-nix-config`, especially `configuration.nix`
- the local agent uses local LLM reasoning, then `nixos-rebuild test`, then `nixos-rebuild switch`
- the proof of concept prioritizes visibility and iteration over production safety

## Verify

```bash
bash scripts/test.sh
bash scripts/simulate-deployment.sh
```

## Related docs

- `current-state.md`
- `proof-of-concept-direction.md`
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
