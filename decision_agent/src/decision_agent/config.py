import os

from pydantic import BaseModel


class DecisionAgentConfig(BaseModel):
    servicebus_connection: str
    analysis_results_topic_name: str
    final_decisions_topic_name: str
    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_decisions_container_name: str = "decisions"


def load_config(env: dict[str, str] | None = None) -> DecisionAgentConfig:
    values = env if env is not None else os.environ
    return DecisionAgentConfig(
        servicebus_connection=values["SERVICEBUS_CONNECTION"],
        analysis_results_topic_name=values.get("SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME", "analysis-results"),
        final_decisions_topic_name=values.get("SERVICEBUS_TOPIC_FINAL_DECISIONS_NAME", "final-decisions"),
        cosmos_endpoint=values["COSMOSDB_ENDPOINT"],
        cosmos_database_name=values["COSMOSDB_DATABASE_NAME"],
        cosmos_decisions_container_name=values.get("COSMOSDB_DECISIONS_CONTAINER_NAME", "decisions"),
    )
