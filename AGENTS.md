# AGENTS.md

## Project Focus
- `phoe-nix` is a multi-service NixOS self-healing pipeline.
- **Implemented today:** a structured backend flow across `token_service`, `log_service`, `log_router`, `analysis_agent`, `decision_agent`, `local_agent`, `schemas`, and `simulator`.
- **Planned direction:** a more agentic proof of concept where `local_agent` becomes the main config-repair engine for disposable VMs. See `proof-of-concept-direction.md`.
- Use `README.md` and `current-state.md` as the best short summaries of current behavior.

## Repo Layout
- `token_service/`: Azure Function that issues short-lived, path-scoped SAS upload URLs.
- `log_service/`: on-node journal collector/uploader with batching, retry, and local spooling.
- `log_router/`: Azure Function that normalizes uploaded log batches onto Service Bus topic `analysis-input`.
- `analysis_agent/`: Azure Function that consumes logs/observations, calls OpenCode, and publishes to `analysis-results`.
- `decision_agent/`: Azure Function that turns analysis into remediation intent and stores audit records in Cosmos DB.
- `local_agent/`: node-side observation/execution/reporting core plus the daemon/runtime and Git-backed repair loop.
- `schemas/`: shared Pydantic message contracts.
- `simulator/`: local end-to-end pipeline simulator.
- `infrastructure/`: Terraform modules and Azure bring-up helpers.
- `scripts/`: repo-level test, simulation, deployment, and helper scripts.
- `docs/`, `PLAN.md`, `idea.md`, `current-state.md`, `proof-of-concept-direction.md`: design, status, and direction docs.

## Working Rules
- Prefer Nix-managed workflows when the repo provides them.
- Ignore generated/local state when searching or editing: `*/.venv/`, `infrastructure/.terraform/`, `.ruff_cache/`, `.build/`.
- Root `pyproject.toml` is Ruff configuration, not a workspace/package manifest.
- Do not assume one Python version across the repo; the scripts already handle service-specific runtimes.
- Keep the distinction clear between:
  - **implemented today:** structured cloud pipeline + simulator + local-agent repair loop
  - **planned direction:** make that repair loop the primary disposable-VM repair path

## Common Validation Commands
- Repo tests: `bash scripts/test.sh`
- Local pipeline simulation: `bash scripts/simulate-deployment.sh`
- `log_service` manual run: `cd log_service && nix develop`, then run `log_service -s nginx` inside the shell.
- Infrastructure shell: `cd infrastructure && nix develop`
- Azure Functions deploy: `bash scripts/deploy-functions.sh <resource-group> <environment> token router analysis decision`

## Verification Guidance
- Prefer the repo-level scripts first; they reflect the supported validation path.
- For Terraform, validate per module inside `infrastructure/01-networking` through `04-stateless`; there is no root Terraform module.
- The simulator is the best focused check for cross-service pipeline changes.
- `local_agent` now has tested core logic plus the daemon / rebuild / Git repair loop.

## Infrastructure Safety
- `infrastructure/apply.sh` applies modules in order: `01-networking` -> `02-cosmos` -> `03-blob-storage` -> `04-stateless`.
- `infrastructure/destroy.sh` destroys them in reverse order.
- Both scripts run `terraform apply -auto-approve` / `terraform destroy -auto-approve`; do not run them without explicit user intent.
- Azure login and subscription selection are prerequisites for infra work.
- Azure bring-up steps live in `infrastructure/commands.md`.
- `TF_VAR_opencode_api_key` and node API key values are sensitive; never hardcode or commit them.

## High-Value Facts
- Intended Service Bus topic flow: `analysis-input` -> `analysis-results` -> `final-decisions`.
- Cloud-side AI uses the OpenCode API.
- Proof-of-concept repair target: `https://github.com/Free-Rat/phoe-nix-config`, mainly `configuration.nix`.
- Current repair loop: `nixos-rebuild test` before `nixos-rebuild switch`, with visibility and traceability prioritized for the disposable-VM POC.
