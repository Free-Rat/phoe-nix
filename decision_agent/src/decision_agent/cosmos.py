from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential


def upsert_decision_document(
    *,
    endpoint: str,
    database_name: str,
    container_name: str,
    document: dict[str, object],
    key: str | None = None,
) -> None:
    credential = key if key else DefaultAzureCredential()
    client = CosmosClient(url=endpoint, credential=credential)
    container = client.get_database_client(database_name).get_container_client(container_name)
    container.upsert_item(document)
