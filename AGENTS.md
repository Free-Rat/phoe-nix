# AGENTS.md

## Project Focus
- `phoe-nix` is a multi-service NixOS self-healing pipeline.
- **Current implementation:** an end-to-end backend flow exists across `token_service`, `log_service`, `log_router`, `analysis_agent`, `decision_agent`, `local_agent`, `schemas`, and `simulator`.
- **Intended next direction:** move from structured cloud-side diagnosis plus command execution toward a more agentic proof of concept where `local_agent` becomes the main config-repair engine for disposable VMs. See `proof-of-concept-direction.md`.
- Treat `README.md` and `current-state.md` as the best high-level summary of what is implemented today.

## Repo Structure
- `token_service/`: Azure Function that issues short-lived, path-scoped SAS upload URLs.
- `log_service/`: on-node journal collector/uploader with batching, retry, and local spooling.
- `log_router/`: Azure Function that normalizes uploaded log batches onto Service Bus topic `analysis-input`.
- `analysis_agent/`: Azure Function that consumes logs/observations, calls OpenCode, and publishes to `analysis-results`.
- `decision_agent/`: Azure Function that converts analysis into remediation intent and stores audit records in Cosmos DB.
- `local_agent/`: current node-side observation/execution/reporting core; not yet the full long-running repair daemon described in `proof-of-concept-direction.md`.
- `schemas/`: shared Pydantic message contracts used across services.
- `simulator/`: local end-to-end pipeline simulator; best repo-level substitute for a real deployment.
- `infrastructure/`: Terraform modules plus Azure bring-up helpers.
- `scripts/`: repo-level test, simulation, deployment, and helper scripts.
- `docs/`, `PLAN.md`, `idea.md`, `current-state.md`, `proof-of-concept-direction.md`: design, status, and implementation-direction docs.

## Working Rules
- Prefer Nix-managed workflows when the repo already provides them.
- Ignore generated/local state when searching or editing: `*/.venv/`, `infrastructure/.terraform/`, `.ruff_cache/`, `.build/`.
- Root `pyproject.toml` is Ruff configuration, not a workspace/package manifest.
- Do not assume one Python version across the repo; service runtimes differ, and the repo scripts already handle that.
- Keep the distinction clear between:
  - **implemented today**: multi-service event pipeline plus simulator
  - **planned direction**: richer local-agent config-repair loop on disposable VMs

## Main Verification Commands
- Repo tests: `bash scripts/test.sh`
- Full local pipeline simulation: `bash scripts/simulate-deployment.sh`
- `log_service` manual run: `cd log_service && nix develop` then `log_service -s nginx`
- Infrastructure shell: `cd infrastructure && nix develop`

## Verification Guidance
- Prefer the repo-level scripts first; they reflect the real supported verification path.
- For Terraform, validate per module inside `infrastructure/01-networking` through `04-stateless`; there is no root Terraform module.
- The simulator is the best focused check for cross-service pipeline changes.
- For `local_agent`, remember the package has tested core logic, but the full daemon/rebuild/Git repair loop is still not implemented.

## Infrastructure Safety
- `infrastructure/apply.sh` applies modules in order: `01-networking` -> `02-cosmos` -> `03-blob-storage` -> `04-stateless`.
- `infrastructure/destroy.sh` destroys in reverse order.
- Both scripts use `terraform apply -auto-approve` / `terraform destroy -auto-approve`; do not run them without explicit user intent.
- Azure bring-up steps live in `infrastructure/commands.md`.
- Azure login and subscription selection are prerequisites for infra work.
- `TF_VAR_opencode_api_key` and node API key values are sensitive; never hardcode or commit them.

## High-Value Project Facts
- Intended Service Bus topic flow is `analysis-input` -> `analysis-results` -> `final-decisions`.
- Cloud-side AI uses the OpenCode API.
- The proof-of-concept repair target is the shared config repo `https://github.com/Free-Rat/phoe-nix-config`, mainly `configuration.nix`.
- The intended repair loop is `nixos-rebuild test` before `nixos-rebuild switch`, with visibility and traceability prioritized over production safety for the disposable-VM POC.
