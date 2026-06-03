# AGENTS.md

## Current Reality
- This repo is not a full multi-service implementation yet. `idea.md` and `PLAN.md` are design docs; the only implemented code paths today are `log_service/` and `infrastructure/`.
- Ignore local artifacts when searching or editing: `log_service/.venv/` and `infrastructure/.terraform/` are generated state, not source.
- Prefer Nix-managed workflows in every service: new tooling, runnable services, and dev environments should be packaged and managed through Nix rather than ad hoc local setup.

## Repo Layout
- `log_service/`: Python package for the on-node journal reader. Real entrypoint is `log_service/src/log_service/main.py`.
- `infrastructure/`: Terraform split into numbered modules (`01-networking` -> `04-stateless`), plus helper scripts and a Nix dev shell.

## Log Service
- Source of truth is `log_service/pyproject.toml` + `log_service/flake.nix`, not the README examples.
- Local Python target is `>=3.14` (`pyproject.toml`), while Azure Function infra is pinned to Python `3.11` in `infrastructure/04-stateless/main.tf`. Do not assume one runtime across the repo.
- The installable CLI name is `log_service` (underscore). The README example `log-service` is stale.
- `python -m log_service` is not wired by the package layout; use the console script or `nix run` instead.
- Current implementation is still stubbed for storage integration: `get_storage_token()` and `save_to_storage()` in `src/log_service/main.py` do not talk to Azure yet.
- `systemd-python` needs system packages; `log_service/flake.nix` supplies `pkg-config` and `systemd.dev`, and its `shellHook` runs `uv sync` automatically.

## Commands
- Log service, preferred: `cd log_service && nix run . -- -s nginx systemd`
- Log service dev shell: `cd log_service && nix develop` then run `log_service -s nginx`
- Infrastructure shell: `cd infrastructure && nix develop`
- Apply infra: `cd infrastructure && ./apply.sh`
- Destroy infra: `cd infrastructure && ./destroy.sh`

## Verification
- There is currently no repo-wide CI, test suite, lint config, or formatter config checked in.
- For `log_service`, the most realistic focused verification is exercising the CLI/help path from `log_service/` rather than guessing nonexistent test commands.
- For Terraform, validate per module inside `infrastructure/01-*` through `04-*`; there is no root Terraform module.

## Infrastructure Gotchas
- `infrastructure/apply.sh` applies modules in dependency order and uses `terraform apply -auto-approve`.
- `infrastructure/destroy.sh` destroys in reverse order and uses `terraform destroy -auto-approve`.
- Do not run either infrastructure script without explicit user intent.
- Terraform uses a local backend (`backend "local" {}` in `04-stateless/versions.tf`); expect local state files rather than remote state.
- Azure login is a prerequisite before infra work (`az login`, then `az account set ...` per `infrastructure/commands.md`).
- `04-stateless` requires the sensitive Terraform variable `opencode_api_key`; do not hardcode or commit secrets.
