from __future__ import annotations

import json
from urllib import request
from urllib.error import HTTPError, URLError


class OllamaError(RuntimeError):
    pass


def generate_text(*, base_url: str, model: str, prompt: str, timeout_seconds: float) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    http_request = request.Request(
        url=f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise OllamaError(f"ollama HTTP error: {error.code} {detail}") from error
    except URLError as error:
        raise OllamaError(f"ollama connection failed: {error.reason}") from error

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise OllamaError("ollama returned invalid JSON") from error
    response_text = decoded.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise OllamaError("ollama response did not contain text")
    return response_text.strip()
