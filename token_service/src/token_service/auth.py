from collections.abc import Mapping


class AuthenticationError(Exception):
    pass


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def authenticate_node_request(
    headers: Mapping[str, str],
    *,
    expected_api_key: str,
    requested_node_id: str,
) -> None:
    normalized_headers = _normalize_headers(headers)

    # The repo plan requires the body node_id to match the authenticated identity.
    header_node_id = normalized_headers.get("x-node-id")
    if header_node_id != requested_node_id:
        raise AuthenticationError("x-node-id must match the requested node_id")

    # API key auth is intentionally simple for the POC and can be swapped later.
    if normalized_headers.get("x-api-key") != expected_api_key:
        raise AuthenticationError("invalid api key")
