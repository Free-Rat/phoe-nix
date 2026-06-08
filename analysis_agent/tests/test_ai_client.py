import json
import unittest
from datetime import UTC, datetime

from analysis_agent.ai_client import build_request_headers, build_request_payload, extract_response_text, parse_analysis_response


class AiClientTests(unittest.TestCase):
    def test_build_request_payload_uses_openai_compatible_shape_for_chat_completions(self) -> None:
        payload = json.loads(
            build_request_payload(
                api_url="https://opencode.ai/zen/go/v1/chat/completions",
                model="deepseek-v4-flash",
                prompt="Investigate nginx failure",
            ).decode("utf-8")
        )

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "Investigate nginx failure"})

    def test_build_request_headers_sets_non_urllib_user_agent(self) -> None:
        headers = build_request_headers("secret")

        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], "phoe-nix-analysis-agent/0.1")

    def test_extract_response_text_prefers_openai_compatible_choices(self) -> None:
        response = extract_response_text(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"error_type":"service_failure","analysis_text":"restart nginx"}'
                            }
                        }
                    ]
                }
            )
        )
        self.assertEqual(response, '{"error_type":"service_failure","analysis_text":"restart nginx"}')

    def test_extract_response_text_prefers_wrapped_output_field(self) -> None:
        response = extract_response_text('{"output":"{\\"error_type\\":\\"service_failure\\"}"}')
        self.assertEqual(response, '{"error_type":"service_failure"}')

    def test_parse_analysis_response_applies_defaults_and_fallback_unit(self) -> None:
        result = parse_analysis_response(
            json.dumps(
                {
                    "error_type": "service_failure",
                    "severity": "critical",
                    "root_cause": "bad config",
                    "suggested_action": "restart_service",
                    "confidence": 0.9,
                    "analysis_text": "bad config requires restart",
                    "remediation_hint": "restart nginx",
                }
            ),
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

    def test_parse_analysis_response_fills_missing_nullable_fields(self) -> None:
        result = parse_analysis_response(
            json.dumps(
                {
                    "severity": "info",
                    "root_cause": None,
                    "suggested_action": None,
                    "analysis_text": "No action required.",
                    "remediation_hint": None,
                }
            ),
            node_id="node-01",
            original_message_id="msg-1",
            source_type="log_router",
            fallback_unit=None,
        )
        self.assertEqual(result.root_cause, "No action required.")
        self.assertEqual(result.suggested_action, "no_action")
        self.assertEqual(result.remediation_hint, "No action required.")

    def test_parse_analysis_response_normalizes_confidence_labels(self) -> None:
        result = parse_analysis_response(
            json.dumps(
                {
                    "severity": "warning",
                    "root_cause": "needs review",
                    "suggested_action": "no_action",
                    "analysis_text": "Informational only.",
                    "confidence": "high",
                }
            ),
            node_id="node-01",
            original_message_id="msg-1",
            source_type="log_router",
            fallback_unit=None,
        )
        self.assertEqual(result.confidence, 0.9)

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
