import unittest
from unittest.mock import patch

from log_service.storage import upload_log_payload


class _MockResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class MockStorageTests(unittest.TestCase):
    @patch("log_service.storage.request.urlopen")
    def test_upload_log_payload_supports_mockblob_http(self, urlopen) -> None:
        urlopen.return_value = _MockResponse()

        upload_log_payload(
            "mockblob+http://127.0.0.1:8088/blob/logs/node-01/test.json",
            b"payload",
            timeout_seconds=5,
        )

        http_request = urlopen.call_args.args[0]
        self.assertEqual(http_request.full_url, "http://127.0.0.1:8088/blob/logs/node-01/test.json")
        self.assertEqual(http_request.method, "PUT")
