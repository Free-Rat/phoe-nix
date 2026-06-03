import json
import unittest

from log_service.storage import build_log_payload


class StorageTests(unittest.TestCase):
    def test_build_log_payload_preserves_raw_entry_and_node_id(self) -> None:
        payload = build_log_payload(
            {
                "MESSAGE": "hello",
                "__REALTIME_TIMESTAMP": 123,
                "_SYSTEMD_UNIT": "nginx.service",
            },
            node_id="node-01",
        )

        decoded = json.loads(payload.decode("utf-8"))
        self.assertEqual(decoded["node_id"], "node-01")
        self.assertEqual(decoded["entry"]["MESSAGE"], "hello")
        self.assertEqual(decoded["entry"]["_SYSTEMD_UNIT"], "nginx.service")
