#!/usr/bin/env bash
set -euo pipefail

runner_spec="$(systemctl show local_agent --property=ExecStart --value)"
runner_path="${runner_spec#\{ path=}"
runner_path="${runner_path%% ;*}"

preexisting_git_ssh_command="${GIT_SSH_COMMAND-}"
preexisting_repo_url="${CONFIG_REPO_URL-}"
preexisting_repo_branch="${CONFIG_REPO_BRANCH-}"
preexisting_rebuild_test_command="${REBUILD_TEST_COMMAND-}"
preexisting_rebuild_switch_command="${REBUILD_SWITCH_COMMAND-}"

python_bin="$(grep '^exec ' "$runner_path" | cut -d' ' -f2)"
python_path="$(grep '^export PYTHONPATH=' "$runner_path" | cut -d'"' -f2)"
path_prefix="$(grep '^export PATH=' "$runner_path" | cut -d'"' -f2)"

export PATH="$path_prefix:$PATH"
export PYTHONPATH="$python_path"

set -a
. <(grep -v '^GIT_SSH_COMMAND=' /etc/phoe-nix/local-agent.env.defaults)
if [[ -f /etc/phoe-nix/local-agent.env ]]; then
  . <(grep -v '^GIT_SSH_COMMAND=' /etc/phoe-nix/local-agent.env)
fi
set +a

if [[ -n "$preexisting_git_ssh_command" ]]; then
  export GIT_SSH_COMMAND="$preexisting_git_ssh_command"
fi
if [[ -n "$preexisting_repo_url" ]]; then
  export CONFIG_REPO_URL="$preexisting_repo_url"
fi
if [[ -n "$preexisting_repo_branch" ]]; then
  export CONFIG_REPO_BRANCH="$preexisting_repo_branch"
fi
if [[ -n "$preexisting_rebuild_test_command" ]]; then
  export REBUILD_TEST_COMMAND="$preexisting_rebuild_test_command"
fi
if [[ -n "$preexisting_rebuild_switch_command" ]]; then
  export REBUILD_SWITCH_COMMAND="$preexisting_rebuild_switch_command"
fi

export NODE_ID="${NODE_ID:-nixos}"
export LOCAL_AGENT_MANUAL_REPO_PATH="${LOCAL_AGENT_MANUAL_REPO_PATH:-/tmp/phoe-nix-manual-test}"

exec "$python_bin" -c 'from local_agent.manual_integration import run_manual_integration; import json; print(json.dumps(run_manual_integration(), indent=2, sort_keys=True))'
