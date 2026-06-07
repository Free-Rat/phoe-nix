import json
import unittest

from analysis_agent.config import AnalysisAgentConfig
from analysis_agent.message_handler import analyze_message, parse_message


class MessageHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AnalysisAgentConfig(
            servicebus_connection="Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
            analysis_results_topic_name="analysis-results",
            keyvault_name="kv-example",
            opencode_api_key_secret="OpenCodeApiKey",
            opencode_api_url="https://api.example/v1/analyze",
            ai_timeout_seconds=30.0,
        )

    def test_parse_message_routes_log_router_payload(self) -> None:
        parsed = parse_message(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "node_id": "node-01",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": "Failed to start nginx.service",
                    "unit": "nginx.service",
                    "priority": 3,
                    "hostname": "node-01",
                    "source": "log_router",
                    "blob_path": "logs/node-01/blob-1",
                }
            ).encode("utf-8")
        )

        self.assertEqual(parsed.source_type, "log_router")
        self.assertEqual(parsed.fallback_unit, "nginx.service")

    def test_analyze_message_builds_structured_result(self) -> None:
        result = analyze_message(
            raw_body=json.dumps(
                {
                    "schema_version": "1.0",
                    "node_id": "node-01",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": "Failed to start nginx.service",
                    "unit": "nginx.service",
                    "priority": 3,
                    "hostname": "node-01",
                    "source": "log_router",
                    "blob_path": "logs/node-01/blob-1",
                }
            ).encode("utf-8"),
            message_id="msg-1",
            config=self.config,
            read_secret_value=lambda vault_name, secret_name: "api-key",
            model_caller=lambda **kwargs: json.dumps(
                {
                    "error_type": "service_failure",
                    "severity": "critical",
                    "root_cause": "bad config",
                    "suggested_action": "restart_service",
                    "confidence": 0.95,
                    "analysis_text": "bad config requires service restart",
                    "remediation_hint": "restart nginx",
                }
            ),
        )

        self.assertEqual(result.original_message_id, "msg-1")
        self.assertEqual(result.affected_unit, "nginx.service")
        self.assertEqual(result.suggested_action, "restart_service")
        self.assertIn("restart", result.analysis_text)
