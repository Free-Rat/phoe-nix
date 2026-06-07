import json

import azure.functions as func
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from decision_agent.app import process_analysis_result
from decision_agent.config import load_config
from schemas import AnalysisResult


def publish_decision(*, connection_string: str, topic_name: str, decision_body: str, message_id: str) -> None:
    with ServiceBusClient.from_connection_string(connection_string) as client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            sender.send_messages(
                ServiceBusMessage(
                    decision_body,
                    content_type="application/json",
                    message_id=message_id,
                    application_properties={"message_kind": "decision"},
                )
            )


def main(msg: func.ServiceBusMessage) -> None:
    config = load_config()
    raw_body = msg.get_body().decode("utf-8")
    payload_dict = json.loads(raw_body)

    payload = AnalysisResult.model_validate(payload_dict)
    decision = process_analysis_result(analysis_result=payload, config=config)
    publish_decision(
        connection_string=config.servicebus_connection,
        topic_name=config.final_decisions_topic_name,
        decision_body=decision.model_dump_json(),
        message_id=decision.decision_id,
    )


def run_cli() -> None:
    sample = {
        "schema_version": "1.0",
        "node_id": "local-dev",
        "original_message_id": "msg-1",
        "source_type": "log_router",
        "error_type": "service_failure",
        "severity": "critical",
        "root_cause": "nginx config broken",
        "suggested_action": "restart_service",
        "affected_unit": "nginx.service",
        "confidence": 0.9,
        "raw_ai_response": "{}",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    print(json.dumps(sample))
