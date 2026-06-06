from __future__ import annotations

import json
import os
import tempfile

from local_agent.config import load_config
from local_agent.executor import CommandResult, run_subprocess
from local_agent.git_repo import commit_and_push, current_revision, read_config_text, refresh_repo, write_config_text
from local_agent.ollama_client import generate_text
from local_agent.repair_planner import execute_repair_loop
from schemas import Decision, NodeState


def _manual_command_runner() -> callable:
    if os.environ.get("LOCAL_AGENT_MANUAL_REAL_REBUILD") == "1":
        return lambda command: run_subprocess(command, timeout_seconds=300)

    state = {"test_calls": 0}

    def runner(command: str) -> CommandResult:
        if command == "nixos-rebuild test":
            state["test_calls"] += 1
            if state["test_calls"] == 1:
                return CommandResult(returncode=1, stdout="", stderr="simulated syntax error")
            return CommandResult(returncode=0, stdout="simulated test ok", stderr="")
        if command == "nixos-rebuild switch":
            return CommandResult(returncode=0, stdout="simulated switch ok", stderr="")
        return CommandResult(returncode=0, stdout=f"executed: {command}", stderr="")

    return runner


def run_manual_integration() -> dict[str, object]:
    config = load_config()
    repo_path = os.environ.get("LOCAL_AGENT_MANUAL_REPO_PATH") or tempfile.mkdtemp(prefix="phoe-nix-manual-")
    refresh_repo(repo_url=config.config_repo_url, repo_path=repo_path, branch=config.config_repo_branch)
    before_revision = current_revision(repo_path=repo_path)
    before_config = read_config_text(repo_path=repo_path, config_file_path=config.config_file_path)

    decision = Decision.model_validate(
        {
            "decision_id": "manual-integration",
            "node_id": config.node_id,
            "analysis_id": "manual-analysis",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": os.environ.get(
                "LOCAL_AGENT_MANUAL_ANALYSIS_SUMMARY",
                "SSH connectivity is failing; inspect configuration.nix and repair it.",
            ),
            "remediation_text": os.environ.get(
                "LOCAL_AGENT_MANUAL_REMEDIATION_TEXT",
                "Enable the necessary SSH-related configuration in configuration.nix.",
            ),
            "idempotency_key": "manual-integration",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )

    outcome = execute_repair_loop(
        decision=decision,
        node_state=NodeState(failed_units=["sshd.service"]),
        repo_url=config.config_repo_url,
        repo_path=repo_path,
        branch=config.config_repo_branch,
        config_file_path=config.config_file_path,
        max_attempts=config.repair_max_attempts,
        rebuild_test_command=config.rebuild_test_command,
        rebuild_switch_command=config.rebuild_switch_command,
        llm_generate=lambda prompt: generate_text(
            base_url=config.ollama_base_url,
            model=config.ollama_model,
            prompt=prompt,
            timeout_seconds=config.ollama_timeout_seconds,
        ),
        command_runner=_manual_command_runner(),
        commit_and_push_func=commit_and_push
        if os.environ.get("LOCAL_AGENT_MANUAL_REAL_PUSH") == "1"
        else lambda **kwargs: (True, "simulated push"),
    )
    after_config = read_config_text(repo_path=repo_path, config_file_path=config.config_file_path)
    return {
        "repo_path": repo_path,
        "before_revision": before_revision,
        "after_revision": outcome.repo_revision_after,
        "attempt_count": len(outcome.attempts),
        "success": outcome.success,
        "before_config_excerpt": before_config[:200],
        "after_config_excerpt": after_config[:200],
        "stderr": outcome.stderr,
        "stdout": outcome.stdout,
    }


def main() -> None:
    print(json.dumps(run_manual_integration(), indent=2, sort_keys=True))
