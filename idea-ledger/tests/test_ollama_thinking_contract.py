from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "idea-ledger" / "tools"))

import semantic_lab_ollama  # noqa: E402,F401  patches OllamaClient transport
from semantic_lab import OllamaClient, SemanticLabError  # noqa: E402


class InspectableOllamaClient(OllamaClient):
    def __init__(self, response):
        super().__init__("http://example.invalid")
        self.response_data = response
        self.last_payload = None

    def _request(self, method, path, payload=None):
        self.last_payload = payload
        return dict(self.response_data)


class OllamaThinkingContractTests(unittest.TestCase):
    def test_thinking_is_disabled_for_structured_contract_output(self):
        client = InspectableOllamaClient({"response": "{\"ok\": true}", "done": True})
        result = client.generate_json("qwen3:4b", "prompt", system="test")
        self.assertEqual(result, {"ok": True})
        self.assertIs(client.last_payload["think"], False)
        self.assertIs(client.last_payload["stream"], False)
        self.assertEqual(client.last_payload["format"], "json")

    def test_thinking_trace_is_never_promoted_to_semantic_output(self):
        client = InspectableOllamaClient({
            "response": "",
            "thinking": "internal trace that must not become memory",
            "done": True,
            "done_reason": "stop",
        })
        with self.assertRaisesRegex(SemanticLabError, "thinking_chars"):
            client.generate_text("qwen3:4b", "prompt", system="test")
        self.assertIs(client.last_payload["think"], False)


if __name__ == "__main__":
    unittest.main()
