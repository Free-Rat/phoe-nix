import unittest

from token_service.auth import AuthenticationError, authenticate_node_request


class AuthenticateNodeRequestTests(unittest.TestCase):
    def test_accepts_matching_node_identity_and_api_key(self) -> None:
        authenticate_node_request(
            {"X-Node-ID": "node-01", "X-API-Key": "secret"},
            expected_api_key="secret",
            requested_node_id="node-01",
        )

    def test_rejects_mismatched_node_identity(self) -> None:
        with self.assertRaises(AuthenticationError):
            authenticate_node_request(
                {"X-Node-ID": "node-02", "X-API-Key": "secret"},
                expected_api_key="secret",
                requested_node_id="node-01",
            )

    def test_rejects_invalid_api_key(self) -> None:
        with self.assertRaises(AuthenticationError):
            authenticate_node_request(
                {"X-Node-ID": "node-01", "X-API-Key": "wrong"},
                expected_api_key="secret",
                requested_node_id="node-01",
            )
