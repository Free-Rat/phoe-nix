import unittest

from local_agent.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_disables_backends_when_env_marks_them_off(self) -> None:
        config = load_config(
            {
                "SERVICEBUS_ENABLED": "0",
                "SERVICEBUS_CONNECTION": "Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
                "COSMOSDB_ENABLED": "false",
                "COSMOSDB_ENDPOINT": "https://cosmos.example",
                "COSMOSDB_DATABASE_NAME": "project-healer",
                "NODE_ID": "node-01",
            }
        )

        self.assertFalse(config.servicebus_enabled)
        self.assertFalse(config.cosmos_enabled)

    def test_load_config_enables_backends_when_connection_values_exist(self) -> None:
        config = load_config(
            {
                "SERVICEBUS_CONNECTION": "Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
                "COSMOSDB_ENDPOINT": "https://cosmos.example",
                "COSMOSDB_DATABASE_NAME": "project-healer",
                "COSMOSDB_KEY": "secret-key",
                "NODE_ID": "node-01",
            }
        )

        self.assertTrue(config.servicebus_enabled)
        self.assertTrue(config.cosmos_enabled)
        self.assertEqual(config.cosmos_key, "secret-key")

    def test_load_config_uses_decision_poll_env_overrides(self) -> None:
        config = load_config(
            {
                "SERVICEBUS_CONNECTION": "Endpoint=sb://example/;SharedAccessKeyName=test;SharedAccessKey=secret",
                "COSMOSDB_ENDPOINT": "https://cosmos.example",
                "COSMOSDB_DATABASE_NAME": "project-healer",
                "NODE_ID": "node-01",
                "DECISION_POLL_BASE_SECONDS": "0.25",
                "DECISION_POLL_MAX_SECONDS": "2.5",
            }
        )

        self.assertEqual(config.decision_poll_base_seconds, 0.25)
        self.assertEqual(config.decision_poll_max_seconds, 2.5)
