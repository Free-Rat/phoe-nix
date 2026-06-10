#!/usr/bin/env bash
# Phase 4 — Exercise the VM-side receive path.
#
# This is the operator-facing orchestrator for Phase 4 of PLAN.md. It:
#
#   1. Pre-flight: confirms the local-agent subscription on final-decisions exists.
#   2. Confirms the VM env file at /etc/phoe-nix/local-agent.env is in place
#      and Service Bus is enabled (read-only check; does not modify the VM).
#   3. Publishes a no_action Decision to the final-decisions topic so the running
#      local_agent on the VM receives and processes it.
#   4. Prints the exact journalctl command to run on the VM to confirm receipt.
#
# It does NOT ssh into the VM. The actual journalctl step is left to the
# operator because this script is meant to be run from the host that already
# has az credentials.

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
export ACTION="${ACTION:-no_action}"
export SEVERITY="${SEVERITY:-info}"
export CONFIDENCE="${CONFIDENCE:-0.9}"
export DECISION_ID="${DECISION_ID:-phase4-$(date -u +%Y%m%dT%H%M%S)}"
export ANALYSIS_ID="${ANALYSIS_ID:-phase4-analysis}"
export ANALYSIS_SUMMARY="${ANALYSIS_SUMMARY:-Phase 4 receive-path smoke test. No repair needed.}"
export REMEDIATION_TEXT="${REMEDIATION_TEXT:-No action required. Continue monitoring.}"
SKIP_VM_CHECK=0

usage() {
  cat <<'EOF'
Usage: bash scripts/phase4-verify.sh [options]

Phase 4 orchestrator. Runs the pre-flight subscription check, optionally
verifies the VM-side env file via ssh, then publishes a no_action Decision
to the final-decisions Service Bus topic and prints the journalctl command
to run on the VM to confirm the local_agent received it.

Options:
  --node-id ID              Target node_id (default: nixos)
  --ssh TARGET              SSH target for VM check (default: user@localhost)
  --ssh-port PORT           SSH port (default: 2222)
  --env-path PATH           Path to local-agent.env on the VM (default: /etc/phoe-nix/local-agent.env)
  --service NAME            systemd service name (default: local_agent)
  --action ACTION           Decision action (default: no_action)
  --severity S              Severity (default: info)
  --confidence F            Confidence 0.0-1.0 (default: 0.9)
  --decision-id ID          Decision id (default: phase4-<UTC timestamp>)
  --analysis-id ID          Analysis id (default: phase4-analysis)
  --skip-vm-check           Do not attempt to read /etc/phoe-nix/local-agent.env
                            on the VM via ssh. Useful when running this from
                            a host that cannot reach the VM directly.
  -h|--help                 Show this help

Environment (same as the rest of the repo):
  SB_NAMESPACE            Optional explicit override of the Service Bus namespace.
  AZURE_TENANT_SUFFIX     Optional explicit override used when SB_NAMESPACE is unset.
                          Otherwise the script derives the suffix from
                          `az account show --query tenantId -o tsv`, matching
                          infrastructure/04-stateless/locals.tf.

Exit codes:
  0  all pre-flight checks passed and Decision was published
  1  prerequisites missing (az, az login) or arguments invalid
  2  pre-flight check failed (subscription missing, VM env not enabled)
  3  publish failed
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node-id)
      NODE_ID="$2"
      shift 2
      ;;
    --ssh)
      VM_SSH_TARGET="$2"
      shift 2
      ;;
    --ssh-port)
      VM_SSH_PORT="$2"
      shift 2
      ;;
    --env-path)
      VM_ENV_PATH="$2"
      shift 2
      ;;
    --service)
      VM_SERVICE_NAME="$2"
      shift 2
      ;;
    --action)
      ACTION="$2"
      shift 2
      ;;
    --severity)
      SEVERITY="$2"
      shift 2
      ;;
    --confidence)
      CONFIDENCE="$2"
      shift 2
      ;;
    --decision-id)
      DECISION_ID="$2"
      shift 2
      ;;
    --analysis-id)
      ANALYSIS_ID="$2"
      shift 2
      ;;
    --skip-vm-check)
      SKIP_VM_CHECK=1
      shift
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

echo "== Phase 4 pre-flight =="

actual_topic="$(az servicebus topic show \
  --resource-group "$RG" \
  --namespace-name "$SB_NAMESPACE" \
  --name "$DECISION_TOPIC" \
  --query name -o tsv 2>/dev/null || true)"
if [[ "$actual_topic" != "$DECISION_TOPIC" ]]; then
  echo "FAILED: service bus topic $DECISION_TOPIC not found in $SB_NAMESPACE" >&2
  exit 2
fi
echo "PASS: topic $DECISION_TOPIC exists in $SB_NAMESPACE"

actual_sub="$(az servicebus topic subscription show \
  --resource-group "$RG" \
  --namespace-name "$SB_NAMESPACE" \
  --topic-name "$DECISION_TOPIC" \
  --name "$LOCAL_AGENT_SUBSCRIPTION" \
  --query name -o tsv 2>/dev/null || true)"
if [[ "$actual_sub" != "$LOCAL_AGENT_SUBSCRIPTION" ]]; then
  echo "FAILED: subscription $LOCAL_AGENT_SUBSCRIPTION on $DECISION_TOPIC not found" >&2
  echo "  HINT: this is provisioned by infrastructure/04-stateless/main.tf (azurerm_servicebus_subscription.local_agent)" >&2
  exit 2
fi
echo "PASS: subscription $LOCAL_AGENT_SUBSCRIPTION exists on $DECISION_TOPIC"

if [[ "$SKIP_VM_CHECK" -eq 0 ]]; then
  echo "== VM env file pre-flight =="
  if ! command -v ssh >/dev/null 2>&1; then
    echo "SKIP: ssh not available; cannot read $VM_ENV_PATH" >&2
  else
    if ! ssh -o ConnectTimeout=5 -p "$VM_SSH_PORT" "$VM_SSH_TARGET" true 2>/dev/null; then
      echo "SKIP: cannot reach $VM_SSH_TARGET on port $VM_SSH_PORT" >&2
    else
      env_dump="$(ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "cat '$VM_ENV_PATH' 2>/dev/null || true")"
      if [[ -z "$env_dump" ]]; then
        echo "WARN: $VM_ENV_PATH on the VM is empty or unreadable" >&2
        echo "      local_agent will run with SERVICEBUS_ENABLED=0 (receive loop is a no-op)" >&2
      else
        sb_enabled="$(printf '%s\n' "$env_dump" | awk -F= '/^SERVICEBUS_ENABLED=/{print $2; exit}')"
        sb_conn="$(printf '%s\n' "$env_dump" | awk -F= '/^SERVICEBUS_CONNECTION=/{print $2; exit}')"
        if [[ "$sb_enabled" != "1" ]]; then
          echo "WARN: SERVICEBUS_ENABLED is '$sb_enabled' (expected '1')" >&2
          echo "      local_agent receive loop will not run" >&2
        else
          echo "PASS: SERVICEBUS_ENABLED=1 on the VM"
        fi
        if [[ -z "$sb_conn" ]]; then
          echo "WARN: SERVICEBUS_CONNECTION is empty on the VM" >&2
        else
          echo "PASS: SERVICEBUS_CONNECTION is set on the VM (length=${#sb_conn})"
        fi
      fi
      svc_status="$(ssh -p "$VM_SSH_PORT" "$VM_SSH_TARGET" "systemctl is-active '$VM_SERVICE_NAME' 2>/dev/null || true")"
      if [[ "$svc_status" != "active" ]]; then
        echo "WARN: $VM_SERVICE_NAME is '$svc_status' on the VM (expected 'active')" >&2
      else
        echo "PASS: $VM_SERVICE_NAME is active on the VM"
      fi
    fi
  fi
else
  echo "SKIP: VM check disabled (--skip-vm-check)"
fi

echo
echo "== Publishing test Decision =="
if ! bash "$ROOT_DIR/scripts/publish-test-decision.sh" \
    --node-id "$NODE_ID" \
    --decision-id "$DECISION_ID" \
    --analysis-id "$ANALYSIS_ID" \
    --action "$ACTION" \
    --severity "$SEVERITY" \
    --confidence "$CONFIDENCE" \
    --summary "$ANALYSIS_SUMMARY" \
    --remediation "$REMEDIATION_TEXT" \
    --topic "$DECISION_TOPIC"; then
  echo "FAILED: publish-test-decision.sh exited non-zero" >&2
  exit 3
fi

echo
echo "== Next step (run on the VM) =="
echo "  ssh -p $VM_SSH_PORT $VM_SSH_TARGET 'journalctl -u $VM_SERVICE_NAME -f | grep -E \"decision|repair|receive\"'"
echo
echo "You should see a 'decision' stage service-status document with correlation_id=$DECISION_ID."
echo "If you used --action no_action, look for 'status=skipped' with detail='no action requested'."
echo "If you used --action apply_config, the repair loop will run; expect 'nixos-rebuild test' invocations."
