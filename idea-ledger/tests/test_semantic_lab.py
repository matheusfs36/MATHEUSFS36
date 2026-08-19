from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "idea-ledger" / "tools"))

from semantic_lab import (  # noqa: E402
    compression_metrics,
    extract_anchor,
    run_experiment,
    runtime_payload,
)


ANCHOR_PAYLOAD = {
    "title": "Memória compacta",
    "core": {
        "meaning": "Compactar sem mudar sentido.",
        "intent": "Guardar ideias economicamente.",
        "tensions": ["compressão vs fidelidade"],
    },
    "semantic_atoms": [
        {
            "statement": "A fonte original deve permanecer preservada.",
            "kind": "constraint",
            "protected": True,
            "provenance": "reported",
            "confidence": 1.0,
            "verbatim_tokens": ["fonte original"],
        },
        {
            "statement": "A emoção foi inferida, não relatada.",
            "kind": "affect",
            "protected": True,
            "provenance": "inferred",
            "confidence": 0.8,
            "verbatim_tokens": [],
        },
        {
            "statement": "A linguagem pode variar.",
            "kind": "intent",
            "protected": False,
            "provenance": "reported",
            "confidence": 1.0,
            "verbatim_tokens": [],
        },
    ],
    "affect": {
        "mode": "inferred",
        "qualities": ["curiosidade"],
        "valence": 0.4,
        "arousal": 0.5,
        "confidence": 0.8,
    },
}

COMPRESSED_PAYLOAD = {
    "core": {
        "meaning": "Compactar sem distorcer.",
        "intent": "Memória econômica.",
        "tensions": ["compressão/fidelidade"],
    },
    "unprotected_atoms": [
        {
            "statement": "A linguagem pode variar.",
            "kind": "intent",
            "provenance": "reported",
            "confidence": 1.0,
        }
    ],
}

PASS_JUDGEMENT = {
    "idea_id": "ignored-by-wrapper",
    "atom_results": [
        {"atom_id": "a1", "relation": "entailed"},
        {"atom_id": "a2", "relation": "entailed"},
        {"atom_id": "a3", "relation": "entailed"},
    ],
    "novel_material_claims": [],
}


class ScriptedClient:
    def __init__(self, *, novel=False):
        self.calls = []
        self.novel = novel

    def list_models(self):
        return ["qwen3:4b", "qwen3:8b"]

    def generate_json(self, model, prompt, *, system="", temperature=0.0):
        self.calls.append(("json", model, system, prompt))
        if "ANCHOR_EXTRACTOR" in system:
            return ANCHOR_PAYLOAD
        if "SEED_COMPRESSOR" in system:
            return COMPRESSED_PAYLOAD
        if "FIDELITY_JUDGE" in system:
            result = dict(PASS_JUDGEMENT)
            if self.novel:
                result["novel_material_claims"] = ["A ideia exige nuvem."]
            return result
        raise AssertionError(system)

    def generate_text(self, model, prompt, *, system="", temperature=0.1):
        self.calls.append(("text", model, system, prompt))
        if "DECODER" not in system:
            raise AssertionError(system)
        return "A fonte original permanece preservada. A emoção é inferida, não relatada. A linguagem pode variar."


class SemanticLabTests(unittest.TestCase):
    def test_anchor_preserves_affect_provenance(self):
        client = ScriptedClient()
        anchor = extract_anchor(
            client,
            "qwen3:4b",
            "texto",
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            created_at="2026-08-12T00:00:00+00:00",
        )
        self.assertEqual(anchor["affect"]["mode"], "inferred")
        self.assertEqual(anchor["semantic_atoms"][1]["provenance"], "inferred")

    def test_decoder_never_receives_raw_source(self):
        client = ScriptedClient()
        result = run_experiment(
            client,
            original_text="RAW_SECRET_DO_NOT_LEAK: texto completo da fonte.",
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:8b",
            budgets=[1800],
            roundtrips=1,
            created_at="2026-08-12T00:00:00+00:00",
        )
        decoder_prompts = [call[3] for call in client.calls if call[0] == "text"]
        self.assertEqual(len(decoder_prompts), 1)
        self.assertNotIn("RAW_SECRET_DO_NOT_LEAK", decoder_prompts[0])
        self.assertFalse(result["source"]["content_persisted_in_report"])

    def test_judge_always_receives_original_source(self):
        client = ScriptedClient()
        run_experiment(
            client,
            original_text="ORIGINAL_ANCHOR_987",
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:8b",
            budgets=[1800],
            roundtrips=3,
            created_at="2026-08-12T00:00:00+00:00",
        )
        judge_prompts = [call[3] for call in client.calls if "FIDELITY_JUDGE" in call[2]]
        self.assertEqual(len(judge_prompts), 3)
        self.assertTrue(all("ORIGINAL_ANCHOR_987" in prompt for prompt in judge_prompts))

    def test_novel_material_claim_fails_semantic_gate(self):
        client = ScriptedClient(novel=True)
        result = run_experiment(
            client,
            original_text="Uma fonte original deve ser preservada.",
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:8b",
            budgets=[1800],
            roundtrips=1,
            created_at="2026-08-12T00:00:00+00:00",
        )
        self.assertFalse(result["sweeps"][0]["semantic_fidelity_passed"])
        self.assertIsNone(result["safe_floor_target_chars"])

    def test_budget_is_measured_not_silently_truncated(self):
        client = ScriptedClient()
        result = run_experiment(
            client,
            original_text="A" * 2000,
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:8b",
            budgets=[50],
            roundtrips=1,
            created_at="2026-08-12T00:00:00+00:00",
        )
        metrics = result["sweeps"][0]["rounds"][0]["metrics"]
        self.assertGreater(metrics["payload_chars"], 50)
        self.assertFalse(metrics["within_target"])
        self.assertFalse(result["sweeps"][0]["passed"])

    def test_independent_judge_flag(self):
        independent = run_experiment(
            ScriptedClient(),
            original_text="fonte original",
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:8b",
            budgets=[1800],
            roundtrips=1,
            created_at="2026-08-12T00:00:00+00:00",
        )
        self.assertTrue(independent["models"]["judge_independent_from_generation"])

        same = run_experiment(
            ScriptedClient(),
            original_text="fonte original",
            idea_id="idea-y",
            raw_anchor="raw/y.md",
            source_ref="y.md",
            visibility="private",
            compressor_model="qwen3:4b",
            decoder_model="qwen3:4b",
            judge_model="qwen3:4b",
            budgets=[1800],
            roundtrips=1,
            created_at="2026-08-12T00:00:00+00:00",
        )
        self.assertFalse(same["models"]["judge_independent_from_generation"])

    def test_runtime_payload_excludes_storage_source_metadata(self):
        client = ScriptedClient()
        anchor = extract_anchor(
            client,
            "qwen3:4b",
            "texto",
            idea_id="idea-x",
            raw_anchor="C:/private/source.md",
            source_ref="source.md",
            visibility="private",
            created_at="2026-08-12T00:00:00+00:00",
        )
        payload = runtime_payload(anchor)
        self.assertNotIn("source", payload)
        self.assertNotIn("C:/private/source.md", str(payload))

    def test_compression_metrics_are_explicit(self):
        client = ScriptedClient()
        anchor = extract_anchor(
            client,
            "qwen3:4b",
            "texto longo " * 100,
            idea_id="idea-x",
            raw_anchor="raw/x.md",
            source_ref="x.md",
            visibility="private",
            created_at="2026-08-12T00:00:00+00:00",
        )
        metrics = compression_metrics("texto longo " * 100, anchor, 1800)
        self.assertGreater(metrics.raw_chars, 0)
        self.assertGreater(metrics.payload_chars, 0)
        self.assertGreater(metrics.byte_ratio, 0)


if __name__ == "__main__":
    unittest.main()
