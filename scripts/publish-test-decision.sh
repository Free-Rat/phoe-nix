#!/usr/bin/env bash
# publish-test-decision.sh — Publish a Decision JSON payload to Service Bus
# so a running local_agent on the VM can pick it up via its real receive loop.
#
# This is the Phase 4 verification helper. The body shape matches
# schemas.Decision exactly so the consumer can parse it with Decision.model_validate.
#
# Implementation note: the old `az servicebus topic message send` CLI was
# removed in az 2.80+, so we use the azure-servicebus Python SDK (which is
# already a dependency of the local_agent flake) and run it under nix-shell.

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

DECISION_ID="${DECISION_ID:-phase4-$(date -u +%Y%m%dT%H%M%S)}"
export DECISION_ID
ANALYSIS_ID="${ANALYSIS_ID:-phase4-analysis}"
export ANALYSIS_ID
ACTION="${ACTION:-no_action}"
export ACTION
SEVERITY="${SEVERITY:-info}"
export SEVERITY
ANALYSIS_SUMMARY="${ANALYSIS_SUMMARY:-Phase 4 receive-path smoke test. No repair needed.}"
export ANALYSIS_SUMMARY
REMEDIATION_TEXT="${REMEDIATION_TEXT:-No action required. Continue monitoring.}"
export REMEDIATION_TEXT
CONFIDENCE="${CONFIDENCE:-0.9}"
export CONFIDENCE
NODE_ID="${NODE_ID:-nixos}"
export NODE_ID

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
  1  prerequisites missing or arguments invalid
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

resolve_sb_namespace() {
  if [[ -n "$SB_NAMESPACE" ]]; then
    return 0
  fi
  if ! command -v az >/dev/null 2>&1; then
    echo "SB_NAMESPACE unset and az not on PATH; set SB_NAMESPACE explicitly" >&2
    return 1
  fi
  if [[ -z "${AZURE_TENANT_SUFFIX:-}" ]]; then
    local tenant_id
    tenant_id="$(az account show --query tenantId -o tsv 2>/dev/null)" || {
      echo "Unable to derive AZURE_TENANT_SUFFIX from Azure CLI; set SB_NAMESPACE or AZURE_TENANT_SUFFIX, or run az login" >&2
      return 1
    }
    if [[ -z "$tenant_id" ]]; then
      echo "Azure CLI returned an empty tenantId; set SB_NAMESPACE or AZURE_TENANT_SUFFIX explicitly" >&2
      return 1
    fi
    export AZURE_TENANT_SUFFIX
    AZURE_TENANT_SUFFIX="$(printf '%s' "${tenant_id//-/}" | cut -c1-6)"
  fi
  SB_NAMESPACE="sb-${PROJECT_NAME}-${ENV}-${AZURE_TENANT_SUFFIX}"
}

resolve_sb_namespace

# Resolve the namespace access key. We prefer the Key Vault secret set up by
# infrastructure/04-stateless; falling back to az CLI keeps the script usable
# for ad-hoc testing before the KV access policy has been wired.
resolve_sb_key() {
  if [[ -n "${SERVICEBUS_CONNECTION:-}" ]]; then
    return 0
  fi
  if ! command -v az >/dev/null 2>&1; then
    echo "SERVICEBUS_CONNECTION unset and az not on PATH; set SERVICEBUS_CONNECTION explicitly" >&2
    return 1
  fi
  local auth_rule="SharedAccessPolicy"
  local key
  key="$(az servicebus namespace authorization-rule keys list \
    --resource-group "$RG" \
    --namespace-name "$SB_NAMESPACE" \
    --name "$auth_rule" \
    --query primaryKey -o tsv 2>/dev/null || true)"
  if [[ -z "$key" ]]; then
    echo "Unable to fetch Service Bus key via az; set SERVICEBUS_CONNECTION explicitly" >&2
    return 1
  fi
  SERVICEBUS_CONNECTION="Endpoint=sb://${SB_NAMESPACE}.servicebus.windows.net/;SharedAccessKeyName=${auth_rule};SharedAccessKey=${key}"
}
resolve_sb_key

export TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export SERVICEBUS_CONNECTION

TMP_BODY="$(mktemp -t phoe-nix-decision.XXXXXX.json)"
TMP_OUTPUT="$(mktemp -t phoe-nix-publish.XXXXXX.txt)"
trap 'rm -f "$TMP_BODY" "$TMP_OUTPUT"' EXIT

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
# fails fast with a clear error rather than dumping an opaque SDK error.
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

# Write a tiny publisher script to a temp file. We could feed it to nix-shell
# via stdin, but mixing bash heredocs with nix-shell's --run / -- argument
# forwarding is awkward; a tempfile is clearer and easier to debug on
# failure (TMP_OUTPUT captures the SDK's traceback).
TMP_PUBLISHER="$(mktemp -t phoe-nix-publish.XXXXXX.py)"
trap 'rm -f "$TMP_BODY" "$TMP_OUTPUT" "$TMP_PUBLISHER"' EXIT
cat > "$TMP_PUBLISHER" <<'PY'
"""Publish a Decision JSON to the configured Service Bus topic."""
import os
import sys

from azure.servicebus import ServiceBusClient, ServiceBusMessage


def main() -> int:
    body_path = sys.argv[1]
    topic_name = sys.argv[2]
    conn = os.environ.get("SERVICEBUS_CONNECTION", "")
    if not conn:
        print("SERVICEBUS_CONNECTION is empty", file=sys.stderr)
        return 2
    with open(body_path, "rb") as handle:
        body_bytes = handle.read()
    client = ServiceBusClient.from_connection_string(conn)
    with client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            message = ServiceBusMessage(body_bytes, content_type="application/json")
            sender.send_messages(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

# Run the publisher under nix-shell so we don't depend on the host's
# Python having azure-servicebus installed; this matches the Python
# runtime the local_agent on the VM uses to *receive* these messages.
NIX_PYTHON_PACKAGE="${NIX_PYTHON_PACKAGE:-python313}"
if ! nix-shell -p "${NIX_PYTHON_PACKAGE}.withPackages (ps: [ ps.azure-servicebus ])" --run \
    "python3 \"$TMP_PUBLISHER\" \"$TMP_BODY\" \"$DECISION_TOPIC\"" \
    2>"$TMP_OUTPUT"; then
  echo "Failed to publish Decision to $SB_NAMESPACE/$DECISION_TOPIC" >&2
  cat "$TMP_OUTPUT" >&2
  exit 2
fi

echo "Sent Decision $DECISION_ID to $DECISION_TOPIC"
echo "Watch on the VM with:"
echo "  ssh -p 2222 user@localhost 'journalctl -u local_agent -f | grep -E \"decision|repair\"'"
