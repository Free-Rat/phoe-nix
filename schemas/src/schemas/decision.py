from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Decision(BaseModel):
    schema_version: str = "1.0"
    node_id: str
    action: Literal["rollback", "restart_service", "rebuild"]
    reason: str
    created_at: datetime
