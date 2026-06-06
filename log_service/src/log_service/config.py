import os

from pydantic import BaseModel, Field


class LogServiceConfig(BaseModel):
    token_service_url: str
    node_id: str
    node_api_key: str | None = None
    upload_timeout_seconds: float = Field(default=10.0, gt=0)
    batch_size: int = Field(default=100, ge=1)
    flush_interval_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=1.0, gt=0)
    spool_directory: str = "/tmp/phoe-nix-log-service"


def load_config(env: dict[str, str] | None = None) -> LogServiceConfig:
    values = env if env is not None else os.environ
    return LogServiceConfig(
        token_service_url=values["TOKEN_SERVICE_URL"],
        node_id=values["NODE_ID"],
        node_api_key=values.get("NODE_API_KEY"),
        upload_timeout_seconds=float(values.get("UPLOAD_TIMEOUT_SECONDS", "10")),
        batch_size=int(values.get("BATCH_SIZE", "100")),
        flush_interval_seconds=float(values.get("FLUSH_INTERVAL_SECONDS", "30")),
        max_retries=int(values.get("MAX_RETRIES", "3")),
        retry_base_delay_seconds=float(values.get("RETRY_BASE_DELAY_SECONDS", "1")),
        spool_directory=values.get("SPOOL_DIRECTORY", "/tmp/phoe-nix-log-service"),
    )
