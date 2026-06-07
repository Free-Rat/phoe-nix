import json

import azure.functions as func

from token_service.app import handle_token_request
from token_service.config import load_config
from token_service.keyvault import read_secret_value


def _to_http_response(result: tuple[int, str, dict[str, str]]) -> func.HttpResponse:
    return func.HttpResponse(
        body=result[1],
        status_code=result[0],
        headers=result[2],
    )


def main(req: func.HttpRequest) -> func.HttpResponse:
    # Azure Functions calls this entrypoint; everything else stays in plain Python helpers.
    config = load_config()
    result = handle_token_request(
        raw_body=req.get_body(),
        headers=req.headers,
        config=config,
        read_storage_account_key=read_secret_value,
    )
    return _to_http_response(result)


def run_cli() -> None:
    # A tiny CLI is handy for local smoke checks without the Functions host.
    config = load_config()
    result = handle_token_request(
        raw_body=json.dumps({"node_id": "local-dev"}).encode("utf-8"),
        headers={"x-node-id": "local-dev", "x-api-key": config.node_api_key or ""},
        config=config,
        read_storage_account_key=read_secret_value,
    )
    print(result[1])
