#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
RUNTIME_DIR="$ROOT_DIR/simulator"
PYTHON_SCRIPT="$ROOT_DIR/scripts/run-live-azure-vm-e2e.py"

if [[ -f "$RUNTIME_DIR/flake.nix" ]] && command -v nix >/dev/null 2>&1; then
  (
    cd "$RUNTIME_DIR"
    nix develop --no-write-lock-file --command bash -lc 'uv sync --refresh >/dev/null && uv run "$1" "${@:2}"' bash "$PYTHON_SCRIPT" "$@"
  )
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  (
    cd "$RUNTIME_DIR"
    uv sync --refresh >/dev/null
    uv run "$PYTHON_SCRIPT" "$@"
  )
  exit 0
fi

printf 'No supported runtime found for live Azure/VM end-to-end script\n' >&2
exit 1
