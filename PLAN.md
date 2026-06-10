# Phoe-nix Plan

> Last updated: 2026-06-08
> This replaces the old plan now that the Azure bring-up POC is working.

## What Was Fixed This Session

### Root cause of the token 500
`ModuleNotFoundError: No module named 'pydantic'` in `token_service/config.py`. The zip-deploy path was not running `pip install` on the deployed package. Confirmed via Application Insights.

### Fixes applied
1. **`scripts/deploy-functions.sh`** — adds root `host.json`, strips local-only `requirements.txt` entries (`../schemas`, `-e .`), and uses `--build-remote true` so Oryx installs dependencies on deploy.
2. **`infrastructure/04-stateless/main.tf`** — cleaned up live Azure issues: Cosmos serverless config, blocked-region workaround, unique Key Vault / Service Bus names, Key Vault access policy for the operator, and removed stale `SCM_DO_BUILD_DURING_DEPLOYMENT` / `ENABLE_ORYX_BUILD` app settings (remote build now driven by the CLI).
3. **`infrastructure/render-vm-env.sh`**, **`infrastructure/smoke-test-poc.sh`**, and **`infrastructure/flake.nix`** — helper paths now derive the Service Bus tenant suffix from `az account show --query tenantId -o tsv` when `SB_NAMESPACE` and `AZURE_TENANT_SUFFIX` are unset, matching Terraform. `render-vm-env.sh` also fails fast if `NODE_API_KEY` / `TF_VAR_node_api_key` is missing.
4. **`infrastructure/commands.md`** — expanded into a full Azure POC runbook.
5. **`scripts/deploy-functions.sh`** and **`log_router/src/log_router/normalizer.py`** — function app `host.json` now includes the Azure Functions extension bundle required by non-HTTP triggers, and `log_router` now accepts both microsecond journal timestamps and ISO-8601 timestamps seen in live uploads.
6. **`analysis_agent/src/analysis_agent/ai_client.py`** — `call_opencode_api` now sends `User-Agent: phoe-nix-analysis-agent/0.1` (OpenCode blocks `urllib`'s default UA with 403); `parse_analysis_response` normalizes nullable AI fields (`root_cause`, `suggested_action`, `remediation_hint`) and string confidence labels (`"high"`→0.9, etc.).
7. **`decision_agent/src/decision_agent/decision_engine.py`** — `normalize_suggested_action` maps AI-varied action phrases (`"None required. Continue monitoring."`, `"no action required"`, `"none"`, etc.) to canonical actions; `build_decision` uses normalized action.
8. **`token_service/src/token_service/auth.py`**, **`token_service/src/token_service/config.py`** — `node_api_key` is now required (`str`, not `str | None`); `authenticate_node_request` always validates the key; Terraform `variable "node_api_key"` has validation requiring non-empty.

### Live verification
- `bash infrastructure/smoke-test-poc.sh --node-id nixos` — **all checks PASS**
- Token service: `HTTP/1.1 200 OK`, returns SAS URL
- Token call from inside the VM: **HTTP 200**, response contains `sas_url`
- VM services (`log_service`, `local_agent`): **active**
- Recent VM log uploads were observed in Blob Storage under `logs/nixos/...`
- `analysis_agent` → `decision_agent` pipeline is live and producing decisions stored in Cosmos DB
- OpenCode API key rotated and `analysis_agent` User-Agent fix deployed; analysis calls succeed (~5–10s latency)
- `decision_agent` action normalization deployed; all recent executions succeed with zero exceptions

---

## Architecture That Now Exists

```
┌─────────────── VM (nixos) ───────────────┐
│                                            │
│  log_service ──► token_service (Azure)     │
│                    │                       │
│                    ▼                       │
│              Blob Storage                  │
│                    │                       │
│                    ▼                       │
│              log_router (Azure Function)   │
│                    │                       │
│                    ▼                       │
│         Service Bus: analysis-input        │
│                    │                       │
│                    ▼                       │
│         analysis_agent (Azure Function)    │
│              uses OpenCode Go API          │
│                    │                       │
│                    ▼                       │
│         Service Bus: analysis-results      │
│                    │                       │
│                    ▼                       │
│         decision_agent (Azure Function)    │
│              stores in Cosmos DB           │
│                    │                       │
│                    ▼                       │
│         Service Bus: final-decisions       │
│                    │                       │
│                    ▼                       │
│         local_agent (VM daemon) ◄──────────┘
│              │
│              ▼
│         Ollama (host) → nixos-rebuild
└────────────────────────────────────────────┘
```

### What is verified working in Azure
- [x] All four Function Apps exist and are deployed
- [x] Three Service Bus topics exist with subscriptions
- [x] Cosmos DB account exists
- [x] Token service returns SAS URLs for authenticated nodes
- [x] Analysis app has correct `OPENCODE_API_URL` and `OPENCODE_MODEL`
- [x] `log_router` processes VM journal blobs and publishes to `analysis-input`
- [x] `analysis_agent` receives messages from `analysis-input`, calls OpenCode, and publishes to `analysis-results`
- [x] `decision_agent` receives analysis results, normalizes AI-varied actions, persists decisions to Cosmos DB

### What is verified working on the VM
- [x] `log_service.service` is active
- [x] `local_agent.service` is active
- [x] VM can reach Azure token service and get SAS payloads
- [x] VM env values are loaded via persistent `EnvironmentFile` wiring in `../phoe-nix-config/phoe-services.nix` (`/etc/phoe-nix/*.env`)

---

## Remaining Work

### Phase 1 — Repo cleanup (completed locally)

These small repo-local fixes are now in place so the next work can build on a stable baseline.

- [x] **1a. Tenant suffix portability**
  - `infrastructure/render-vm-env.sh`, `infrastructure/smoke-test-poc.sh`, and `infrastructure/flake.nix` now honor explicit `SB_NAMESPACE` / `AZURE_TENANT_SUFFIX` overrides first.
  - When those overrides are unset, the helper paths derive the suffix from `az account show --query tenantId -o tsv`, strip `-`, and take the first 6 characters to match `infrastructure/04-stateless/locals.tf`.

- [x] **1b. `render-vm-env.sh` early failure on missing `NODE_API_KEY`**
  - `render-vm-env.sh` now exits with a clear error before rendering anything if `NODE_API_KEY` / `TF_VAR_node_api_key` is empty.

- [x] **1c. Persistent VM env wiring**
  - No repo-local code change was needed here: `../phoe-nix-config/phoe-services.nix` already uses persistent `EnvironmentFile` entries for `/etc/phoe-nix/log-service.env` and `/etc/phoe-nix/local-agent.env`.
  - The runbook step remains “copy env files into `/etc/phoe-nix/` and restart services”, and that wiring survives reboots.

### Phase 2 — Exercise the log upload path

Goal: a real log entry flows from the VM all the way into Blob Storage.

- [x] **2a. Trigger a real upload from `log_service` in the VM**
  - The VM's `log_service` now has a real `TOKEN_SERVICE_URL` and `NODE_API_KEY`.
  - Direct token call from inside the VM now returns `HTTP 200` with a SAS payload.
  - Recent SSH activity on the VM caused the `logs` container blob count to increase, confirming live uploads under `logs/nixos/...`.

- [x] **2b. Verify `log_router` triggers on new blobs**
  - Fixed: `log_router/normalizer.py` now accepts both microsecond and ISO-8601 journal timestamps.
  - Fixed: `scripts/deploy-functions.sh` now emits a root `host.json` with the Azure Functions extension bundle.
  - Live confirmation: `log_router` successfully processes VM-generated blobs and publishes normalized logs to `analysis-input`.

- [ ] **3a. Publish a test observation to `analysis-input`**
  - Use `az servicebus topic subscription list` to confirm the `analysis-input` topic has the `analysis-agent` subscription.
  - Send a test message manually: `az servicebus message send ...`
  - Check Application Insights for `analysis_agent` traces showing message receipt.
### Phase 3 — Exercise the full cloud pipeline

Goal: an observation flows from Service Bus through analysis to a decision.

- [ ] **3a. Publish a test observation to `analysis-input`**
  - Use `az servicebus topic subscription list` to confirm the `analysis-input` topic has the `analysis-agent` subscription.
  - Send a test message manually: `az servicebus message send ...`
  - Check Application Insights for `analysis_agent` traces showing message receipt.

- [x] **3b. Verify analysis → decision handoff**
  - Fixed: `analysis_agent/ai_client.py` now sets a non-default `User-Agent` header (`phoe-nix-analysis-agent/0.1`) because OpenCode blocks the Python `urllib` default UA with 403.
  - Fixed: `analysis_agent/ai_client.py` `parse_analysis_response` now normalizes nullable AI fields (`root_cause`, `suggested_action`, `remediation_hint`) and string confidence values (e.g., `"high"` → `0.9`).
  - Fixed: `decision_agent/decision_engine.py` `normalize_suggested_action` now maps AI-varied action strings (`"None required. Continue monitoring."`, `"no action required"`, `"none"`, `"No action required."`, etc.) to canonical `no_action`.
  - Live confirmation: App Insights after 2026-06-08T15:01:41Z shows analysis success_count=12/0 failures and decision success_count=14/0 failures, with zero exceptions.
  - Cosmos DB `decisions` container contains documents with `node_id: nixos` and `action: no_action`, confirming the full pipeline VM → blob → router → analysis → decision → Cosmos.

- [x] **3c. If analysis doesn't trigger**
  - Resolved: OpenCode API key was rotated; key vault secret updated via Terraform apply.
  - Resolved: OpenCode API (`opencode.ai/zen/go/v1/chat/completions`) returns 403 for Python `urllib`'s default User-Agent but 200 for custom UA; fixed in `ai_client.py`.

### Phase 4 — Exercise the VM-side receive path

Goal: `local_agent` receives a real decision from Service Bus.

- [x] **4a. Publish a test decision to `final-decisions`**
  - `scripts/publish-test-decision.sh` now wraps `az servicebus topic message send` with a body that matches `schemas.Decision` exactly (validated before send).
  - The script accepts `--action`, `--severity`, `--node-id`, `--body-file`, and other Decision fields as flags.
  - `scripts/phase4-verify.sh` is the orchestrator: it checks the `local-agent` subscription exists, optionally pokes the VM env via ssh, then publishes a `no_action` Decision and prints the `journalctl` command to run.

- [ ] **4b. Watch `local_agent` receive and process it**
  - Run `bash scripts/phase4-verify.sh` from a host with `az` credentials.
  - On the VM, run `ssh -p 2222 user@localhost 'journalctl -u local_agent -f | grep -E "decision|repair|receive"'`.
  - Verify the decision is parsed, the receive loop works, and the message is completed.

- [ ] **4c. If `local_agent` receives but the repair loop is not ready**
  - This is expected — Phase 5 covers the repair loop.
  - For Phase 4, just confirming Service Bus receive works from the VM is the goal.

#### Phase 4 helpers added in this session

- `scripts/publish-test-decision.sh` — one-shot Decision publisher. Derives `SB_NAMESPACE` from `az account show` the same way `infrastructure/render-vm-env.sh` does. Validates the JSON body against the `Decision` schema before sending.
- `scripts/phase4-verify.sh` — pre-flight + publish + journal hint. Use this for the first live verification.
- `local_agent/src/local_agent/config.py` — added `decision_poll_base_seconds` (default 0.05) and `decision_poll_max_seconds` (default 1.0) so the receive-loop backoff is tunable from the VM env.
- `local_agent/src/local_agent/runtime.py:decision_worker` — exponential backoff on persistent `receive_failed` errors. First error stays at base, subsequent consecutive errors double up to the cap. Reset to base on the next successful receive. Avoids burning CPU when the Service Bus namespace is unreachable.

### Phase 5 — Real repair loop from inside the VM

Goal: `local_agent` receives a real decision, runs the repair planner with host Ollama, and attempts `nixos-rebuild test`.

- [ ] **5a. Verify Ollama reachability under load**
  - `local_agent` already has `OLLAMA_BASE_URL=http://10.0.2.2:11434`.
  - Run a manual test: generate a config fix from inside the VM using the Ollama client.

- [ ] **5b. Run a simulated repair with real Ollama**
  - Either use the simulator path with `--ollama-base-url` pointed at the host, or
  - Trigger a real decision via Service Bus and let the daemon process it.
  - Verify the repair planner produces a candidate `configuration.nix`.

- [ ] **5c. Run `nixos-rebuild test` inside the VM**
  - Confirm the command works (it should, since the VM is NixOS).
  - Handle the case where `test` fails and the retry loop kicks in.

- [ ] **5d. Confirm config push to shared repo**
  - After a successful repair, verify the commit appears in `phoe-nix-config`.
  - Handle merge conflicts (the current code pulls and retries; verify this path).

### Phase 6 — TUI

Goal: a minimal dashboard showing the pipeline state.

- [ ] **6a. Choose approach**
  - Options: Streamlit (matches requirements), Textual (TUI), or a simple web dashboard.
  - Streamlit is the requirement from `idea.md` ("minimal frontend e.g. Streamlit").

- [ ] **6b. Show pipeline stages**
  - Nodes and latest state
  - Latest observations
  - Analysis results
  - Decisions
  - Execution results / repair traces

- [ ] **6c. Pull data from Cosmos DB**
  - Read from the containers already populated by the pipeline.

### Phase 7 — CI/CD (required by project spec)

Goal: GitHub Actions pipeline that validates and deploys.

- [ ] **7a. Add a basic CI workflow**
  - Lint and test all Python services.
  - Validate Terraform (`terraform validate` per module).
  - No workflow is checked in yet; add this from scratch.

- [ ] **7b. Add CD for function deploys**
  - On push to main, deploy functions to Azure.
  - Use the same `scripts/deploy-functions.sh` logic in a workflow.

---

## Known Issues (not blocking, but should be fixed)

1. **Service Bus topology is single-subscription** — fine for one-VM POC, needs redesign for multi-node.
2. **`local_agent` flake packaging not used by VM** — the VM uses direct source wrappers; the flake packaging path exists but isn't the active path.
3. **Automated tests exist, but CI does not** — `bash scripts/test.sh` covers the Python services and simulator locally, but there is still no checked-in GitHub Actions workflow.
4. **`log_service` Azure integration is implemented, but live coverage is still manual** — the token request and Blob upload path exists in code; the remaining gap is repeatable live-Azure validation.
5. **AI response normalization is defensive, not exhaustive** — `parse_analysis_response` and `normalize_suggested_action` handle observed LLM output variations (nullable fields, string confidence, varied action phrases), but new LLM output patterns may still cause occasional `OpenCodeError` or `ValueError` until more normalization is added.

## Fixed Decisions (carried forward)

- Proof of concept only; nodes are disposable VMs
- `local_agent` is a single long-running process with internal async workers
- Cloud authentication uses connection strings (Service Bus, Cosmos)
- Cloud-side AI uses OpenCode Go API (`deepseek-v4-flash`)
- Node-side repair uses Ollama on the VM host (`gemma3:4b`)
- Shared config repo: `https://github.com/Free-Rat/phoe-nix-config`
- Repair loop: `nixos-rebuild test` → retry with LLM → `nixos-rebuild switch`
- Config changes are committed and pushed back to the shared repo
- `NODE_API_KEY` is required for the POC; Terraform, render-vm-env, and smoke-test all fail fast if it is empty
- OpenCode API requires a custom `User-Agent` header; Python `urllib`'s default is blocked with 403


## Commands Reference

### Azure bring-up
```bash
cd infrastructure && nix develop
az login && az account set --subscription <id>
./apply.sh
cd .. && ./scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision
bash infrastructure/smoke-test-poc.sh --node-id nixos
```

### VM env rendering and deployment
```bash
bash infrastructure/render-vm-env.sh --write /tmp/phoe-nix-vm-env
scp -P 2222 /tmp/phoe-nix-vm-env/*.env user@localhost:/tmp/
# Then move them into /etc/phoe-nix/ and restart the services.
```

### VM service check
```bash
ssh -p 2222 user@localhost 'systemctl status log_service local_agent --no-pager'
ssh -p 2222 user@localhost 'journalctl -u log_service -u local_agent -f'
```

### Test commands
```bash
# Python tests
bash scripts/test.sh

# Simulator (local, no Azure needed)
bash scripts/simulate-deployment.sh
```

## Non-Goals For Now
- Production safety guarantees
- Public exposure of Ollama to Azure
- Strict bounded config-edit schemas
- Human approval workflows
- Multi-node coordination
- Kubernetes (serverless Functions are used instead)
