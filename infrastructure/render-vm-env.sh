#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_DIR/.env"
  set +a
fi

export PROJECT_NAME="${PROJECT_NAME:-project-healer}"
export ENV="${ENV:-dev}"
export RG="${RG:-rg-${PROJECT_NAME}-${ENV}}"
export SB_NAMESPACE="${SB_NAMESPACE:-}"
export COSMOS_ACCOUNT="${COSMOS_ACCOUNT:-cosmos-${PROJECT_NAME}-${ENV}}"
export TOKEN_APP="${TOKEN_APP:-func-${PROJECT_NAME}-${ENV}-token}"
export TOKEN_FUNCTION_NAME="${TOKEN_FUNCTION_NAME:-token_service}"
export NODE_API_KEY="${NODE_API_KEY:-${TF_VAR_node_api_key:-}}"

WRITE_DIR=""
COSMOS_MODE="on"

usage() {
  cat <<'EOF'
Usage: bash infrastructure/render-vm-env.sh [--write DIR] [--cosmos on|off]

Fetches the Azure values needed by the VM POC and renders:
- log-service.env
- local-agent.env

Environment:
  NODE_API_KEY / TF_VAR_node_api_key  Required. Used for log-service.env.
  SB_NAMESPACE                        Optional explicit override.
  AZURE_TENANT_SUFFIX                 Optional explicit override used when SB_NAMESPACE is unset.
                                      Otherwise the script derives the suffix from
                                      `az account show --query tenantId -o tsv`.

Options:
  --write DIR      Write both env files into DIR instead of only printing them.
  --cosmos MODE    Render local-agent.env with Cosmos on or off. Default: on.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write)
      WRITE_DIR="$2"
      shift 2
      ;;
    --cosmos)
      COSMOS_MODE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$COSMOS_MODE" != "on" && "$COSMOS_MODE" != "off" ]]; then
  echo "--cosmos must be 'on' or 'off'" >&2
  exit 1
fi

if [[ -z "$NODE_API_KEY" ]]; then
  echo "NODE_API_KEY or TF_VAR_node_api_key must be set before rendering VM env files" >&2
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "azure-cli (az) is required" >&2
  exit 1
fi

resolve_tenant_suffix() {
  if [[ -n "${AZURE_TENANT_SUFFIX:-}" ]]; then
    printf '%s\n' "$AZURE_TENANT_SUFFIX"
    return 0
  fi

  local tenant_id
  tenant_id="$(az account show --query tenantId -o tsv 2>/dev/null)" || {
    echo "Unable to derive AZURE_TENANT_SUFFIX from Azure CLI; set SB_NAMESPACE or AZURE_TENANT_SUFFIX, or run az login" >&2
    return 1
  }

  if [[ -z "$tenant_id" ]]; then
    echo "Azure CLI returned an empty tenantId; set SB_NAMESPACE or AZURE_TENANT_SUFFIX explicitly" >&2
    return 1
  fi

  printf '%s\n' "${tenant_id//-/}" | cut -c1-6
}

if [[ -z "$SB_NAMESPACE" ]]; then
  export AZURE_TENANT_SUFFIX="$(resolve_tenant_suffix)"
  export SB_NAMESPACE="sb-${PROJECT_NAME}-${ENV}-${AZURE_TENANT_SUFFIX}"
fi

SERVICEBUS_CONNECTION="$(az servicebus namespace authorization-rule keys list \
  --resource-group "$RG" \
  --namespace-name "$SB_NAMESPACE" \
  --name SharedAccessPolicy \
  --query primaryConnectionString -o tsv)"

TOKEN_FUNCTION_KEY="$(az functionapp function keys list \
  --resource-group "$RG" \
  --name "$TOKEN_APP" \
  --function-name "$TOKEN_FUNCTION_NAME" \
  --query default -o tsv)"
TOKEN_SERVICE_URL="https://${TOKEN_APP}.azurewebsites.net/api/token?code=${TOKEN_FUNCTION_KEY}"

COSMOSDB_ENDPOINT=""
COSMOSDB_KEY=""
COSMOSDB_ENABLED="0"
if [[ "$COSMOS_MODE" == "on" ]]; then
  COSMOSDB_ENDPOINT="$(az cosmosdb show \
    --resource-group "$RG" \
    --name "$COSMOS_ACCOUNT" \
    --query documentEndpoint -o tsv)"
  COSMOSDB_KEY="$(az cosmosdb keys list \
    --resource-group "$RG" \
    --name "$COSMOS_ACCOUNT" \
    --type keys \
    --query primaryMasterKey -o tsv)"
  COSMOSDB_ENABLED="1"
fi

LOG_SERVICE_ENV="TOKEN_SERVICE_URL=${TOKEN_SERVICE_URL}
NODE_API_KEY=${NODE_API_KEY}"

LOCAL_AGENT_GIT_SSH_COMMAND="ssh -i /var/lib/phoe-nix-secrets/local-agent-repo-key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/phoe-nix/github-known_hosts"
LOCAL_AGENT_REBUILD_COMMAND='NIX_CONFIG="experimental-features = nix-command flakes" nixos-rebuild test --flake /var/lib/phoe-nix-config-repo#simulation --impure'

LOCAL_AGENT_ENV="SERVICEBUS_ENABLED=1
SERVICEBUS_CONNECTION=${SERVICEBUS_CONNECTION}
SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME=analysis-input
SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME=final-decisions
SERVICEBUS_SUBSCRIPTION_LOCAL_AGENT=local-agent

COSMOSDB_ENABLED=${COSMOSDB_ENABLED}
COSMOSDB_ENDPOINT=${COSMOSDB_ENDPOINT}
COSMOSDB_KEY=${COSMOSDB_KEY}
COSMOSDB_DATABASE_NAME=project-healer

CONFIG_REPO_URL=git@github.com:Free-Rat/phoe-nix-config.git
CONFIG_REPO_BRANCH=main
CONFIG_REPO_PATH=/var/lib/phoe-nix-config-repo
GIT_SSH_COMMAND='${LOCAL_AGENT_GIT_SSH_COMMAND}'
REBUILD_TEST_COMMAND='${LOCAL_AGENT_REBUILD_COMMAND}'
REBUILD_SWITCH_COMMAND='${LOCAL_AGENT_REBUILD_COMMAND}'

OLLAMA_BASE_URL=http://10.0.2.2:11434
OLLAMA_MODEL=gpt-oss:20b
COOLDOWN_SECONDS=0
MAX_REMEDIATIONS_PER_HOUR=100

OBSERVE_INTERVAL_SECONDS=10
REPO_REFRESH_SECONDS=300"

if [[ -n "$WRITE_DIR" ]]; then
  mkdir -p "$WRITE_DIR"
  printf '%s
' "$LOG_SERVICE_ENV" >"$WRITE_DIR/log-service.env"
  printf '%s
' "$LOCAL_AGENT_ENV" >"$WRITE_DIR/local-agent.env"
  echo "Wrote $WRITE_DIR/log-service.env"
  echo "Wrote $WRITE_DIR/local-agent.env"
fi

echo "# /etc/phoe-nix/log-service.env"
printf '%s
' "$LOG_SERVICE_ENV"
echo
echo "# /etc/phoe-nix/local-agent.env"
printf '%s
' "$LOCAL_AGENT_ENV"
