import json
import unittest
from datetime import UTC, datetime

from token_service.app import handle_token_request
from token_service.config import TokenServiceConfig
from token_service.models import TokenResponse


class HandleTokenRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TokenServiceConfig(
            storage_account_name="storageacct",
            logs_container_name="logs",
            keyvault_name="kv-example",
            storage_account_key_secret="StorageAccountKey",
            node_api_key="secret",
            token_ttl_minutes=5,
        )

    def test_returns_unauthorized_for_bad_api_key(self) -> None:
        result = handle_token_request(
            raw_body=b'{"node_id":"node-01"}',
            headers={"x-node-id": "node-01", "x-api-key": "wrong"},
            config=self.config,
            read_storage_account_key=lambda vault_name, secret_name: "account-key",
        )

        self.assertEqual(result.status_code, 401)
        self.assertEqual(json.loads(result.body), {"error": "invalid api key"})

    def test_returns_bad_request_for_invalid_json(self) -> None:
        result = handle_token_request(
            raw_body=b"not-json",
            headers={"x-node-id": "node-01", "x-api-key": "secret"},
            config=self.config,
            read_storage_account_key=lambda vault_name, secret_name: "account-key",
        )

        self.assertEqual(result.status_code, 400)

    def test_returns_token_response_for_valid_request(self) -> None:
        def fake_issue_token(**_: object) -> TokenResponse:
            return TokenResponse(
                sas_url="https://storageacct.blob.core.windows.net/logs/node-01/blob-id?sig=test",
                blob_path="logs/node-01/blob-id",
                expires_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            )

        result = handle_token_request(
            raw_body=b'{"node_id":"node-01"}',
            headers={"x-node-id": "node-01", "x-api-key": "secret"},
            config=self.config,
            read_storage_account_key=lambda vault_name, secret_name: "account-key",
            issue_token=fake_issue_token,
        )

        self.assertEqual(result.status_code, 200)

        payload = json.loads(result.body)
        self.assertEqual(payload["blob_path"].split("/")[0], "logs")
        self.assertIn("sas_url", payload)
        self.assertIn("expires_at", payload)
