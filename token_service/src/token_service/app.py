import json
from collections.abc import Callable, Mapping

from token_service.auth import AuthenticationError, authenticate_node_request
from token_service.config import TokenServiceConfig
from token_service.models import ErrorResponse, TokenRequest, TokenResponse
from token_service.sas_generator import issue_upload_token


class HttpResult(tuple[int, str, dict[str, str]]):
    __slots__ = ()

    @property
    def status_code(self) -> int:
        return self[0]

    @property
    def body(self) -> str:
        return self[1]

    @property
    def headers(self) -> dict[str, str]:
        return self[2]


def json_response(status_code: int, payload: TokenResponse | ErrorResponse) -> HttpResult:
    return HttpResult(
        (
            status_code,
            payload.model_dump_json(),
            {"Content-Type": "application/json"},
        )
    )


def parse_json_body(raw_body: bytes) -> dict[str, object]:
    if not raw_body:
        raise ValueError("request body is required")

    return json.loads(raw_body.decode("utf-8"))


def handle_token_request(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    config: TokenServiceConfig,
    read_storage_account_key: Callable[[str, str], str],
    issue_token: Callable[..., TokenResponse] = issue_upload_token,
) -> HttpResult:
    try:
        request_payload = TokenRequest.model_validate(parse_json_body(raw_body))
        authenticate_node_request(
            headers,
            expected_api_key=config.node_api_key,
            requested_node_id=request_payload.node_id,
        )
        account_key = read_storage_account_key(config.keyvault_name, config.storage_account_key_secret)

        # All side effects are pushed to the edges; the core token builder stays deterministic.
        response = issue_token(
            node_id=request_payload.node_id,
            account_name=config.storage_account_name,
            container_name=config.logs_container_name,
            account_key=account_key,
            token_ttl_minutes=config.token_ttl_minutes,
        )
        return json_response(200, response)
    except AuthenticationError as error:
        return json_response(401, ErrorResponse(error=str(error)))
    except KeyError as error:
        return json_response(500, ErrorResponse(error=f"missing configuration: {error.args[0]}"))
    except ValueError as error:
        return json_response(400, ErrorResponse(error=str(error)))
    except Exception:
        # Avoid leaking secrets or internal state to callers.
        return json_response(500, ErrorResponse(error="internal server error"))
