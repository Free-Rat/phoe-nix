import unittest

from decision_agent.app import process_analysis_result
from decision_agent.config import DecisionAgentConfig
from schemas import AnalysisResult


class DecisionAppTests(unittest.TestCase):
    def test_process_analysis_result_writes_document_and_returns_decision(self) -> None:
        written_documents = []
        config = DecisionAgentConfig(
            servicebus_connection="Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
            analysis_results_topic_name="analysis-results",
            final_decisions_topic_name="final-decisions",
            cosmos_endpoint="https://cosmos.example",
            cosmos_database_name="project-healer",
            cosmos_decisions_container_name="decisions",
        )
        analysis_result = AnalysisResult.model_validate(
            {
                "node_id": "node-01",
                "original_message_id": "msg-1",
                "source_type": "log_router",
                "error_type": "service_failure",
                "severity": "critical",
                "root_cause": "broken nginx config",
                "suggested_action": "rollback",
                "confidence": 0.95,
                "analysis_text": "Rollback the node to recover from a broken nginx config.",
                "remediation_hint": "Rollback to the previous generation.",
                "raw_ai_response": "{}",
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )

        decision = process_analysis_result(
            analysis_result=analysis_result,
            config=config,
            write_document=lambda **kwargs: written_documents.append(kwargs["document"]),
        )

        self.assertEqual(decision.action, "rollback")
        self.assertEqual(written_documents[0]["id"], decision.decision_id)
        self.assertIn("Rollback", decision.analysis_summary)
