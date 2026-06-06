from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from schemas.node_state import NodeState


class AnalysisContext(BaseModel):
    corroborating_observations: list[str] = Field(default_factory=list)
    contradicting_observations: list[str] = Field(default_factory=list)
    node_state_at_analysis_time: NodeState | None = None


class AnalysisResult(BaseModel):
    schema_version: str = "1.0"
    node_id: str
    original_message_id: str
    source_type: Literal["log_router", "local_agent"]
    error_type: str = "other"
    severity: Literal["critical", "warning", "info"]
    root_cause: str
    suggested_action: str
    affected_unit: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    context: AnalysisContext = Field(default_factory=AnalysisContext)
    analysis_text: str
    remediation_hint: str | None = None
    raw_ai_response: str
    timestamp: datetime
