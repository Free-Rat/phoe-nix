import os

from pydantic import BaseModel, Field


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


class LocalAgentConfig(BaseModel):
    servicebus_enabled: bool = True
    servicebus_connection: str
    analysis_input_topic_name: str = "analysis-input"
    final_decisions_topic_name: str = "final-decisions"
    decision_subscription_name: str = "local-agent"
    cosmos_enabled: bool = True
    cosmos_endpoint: str
    cosmos_key: str | None = None
    cosmos_database_name: str
    cosmos_observations_container_name: str = "observations"
    cosmos_execution_results_container_name: str = "execution-results"
    cosmos_node_state_container_name: str = "node-state-current"
    cosmos_config_snapshots_container_name: str = "config-snapshots"
    cosmos_repair_traces_container_name: str = "repair-traces"
    cosmos_service_status_container_name: str = "service-status"
    node_id: str
    config_repo_url: str = "https://github.com/Free-Rat/phoe-nix-config"
    config_repo_branch: str = "main"
    config_repo_path: str = "/var/lib/phoe-nix-config"
    config_file_path: str = "configuration.nix"
    repo_refresh_seconds: int = Field(default=300, ge=1)
    ollama_base_url: str = "http://10.0.2.2:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_timeout_seconds: float = Field(default=60.0, gt=0)
    repair_max_attempts: int = Field(default=3, ge=1)
    rebuild_test_command: str = "nixos-rebuild test"
    rebuild_switch_command: str = "nixos-rebuild switch"
    observe_interval_seconds: int = Field(default=60, ge=1)
    cooldown_seconds: int = Field(default=300, ge=0)
    max_remediations_per_hour: int = Field(default=3, ge=1)
    decision_poll_base_seconds: float = Field(default=0.05, gt=0)
    decision_poll_max_seconds: float = Field(default=1.0, gt=0)


def load_config(env: dict[str, str] | None = None) -> LocalAgentConfig:
    values = env if env is not None else os.environ
    servicebus_connection = values.get("SERVICEBUS_CONNECTION", "")
    cosmos_endpoint = values.get("COSMOSDB_ENDPOINT", "")
    cosmos_database_name = values.get("COSMOSDB_DATABASE_NAME", "")
    return LocalAgentConfig(
        servicebus_enabled=_parse_bool(values.get("SERVICEBUS_ENABLED"), default=bool(servicebus_connection.strip())),
        servicebus_connection=servicebus_connection,
        analysis_input_topic_name=values.get("SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME", "analysis-input"),
        final_decisions_topic_name=values.get("SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME", "final-decisions"),
        decision_subscription_name=values.get("SERVICEBUS_SUBSCRIPTION_LOCAL_AGENT", "local-agent"),
        cosmos_enabled=_parse_bool(
            values.get("COSMOSDB_ENABLED"), default=bool(cosmos_endpoint.strip() and cosmos_database_name.strip())
        ),
        cosmos_endpoint=cosmos_endpoint,
        cosmos_key=values.get("COSMOSDB_KEY") or None,
        cosmos_database_name=cosmos_database_name,
        cosmos_observations_container_name=values.get("COSMOSDB_OBSERVATIONS_CONTAINER_NAME", "observations"),
        cosmos_execution_results_container_name=values.get(
            "COSMOSDB_EXECUTION_RESULTS_CONTAINER_NAME", "execution-results"
        ),
        cosmos_node_state_container_name=values.get("COSMOSDB_NODE_STATE_CONTAINER_NAME", "node-state-current"),
        cosmos_config_snapshots_container_name=values.get(
            "COSMOSDB_CONFIG_SNAPSHOTS_CONTAINER_NAME", "config-snapshots"
        ),
        cosmos_repair_traces_container_name=values.get("COSMOSDB_REPAIR_TRACES_CONTAINER_NAME", "repair-traces"),
        cosmos_service_status_container_name=values.get("COSMOSDB_SERVICE_STATUS_CONTAINER_NAME", "service-status"),
        node_id=values.get("NODE_ID", "localhost"),
        config_repo_url=values.get("CONFIG_REPO_URL", "https://github.com/Free-Rat/phoe-nix-config"),
        config_repo_branch=values.get("CONFIG_REPO_BRANCH", "main"),
        config_repo_path=values.get("CONFIG_REPO_PATH", "/var/lib/phoe-nix-config"),
        config_file_path=values.get("CONFIG_FILE_PATH", "configuration.nix"),
        repo_refresh_seconds=int(values.get("REPO_REFRESH_SECONDS", "300")),
        ollama_base_url=values.get("OLLAMA_BASE_URL", "http://10.0.2.2:11434"),
        ollama_model=values.get("OLLAMA_MODEL", "gemma4:e4b"),
        ollama_timeout_seconds=float(values.get("OLLAMA_TIMEOUT_SECONDS", "60")),
        repair_max_attempts=int(values.get("REPAIR_MAX_ATTEMPTS", "3")),
        rebuild_test_command=values.get("REBUILD_TEST_COMMAND", "nixos-rebuild test"),
        rebuild_switch_command=values.get("REBUILD_SWITCH_COMMAND", "nixos-rebuild switch"),
        observe_interval_seconds=int(values.get("OBSERVE_INTERVAL_SECONDS", "60")),
        cooldown_seconds=int(values.get("COOLDOWN_SECONDS", "300")),
        max_remediations_per_hour=int(values.get("MAX_REMEDIATIONS_PER_HOUR", "3")),
        decision_poll_base_seconds=float(values.get("DECISION_POLL_BASE_SECONDS", "0.05")),
        decision_poll_max_seconds=float(values.get("DECISION_POLL_MAX_SECONDS", "1.0")),
    )
