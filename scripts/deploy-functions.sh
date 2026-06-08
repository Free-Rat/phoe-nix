#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <resource-group> <environment> <service> [<service> ...]" >&2
  echo "services: token router analysis decision" >&2
  exit 1
fi

ROOT_DIR="$(git rev-parse --show-toplevel)"
RESOURCE_GROUP="$1"
ENVIRONMENT="$2"
shift 2
PROJECT_NAME="${PROJECT_NAME:-project-healer}"

declare -A SERVICE_DIRS=(
  [token]="token_service"
  [router]="log_router"
  [analysis]="analysis_agent"
  [decision]="decision_agent"
)

declare -A FUNCTION_NAMES=(
  [token]="func-${PROJECT_NAME}-${ENVIRONMENT}-token"
  [router]="func-${PROJECT_NAME}-${ENVIRONMENT}-router"
  [analysis]="func-${PROJECT_NAME}-${ENVIRONMENT}-analysis"
  [decision]="func-${PROJECT_NAME}-${ENVIRONMENT}-decision"
)

BUILD_DIR="$ROOT_DIR/.build/functions"
mkdir -p "$BUILD_DIR"

export_requirements() {
  local service_path="$1"
  local output_path="$2"
  local service_name
  service_name="$(basename "$service_path")"

  if [[ -f "$service_path/flake.nix" ]] && command -v nix >/dev/null 2>&1; then
    if git -C "$ROOT_DIR" ls-files --error-unmatch "$service_name" >/dev/null 2>&1 && (
      cd "$service_path"
      nix develop --no-write-lock-file --command sh -c "uv export --no-hashes --format requirements-txt > '$output_path'"
    ); then
      return
    fi

    nix shell nixpkgs#uv nixpkgs#python311 --command sh -c \
      "cd '$service_path' && uv export --no-hashes --format requirements-txt > '$output_path'"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    (
      cd "$service_path"
      uv export --no-hashes --format requirements-txt > "$output_path"
    )
    return
  fi

  echo "uv is required to export requirements for $service_path" >&2
  exit 1
}

for service in "$@"; do
  SERVICE_DIR="${SERVICE_DIRS[$service]:-}"
  FUNCTION_NAME="${FUNCTION_NAMES[$service]:-}"

  if [[ -z "$SERVICE_DIR" || -z "$FUNCTION_NAME" ]]; then
    echo "unknown service: $service" >&2
    exit 1
  fi

  SERVICE_PATH="$ROOT_DIR/$SERVICE_DIR"
  ARCHIVE_PATH="$BUILD_DIR/${service}.zip"
  STAGING_PATH="$BUILD_DIR/${service}"

  rm -rf "$STAGING_PATH" "$ARCHIVE_PATH"
  mkdir -p "$STAGING_PATH"

  export_requirements "$SERVICE_PATH" "$STAGING_PATH/requirements.txt"

  cp -R "$SERVICE_PATH/src/." "$STAGING_PATH/"
  find "$STAGING_PATH" -type d -name '__pycache__' -prune -exec rm -rf {} +

  cat > "$STAGING_PATH/host.json" <<'EOF'
{
  "version": "2.0",
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.0.0, 5.0.0)"
  }
}
EOF

  if [[ "$service" == "router" ]]; then
    mkdir -p "$STAGING_PATH/schemas"
    cp -R "$ROOT_DIR/schemas/src/schemas/." "$STAGING_PATH/schemas/"
  fi

  if [[ "$service" == "analysis" || "$service" == "decision" ]]; then
    mkdir -p "$STAGING_PATH/schemas"
    cp -R "$ROOT_DIR/schemas/src/schemas/." "$STAGING_PATH/schemas/"
  fi

  if [[ -f "$STAGING_PATH/requirements.txt" ]]; then
    FILTERED_REQUIREMENTS="$STAGING_PATH/requirements.filtered.txt"
    : > "$FILTERED_REQUIREMENTS"
    while IFS= read -r line; do
      case "$line" in
        schemas\ @\ file:*|schemas==*|-e\ ../schemas|-e\ .|../schemas)
          continue
          ;;
      esac
      printf '%s\n' "$line" >> "$FILTERED_REQUIREMENTS"
    done < "$STAGING_PATH/requirements.txt"
    mv "$FILTERED_REQUIREMENTS" "$STAGING_PATH/requirements.txt"
  fi

  (
    cd "$STAGING_PATH"
    zip -qr "$ARCHIVE_PATH" .
  )

  az functionapp deployment source config-zip \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FUNCTION_NAME" \
    --src "$ARCHIVE_PATH" \
    --build-remote true
done
