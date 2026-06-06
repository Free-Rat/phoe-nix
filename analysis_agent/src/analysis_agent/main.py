import json

import azure.functions as func
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from analysis_agent.config import load_config
from analysis_agent.keyvault import read_secret_value
from analysis_agent.message_handler import analyze_message


def publish_analysis_result(*, connection_string: str, topic_name: str, message_id: str, result_body: str) -> None:
    with ServiceBusClient.from_connection_string(connection_string) as client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            sender.send_messages(
                ServiceBusMessage(
                    result_body,
                    content_type="application/json",
                    message_id=message_id,
                    application_properties={"message_kind": "analysis_result"},
                )
            )


def main(msg: func.ServiceBusMessage) -> None:
    config = load_config()
    analysis_result = analyze_message(
        raw_body=msg.get_body(),
        message_id=msg.metadata.get("MessageId", getattr(msg, "message_id", "unknown")),
        config=config,
        read_secret_value=read_secret_value,
    )
    publish_analysis_result(
        connection_string=config.servicebus_connection,
        topic_name=config.analysis_results_topic_name,
        message_id=analysis_result.original_message_id,
        result_body=analysis_result.model_dump_json(),
    )


def run_cli() -> None:
    sample_message = {
        "schema_version": "1.0",
        "node_id": "local-dev",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": "Failed to start nginx.service",
        "unit": "nginx.service",
        "priority": 3,
        "hostname": "local-dev",
        "source": "log_router",
        "blob_path": "logs/local-dev/example",
    }
    print(json.dumps(sample_message))
