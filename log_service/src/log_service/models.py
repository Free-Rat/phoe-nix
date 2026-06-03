from datetime import datetime

from pydantic import BaseModel


class StorageTokenResponse(BaseModel):
    sas_url: str
    blob_path: str
    expires_at: datetime
