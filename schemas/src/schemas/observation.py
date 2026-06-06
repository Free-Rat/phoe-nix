from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from schemas.node_state import NodeState


class Observation(BaseModel):
    schema_version: str = "1.0"
    source: Literal["local_agent"] = "local_agent"
    node_id: str
    observation_type: Literal["periodic_state", "state_change"]
    timestamp: datetime
    node_state: NodeState
    message: str
    severity_hint: Literal["critical", "warning", "info"]
