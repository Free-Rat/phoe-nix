import unittest
from datetime import UTC, datetime, timedelta

from local_agent.state import LocalAgentState, can_apply_remediation, has_significant_change, record_remediation
from schemas import NodeState


class LocalAgentStateTests(unittest.TestCase):
    def test_has_significant_change_detects_node_state_change(self) -> None:
        node_state = NodeState(failed_units=["nginx.service"])
        self.assertTrue(has_significant_change(None, node_state))

    def test_can_apply_remediation_respects_cooldown(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        state = LocalAgentState(node_state=NodeState(last_remediation_timestamp=now - timedelta(seconds=30)))
        self.assertFalse(can_apply_remediation(state, cooldown_seconds=60, max_remediations_per_hour=3, now=now))

    def test_record_remediation_updates_timestamp_and_counter(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        state = LocalAgentState(node_state=NodeState())
        updated = record_remediation(state, now=now, node_state_after=NodeState(failed_units=[]))
        self.assertEqual(updated.remediations_this_hour, 1)
        self.assertEqual(updated.node_state.last_remediation_timestamp, now)
