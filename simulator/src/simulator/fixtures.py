import tempfile
from pathlib import Path

from analysis_agent.config import AnalysisAgentConfig
from decision_agent.config import DecisionAgentConfig
from local_agent.config import LocalAgentConfig
from local_agent.state import new_state
from log_service.config import LogServiceConfig
from schemas import NodeState, Observation
from simulator.fakes import FakeBlobStorage, FakeConfigRepo, FakeCosmos, FakeKeyVault, FakeLocalAgent, FakeServiceBus
from simulator.pipeline import LocalPipelineEnvironment, simulate_opencode_response
from token_service.config import TokenServiceConfig


def build_environment(*, spool_directory: str | None = None) -> LocalPipelineEnvironment:
    temp_spool_directory = spool_directory or tempfile.mkdtemp(prefix="phoe-nix-sim-")
    Path(temp_spool_directory).mkdir(parents=True, exist_ok=True)

    return LocalPipelineEnvironment(
        token_config=TokenServiceConfig(
            storage_account_name="blob",
            logs_container_name="logs",
            keyvault_name="kv-local",
            storage_account_key_secret="StorageAccountKey",
            node_api_key="secret",
            token_ttl_minutes=5,
        ),
        log_config=LogServiceConfig(
            token_service_url="http://token.local/api/token",
            node_id="nixos-node-01",
            node_api_key="secret",
            upload_timeout_seconds=5.0,
            batch_size=100,
            flush_interval_seconds=30.0,
            max_retries=3,
            retry_base_delay_seconds=0.01,
            spool_directory=temp_spool_directory,
        ),
        analysis_config=AnalysisAgentConfig(
            servicebus_connection="Endpoint=sb://local/;SharedAccessKeyName=test;SharedAccessKey=secret",
            analysis_results_topic_name="analysis-results",
            keyvault_name="kv-local",
            opencode_api_key_secret="OpenCodeApiKey",
            opencode_api_url="https://opencode.local/v1/analyze",
            ai_timeout_seconds=5.0,
        ),
        decision_config=DecisionAgentConfig(
            servicebus_connection="Endpoint=sb://local/;SharedAccessKeyName=test;SharedAccessKey=secret",
            analysis_results_topic_name="analysis-results",
            final_decisions_topic_name="final-decisions",
            cosmos_endpoint="https://cosmos.local",
            cosmos_database_name="project-healer",
            cosmos_decisions_container_name="decisions",
        ),
        blob_storage=FakeBlobStorage(),
        service_bus=FakeServiceBus(),
        cosmos=FakeCosmos(),
        local_agent_config=LocalAgentConfig(
            servicebus_connection="Endpoint=sb://local/;SharedAccessKeyName=test;SharedAccessKey=secret",
            cosmos_endpoint="https://cosmos.local",
            cosmos_database_name="project-healer",
            node_id="nixos-node-01",
            config_repo_path=temp_spool_directory,
        ),
        config_repo=FakeConfigRepo(),
        keyvault=FakeKeyVault(
            {
                "StorageAccountKey": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "OpenCodeApiKey": "fake-opencode-key",
            }
        ),
        local_agent=FakeLocalAgent(),
        model_caller=simulate_opencode_response,
        local_agent_state=new_state("nixos-node-01"),
    )


def sample_log_entries() -> list[dict[str, object]]:
    return [
        {
            "__REALTIME_TIMESTAMP": "1234567890123456",
            "MESSAGE": "Failed to start nginx.service",
            "_SYSTEMD_UNIT": "nginx.service",
            "PRIORITY": "3",
            "_HOSTNAME": "nixos-node-01",
            "SYSLOG_IDENTIFIER": "systemd",
        },
        {
            "__REALTIME_TIMESTAMP": "1234567891123456",
            "MESSAGE": "nginx.service entered failed state",
            "_SYSTEMD_UNIT": "nginx.service",
            "PRIORITY": "3",
            "_HOSTNAME": "nixos-node-01",
            "SYSLOG_IDENTIFIER": "systemd",
        },
    ]


def sample_observation() -> Observation:
    return Observation(
        node_id="nixos-node-01",
        observation_type="state_change",
        timestamp="2026-01-01T00:00:00Z",
        node_state=NodeState(
            current_generation=47,
            previous_generation=46,
            failed_units=["nginx.service"],
            restart_counts={"nginx.service": 5},
            disk_usage={"/": "71%", "/nix": "85%"},
        ),
        message="nginx has restarted 5 times in the last hour",
        severity_hint="warning",
    )
