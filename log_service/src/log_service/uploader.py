import json
import time
from pathlib import Path
from uuid import uuid4

from log_service.config import LogServiceConfig
from log_service.storage import build_log_payload
from log_service.token_client import request_storage_token


class BatchUploader:
    def __init__(
        self,
        *,
        config: LogServiceConfig,
        token_requester=request_storage_token,
        payload_uploader=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        from log_service.storage import upload_log_payload

        self.config = config
        self.token_requester = token_requester
        self.payload_uploader = payload_uploader or upload_log_payload
        self.sleep = sleep
        self.monotonic = monotonic
        self.buffer: list[dict[str, object]] = []
        self.last_flush_at = monotonic()
        self.spool_directory = Path(config.spool_directory)
        self.spool_directory.mkdir(parents=True, exist_ok=True)

    def add_entry(self, entry: dict[str, object]) -> bool:
        self.buffer.append(entry)
        return len(self.buffer) >= self.config.batch_size

    def flush_due(self) -> bool:
        return bool(self.buffer) and (self.monotonic() - self.last_flush_at) >= self.config.flush_interval_seconds

    def flush(self) -> bool:
        self._drain_spool()
        if not self.buffer:
            self.last_flush_at = self.monotonic()
            return True

        payload = build_log_payload(self.buffer, node_id=self.config.node_id)
        if self._upload_with_retry(payload):
            self.buffer.clear()
            self.last_flush_at = self.monotonic()
            return True

        self._spool_payload(payload)
        self.buffer.clear()
        self.last_flush_at = self.monotonic()
        return False

    def _upload_with_retry(self, payload: bytes) -> bool:
        delay = self.config.retry_base_delay_seconds
        for attempt in range(1, self.config.max_retries + 1):
            try:
                token_response = self.token_requester(
                    self.config.token_service_url,
                    node_id=self.config.node_id,
                    node_api_key=self.config.node_api_key,
                    timeout_seconds=self.config.upload_timeout_seconds,
                )
                self.payload_uploader(
                    token_response.sas_url, payload, timeout_seconds=self.config.upload_timeout_seconds
                )
                return True
            except Exception:
                if attempt == self.config.max_retries:
                    return False
                self.sleep(delay)
                delay *= 2
        return False

    def _spool_payload(self, payload: bytes) -> None:
        spool_path = self.spool_directory / f"{uuid4()}.json"
        spool_path.write_bytes(payload)

    def _drain_spool(self) -> None:
        for spool_path in sorted(self.spool_directory.glob("*.json")):
            payload = spool_path.read_bytes()
            if not self._upload_with_retry(payload):
                return
            spool_path.unlink()

    def load_spooled_payloads(self) -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        for spool_path in sorted(self.spool_directory.glob("*.json")):
            payloads.append(json.loads(spool_path.read_text(encoding="utf-8")))
        return payloads
