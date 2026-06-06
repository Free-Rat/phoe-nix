from __future__ import annotations

import json
from urllib import parse, request

from azure.servicebus import ServiceBusClient, ServiceBusMessage


def _is_mock_connection_string(connection_string: str) -> bool:
    return connection_string.startswith("Endpoint=mock://")


def _mock_base_url(connection_string: str) -> str:
    if not _is_mock_connection_string(connection_string):
        raise ValueError("not a mock connection string")
    endpoint = connection_string.split(";", 1)[0].split("=", 1)[1]
    parsed = parse.urlparse(endpoint)
    host = parsed.netloc or parsed.path
    path = parsed.path.rstrip("/")
    return f"http://{host}{path}"


def _mock_request(base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    http_request = request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=10) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}


def publish_message(*, connection_string: str, topic_name: str, body: str, message_id: str) -> None:
    if _is_mock_connection_string(connection_string):
        _mock_request(
            _mock_base_url(connection_string),
            f"/servicebus/topics/{topic_name}/publish",
            {"body": body, "message_id": message_id, "content_type": "application/json"},
        )
        return
    with ServiceBusClient.from_connection_string(connection_string) as client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            sender.send_messages(ServiceBusMessage(body, content_type="application/json", message_id=message_id))


def build_topic_receiver(*, connection_string: str, topic_name: str, subscription_name: str):
    client = ServiceBusClient.from_connection_string(connection_string)
    return client, client.get_subscription_receiver(topic_name=topic_name, subscription_name=subscription_name)


def receive_messages(*, connection_string: str, topic_name: str, subscription_name: str, max_message_count: int = 1):
    if _is_mock_connection_string(connection_string):
        response = _mock_request(
            _mock_base_url(connection_string),
            f"/servicebus/topics/{topic_name}/subscriptions/{subscription_name}/receive",
            {"max_message_count": max_message_count},
        )
        return response.get("messages", [])
    client, receiver = build_topic_receiver(
        connection_string=connection_string,
        topic_name=topic_name,
        subscription_name=subscription_name,
    )
    with client, receiver:
        return list(receiver.receive_messages(max_message_count=max_message_count, max_wait_time=5))


def complete_message(
    *,
    connection_string: str,
    topic_name: str,
    subscription_name: str,
    raw_message,
):
    if _is_mock_connection_string(connection_string):
        return
    client, receiver = build_topic_receiver(
        connection_string=connection_string,
        topic_name=topic_name,
        subscription_name=subscription_name,
    )
    with client, receiver:
        receiver.complete_message(raw_message)
