# Azure POC bring-up runbook

Use the Nix shell for every Azure-side step so the same environment variables and toolchain are loaded each time.

## 0. Prepare `../.env`

Create a repo-root `.env` file (it is already gitignored) with at least:

```dotenv
TF_VAR_opencode_api_key=sk-example
TF_VAR_node_api_key=replace-with-a-shared-node-api-key
# Optional overrides; these defaults are exported by `nix develop` when omitted.
PROJECT_NAME=project-healer
ENV=dev
```

Notes:
- `TF_VAR_opencode_api_key` is required by Terraform.
- `TF_VAR_node_api_key` is required for this POC. Terraform, `render-vm-env.sh`, and `smoke-test-poc.sh` now all fail fast when it is empty.
- `nix develop` sources `../.env` automatically and exports derived names such as `RG`, `COSMOS_ACCOUNT`, `TOKEN_APP`, `ROUTER_APP`, `ANALYSIS_APP`, and `DECISION_APP`.
- Explicit `SB_NAMESPACE` and `AZURE_TENANT_SUFFIX` overrides stay authoritative. When they are unset and Azure account context already exists, the shell/scripts derive the tenant suffix from `az account show --query tenantId -o tsv` using the same first-6-characters rule as Terraform.

## 1. Enter the Azure shell

```bash
cd infrastructure
nix develop
az login
az account set --subscription <subscription-id>
```

`nix develop` remains best-effort only: it does **not** fail if you have not logged into Azure yet. If you enter the shell before `az login`, either log in before running the helper scripts or set `SB_NAMESPACE` / `AZURE_TENANT_SUFFIX` explicitly.

Check the derived shell variables if you want:

```bash
printf '%s\n' "$PROJECT_NAME" "$ENV" "$RG" "$SB_NAMESPACE" "$COSMOS_ACCOUNT" "$TOKEN_APP" "$ANALYSIS_APP"
```

## 2. Apply infrastructure

```bash
./apply.sh
```

What this now automates for you:
- Token Function receives `NODE_API_KEY` from `TF_VAR_node_api_key`
- Analysis Function receives:
  - `OPENCODE_API_URL=https://opencode.ai/zen/go/v1/chat/completions`
  - `OPENCODE_MODEL=deepseek-v4-flash`

You no longer need a manual `az functionapp config appsettings set` step for the OpenCode URL/model.

## 3. Deploy function code

From repo root:

```bash
cd /home/freerat/projects/phoe-nix
./scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision
```

This deploy step is safe to rerun as part of an explicit deployment flow:
- rerun it after Python/function code changes
- rerun it after recreating Function Apps
- rerun it after Terraform changes that replace a Function App

Treat it as deployment-idempotent enough for the POC, but **do not** run it automatically on every `nix develop`; entering a dev shell should not mutate Azure.

## 4. Render the VM env files from Azure

Render the exact env blocks the VM needs (requires `NODE_API_KEY` or `TF_VAR_node_api_key` to be set first):

```bash
cd /home/freerat/projects/phoe-nix
bash infrastructure/render-vm-env.sh
```

Write ready-to-copy files into a temp directory:

```bash
bash infrastructure/render-vm-env.sh --write /tmp/phoe-nix-vm-env
```

That produces:
- `/tmp/phoe-nix-vm-env/log-service.env`
- `/tmp/phoe-nix-vm-env/local-agent.env`

If you want to keep Cosmos disabled on the VM while proving the Azure message path first:

```bash
bash infrastructure/render-vm-env.sh --cosmos off --write /tmp/phoe-nix-vm-env
```

## 5. Install the rendered env files on the VM

Copy the generated files into the VM's persistent `/etc/phoe-nix/*.env` files:

```bash
scp -P 2222 /tmp/phoe-nix-vm-env/log-service.env user@localhost:/tmp/log-service.env
scp -P 2222 /tmp/phoe-nix-vm-env/local-agent.env user@localhost:/tmp/local-agent.env

ssh -p 2222 user@localhost 'sudo mv /tmp/log-service.env /etc/phoe-nix/log-service.env && sudo mv /tmp/local-agent.env /etc/phoe-nix/local-agent.env && sudo systemctl restart log_service local_agent && sudo systemctl status log_service local_agent --no-pager'
```

## 6. Run live Azure smoke checks

Run the scripted smoke checks from the repo root:

```bash
cd /home/freerat/projects/phoe-nix
bash infrastructure/smoke-test-poc.sh --node-id nixos
```

The smoke script checks (and it also requires `NODE_API_KEY` / `TF_VAR_node_api_key`):
- function apps exist
- deployed functions are visible in Azure
- Service Bus topics exist
- Cosmos account exists
- analysis app OpenCode URL/model settings match the expected values
- token service returns a SAS payload for an authenticated node request

## 7. Optional manual spot checks

Watch VM services:

```bash
ssh -p 2222 user@localhost 'journalctl -u log_service -u local_agent -f'
```

Confirm host Ollama reachability from the guest:

```bash
ssh -p 2222 user@localhost 'curl http://10.0.2.2:11434/api/tags'
```

## 8. Current manual steps that still remain

The Azure-side automation is improved, but these are still manual:
- `az login`
- `az account set --subscription ...`
- `./apply.sh`
- `./scripts/deploy-functions.sh ...`
- copying the rendered env files into the VM
- restarting the VM services

## 9. POC notes

- The analysis client now speaks to the OpenAI-compatible OpenCode Go chat-completions endpoint and uses `deepseek-v4-flash` by default.
- The current Service Bus topology is still single-subscription for `local-agent`, which is fine for a single disposable VM POC but not for a real multi-node rollout.
- `servicebus_sku` is now intentionally limited to `Standard` or `Premium` because the design depends on topics.
