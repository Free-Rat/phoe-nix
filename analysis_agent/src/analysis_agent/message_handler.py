import json
from dataclasses import dataclass
from typing import Literal

from analysis_agent.ai_client import call_opencode_api, parse_analysis_response
from analysis_agent.config import AnalysisAgentConfig
from analysis_agent.prompt_builder import build_prompt
from schemas import AnalysisResult, NormalizedLog, Observation


@dataclass(frozen=True)
class AnalysisInput:
    source_type: Literal["log_router", "local_agent"]
    message: NormalizedLog | Observation
    fallback_unit: str | None


def parse_message(raw_body: bytes) -> AnalysisInput:
    payload = json.loads(raw_body.decode("utf-8"))
    source = payload.get("source")
    if source == "local_agent":
        observation = Observation.model_validate(payload)
        return AnalysisInput(source_type="local_agent", message=observation, fallback_unit=None)

    normalized_log = NormalizedLog.model_validate(payload)
    return AnalysisInput(source_type="log_router", message=normalized_log, fallback_unit=normalized_log.unit)


def analyze_message(
    *,
    raw_body: bytes,
    message_id: str,
    config: AnalysisAgentConfig,
    read_secret_value,
    model_caller=call_opencode_api,
) -> AnalysisResult:
    parsed = parse_message(raw_body)
    prompt = build_prompt(parsed.message)
    api_key = read_secret_value(config.keyvault_name, config.opencode_api_key_secret)
    raw_response = model_caller(
        api_url=config.opencode_api_url,
        api_key=api_key,
        model=config.opencode_model,
        prompt=prompt,
        timeout_seconds=config.ai_timeout_seconds,
    )
    return parse_analysis_response(
        raw_response,
        node_id=parsed.message.node_id,
        original_message_id=message_id,
        source_type=parsed.source_type,
        fallback_unit=parsed.fallback_unit,
    )
