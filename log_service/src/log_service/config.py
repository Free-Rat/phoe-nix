import os

from pydantic import BaseModel, Field


class LogServiceConfig(BaseModel):
    token_service_url: str
    node_id: str
    node_api_key: str | None = None

    # Keep uploads simple for now: each observed entry becomes one blob upload.
    upload_timeout_seconds: float = Field(default=10.0, gt=0)


def load_config(env: dict[str, str] | None = None) -> LogServiceConfig:
    values = env if env is not None else os.environ
    return LogServiceConfig(
        token_service_url=values["TOKEN_SERVICE_URL"],
        node_id=values["NODE_ID"],
        node_api_key=values.get("NODE_API_KEY"),
        upload_timeout_seconds=float(values.get("UPLOAD_TIMEOUT_SECONDS", "10")),
    )
