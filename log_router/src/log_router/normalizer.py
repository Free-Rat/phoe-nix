import json
from datetime import UTC, datetime

from schemas import NormalizedLog


def parse_blob_payload(payload: bytes) -> tuple[str, list[dict[str, object]]]:
    data = json.loads(payload.decode("utf-8"))
    node_id = str(data["node_id"])
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    return node_id, [dict(entry) for entry in entries]


def timestamp_from_journal(raw_timestamp: object) -> datetime:
    if raw_timestamp is None:
        raise ValueError("missing __REALTIME_TIMESTAMP")

    raw_text = str(raw_timestamp)
    try:
        microseconds = int(raw_text)
    except ValueError:
        iso_text = raw_text.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_text).astimezone(UTC)
    return datetime.fromtimestamp(microseconds / 1_000_000, tz=UTC)


def normalize_entry(entry: dict[str, object], *, node_id: str, blob_path: str) -> NormalizedLog:
    message = str(entry.get("MESSAGE", "")).strip()
    if not message:
        raise ValueError("journal entry is missing MESSAGE")

    priority = entry.get("PRIORITY")
    return NormalizedLog(
        node_id=node_id,
        timestamp=timestamp_from_journal(entry.get("__REALTIME_TIMESTAMP")),
        unit=str(entry["_SYSTEMD_UNIT"]) if entry.get("_SYSTEMD_UNIT") is not None else None,
        priority=int(str(priority)) if priority is not None else None,
        message=message,
        hostname=str(entry["_HOSTNAME"]) if entry.get("_HOSTNAME") is not None else None,
        source_identifier=str(entry["SYSLOG_IDENTIFIER"]) if entry.get("SYSLOG_IDENTIFIER") is not None else None,
        blob_path=blob_path,
    )


def normalize_blob(payload: bytes, *, blob_path: str) -> list[NormalizedLog]:
    node_id, entries = parse_blob_payload(payload)
    return [normalize_entry(entry, node_id=node_id, blob_path=blob_path) for entry in entries]
