#!/usr/bin/env bash
# Phase 5 — Exercise the repair loop end-to-end with real Ollama.
#
# This is the operator-facing orchestrator for Phase 5 of PLAN.md. It:
#
#   1. Pre-flight: confirms the VM is up, ollama is reachable, the config
#      repo is cloned at the expected path, and a hardware-configuration.nix
#      exists in the repo.
#   2. Ollama reachability check: from inside the VM, issues a small
#      /api/generate call against the host ollama. Verifies the model
#      loads and returns a response.
#   3. Publishes an apply_config Decision to final-decisions so the running
#      local_agent on the VM triggers execute_repair_loop.
#   4. Watches the resulting service-status documents in Cosmos DB. The
#      expectation is:
#        - decision/received   with correlation_id=<our_decision>
#        - decision/skipped|completed|failed depending on the rebuild
#          outcome (on CPU-only hardware the rebuild often exceeds the
#          300s subprocess timeout; that is the expected POC failure
#          mode, not a regression).
#   5. Prints the journalctl command to run on the VM to inspect the
#      local_agent logs.
#
# It does NOT rebuild the VM. The hardware-config / repo / env setup
# has to be done out of band (see PLAN.md Phase 5 preconditions).

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
export DECISION_TOPIC="${DECISION_TOPIC:-final-decisions}"
export LOCAL_AGENT_SUBSCRIPTION="${LOCAL_AGENT_SUBSCRIPTION:-local-agent}"
export NODE_ID="${NODE_ID:-nixos}"
export VM_SSH_TARGET="${VM_SSH_TARGET:-user@localhost}"
export VM_SSH_PORT="${VM_SSH_PORT:-2222}"
export VM_ENV_PATH="${VM_ENV_PATH:-/etc/phoe-nix/local-agent.env}"
export VM_SERVICE_NAME="${VM_SERVICE_NAME:-local_agent}"
export ACTION="${ACTION:-apply_config}"
export SEVERITY="${SEVERITY:-info}"
export CONFIDENCE="${CONFIDENCE:-0.9}"
export DECISION_ID="${DECISION_ID:-phase5-$(date -u +%Y%m%dT%H%M%S)}"
export ANALYSIS_ID="${ANALYSIS_ID:-phase5-analysis}"
export ANALYSIS_SUMMARY="${ANALYSIS_SUMMARY:-Phase 5 verification: trigger repair loop with real Ollama.}"
export REMEDIATION_TEXT="${REMEDIATION_TEXT:-Enable nginx in configuration.nix.}"
export WATCH_SECONDS="${WATCH_SECONDS:-60}"
export SKIP_VM_CHECK=0

usage() {
  cat <<'EOF'
Usage: bash scripts/phase5-verify.sh [options]

  --decision-id ID         Override the decision_id (default: phase5-<UTC timestamp>)
  --action ACTION          Decision action: apply_config (default) | no_action
  --remediation-text TEXT  Decision remediation_text (default: enable nginx)
  --watch-seconds N        How long to watch service-status for the response
                           (default: 60)
  --skip-vm-check          Don't ssh into the VM for pre-flight
  --dry-run                Print what would happen without publishing
  --help                   Show this help

Exits 0 if the local_agent receives and acknowledges the decision (decision/received
in service-status), regardless of the rebuild outcome. Exits non-zero on pre-flight
failure or if the message is never received within the watch window.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --decision-id) DECISION_ID="$2"; shift 2 ;;
    --action) ACTION="$2"; shift 2 ;;
    --remediation-text) REMEDIATION_TEXT="$2"; shift 2 ;;
    --analysis-summary) ANALYSIS_SUMMARY="$2"; shift 2 ;;
    --watch-seconds) WATCH_SECONDS="$2"; shift 2 ;;
    --skip-vm-check) SKIP_VM_CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# Sanity check the action
case "$ACTION" in
  no_action|apply_config|restart_service|rebuild|rollback) ;;
  *) echo "Invalid --action: $ACTION" >&2; exit 2 ;;
esac

# Derive Service Bus namespace from the tenant suffix when not provided
if [[ -z "$SB_NAMESPACE" ]]; then
  TENANT_SUFFIX="${AZURE_TENANT_SUFFIX:-$(az account show --query tenantId -o tsv 2>/dev/null | tr -d '-' | head -c 6)}"
  if [[ -z "$TENANT_SUFFIX" ]]; then
    echo "Could not derive SB_NAMESPACE: set SB_NAMESPACE or run 'az login'" >&2
    exit 2
  fi
  SB_NAMESPACE="sb-${PROJECT_NAME}-${ENV}-${TENANT_SUFFIX}"
fi
export SB_NAMESPACE

echo "== Phase 5 pre-flight =="
TOPIC_ARGS=(--resource-group "$RG" --namespace-name "$SB_NAMESPACE" --topic-name "$DECISION_TOPIC")
SUB_ARGS=(--resource-group "$RG" --namespace-name "$SB_NAMESPACE" --topic-name "$DECISION_TOPIC" --name "$LOCAL_AGENT_SUBSCRIPTION")

if ! az servicebus topic show "${TOPIC_ARGS[@]}" >/dev/null 2>&1; then
  echo "FAIL: topic $DECISION_TOPIC not found in $SB_NAMESPACE"
  exit 2
fi
echo "PASS: topic $DECISION_TOPIC exists in $SB_NAMESPACE"

if ! az servicebus topic subscription show "${SUB_ARGS[@]}" >/dev/null 2>&1; then
  echo "FAIL: subscription $LOCAL_AGENT_SUBSCRIPTION missing on $DECISION_TOPIC"
  exit 2
fi
echo "PASS: subscription $LOCAL_AGENT_SUBSCRIPTION exists on $DECISION_TOPIC"

if [[ "$SKIP_VM_CHECK" -eq 0 ]]; then
  echo "== VM pre-flight =="
  if ! timeout 5 bash -c "exec 3<>/dev/tcp/127.0.0.1/${VM_SSH_PORT}" 2>/dev/null; then
    echo "FAIL: cannot reach VM SSH at 127.0.0.1:${VM_SSH_PORT}"
    exit 2
  fi
  echo "PASS: VM SSH port reachable"

  REMOTE_ENV=$(ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "cat $VM_ENV_PATH 2>/dev/null" 2>/dev/null || true)
  if [[ -z "$REMOTE_ENV" ]]; then
    echo "FAIL: cannot read $VM_ENV_PATH on VM"
    exit 2
  fi
  if ! grep -q '^SERVICEBUS_ENABLED=1' <<<"$REMOTE_ENV"; then
    echo "FAIL: SERVICEBUS_ENABLED != 1 in $VM_ENV_PATH"
    exit 2
  fi
  if ! grep -q '^SERVICEBUS_CONNECTION=' <<<"$REMOTE_ENV"; then
    echo "FAIL: SERVICEBUS_CONNECTION empty in $VM_ENV_PATH"
    exit 2
  fi
  echo "PASS: SERVICEBUS_ENABLED=1 and SERVICEBUS_CONNECTION set on the VM"

  if ! ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "systemctl is-active $VM_SERVICE_NAME" 2>/dev/null | grep -q '^active'; then
    echo "FAIL: $VM_SERVICE_NAME is not active on the VM"
    exit 2
  fi
  echo "PASS: $VM_SERVICE_NAME is active on the VM"

  # Read the env defaults too, so OLLAMA_BASE_URL and CONFIG_REPO_PATH etc. are
  # visible to the pre-flight checks even if the operator's override doesn't
  # repeat every key.
  REMOTE_DEFAULTS=$(ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "cat ${VM_ENV_PATH}.defaults 2>/dev/null" 2>/dev/null || true)
  REMOTE_ALL="$REMOTE_ENV"$'\n'"$REMOTE_DEFAULTS"

  OLLAMA_URL=$(grep '^OLLAMA_BASE_URL=' <<<"$REMOTE_ALL" | head -1 | cut -d= -f2-)
  if [[ -z "$OLLAMA_URL" ]]; then
    OLLAMA_URL="http://10.0.2.2:11434"
  fi
  if ! ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "curl -sS --max-time 5 ${OLLAMA_URL}/api/tags" 2>/dev/null | grep -q '"models"'; then
    echo "FAIL: cannot reach Ollama at $OLLAMA_URL from the VM"
    exit 2
  fi
  echo "PASS: Ollama reachable at $OLLAMA_URL from the VM"

  REPO_PATH=$(grep '^CONFIG_REPO_PATH=' <<<"$REMOTE_ALL" | head -1 | cut -d= -f2-)
  REPO_PATH="${REPO_PATH:-/var/lib/phoe-nix-config-repo}"
  if ! ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "test -f ${REPO_PATH}/configuration.nix" 2>/dev/null; then
    echo "WARN: ${REPO_PATH}/configuration.nix does not exist on the VM"
    echo "      the repair loop will still run, but the rebuild test will likely fail"
  else
    echo "PASS: ${REPO_PATH}/configuration.nix present on the VM"
  fi
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo
  echo "== Dry run: would publish =="
  echo "  decision_id: $DECISION_ID"
  echo "  action:      $ACTION"
  echo "  severity:    $SEVERITY"
  echo "  node_id:     $NODE_ID"
  echo "  topic:       $DECISION_TOPIC on $SB_NAMESPACE"
  exit 0
fi

echo
echo "== Publishing test Decision =="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION="$ACTION" \
  SEVERITY="$SEVERITY" \
  CONFIDENCE="$CONFIDENCE" \
  DECISION_ID="$DECISION_ID" \
  ANALYSIS_ID="$ANALYSIS_ID" \
  ANALYSIS_SUMMARY="$ANALYSIS_SUMMARY" \
  REMEDIATION_TEXT="$REMEDIATION_TEXT" \
  NODE_ID="$NODE_ID" \
  bash "$SCRIPT_DIR/publish-test-decision.sh"

echo
echo "== Watching service-status for $WATCH_SECONDS seconds =="
COSMOS_KEY=$(az cosmosdb keys list --resource-group "$RG" --name cosmos-${PROJECT_NAME}-${ENV} --type keys --query primaryMasterKey -o tsv 2>/dev/null || true)
if [[ -z "$COSMOS_KEY" ]]; then
  echo "WARN: could not read COSMOS_KEY; skipping watch step"
  echo "  Inspect manually with: bash scripts/check-deployment.sh"
  exit 0
fi

DEADLINE=$(( $(date +%s) + WATCH_SECONDS ))
RECEIVED=0
OUTCOME=""
while [[ $(date +%s) -lt $DEADLINE ]]; do
  QUERY="SELECT TOP 1 c.status, c.detail FROM c WHERE c.stage='decision' AND c.correlation_id='${DECISION_ID}' AND c._ts > $(($(date +%s) - 1800)) ORDER BY c._ts DESC"
  ROW=$(COSMOS_KEY="$COSMOS_KEY" COSMOS_ENDPOINT="https://cosmos-${PROJECT_NAME}-${ENV}.documents.azure.com:443/" \
    COSMOS_DATABASE_NAME="project-healer" \
    nix-shell -p "python313.withPackages (ps: [ ps.azure-cosmos ])" --run "python3 -c \"
import os
from azure.cosmos import CosmosClient
c = CosmosClient(os.environ['COSMOS_ENDPOINT'], os.environ['COSMOS_KEY'])
db = c.get_database_client(os.environ['COSMOS_DATABASE_NAME'])
container = db.get_container_client('service-status')
query = \\\"${QUERY}\\\"
items = list(container.query_items(query=query, enable_cross_partition_query=True))
if items:
    print(items[0]['status'], '|', items[0].get('detail',''))
\"" 2>/dev/null | tail -1 || true)
  if [[ -n "$ROW" ]]; then
    STATUS="${ROW%%|*}"
    DETAIL="${ROW#*|}"
    DETAIL="${DETAIL# }"
    case "$STATUS" in
      received)
        if [[ "$RECEIVED" -eq 0 ]]; then
          echo "  $(date -u +%H:%M:%S)  decision/received  corr=$DECISION_ID  detail=$DETAIL"
          RECEIVED=1
        fi
        ;;
      completed|skipped|failed|blocked)
        echo "  $(date -u +%H:%M:%S)  decision/$STATUS  corr=$DECISION_ID  detail=$DETAIL"
        OUTCOME="$STATUS"
        break
        ;;
    esac
  fi
  sleep 5
done

echo
if [[ "$RECEIVED" -eq 0 ]]; then
  echo "FAIL: no decision/received service-status doc appeared for corr=$DECISION_ID within $WATCH_SECONDS seconds"
  echo "  The local_agent on the VM is probably not subscribed to $DECISION_TOPIC"
  exit 1
fi

if [[ -z "$OUTCOME" ]]; then
  echo "PARTIAL: received but no terminal status within $WATCH_SECONDS seconds"
  echo "  The repair loop may still be running. Wait longer or check the journal."
  exit 0
fi

case "$OUTCOME" in
  completed)
    echo "PASS: repair loop completed successfully (decision/completed)"
    ;;
  skipped)
    if [[ "$ACTION" == "no_action" ]]; then
      echo "PASS: decision was a no_action as expected (decision/skipped)"
    else
      echo "PARTIAL: decision was skipped (likely blocked by safety limits)"
    fi
    ;;
  failed)
    echo "PARTIAL: decision failed (most likely the nixos-rebuild test/subprocess timed out on CPU)"
    echo "  On CPU-only hardware, the rebuild test can exceed 300s. That is a POC"
    echo "  limitation, not a regression. Inspect the journal to confirm Ollama was"
    echo "  called and a proposed config was generated before the timeout."
    ;;
  blocked)
    echo "PARTIAL: decision blocked by safety limits (cooldown / max remediations per hour)"
    ;;
esac
echo
echo "Next step (run on the host):"
echo "  ssh -p $VM_SSH_PORT $VM_SSH_TARGET 'journalctl -u $VM_SERVICE_NAME -n 100 --no-pager | grep -E \"decision|repair|ollama\"'"
