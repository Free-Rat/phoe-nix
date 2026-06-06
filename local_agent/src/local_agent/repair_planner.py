from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from schemas import Decision, NodeState

from local_agent.executor import CommandResult, run_subprocess
from local_agent.git_repo import commit_and_push, current_revision, read_config_text, refresh_repo, write_config_text


@dataclass(frozen=True)
class RepairAttempt:
    attempt_number: int
    prompt: str
    model_response: str
    previous_config: str
    proposed_config: str
    test_command: str
    test_result: CommandResult
    switch_command: str | None = None
    switch_result: CommandResult | None = None
    push_success: bool = False
    push_message: str = ""


@dataclass(frozen=True)
class RepairOutcome:
    success: bool
    executed_command: str
    stdout: str
    stderr: str
    repo_revision_before: str
    repo_revision_after: str
    attempts: list[RepairAttempt]
    final_config_text: str


FENCED_BLOCK_PATTERN = re.compile(r"```(?:nix|json)?\s*(.*?)```", re.DOTALL)


def build_repair_prompt(
    *,
    decision: Decision,
    node_state: NodeState,
    current_config: str,
    previous_error: str | None,
    attempt_number: int,
) -> str:
    parts = [
        "You are repairing a NixOS node configuration.",
        f"Attempt: {attempt_number}",
        f"Decision summary: {decision.analysis_summary}",
        f"Remediation text: {decision.remediation_text}",
        f"Node state: {node_state.model_dump_json(indent=2)}",
        "Return the full replacement content of configuration.nix.",
        "You may return raw Nix text, a fenced nix block, or JSON with updated_config_text.",
        "Current configuration.nix:",
        current_config,
    ]
    if previous_error:
        parts.extend(["Previous nixos-rebuild test failure:", previous_error])
    return "\n\n".join(parts)


def extract_config_text(response_text: str) -> str:
    stripped = response_text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            updated = payload.get("updated_config_text")
            if isinstance(updated, str) and updated.strip():
                return updated.strip()
    fence_match = FENCED_BLOCK_PATTERN.search(response_text)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped


def sync_local_hardware_configuration(*, repo_path: str) -> None:
    source_path = Path("/etc/nixos/hardware-configuration.nix")
    destination_path = Path(repo_path) / "hardware-configuration.nix"
    if destination_path.exists() or not source_path.exists():
        return
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")


def execute_repair_loop(
    *,
    decision: Decision,
    node_state: NodeState,
    repo_url: str,
    repo_path: str,
    branch: str,
    config_file_path: str,
    max_attempts: int,
    rebuild_test_command: str,
    rebuild_switch_command: str,
    llm_generate: Callable[[str], str],
    command_runner: Callable[[str], CommandResult] | None = None,
    refresh_repo_func=refresh_repo,
    read_config_func=read_config_text,
    write_config_func=write_config_text,
    current_revision_func=current_revision,
    commit_and_push_func=commit_and_push,
    sync_repo_support_files_func=sync_local_hardware_configuration,
) -> RepairOutcome:
    runner = command_runner or (lambda command: run_subprocess(command, timeout_seconds=300))
    refresh_repo_func(repo_url=repo_url, repo_path=repo_path, branch=branch)
    sync_repo_support_files_func(repo_path=repo_path)
    repo_revision_before = current_revision_func(repo_path=repo_path)
    current_config = read_config_func(repo_path=repo_path, config_file_path=config_file_path)
    attempts: list[RepairAttempt] = []
    previous_error: str | None = None
    latest_revision = repo_revision_before

    for attempt_number in range(1, max_attempts + 1):
        prompt = build_repair_prompt(
            decision=decision,
            node_state=node_state,
            current_config=current_config,
            previous_error=previous_error,
            attempt_number=attempt_number,
        )
        model_response = llm_generate(prompt)
        proposed_config = extract_config_text(model_response)
        write_config_func(repo_path=repo_path, config_file_path=config_file_path, content=proposed_config)
        test_result = runner(rebuild_test_command)
        attempt = RepairAttempt(
            attempt_number=attempt_number,
            prompt=prompt,
            model_response=model_response,
            previous_config=current_config,
            proposed_config=proposed_config,
            test_command=rebuild_test_command,
            test_result=test_result,
        )
        attempts.append(attempt)
        if test_result.returncode != 0:
            previous_error = (test_result.stdout + "\n" + test_result.stderr).strip()
            current_config = proposed_config
            continue

        switch_result = runner(rebuild_switch_command)
        attempts[-1] = RepairAttempt(
            attempt_number=attempt.attempt_number,
            prompt=attempt.prompt,
            model_response=attempt.model_response,
            previous_config=attempt.previous_config,
            proposed_config=attempt.proposed_config,
            test_command=attempt.test_command,
            test_result=attempt.test_result,
            switch_command=rebuild_switch_command,
            switch_result=switch_result,
        )
        if switch_result.returncode != 0:
            latest_revision = current_revision_func(repo_path=repo_path)
            return RepairOutcome(
                success=False,
                executed_command=rebuild_switch_command,
                stdout=switch_result.stdout,
                stderr=switch_result.stderr,
                repo_revision_before=repo_revision_before,
                repo_revision_after=latest_revision,
                attempts=attempts,
                final_config_text=proposed_config,
            )

        push_success, push_message = commit_and_push_func(
            repo_path=repo_path,
            config_file_path=config_file_path,
            branch=branch,
            commit_message=f"phoe-nix repair {decision.decision_id}",
        )
        attempts[-1] = RepairAttempt(
            attempt_number=attempts[-1].attempt_number,
            prompt=attempts[-1].prompt,
            model_response=attempts[-1].model_response,
            previous_config=attempts[-1].previous_config,
            proposed_config=attempts[-1].proposed_config,
            test_command=attempts[-1].test_command,
            test_result=attempts[-1].test_result,
            switch_command=attempts[-1].switch_command,
            switch_result=attempts[-1].switch_result,
            push_success=push_success,
            push_message=push_message,
        )
        if push_success:
            latest_revision = current_revision_func(repo_path=repo_path)
            return RepairOutcome(
                success=True,
                executed_command=f"{rebuild_test_command} && {rebuild_switch_command}",
                stdout=(switch_result.stdout + "\n" + push_message).strip(),
                stderr=switch_result.stderr,
                repo_revision_before=repo_revision_before,
                repo_revision_after=latest_revision,
                attempts=attempts,
                final_config_text=proposed_config,
            )

        previous_error = f"Git push failed: {push_message}"
        refresh_repo_func(repo_url=repo_url, repo_path=repo_path, branch=branch)
        current_config = read_config_func(repo_path=repo_path, config_file_path=config_file_path)

    latest_revision = current_revision_func(repo_path=repo_path)
    return RepairOutcome(
        success=False,
        executed_command=rebuild_test_command,
        stdout="",
        stderr=previous_error or "repair attempts exhausted",
        repo_revision_before=repo_revision_before,
        repo_revision_after=latest_revision,
        attempts=attempts,
        final_config_text=current_config,
    )
