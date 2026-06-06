from datetime import UTC, datetime

from schemas import NodeState, Observation

from local_agent.state import build_observation_hash, has_significant_change


def summarize_node_state(node_state: NodeState) -> str:
    parts: list[str] = []
    if node_state.failed_units:
        parts.append(f"failed units: {', '.join(node_state.failed_units)}")
    if node_state.restart_counts:
        frequent = [f"{unit}={count}" for unit, count in sorted(node_state.restart_counts.items()) if count > 0]
        if frequent:
            parts.append(f"restart counts: {', '.join(frequent)}")
    if node_state.disk_usage:
        parts.append(
            "disk usage: " + ", ".join(f"{mount}={usage}" for mount, usage in sorted(node_state.disk_usage.items()))
        )
    return "; ".join(parts) if parts else "node state unchanged"


def infer_severity(node_state: NodeState) -> str:
    if node_state.failed_units:
        return "warning"
    return "info"


def build_observation(node_id: str, node_state: NodeState, *, observation_type: str) -> Observation:
    return Observation(
        node_id=node_id,
        observation_type=observation_type,
        timestamp=datetime.now(UTC),
        node_state=node_state,
        message=summarize_node_state(node_state),
        severity_hint=infer_severity(node_state),
    )


def should_publish_observation(previous_hash: str | None, node_state: NodeState) -> bool:
    return has_significant_change(previous_hash, node_state)


def current_state_hash(node_state: NodeState) -> str:
    return build_observation_hash(node_state)
