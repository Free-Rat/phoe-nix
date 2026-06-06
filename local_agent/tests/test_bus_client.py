import json
import unittest
from unittest.mock import patch

from local_agent.bus_client import complete_message, publish_message, receive_messages


class _MockResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class BusClientTests(unittest.TestCase):
    @patch("local_agent.bus_client.request.urlopen")
    def test_publish_message_uses_mock_http_backend(self, urlopen) -> None:
        urlopen.return_value = _MockResponse({"published": True})

        publish_message(
            connection_string="Endpoint=mock://127.0.0.1:8088",
            topic_name="analysis-input",
            body="{}",
            message_id="msg-1",
        )

        http_request = urlopen.call_args.args[0]
        self.assertEqual(http_request.full_url, "http://127.0.0.1:8088/servicebus/topics/analysis-input/publish")
        self.assertEqual(json.loads(http_request.data.decode("utf-8"))["message_id"], "msg-1")

    @patch("local_agent.bus_client.request.urlopen")
    def test_receive_messages_uses_mock_http_backend(self, urlopen) -> None:
        urlopen.return_value = _MockResponse({"messages": [{"body": "{}", "message_id": "msg-1"}]})

        messages = receive_messages(
            connection_string="Endpoint=mock://127.0.0.1:8088",
            topic_name="final-decisions",
            subscription_name="local-agent",
            max_message_count=2,
        )

        http_request = urlopen.call_args.args[0]
        self.assertEqual(
            http_request.full_url,
            "http://127.0.0.1:8088/servicebus/topics/final-decisions/subscriptions/local-agent/receive",
        )
        self.assertEqual(messages[0]["message_id"], "msg-1")

    def test_complete_message_noops_for_mock_backend(self) -> None:
        self.assertIsNone(
            complete_message(
                connection_string="Endpoint=mock://127.0.0.1:8088",
                topic_name="final-decisions",
                subscription_name="local-agent",
                raw_message={"body": "{}"},
            )
        )
