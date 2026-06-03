from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from azure.storage.blob import BlobSasPermissions, generate_blob_sas

from token_service.models import TokenResponse


def build_blob_name(node_id: str, blob_id: UUID) -> str:
    return f"{node_id}/{blob_id}"


def build_blob_path(container_name: str, blob_name: str) -> str:
    return f"{container_name}/{blob_name}"


def build_blob_url(account_name: str, container_name: str, blob_name: str) -> str:
    return f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}"


def build_upload_sas_token(
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    account_key: str,
    expires_at: datetime,
) -> str:
    # The token is scoped to one blob and only grants write permissions.
    return generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(write=True),
        expiry=expires_at,
    )


def issue_upload_token(
    *,
    node_id: str,
    account_name: str,
    container_name: str,
    account_key: str,
    token_ttl_minutes: int = 5,
    now_factory: Callable[[], datetime] | None = None,
    uuid_factory: Callable[[], UUID] | None = None,
) -> TokenResponse:
    current_time = now_factory() if now_factory is not None else datetime.now(UTC)
    blob_id = uuid_factory() if uuid_factory is not None else uuid4()
    blob_name = build_blob_name(node_id, blob_id)
    blob_path = build_blob_path(container_name, blob_name)
    expires_at = current_time + timedelta(minutes=token_ttl_minutes)
    sas_token = build_upload_sas_token(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        expires_at=expires_at,
    )

    return TokenResponse(
        sas_url=f"{build_blob_url(account_name, container_name, blob_name)}?{sas_token}",
        blob_path=blob_path,
        expires_at=expires_at,
    )
