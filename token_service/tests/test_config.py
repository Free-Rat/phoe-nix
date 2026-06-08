import unittest

from token_service.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_requires_node_api_key(self) -> None:
        with self.assertRaises(KeyError):
            load_config(
                {
                    "STORAGE_ACCOUNT_NAME": "storageacct",
                    "KEYVAULT_NAME": "kv-example",
                }
            )

    def test_load_config_reads_node_api_key(self) -> None:
        config = load_config(
            {
                "STORAGE_ACCOUNT_NAME": "storageacct",
                "KEYVAULT_NAME": "kv-example",
                "NODE_API_KEY": "secret",
            }
        )

        self.assertEqual(config.node_api_key, "secret")
