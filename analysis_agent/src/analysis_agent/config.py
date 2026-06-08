import os

from pydantic import BaseModel, Field


class AnalysisAgentConfig(BaseModel):
    servicebus_connection: str
    analysis_results_topic_name: str
    keyvault_name: str
    opencode_api_key_secret: str = "OpenCodeApiKey"
    opencode_api_url: str = "https://opencode.ai/zen/go/v1/chat/completions"
    opencode_model: str = "deepseek-v4-flash"
    ai_timeout_seconds: float = Field(default=30.0, gt=0)


def load_config(env: dict[str, str] | None = None) -> AnalysisAgentConfig:
    values = env if env is not None else os.environ
    return AnalysisAgentConfig(
        servicebus_connection=values["SERVICEBUS_CONNECTION"],
        analysis_results_topic_name=values.get("SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME", "analysis-results"),
        keyvault_name=values["KEYVAULT_NAME"],
        opencode_api_key_secret=values.get("OPENCODE_API_KEY_SECRET", "OpenCodeApiKey"),
        opencode_api_url=values.get("OPENCODE_API_URL", "https://opencode.ai/zen/go/v1/chat/completions"),
        opencode_model=values.get("OPENCODE_MODEL", "deepseek-v4-flash"),
        ai_timeout_seconds=float(values.get("AI_TIMEOUT_SECONDS", "30")),
    )
