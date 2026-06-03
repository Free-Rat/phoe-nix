import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel


class TokenResponse(BaseModel):
    sas_url: str
    blob_path: str
    expires_at: datetime


def build_blob_path(node_id: str) -> str:
    return f"logs/{node_id}/{uuid4()}"


def issue_placeholder_token(node_id: str) -> TokenResponse:
    blob_path = build_blob_path(node_id)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    return TokenResponse(
        sas_url=f"https://example.invalid/{blob_path}?sig=stub",
        blob_path=blob_path,
        expires_at=expires_at,
    )


def main() -> None:
    response = issue_placeholder_token("local-dev")
    print(json.dumps(response.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
