"""Ollama transport hardening for Idea Ledger Semantic Lab.

Reasoning-capable Ollama models such as Qwen 3 may enable thinking by default.
The Idea Ledger contract needs the final answer in `response` and must never
promote a reasoning trace from `thinking` into semantic memory.
"""
from __future__ import annotations

import json
import os

from idea_fidelity import IdeaLedgerError
from semantic_lab import OllamaClient, SemanticLabError, main


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
        payload["format"] = "json"

    data = self._request("POST", "/api/generate", payload)
    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        thinking = data.get("thinking")
        thinking_chars = len(thinking) if isinstance(thinking, str) else 0
        done_reason = data.get("done_reason")
        raise SemanticLabError(
            "Ollama nao retornou texto final em response "
            f"(done_reason={done_reason!r}, thinking_chars={thinking_chars}). "
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
