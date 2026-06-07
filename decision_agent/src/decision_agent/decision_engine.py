import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from schemas import AnalysisResult, Decision

NIX_ASSIGNMENT_PATTERN = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_.-]*\s*=\s*.+?;", re.DOTALL)


def contains_nix_assignment(text: str | None) -> bool:
    return bool(text and NIX_ASSIGNMENT_PATTERN.search(text))


def build_command(analysis_result: AnalysisResult) -> str:
    if contains_nix_assignment(analysis_result.remediation_hint) or contains_nix_assignment(
        analysis_result.analysis_text
    ):
        return ""
    if analysis_result.suggested_action == "rollback":
        return "nixos-rebuild switch --rollback"
    if analysis_result.suggested_action == "rebuild":
        return "nixos-rebuild switch"
    if analysis_result.suggested_action == "restart_service":
        if not analysis_result.affected_unit:
            raise ValueError("restart_service requires affected_unit")
        return f"systemctl restart {analysis_result.affected_unit}"
    if analysis_result.suggested_action == "no_action":
        return ""
    raise ValueError(f"unsupported action: {analysis_result.suggested_action}")


def build_idempotency_key(analysis_result: AnalysisResult, command: str) -> str:
    digest = hashlib.sha256(
        f"{analysis_result.node_id}:{analysis_result.suggested_action}:{command}:{analysis_result.original_message_id}".encode(
            "utf-8"
        )
    )
    return digest.hexdigest()


def build_decision(
    analysis_result: AnalysisResult,
    *,
    now_factory: Callable[[], datetime] | None = None,
    uuid_factory: Callable[[], UUID] | None = None,
) -> Decision:
    current_time = now_factory() if now_factory is not None else datetime.now(UTC)
    decision_id = str(uuid_factory() if uuid_factory is not None else uuid4())
    command = build_command(analysis_result)
    remediation_text = analysis_result.remediation_hint or analysis_result.analysis_text
    return Decision(
        decision_id=decision_id,
        node_id=analysis_result.node_id,
        analysis_id=analysis_result.original_message_id,
        action=analysis_result.suggested_action,
        command=command,
        severity=analysis_result.severity,
        confidence=analysis_result.confidence,
        analysis_summary=analysis_result.analysis_text,
        remediation_text=remediation_text,
        idempotency_key=build_idempotency_key(analysis_result, command),
        timestamp=current_time,
    )


def build_decision_document(decision: Decision) -> dict[str, object]:
    document = decision.model_dump(mode="json")
    document["id"] = decision.decision_id
    return document
