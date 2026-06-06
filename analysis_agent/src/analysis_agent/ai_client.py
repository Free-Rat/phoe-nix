import json
from collections.abc import Callable
from datetime import UTC, datetime
from urllib import request

from schemas import AnalysisResult


class OpenCodeError(Exception):
    pass


def build_request_payload(prompt: str) -> bytes:
    return json.dumps({"prompt": prompt}).encode("utf-8")


def build_request_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def extract_response_text(response_body: str) -> str:
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return response_body.strip()

    if isinstance(payload, dict):
        for key in ("response", "output", "output_text", "content", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value.strip()
    return response_body.strip()


def strip_markdown_fence(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_json_object(raw_text: str) -> dict[str, object]:
    return json.loads(strip_markdown_fence(raw_text))


def call_opencode_api(
    *,
    api_url: str,
    api_key: str,
    prompt: str,
    timeout_seconds: float,
    urlopen: Callable[..., object] = request.urlopen,
) -> str:
    http_request = request.Request(
        api_url,
        data=build_request_payload(prompt),
        headers=build_request_headers(api_key),
        method="POST",
    )
    with urlopen(http_request, timeout=timeout_seconds) as response:
        return extract_response_text(response.read().decode("utf-8"))


def parse_analysis_response(
    raw_response: str,
    *,
    node_id: str,
    original_message_id: str,
    source_type: str,
    fallback_unit: str | None,
    now_factory: Callable[[], datetime] | None = None,
) -> AnalysisResult:
    try:
        payload = parse_json_object(raw_response)
    except json.JSONDecodeError:
        stripped_response = strip_markdown_fence(raw_response)
        payload = {
            "error_type": "other",
            "severity": "warning",
            "root_cause": stripped_response,
            "suggested_action": "investigate",
            "confidence": 0.5,
            "analysis_text": stripped_response,
            "remediation_hint": stripped_response,
        }

    current_time = now_factory() if now_factory is not None else datetime.now(UTC)
    analysis_text = payload.get("analysis_text")
    if not isinstance(analysis_text, str) or not analysis_text.strip():
        root_cause = str(payload.get("root_cause", "")).strip()
        suggested_action = str(payload.get("suggested_action", "")).strip()
        payload["analysis_text"] = ". ".join(part for part in (root_cause, suggested_action) if part)
    payload.setdefault("remediation_hint", payload.get("analysis_text"))
    payload.setdefault("schema_version", "1.0")
    payload.setdefault("error_type", "other")
    payload.setdefault("severity", "warning")
    payload.setdefault("node_id", node_id)
    payload.setdefault("original_message_id", original_message_id)
    payload.setdefault("source_type", source_type)
    payload.setdefault("root_cause", payload.get("analysis_text", "analysis unavailable"))
    payload.setdefault("suggested_action", "no_action")
    payload.setdefault("affected_unit", fallback_unit)
    payload.setdefault("confidence", 0.5)
    payload.setdefault("timestamp", current_time.isoformat())
    payload["raw_ai_response"] = raw_response
    try:
        return AnalysisResult.model_validate(payload)
    except Exception as error:
        raise OpenCodeError("invalid analysis response") from error
