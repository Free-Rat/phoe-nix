import asyncio
import json
import os

from local_agent.config import load_config
from local_agent.monitor import build_observation
from local_agent.reporter import build_node_state_document
from local_agent.runtime import RuntimeDependencies, run_daemon, run_runtime_once
from schemas import NodeState


def run_cli() -> None:
    config = load_config()
    if os.environ.get("LOCAL_AGENT_RUN_MODE", "sample") == "daemon":
        result = asyncio.run(run_daemon(config=config))
        print(json.dumps(result))
        return

    observation = build_observation(
        config.node_id,
        NodeState(failed_units=["nginx.service"], restart_counts={"nginx.service": 2}),
        observation_type="periodic_state",
    )
    print(observation.model_dump_json())


def run_once(decision_payload: dict[str, object]) -> dict[str, object]:
    config = load_config()
    result = asyncio.run(
        run_runtime_once(
            config=config,
            decision_payloads=[decision_payload],
            dependencies=RuntimeDependencies(
                read_node_state=lambda: NodeState(failed_units=["nginx.service"]),
                command_runner=lambda command: type(
                    "Result", (), {"returncode": 0, "stdout": f"executed: {command}", "stderr": ""}
                )(),
                llm_generate=lambda prompt: '{"updated_config_text":"{ services.openssh.enable = true; }"}',
                persist_document=lambda **kwargs: None,
            ),
        )
    )
    if result["decision_results"] and "error" in result["decision_results"][0]:
        return {"error": result["decision_results"][0]["error"]}
    return {
        "execution_result": result["decision_results"][0].get("execution_result")
        if result["decision_results"]
        else None,
        "node_state_document": build_node_state_document(config.node_id, new_state(config.node_id).node_state),
    }


if __name__ == "__main__":
    run_cli()
