import json
from collections.abc import Callable
from datetime import UTC, datetime
from urllib import request

from schemas import AnalysisResult


class OpenCodeError(Exception):
    pass


def _uses_chat_completions(api_url: str) -> bool:
    return api_url.rstrip("/").endswith("/chat/completions")


def build_request_payload(*, api_url: str, model: str, prompt: str) -> bytes:
    if _uses_chat_completions(api_url):
        return json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You analyze NixOS operational issues. Reply with exactly one JSON object and no markdown. "
                            "Include fields such as error_type, severity, root_cause, suggested_action, confidence, "
                            "analysis_text, and remediation_hint when supported by the evidence."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
    return json.dumps({"prompt": prompt}).encode("utf-8")


def build_request_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "phoe-nix-analysis-agent/0.1",
    }


def _extract_chat_content(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)
    return None


def extract_response_text(response_body: str) -> str:
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return response_body.strip()

    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict):
                    content = _extract_chat_content(message.get("content"))
                    if content:
                        return content
        for key in ("response", "output", "output_text", "content", "text"):
            value = payload.get(key)
            content = _extract_chat_content(value)
            if content:
                return content
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
    model: str,
    prompt: str,
    timeout_seconds: float,
    urlopen: Callable[..., object] = request.urlopen,
) -> str:
    http_request = request.Request(
        api_url,
        data=build_request_payload(api_url=api_url, model=model, prompt=prompt),
        headers=build_request_headers(api_key),
        method="POST",
    )
    with urlopen(http_request, timeout=timeout_seconds) as response:
        return extract_response_text(response.read().decode("utf-8"))


def normalize_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return 0.5
        if normalized in {"high", "confident"}:
            return 0.9
        if normalized in {"medium", "moderate"}:
            return 0.6
        if normalized in {"low", "weak"}:
            return 0.3
        try:
            return max(0.0, min(1.0, float(normalized)))
        except ValueError:
            return 0.5
    return 0.5


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
        root_cause = str(payload.get("root_cause") or "").strip()
        suggested_action = str(payload.get("suggested_action") or "").strip()
        payload["analysis_text"] = ". ".join(part for part in (root_cause, suggested_action) if part) or "analysis unavailable"

    root_cause = payload.get("root_cause")
    if not isinstance(root_cause, str) or not root_cause.strip():
        payload["root_cause"] = payload["analysis_text"]

    suggested_action = payload.get("suggested_action")
    if not isinstance(suggested_action, str) or not suggested_action.strip():
        payload["suggested_action"] = "no_action"

    remediation_hint = payload.get("remediation_hint")
    if not isinstance(remediation_hint, str) or not remediation_hint.strip():
        payload["remediation_hint"] = payload["analysis_text"]

    payload.setdefault("schema_version", "1.0")
    payload.setdefault("error_type", "other")
    payload.setdefault("severity", "warning")
    payload.setdefault("node_id", node_id)
    payload.setdefault("original_message_id", original_message_id)
    payload.setdefault("source_type", source_type)
    payload.setdefault("affected_unit", fallback_unit)
    payload["confidence"] = normalize_confidence(payload.get("confidence"))
    payload.setdefault("timestamp", current_time.isoformat())
    payload["raw_ai_response"] = raw_response
    try:
        return AnalysisResult.model_validate(payload)
    except Exception as error:
        raise OpenCodeError("invalid analysis response") from error
