import json
from dataclasses import dataclass

from analysis_agent.config import AnalysisAgentConfig
from analysis_agent.message_handler import analyze_message
from decision_agent.app import process_analysis_result
from decision_agent.config import DecisionAgentConfig
from local_agent.config import LocalAgentConfig
from local_agent.executor import CommandResult
from local_agent.repair_planner import execute_repair_loop
from local_agent.runtime import LocalAgentRuntime, RuntimeDependencies, handle_decision, persist_pending
from local_agent.state import LocalAgentState
from log_router.normalizer import normalize_blob
from log_service.config import LogServiceConfig
from log_service.uploader import BatchUploader
from schemas import NodeState, Observation
from simulator.fakes import FakeBlobStorage, FakeConfigRepo, FakeCosmos, FakeKeyVault, FakeLocalAgent, FakeServiceBus
from token_service.app import handle_token_request
from token_service.config import TokenServiceConfig
from token_service.models import TokenResponse


@dataclass
class LocalPipelineEnvironment:
    token_config: TokenServiceConfig
    log_config: LogServiceConfig
    analysis_config: AnalysisAgentConfig
    decision_config: DecisionAgentConfig
    blob_storage: FakeBlobStorage
    service_bus: FakeServiceBus
    cosmos: FakeCosmos
    local_agent_config: LocalAgentConfig
    config_repo: FakeConfigRepo
    keyvault: FakeKeyVault
    local_agent: FakeLocalAgent
    model_caller: object
    local_agent_state: LocalAgentState


def issue_token_via_service(
    environment: LocalPipelineEnvironment, *, node_id: str, node_api_key: str | None
) -> TokenResponse:
    result = handle_token_request(
        raw_body=json.dumps({"node_id": node_id}).encode("utf-8"),
        headers={"x-node-id": node_id, "x-api-key": node_api_key or ""},
        config=environment.token_config,
        read_storage_account_key=environment.keyvault.read,
    )
    if result.status_code != 200:
        raise RuntimeError(f"token service failed: {result.body}")
    token_response = TokenResponse.model_validate_json(result.body)
    return token_response.model_copy(update={"sas_url": f"https://blob.local/{token_response.blob_path}?sig=fake"})


def upload_payload_to_fake_blob_storage(
    environment: LocalPipelineEnvironment, sas_url: str, payload: bytes, *, timeout_seconds: float
) -> None:
    del timeout_seconds
    blob_path = sas_url.split("https://blob.local/", 1)[1].split("?", 1)[0]
    environment.blob_storage.upload(blob_path, payload)


def build_batch_uploader(environment: LocalPipelineEnvironment) -> BatchUploader:
    return BatchUploader(
        config=environment.log_config,
        token_requester=lambda token_service_url, node_id, node_api_key, timeout_seconds: issue_token_via_service(
            environment,
            node_id=node_id,
            node_api_key=node_api_key,
        ),
        payload_uploader=lambda sas_url, payload, timeout_seconds: upload_payload_to_fake_blob_storage(
            environment,
            sas_url,
            payload,
            timeout_seconds=timeout_seconds,
        ),
        sleep=lambda seconds: None,
    )


def publish_normalized_logs(environment: LocalPipelineEnvironment, *, blob_path: str) -> int:
    payload = environment.blob_storage.read(blob_path)
    messages = normalize_blob(payload, blob_path=blob_path)
    for index, message in enumerate(messages):
        environment.service_bus.publish(
            topic_name="analysis-input",
            body=message.model_dump_json(),
            message_id=f"{blob_path}:{index}",
            application_properties={"message_kind": "normalized_log"},
        )
    return len(messages)


def process_analysis_topic(environment: LocalPipelineEnvironment) -> int:
    count = 0
    for message in environment.service_bus.topic_messages("analysis-input"):
        result = analyze_message(
            raw_body=message.body.encode("utf-8"),
            message_id=message.message_id,
            config=environment.analysis_config,
            read_secret_value=environment.keyvault.read,
            model_caller=environment.model_caller,
        )
        environment.service_bus.publish(
            topic_name=environment.analysis_config.analysis_results_topic_name,
            body=result.model_dump_json(),
            message_id=result.original_message_id,
            application_properties={"message_kind": "analysis_result"},
        )
        count += 1
    return count


def process_decision_topic(environment: LocalPipelineEnvironment) -> int:
    def write_document(endpoint, database_name, container_name, document, key=None):
        del endpoint, database_name, key
        environment.cosmos.upsert(container_name, document)

    count = 0
    for message in environment.service_bus.topic_messages(environment.decision_config.analysis_results_topic_name):
        payload = json.loads(message.body)
        decision = process_analysis_result(
            analysis_result=analyze_result_from_payload(payload),
            config=environment.decision_config,
            write_document=write_document,
        )
        environment.service_bus.publish(
            topic_name=environment.decision_config.final_decisions_topic_name,
            body=decision.model_dump_json(),
            message_id=decision.decision_id,
            application_properties={"message_kind": "decision"},
        )
        count += 1
    return count


def process_local_agent(environment: LocalPipelineEnvironment) -> int:
    import asyncio

    count = 0
    for message in environment.service_bus.topic_messages(environment.decision_config.final_decisions_topic_name):
        payload = json.loads(message.body)
        runtime = LocalAgentRuntime(
            config=environment.local_agent_config,
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(failed_units=["nginx.service"]),
                publish_message=lambda **kwargs: None,
                persist_document=lambda **kwargs: environment.cosmos.upsert(
                    kwargs["container_name"],
                    kwargs["document"],
                ),
                llm_generate=lambda prompt: '{"updated_config_text":"{ services.openssh.enable = true; }"}',
                execute_repair_loop_func=lambda **kwargs: execute_repair_loop(
                    **kwargs,
                    refresh_repo_func=lambda **repo_kwargs: environment.config_repo.refresh(),
                    read_config_func=lambda **repo_kwargs: environment.config_repo.read(),
                    write_config_func=lambda **repo_kwargs: environment.config_repo.write(repo_kwargs["content"]),
                    current_revision_func=lambda **repo_kwargs: environment.config_repo.revision(),
                    commit_and_push_func=lambda **repo_kwargs: environment.config_repo.push(),
                ),
                command_runner=lambda command: CommandResult(returncode=0, stdout=f"executed: {command}", stderr=""),
            ),
        )
        runtime.state = environment.local_agent_state
        result = asyncio.run(handle_decision(runtime, payload))
        environment.local_agent_state = runtime.state
        asyncio.run(persist_pending(runtime))
        execution_result = result.get("execution_result")
        if execution_result is None:
            continue
        environment.local_agent.execute(execution_result["command"], execution_result)
        count += 1
    return count


def publish_observation(
    environment: LocalPipelineEnvironment, observation: Observation, *, message_id: str = "observation-1"
) -> None:
    environment.service_bus.publish(
        topic_name="analysis-input",
        body=observation.model_dump_json(),
        message_id=message_id,
        application_properties={"message_kind": "observation"},
    )


def analyze_result_from_payload(payload: dict[str, object]):
    from schemas import AnalysisResult

    return AnalysisResult.model_validate(payload)


def simulate_opencode_response(*, api_url: str, api_key: str, prompt: str, timeout_seconds: float) -> str:
    del api_url, api_key, timeout_seconds
    lower_prompt = prompt.lower()
    if "failed to start" in lower_prompt or "restart" in lower_prompt or "nginx.service" in lower_prompt:
        return json.dumps(
            {
                "error_type": "service_failure",
                "severity": "critical",
                "root_cause": "service failed to start after configuration change",
                "suggested_action": "apply_config",
                "affected_unit": "nginx.service",
                "confidence": 0.96,
                "analysis_text": "Enable SSH on the target node so inbound SSH connections succeed.",
                "remediation_hint": "services.openssh.enable = true;",
            }
        )
    return json.dumps(
        {
            "error_type": "other",
            "severity": "info",
            "root_cause": "no actionable incident detected",
            "suggested_action": "no_action",
            "confidence": 0.75,
            "analysis_text": "No actionable incident detected.",
            "remediation_hint": "No config change needed.",
        }
    )


def invalid_opencode_response(*, api_url: str, api_key: str, prompt: str, timeout_seconds: float) -> str:
    del api_url, api_key, prompt, timeout_seconds
    return json.dumps(
        {
            "error_type": "other",
            "severity": "fatal",
            "root_cause": "provider emitted invalid severity",
            "suggested_action": "no_action",
            "confidence": 1.2,
            "analysis_text": "provider emitted invalid severity",
        }
    )


def run_pipeline(environment: LocalPipelineEnvironment, *, entries: list[dict[str, object]]) -> dict[str, object]:
    uploader = build_batch_uploader(environment)
    for entry in entries:
        uploader.add_entry(entry)
    uploader.flush()

    uploaded_blob_paths = sorted(environment.blob_storage.blobs)
    normalized_count = 0
    for blob_path in uploaded_blob_paths:
        normalized_count += publish_normalized_logs(environment, blob_path=blob_path)

    analysis_count = process_analysis_topic(environment)
    decision_count = process_decision_topic(environment)
    execution_count = process_local_agent(environment)

    return {
        "uploaded_blob_paths": uploaded_blob_paths,
        "normalized_count": normalized_count,
        "analysis_count": analysis_count,
        "decision_count": decision_count,
        "execution_count": execution_count,
        "analysis_messages": [message.json() for message in environment.service_bus.topic_messages("analysis-input")],
        "analysis_results_messages": [
            message.json()
            for message in environment.service_bus.topic_messages(
                environment.analysis_config.analysis_results_topic_name
            )
        ],
        "decision_topic_messages": [
            message.json()
            for message in environment.service_bus.topic_messages(
                environment.decision_config.final_decisions_topic_name
            )
        ],
        "cosmos_decisions": environment.cosmos.container_items(
            environment.decision_config.cosmos_decisions_container_name
        ),
        "cosmos_execution_results": environment.cosmos.container_items("execution-results"),
        "cosmos_config_snapshots": environment.cosmos.container_items("config-snapshots"),
        "cosmos_repair_traces": environment.cosmos.container_items("repair-traces"),
        "local_agent_commands": list(environment.local_agent.executed_commands),
    }


def run_observation_pipeline(environment: LocalPipelineEnvironment, *, observation: Observation) -> dict[str, object]:
    publish_observation(environment, observation)
    analysis_count = process_analysis_topic(environment)
    decision_count = process_decision_topic(environment)
    execution_count = process_local_agent(environment)
    return {
        "analysis_count": analysis_count,
        "decision_count": decision_count,
        "execution_count": execution_count,
        "analysis_messages": [message.json() for message in environment.service_bus.topic_messages("analysis-input")],
        "analysis_results_messages": [
            message.json()
            for message in environment.service_bus.topic_messages(
                environment.analysis_config.analysis_results_topic_name
            )
        ],
        "decision_topic_messages": [
            message.json()
            for message in environment.service_bus.topic_messages(
                environment.decision_config.final_decisions_topic_name
            )
        ],
        "cosmos_decisions": environment.cosmos.container_items(
            environment.decision_config.cosmos_decisions_container_name
        ),
        "cosmos_execution_results": environment.cosmos.container_items("execution-results"),
        "cosmos_config_snapshots": environment.cosmos.container_items("config-snapshots"),
        "cosmos_repair_traces": environment.cosmos.container_items("repair-traces"),
        "local_agent_commands": list(environment.local_agent.executed_commands),
    }


def simulate_token_failure(
    environment: LocalPipelineEnvironment, *, entries: list[dict[str, object]]
) -> dict[str, object]:
    uploader = BatchUploader(
        config=environment.log_config,
        token_requester=lambda token_service_url, node_id, node_api_key, timeout_seconds: (_ for _ in ()).throw(
            RuntimeError("token service unavailable")
        ),
        payload_uploader=lambda sas_url, payload, timeout_seconds: upload_payload_to_fake_blob_storage(
            environment,
            sas_url,
            payload,
            timeout_seconds=timeout_seconds,
        ),
        sleep=lambda seconds: None,
    )
    for entry in entries:
        uploader.add_entry(entry)
    flush_result = uploader.flush()
    return {
        "flush_result": flush_result,
        "spooled_payloads": uploader.load_spooled_payloads(),
        "uploaded_blob_paths": sorted(environment.blob_storage.blobs),
    }


def simulate_upload_retry_and_recovery(
    environment: LocalPipelineEnvironment, *, entries: list[dict[str, object]]
) -> dict[str, object]:
    attempts = {"count": 0}

    def flaky_uploader(sas_url: str, payload: bytes, *, timeout_seconds: float) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("blob upload failed")
        upload_payload_to_fake_blob_storage(environment, sas_url, payload, timeout_seconds=timeout_seconds)

    uploader = BatchUploader(
        config=environment.log_config,
        token_requester=lambda token_service_url, node_id, node_api_key, timeout_seconds: issue_token_via_service(
            environment,
            node_id=node_id,
            node_api_key=node_api_key,
        ),
        payload_uploader=flaky_uploader,
        sleep=lambda seconds: None,
    )
    for entry in entries:
        uploader.add_entry(entry)
    first_flush = uploader.flush()
    second_flush = uploader.flush()
    return {
        "first_flush": first_flush,
        "second_flush": second_flush,
        "spooled_payloads": uploader.load_spooled_payloads(),
        "uploaded_blob_paths": sorted(environment.blob_storage.blobs),
        "attempts": attempts["count"],
    }


def simulate_malformed_log_blob(environment: LocalPipelineEnvironment) -> dict[str, object]:
    blob_path = "logs/nixos-node-01/bad-blob"
    environment.blob_storage.upload(
        blob_path, json.dumps({"node_id": "nixos-node-01", "entries": [{}]}).encode("utf-8")
    )
    try:
        publish_normalized_logs(environment, blob_path=blob_path)
    except Exception as error:
        return {"error": str(error), "blob_path": blob_path}
    raise AssertionError("malformed blob unexpectedly succeeded")


def simulate_invalid_ai_response(
    environment: LocalPipelineEnvironment, *, entries: list[dict[str, object]]
) -> dict[str, object]:
    uploader = build_batch_uploader(environment)
    for entry in entries:
        uploader.add_entry(entry)
    uploader.flush()
    for blob_path in sorted(environment.blob_storage.blobs):
        publish_normalized_logs(environment, blob_path=blob_path)
    environment.model_caller = invalid_opencode_response
    try:
        process_analysis_topic(environment)
    except Exception as error:
        return {"error_type": type(error).__name__, "error": str(error)}
    raise AssertionError("invalid AI response unexpectedly succeeded")
