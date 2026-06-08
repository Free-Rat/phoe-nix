import os

from pydantic import BaseModel, Field


class TokenServiceConfig(BaseModel):
    storage_account_name: str
    logs_container_name: str = "logs"
    keyvault_name: str
    storage_account_key_secret: str = "StorageAccountKey"
    node_api_key: str
    token_ttl_minutes: int = Field(default=5, ge=1, le=60)


def load_config(env: dict[str, str] | None = None) -> TokenServiceConfig:
    values = env if env is not None else os.environ

    # Keep configuration loading in one place so the request handler stays pure.
    return TokenServiceConfig(
        storage_account_name=values["STORAGE_ACCOUNT_NAME"],
        logs_container_name=values.get("LOGS_CONTAINER_NAME", "logs"),
        keyvault_name=values["KEYVAULT_NAME"],
        storage_account_key_secret=values.get("STORAGE_ACCOUNT_KEY_SECRET", "StorageAccountKey"),
        node_api_key=values["NODE_API_KEY"],
        token_ttl_minutes=int(values.get("TOKEN_TTL_MINUTES", "5")),
    )
