#!/usr/bin/env bash
# check-deployment.sh — Report the live status of every phoe-nix component.
#
# Designed to be human-readable by default and JSON-dumpable with --json so a
# later TUI can parse it. Each check is independent: a missing prerequisite
# for one element (e.g. no resource group) does not stop the others.
#
# Usage:
#   bash scripts/check-deployment.sh            # human-readable summary
#   bash scripts/check-deployment.sh --json     # machine-readable
#   bash scripts/check-deployment.sh --help
#
# Implementation note: `az` is slow (~5 s per call due to Python startup).
# This script gets a bearer token once via `az account get-access-token`,
# caches it under /tmp, and uses curl for every Azure REST call. A full
# run finishes in ~6-8 s instead of ~60 s.
#
# Environment (all optional, defaults match infrastructure/flake.nix):
#   PROJECT_NAME, ENV, RG, COSMOS_ACCOUNT, TOKEN_APP, ROUTER_APP,
#   ANALYSIS_APP, DECISION_APP, VM_HOST, VM_PORT, CONFIG_REPO
#   SB_NAMESPACE, AZURE_TENANT_SUFFIX
# Run inside `nix develop -c bash ...` from infrastructure/ so az is on PATH.

set -uo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

OUTPUT_MODE="human"  # human|json
SHOW_HELP=0
QUICK=0
CLEAR_CACHE=0

usage() {
  cat <<EOF
Usage: bash $SCRIPT_NAME [--json] [--quick] [--clear-cache] [--help]

Reports the live status of every phoe-nix component: cloud resources,
Service Bus topology, Cosmos containers, Azure Function apps, VM qemu
process, and rendered VM env files. Useful before running the Phase 4
verification scripts.

Options:
  --json          Emit a JSON document to stdout (one object per component)
                  instead of the human-readable table.
  --quick         Skip slow checks (function apps, key vault secrets). Use
                  this in a TUI polling loop.
  --clear-cache   Forget the cached Azure bearer token and force a fresh
                  \`az account get-access-token\` call.
  --help          Show this help and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)  OUTPUT_MODE="json" ;;
    --quick) QUICK=1 ;;
    --clear-cache) CLEAR_CACHE=1 ;;
    --help|-h) SHOW_HELP=1 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 64 ;;
  esac
  shift
done

if [[ $SHOW_HELP -eq 1 ]]; then
  usage
  exit 0
fi

# ---------------------------------------------------------------------------
# Config / naming conventions
# ---------------------------------------------------------------------------

PROJECT_NAME="${PROJECT_NAME:-project-healer}"
ENV="${ENV:-dev}"
RG="${RG:-rg-${PROJECT_NAME}-${ENV}}"
COSMOS_ACCOUNT="${COSMOS_ACCOUNT:-cosmos-${PROJECT_NAME}-${ENV}}"
TOKEN_APP="${TOKEN_APP:-func-${PROJECT_NAME}-${ENV}-token}"
ROUTER_APP="${ROUTER_APP:-func-${PROJECT_NAME}-${ENV}-router}"
ANALYSIS_APP="${ANALYSIS_APP:-func-${PROJECT_NAME}-${ENV}-analysis}"
DECISION_APP="${DECISION_APP:-func-${PROJECT_NAME}-${ENV}-decision}"
VM_HOST="${VM_HOST:-127.0.0.1}"
VM_PORT="${VM_PORT:-2222}"
CONFIG_REPO="${CONFIG_REPO:-$ROOT_DIR/../phoe-nix-config}"

TOPIC_ANALYSIS_INPUT="analysis-input"
TOPIC_ANALYSIS_RESULTS="analysis-results"
TOPIC_FINAL_DECISIONS="final-decisions"
SUB_ANALYSIS_AGENT="analysis-agent"
SUB_DECISION_AGENT="decision-agent"
SUB_LOCAL_AGENT="local-agent"

COSMOS_DB="project-healer"
COSMOS_CONTAINERS=(
  observations
  node-state-current
  decisions
  execution-results
  config-snapshots
  repair-traces
  service-status
)

FUNCTION_APPS=("$TOKEN_APP" "$ROUTER_APP" "$ANALYSIS_APP" "$DECISION_APP")
KEYVAULT_SECRETS=(OpenCodeApiKey ServiceBusConnection LogsStorageConnection)

API_VER_RG="2021-04-01"
API_VER_RESOURCES="2021-04-01"
API_VER_SB="2021-11-01"
API_VER_COSMOS="2022-05-15"
API_VER_KV="2022-07-01"
API_VER_WEB="2022-09-01"

# ---------------------------------------------------------------------------
# Temp dir for parallel check results
# ---------------------------------------------------------------------------

TMPDIR_RESULTS="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_RESULTS"; }
trap cleanup EXIT

# Persistent token cache so repeated TUI polls do not re-pay the ~5 s
# `az account get-access-token` cost on every run. Keyed per-user.
TOKEN_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/phoe-nix"
TOKEN_CACHE_FILE="${TOKEN_CACHE_DIR}/azure-bearer-token"
TOKEN_TTL_SECONDS=$((50 * 60))  # access tokens last ~60 min; refresh at 50

if [[ $CLEAR_CACHE -eq 1 ]]; then
  rm -f "$TOKEN_CACHE_FILE" "${TOKEN_CACHE_FILE%.token}.meta" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Azure REST helpers
# ---------------------------------------------------------------------------

# Lazily get a bearer token. Caches in $TOKEN_FILE for the lifetime of the
# process. Returns 0 on success, 1 on failure. Echoes nothing on failure.
TOKEN_FILE="$TMPDIR_RESULTS/azure-token"
SUBSCRIPTION_ID=""
AZ_TENANT_ID=""

have_az() {
  command -v az >/dev/null 2>&1
}

acquire_token() {
  if [[ -s "$TOKEN_FILE" ]]; then
    return 0
  fi
  if [[ -s "$TOKEN_CACHE_FILE" ]]; then
    local age now
    now="$(date +%s)"
    age=$((now - $(stat -c %Y "$TOKEN_CACHE_FILE" 2>/dev/null || echo 0)))
    if [[ $age -lt $TOKEN_TTL_SECONDS ]]; then
      cp "$TOKEN_CACHE_FILE" "$TOKEN_FILE"
      # Also reload the subscription + tenant cached alongside it.
      local cached
      cached="$(cat "${TOKEN_CACHE_FILE%.token}.meta" 2>/dev/null || true)"
      if [[ -n "$cached" ]]; then
        SUBSCRIPTION_ID="$(printf '%s' "$cached" | jq -r .subscription 2>/dev/null)"
        AZ_TENANT_ID="$(printf '%s' "$cached" | jq -r .tenant 2>/dev/null)"
      fi
      return 0
    fi
  fi
  if ! have_az; then
    return 1
  fi
  # Single az call gives us token + subscription + tenant id. Avoiding
  # separate `az account show` calls saves ~5 s of Python startup each.
  local raw sub token tenant
  raw="$(az account get-access-token --resource https://management.azure.com 2>/dev/null || true)"
  if [[ -z "$raw" ]]; then
    return 1
  fi
  token="$(printf '%s' "$raw" | jq -r '.accessToken // empty' 2>/dev/null)"
  sub="$(printf '%s' "$raw" | jq -r '.subscription // empty' 2>/dev/null)"
  tenant="$(printf '%s' "$raw" | jq -r '.tenant // empty' 2>/dev/null)"
  if [[ -z "$token" || -z "$sub" || -z "$tenant" ]]; then
    return 1
  fi
  SUBSCRIPTION_ID="$sub"
  AZ_TENANT_ID="$tenant"
  printf '%s' "$token" > "$TOKEN_FILE"
  # Persist to disk for the next TUI tick (best-effort; never fail).
  mkdir -p "$TOKEN_CACHE_DIR" 2>/dev/null || true
  if [[ -d "$TOKEN_CACHE_DIR" ]]; then
    ( umask 077
      printf '%s' "$token" > "$TOKEN_CACHE_FILE" 2>/dev/null || true
      jq -nc --arg s "$sub" --arg t "$tenant" '{subscription:$s, tenant:$t}' \
        > "${TOKEN_CACHE_FILE%.token}.meta" 2>/dev/null || true
      chmod 600 "$TOKEN_CACHE_FILE" "${TOKEN_CACHE_FILE%.token}.meta" 2>/dev/null || true
    )
  fi
  return 0
}

# GET a URL with the bearer token. Sets AZ_HTTP_CODE and AZ_BODY.
# Usage: az_http_get URL
az_http_get() {
  local url="$1"
  local token
  token="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
  if [[ -z "$token" ]]; then
    AZ_HTTP_CODE=000
    AZ_BODY=""
    return 1
  fi
  local response
  response="$(curl -sS -m 15 -o - -w '\n__HTTP__%{http_code}' \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    "$url" 2>/dev/null || true)"
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    AZ_HTTP_CODE=000
    AZ_BODY=""
    return $rc
  fi
  AZ_HTTP_CODE="$(printf '%s' "$response" | sed -n 's/^__HTTP__//p' | tail -1)"
  AZ_BODY="$(printf '%s' "$response" | sed 's/__HTTP__[0-9]*$//')"
  return 0
}

# Extract a JSON array of names from an Azure list response. Each Azure
# resource list has .value[].name.
extract_names() {
  local body="$1"
  [[ -z "$body" ]] && return 0
  printf '%s' "$body" | jq -r '.value[]?.name // empty' 2>/dev/null
}

# Check if a name appears in a newline-separated list.
list_contains() {
  local needle="$1" haystack="$2"
  [[ -z "$haystack" ]] && return 1
  grep -Fxq "$needle" <<<"$haystack"
}

# ---------------------------------------------------------------------------
# Resource group existence
# ---------------------------------------------------------------------------

rg_url() { printf 'https://management.azure.com/subscriptions/%s/resourceGroups/%s?api-version=%s' "$SUBSCRIPTION_ID" "$RG" "$API_VER_RG"; }

resources_in_rg_url() { printf 'https://management.azure.com/subscriptions/%s/resourceGroups/%s/resources?api-version=%s' "$SUBSCRIPTION_ID" "$RG" "$API_VER_RESOURCES"; }

# ---------------------------------------------------------------------------
# Resource-existence checks via single resource-list call
# ---------------------------------------------------------------------------

RESOURCES_CACHE="$TMPDIR_RESULTS/resources-cache"

# Fetch all resources in the RG once. Returns 0 if RG exists, 1 otherwise.
# Writes a TSV of "type<TAB>name" lines to $RESOURCES_CACHE on success.
# Parallel callers use flock to avoid clobbering each other mid-write.
load_resources() {
  if [[ -s "$RESOURCES_CACHE" ]]; then
    return 0
  fi
  if ! acquire_token; then
    return 1
  fi
  (
    umask 077
    flock -n 200 || {
      # Another worker is fetching; wait for it.
      flock 200
      [[ -s "$RESOURCES_CACHE" ]] && exit 0
      exit 1
    }
    # Re-check inside the lock: another worker may have just written it.
    if [[ -s "$RESOURCES_CACHE" ]]; then
      exit 0
    fi
    az_http_get "$(resources_in_rg_url)"
    if [[ "$AZ_HTTP_CODE" != "200" ]]; then
      exit 1
    fi
    # Atomic write: build the cache in a temp file, then move into place so
    # concurrent readers never see a half-written cache.
    local tmp_cache
    tmp_cache="$RESOURCES_CACHE.tmp.$$"
    if printf '%s' "$AZ_BODY" | jq -r '.value[]? | "\(.type)\t\(.name)"' 2>/dev/null > "$tmp_cache"; then
      mv "$tmp_cache" "$RESOURCES_CACHE"
    else
      rm -f "$tmp_cache"
      exit 1
    fi
    [[ -s "$RESOURCES_CACHE" ]]
  ) 200>"$TMPDIR_RESULTS/resources-cache.lock"
}

# Check whether a resource of given type+name exists in the cached list.
# Azure resource type strings are case-insensitive but the casing is
# non-deterministic (e.g. "serverFarms" vs "serverfarms"), so compare
# case-insensitively.
resource_exists() {
  local type="$1" name="$2"
  [[ -s "$RESOURCES_CACHE" ]] || return 1
  awk -F'\t' -v t="$type" -v n="$name" 'BEGIN{IGNORECASE=1} $1 == t && $2 == n { found=1 } END { exit !found }' "$RESOURCES_CACHE"
}

# Write one result line. Format: <group>|<name>|<status>|<detail>
write_result() {
  printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" > "$TMPDIR_RESULTS/$1-$2"
}

# ---------------------------------------------------------------------------
# Cloud checks
# ---------------------------------------------------------------------------

check_az_login() {
  if ! have_az; then
    write_result cloud az_login error "az CLI not on PATH; run inside infrastructure nix develop"
    return
  fi
  if ! acquire_token; then
    write_result cloud az_login error "az not logged in (run 'az login')"
    return
  fi
  local sub="${SUBSCRIPTION_ID:0:8}" tenant="${AZ_TENANT_ID:0:8}"
  write_result cloud az_login ok "sub ${sub}…, tenant ${tenant}…"
}

# Build a name from locals-style template using the given tenant suffix.
derive_sb_namespace() {
  local suffix="$1"
  printf 'sb-%s-%s-%s' "$PROJECT_NAME" "$ENV" "$suffix"
}

derive_keyvault_name() {
  local suffix="$1"
  local raw="kv${PROJECT_NAME//-/}${ENV}${suffix}"
  printf '%s' "${raw:0:24}"
}

derive_storage_name() { printf 'st%s%s' "${PROJECT_NAME//-/}" "$ENV"; }
derive_func_storage_name() { printf 'stfunc%s%s' "${PROJECT_NAME//-/}" "$ENV"; }

# Some checks need the tenant suffix. Use the tenant id captured during
# acquire_token; no extra az call needed.
get_tenant_suffix() {
  if [[ -n "$AZ_TENANT_ID" ]]; then
    printf '%s' "${AZ_TENANT_ID//-/}" | cut -c1-6
  else
    printf ''
  fi
}

check_resource_group() {
  if ! acquire_token; then
    write_result cloud resource_group error "az not available or not logged in"
    return
  fi
  az_http_get "$(rg_url)"
  if [[ "$AZ_HTTP_CODE" == "200" ]]; then
    local loc
    loc="$(printf '%s' "$AZ_BODY" | jq -r '.location // "unknown"' 2>/dev/null)"
    write_result cloud resource_group ok "${RG} in ${loc}"
  else
    write_result cloud resource_group missing "${RG} does not exist"
  fi
}

# All other cloud checks gate on the resource group via load_resources.
# If the RG is missing, load_resources returns 1 and we report missing.

rg_exists_quick() {
  [[ -s "$TMPDIR_RESULTS/cloud-resource_group" ]] || return 1
  local status
  IFS='|' read -r _ _ status _ < "$TMPDIR_RESULTS/cloud-resource_group"
  [[ "$status" == "ok" ]]
}

check_service_bus() {
  if ! acquire_token; then write_result cloud service_bus error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud service_bus error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud service_bus error "could not list resources in RG"; return; fi
  local sb_name
  sb_name="$(derive_sb_namespace "$(get_tenant_suffix)")"
  if ! resource_exists "Microsoft.ServiceBus/namespaces" "$sb_name"; then
    write_result cloud service_bus missing "${sb_name} does not exist"
    return
  fi
  # Namespace exists; check topics + subscriptions via REST.
  local topics_url="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${sb_name}/topics?api-version=${API_VER_SB}"
  az_http_get "$topics_url"
  if [[ "$AZ_HTTP_CODE" != "200" ]]; then
    write_result cloud service_bus partial "${sb_name} exists, could not list topics (HTTP ${AZ_HTTP_CODE})"
    return
  fi
  local topics_list
  topics_list="$(extract_names "$AZ_BODY")"
  local missing_topics=()
  for t in "$TOPIC_ANALYSIS_INPUT" "$TOPIC_ANALYSIS_RESULTS" "$TOPIC_FINAL_DECISIONS"; do
    if ! list_contains "$t" "$topics_list"; then missing_topics+=("$t"); fi
  done
  if [[ ${#missing_topics[@]} -gt 0 ]]; then
    write_result cloud service_bus partial "${sb_name} exists, missing topics: ${missing_topics[*]}"
    return
  fi
  # Check each subscription across all topics
  local missing_subs=()
  for s in "$SUB_ANALYSIS_AGENT" "$SUB_DECISION_AGENT" "$SUB_LOCAL_AGENT"; do
    local found=0
    for t in "$TOPIC_ANALYSIS_INPUT" "$TOPIC_ANALYSIS_RESULTS" "$TOPIC_FINAL_DECISIONS"; do
      local subs_url="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${sb_name}/topics/${t}/subscriptions?api-version=${API_VER_SB}"
      az_http_get "$subs_url"
      if [[ "$AZ_HTTP_CODE" == "200" ]] && list_contains "$s" "$(extract_names "$AZ_BODY")"; then
        found=1
        break
      fi
    done
    [[ $found -eq 0 ]] && missing_subs+=("$s")
  done
  if [[ ${#missing_subs[@]} -eq 0 ]]; then
    write_result cloud service_bus ok "${sb_name} (3 topics, 3 subscriptions)"
  else
    write_result cloud service_bus partial "${sb_name} exists, missing subs: ${missing_subs[*]}"
  fi
}

check_cosmos() {
  if ! acquire_token; then write_result cloud cosmos error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud cosmos error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud cosmos error "could not list resources in RG"; return; fi
  if ! resource_exists "Microsoft.DocumentDB/databaseAccounts" "$COSMOS_ACCOUNT"; then
    write_result cloud cosmos missing "${COSMOS_ACCOUNT} does not exist"
    return
  fi
  # List containers under the SQL database
  local url="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.DocumentDB/databaseAccounts/${COSMOS_ACCOUNT}/sqlDatabases/${COSMOS_DB}/containers?api-version=${API_VER_COSMOS}"
  az_http_get "$url"
  if [[ "$AZ_HTTP_CODE" != "200" ]]; then
    write_result cloud cosmos partial "${COSMOS_ACCOUNT} exists, could not list containers (HTTP ${AZ_HTTP_CODE})"
    return
  fi
  local present
  present="$(extract_names "$AZ_BODY")"
  local missing=()
  for c in "${COSMOS_CONTAINERS[@]}"; do
    if ! list_contains "$c" "$present"; then missing+=("$c"); fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    write_result cloud cosmos ok "${COSMOS_ACCOUNT} (db ${COSMOS_DB}, ${#COSMOS_CONTAINERS[@]} containers)"
  else
    write_result cloud cosmos partial "${COSMOS_ACCOUNT}: missing containers: ${missing[*]}"
  fi
}

check_logs_storage() {
  if ! acquire_token; then write_result cloud logs_storage error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud logs_storage error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud logs_storage error "could not list resources in RG"; return; fi
  local name
  name="$(derive_storage_name)"
  if resource_exists "Microsoft.Storage/storageAccounts" "$name"; then
    write_result cloud logs_storage ok "${name}"
  else
    write_result cloud logs_storage missing "${name} does not exist"
  fi
}

check_function_storage() {
  if ! acquire_token; then write_result cloud function_storage error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud function_storage error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud function_storage error "could not list resources in RG"; return; fi
  local name
  name="$(derive_func_storage_name)"
  if resource_exists "Microsoft.Storage/storageAccounts" "$name"; then
    write_result cloud function_storage ok "${name}"
  else
    write_result cloud function_storage missing "${name} does not exist"
  fi
}

check_key_vault() {
  if ! acquire_token; then write_result cloud key_vault error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud key_vault error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud key_vault error "could not list resources in RG"; return; fi
  local kv
  kv="$(derive_keyvault_name "$(get_tenant_suffix)")"
  if ! resource_exists "Microsoft.KeyVault/vaults" "$kv"; then
    write_result cloud key_vault missing "${kv} does not exist"
    return
  fi
  if [[ $QUICK -eq 1 ]]; then
    write_result cloud key_vault ok "${kv}"
    return
  fi
  local url="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.KeyVault/vaults/${kv}/secrets?api-version=${API_VER_KV}"
  az_http_get "$url"
  if [[ "$AZ_HTTP_CODE" != "200" ]]; then
    write_result cloud key_vault partial "${kv} exists, could not list secrets (HTTP ${AZ_HTTP_CODE})"
    return
  fi
  local present
  present="$(extract_names "$AZ_BODY")"
  local missing=()
  for s in "${KEYVAULT_SECRETS[@]}"; do
    if ! list_contains "$s" "$present"; then missing+=("$s"); fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    write_result cloud key_vault ok "${kv} (${#KEYVAULT_SECRETS[@]}/${#KEYVAULT_SECRETS[@]} secrets)"
  else
    write_result cloud key_vault partial "${kv}: missing secrets: ${missing[*]}"
  fi
}

check_app_insights() {
  if ! acquire_token; then write_result cloud app_insights error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud app_insights error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud app_insights error "could not list resources in RG"; return; fi
  local name="appi-${PROJECT_NAME}-${ENV}"
  if resource_exists "Microsoft.Insights/components" "$name"; then
    write_result cloud app_insights ok "${name}"
  else
    write_result cloud app_insights missing "${name} does not exist"
  fi
}

check_app_plan() {
  if ! acquire_token; then write_result cloud app_plan error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud app_plan error "resource group ${RG} missing"; return; fi
  if ! load_resources; then write_result cloud app_plan error "could not list resources in RG"; return; fi
  local name="plan-${PROJECT_NAME}-${ENV}"
  # resource_exists() is already case-insensitive, so this matches both
  # "Microsoft.Web/serverfarms" and the actual "Microsoft.Web/serverFarms".
  if resource_exists "Microsoft.Web/serverfarms" "$name"; then
    write_result cloud app_plan ok "${name}"
  else
    write_result cloud app_plan missing "${name} does not exist"
  fi
}

check_function_apps() {
  if ! acquire_token; then write_result cloud function_apps error "az not available"; return; fi
  if ! rg_exists_quick; then write_result cloud function_apps error "resource group ${RG} missing"; return; fi
  local url="https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.Web/sites?api-version=${API_VER_WEB}"
  az_http_get "$url"
  if [[ "$AZ_HTTP_CODE" != "200" ]]; then
    write_result cloud function_apps error "could not list sites (HTTP ${AZ_HTTP_CODE})"
    return
  fi
  # Filter to function apps (kind contains 'functionapp' or 'functionapp,linux')
  local present
  present="$(printf '%s' "$AZ_BODY" | jq -r '.value[]? | select(.kind | tostring | test("functionapp")) | .name' 2>/dev/null || true)"
  local found=0 missing_names=()
  for a in "${FUNCTION_APPS[@]}"; do
    if list_contains "$a" "$present"; then
      found=$((found + 1))
    else
      missing_names+=("$a")
    fi
  done
  if [[ $found -eq 4 ]]; then
    write_result cloud function_apps ok "4/4 (token, router, analysis, decision)"
  elif [[ $found -eq 0 ]]; then
    write_result cloud function_apps missing "0/4 function apps deployed"
  else
    write_result cloud function_apps partial "$found/4 deployed, missing: ${missing_names[*]}"
  fi
}

# ---------------------------------------------------------------------------
# Code-artifact checks (do not require az)
# ---------------------------------------------------------------------------

check_code_artifacts() {
  local build_dir="$ROOT_DIR/.build/functions"
  local expected=(token.zip router.zip analysis.zip decision.zip)
  local present=() missing=()
  for z in "${expected[@]}"; do
    if [[ -f "$build_dir/$z" ]]; then
      present+=("$z")
    else
      missing+=("$z")
    fi
  done
  if [[ ${#present[@]} -eq 4 ]]; then
    write_result local code_artifacts ok "$build_dir contains all 4 zip packages"
  elif [[ ${#present[@]} -eq 0 ]]; then
    write_result local code_artifacts missing "no zip packages built (run scripts/deploy-functions.sh)"
  else
    write_result local code_artifacts partial "$((4 - ${#missing[@]}))/4 zip packages: present=${present[*]}, missing=${missing[*]}"
  fi
}

# ---------------------------------------------------------------------------
# VM checks
# ---------------------------------------------------------------------------

check_qemu_process() {
  local pid
  pid="$(pgrep -f "qemu-kvm.*-name nixos" 2>/dev/null | head -1 || true)"
  if [[ -n "$pid" ]]; then
    write_result vm qemu ok "PID $pid"
  else
    write_result vm qemu missing "no qemu-kvm process with -name nixos"
  fi
}

check_ssh_port() {
  local host="$VM_HOST" port="$VM_PORT"
  if timeout 2 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
    write_result vm ssh_port ok "${host}:${port} reachable"
  else
    write_result vm ssh_port missing "${host}:${port} not reachable"
  fi
}

check_vm_config_repo() {
  if [[ ! -d "$CONFIG_REPO" ]]; then
    write_result vm config_repo missing "${CONFIG_REPO} not found"
    return
  fi
  local has_qcow2=0 has_nix=0 has_env=0
  [[ -f "$CONFIG_REPO/nixos.qcow2" ]] && has_qcow2=1
  [[ -f "$CONFIG_REPO/phoe-services.nix" ]] && has_nix=1
  [[ -f "$CONFIG_REPO/local-agent.env" || -f "$CONFIG_REPO/log-service.env" ]] && has_env=1
  if [[ $has_qcow2 -eq 1 && $has_nix -eq 1 && $has_env -eq 1 ]]; then
    write_result vm config_repo ok "qcow2 + phoe-services.nix + env files present"
  elif [[ $has_qcow2 -eq 1 && $has_nix -eq 1 ]]; then
    write_result vm config_repo partial "qcow2 + phoe-services.nix present; env files not rendered (run render-vm-env.sh)"
  else
    write_result vm config_repo missing "qcow2=$has_qcow2 phoe-services.nix=$has_nix env=$has_env"
  fi
}

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

collect_results() {
  # Preserve insertion order: sort by group then by check name
  for f in "$TMPDIR_RESULTS"/*; do
    [[ -f "$f" ]] || continue
    case "$(basename "$f")" in
      azure-token|resources-cache) continue ;;
    esac
    cat "$f"
  done | sort -t'|' -k1,1 -k2,2
}

render_human() {
  local current_group=""
  local line grp name status detail
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    IFS='|' read -r grp name status detail <<<"$line"
    if [[ "$grp" != "$current_group" ]]; then
      echo
      echo "[$grp]"
      current_group="$grp"
    fi
    local marker
    case "$status" in
      ok)      marker="✓" ;;
      partial) marker="~" ;;
      missing) marker="✗" ;;
      error)   marker="!" ;;
      *)       marker="?" ;;
    esac
    printf '  %s %-20s %-9s %s\n' "$marker" "$name" "$status" "$detail"
  done < <(collect_results)

  # Summary
  echo
  echo "[summary]"
  local cloud_total=0 cloud_ok=0 vm_total=0 vm_ok=0 local_total=0 local_ok=0
  while IFS='|' read -r grp _ status _; do
    case "$grp" in
      cloud)
        cloud_total=$((cloud_total + 1))
        [[ "$status" == "ok" ]] && cloud_ok=$((cloud_ok + 1)) ;;
      vm)
        vm_total=$((vm_total + 1))
        [[ "$status" == "ok" ]] && vm_ok=$((vm_ok + 1)) ;;
      local)
        local_total=$((local_total + 1))
        [[ "$status" == "ok" ]] && local_ok=$((local_ok + 1)) ;;
    esac
  done < <(collect_results)
  echo "  cloud: ${cloud_ok}/${cloud_total} components fully deployed"
  echo "  vm:   ${vm_ok}/${vm_total} components ready"
  [[ $local_total -gt 0 ]] && echo "  local: ${local_ok}/${local_total} components ready"
}

render_json() {
  local groups_json=""
  local current_group=""
  local current_items=""
  local line grp name status detail
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    IFS='|' read -r grp name status detail <<<"$line"
    if [[ "$grp" != "$current_group" ]]; then
      if [[ -n "$current_group" ]]; then
        groups_json+="    \"$current_group\": {${current_items%,}},"
      fi
      current_group="$grp"
      current_items=""
    fi
    local safe_detail
    safe_detail="$(printf '%s' "$detail" | jq -R .)"
    current_items+="\"$name\": {\"status\": \"$status\", \"detail\": $safe_detail},"
  done < <(collect_results)
  if [[ -n "$current_group" ]]; then
    groups_json+="    \"$current_group\": {${current_items%,}}"
  fi

  cat <<EOF
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "config": {
    "project_name": "${PROJECT_NAME}",
    "env": "${ENV}",
    "resource_group": "${RG}",
    "vm_host": "${VM_HOST}",
    "vm_port": ${VM_PORT}
  },
  "groups": {
$groups_json
  }
}
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  # Phase 1: az login + resource group (sequential; everything else gates
  # on the RG being present).
  check_az_login
  check_resource_group

  # Phase 2: parallel cloud checks. Each internally short-circuits if the
  # resource group is missing.
  check_service_bus &
  check_cosmos &
  check_logs_storage &
  check_function_storage &
  check_key_vault &
  check_app_insights &
  check_app_plan &
  if [[ $QUICK -eq 0 ]]; then
    check_function_apps &
  fi

  # Phase 3: local checks (no az, fast) — overlap with phase 2.
  check_code_artifacts &
  check_qemu_process &
  check_ssh_port &
  check_vm_config_repo &

  wait

  if [[ "$OUTPUT_MODE" == "json" ]]; then
    render_json
  else
    render_human
  fi
}

main
