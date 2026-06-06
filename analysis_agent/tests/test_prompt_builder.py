import unittest

from analysis_agent.prompt_builder import build_log_prompt, build_observation_prompt
from schemas import NodeState, NormalizedLog, Observation


class PromptBuilderTests(unittest.TestCase):
    def test_build_log_prompt_includes_unit_and_action_constraints(self) -> None:
        prompt = build_log_prompt(
            NormalizedLog(
                node_id="node-01",
                timestamp="2026-01-01T00:00:00Z",
                message="Failed to start nginx.service",
                unit="nginx.service",
                priority=3,
                hostname="node-01",
                blob_path="logs/node-01/blob-1",
            )
        )

        self.assertIn("restart_service", prompt)
        self.assertIn("nginx.service", prompt)

    def test_build_observation_prompt_includes_state_payload(self) -> None:
        prompt = build_observation_prompt(
            Observation(
                node_id="node-01",
                observation_type="state_change",
                timestamp="2026-01-01T00:00:00Z",
                node_state=NodeState(failed_units=["nginx.service"]),
                message="nginx failed",
                severity_hint="warning",
            )
        )

        self.assertIn("failed_units", prompt)
        self.assertIn("nginx.service", prompt)
