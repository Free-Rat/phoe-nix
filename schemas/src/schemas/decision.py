from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Decision(BaseModel):
    schema_version: str = "1.0"
    decision_id: str
    node_id: str
    analysis_id: str
    action: str
    command: str = ""
    severity: Literal["critical", "warning", "info"]
    confidence: float = Field(ge=0.0, le=1.0)
    analysis_summary: str
    remediation_text: str
    idempotency_key: str
    timestamp: datetime
