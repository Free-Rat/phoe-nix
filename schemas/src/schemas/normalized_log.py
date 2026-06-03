from datetime import datetime

from pydantic import BaseModel


class NormalizedLog(BaseModel):
    schema_version: str = "1.0"
    node_id: str
    timestamp: datetime
    message: str
    unit: str | None = None
    priority: int | None = None
    hostname: str | None = None
    source: str = "log_router"
    blob_path: str
