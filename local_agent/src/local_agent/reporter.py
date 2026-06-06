from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from schemas import Decision, ExecutionResult, NodeState


def summarize_execution(node_state_after: NodeState) -> str:
    if node_state_after.failed_units:
        return f"Execution completed. Remaining failed units: {', '.join(node_state_after.failed_units)}."
    return "Execution completed. No failed units remain."


def build_execution_result(
    *,
    decision: Decision,
    executed_command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    started_at: datetime,
    completed_at: datetime,
    node_state_after: NodeState,
    uuid_factory: Callable[[], UUID] | None = None,
) -> ExecutionResult:
    execution_id = str(uuid_factory() if uuid_factory is not None else uuid4())
    return ExecutionResult(
        execution_id=execution_id,
        decision_id=decision.decision_id,
        node_id=decision.node_id,
        action=decision.action,
        command=executed_command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        success=exit_code == 0,
        started_at=started_at,
        completed_at=completed_at,
        node_state_after=node_state_after,
        observation_summary=summarize_execution(node_state_after),
    )


def build_node_state_document(node_id: str, node_state: NodeState) -> dict[str, object]:
    document = node_state.model_dump(mode="json")
    document["id"] = node_id
    document["node_id"] = node_id
    return document


def build_observation_document(observation) -> dict[str, object]:
    document = observation.model_dump(mode="json")
    document["id"] = f"{observation.node_id}:{observation.timestamp.isoformat()}:{observation.observation_type}"
    return document


def build_config_snapshot_document(
    *,
    node_id: str,
    decision_id: str,
    attempt_number: int,
    repo_revision_before: str,
    repo_revision_after: str,
    config_path: str,
    before_text: str,
    after_text: str,
) -> dict[str, object]:
    return {
        "id": f"{decision_id}:config:{attempt_number}",
        "node_id": node_id,
        "decision_id": decision_id,
        "attempt_number": attempt_number,
        "config_path": config_path,
        "repo_revision_before": repo_revision_before,
        "repo_revision_after": repo_revision_after,
        "before_text": before_text,
        "after_text": after_text,
    }


def build_repair_trace_document(
    *,
    node_id: str,
    decision_id: str,
    analysis_id: str,
    attempt,
    repo_revision_before: str,
    repo_revision_after: str,
) -> dict[str, object]:
    return {
        "id": f"{decision_id}:trace:{attempt.attempt_number}",
        "node_id": node_id,
        "decision_id": decision_id,
        "analysis_id": analysis_id,
        "attempt_number": attempt.attempt_number,
        "repo_revision_before": repo_revision_before,
        "repo_revision_after": repo_revision_after,
        "prompt": attempt.prompt,
        "model_response": attempt.model_response,
        "previous_config": attempt.previous_config,
        "proposed_config": attempt.proposed_config,
        "test_command": attempt.test_command,
        "test_exit_code": attempt.test_result.returncode,
        "test_stdout": attempt.test_result.stdout,
        "test_stderr": attempt.test_result.stderr,
        "switch_command": attempt.switch_command,
        "switch_exit_code": attempt.switch_result.returncode if attempt.switch_result is not None else None,
        "switch_stdout": attempt.switch_result.stdout if attempt.switch_result is not None else "",
        "switch_stderr": attempt.switch_result.stderr if attempt.switch_result is not None else "",
        "push_success": attempt.push_success,
        "push_message": attempt.push_message,
    }


def build_service_status_document(
    *,
    node_id: str,
    stage: str,
    status: str,
    correlation_id: str,
    detail: str = "",
    timestamp: datetime | None = None,
    uuid_factory: Callable[[], UUID] | None = None,
) -> dict[str, object]:
    event_time = timestamp if timestamp is not None else current_time()
    status_id = str(uuid_factory() if uuid_factory is not None else uuid4())
    return {
        "id": status_id,
        "node_id": node_id,
        "stage": stage,
        "status": status,
        "correlation_id": correlation_id,
        "detail": detail,
        "timestamp": event_time.isoformat(),
    }


def current_time() -> datetime:
    return datetime.now(UTC)
