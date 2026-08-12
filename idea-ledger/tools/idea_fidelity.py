"""Deterministic fidelity contract for the personal Idea Ledger.

This module does not pretend to solve semantic entailment by itself.
A semantic judge (local NLI/LLM or another verifier) can supply atom-level
relations. This gate decides whether those results authorize a reconstruction.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ALLOWED_PROVENANCE = {
    "observed", "measured", "reported", "memory", "inferred", "hypothesis"
}
ALLOWED_AFFECT_MODES = {"reported", "inferred", "unknown"}
ALLOWED_RELATIONS = {"entailed", "contradicted", "unknown"}
ALLOWED_STATUS = {"captured", "reviewed", "promoted", "archived"}


class IdeaLedgerError(ValueError):
    pass


@dataclass(frozen=True)
class FidelityResult:
    passed: bool
    protected_atom_recall: float
    contradicted_atoms: tuple[str, ...]
    unknown_protected_atoms: tuple[str, ...]
    missing_judgements: tuple[str, ...]
    novel_material_claims: tuple[str, ...]
    lexical_anchor_failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "protected_atom_recall": self.protected_atom_recall,
            "contradicted_atoms": list(self.contradicted_atoms),
            "unknown_protected_atoms": list(self.unknown_protected_atoms),
            "missing_judgements": list(self.missing_judgements),
            "novel_material_claims": list(self.novel_material_claims),
            "lexical_anchor_failures": list(self.lexical_anchor_failures),
        }


def _require(mapping: Mapping[str, Any], key: str, expected: type) -> Any:
    if key not in mapping:
        raise IdeaLedgerError(f"missing required field: {key}")
    value = mapping[key]
    if not isinstance(value, expected):
        raise IdeaLedgerError(f"{key} must be {expected.__name__}")
    return value


def validate_seed(seed: Mapping[str, Any]) -> None:
    if seed.get("schema_version") != "0.1.0":
        raise IdeaLedgerError("unsupported schema_version")
    if seed.get("status") not in ALLOWED_STATUS:
        raise IdeaLedgerError("invalid status")
    _require(seed, "id", str)
    _require(seed, "title", str)
    _require(seed, "created_at", str)

    source = _require(seed, "source", dict)
    if source.get("preserved") is not True:
        raise IdeaLedgerError("source.preserved must be true")
    if source.get("visibility") not in {"private", "public"}:
        raise IdeaLedgerError("invalid source.visibility")
    _require(source, "raw_anchor", str)

    core = _require(seed, "core", dict)
    _require(core, "meaning", str)
    _require(core, "intent", str)
    _require(core, "tensions", list)

    atoms = _require(seed, "semantic_atoms", list)
    if not atoms:
        raise IdeaLedgerError("semantic_atoms must not be empty")
    ids: set[str] = set()
    for atom in atoms:
        if not isinstance(atom, dict):
            raise IdeaLedgerError("semantic atom must be an object")
        atom_id = _require(atom, "id", str)
        if atom_id in ids:
            raise IdeaLedgerError(f"duplicate atom id: {atom_id}")
        ids.add(atom_id)
        _require(atom, "statement", str)
        _require(atom, "protected", bool)
        if atom.get("provenance") not in ALLOWED_PROVENANCE:
            raise IdeaLedgerError(f"invalid provenance for {atom_id}")
        confidence = atom.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise IdeaLedgerError(f"invalid confidence for {atom_id}")
        if not 0 <= float(confidence) <= 1:
            raise IdeaLedgerError(f"confidence out of range for {atom_id}")
        tokens = atom.get("verbatim_tokens", [])
        if not isinstance(tokens, list) or not all(isinstance(x, str) for x in tokens):
            raise IdeaLedgerError(f"invalid verbatim_tokens for {atom_id}")

    affect = _require(seed, "affect", dict)
    if affect.get("mode") not in ALLOWED_AFFECT_MODES:
        raise IdeaLedgerError("invalid affect.mode")
    _require(affect, "qualities", list)
    aconf = affect.get("confidence")
    if isinstance(aconf, bool) or not isinstance(aconf, (int, float)) or not 0 <= float(aconf) <= 1:
        raise IdeaLedgerError("invalid affect.confidence")

    genealogy = _require(seed, "genealogy", dict)
    for key in ("parents", "children", "derived_from"):
        values = _require(genealogy, key, list)
        if not all(isinstance(x, str) for x in values):
            raise IdeaLedgerError(f"genealogy.{key} must contain strings")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def lexical_anchor_failures(seed: Mapping[str, Any], reconstruction: str | None) -> tuple[str, ...]:
    if reconstruction is None:
        return ()
    normalized = _normalize(reconstruction)
    failures = []
    for atom in seed["semantic_atoms"]:
        if not atom.get("protected"):
            continue
        for token in atom.get("verbatim_tokens", []):
            if _normalize(token) not in normalized:
                failures.append(f"{atom['id']}:{token}")
    return tuple(failures)


def evaluate_fidelity(
    seed: Mapping[str, Any],
    judgement: Mapping[str, Any],
    *,
    reconstruction: str | None = None,
) -> FidelityResult:
    validate_seed(seed)
    if judgement.get("idea_id") != seed.get("id"):
        raise IdeaLedgerError("judgement.idea_id does not match seed.id")

    rows = judgement.get("atom_results")
    if not isinstance(rows, list):
        raise IdeaLedgerError("judgement.atom_results must be a list")

    by_id: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise IdeaLedgerError("atom result must be an object")
        atom_id = row.get("atom_id")
        relation = row.get("relation")
        if not isinstance(atom_id, str) or relation not in ALLOWED_RELATIONS:
            raise IdeaLedgerError("invalid atom result")
        by_id[atom_id] = relation

    protected = [a for a in seed["semantic_atoms"] if a.get("protected")]
    protected_ids = {a["id"] for a in protected}
    contradicted = tuple(sorted(k for k, v in by_id.items() if k in protected_ids and v == "contradicted"))
    unknown = tuple(sorted(k for k, v in by_id.items() if k in protected_ids and v == "unknown"))
    missing = tuple(sorted(k for k in protected_ids if k not in by_id))
    entailed_count = sum(1 for k in protected_ids if by_id.get(k) == "entailed")
    recall = 1.0 if not protected_ids else entailed_count / len(protected_ids)

    novel = judgement.get("novel_material_claims", [])
    if not isinstance(novel, list) or not all(isinstance(x, str) for x in novel):
        raise IdeaLedgerError("novel_material_claims must be a list of strings")
    lexical = lexical_anchor_failures(seed, reconstruction)

    passed = (
        recall == 1.0
        and not contradicted
        and not unknown
        and not missing
        and not novel
        and not lexical
    )
    return FidelityResult(
        passed=passed,
        protected_atom_recall=recall,
        contradicted_atoms=contradicted,
        unknown_protected_atoms=unknown,
        missing_judgements=missing,
        novel_material_claims=tuple(novel),
        lexical_anchor_failures=lexical,
    )


def evaluate_round_trip(
    seed: Mapping[str, Any],
    rounds: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate every round against the original seed, never against the previous round."""
    validate_seed(seed)
    results = [evaluate_fidelity(seed, item).to_dict() for item in rounds]
    first_failure = next((i + 1 for i, item in enumerate(results) if not item["passed"]), None)
    return {
        "passed": first_failure is None,
        "rounds": len(results),
        "first_failure_round": first_failure,
        "results": results,
        "comparison_anchor": "original_seed",
    }


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise IdeaLedgerError("JSON root must be an object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Idea Ledger fidelity gate")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("seed")

    p_check = sub.add_parser("check")
    p_check.add_argument("seed")
    p_check.add_argument("judgement")
    p_check.add_argument("--reconstruction")

    args = parser.parse_args()

    if args.command == "validate":
        validate_seed(load_json(args.seed))
        print(json.dumps({"status": "valid"}, ensure_ascii=False))
        return 0

    seed = load_json(args.seed)
    judgement = load_json(args.judgement)
    reconstruction = None
    if args.reconstruction:
        reconstruction = Path(args.reconstruction).read_text(encoding="utf-8")
    result = evaluate_fidelity(seed, judgement, reconstruction=reconstruction)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
