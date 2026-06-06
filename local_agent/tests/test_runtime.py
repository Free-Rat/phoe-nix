import asyncio
import unittest

from local_agent.config import LocalAgentConfig
from local_agent.runtime import (
    RuntimeDependencies,
    run_daemon,
    decision_worker,
    handle_decision,
    observe_once,
    persist_pending,
    run_runtime_once,
    LocalAgentRuntime,
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
