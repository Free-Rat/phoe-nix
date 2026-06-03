from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID

from token_service.sas_generator import issue_upload_token


class IssueUploadTokenTests(TestCase):
    @patch("token_service.sas_generator.generate_blob_sas", return_value="sig=test")
    def test_issues_write_scoped_token_for_single_blob(self, generate_blob_sas_mock) -> None:
        response = issue_upload_token(
            node_id="node-01",
            account_name="storageacct",
            container_name="logs",
            account_key="account-key",
            token_ttl_minutes=5,
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
            uuid_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
        )

        self.assertEqual(response.blob_path, "logs/node-01/11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            response.sas_url,
            "https://storageacct.blob.core.windows.net/logs/node-01/11111111-1111-1111-1111-111111111111?sig=test",
        )
        self.assertEqual(response.expires_at, datetime(2026, 1, 1, 0, 5, tzinfo=UTC))

        call_kwargs = generate_blob_sas_mock.call_args.kwargs
        self.assertEqual(call_kwargs["account_name"], "storageacct")
        self.assertEqual(call_kwargs["container_name"], "logs")
        self.assertEqual(call_kwargs["blob_name"], "node-01/11111111-1111-1111-1111-111111111111")
        self.assertEqual(call_kwargs["account_key"], "account-key")
        self.assertTrue(call_kwargs["permission"].write)
        self.assertFalse(call_kwargs["permission"].read)
