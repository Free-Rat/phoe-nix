import json
from dataclasses import dataclass, field


@dataclass
class FakeBlobObject:
    path: str
    payload: bytes


@dataclass
class FakeBlobStorage:
    blobs: dict[str, FakeBlobObject] = field(default_factory=dict)

    def upload(self, blob_path: str, payload: bytes) -> str:
        self.blobs[blob_path] = FakeBlobObject(path=blob_path, payload=payload)
        return f"https://blob.local/{blob_path}?sig=fake"

    def read(self, blob_path: str) -> bytes:
        return self.blobs[blob_path].payload


@dataclass
class FakeServiceBusMessage:
    topic: str
    body: str
    message_id: str
    content_type: str = "application/json"
    application_properties: dict[str, object] = field(default_factory=dict)

    def json(self) -> dict[str, object]:
        return json.loads(self.body)


@dataclass
class FakeServiceBus:
    topics: dict[str, list[FakeServiceBusMessage]] = field(default_factory=dict)

    def publish(
        self,
        *,
        topic_name: str,
        body: str,
        message_id: str,
        content_type: str = "application/json",
        application_properties: dict[str, object] | None = None,
    ) -> FakeServiceBusMessage:
        message = FakeServiceBusMessage(
            topic=topic_name,
            body=body,
            message_id=message_id,
            content_type=content_type,
            application_properties=application_properties or {},
        )
        self.topics.setdefault(topic_name, []).append(message)
        return message

    def topic_messages(self, topic_name: str) -> list[FakeServiceBusMessage]:
        return list(self.topics.get(topic_name, []))


@dataclass
class FakeCosmosContainer:
    items: list[dict[str, object]] = field(default_factory=list)

    def upsert(self, document: dict[str, object]) -> None:
        self.items = [item for item in self.items if item.get("id") != document.get("id")]
        self.items.append(document)


@dataclass
class FakeCosmos:
    containers: dict[str, FakeCosmosContainer] = field(default_factory=dict)

    def upsert(self, container_name: str, document: dict[str, object]) -> None:
        self.containers.setdefault(container_name, FakeCosmosContainer()).upsert(document)

    def container_items(self, container_name: str) -> list[dict[str, object]]:
        container = self.containers.get(container_name)
        if container is None:
            return []
        return list(container.items)


@dataclass
class FakeLocalAgent:
    executed_commands: list[str] = field(default_factory=list)
    execution_results: list[dict[str, object]] = field(default_factory=list)

    def execute(self, command: str, execution_result: dict[str, object]) -> None:
        self.executed_commands.append(command)
        self.execution_results.append(execution_result)


@dataclass
class FakeConfigRepo:
    current_config_text: str = "{ }"
    revision_counter: int = 0
    refresh_count: int = 0
    push_count: int = 0

    def refresh(self) -> None:
        self.refresh_count += 1

    def read(self) -> str:
        return self.current_config_text

    def write(self, content: str) -> None:
        self.current_config_text = content

    def revision(self) -> str:
        return f"rev-{self.revision_counter}"

    def push(self) -> tuple[bool, str]:
        self.push_count += 1
        self.revision_counter += 1
        return True, "push succeeded"


@dataclass
class FakeKeyVault:
    secrets: dict[str, str]

    def read(self, vault_name: str, secret_name: str) -> str:
        return self.secrets[secret_name]
