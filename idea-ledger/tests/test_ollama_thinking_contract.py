from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "idea-ledger" / "tools"))

import semantic_lab_ollama  # noqa: E402,F401  patches OllamaClient transport
from semantic_lab import OllamaClient, SemanticLabError  # noqa: E402


class InspectableOllamaClient(OllamaClient):
    def __init__(self, response):
        super().__init__("http://example.invalid")
        self.response_data = response
        self.last_payload = None
        self.timeout_seen = None

    def _request(self, method, path, payload=None):
        self.last_payload = payload
        self.timeout_seen = self.timeout
        return dict(self.response_data)


class TimeoutOllamaClient(OllamaClient):
    def __init__(self):
        super().__init__("http://example.invalid")
        self.last_payload = None

    def _request(self, method, path, payload=None):
        self.last_payload = payload
        raise TimeoutError("timed out")


class OllamaThinkingContractTests(unittest.TestCase):
    def test_thinking_is_disabled_for_structured_contract_output(self):
        client = InspectableOllamaClient({"response": "{\"ok\": true}", "done": True})
        with redirect_stdout(io.StringIO()):
            result = client.generate_json("qwen3:4b", "prompt", system="test")
        self.assertEqual(result, {"ok": True})
        self.assertIs(client.last_payload["think"], False)
        self.assertIs(client.last_payload["stream"], False)
        self.assertEqual(client.last_payload["format"], "json")

    def test_anchor_stage_uses_json_schema_and_boolean_protected(self):
        client = InspectableOllamaClient({"response": "{}", "done": True})
        with redirect_stdout(io.StringIO()):
            client.generate_json(
                "qwen3:4b",
                "prompt",
                system="IDEA_LEDGER_ANCHOR_EXTRACTOR_V1",
            )
        schema = client.last_payload["format"]
        self.assertIsInstance(schema, dict)
        atom_schema = schema["properties"]["semantic_atoms"]["items"]
        self.assertEqual(atom_schema["properties"]["protected"]["type"], "boolean")
        self.assertIn("protected", atom_schema["required"])
        self.assertFalse(atom_schema["additionalProperties"])

    def test_compressor_and_judge_stages_use_specific_schemas(self):
        client = InspectableOllamaClient({"response": "{}", "done": True})
        with redirect_stdout(io.StringIO()):
            client.generate_json(
                "qwen3:4b", "prompt", system="IDEA_LEDGER_SEED_COMPRESSOR_V1"
            )
        compressor_schema = client.last_payload["format"]
        self.assertIn("unprotected_atoms", compressor_schema["required"])

        with redirect_stdout(io.StringIO()):
            client.generate_json(
                "qwen3:8b", "prompt", system="IDEA_LEDGER_FIDELITY_JUDGE_V1"
            )
        judge_schema = client.last_payload["format"]
        relation = judge_schema["properties"]["atom_results"]["items"]["properties"]["relation"]
        self.assertEqual(set(relation["enum"]), {"entailed", "contradicted", "unknown"})

    def test_thinking_trace_is_never_promoted_to_semantic_output(self):
        client = InspectableOllamaClient({
            "response": "",
            "thinking": "internal trace that must not become memory",
            "done": True,
            "done_reason": "stop",
        })
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SemanticLabError, "thinking_chars"):
                client.generate_text("qwen3:4b", "prompt", system="test")
        self.assertIs(client.last_payload["think"], False)

    def test_generation_uses_configurable_timeout(self):
        client = InspectableOllamaClient({"response": "ok", "done": True})
        with patch.dict(os.environ, {"IDEA_LEDGER_OLLAMA_TIMEOUT_SECONDS": "321"}):
            with redirect_stdout(io.StringIO()):
                result = client.generate_text("qwen3:4b", "prompt", system="test")
        self.assertEqual(result, "ok")
        self.assertEqual(client.timeout_seen, 321.0)

    def test_timeout_error_names_budget_round_stage_and_model(self):
        client = TimeoutOllamaClient()
        prompt = "OBJETIVO: teste\nALVO: payload final <= 1800 caracteres quando possível."
        with patch.dict(os.environ, {"IDEA_LEDGER_OLLAMA_TIMEOUT_SECONDS": "600"}):
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SemanticLabError) as ctx:
                    client.generate_json(
                        "qwen3:4b",
                        prompt,
                        system="IDEA_LEDGER_SEED_COMPRESSOR_V1",
                    )
        message = str(ctx.exception)
        self.assertIn("stage=BUDGET 1800 | ROUND 1 | COMPRESS", message)
        self.assertIn("model=qwen3:4b", message)
        self.assertIn("limit=600s", message)


if __name__ == "__main__":
    unittest.main()
