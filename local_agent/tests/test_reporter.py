import unittest
from datetime import UTC, datetime
from uuid import UUID

from local_agent.reporter import (
    build_config_snapshot_document,
    build_execution_result,
    build_node_state_document,
    build_repair_trace_document,
    build_service_status_document,
)
from schemas import Decision, NodeState


class ReporterTests(unittest.TestCase):
    def build_decision(self) -> Decision:
        return Decision.model_validate(
            {
                "decision_id": "dec-1",
                "node_id": "node-01",
                "analysis_id": "analysis-1",
                "action": "restart_service",
                "command": "systemctl restart nginx.service",
                "severity": "critical",
                "confidence": 0.9,
                "analysis_summary": "restart nginx",
                "remediation_text": "restart nginx",
                "idempotency_key": "abc",
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )

    def test_build_execution_result_includes_node_state_after(self) -> None:
        result = build_execution_result(
            decision=self.build_decision(),
            executed_command="systemctl restart nginx.service",
            exit_code=0,
            stdout="ok",
            stderr="",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
            node_state_after=NodeState(failed_units=[]),
            uuid_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.execution_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result.command, "systemctl restart nginx.service")

    def test_build_node_state_document_sets_id(self) -> None:
        document = build_node_state_document("node-01", NodeState(failed_units=[]))
        self.assertEqual(document["id"], "node-01")

    def test_build_config_snapshot_document_captures_before_after(self) -> None:
        document = build_config_snapshot_document(
            node_id="node-01",
            decision_id="dec-1",
            attempt_number=1,
            repo_revision_before="rev-a",
            repo_revision_after="rev-b",
            config_path="configuration.nix",
            before_text="{ }",
            after_text="{ services.openssh.enable = true; }",
        )
        self.assertEqual(document["repo_revision_after"], "rev-b")

    def test_build_repair_trace_document_records_test_result(self) -> None:
        attempt = type(
            "Attempt",
            (),
            {
                "attempt_number": 1,
                "prompt": "prompt",
                "model_response": "response",
                "previous_config": "{ }",
                "proposed_config": "{ services.openssh.enable = true; }",
                "test_command": "nixos-rebuild test",
                "test_result": type("Result", (), {"returncode": 1, "stdout": "", "stderr": "err"})(),
                "switch_command": None,
                "switch_result": None,
                "push_success": False,
                "push_message": "",
            },
        )()
        document = build_repair_trace_document(
            node_id="node-01",
            decision_id="dec-1",
            analysis_id="analysis-1",
            attempt=attempt,
            repo_revision_before="rev-a",
            repo_revision_after="rev-b",
        )
        self.assertEqual(document["test_exit_code"], 1)

    def test_build_service_status_document_sets_stage_and_status(self) -> None:
        document = build_service_status_document(
            node_id="node-01",
            stage="repair",
            status="completed",
            correlation_id="dec-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            uuid_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
        )
        self.assertEqual(document["stage"], "repair")
