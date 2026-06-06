from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


def build_vault_url(vault_name: str) -> str:
    return f"https://{vault_name}.vault.azure.net"


def read_secret_value(vault_name: str, secret_name: str) -> str:
    client = SecretClient(vault_url=build_vault_url(vault_name), credential=DefaultAzureCredential())
    return client.get_secret(secret_name).value
