# Scripts, CI, And TUI Plan

Date: 2026-06-12

This document grades each operational script, identifies overlap/safety issues, and proposes a CI pipeline plus a small POC dashboard/TUI.

## Script Grades

| Script | Grade | Purpose | Recommendation | Main risks/fixes |
|---|---:|---|---|---|
| `scripts/test.sh` | A- | Canonical repo test runner. | Keep; make PR CI required. | Add no-refresh CI mode; add lint/type checks separately. |
| `scripts/simulate-deployment.sh` | A- | In-process simulator entrypoint. | Keep; make PR CI required. | Add deterministic CI mode; improve simulator realism. |
| `scripts/deploy-functions.sh` | B | Build/deploy Azure Functions. | Keep for manual deploy workflow. | Add `--dry-run`, preflight checks, service validation, safer cleanup. |
| `scripts/check-deployment.sh` | B+ | Read-only live deployment/VM status reporter. | Keep; use as TUI data source. | Fix macOS portability, token cache handling, JSON output tests. |
| `scripts/publish-test-decision.sh` | B | Publish live test decision. | Keep manual helper. | Live side effect; use real schema validation, set message ID, add `--dry-run`. |
| `scripts/phase4-verify.sh` | B | Verify decision receive path. | Keep short-term; later fold into TUI checks. | Overlaps publisher/check scripts; add `--dry-run`; reuse common helpers. |
| `scripts/phase5-verify.sh` | B- | Verify repair-loop path. | Keep manual POC helper. | Live repair side effect; escape/parameterize Cosmos query; add stronger confirmation. |
| `scripts/run-live-ollama-pipeline.py` | C+ | Hybrid live Azure + local Ollama analysis. | Keep as experimental/manual only. | Stops deployed Function App and mutates subscription; require explicit danger flag. |
| `scripts/verify-vm-repo-write.sh` | C+ | Verify VM can write config repo. | Keep manual helper. | Pushes/deletes branches; require explicit repo/confirmation and cleanup trap. |
| `scripts/manual-local-agent-integration.sh` | B | Run local-agent manual integration. | Keep; optional CI if fully mocked. | Clarify mutation/local temp state. |
| `scripts/run-guest-manual-integration.sh` | C | Run integration inside guest VM. | Keep as VM debug helper. | Fragile `ExecStart` parsing; document runner contract. |
| `scripts/run-mock-simulation.sh` | C | Remote host + guest VM + mock Azure simulation. | Keep experimental; split later. | Hardcoded paths/password/ports/Python store path; add dry-run and cleanup. |
| `scripts/mock_azure.py` | B | Minimal HTTP mock Azure services. | Keep; consider moving under simulator/tools. | No auth; limited Service Bus realism; add tests and docs. |
| `scripts/publish-mock-decision.py` | B | Publish mock decision to mock Azure. | Keep; merge into mock tooling. | Add CLI args and schema validation. |
| `scripts/start-updated-local-agent.sh` | F | Starts guest local-agent from copied source. | Remove or replace. | Hardcoded Nix store Python path and missing env assumptions. |
| `scripts/sanitize-hardware-config.py` | B- | Sanitize hardware config. | Keep. | Add arg validation, stdout mode, and tests. |
| `infrastructure/apply.sh` | C+ | Apply Terraform modules in order. | Keep but make safer. | `-auto-approve`; add plan/confirm, script-dir resolution, validation. |
| `infrastructure/destroy.sh` | D+ | Destroy Terraform modules in reverse. | Keep only with safeguards. | Destructive `-auto-approve`; require typed confirmation. |
| `infrastructure/render-vm-env.sh` | B- | Render VM env from Azure. | Keep but secure. | Prints secrets and broad keys; write `0600`, redact stdout, add `NODE_ID`. |
| `infrastructure/smoke-test-poc.sh` | B | Live POC smoke test. | Keep manual/live CI job. | Requires Azure + secrets; add JSON output and avoid duplicated naming logic. |

## Overlap And Cleanup

Duplicated concerns:

- Azure naming/key resolution appears in `publish-test-decision.sh`, `phase4-verify.sh`, `phase5-verify.sh`, `render-vm-env.sh`, `smoke-test-poc.sh`, and `run-live-ollama-pipeline.py`.
- Live status checks overlap between `check-deployment.sh`, `smoke-test-poc.sh`, and phase scripts.
- Mock POC tooling is spread across `mock_azure.py`, `publish-mock-decision.py`, and `run-mock-simulation.sh`.
- VM/guest integration helpers are fragmented and brittle.

Cleanup plan:

1. Add `scripts/common/azure-names.sh` for shell Azure naming defaults.
2. Add `scripts/common/servicebus_publish.py` or a small Python module for schema-validated test publishing.
3. Make `check-deployment.sh --json` the single read-only status surface.
4. Make phase scripts call shared publisher/status functions instead of duplicating logic.
5. Replace `start-updated-local-agent.sh` with a Nix/uv-based runner or delete it.
6. Split `run-mock-simulation.sh` into smaller mock server, guest setup, and scenario-runner commands.

## Immediate Script Fixes

1. `render-vm-env.sh`: add `NODE_ID`, align `OLLAMA_MODEL`, set `umask 077`, write `0600`, redact stdout by default.
2. `publish-test-decision.sh`: validate using shared `schemas.Decision`, set Service Bus message ID, add `--dry-run`.
3. `phase5-verify.sh`: parameterize Cosmos SQL or move watcher into Python; avoid repeated `nix-shell` loops.
4. `check-deployment.sh`: fix `stat -c` and `awk IGNORECASE` portability; add tests for JSON output.
5. `apply.sh`/`destroy.sh`: resolve script dir, require confirmation for apply/destroy, support `plan` mode.
6. `run-live-ollama-pipeline.py`: require `--i-understand-live-mutation` before stopping Function Apps or altering subscriptions.

## CI Pipeline Plan

### Files To Add

- `.github/workflows/ci.yml`
- `.github/workflows/live-smoke.yml`
- `.github/workflows/deploy-functions.yml`
- `scripts/ci-terraform-validate.sh`
- `scripts/ci-script-smoke.sh`

### Pull Request CI

Jobs:

1. Python lint
   - `ruff check .`
   - `ruff format --check .` if formatting is adopted.

2. Unit tests
   - `bash scripts/test.sh`

3. Simulator
   - `bash scripts/simulate-deployment.sh`

4. Terraform validation
   - For each module: `01-networking`, `02-cosmos`, `03-blob-storage`, `04-stateless`.
   - Commands per module:

```bash
terraform fmt -check
terraform init -backend=false
terraform validate
```

5. Script smoke
   - Help/dry-run commands only.
   - Add ShellCheck if available.

Suggested `ci.yml` shape:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: DeterminateSystems/nix-installer-action@v15
      - run: bash scripts/test.sh
      - run: bash scripts/simulate-deployment.sh

  terraform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
      - run: bash scripts/ci-terraform-validate.sh

  scripts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: bash scripts/ci-script-smoke.sh
```

### Live Smoke Workflow

Use `workflow_dispatch` and a protected GitHub environment.

Auth:

- GitHub OIDC to Azure.
- Required vars/secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `NODE_API_KEY` or future per-node key.

Commands:

```bash
bash infrastructure/smoke-test-poc.sh --node-id nixos
bash scripts/check-deployment.sh --json
```

This workflow must be manual or scheduled against a dev environment only.

### Deploy Functions Workflow

Manual only with inputs:

- resource group
- environment
- service list

Command:

```bash
bash scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision
```

### Terraform Apply Policy

Do not run `infrastructure/apply.sh` automatically from PR CI.

If a future apply workflow is added:

- manual `workflow_dispatch` only;
- protected environment;
- plan artifact first;
- typed confirmation or environment approval;
- no `destroy` workflow unless explicitly protected.

### Secrets Strategy

- PR CI must require no cloud secrets.
- Use OIDC for Azure in live workflows.
- Do not print rendered env files in CI.
- Keep `TF_VAR_opencode_api_key` and node credentials only in protected environments.
- Prefer per-node credentials and managed identity as audit fixes land.

## Small TUI/Dashboard Plan

### Goal

Provide a read-only POC operator dashboard for:

- deployment/resource readiness;
- VM reachability;
- Service Bus topic/subscription health;
- latest node state;
- observations;
- decisions;
- execution results;
- repair traces;
- service status timeline.

### Package Layout

Add:

- `dashboard/pyproject.toml`
- `dashboard/src/phoe_nix_dashboard/__init__.py`
- `dashboard/src/phoe_nix_dashboard/app.py`
- `dashboard/src/phoe_nix_dashboard/config.py`
- `dashboard/src/phoe_nix_dashboard/models.py`
- `dashboard/src/phoe_nix_dashboard/providers/deployment.py`
- `dashboard/src/phoe_nix_dashboard/providers/cosmos.py`
- `dashboard/src/phoe_nix_dashboard/providers/servicebus.py`
- `dashboard/tests/test_models.py`
- `scripts/dashboard.sh`

Use `Textual` for interactive TUI. If that is too heavy initially, start with `Rich` tables plus `--once` mode.

### Dashboard Modes

1. `--mock`
   - Uses checked-in fixture JSON.
   - Safe for CI.

2. `--once`
   - Prints one Rich snapshot and exits.
   - Useful for live smoke logs.

3. Interactive default
   - Polls every 5 to 10 seconds.
   - Uses read-only providers.

4. `--no-cloud`
   - Shows local/VM status only.

### Providers

`deployment.py`:

- Runs `bash scripts/check-deployment.sh --json --quick`.
- Parses resource/function/VM status into typed models.

`cosmos.py`:

- Read-only queries for:
  - `observations`
  - `node-state-current`
  - `decisions`
  - `execution-results`
  - `repair-traces`
  - `service-status`

`servicebus.py`:

- Optional first version: topology and counts only.
- Later: subscription active/dead-letter counts.

### UI Panels

- Deployment: resource group, Function Apps, Key Vault, Blob, Service Bus, Cosmos.
- Node: node ID, failed units, uptime, last observation.
- Pipeline: latest message/result counts by stage.
- Decisions: latest decisions with severity/confidence/action.
- Repair Trace: latest attempt, repo revision, rebuild status, push status.
- Timeline: service-status events ordered by timestamp.

### First Milestone

1. Add dashboard package with `--mock` and `--once`.
2. Reuse `check-deployment.sh --json --quick`.
3. Show deployment and VM readiness only.
4. Add CI test: `uv run phoe-nix-dashboard --once --mock`.

### Second Milestone

1. Add Cosmos read provider.
2. Show latest `service-status`, `decisions`, `execution-results`, and `repair-traces`.
3. Add correlation/incident filters once audit point 23 is implemented.

### Third Milestone

1. Add read-only command suggestions panel.
2. Show commands for phase 4 verify, phase 5 verify, smoke test, and `journalctl`.
3. Do not execute repair or deploy actions from the TUI.

### TUI CI

Commands:

```bash
cd dashboard
uv sync
uv run python -m unittest discover -s tests -p 'test*.py'
uv run phoe-nix-dashboard --once --mock
```

### TUI Safety Rules

- Read-only by default.
- No secrets printed.
- No deploy/apply/destroy/repair execution from UI.
- Any future action button must show the exact command and require explicit confirmation outside the TUI first.
