import unittest
from datetime import UTC, datetime, timedelta

from local_agent.executor import CommandResult, derive_execution_command, execute_decision
from local_agent.state import LocalAgentState
from schemas import Decision, NodeState


class ExecutorTests(unittest.TestCase):
    def build_decision(self, **overrides) -> Decision:
        payload = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "restart_service",
            "command": "systemctl restart nginx.service",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Restart nginx to clear the service failure.",
            "remediation_text": "Restart nginx.",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        payload.update(overrides)
        return Decision.model_validate(payload)

    def test_derive_execution_command_requires_explicit_command_for_apply_config(self) -> None:
        command = derive_execution_command(
            self.build_decision(
                action="apply_config",
                command="",
                analysis_summary="Enable SSH by setting services.openssh.enable = true;",
                remediation_text="services.openssh.enable = true;",
            )
        )
        self.assertEqual(command, "")

    def test_execute_decision_runs_command_and_updates_state(self) -> None:
        next_state, result, error = execute_decision(
            decision=self.build_decision(),
            state=LocalAgentState(node_state=NodeState(failed_units=["nginx.service"])),
            local_node_id="node-01",
            cooldown_seconds=300,
            max_remediations_per_hour=3,
            timeout_seconds=60,
            command_runner=lambda command: CommandResult(returncode=0, stdout="ok", stderr=""),
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(error)
        self.assertTrue(result.success)
        self.assertEqual(next_state.remediations_this_hour, 1)

    def test_execute_decision_blocks_during_cooldown(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        next_state, result, error = execute_decision(
            decision=self.build_decision(),
            state=LocalAgentState(node_state=NodeState(last_remediation_timestamp=now - timedelta(seconds=10))),
            local_node_id="node-01",
            cooldown_seconds=300,
            max_remediations_per_hour=3,
            timeout_seconds=60,
            command_runner=lambda command: CommandResult(returncode=0, stdout="ok", stderr=""),
            now_factory=lambda: now,
        )
        self.assertIsNotNone(error)
        self.assertIsNone(result)

    def test_execute_decision_rejects_missing_command_and_missing_repair_hint(self) -> None:
        _, result, error = execute_decision(
            decision=self.build_decision(
                action="apply_config", command="", analysis_summary="no details", remediation_text=""
            ),
            state=LocalAgentState(node_state=NodeState()),
            local_node_id="node-01",
            cooldown_seconds=300,
            max_remediations_per_hour=3,
            timeout_seconds=60,
            command_runner=lambda command: CommandResult(returncode=0, stdout="ok", stderr=""),
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(result)
        self.assertEqual(error, "decision produced no executable repair plan")
