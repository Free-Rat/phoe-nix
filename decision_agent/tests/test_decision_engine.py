import unittest
from datetime import UTC, datetime
from uuid import UUID

from decision_agent.decision_engine import build_command, build_decision, build_decision_document
from schemas import AnalysisResult


class DecisionEngineTests(unittest.TestCase):
    def build_analysis(self, **overrides):
        payload = {
            "node_id": "node-01",
            "original_message_id": "msg-1",
            "source_type": "log_router",
            "error_type": "service_failure",
            "severity": "critical",
            "root_cause": "broken nginx config",
            "suggested_action": "restart_service",
            "affected_unit": "nginx.service",
            "confidence": 0.95,
            "analysis_text": "nginx config is broken and needs a restart",
            "remediation_hint": "restart nginx.service",
            "raw_ai_response": "{}",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        payload.update(overrides)
        return AnalysisResult.model_validate(payload)

    def test_build_command_maps_restart_service(self) -> None:
        self.assertEqual(build_command(self.build_analysis()), "systemctl restart nginx.service")

    def test_build_decision_populates_audit_fields(self) -> None:
        decision = build_decision(
            self.build_analysis(),
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
            uuid_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
        )

        self.assertEqual(decision.decision_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(decision.command, "systemctl restart nginx.service")
        self.assertTrue(decision.idempotency_key)
        self.assertIn("nginx", decision.analysis_summary)

    def test_build_decision_normalizes_no_action_required(self) -> None:
        decision = build_decision(self.build_analysis(suggested_action="no action required"))

        self.assertEqual(decision.action, "no_action")
        self.assertEqual(decision.command, "")

    def test_build_decision_normalizes_none_action(self) -> None:
        decision = build_decision(self.build_analysis(suggested_action="none"))

        self.assertEqual(decision.action, "no_action")
        self.assertEqual(decision.command, "")

    def test_build_decision_normalizes_punctuated_no_action_required(self) -> None:
        decision = build_decision(self.build_analysis(suggested_action="No action required."))

        self.assertEqual(decision.action, "no_action")
        self.assertEqual(decision.command, "")

    def test_build_decision_normalizes_none_required(self) -> None:
        decision = build_decision(self.build_analysis(suggested_action="None required"))

        self.assertEqual(decision.action, "no_action")
        self.assertEqual(decision.command, "")

    def test_build_decision_leaves_command_empty_for_config_repair_hints(self) -> None:
        decision = build_decision(
            self.build_analysis(
                suggested_action="apply_config",
                remediation_hint="services.openssh.enable = true;",
                analysis_text="Enable SSH by setting services.openssh.enable = true;",
            )
        )
        self.assertEqual(decision.command, "")
        self.assertIn("openssh", decision.remediation_text)

    def test_build_decision_document_sets_cosmos_id(self) -> None:
        document = build_decision_document(build_decision(self.build_analysis()))
        self.assertEqual(document["id"], document["decision_id"])
