import json
import unittest

from log_service.token_client import build_token_request_headers, parse_storage_token_response


class TokenClientTests(unittest.TestCase):
    def test_build_token_request_headers_includes_required_identity_headers(self) -> None:
        headers = build_token_request_headers("node-01", "secret")

        self.assertEqual(headers["X-Node-ID"], "node-01")
        self.assertEqual(headers["X-API-Key"], "secret")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_parse_storage_token_response_builds_typed_response(self) -> None:
        response = parse_storage_token_response(
            json.dumps(
                {
                    "sas_url": "https://storage.blob.core.windows.net/logs/node-01/blob?sig=test",
                    "blob_path": "logs/node-01/blob",
                    "expires_at": "2026-01-01T00:05:00Z",
                }
            ).encode("utf-8")
        )

        self.assertEqual(response.blob_path, "logs/node-01/blob")
        self.assertEqual(response.sas_url, "https://storage.blob.core.windows.net/logs/node-01/blob?sig=test")
