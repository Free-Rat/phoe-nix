import unittest

from simulator.fixtures import build_environment, sample_log_entries, sample_observation
from simulator.pipeline import (
    run_observation_pipeline,
    run_pipeline,
    simulate_invalid_ai_response,
    simulate_malformed_log_blob,
    simulate_token_failure,
    simulate_upload_retry_and_recovery,
)


class PipelineSimulationTests(unittest.TestCase):
    def test_pipeline_simulation_flows_from_logs_to_decision_audit(self) -> None:
        summary = run_pipeline(build_environment(), entries=sample_log_entries())

        self.assertEqual(summary["normalized_count"], 2)
        self.assertEqual(summary["analysis_count"], 2)
        self.assertEqual(summary["decision_count"], 2)
        self.assertEqual(summary["execution_count"], 1)
        self.assertEqual(len(summary["uploaded_blob_paths"]), 1)
        self.assertEqual(len(summary["cosmos_decisions"]), 2)
        self.assertEqual(len(summary["cosmos_execution_results"]), 1)
        self.assertEqual(summary["local_agent_commands"], ["nixos-rebuild test && nixos-rebuild switch"])
        self.assertTrue(all(item["action"] == "apply_config" for item in summary["cosmos_decisions"]))
        self.assertTrue(all(item["command"] == "" for item in summary["cosmos_decisions"]))
        self.assertEqual(len(summary["analysis_results_messages"]), 2)
        self.assertEqual(len(summary["cosmos_repair_traces"]), 1)
        self.assertEqual(len(summary["cosmos_config_snapshots"]), 1)

    def test_observation_pipeline_flows_through_analysis_to_local_agent(self) -> None:
        summary = run_observation_pipeline(build_environment(), observation=sample_observation())

        self.assertEqual(summary["analysis_count"], 1)
        self.assertEqual(summary["decision_count"], 1)
        self.assertEqual(summary["execution_count"], 1)
        self.assertEqual(summary["local_agent_commands"][0], "nixos-rebuild test && nixos-rebuild switch")

    def test_token_failure_spools_payload_instead_of_uploading(self) -> None:
        summary = simulate_token_failure(build_environment(), entries=sample_log_entries())

        self.assertFalse(summary["flush_result"])
        self.assertEqual(summary["uploaded_blob_paths"], [])
        self.assertEqual(len(summary["spooled_payloads"]), 1)

    def test_upload_retry_recovers_after_initial_failure(self) -> None:
        summary = simulate_upload_retry_and_recovery(build_environment(), entries=sample_log_entries())

        self.assertTrue(summary["first_flush"])
        self.assertTrue(summary["second_flush"])
        self.assertEqual(summary["spooled_payloads"], [])
        self.assertEqual(len(summary["uploaded_blob_paths"]), 1)
        self.assertEqual(summary["attempts"], 2)

    def test_malformed_log_blob_raises_clear_error(self) -> None:
        summary = simulate_malformed_log_blob(build_environment())

        self.assertIn("missing MESSAGE", summary["error"])

    def test_invalid_ai_response_surfaces_analysis_failure(self) -> None:
        summary = simulate_invalid_ai_response(build_environment(), entries=sample_log_entries())

        self.assertEqual(summary["error_type"], "OpenCodeError")
