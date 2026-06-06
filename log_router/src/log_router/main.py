import json
import os

import azure.functions as func
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from log_router.normalizer import normalize_blob


def publish_messages(*, connection_string: str, topic_name: str, blob_path: str, payload: bytes) -> int:
    messages = normalize_blob(payload, blob_path=blob_path)
    with ServiceBusClient.from_connection_string(connection_string) as client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            for index, message in enumerate(messages):
                sender.send_messages(
                    ServiceBusMessage(
                        message.model_dump_json(),
                        content_type="application/json",
                        message_id=f"{blob_path}:{index}",
                    )
                )
    return len(messages)


def main(inputblob: func.InputStream) -> None:
    publish_messages(
        connection_string=os.environ["SERVICEBUS_CONNECTION"],
        topic_name=os.environ["SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME"],
        blob_path=inputblob.name,
        payload=inputblob.read(),
    )


def run_cli() -> None:
    sample_payload = json.dumps(
        {
            "node_id": "local-dev",
            "entries": [
                {
                    "__REALTIME_TIMESTAMP": "1234567890123456",
                    "MESSAGE": "Failed to start nginx.service",
                    "_SYSTEMD_UNIT": "nginx.service",
                    "PRIORITY": "3",
                    "_HOSTNAME": "local-dev",
                    "SYSLOG_IDENTIFIER": "systemd",
                }
            ],
        }
    ).encode("utf-8")
    for message in normalize_blob(sample_payload, blob_path="logs/local-dev/example"):
        print(message.model_dump_json())
