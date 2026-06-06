import unittest
from unittest.mock import patch

from local_agent.persistence import upsert_document


class PersistenceTests(unittest.TestCase):
    class _MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    @patch("local_agent.persistence.CosmosClient")
    def test_upsert_document_uses_account_key_when_provided(self, cosmos_client) -> None:
        upsert_document(
            endpoint="https://cosmos.example",
            key="secret-key",
            database_name="project-healer",
            container_name="observations",
            document={"id": "doc-1"},
        )

        cosmos_client.assert_called_once_with(url="https://cosmos.example", credential="secret-key")

    @patch("local_agent.persistence.DefaultAzureCredential")
    @patch("local_agent.persistence.CosmosClient")
    def test_upsert_document_uses_default_credential_without_key(self, cosmos_client, default_credential) -> None:
        token = object()
        default_credential.return_value = token

        upsert_document(
            endpoint="https://cosmos.example",
            database_name="project-healer",
            container_name="observations",
            document={"id": "doc-1"},
        )

        default_credential.assert_called_once_with()
        cosmos_client.assert_called_once_with(url="https://cosmos.example", credential=token)

    @patch("local_agent.persistence.request.urlopen")
    def test_upsert_document_supports_mock_http_backend(self, urlopen) -> None:
        urlopen.return_value = self._MockResponse()

        upsert_document(
            endpoint="mock+http://127.0.0.1:8088/cosmos",
            database_name="project-healer",
            container_name="observations",
            document={"id": "doc-1"},
            key="mock-key",
        )

        http_request = urlopen.call_args.args[0]
        self.assertEqual(
            http_request.full_url,
            "http://127.0.0.1:8088/cosmos/databases/project-healer/containers/observations/upsert",
        )
