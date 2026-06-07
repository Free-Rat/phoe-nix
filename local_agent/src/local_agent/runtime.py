from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from local_agent.bus_client import complete_message, publish_message, receive_messages
from local_agent.config import LocalAgentConfig
from local_agent.executor import CommandResult, execute_decision
from local_agent.monitor import build_observation, current_state_hash, should_publish_observation
from local_agent.ollama_client import generate_text
from local_agent.persistence import upsert_document
from local_agent.repair_planner import RepairOutcome, execute_repair_loop
from local_agent.reporter import (
    build_config_snapshot_document,
    build_execution_result,
    build_node_state_document,
    build_observation_document,
    build_repair_trace_document,
    build_service_status_document,
    current_time,
)
from local_agent.state import (
    LocalAgentState,
    can_apply_remediation,
    new_state,
    record_remediation,
    update_node_state,
)
from local_agent.system_state import collect_node_state
from schemas import Decision, NodeState


@dataclass(frozen=True)
class PersistRequest:
    container_name: str
    document: dict[str, object]


@dataclass
class RuntimeDependencies:
    read_node_state: Callable[[], NodeState] = collect_node_state
    publish_message: Callable[..., None] | None = publish_message
    persist_document: Callable[..., None] = upsert_document
    llm_generate: Callable[[str], str] | None = None
    execute_repair_loop_func: Callable[..., RepairOutcome] = execute_repair_loop
    command_runner: Callable[[str], CommandResult] | None = None
    receive_messages: Callable[..., list[object]] = receive_messages
    complete_message: Callable[..., None] = complete_message
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    now_factory: Callable[[], datetime] = current_time


@dataclass
class LocalAgentRuntime:
    config: LocalAgentConfig
    dependencies: RuntimeDependencies
    state: LocalAgentState = field(init=False)
    persist_queue: asyncio.Queue[PersistRequest] = field(init=False)
    last_repo_refresh_at: datetime | None = None

    def __post_init__(self) -> None:
        self.state = new_state(self.config.node_id)
        self.persist_queue = asyncio.Queue()

    async def enqueue_persist(self, container_name: str, document: dict[str, object]) -> None:
        await self.persist_queue.put(PersistRequest(container_name=container_name, document=document))


async def observe_once(runtime: LocalAgentRuntime) -> bool:
    node_state = runtime.dependencies.read_node_state()
    previous_hash = runtime.state.last_observation_hash
    publish = should_publish_observation(previous_hash, node_state)
    runtime.state = update_node_state(runtime.state, node_state)
    if not publish:
        return False

    observation_type = "state_change" if previous_hash else "periodic_state"
    observation = build_observation(runtime.config.node_id, node_state, observation_type=observation_type)
    if (
        runtime.config.servicebus_enabled
        and runtime.config.servicebus_connection.strip()
        and runtime.dependencies.publish_message is not None
    ):
        try:
            runtime.dependencies.publish_message(
                connection_string=runtime.config.servicebus_connection,
                topic_name=runtime.config.analysis_input_topic_name,
                body=observation.model_dump_json(),
                message_id=f"{runtime.config.node_id}:{observation.timestamp.isoformat()}",
            )
        except Exception:
            pass

    await runtime.enqueue_persist(
        runtime.config.cosmos_observations_container_name,
        build_observation_document(observation),
    )
    await runtime.enqueue_persist(
        runtime.config.cosmos_node_state_container_name,
        build_node_state_document(runtime.config.node_id, node_state),
    )
    await runtime.enqueue_persist(
        runtime.config.cosmos_service_status_container_name,
        build_service_status_document(
            node_id=runtime.config.node_id,
            stage="observation",
            status="published",
            correlation_id=runtime.config.node_id,
            detail=current_state_hash(node_state),
        ),
    )
    return True


async def persist_pending(runtime: LocalAgentRuntime) -> int:
    persisted = 0
    while not runtime.persist_queue.empty():
        request = await runtime.persist_queue.get()
        if (
            not runtime.config.cosmos_enabled
            or not runtime.config.cosmos_endpoint.strip()
            or not runtime.config.cosmos_database_name.strip()
        ):
            continue
        runtime.dependencies.persist_document(
            endpoint=runtime.config.cosmos_endpoint,
            key=runtime.config.cosmos_key,
            database_name=runtime.config.cosmos_database_name,
            container_name=request.container_name,
            document=request.document,
        )
        persisted += 1
    return persisted


async def _enqueue_decision_status(
    runtime: LocalAgentRuntime, *, status: str, correlation_id: str, detail: str
) -> None:
    await runtime.enqueue_persist(
        runtime.config.cosmos_service_status_container_name,
        build_service_status_document(
            node_id=runtime.config.node_id,
            stage="decision",
            status=status,
            correlation_id=correlation_id,
            detail=detail,
        ),
    )


def _repair_llm(runtime: LocalAgentRuntime) -> Callable[[str], str]:
    if runtime.dependencies.llm_generate is not None:
        return runtime.dependencies.llm_generate
    return lambda prompt: generate_text(
        base_url=runtime.config.ollama_base_url,
        model=runtime.config.ollama_model,
        prompt=prompt,
        timeout_seconds=runtime.config.ollama_timeout_seconds,
    )


async def handle_decision(runtime: LocalAgentRuntime, decision_payload: dict[str, object]) -> dict[str, object]:
    decision = Decision.model_validate(decision_payload)
    await runtime.enqueue_persist(
        runtime.config.cosmos_service_status_container_name,
        build_service_status_document(
            node_id=runtime.config.node_id,
            stage="decision",
            status="received",
            correlation_id=decision.decision_id,
            detail=decision.analysis_id,
        ),
    )

    if decision.node_id != runtime.config.node_id:
        await _enqueue_decision_status(
            runtime,
            status="skipped",
            correlation_id=decision.decision_id,
            detail="decision targeted at another node",
        )
        return {"error": "decision targeted at another node"}

    if decision.action == "no_action":
        await _enqueue_decision_status(
            runtime,
            status="skipped",
            correlation_id=decision.decision_id,
            detail="no action requested",
        )
        return {"status": "no_action"}

    if decision.command:
        next_state, execution_result, error = execute_decision(
            decision=decision,
            state=runtime.state,
            local_node_id=runtime.config.node_id,
            cooldown_seconds=runtime.config.cooldown_seconds,
            max_remediations_per_hour=runtime.config.max_remediations_per_hour,
            timeout_seconds=60,
            command_runner=runtime.dependencies.command_runner,
            now_factory=runtime.dependencies.now_factory,
        )
        if error is not None or execution_result is None:
            return {"error": error or "execution failed"}
        runtime.state = next_state
        await runtime.enqueue_persist(
            runtime.config.cosmos_execution_results_container_name,
            {"id": execution_result.execution_id, **execution_result.model_dump(mode="json")},
        )
        await runtime.enqueue_persist(
            runtime.config.cosmos_node_state_container_name,
            build_node_state_document(runtime.config.node_id, next_state.node_state),
        )
        return {"execution_result": execution_result.model_dump(mode="json")}

    if decision.action != "apply_config":
        await _enqueue_decision_status(
            runtime,
            status="failed",
            correlation_id=decision.decision_id,
            detail="decision produced no executable repair plan",
        )
        return {"error": "decision produced no executable repair plan"}

    now = runtime.dependencies.now_factory()
    if not can_apply_remediation(
        runtime.state,
        cooldown_seconds=runtime.config.cooldown_seconds,
        max_remediations_per_hour=runtime.config.max_remediations_per_hour,
        now=now,
    ):
        await _enqueue_decision_status(
            runtime,
            status="blocked",
            correlation_id=decision.decision_id,
            detail="remediation is blocked by safety limits",
        )
        return {"error": "remediation is blocked by safety limits"}

    repair_outcome = runtime.dependencies.execute_repair_loop_func(
        decision=decision,
        node_state=runtime.state.node_state,
        repo_url=runtime.config.config_repo_url,
        repo_path=runtime.config.config_repo_path,
        branch=runtime.config.config_repo_branch,
        config_file_path=runtime.config.config_file_path,
        max_attempts=runtime.config.repair_max_attempts,
        rebuild_test_command=runtime.config.rebuild_test_command,
        rebuild_switch_command=runtime.config.rebuild_switch_command,
        llm_generate=_repair_llm(runtime),
        command_runner=runtime.dependencies.command_runner,
    )

    node_state_after = NodeState(failed_units=[] if repair_outcome.success else runtime.state.node_state.failed_units)
    completed_at = runtime.dependencies.now_factory()
    started_at = completed_at - timedelta(seconds=len(repair_outcome.attempts))
    execution_result = build_execution_result(
        decision=decision,
        executed_command=repair_outcome.executed_command,
        exit_code=0 if repair_outcome.success else 1,
        stdout=repair_outcome.stdout,
        stderr=repair_outcome.stderr,
        started_at=started_at,
        completed_at=completed_at,
        node_state_after=node_state_after,
    )
    runtime.state = record_remediation(runtime.state, now=completed_at, node_state_after=node_state_after)
    await runtime.enqueue_persist(
        runtime.config.cosmos_execution_results_container_name,
        {"id": execution_result.execution_id, **execution_result.model_dump(mode="json")},
    )
    await runtime.enqueue_persist(
        runtime.config.cosmos_node_state_container_name,
        build_node_state_document(runtime.config.node_id, runtime.state.node_state),
    )
    for attempt in repair_outcome.attempts:
        await runtime.enqueue_persist(
            runtime.config.cosmos_config_snapshots_container_name,
            build_config_snapshot_document(
                node_id=runtime.config.node_id,
                decision_id=decision.decision_id,
                attempt_number=attempt.attempt_number,
                repo_revision_before=repair_outcome.repo_revision_before,
                repo_revision_after=repair_outcome.repo_revision_after,
                config_path=runtime.config.config_file_path,
                before_text=attempt.previous_config,
                after_text=attempt.proposed_config,
            ),
        )
        await runtime.enqueue_persist(
            runtime.config.cosmos_repair_traces_container_name,
            build_repair_trace_document(
                node_id=runtime.config.node_id,
                decision_id=decision.decision_id,
                analysis_id=decision.analysis_id,
                attempt=attempt,
                repo_revision_before=repair_outcome.repo_revision_before,
                repo_revision_after=repair_outcome.repo_revision_after,
            ),
        )
    await runtime.enqueue_persist(
        runtime.config.cosmos_service_status_container_name,
        build_service_status_document(
            node_id=runtime.config.node_id,
            stage="repair",
            status="completed" if repair_outcome.success else "failed",
            correlation_id=decision.decision_id,
            detail=repair_outcome.repo_revision_after,
        ),
    )
    return {
        "execution_result": execution_result.model_dump(mode="json"),
        "repair_attempts": len(repair_outcome.attempts),
        "repo_revision_after": repair_outcome.repo_revision_after,
    }


async def run_runtime_once(
    *,
    config: LocalAgentConfig,
    decision_payloads: list[dict[str, object]] | None = None,
    dependencies: RuntimeDependencies | None = None,
) -> dict[str, object]:
    runtime = LocalAgentRuntime(config=config, dependencies=dependencies or RuntimeDependencies())
    observation_published = await observe_once(runtime)
    decision_results: list[dict[str, object]] = []
    for payload in decision_payloads or []:
        decision_results.append(await handle_decision(runtime, payload))
    persisted_count = await persist_pending(runtime)
    return {
        "observation_published": observation_published,
        "decision_results": decision_results,
        "persisted_count": persisted_count,
        "node_state": runtime.state.node_state.model_dump(mode="json"),
    }


def _message_body_to_payload(message: object) -> dict[str, object]:
    if isinstance(message, dict):
        if "body" in message:
            body = message["body"]
            if isinstance(body, str):
                return json.loads(body)
            if isinstance(body, bytes):
                return json.loads(body.decode("utf-8"))
        return message
    body = message.get_body() if hasattr(message, "get_body") else getattr(message, "body", b"{}")
    if isinstance(body, bytes):
        return json.loads(body.decode("utf-8"))
    if isinstance(body, str):
        return json.loads(body)
    return json.loads(bytes(body).decode("utf-8"))


def _message_correlation_id(message: object) -> str:
    if isinstance(message, dict):
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message_id:
            return message_id
        body = message.get("body")
        if isinstance(body, str):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return "unknown"
            decision_id = payload.get("decision_id")
            if isinstance(decision_id, str) and decision_id:
                return decision_id
        return "unknown"
    message_id = getattr(message, "message_id", None)
    if isinstance(message_id, str) and message_id:
        return message_id
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        metadata_message_id = metadata.get("MessageId")
        if isinstance(metadata_message_id, str) and metadata_message_id:
            return metadata_message_id
    return "unknown"


async def decision_worker(runtime: LocalAgentRuntime, *, stop_after_idle_cycles: int | None = None) -> int:
    processed = 0
    idle_cycles = 0
    while True:
        if not runtime.config.servicebus_enabled or not runtime.config.servicebus_connection.strip():
            idle_cycles += 1
            if stop_after_idle_cycles is not None and idle_cycles >= stop_after_idle_cycles:
                return processed
            await runtime.dependencies.sleep(0.05)
            continue
        try:
            messages = await asyncio.to_thread(
                runtime.dependencies.receive_messages,
                connection_string=runtime.config.servicebus_connection,
                topic_name=runtime.config.final_decisions_topic_name,
                subscription_name=runtime.config.decision_subscription_name,
                max_message_count=1,
            )
        except Exception as error:
            await _enqueue_decision_status(
                runtime,
                status="receive_failed",
                correlation_id=runtime.config.node_id,
                detail=str(error),
            )
            messages = []
        if not messages:
            idle_cycles += 1
            if stop_after_idle_cycles is not None and idle_cycles >= stop_after_idle_cycles:
                return processed
            await runtime.dependencies.sleep(0.05)
            continue

        idle_cycles = 0
        for message in messages:
            correlation_id = _message_correlation_id(message)
            try:
                payload = _message_body_to_payload(message)
                await handle_decision(runtime, payload)
            except Exception as error:
                await _enqueue_decision_status(
                    runtime,
                    status="failed",
                    correlation_id=correlation_id,
                    detail=str(error),
                )
                continue
            processed += 1
            if not isinstance(message, dict):
                try:
                    await asyncio.to_thread(
                        runtime.dependencies.complete_message,
                        connection_string=runtime.config.servicebus_connection,
                        topic_name=runtime.config.final_decisions_topic_name,
                        subscription_name=runtime.config.decision_subscription_name,
                        raw_message=message,
                    )
                except Exception as error:
                    await _enqueue_decision_status(
                        runtime,
                        status="complete_failed",
                        correlation_id=correlation_id,
                        detail=str(error),
                    )


async def observe_worker(runtime: LocalAgentRuntime, *, iterations: int | None = None) -> int:
    count = 0
    while iterations is None or count < iterations:
        try:
            await observe_once(runtime)
        except Exception:
            pass
        count += 1
        if iterations is None or count < iterations:
            await runtime.dependencies.sleep(runtime.config.observe_interval_seconds)
    return count


async def persist_worker(runtime: LocalAgentRuntime, *, stop_after_idle_cycles: int | None = None) -> int:
    persisted = 0
    idle_cycles = 0
    while True:
        pending = await persist_pending(runtime)
        persisted += pending
        if pending == 0:
            idle_cycles += 1
            if stop_after_idle_cycles is not None and idle_cycles >= stop_after_idle_cycles:
                return persisted
            await runtime.dependencies.sleep(0.05)
        else:
            idle_cycles = 0


async def run_daemon(
    *,
    config: LocalAgentConfig,
    dependencies: RuntimeDependencies | None = None,
    observe_iterations: int | None = None,
    decision_idle_cycles: int | None = None,
    persist_idle_cycles: int | None = None,
) -> dict[str, int]:
    runtime = LocalAgentRuntime(config=config, dependencies=dependencies or RuntimeDependencies())
    observe_task = asyncio.create_task(observe_worker(runtime, iterations=observe_iterations))
    decision_task = asyncio.create_task(decision_worker(runtime, stop_after_idle_cycles=decision_idle_cycles))
    persist_task = asyncio.create_task(persist_worker(runtime, stop_after_idle_cycles=persist_idle_cycles))
    observed, processed, persisted = await asyncio.gather(observe_task, decision_task, persist_task)
    return {
        "observations": observed,
        "decisions": processed,
        "persisted": persisted,
    }
