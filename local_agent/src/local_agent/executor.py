import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from schemas import Decision, NodeState

from local_agent.reporter import build_execution_result, current_time
from local_agent.state import LocalAgentState, can_apply_remediation, record_remediation, start_remediation


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def validate_decision_target(decision: Decision, node_id: str) -> bool:
    return decision.node_id == node_id


def derive_execution_command(decision: Decision) -> str:
    if decision.command:
        return decision.command
    if decision.action == "no_action":
        return ""
    return ""


def run_subprocess(command: str, *, timeout_seconds: int) -> CommandResult:
    completed = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=timeout_seconds, check=False
    )
    return CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def execute_decision(
    *,
    decision: Decision,
    state: LocalAgentState,
    local_node_id: str,
    cooldown_seconds: int,
    max_remediations_per_hour: int,
    timeout_seconds: int,
    command_runner: Callable[[str], CommandResult] | None = None,
    now_factory: Callable[[], datetime] = current_time,
    node_state_after_factory: Callable[[Decision], NodeState] | None = None,
):
    now = now_factory()
    if not validate_decision_target(decision, local_node_id):
        return state, None, "decision targeted at another node"

    execution_command = derive_execution_command(decision)
    if not execution_command and decision.action != "no_action":
        return state, None, "decision produced no executable repair plan"
    if not can_apply_remediation(
        state,
        cooldown_seconds=cooldown_seconds,
        max_remediations_per_hour=max_remediations_per_hour,
        now=now,
    ):
        return state, None, "remediation is blocked by safety limits"

    active_state = start_remediation(state)
    started_at = now
    if decision.action == "no_action":
        command_result = CommandResult(returncode=0, stdout="no action executed", stderr="")
    else:
        runner = command_runner or (lambda command: run_subprocess(command, timeout_seconds=timeout_seconds))
        command_result = runner(execution_command)

    node_state_after = (
        node_state_after_factory(decision)
        if node_state_after_factory is not None
        else NodeState(failed_units=[] if command_result.returncode == 0 else state.node_state.failed_units)
    )
    completed_at = now_factory()
    execution_result = build_execution_result(
        decision=decision,
        executed_command=execution_command,
        exit_code=command_result.returncode,
        stdout=command_result.stdout,
        stderr=command_result.stderr,
        started_at=started_at,
        completed_at=completed_at,
        node_state_after=node_state_after,
    )
    next_state = record_remediation(active_state, now=completed_at, node_state_after=node_state_after)
    return next_state, execution_result, None
