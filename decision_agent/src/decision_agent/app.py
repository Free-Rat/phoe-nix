from decision_agent.config import DecisionAgentConfig
from decision_agent.cosmos import upsert_decision_document
from decision_agent.decision_engine import build_decision, build_decision_document
from schemas import AnalysisResult, Decision


def process_analysis_result(
    *,
    analysis_result: AnalysisResult,
    config: DecisionAgentConfig,
    write_document=upsert_decision_document,
) -> Decision:
    decision = build_decision(analysis_result)
    write_document(
        endpoint=config.cosmos_endpoint,
        database_name=config.cosmos_database_name,
        container_name=config.cosmos_decisions_container_name,
        document=build_decision_document(decision),
        key=config.cosmos_key,
    )
    return decision
