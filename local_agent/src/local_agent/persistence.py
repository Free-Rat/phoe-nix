from __future__ import annotations

import json
from urllib import parse, request

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential


def upsert_document(
    *,
    endpoint: str,
    database_name: str,
    container_name: str,
    document: dict[str, object],
    key: str | None = None,
) -> None:
    if endpoint.startswith("mock+http://") or endpoint.startswith("mock+https://"):
        mock_url = endpoint.replace("mock+", "", 1)
        parsed = parse.urlparse(mock_url)
        path = parsed.path.rstrip("/")
        http_request = request.Request(
            f"{parsed.scheme}://{parsed.netloc}{path}/databases/{database_name}/containers/{container_name}/upsert",
            data=json.dumps(document).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Mock-Key": key or ""},
            method="POST",
        )
        with request.urlopen(http_request, timeout=10):
            return
    credential = key if key else DefaultAzureCredential()
    client = CosmosClient(url=endpoint, credential=credential)
    container = client.get_database_client(database_name).get_container_client(container_name)
    container.upsert_item(document)
