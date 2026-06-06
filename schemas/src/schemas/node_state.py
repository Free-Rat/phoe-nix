from datetime import datetime

from pydantic import BaseModel, Field


class NodeState(BaseModel):
    current_generation: int | None = None
    previous_generation: int | None = None
    boot_generation: int | None = None
    failed_units: list[str] = Field(default_factory=list)
    restart_counts: dict[str, int] = Field(default_factory=dict)
    disk_usage: dict[str, str] = Field(default_factory=dict)
    memory_usage_percent: int | None = None
    cpu_usage_percent: int | None = None
    uptime_seconds: int | None = None
    last_remediation_timestamp: datetime | None = None
