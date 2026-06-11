import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from schemas import NodeState

RESOURCE_ALERT_THRESHOLD_PERCENT = 80
RESOURCE_BUCKET_STEP_PERCENT = 5


@dataclass(frozen=True)
class LocalAgentState:
    node_state: NodeState
    ongoing_remediation: bool = False
    remediations_this_hour: int = 0
    last_observation_hash: str | None = None


def _bucket_resource_percent(value: int | None) -> int | None:
    if value is None or value < RESOURCE_ALERT_THRESHOLD_PERCENT:
        return None
    return (value // RESOURCE_BUCKET_STEP_PERCENT) * RESOURCE_BUCKET_STEP_PERCENT


def _meaningful_observation_fields(node_state: NodeState) -> dict[str, object]:
    return {
        "failed_units": sorted(set(node_state.failed_units)),
        "restart_counts": {unit: count for unit, count in sorted(node_state.restart_counts.items()) if count > 0},
        "disk_usage": dict(sorted(node_state.disk_usage.items())),
        "memory_usage_bucket_percent": _bucket_resource_percent(node_state.memory_usage_percent),
        "cpu_usage_bucket_percent": _bucket_resource_percent(node_state.cpu_usage_percent),
    }


def build_observation_hash(node_state: NodeState) -> str:
    return json.dumps(_meaningful_observation_fields(node_state), sort_keys=True)


def has_significant_change(previous_hash: str | None, node_state: NodeState) -> bool:
    return previous_hash != build_observation_hash(node_state)


def within_cooldown(node_state: NodeState, *, cooldown_seconds: int, now: datetime) -> bool:
    if node_state.last_remediation_timestamp is None:
        return False
    return (now - node_state.last_remediation_timestamp) < timedelta(seconds=cooldown_seconds)


def can_apply_remediation(
    state: LocalAgentState,
    *,
    cooldown_seconds: int,
    max_remediations_per_hour: int,
    now: datetime,
) -> bool:
    if state.ongoing_remediation:
        return False
    if within_cooldown(state.node_state, cooldown_seconds=cooldown_seconds, now=now):
        return False
    return state.remediations_this_hour < max_remediations_per_hour


def record_remediation(state: LocalAgentState, *, now: datetime, node_state_after: NodeState) -> LocalAgentState:
    updated_node_state = node_state_after.model_copy(update={"last_remediation_timestamp": now})
    return replace(
        state,
        node_state=updated_node_state,
        ongoing_remediation=False,
        remediations_this_hour=state.remediations_this_hour + 1,
        last_observation_hash=build_observation_hash(updated_node_state),
    )


def start_remediation(state: LocalAgentState) -> LocalAgentState:
    return replace(state, ongoing_remediation=True)


def update_node_state(state: LocalAgentState, node_state: NodeState) -> LocalAgentState:
    return replace(state, node_state=node_state, last_observation_hash=build_observation_hash(node_state))


def new_state(node_id: str) -> LocalAgentState:
    del node_id
    return LocalAgentState(node_state=NodeState())
