import unittest
from datetime import UTC, datetime

from analysis_agent.ai_client import extract_response_text, parse_analysis_response


class AiClientTests(unittest.TestCase):
    def test_extract_response_text_prefers_wrapped_output_field(self) -> None:
        response = extract_response_text('{"output":"{\\"error_type\\":\\"service_failure\\"}"}')
        self.assertEqual(response, '{"error_type":"service_failure"}')

    def test_parse_analysis_response_applies_defaults_and_fallback_unit(self) -> None:
        result = parse_analysis_response(
            '{"error_type":"service_failure","severity":"critical","root_cause":"bad config","suggested_action":"restart_service","confidence":0.9,"analysis_text":"bad config requires restart","remediation_hint":"restart nginx"}',
            node_id="node-01",
            original_message_id="msg-1",
            source_type="log_router",
            fallback_unit="nginx.service",
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )

        self.assertEqual(result.node_id, "node-01")
        self.assertEqual(result.affected_unit, "nginx.service")
        self.assertEqual(result.original_message_id, "msg-1")
        self.assertEqual(result.remediation_hint, "restart nginx")

    def test_parse_analysis_response_falls_back_to_raw_text(self) -> None:
        result = parse_analysis_response(
            "Enable services.openssh.enable = true; and rebuild the node.",
            node_id="node-01",
            original_message_id="msg-1",
            source_type="log_router",
            fallback_unit=None,
        )
        self.assertEqual(result.suggested_action, "investigate")
        self.assertIn("openssh", result.analysis_text)
