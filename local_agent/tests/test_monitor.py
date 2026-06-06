import unittest

from local_agent.monitor import build_observation, infer_severity, summarize_node_state
from schemas import NodeState


class MonitorTests(unittest.TestCase):
    def test_summarize_node_state_mentions_failed_units_and_restart_counts(self) -> None:
        message = summarize_node_state(NodeState(failed_units=["nginx.service"], restart_counts={"nginx.service": 5}))
        self.assertIn("nginx.service", message)
        self.assertIn("restart counts", message)

    def test_infer_severity_returns_warning_for_failed_units(self) -> None:
        self.assertEqual(infer_severity(NodeState(failed_units=["nginx.service"])), "warning")

    def test_build_observation_produces_schema_payload(self) -> None:
        observation = build_observation(
            "node-01", NodeState(failed_units=["nginx.service"]), observation_type="state_change"
        )
        self.assertEqual(observation.node_id, "node-01")
        self.assertEqual(observation.observation_type, "state_change")
