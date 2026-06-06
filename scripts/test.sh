#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
SERVICES=(schemas token_service log_service log_router analysis_agent decision_agent local_agent simulator)

run_tests() {
  local service_path="$1"
  local service_name
  local python_package="nixpkgs#python311"
  local extra_nix_packages=()
  service_name="$(basename "$service_path")"

  if [[ "$service_name" == "simulator" ]]; then
    python_package="nixpkgs#python314"
    extra_nix_packages+=("nixpkgs#pkg-config" "nixpkgs#systemd.dev")
  fi

  if [[ "$service_name" == "local_agent" ]]; then
    python_package="nixpkgs#python314"
  fi

  if [[ -f "$service_path/flake.nix" ]] && command -v nix >/dev/null 2>&1; then
    if git -C "$ROOT_DIR" ls-files --error-unmatch "$service_name" >/dev/null 2>&1 && (
      cd "$service_path"
      nix develop --command sh -c "uv sync --refresh >/dev/null && uv run python -m unittest discover -s tests -p 'test*.py'"
    ); then
      return
    fi

    nix shell nixpkgs#uv "$python_package" "${extra_nix_packages[@]}" --command sh -c \
      "cd '$service_path' && uv sync --refresh >/dev/null && uv run python -m unittest discover -s tests -p 'test*.py'"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    (
      cd "$service_path"
      uv sync --refresh >/dev/null
      uv run python -m unittest discover -s tests -p 'test*.py'
    )
    return
  fi

  echo "No supported test runner found for $service_path" >&2
  exit 1
}

for service in "${SERVICES[@]}"; do
  if [[ ! -d "$ROOT_DIR/$service" ]]; then
    continue
  fi

  if [[ ! -d "$ROOT_DIR/$service/tests" ]]; then
    continue
  fi

  echo "==> Testing $service"
  run_tests "$ROOT_DIR/$service"
done
