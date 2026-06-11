# Infrastructure overview

This directory contains the Terraform-managed Azure infrastructure for the current cloud pipeline. It provisions the cloud side only; `local_agent` runs on the node/VM and is not deployed by these modules.

For the broader planned VM repair loop, see `../proof-of-concept-direction.md`.

## Current flow

1. `token_service` issues a short-lived, path-scoped SAS URL.
2. The node uploads logs to Blob Storage.
3. Blob creation triggers the router function.
4. The router publishes normalized events to Service Bus topic `analysis-input`.
5. `analysis_agent` consumes `analysis-input`, calls OpenCode, and publishes to `analysis-results`.
6. `decision_agent` consumes `analysis-results` and publishes remediation intent to `final-decisions`.
7. `local_agent` consumes `final-decisions` on the node/VM.

## Terraform layout

The infrastructure is split into four module directories, each with its own local backend state (`backend "local"`). Apply order matters because later modules read resources created by earlier ones.

| Module | What it creates |
|---|---|
| `01-networking` | Resource group only |
| `02-cosmos` | Cosmos DB account, SQL database `project-healer`, and containers: `observations`, `node-state-current`, `decisions`, `execution-results`, `config-snapshots`, `repair-traces`, `service-status` |
| `03-blob-storage` | Logs storage account, private `logs` container, and a 30-day cleanup policy |
| `04-stateless` | Service Bus namespace, 3 topics, 3 subscriptions, Shared Access authorization rule, Key Vault (`OpenCodeApiKey`, `ServiceBusConnection`, `StorageAccountKey`, `LogsStorageConnection`), Application Insights, user-assigned identity, Linux service plan, function storage account, 4 Linux Function Apps (`token`, `router`, `analysis`, `decision`), and 6 RBAC role assignments |

## Prerequisites

- Azure CLI authenticated and subscribed to the target subscription.
- Terraform available locally, or use `nix develop` from `infrastructure/`.
- If you use the Nix shell, it sources the repo-root `.env` automatically.
- Repo-root `.env` populated with at least:
  - `TF_VAR_opencode_api_key`
  - `TF_VAR_node_api_key`
- Keep secrets out of source control; Terraform stores the OpenCode API key and other sensitive values in Azure resources, not in this file.

Notes:
- `node_api_key` is required by `04-stateless` and the token service.
- Module defaults do not all point to the same Azure region: `01-networking`, `02-cosmos`, and `03-blob-storage` default to `polandcentral`, while `04-stateless` defaults to `swedencentral`. Override `location` if you want a single region.

## Apply flow

From `infrastructure/`:

```bash
nix develop
az login
az account set --subscription <subscription-id>
./apply.sh
```

`apply.sh` runs `terraform init` and `terraform apply -auto-approve` in this order:

`01-networking` → `02-cosmos` → `03-blob-storage` → `04-stateless`

That order is required because:
- `02-cosmos`, `03-blob-storage`, and `04-stateless` read the resource group from `01-networking`
- `04-stateless` also reads Cosmos DB and Blob Storage created by earlier modules

## Destroy flow

`destroy.sh` runs `terraform init` and `terraform destroy -auto-approve` in reverse order:

`04-stateless` → `03-blob-storage` → `02-cosmos` → `01-networking`

Use the module-specific destroy commands when you only want part of the stack removed:

- `cd 04-stateless && terraform destroy` removes the function apps, Service Bus, Key Vault, Application Insights, identity, service plan, and function storage account.
- `cd 03-blob-storage && terraform destroy` removes the logs storage account and retained log data.
- `cd 02-cosmos && terraform destroy` removes Cosmos DB data, including observations, decisions, execution results, snapshots, repair traces, and service status.
- `./destroy.sh` removes everything in safe reverse order.

## Safety notes

- `02-cosmos` and `03-blob-storage` are stateful; destroy them only when you are willing to lose stored data.
- `04-stateless` still contains secrets and secret references. Keep them in Azure Key Vault and do not hardcode values in Terraform or docs.
- The apply/destroy scripts use `-auto-approve`; run them intentionally.
- Do not apply modules out of order, because later modules depend on names and data sources created by earlier ones.
