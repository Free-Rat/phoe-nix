# Azure POC bring-up runbook

Use the Nix shell for Azure-side work so the same toolchain and repo-root `.env` are loaded each time.

## Prerequisites

Create a repo-root `.env` file (it is already gitignored) with at least:

```dotenv
TF_VAR_opencode_api_key=sk-example
TF_VAR_node_api_key=replace-with-a-shared-node-api-key
# Optional overrides; the shell provides these defaults when omitted.
PROJECT_NAME=project-healer
ENV=dev
```

Notes:
- `TF_VAR_opencode_api_key` is required by Terraform.
- `TF_VAR_node_api_key` is required for the POC. Terraform, `render-vm-env.sh`, and `smoke-test-poc.sh` all fail fast when it is empty.
- `nix develop` sources `../.env` automatically and exports derived names such as `RG`, `COSMOS_ACCOUNT`, `TOKEN_APP`, `ROUTER_APP`, `ANALYSIS_APP`, and `DECISION_APP`.
- If `SB_NAMESPACE` is unset, the shell and helper scripts derive it from `az account show --query tenantId -o tsv` using the same first-6-characters rule as Terraform. `AZURE_TENANT_SUFFIX` can override that derivation.

Before running the Azure helpers, log in and select the target subscription:

```bash
az login
az account set --subscription <subscription-id>
```

You can open the shell before `az login`, but the helper scripts below need Azure authentication.

## 1. Enter the infrastructure shell

```bash
cd infrastructure
nix develop
```

## 2. Apply Terraform

```bash
./apply.sh
```

This applies `01-networking` → `02-cosmos` → `03-blob-storage` → `04-stateless`.
It provisions the resource group, Cosmos DB, Blob Storage, Service Bus topics/subscriptions, Key Vault, App Insights, and the four Function Apps.

## 3. Deploy the Function App code

From the infrastructure shell:

```bash
bash ../scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision
```

This stages zip artifacts under `.build/functions/` and deploys the current Python code for token/router/analysis/decision.

## 4. Render the VM env files

```bash
bash ./render-vm-env.sh --write /tmp/phoe-nix-vm-env
```

Use `--cosmos off` if you want the VM to stay off Cosmos while proving the Azure message path first.

The script requires `NODE_API_KEY` or `TF_VAR_node_api_key`.
If `SB_NAMESPACE` is unset, it derives the namespace name from the logged-in Azure tenant.

## 5. Install the env files on the VM

```bash
scp -P 2222 /tmp/phoe-nix-vm-env/log-service.env user@localhost:/tmp/log-service.env
scp -P 2222 /tmp/phoe-nix-vm-env/local-agent.env user@localhost:/tmp/local-agent.env

ssh -p 2222 user@localhost '
  sudo install -d -m 755 /etc/phoe-nix &&
  sudo install -m 600 /tmp/log-service.env /etc/phoe-nix/log-service.env &&
  sudo install -m 600 /tmp/local-agent.env /etc/phoe-nix/local-agent.env &&
  sudo systemctl restart log_service local_agent &&
  sudo systemctl status log_service local_agent --no-pager
'
```

These files contain secrets; keep them root-owned on the VM.

## 6. Run live smoke checks

```bash
bash ./smoke-test-poc.sh --node-id nixos
```

This checks Function Apps, deployed functions, Service Bus topics/subscriptions, Cosmos, the analysis app OpenCode settings, and a token-service request.

## Optional spot checks

Keep an eye on the Ollama model setting: `local_agent` defaults to `OLLAMA_MODEL=gemma3:4b`, while `infrastructure/render-vm-env.sh` still writes `OLLAMA_MODEL=gemma4:e4b`. If model selection looks wrong on the VM, check which side is supplying the override.

Watch the VM services:

```bash
ssh -p 2222 user@localhost 'journalctl -u log_service -u local_agent -f'
```

Confirm Ollama reachability from the guest:

```bash
ssh -p 2222 user@localhost 'curl http://10.0.2.2:11434/api/tags'
```

## Safety notes

- `TF_VAR_opencode_api_key` and `TF_VAR_node_api_key` are sensitive; do not commit them.
- `./apply.sh` is for bring-up; only run `./destroy.sh` when you explicitly want to tear the stack down.
- Azure login and subscription selection are required before rendering VM envs or running smoke checks unless you provide explicit `SB_NAMESPACE` and `AZURE_TENANT_SUFFIX` values.
