import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_agent.executor import CommandResult
from local_agent.repair_planner import (
    build_repair_prompt,
    execute_repair_loop,
    extract_config_text,
    sync_local_hardware_configuration,
)
from schemas import Decision, NodeState


class RepairPlannerTests(unittest.TestCase):
    def build_decision(self, **overrides) -> Decision:
        payload = {
            "decision_id": "dec-1",
            "node_id": "node-01",
            "analysis_id": "analysis-1",
            "action": "apply_config",
            "command": "",
            "severity": "critical",
            "confidence": 0.9,
            "analysis_summary": "Enable SSH on the node.",
            "remediation_text": "services.openssh.enable = true;",
            "idempotency_key": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        payload.update(overrides)
        return Decision.model_validate(payload)

    def test_extract_config_text_supports_json_payload(self) -> None:
        self.assertEqual(
            extract_config_text('{"updated_config_text":"{ services.openssh.enable = true; }"}'),
            "{ services.openssh.enable = true; }",
        )

    def test_build_repair_prompt_includes_previous_error(self) -> None:
        prompt = build_repair_prompt(
            decision=self.build_decision(),
            node_state=NodeState(failed_units=["sshd.service"]),
            current_config="{ }",
            previous_error="syntax error",
            attempt_number=2,
        )
        self.assertIn("syntax error", prompt)
        self.assertIn("Attempt: 2", prompt)

    def test_execute_repair_loop_retries_after_failed_test(self) -> None:
        configs = {"text": "{ }"}
        revisions = iter(["rev-before", "rev-after"])
        commands = []
        llm_responses = iter(
            [
                '{"updated_config_text":"{ services.openssh.enable = true }"}',
                '{"updated_config_text":"{ services.openssh.enable = true; }"}',
            ]
        )
        command_results = iter(
            [
                CommandResult(returncode=1, stdout="", stderr="missing semicolon"),
                CommandResult(returncode=0, stdout="test ok", stderr=""),
                CommandResult(returncode=0, stdout="switch ok", stderr=""),
            ]
        )

        outcome = execute_repair_loop(
            decision=self.build_decision(),
            node_state=NodeState(),
            repo_url="https://example/repo.git",
            repo_path="/tmp/repo",
            branch="main",
            config_file_path="configuration.nix",
            max_attempts=3,
            rebuild_test_command="nixos-rebuild test",
            rebuild_switch_command="nixos-rebuild switch",
            llm_generate=lambda prompt: next(llm_responses),
            command_runner=lambda command: commands.append(command) or next(command_results),
            refresh_repo_func=lambda **kwargs: None,
            read_config_func=lambda **kwargs: configs["text"],
            write_config_func=lambda **kwargs: configs.__setitem__("text", kwargs["content"]),
            current_revision_func=lambda **kwargs: next(revisions),
            commit_and_push_func=lambda **kwargs: (True, "pushed"),
        )

        self.assertTrue(outcome.success)
        self.assertEqual(len(outcome.attempts), 2)
        self.assertEqual(commands, ["nixos-rebuild test", "nixos-rebuild test", "nixos-rebuild switch"])

    def test_sync_local_hardware_configuration_copies_missing_file(self) -> None:
        with TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "hardware-configuration.nix"
            repo = Path(tempdir) / "repo"
            repo.mkdir()
            source.write_text('{ fileSystems."/" = { }; }', encoding="utf-8")

            with patch("local_agent.repair_planner.Path") as path_cls:

                def build_path(value: str) -> Path:
                    if value == "/etc/nixos/hardware-configuration.nix":
                        return source
                    return Path(value)

                path_cls.side_effect = build_path
                sync_local_hardware_configuration(repo_path=str(repo))

            self.assertEqual(
                (repo / "hardware-configuration.nix").read_text(encoding="utf-8"),
                '{ fileSystems."/" = { }; }',
            )
