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
export ROUTER_APP="${ROUTER_APP:-func-${PROJECT_NAME}-${ENV}-router}"
export ANALYSIS_APP="${ANALYSIS_APP:-func-${PROJECT_NAME}-${ENV}-analysis}"
export DECISION_APP="${DECISION_APP:-func-${PROJECT_NAME}-${ENV}-decision}"
export TOKEN_FUNCTION_NAME="${TOKEN_FUNCTION_NAME:-token_service}"
export NODE_API_KEY="${NODE_API_KEY:-${TF_VAR_node_api_key:-}}"
export OPENCODE_API_URL="${OPENCODE_API_URL:-https://opencode.ai/zen/go/v1/chat/completions}"
export OPENCODE_MODEL="${OPENCODE_MODEL:-deepseek-v4-flash}"

NODE_ID="${NODE_ID:-nixos}"

usage() {
  cat <<'EOF'
Usage: bash infrastructure/smoke-test-poc.sh [--node-id NODE_ID]

Runs live Azure smoke checks for the POC path:
- function apps exist
- deployed functions are visible
- Service Bus topics exist
- Cosmos account exists
- analysis function OpenCode settings match the expected endpoint/model
- token function responds to an authenticated request

Environment:
  NODE_API_KEY / TF_VAR_node_api_key  Required for the token-service smoke test.
  SB_NAMESPACE                        Optional explicit override.
  AZURE_TENANT_SUFFIX                 Optional explicit override used when SB_NAMESPACE is unset.
                                      Otherwise the script derives the suffix from
                                      `az account show --query tenantId -o tsv`.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node-id)
      NODE_ID="$2"
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

if ! command -v az >/dev/null 2>&1; then
  echo "azure-cli (az) is required" >&2
  exit 1
fi

if [[ -z "$NODE_API_KEY" ]]; then
  echo "NODE_API_KEY or TF_VAR_node_api_key must be set for the token-service smoke test" >&2
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

assert_equals() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "FAILED: $label" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
  echo "PASS: $label"
}

assert_name_suffix() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [[ "$actual" != "$expected" && "$actual" != */"$expected" ]]; then
    echo "FAILED: $label" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
  echo "PASS: $label"
}

for app in "$TOKEN_APP" "$ROUTER_APP" "$ANALYSIS_APP" "$DECISION_APP"; do
  actual_name="$(az functionapp show --resource-group "$RG" --name "$app" --query name -o tsv)"
  assert_equals "$app" "$actual_name" "function app exists: $app"
done

assert_name_suffix "token_service" "$(az functionapp function show --resource-group "$RG" --name "$TOKEN_APP" --function-name token_service --query name -o tsv)" "token function deployed"
assert_name_suffix "log_router" "$(az functionapp function show --resource-group "$RG" --name "$ROUTER_APP" --function-name log_router --query name -o tsv)" "router function deployed"
assert_name_suffix "analysis_agent" "$(az functionapp function show --resource-group "$RG" --name "$ANALYSIS_APP" --function-name analysis_agent --query name -o tsv)" "analysis function deployed"
assert_name_suffix "decision_agent" "$(az functionapp function show --resource-group "$RG" --name "$DECISION_APP" --function-name decision_agent --query name -o tsv)" "decision function deployed"

for topic in analysis-input analysis-results final-decisions; do
  actual_topic="$(az servicebus topic show --resource-group "$RG" --namespace-name "$SB_NAMESPACE" --name "$topic" --query name -o tsv)"
  assert_equals "$topic" "$actual_topic" "service bus topic exists: $topic"
done

assert_equals "analysis-agent" "$(az servicebus topic subscription show --resource-group "$RG" --namespace-name "$SB_NAMESPACE" --topic-name analysis-input --name analysis-agent --query name -o tsv)" "analysis-input subscription exists"
assert_equals "decision-agent" "$(az servicebus topic subscription show --resource-group "$RG" --namespace-name "$SB_NAMESPACE" --topic-name analysis-results --name decision-agent --query name -o tsv)" "analysis-results subscription exists"
assert_equals "local-agent" "$(az servicebus topic subscription show --resource-group "$RG" --namespace-name "$SB_NAMESPACE" --topic-name final-decisions --name local-agent --query name -o tsv)" "final-decisions subscription exists"

assert_equals "$COSMOS_ACCOUNT" "$(az cosmosdb show --resource-group "$RG" --name "$COSMOS_ACCOUNT" --query name -o tsv)" "cosmos account exists"

analysis_api_url="$(az functionapp config appsettings list --resource-group "$RG" --name "$ANALYSIS_APP" --query "[?name=='OPENCODE_API_URL'].value | [0]" -o tsv)"
analysis_model="$(az functionapp config appsettings list --resource-group "$RG" --name "$ANALYSIS_APP" --query "[?name=='OPENCODE_MODEL'].value | [0]" -o tsv)"
assert_equals "$OPENCODE_API_URL" "$analysis_api_url" "analysis app OPENCODE_API_URL"
assert_equals "$OPENCODE_MODEL" "$analysis_model" "analysis app OPENCODE_MODEL"

TOKEN_FUNCTION_KEY="$(az functionapp function keys list \
  --resource-group "$RG" \
  --name "$TOKEN_APP" \
  --function-name "$TOKEN_FUNCTION_NAME" \
  --query default -o tsv)"
TOKEN_SERVICE_URL="https://${TOKEN_APP}.azurewebsites.net/api/token?code=${TOKEN_FUNCTION_KEY}"
TOKEN_RESPONSE="$(curl -fsS -X POST "$TOKEN_SERVICE_URL" \
  -H 'Content-Type: application/json' \
  -H "X-Node-ID: ${NODE_ID}" \
  -H "X-API-Key: ${NODE_API_KEY}" \
  -d "{\"node_id\":\"${NODE_ID}\"}")"

if [[ "$TOKEN_RESPONSE" != *'"sas_url"'* ]]; then
  echo "FAILED: token service response did not contain sas_url" >&2
  echo "$TOKEN_RESPONSE" >&2
  exit 1
fi

echo "PASS: token service returned a SAS payload"
echo "All Azure POC smoke checks passed."
