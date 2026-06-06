#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${1:-malenia}"
WORK_ROOT="${WORK_ROOT:-/home/freerat/projects}"
PHOE_NIX_LOCAL="${PHOE_NIX_LOCAL:-/home/freerat/projects/phoe-nix}"
PHOE_NIX_CONFIG_LOCAL="${PHOE_NIX_CONFIG_LOCAL:-/home/freerat/projects/phoe-nix-config}"
REMOTE_CODE_DIR="${REMOTE_CODE_DIR:-$WORK_ROOT/phoe-nix-poc-test}"
REMOTE_CONFIG_DIR="${REMOTE_CONFIG_DIR:-$WORK_ROOT/phoe-nix-config-poc-test}"
REMOTE_MOCK_STATE_DIR="${REMOTE_MOCK_STATE_DIR:-/tmp/phoe-nix-mock-azure-state}"
REMOTE_MOCK_LOG="${REMOTE_MOCK_LOG:-/tmp/phoe-nix-mock-azure.log}"
GUEST_NODE_SRC_DIR="${GUEST_NODE_SRC_DIR:-/home/user/phoe-nix-node-src}"
GUEST_CONFIG_ORIGIN_SRC="${GUEST_CONFIG_ORIGIN_SRC:-/home/user/phoe-nix-config-origin-src}"
GUEST_CONFIG_ORIGIN_GIT="${GUEST_CONFIG_ORIGIN_GIT:-/home/user/phoe-nix-config-origin.git}"
GUEST_CONFIG_REPO_PATH="${GUEST_CONFIG_REPO_PATH:-/var/lib/phoe-nix-config-repo}"
DECISION_ID="${DECISION_ID:-mock-sim-decision}"
ANALYSIS_ID="${ANALYSIS_ID:-mock-sim-analysis}"
ANALYSIS_SUMMARY="${ANALYSIS_SUMMARY:-Add a single harmless comment near the top of configuration.nix and keep the configuration otherwise valid.}"
REMEDIATION_TEXT="${REMEDIATION_TEXT:-Add a comment line saying mock simulation verification near the top of configuration.nix without changing behavior.}"

proxy_cmd=(ssh -o BatchMode=yes -W %h:%p "$REMOTE_HOST")
guest_ssh=(nix shell nixpkgs#sshpass -c sshpass -p user ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ProxyCommand="${proxy_cmd[*]}" -p 2222 user@localhost)
guest_scp=(nix shell nixpkgs#sshpass -c sshpass -p user scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ProxyCommand="${proxy_cmd[*]}" -P 2222)

log() {
  printf '[mock-sim] %s\n' "$*"
}

remote_ssh() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "$@"
}

wait_for_guest() {
  for _ in $(seq 1 24); do
    if remote_ssh "ss -ltn '( sport = :2222 )' | grep -q 2222"; then
      return 0
    fi
    sleep 5
  done
  return 1
}

log "Syncing staged repos to $REMOTE_HOST"
rsync -az --delete --exclude '.git/' --exclude 'result/' --exclude 'nixos.qcow2' --exclude '.venv/' --exclude '.terraform/' "$PHOE_NIX_LOCAL/" "$REMOTE_HOST:$REMOTE_CODE_DIR/"
rsync -az --delete --exclude '.git/' --exclude 'result/' --exclude 'nixos.qcow2' "$PHOE_NIX_CONFIG_LOCAL/" "$REMOTE_HOST:$REMOTE_CONFIG_DIR/"

log "Starting mock Azure on $REMOTE_HOST"
if ! remote_ssh "curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1"; then
  remote_ssh "rm -rf '$REMOTE_MOCK_STATE_DIR' '$REMOTE_MOCK_LOG'; mkdir -p '$REMOTE_MOCK_STATE_DIR'; cd '$REMOTE_CODE_DIR' && setsid -f bash -lc 'exec nix shell nixpkgs#python3 -c python scripts/mock_azure.py --host 127.0.0.1 --port 8088 --state-dir '\''$REMOTE_MOCK_STATE_DIR'\'' > '\''$REMOTE_MOCK_LOG'\'' 2>&1 < /dev/null'"
fi
remote_ssh "for i in 1 2 3 4 5 6 7 8; do curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1 && break; sleep 2; done; curl -fsS -X POST http://127.0.0.1:8088/reset >/dev/null"

log "Booting fresh VM on $REMOTE_HOST"
if ! remote_ssh "ss -ltn '( sport = :2222 )' | grep -q 2222"; then
  remote_ssh "cd '$REMOTE_CONFIG_DIR' && rm -f result && setsid -f bash -lc 'exec env PHOE_NIX_SOURCE_ROOT='\''$REMOTE_CODE_DIR'\'' ./run-vm.sh > /tmp/phoe-nix-vm-mock-sim.log 2>&1 < /dev/null'"
fi
wait_for_guest

log "Preparing guest source tree"
"${guest_ssh[@]}" "printf 'user\\n' | sudo -S bash -lc 'systemctl stop local_agent log_service >/dev/null 2>&1 || true; rm -rf "$GUEST_NODE_SRC_DIR" "$GUEST_CONFIG_ORIGIN_SRC" "$GUEST_CONFIG_ORIGIN_GIT"' && mkdir -p '$GUEST_NODE_SRC_DIR/local_agent' '$GUEST_NODE_SRC_DIR/log_service' '$GUEST_NODE_SRC_DIR/schemas' '$GUEST_NODE_SRC_DIR/scripts' '$GUEST_CONFIG_ORIGIN_SRC'"

"${guest_scp[@]}" -r "$PHOE_NIX_LOCAL/local_agent/src" user@localhost:"$GUEST_NODE_SRC_DIR/local_agent/"
"${guest_scp[@]}" -r "$PHOE_NIX_LOCAL/log_service/src" user@localhost:"$GUEST_NODE_SRC_DIR/log_service/"
"${guest_scp[@]}" -r "$PHOE_NIX_LOCAL/schemas/src" user@localhost:"$GUEST_NODE_SRC_DIR/schemas/"
"${guest_scp[@]}" "$PHOE_NIX_LOCAL/scripts/mock-local-agent.env" "$PHOE_NIX_LOCAL/scripts/mock-log-service.env" "$PHOE_NIX_LOCAL/scripts/start-updated-local-agent.sh" "$PHOE_NIX_LOCAL/scripts/sanitize-hardware-config.py" user@localhost:"$GUEST_NODE_SRC_DIR/scripts/"
"${guest_scp[@]}" "$PHOE_NIX_CONFIG_LOCAL/configuration.nix" "$PHOE_NIX_CONFIG_LOCAL/flake.nix" "$PHOE_NIX_CONFIG_LOCAL/phoe-services.nix" user@localhost:"$GUEST_CONFIG_ORIGIN_SRC/"

log "Seeding guest-local config origin"
"${guest_ssh[@]}" "cd '$GUEST_CONFIG_ORIGIN_SRC' && git init -b main >/dev/null && git add configuration.nix flake.nix phoe-services.nix && git -c user.name=phoe-nix -c user.email=phoe-nix@local commit -m initial >/dev/null"
"${guest_ssh[@]}" "printf 'user\\n' | sudo -S bash -lc 'if nixos-generate-config --show-hardware-config > /tmp/hardware-configuration.nix; then :; elif [ -f /etc/nixos/hardware-configuration.nix ]; then cp /etc/nixos/hardware-configuration.nix /tmp/hardware-configuration.nix; else exit 1; fi; /nix/store/12ssf28w7zvg3g6ms7hnxsfap2cpd5h5-python3-3.11.11-env/bin/python "$GUEST_NODE_SRC_DIR/scripts/sanitize-hardware-config.py" /tmp/hardware-configuration.nix; cp /tmp/hardware-configuration.nix "$GUEST_CONFIG_ORIGIN_SRC/hardware-configuration.nix" && chown user:users "$GUEST_CONFIG_ORIGIN_SRC/hardware-configuration.nix"'"
"${guest_ssh[@]}" "cd '$GUEST_CONFIG_ORIGIN_SRC' && git add hardware-configuration.nix && git -c user.name=phoe-nix -c user.email=phoe-nix@local commit -m add-hardware-config >/dev/null && git clone --bare '$GUEST_CONFIG_ORIGIN_SRC' '$GUEST_CONFIG_ORIGIN_GIT' >/dev/null"

log "Launching updated local_agent daemon from guest source"
"${guest_ssh[@]}" "chmod +x '$GUEST_NODE_SRC_DIR/scripts/start-updated-local-agent.sh' && printf 'user\\n' | sudo -S bash -lc 'rm -rf "$GUEST_CONFIG_REPO_PATH"; setsid -f env NODE_SOURCE_ROOT="$GUEST_NODE_SRC_DIR" "$GUEST_NODE_SRC_DIR/scripts/start-updated-local-agent.sh" > /tmp/phoe-nix-local-agent-mock-sim.log 2>&1 < /dev/null'"
for _ in $(seq 1 12); do
  if "${guest_ssh[@]}" "ps -ef | grep -E '[l]ocal_agent.main' >/dev/null"; then
    break
  fi
  sleep 2
done
if ! "${guest_ssh[@]}" "ps -ef | grep -E '[l]ocal_agent.main' >/dev/null"; then
  printf '%s\n' "--- local-agent-log ---"
  "${guest_ssh[@]}" "printf 'user\\n' | sudo -S grep -n '' /tmp/phoe-nix-local-agent-mock-sim.log || true"
  exit 1
fi

log "Publishing one decision to mock Service Bus"
remote_ssh "cd '$REMOTE_CODE_DIR' && nix shell nixpkgs#python3 -c python scripts/publish-mock-decision.py --base-url http://127.0.0.1:8088 --decision-id '$DECISION_ID' --analysis-id '$ANALYSIS_ID' --analysis-summary '$ANALYSIS_SUMMARY' --remediation-text '$REMEDIATION_TEXT' >/dev/null"

log "Waiting for execution result"
result_json=""
for _ in $(seq 1 90); do
  result_json="$(remote_ssh "curl -fsS http://127.0.0.1:8088/cosmos/databases/project-healer/containers/execution-results")"
if printf '%s' "$result_json" | grep -q '"documents": \[{'; then
    break
  fi
  sleep 5
done

log "Collecting simulation outputs"
service_status="$(remote_ssh "curl -fsS http://127.0.0.1:8088/cosmos/databases/project-healer/containers/service-status")"
repair_traces="$(remote_ssh "curl -fsS http://127.0.0.1:8088/cosmos/databases/project-healer/containers/repair-traces")"
analysis_input="$(remote_ssh "curl -fsS http://127.0.0.1:8088/servicebus/topics/analysis-input")"

printf '%s\n' "--- execution-results ---"
printf '%s\n' "$result_json"
printf '%s\n' "--- repair-traces ---"
printf '%s\n' "$repair_traces"
printf '%s\n' "--- service-status ---"
printf '%s\n' "$service_status"
printf '%s\n' "--- analysis-input ---"
printf '%s\n' "$analysis_input"
