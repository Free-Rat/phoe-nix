import tempfile
import unittest
from pathlib import Path

from log_service.config import LogServiceConfig
from log_service.uploader import BatchUploader


class BatchUploaderTests(unittest.TestCase):
    def build_config(self, spool_directory: str) -> LogServiceConfig:
        return LogServiceConfig(
            token_service_url="https://token.example/api/token",
            node_id="node-01",
            node_api_key="secret",
            upload_timeout_seconds=5.0,
            batch_size=2,
            flush_interval_seconds=30.0,
            max_retries=3,
            retry_base_delay_seconds=0.01,
            spool_directory=spool_directory,
        )

    def test_flush_uploads_batch_after_threshold(self) -> None:
        uploaded: list[bytes] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            uploader = BatchUploader(
                config=self.build_config(temp_dir),
                token_requester=lambda *args, **kwargs: type("Token", (), {"sas_url": "https://blob?sas=1"})(),
                payload_uploader=lambda sas_url, payload, timeout_seconds: uploaded.append(payload),
            )

            self.assertFalse(uploader.add_entry({"MESSAGE": "one"}))
            self.assertTrue(uploader.add_entry({"MESSAGE": "two"}))
            self.assertTrue(uploader.flush())

        self.assertEqual(len(uploaded), 1)

    def test_flush_spools_payload_after_retries_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            uploader = BatchUploader(
                config=self.build_config(temp_dir),
                token_requester=lambda *args, **kwargs: type("Token", (), {"sas_url": "https://blob?sas=1"})(),
                payload_uploader=lambda sas_url, payload, timeout_seconds: (_ for _ in ()).throw(RuntimeError("boom")),
                sleep=lambda seconds: None,
            )

            uploader.add_entry({"MESSAGE": "one"})
            result = uploader.flush()

            self.assertFalse(result)
            self.assertEqual(len(list(Path(temp_dir).glob("*.json"))), 1)

    def test_flush_replays_spooled_payload_before_current_buffer(self) -> None:
        uploaded: list[bytes] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            stale_payload = Path(temp_dir) / "stale.json"
            stale_payload.write_text(
                '{"node_id":"node-01","entries":[{"MESSAGE":"stale"}],"uploaded_at":"2026-01-01T00:00:00Z"}',
                encoding="utf-8",
            )

            uploader = BatchUploader(
                config=self.build_config(temp_dir),
                token_requester=lambda *args, **kwargs: type("Token", (), {"sas_url": "https://blob?sas=1"})(),
                payload_uploader=lambda sas_url, payload, timeout_seconds: uploaded.append(payload),
            )

            uploader.add_entry({"MESSAGE": "fresh"})
            uploader.flush()

            self.assertEqual(len(uploaded), 2)
            self.assertFalse(stale_payload.exists())
