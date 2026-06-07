from collections.abc import Sequence
from datetime import UTC, datetime
from urllib import request

from azure.storage.blob import BlobClient

from log_service.models import LogBatch


def build_log_payload(entries: Sequence[dict[str, object]], *, node_id: str) -> bytes:
    batch = LogBatch(
        node_id=node_id,
        entries=[{str(key): value for key, value in entry.items()} for entry in entries],
        uploaded_at=datetime.now(UTC),
    )
    return batch.model_dump_json().encode("utf-8")


def upload_log_payload(sas_url: str, payload: bytes, *, timeout_seconds: float) -> None:
    if sas_url.startswith("mockblob+http://") or sas_url.startswith("mockblob+https://"):
        upload_url = sas_url.replace("mockblob+", "", 1)
        http_request = request.Request(upload_url, data=payload, method="PUT")
        with request.urlopen(http_request, timeout=timeout_seconds):
            return
    blob_client = BlobClient.from_blob_url(sas_url)
    blob_client.upload_blob(payload, overwrite=True, timeout=timeout_seconds)
