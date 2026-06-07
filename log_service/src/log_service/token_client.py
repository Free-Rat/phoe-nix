import json
from urllib import request
from urllib.error import HTTPError, URLError

from log_service.models import StorageTokenResponse


class TokenServiceError(Exception):
    pass


def build_token_request_headers(node_id: str, node_api_key: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Node-ID": node_id,
    }
    if node_api_key is not None:
        headers["X-API-Key"] = node_api_key
    return headers


def parse_storage_token_response(body: bytes) -> StorageTokenResponse:
    payload = json.loads(body.decode("utf-8"))
    return StorageTokenResponse.model_validate(payload)


def request_storage_token(
    token_service_url: str,
    *,
    node_id: str,
    node_api_key: str | None,
    timeout_seconds: float,
) -> StorageTokenResponse:
    payload = json.dumps({"node_id": node_id}).encode("utf-8")
    http_request = request.Request(
        token_service_url,
        data=payload,
        headers=build_token_request_headers(node_id, node_api_key),
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            return parse_storage_token_response(response.read())
    except HTTPError as error:
        raise TokenServiceError(f"token service returned HTTP {error.code}") from error
    except URLError as error:
        raise TokenServiceError("token service is unreachable") from error
