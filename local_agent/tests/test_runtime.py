import asyncio
import json
import unittest
from datetime import UTC, datetime

from local_agent.config import LocalAgentConfig
from local_agent.runtime import (
    LocalAgentRuntime,
    RuntimeDependencies,
    _coerce_message_body_to_bytes,
    _message_body_to_payload,
    decision_worker,
    observe_once,
    persist_pending,
    run_daemon,
    run_runtime_once,
)
from schemas import NodeState


class RuntimeTests(unittest.TestCase):
    def build_config(self) -> LocalAgentConfig:
        return LocalAgentConfig(
            servicebus_connection="Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
            cosmos_endpoint="https://cosmos.example",
            cosmos_database_name="project-healer",
            node_id="node-01",
            config_repo_path="/tmp/phoe-nix-config",
        )

    def test_observe_once_publishes_and_enqueues_documents(self) -> None:
        published = []
        runtime = LocalAgentRuntime(
            config=self.build_config(),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(failed_units=["nginx.service"]),
                publish_message=lambda **kwargs: published.append(kwargs),
                persist_document=lambda **kwargs: None,
            ),
        )
        result = asyncio.run(observe_once(runtime))
        self.assertTrue(result)
        self.assertEqual(len(published), 1)
        self.assertGreater(runtime.persist_queue.qsize(), 0)

    def test_run_runtime_once_handles_config_repair_decision(self) -> None:
        persisted = []
        decision = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Enable SSH",
            "remediation_text": "services.openssh.enable = true;",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }

        class Attempt:
            attempt_number = 1
            prompt = "prompt"
            model_response = "response"
            previous_config = "{ }"
            proposed_config = "{ services.openssh.enable = true; }"
            test_command = "nixos-rebuild test"
            test_result = type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
            switch_command = "nixos-rebuild switch"
            switch_result = type("Result", (), {"returncode": 0, "stdout": "switched", "stderr": ""})()
            push_success = True
            push_message = "pushed"

        outcome = type(
            "Outcome",
            (),
            {
                "success": True,
                "executed_command": "nixos-rebuild test && nixos-rebuild switch",
                "stdout": "ok",
                "stderr": "",
                "repo_revision_before": "rev-before",
                "repo_revision_after": "rev-after",
                "attempts": [Attempt()],
                "final_config_text": "{ services.openssh.enable = true; }",
            },
        )()

        result = asyncio.run(
            run_runtime_once(
                config=self.build_config(),
                decision_payloads=[decision],
                dependencies=RuntimeDependencies(
                    read_node_state=lambda: NodeState(failed_units=["sshd.service"]),
                    publish_message=lambda **kwargs: None,
                    llm_generate=lambda prompt: prompt,
                    execute_repair_loop_func=lambda **kwargs: outcome,
                    persist_document=lambda **kwargs: persisted.append((kwargs["container_name"], kwargs["document"])),
                ),
            )
        )
        self.assertEqual(result["decision_results"][0]["repair_attempts"], 1)
        self.assertTrue(any(container == "repair-traces" for container, _ in persisted))

    def test_run_runtime_once_skips_no_action_decision(self) -> None:
        decision = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "no_action",
            "command": "",
            "severity": "info",
            "confidence": 0.9,
            "analysis_summary": "Nothing to do",
            "remediation_text": "No repair needed.",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }

        result = asyncio.run(
            run_runtime_once(
                config=self.build_config(),
                decision_payloads=[decision],
                dependencies=RuntimeDependencies(
                    read_node_state=lambda: NodeState(),
                    publish_message=lambda **kwargs: None,
                    execute_repair_loop_func=lambda **kwargs: self.fail("no_action must not run repair"),
                    persist_document=lambda **kwargs: None,
                ),
            )
        )

        self.assertEqual(result["decision_results"][0], {"status": "no_action"})

    def test_config_repair_rejects_decision_for_another_node(self) -> None:
        decision = {
            "decision_id": "dec-1",
            "node_id": "node-02",
            "analysis_id": "analysis-1",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Enable SSH",
            "remediation_text": "services.openssh.enable = true;",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }

        result = asyncio.run(
            run_runtime_once(
                config=self.build_config(),
                decision_payloads=[decision],
                dependencies=RuntimeDependencies(
                    read_node_state=lambda: NodeState(),
                    publish_message=lambda **kwargs: None,
                    execute_repair_loop_func=lambda **kwargs: self.fail("wrong-node decision must not run repair"),
                    persist_document=lambda **kwargs: None,
                ),
            )
        )

        self.assertEqual(result["decision_results"][0]["error"], "decision targeted at another node")

    def test_config_repair_respects_cooldown(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        decision = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Enable SSH",
            "remediation_text": "services.openssh.enable = true;",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }

        result = asyncio.run(
            run_runtime_once(
                config=self.build_config(),
                decision_payloads=[decision],
                dependencies=RuntimeDependencies(
                    read_node_state=lambda: NodeState(last_remediation_timestamp=now),
                    publish_message=lambda **kwargs: None,
                    now_factory=lambda: now,
                    execute_repair_loop_func=lambda **kwargs: self.fail("cooldown must block repair"),
                    persist_document=lambda **kwargs: None,
                ),
            )
        )

        self.assertEqual(result["decision_results"][0]["error"], "remediation is blocked by safety limits")

    def test_persist_pending_flushes_queue(self) -> None:
        persisted = []
        runtime = LocalAgentRuntime(
            config=self.build_config(),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(),
                persist_document=lambda **kwargs: persisted.append(kwargs),
            ),
        )
        asyncio.run(runtime.enqueue_persist("docs", {"id": "1"}))
        count = asyncio.run(persist_pending(runtime))
        self.assertEqual(count, 1)
        self.assertEqual(len(persisted), 1)

    def test_persist_pending_discards_queue_when_cosmos_disabled(self) -> None:
        persisted = []
        runtime = LocalAgentRuntime(
            config=self.build_config().model_copy(update={"cosmos_enabled": False}),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(),
                persist_document=lambda **kwargs: persisted.append(kwargs),
            ),
        )
        asyncio.run(runtime.enqueue_persist("docs", {"id": "1"}))
        count = asyncio.run(persist_pending(runtime))
        self.assertEqual(count, 0)
        self.assertEqual(len(persisted), 0)
        self.assertEqual(runtime.persist_queue.qsize(), 0)

    def test_observe_once_skips_publish_when_servicebus_disabled(self) -> None:
        published = []
        runtime = LocalAgentRuntime(
            config=self.build_config().model_copy(update={"servicebus_enabled": False}),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(failed_units=["nginx.service"]),
                publish_message=lambda **kwargs: published.append(kwargs),
                persist_document=lambda **kwargs: None,
            ),
        )
        result = asyncio.run(observe_once(runtime))
        self.assertTrue(result)
        self.assertEqual(published, [])

    def test_run_daemon_processes_received_decision(self) -> None:
        persisted = []
        decision = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Enable SSH",
            "remediation_text": "services.openssh.enable = true;",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }

        class Attempt:
            attempt_number = 1
            prompt = "prompt"
            model_response = "response"
            previous_config = "{ }"
            proposed_config = "{ services.openssh.enable = true; }"
            test_command = "nixos-rebuild test"
            test_result = type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
            switch_command = "nixos-rebuild switch"
            switch_result = type("Result", (), {"returncode": 0, "stdout": "switched", "stderr": ""})()
            push_success = True
            push_message = "pushed"

        outcome = type(
            "Outcome",
            (),
            {
                "success": True,
                "executed_command": "nixos-rebuild test && nixos-rebuild switch",
                "stdout": "ok",
                "stderr": "",
                "repo_revision_before": "rev-before",
                "repo_revision_after": "rev-after",
                "attempts": [Attempt()],
                "final_config_text": "{ services.openssh.enable = true; }",
            },
        )()

        counts = {"called": 0}

        def receive_messages(**kwargs):
            del kwargs
            if counts["called"] == 0:
                counts["called"] += 1
                return [{"body": __import__("json").dumps(decision)}]
            return []

        result = asyncio.run(
            run_daemon(
                config=self.build_config(),
                dependencies=RuntimeDependencies(
                    read_node_state=lambda: NodeState(failed_units=["sshd.service"]),
                    publish_message=lambda **kwargs: None,
                    persist_document=lambda **kwargs: persisted.append(kwargs),
                    receive_messages=receive_messages,
                    complete_message=lambda **kwargs: None,
                    llm_generate=lambda prompt: prompt,
                    execute_repair_loop_func=lambda **kwargs: outcome,
                    sleep=lambda seconds: asyncio.sleep(0),
                ),
                observe_iterations=1,
                decision_idle_cycles=1,
                persist_idle_cycles=2,
            )
        )
        self.assertEqual(result["decisions"], 1)
        self.assertTrue(any(item["container_name"] == "repair-traces" for item in persisted))

    def test_decision_worker_persists_failure_and_skips_completion_on_handler_error(self) -> None:
        persisted = []
        completed = []
        calls = {"count": 0}

        class Message:
            message_id = "msg-1"

            def get_body(self):
                return b'{"not":"a decision"}'

        def receive_messages(**kwargs):
            del kwargs
            if calls["count"] == 0:
                calls["count"] += 1
                return [Message()]
            return []

        runtime = LocalAgentRuntime(
            config=self.build_config(),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(),
                persist_document=lambda **kwargs: persisted.append(kwargs),
                receive_messages=receive_messages,
                complete_message=lambda **kwargs: completed.append(kwargs),
                sleep=lambda seconds: asyncio.sleep(0),
            ),
        )

        processed = asyncio.run(decision_worker(runtime, stop_after_idle_cycles=1))
        asyncio.run(persist_pending(runtime))

        self.assertEqual(processed, 0)
        self.assertEqual(completed, [])
        self.assertTrue(
            any(
                item["container_name"] == "service-status"
                and item["document"]["status"] == "failed"
                and item["document"]["correlation_id"] == "msg-1"
                for item in persisted
            )
        )

    def test_decision_worker_applies_exponential_backoff_on_persistent_receive_errors(self) -> None:
        sleeps: list[float] = []

        def receive_messages(**kwargs):
            del kwargs
            raise RuntimeError("namespace unreachable")

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        runtime = LocalAgentRuntime(
            config=self.build_config().model_copy(
                update={"decision_poll_base_seconds": 0.05, "decision_poll_max_seconds": 0.4}
            ),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(),
                persist_document=lambda **kwargs: None,
                receive_messages=receive_messages,
                complete_message=lambda **kwargs: None,
                sleep=record_sleep,
            ),
        )

        processed = asyncio.run(decision_worker(runtime, stop_after_idle_cycles=5))

        self.assertEqual(processed, 0)
        # First error stays at base; subsequent errors double up to the cap.
        # stop_after_idle_cycles=5 returns *before* the 5th sleep, so we
        # observe the first four backoff values.
        self.assertEqual(sleeps, [0.05, 0.1, 0.2, 0.4])

    def test_decision_worker_resets_backoff_after_successful_receive(self) -> None:
        sleeps: list[float] = []
        calls = {"count": 0}
        decision_payload = json.dumps({"decision_id": "dec-1", "node_id": "node-01"})

        def receive_messages(**kwargs):
            del kwargs
            calls["count"] += 1
            if calls["count"] <= 2:
                raise RuntimeError("transient error")
            if calls["count"] == 3:
                return [{"body": decision_payload, "message_id": "msg-1"}]
            return []

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        runtime = LocalAgentRuntime(
            config=self.build_config().model_copy(
                update={"decision_poll_base_seconds": 0.05, "decision_poll_max_seconds": 0.4}
            ),
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(),
                persist_document=lambda **kwargs: None,
                receive_messages=receive_messages,
                complete_message=lambda **kwargs: None,
                sleep=record_sleep,
            ),
        )

        # Note: the decision payload above is intentionally incomplete (missing
        # required fields). handle_decision will raise and the worker will
        # record 'failed' status; that's enough to prove the backoff reset
        # path runs after a successful receive.
        processed = asyncio.run(decision_worker(runtime, stop_after_idle_cycles=3))

        # Two error cycles (first at base, second doubled), then a successful
        # receive that resets to base, then idle at base.
        self.assertEqual(sleeps[:3], [0.05, 0.1, 0.05])
        self.assertEqual(processed, 0)


class MessageBodyToPayloadTests(unittest.TestCase):
    """Cover the various shapes an incoming SB message body can take."""

    def test_dict_with_string_body(self) -> None:
        payload = json.dumps({"decision_id": "d-1", "node_id": "n", "action": "no_action"})
        self.assertEqual(
            _message_body_to_payload({"body": payload, "message_id": "m-1"}),
            {"decision_id": "d-1", "node_id": "n", "action": "no_action"},
        )

    def test_dict_with_bytes_body(self) -> None:
        payload = json.dumps({"decision_id": "d-2"}).encode("utf-8")
        self.assertEqual(
            _message_body_to_payload({"body": payload}),
            {"decision_id": "d-2"},
        )

    def test_dict_with_no_body_key_returns_message_as_is(self) -> None:
        # The mock backend sometimes returns shapes without a "body" key
        # (e.g. already-parsed payloads).
        self.assertEqual(
            _message_body_to_payload({"decision_id": "d-3"}),
            {"decision_id": "d-3"},
        )

    def test_service_bus_received_message_with_generator_body(self) -> None:
        """azure-servicebus 7.x exposes ``message.body`` as a generator."""

        class FakeReceivedMessage:
            def __init__(self, chunks: list[bytes]) -> None:
                self.body = (chunk for chunk in chunks)
                self.message_id = "msg-gen"

        payload = json.dumps({"decision_id": "d-4", "node_id": "n"}).encode("utf-8")
        message = FakeReceivedMessage([payload[:10], payload[10:]])
        self.assertEqual(
            _message_body_to_payload(message),
            {"decision_id": "d-4", "node_id": "n"},
        )

    def test_service_bus_received_message_with_get_body_method(self) -> None:
        """Older SDK + some transports expose ``get_body()`` returning bytes."""

        class FakeLegacyMessage:
            def get_body(self) -> bytes:
                return json.dumps({"decision_id": "d-5"}).encode("utf-8")

        self.assertEqual(
            _message_body_to_payload(FakeLegacyMessage()),
            {"decision_id": "d-5"},
        )

    def test_service_bus_received_message_with_bytes_body(self) -> None:
        class FakeBytesMessage:
            body = json.dumps({"decision_id": "d-6"}).encode("utf-8")

        self.assertEqual(
            _message_body_to_payload(FakeBytesMessage()),
            {"decision_id": "d-6"},
        )

    def test_missing_body_attribute_falls_back_to_empty_dict(self) -> None:
        class EmptyMessage:
            pass

        self.assertEqual(_message_body_to_payload(EmptyMessage()), {})

    def test_invalid_body_raises_type_error(self) -> None:
        class BadMessage:
            body = 12345  # type: ignore[assignment]

        with self.assertRaises(TypeError):
            _message_body_to_payload(BadMessage())


class CoerceMessageBodyToBytesTests(unittest.TestCase):
    def test_bytes_passthrough(self) -> None:
        self.assertEqual(_coerce_message_body_to_bytes(b"abc"), b"abc")

    def test_bytearray_converted(self) -> None:
        self.assertEqual(_coerce_message_body_to_bytes(bytearray(b"abc")), b"abc")

    def test_string_encoded(self) -> None:
        self.assertEqual(_coerce_message_body_to_bytes("abc"), b"abc")

    def test_dict_serialised(self) -> None:
        self.assertEqual(
            _coerce_message_body_to_bytes({"x": 1}),
            b'{"x": 1}',
        )

    def test_list_of_bytes_joined(self) -> None:
        self.assertEqual(_coerce_message_body_to_bytes([b"a", b"b"]), b"ab")

    def test_generator_of_bytes_joined(self) -> None:
        self.assertEqual(
            _coerce_message_body_to_bytes(chunk for chunk in [b"a", b"b"]),
            b"ab",
        )

    def test_unsupported_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            _coerce_message_body_to_bytes(12345)
