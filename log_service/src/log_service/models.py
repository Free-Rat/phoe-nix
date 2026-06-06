from datetime import datetime

from pydantic import BaseModel, Field


class StorageTokenResponse(BaseModel):
    sas_url: str
    blob_path: str
    expires_at: datetime


class LogBatch(BaseModel):
    node_id: str
    entries: list[dict[str, object]] = Field(default_factory=list)
    uploaded_at: datetime
