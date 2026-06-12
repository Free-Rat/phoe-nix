import json
import unittest
from unittest.mock import patch

from local_agent.ollama_client import OllamaError, generate_text


class OllamaClientTests(unittest.TestCase):
    def test_generate_text_parses_response_field(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def read(self):
                return json.dumps({"response": "hello"}).encode("utf-8")

        with patch("local_agent.ollama_client.request.urlopen", return_value=FakeResponse()):
            result = generate_text(base_url="http://ollama", model="gemma", prompt="hi", timeout_seconds=1)
        self.assertEqual(result, "hello")

    def test_generate_text_rejects_missing_response(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def read(self):
                return json.dumps({}).encode("utf-8")

        with patch("local_agent.ollama_client.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(OllamaError):
                generate_text(base_url="http://ollama", model="gemma", prompt="hi", timeout_seconds=1)

    def test_generate_text_surfaces_ollama_error_field(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def read(self):
                return json.dumps({"error": "model requires more system memory"}).encode("utf-8")

        with patch("local_agent.ollama_client.request.urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(OllamaError, "requires more system memory"):
                generate_text(base_url="http://ollama", model="gpt-oss:20b", prompt="hi", timeout_seconds=1)
