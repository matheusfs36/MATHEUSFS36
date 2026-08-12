from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "idea-ledger" / "tools"))

from idea_fidelity import (  # noqa: E402
    IdeaLedgerError,
    evaluate_fidelity,
    evaluate_round_trip,
    lexical_anchor_failures,
    validate_seed,
)


def seed():
    return {
        "schema_version": "0.1.0",
        "id": "idea-test-001",
        "title": "Teste",
        "created_at": "2026-08-12T13:09:00-03:00",
        "status": "captured",
        "source": {
            "kind": "note",
            "ref": "test",
            "raw_anchor": "raw/test.md",
            "visibility": "private",
            "preserved": True,
        },
        "core": {
            "meaning": "Preservar sentido com compactação.",
            "intent": "Testar fidelidade.",
            "tensions": ["compression_vs_fidelity"],
        },
        "semantic_atoms": [
            {
                "id": "a1",
                "statement": "A fonte original é preservada.",
                "kind": "constraint",
                "protected": True,
                "provenance": "reported",
                "confidence": 1.0,
                "verbatim_tokens": ["fonte original"],
            },
            {
                "id": "a2",
                "statement": "A emoção foi inferida, não relatada.",
                "kind": "affect",
                "protected": True,
                "provenance": "inferred",
                "confidence": 0.7,
                "verbatim_tokens": [],
            },
        ],
        "affect": {
            "mode": "inferred",
            "qualities": ["curiosidade"],
            "valence": 0.4,
            "arousal": 0.5,
            "confidence": 0.7,
        },
        "genealogy": {"parents": [], "children": [], "derived_from": []},
    }


def judgement(a1="entailed", a2="entailed", novel=None):
    return {
        "idea_id": "idea-test-001",
        "atom_results": [
            {"atom_id": "a1", "relation": a1},
            {"atom_id": "a2", "relation": a2},
        ],
        "novel_material_claims": list(novel or []),
    }


class IdeaFidelityTests(unittest.TestCase):
    def test_valid_seed(self):
        validate_seed(seed())

    def test_source_must_remain_preserved(self):
        s = seed()
        s["source"]["preserved"] = False
        with self.assertRaises(IdeaLedgerError):
            validate_seed(s)

    def test_affect_provenance_must_be_valid(self):
        s = seed()
        s["affect"]["mode"] = "felt_by_machine"
        with self.assertRaises(IdeaLedgerError):
            validate_seed(s)

    def test_all_protected_atoms_entailed_passes(self):
        result = evaluate_fidelity(
            seed(),
            judgement(),
            reconstruction="A fonte original continua presente.",
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.protected_atom_recall, 1.0)

    def test_missing_protected_atom_fails(self):
        j = judgement()
        j["atom_results"] = j["atom_results"][:1]
        result = evaluate_fidelity(seed(), j, reconstruction="fonte original")
        self.assertFalse(result.passed)
        self.assertEqual(result.missing_judgements, ("a2",))

    def test_unknown_protected_atom_fails(self):
        result = evaluate_fidelity(seed(), judgement(a2="unknown"), reconstruction="fonte original")
        self.assertFalse(result.passed)
        self.assertEqual(result.unknown_protected_atoms, ("a2",))

    def test_contradiction_fails(self):
        result = evaluate_fidelity(seed(), judgement(a1="contradicted"), reconstruction="fonte original")
        self.assertFalse(result.passed)
        self.assertEqual(result.contradicted_atoms, ("a1",))

    def test_material_novel_claim_fails(self):
        result = evaluate_fidelity(
            seed(),
            judgement(novel=["A ideia exige uso de nuvem."]),
            reconstruction="fonte original",
        )
        self.assertFalse(result.passed)
        self.assertEqual(len(result.novel_material_claims), 1)

    def test_lexical_anchor_failure_catches_protected_data(self):
        failures = lexical_anchor_failures(seed(), "A fonte foi guardada.")
        self.assertEqual(failures, ("a1:fonte original",))

    def test_round_trip_compares_every_round_to_original(self):
        rounds = [
            judgement(),
            judgement(),
            judgement(a2="unknown"),
        ]
        result = evaluate_round_trip(seed(), rounds)
        self.assertFalse(result["passed"])
        self.assertEqual(result["first_failure_round"], 3)
        self.assertEqual(result["comparison_anchor"], "original_seed")

    def test_duplicate_atom_ids_fail(self):
        s = seed()
        s["semantic_atoms"].append(copy.deepcopy(s["semantic_atoms"][0]))
        with self.assertRaises(IdeaLedgerError):
            validate_seed(s)


if __name__ == "__main__":
    unittest.main()
