#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
LOCAL_AGENT_DIR="$ROOT_DIR/local_agent"

if [[ -f "$LOCAL_AGENT_DIR/flake.nix" ]] && command -v nix >/dev/null 2>&1; then
  nix shell nixpkgs#uv nixpkgs#python314 --command sh -c \
    "cd '$LOCAL_AGENT_DIR' && uv sync --refresh >/dev/null && uv run local_agent_manual_integration"
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  (
    cd "$LOCAL_AGENT_DIR"
    uv sync --refresh >/dev/null
    uv run local_agent_manual_integration
  )
  exit 0
fi

printf 'No supported runtime found for local_agent manual integration\n' >&2
exit 1
