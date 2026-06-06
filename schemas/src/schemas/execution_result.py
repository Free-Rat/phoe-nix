from datetime import datetime

from pydantic import BaseModel

from schemas.node_state import NodeState


class ExecutionResult(BaseModel):
    schema_version: str = "1.0"
    execution_id: str
    decision_id: str
    node_id: str
    action: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    started_at: datetime
    completed_at: datetime
    node_state_after: NodeState
    observation_summary: str
