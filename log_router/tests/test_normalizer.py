import json
import unittest
from datetime import UTC, datetime

from log_router.normalizer import normalize_blob, normalize_entry, parse_blob_payload


class NormalizerTests(unittest.TestCase):
    def test_parse_blob_payload_extracts_node_id_and_entries(self) -> None:
        node_id, entries = parse_blob_payload(
            json.dumps({"node_id": "node-01", "entries": [{"MESSAGE": "hello"}]}).encode("utf-8")
        )

        self.assertEqual(node_id, "node-01")
        self.assertEqual(entries[0]["MESSAGE"], "hello")

    def test_normalize_entry_maps_journal_fields(self) -> None:
        normalized = normalize_entry(
            {
                "__REALTIME_TIMESTAMP": "1234567890123456",
                "MESSAGE": "Failed to start nginx.service",
                "_SYSTEMD_UNIT": "nginx.service",
                "PRIORITY": "3",
                "_HOSTNAME": "nixos-node-01",
                "SYSLOG_IDENTIFIER": "systemd",
            },
            node_id="nixos-node-01",
            blob_path="logs/nixos-node-01/abc-123",
        )

        self.assertEqual(normalized.node_id, "nixos-node-01")
        self.assertEqual(normalized.unit, "nginx.service")
        self.assertEqual(normalized.priority, 3)
        self.assertEqual(normalized.source_identifier, "systemd")
        self.assertEqual(normalized.blob_path, "logs/nixos-node-01/abc-123")

    def test_normalize_entry_accepts_iso8601_timestamp(self) -> None:
        normalized = normalize_entry(
            {
                "__REALTIME_TIMESTAMP": "2026-06-08T13:16:02.848036Z",
                "MESSAGE": "Failed to start nginx.service",
            },
            node_id="nixos-node-01",
            blob_path="logs/nixos-node-01/abc-123",
        )

        self.assertEqual(normalized.timestamp, datetime(2026, 6, 8, 13, 16, 2, 848036, tzinfo=UTC))

    def test_normalize_blob_rejects_entry_without_message(self) -> None:
        with self.assertRaises(ValueError):
            normalize_blob(
                json.dumps(
                    {
                        "node_id": "node-01",
                        "entries": [{"__REALTIME_TIMESTAMP": "1234567890123456"}],
                    }
                ).encode("utf-8"),
                blob_path="logs/node-01/blob-1",
            )
