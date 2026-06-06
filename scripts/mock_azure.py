#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


class MockAzureState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.blob_root = self.root / "blobs"
        self.cosmos_root = self.root / "cosmos"
        self.servicebus_root = self.root / "servicebus"
        self.blob_root.mkdir(exist_ok=True)
        self.cosmos_root.mkdir(exist_ok=True)
        self.servicebus_root.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._topic_messages: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._subscription_offsets: dict[tuple[str, str], int] = defaultdict(int)

    def reset(self) -> None:
        for path in (self.blob_root, self.cosmos_root, self.servicebus_root):
            for child in path.glob("**/*"):
                if child.is_file():
                    child.unlink()
            for child in sorted(path.glob("**/*"), reverse=True):
                if child.is_dir() and child != path:
                    child.rmdir()
        self.blob_root.mkdir(exist_ok=True)
        self.cosmos_root.mkdir(exist_ok=True)
        self.servicebus_root.mkdir(exist_ok=True)
        with self._lock:
            self._topic_messages.clear()
            self._subscription_offsets.clear()

    def issue_blob_url(self, *, host: str, node_id: str) -> dict[str, object]:
        blob_path = f"logs/{node_id}/{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex}.json"
        return {
            "sas_url": f"mockblob+http://{host}/blob/{blob_path}",
            "blob_path": blob_path,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }

    def put_blob(self, blob_path: str, payload: bytes) -> None:
        destination = self.blob_root / blob_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    def upsert_document(self, database: str, container: str, document: dict[str, object]) -> None:
        container_dir = self.cosmos_root / database / container
        container_dir.mkdir(parents=True, exist_ok=True)
        document_id = str(document.get("id", uuid4().hex))
        (container_dir / f"{document_id}.json").write_text(
            json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
        )

    def publish(self, topic: str, body: str, message_id: str, content_type: str) -> None:
        payload = {
            "body": body,
            "message_id": message_id,
            "content_type": content_type,
            "received_at": datetime.now(UTC).isoformat(),
        }
        topic_dir = self.servicebus_root / topic
        topic_dir.mkdir(parents=True, exist_ok=True)
        event_path = topic_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex}.json"
        event_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        with self._lock:
            self._topic_messages[topic].append(payload)

    def receive(self, topic: str, subscription: str, max_message_count: int) -> list[dict[str, object]]:
        with self._lock:
            offset_key = (topic, subscription)
            offset = self._subscription_offsets[offset_key]
            topic_messages = self._topic_messages[topic]
            messages = topic_messages[offset : offset + max_message_count]
            self._subscription_offsets[offset_key] = offset + len(messages)
            return messages

    def list_topic_messages(self, topic: str) -> list[dict[str, object]]:
        topic_dir = self.servicebus_root / topic
        if not topic_dir.exists():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(topic_dir.glob("*.json"))]

    def list_container_documents(self, database: str, container: str) -> list[dict[str, object]]:
        container_dir = self.cosmos_root / database / container
        if not container_dir.exists():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(container_dir.glob("*.json"))]


def build_handler(state: MockAzureState):
    class Handler(BaseHTTPRequestHandler):
        def _read_json(self) -> dict[str, object]:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(content_length) if content_length else b"{}"
            return json.loads(payload.decode("utf-8"))

        def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            payload = self._read_json()

            if path == "/token":
                node_id = str(payload.get("node_id") or self.headers.get("X-Node-ID") or "unknown-node")
                host = self.headers.get("Host", "127.0.0.1:8088")
                self._send_json(state.issue_blob_url(host=host, node_id=node_id))
                return

            if path.startswith("/servicebus/topics/") and path.endswith("/publish"):
                parts = path.split("/")
                topic = parts[3]
                state.publish(
                    topic=topic,
                    body=str(payload["body"]),
                    message_id=str(payload.get("message_id", uuid4().hex)),
                    content_type=str(payload.get("content_type", "application/json")),
                )
                self._send_json({"published": True})
                return

            if "/subscriptions/" in path and path.endswith("/receive"):
                parts = path.split("/")
                topic = parts[3]
                subscription = parts[5]
                max_count = int(payload.get("max_message_count", 1))
                self._send_json({"messages": state.receive(topic, subscription, max_count)})
                return

            if path.startswith("/cosmos/databases/") and path.endswith("/upsert"):
                parts = path.split("/")
                database = parts[3]
                container = parts[5]
                state.upsert_document(database, container, payload)
                self._send_json({"upserted": True})
                return

            if path == "/reset":
                state.reset()
                self._send_json({"reset": True})
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path.startswith("/blob/"):
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(content_length) if content_length else b""
                state.put_blob(path.removeprefix("/blob/"), payload)
                self.send_response(HTTPStatus.CREATED)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/health":
                self._send_json({"ok": True})
                return
            if path.startswith("/servicebus/topics/"):
                parts = path.split("/")
                topic = parts[3]
                self._send_json({"messages": state.list_topic_messages(topic)})
                return
            if path.startswith("/cosmos/databases/") and "/containers/" in path:
                parts = path.split("/")
                database = parts[3]
                container = parts[5]
                self._send_json({"documents": state.list_container_documents(database, container)})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mock Azure services for phoe-nix")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--state-dir", default="/tmp/phoe-nix-mock-azure")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = MockAzureState(Path(args.state_dir))
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))
    print(json.dumps({"host": args.host, "port": args.port, "state_dir": str(state.root)}, indent=2), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
