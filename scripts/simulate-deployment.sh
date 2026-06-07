#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
SIMULATOR_DIR="$ROOT_DIR/simulator"

if [[ -f "$SIMULATOR_DIR/flake.nix" ]] && command -v nix >/dev/null 2>&1; then
  if git -C "$ROOT_DIR" ls-files --error-unmatch "simulator" >/dev/null 2>&1; then
    (
      cd "$SIMULATOR_DIR"
      nix develop --no-write-lock-file --command sh -c "uv sync --refresh >/dev/null && uv run simulate_pipeline"
    )
    exit 0
  fi

  nix shell nixpkgs#uv nixpkgs#python314 nixpkgs#pkg-config nixpkgs#systemd.dev --command sh -c \
    "cd '$SIMULATOR_DIR' && uv sync --refresh >/dev/null && uv run simulate_pipeline"
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  (
    cd "$SIMULATOR_DIR"
    uv sync --refresh >/dev/null
    uv run simulate_pipeline
  )
  exit 0
fi

printf 'No supported runtime found for simulator\n' >&2
exit 1
