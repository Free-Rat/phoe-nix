#!/usr/bin/env bash
set -euo pipefail

NODE_SOURCE_ROOT="${NODE_SOURCE_ROOT:-/home/user/phoe-nix-node-src}"

cd "$NODE_SOURCE_ROOT"
set -a
. "$NODE_SOURCE_ROOT/scripts/mock-local-agent.env"
set +a

export NODE_ID="${NODE_ID:-nixos}"
export LOCAL_AGENT_RUN_MODE=daemon
export PYTHONPATH="$NODE_SOURCE_ROOT/local_agent/src:$NODE_SOURCE_ROOT/schemas/src"

exec /nix/store/12ssf28w7zvg3g6ms7hnxsfap2cpd5h5-python3-3.11.11-env/bin/python -m local_agent.main
