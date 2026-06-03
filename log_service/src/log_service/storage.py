import json
from collections.abc import Mapping

from azure.storage.blob import BlobClient

from log_service.models import StorageTokenResponse


def build_log_payload(entry: Mapping[str, object], *, node_id: str) -> bytes:
    # Persist the raw journal fields plus node context so downstream services can normalize later.
    serializable_entry = {str(key): value for key, value in entry.items()}
    payload = {
        "node_id": node_id,
        "entry": serializable_entry,
    }
    return json.dumps(payload, default=str).encode("utf-8")


def upload_log_payload(token_response: StorageTokenResponse, payload: bytes, *, timeout_seconds: float) -> None:
    blob_client = BlobClient.from_blob_url(token_response.sas_url)
    blob_client.upload_blob(payload, overwrite=True, timeout=timeout_seconds)
