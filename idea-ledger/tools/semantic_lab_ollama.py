"""Ollama transport hardening for Idea Ledger Semantic Lab.

Reasoning-capable Ollama models such as Qwen 3 may enable thinking by default.
The Idea Ledger contract needs the final answer in `response` and must never
promote a reasoning trace from `thinking` into semantic memory.

For structured semantic stages, this adapter also sends a JSON Schema through
Ollama's `format` field. Types such as `protected` are therefore constrained at
the model boundary instead of being guessed or silently coerced afterwards.

This adapter also adds bounded observability for slow local inference:
- configurable per-request timeout (default 600 seconds);
- stage/model/budget/round progress lines;
- elapsed time for every generation request;
- fail-closed timeout errors naming the exact semantic stage.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time

from idea_fidelity import IdeaLedgerError
from semantic_lab import OllamaClient, SemanticLabError, main

KINDS = [
    "fact", "intent", "constraint", "uncertainty", "negation",
    "causality", "authorship", "affect", "relation",
]
PROVENANCE = ["observed", "measured", "reported", "memory", "inferred", "hypothesis"]

CORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "meaning": {"type": "string"},
        "intent": {"type": "string"},
        "tensions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["meaning", "intent", "tensions"],
}

ANCHOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "core": CORE_SCHEMA,
        "semantic_atoms": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "statement": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "protected": {"type": "boolean"},
                    "provenance": {"type": "string", "enum": PROVENANCE},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "verbatim_tokens": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "statement", "kind", "protected", "provenance",
                    "confidence", "verbatim_tokens",
                ],
            },
        },
        "affect": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["reported", "inferred", "unknown"]},
                "qualities": {"type": "array", "items": {"type": "string"}},
                "valence": {"type": ["number", "null"], "minimum": -1, "maximum": 1},
                "arousal": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["mode", "qualities", "valence", "arousal", "confidence"],
        },
    },
    "required": ["title", "core", "semantic_atoms", "affect"],
}

COMPRESSOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "core": CORE_SCHEMA,
        "unprotected_atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "statement": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "provenance": {"type": "string", "enum": PROVENANCE},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["statement", "kind", "provenance", "confidence"],
            },
        },
    },
    "required": ["core", "unprotected_atoms"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "idea_id": {"type": "string"},
        "atom_results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "atom_id": {"type": "string"},
                    "relation": {
                        "type": "string",
                        "enum": ["entailed", "contradicted", "unknown"],
                    },
                },
                "required": ["atom_id", "relation"],
            },
        },
        "novel_material_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["idea_id", "atom_results", "novel_material_claims"],
}


def _schema_for_system(system: str):
    if "ANCHOR_EXTRACTOR" in system:
        return ANCHOR_SCHEMA
    if "SEED_COMPRESSOR" in system:
        return COMPRESSOR_SCHEMA
    if "FIDELITY_JUDGE" in system:
        return JUDGE_SCHEMA
    return None


def _stage_name(system: str) -> str:
    if "ANCHOR_EXTRACTOR" in system:
        return "ANCHOR"
    if "SEED_COMPRESSOR" in system:
        return "COMPRESS"
    if "FIDELITY_JUDGE" in system:
        return "JUDGE"
    if "DECODER" in system:
        return "DECODE"
    return "GENERATE"


def _configured_timeout() -> float:
    raw = os.getenv("IDEA_LEDGER_OLLAMA_TIMEOUT_SECONDS", "600").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SemanticLabError(
            "IDEA_LEDGER_OLLAMA_TIMEOUT_SECONDS deve ser numero positivo"
        ) from exc
    if value <= 0:
        raise SemanticLabError(
            "IDEA_LEDGER_OLLAMA_TIMEOUT_SECONDS deve ser numero positivo"
        )
    return value


def _progress_context(self: OllamaClient, system: str, prompt: str) -> str:
    stage = _stage_name(system)
    if stage == "ANCHOR":
        return "ANCHOR"
    if stage == "COMPRESS":
        match = re.search(r"ALVO:\s*payload final <=\s*(\d+)\s*caracteres", prompt)
        budget = int(match.group(1)) if match else None
        previous = getattr(self, "_idea_ledger_budget", None)
        if budget != previous:
            self._idea_ledger_budget = budget
            self._idea_ledger_round = 1
        else:
            self._idea_ledger_round = int(getattr(self, "_idea_ledger_round", 0)) + 1
    budget = getattr(self, "_idea_ledger_budget", None)
    round_index = getattr(self, "_idea_ledger_round", None)
    if budget is not None and round_index is not None:
        return f"BUDGET {budget} | ROUND {round_index} | {stage}"
    return stage


def _contract_generate(
    self: OllamaClient,
    model: str,
    prompt: str,
    *,
    system: str,
    temperature: float,
    json_mode: bool,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
    }
    if json_mode:
        payload["format"] = _schema_for_system(system) or "json"

    timeout = _configured_timeout()
    self.timeout = timeout
    context = _progress_context(self, system, prompt)
    started = time.monotonic()
    print(
        f"[{context}] START | model={model} | timeout={int(timeout)}s",
        flush=True,
    )
    try:
        data = self._request("POST", "/api/generate", payload)
    except (TimeoutError, socket.timeout) as exc:
        elapsed = time.monotonic() - started
        raise SemanticLabError(
            f"Timeout no Ollama | stage={context} | model={model} | "
            f"elapsed={elapsed:.1f}s | limit={timeout:.0f}s"
        ) from exc
    elapsed = time.monotonic() - started
    print(
        f"[{context}] DONE  | model={model} | elapsed={elapsed:.1f}s",
        flush=True,
    )

    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        thinking = data.get("thinking")
        thinking_chars = len(thinking) if isinstance(thinking, str) else 0
        done_reason = data.get("done_reason")
        raise SemanticLabError(
            "Ollama nao retornou texto final em response "
            f"(stage={context}, model={model}, done_reason={done_reason!r}, "
            f"thinking_chars={thinking_chars}). "
            "O campo thinking nunca e usado como saida semantica."
        )
    return response.strip()


# Patch only the transport boundary. All semantic policies remain in semantic_lab.py.
OllamaClient._generate = _contract_generate


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SemanticLabError, IdeaLedgerError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
        raise SystemExit(2)
