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
export NODE_ID="${NODE_ID:-nixos}"
export DECISION_TOPIC="${DECISION_TOPIC:-final-decisions}"

DECISION_ID="phase4-$(date -u +%Y%m%dT%H%M%S)"
ANALYSIS_ID="${ANALYSIS_ID:-phase4-analysis}"
ACTION="${ACTION:-no_action}"
SEVERITY="${SEVERITY:-info}"
ANALYSIS_SUMMARY="${ANALYSIS_SUMMARY:-Phase 4 receive-path smoke test. No repair needed.}"
REMEDIATION_TEXT="${REMEDIATION_TEXT:-No action required. Continue monitoring.}"
CONFIDENCE="${CONFIDENCE:-0.9}"

usage() {
  cat <<'EOF'
Usage: bash scripts/publish-test-decision.sh [options]

Publishes a minimal Decision JSON payload to the final-decisions Service Bus topic
so a running local_agent on the VM can pick it up via its real receive loop.

This is the Phase 4 verification helper. The body shape matches
schemas.Decision exactly so the consumer can parse it with Decision.model_validate.

Options:
  --node-id ID          Target node_id (default: $NODE_ID or 'nixos')
  --decision-id ID      Decision id (default: phase4-<UTC timestamp>)
  --analysis-id ID      Analysis id (default: phase4-analysis)
  --action ACTION       Decision action: no_action|apply_config|restart_service|rebuild|rollback
                        (default: no_action)
  --severity S          Severity: critical|warning|info (default: info)
  --confidence F        Confidence 0.0-1.0 (default: 0.9)
  --summary TEXT        Human-readable analysis summary
  --remediation TEXT    Human-readable remediation text
  --topic NAME          Service Bus topic name (default: final-decisions)
  --body-file PATH      Send a pre-built JSON body from PATH instead of the
                        generated default. Useful for replaying real decisions.
  -h|--help             Show this help

Environment:
  SB_NAMESPACE            Optional explicit override of the Service Bus namespace.
  AZURE_TENANT_SUFFIX     Optional explicit override used when SB_NAMESPACE is unset.
                          Otherwise the script derives the suffix from
                          `az account show --query tenantId -o tsv`, matching
                          infrastructure/04-stateless/locals.tf.

Exit codes:
  0  message sent successfully
  1  prerequisites missing (az, az login) or arguments invalid
  2  message send failed
EOF
}

BODY_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --node-id)
      NODE_ID="$2"
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
    --summary)
      ANALYSIS_SUMMARY="$2"
      shift 2
      ;;
    --remediation)
      REMEDIATION_TEXT="$2"
      shift 2
      ;;
    --topic)
      DECISION_TOPIC="$2"
      shift 2
      ;;
    --body-file)
      BODY_FILE="$2"
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

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to build the JSON body" >&2
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

case "$ACTION" in
  no_action|apply_config|restart_service|rebuild|rollback) ;;
  *)
    echo "Invalid --action: $ACTION" >&2
    exit 1
    ;;
esac

case "$SEVERITY" in
  critical|warning|info) ;;
  *)
    echo "Invalid --severity: $SEVERITY" >&2
    exit 1
    ;;
esac

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

TMP_BODY="$(mktemp -t phoe-nix-decision.XXXXXX.json)"
trap 'rm -f "$TMP_BODY"' EXIT

if [[ -n "$BODY_FILE" ]]; then
  if [[ ! -f "$BODY_FILE" ]]; then
    echo "Body file not found: $BODY_FILE" >&2
    exit 1
  fi
  cp "$BODY_FILE" "$TMP_BODY"
else
  python3 - "$TMP_BODY" <<PY
import json
import os
import sys

target = sys.argv[1]
decision = {
    "schema_version": "1.0",
    "decision_id": os.environ["DECISION_ID"],
    "node_id": os.environ["NODE_ID"],
    "analysis_id": os.environ["ANALYSIS_ID"],
    "action": os.environ["ACTION"],
    "command": "",
    "severity": os.environ["SEVERITY"],
    "confidence": float(os.environ["CONFIDENCE"]),
    "analysis_summary": os.environ["ANALYSIS_SUMMARY"],
    "remediation_text": os.environ["REMEDIATION_TEXT"],
    "idempotency_key": os.environ["DECISION_ID"],
    "timestamp": os.environ["TIMESTAMP"],
}
with open(target, "w", encoding="utf-8") as handle:
    json.dump(decision, handle)
PY
fi

# Validate the body parses as a Decision before we hit Azure. This guarantees
# the consumer (local_agent) will accept the message; if it doesn't, the script
# fails fast with a clear error rather than dumping an opaque az error.
python3 - "$TMP_BODY" <<'PY' || VALIDATION_FAILED=1
import json
import sys

target = sys.argv[1]
with open(target, encoding="utf-8") as handle:
    raw = json.load(handle)
required = {
    "schema_version", "decision_id", "node_id", "analysis_id", "action",
    "command", "severity", "confidence", "analysis_summary",
    "remediation_text", "idempotency_key", "timestamp",
}
missing = required - set(raw)
if missing:
    raise SystemExit(f"missing fields: {sorted(missing)}")
allowed_severity = {"critical", "warning", "info"}
if raw["severity"] not in allowed_severity:
    raise SystemExit(f"invalid severity: {raw['severity']!r}")
confidence = raw["confidence"]
if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
    raise SystemExit(f"confidence out of range: {confidence!r}")
PY
VALIDATION_FAILED=${VALIDATION_FAILED:-0}
if [[ "$VALIDATION_FAILED" -ne 0 ]]; then
  echo "Generated body did not validate against the Decision schema:" >&2
  cat "$TMP_BODY" >&2
  exit 1
fi

echo "Publishing Decision to $SB_NAMESPACE/$DECISION_TOPIC ..."
echo "  decision_id : $DECISION_ID"
echo "  node_id     : $NODE_ID"
echo "  action      : $ACTION"
echo "  severity    : $SEVERITY"

if ! az servicebus topic message send \
    --resource-group "$RG" \
    --namespace-name "$SB_NAMESPACE" \
    --topic-name "$DECISION_TOPIC" \
    --content-type "application/json" \
    --body "@$TMP_BODY" \
    -o none; then
  echo "Failed to publish Decision to $SB_NAMESPACE/$DECISION_TOPIC" >&2
  exit 2
fi

echo "Sent Decision $DECISION_ID to $DECISION_TOPIC"
echo "Watch on the VM with:"
echo "  ssh -p 2222 user@localhost 'journalctl -u local_agent -f | grep -E \"decision|repair\"'"
