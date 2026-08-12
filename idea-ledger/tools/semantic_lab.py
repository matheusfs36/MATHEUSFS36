"""Local semantic compression laboratory for the Idea Ledger.

Pipeline:
    raw source -> canonical anchor -> compressed runtime seed -> reconstruction
               -> independent semantic judgement -> deterministic fidelity gate

The decoder never receives the raw source. The judge always compares every
round against the original source + original canonical anchor, preventing
telephone-game drift from becoming the new truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from idea_fidelity import IdeaLedgerError, evaluate_fidelity, validate_seed

ALLOWED_KINDS = {
    "fact", "intent", "constraint", "uncertainty", "negation",
    "causality", "authorship", "affect", "relation",
}
ALLOWED_PROVENANCE = {
    "observed", "measured", "reported", "memory", "inferred", "hypothesis"
}
ALLOWED_AFFECT_MODES = {"reported", "inferred", "unknown"}


class SemanticLabError(RuntimeError):
    pass


class ModelClient(Protocol):
    def list_models(self) -> list[str]: ...
    def generate_json(
        self, model: str, prompt: str, *, system: str = "", temperature: float = 0.0
    ) -> dict[str, Any]: ...
    def generate_text(
        self, model: str, prompt: str, *, system: str = "", temperature: float = 0.1
    ) -> str: ...


@dataclass(frozen=True)
class CompressionMetrics:
    raw_chars: int
    raw_bytes: int
    payload_chars: int
    payload_bytes: int
    char_ratio: float
    byte_ratio: float
    target_chars: int
    within_target: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class OllamaClient:
    """Minimal dependency-free client for a local Ollama-compatible endpoint."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise SemanticLabError(f"Ollama indisponível em {self.base_url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SemanticLabError("Ollama retornou JSON HTTP inválido") from exc
        if not isinstance(data, dict):
            raise SemanticLabError("Resposta HTTP do Ollama não é um objeto JSON")
        return data

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        models = data.get("models", [])
        if not isinstance(models, list):
            return []
        return sorted(
            str(item.get("name"))
            for item in models
            if isinstance(item, dict) and item.get("name")
        )

    def _generate(self, model: str, prompt: str, *, system: str, temperature: float, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        data = self._request("POST", "/api/generate", payload)
        response = data.get("response")
        if not isinstance(response, str) or not response.strip():
            raise SemanticLabError("Ollama não retornou texto em response")
        return response.strip()

    def generate_json(
        self, model: str, prompt: str, *, system: str = "", temperature: float = 0.0
    ) -> dict[str, Any]:
        raw = self._generate(model, prompt, system=system, temperature=temperature, json_mode=True)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SemanticLabError(f"Modelo {model} não devolveu JSON válido") from exc
        if not isinstance(data, dict):
            raise SemanticLabError(f"Modelo {model} devolveu JSON que não é objeto")
        return data

    def generate_text(
        self, model: str, prompt: str, *, system: str = "", temperature: float = 0.1
    ) -> str:
        return self._generate(model, prompt, system=system, temperature=temperature, json_mode=False)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticLabError(f"campo ausente ou inválido: {field}")
    return value.strip()


def _require_float01(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticLabError(f"campo deve ser número 0..1: {field}")
    value = float(value)
    if not 0 <= value <= 1:
        raise SemanticLabError(f"campo fora de 0..1: {field}")
    return value


def _text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SemanticLabError(f"campo deve ser lista de strings: {field}")
    return [item.strip() for item in value if item.strip()]


def _canonical_atoms(rows: Any, *, prefix: str = "a") -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise SemanticLabError("semantic_atoms precisa conter ao menos um átomo")
    atoms: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SemanticLabError("semantic atom deve ser objeto")
        kind = row.get("kind")
        provenance = row.get("provenance")
        if kind not in ALLOWED_KINDS:
            raise SemanticLabError(f"kind inválido no átomo {index}: {kind}")
        if provenance not in ALLOWED_PROVENANCE:
            raise SemanticLabError(f"provenance inválida no átomo {index}: {provenance}")
        protected = row.get("protected")
        if not isinstance(protected, bool):
            raise SemanticLabError(f"protected inválido no átomo {index}")
        atoms.append({
            "id": f"{prefix}{index}",
            "statement": _require_text(row.get("statement"), f"semantic_atoms[{index}].statement"),
            "kind": kind,
            "protected": protected,
            "provenance": provenance,
            "confidence": _require_float01(row.get("confidence"), f"semantic_atoms[{index}].confidence"),
            "verbatim_tokens": _text_list(row.get("verbatim_tokens", []), f"semantic_atoms[{index}].verbatim_tokens"),
        })
    return atoms


def _canonical_affect(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemanticLabError("affect deve ser objeto")
    mode = value.get("mode")
    if mode not in ALLOWED_AFFECT_MODES:
        raise SemanticLabError(f"affect.mode inválido: {mode}")
    valence = value.get("valence")
    arousal = value.get("arousal")
    if valence is not None:
        if isinstance(valence, bool) or not isinstance(valence, (int, float)) or not -1 <= float(valence) <= 1:
            raise SemanticLabError("affect.valence deve estar entre -1 e 1 ou null")
        valence = float(valence)
    if arousal is not None:
        arousal = _require_float01(arousal, "affect.arousal")
    return {
        "mode": mode,
        "qualities": _text_list(value.get("qualities", []), "affect.qualities"),
        "valence": valence,
        "arousal": arousal,
        "confidence": _require_float01(value.get("confidence"), "affect.confidence"),
    }


def extract_anchor(
    client: ModelClient,
    model: str,
    raw_text: str,
    *,
    idea_id: str,
    raw_anchor: str,
    source_ref: str,
    visibility: str,
    created_at: str,
) -> dict[str, Any]:
    if visibility not in {"private", "public"}:
        raise SemanticLabError("visibility deve ser private ou public")
    prompt = f"""Extraia uma âncora semântica canônica da fonte abaixo.

REGRAS INVIOLÁVEIS:
1. Não invente intenção, emoção, causalidade, autoria, certeza, nomes, datas ou números.
2. Frases explicitamente ditas/registradas usam provenance=reported.
3. Emoção não explicitamente declarada nunca pode virar reported; use inferred ou unknown.
4. Proteja especialmente: negações, autoria, intenção, restrições, incerteza, causalidade, nomes, datas e números.
5. Cada statement deve ser curto, autônomo e fiel, preferencialmente <= 160 caracteres.
6. verbatim_tokens só para literais cuja alteração mudaria o compromisso semântico.
7. Não confunda possibilidade com decisão, hipótese com fato, desejo com obrigação.

Retorne APENAS JSON neste formato:
{{
  "title": "...",
  "core": {{"meaning":"...","intent":"...","tensions":["..."]}},
  "semantic_atoms": [
    {{"statement":"...","kind":"fact|intent|constraint|uncertainty|negation|causality|authorship|affect|relation","protected":true,"provenance":"reported|inferred|hypothesis|memory|observed|measured","confidence":0.0,"verbatim_tokens":[]}}
  ],
  "affect": {{"mode":"reported|inferred|unknown","qualities":[],"valence":null,"arousal":null,"confidence":0.0}}
}}

FONTE ORIGINAL:
<<<RAW_SOURCE>>>
{raw_text}
<<<END_RAW_SOURCE>>>"""
    payload = client.generate_json(model, prompt, system="IDEA_LEDGER_ANCHOR_EXTRACTOR_V1", temperature=0.0)
    core = payload.get("core")
    if not isinstance(core, dict):
        raise SemanticLabError("anchor.core ausente")
    seed = {
        "schema_version": "0.1.0",
        "id": idea_id,
        "title": _require_text(payload.get("title"), "title"),
        "created_at": created_at,
        "status": "captured",
        "source": {
            "kind": "note",
            "ref": source_ref,
            "raw_anchor": raw_anchor,
            "visibility": visibility,
            "preserved": True,
        },
        "core": {
            "meaning": _require_text(core.get("meaning"), "core.meaning"),
            "intent": _require_text(core.get("intent"), "core.intent"),
            "tensions": _text_list(core.get("tensions", []), "core.tensions"),
        },
        "semantic_atoms": _canonical_atoms(payload.get("semantic_atoms")),
        "affect": _canonical_affect(payload.get("affect")),
        "genealogy": {"parents": [], "children": [], "derived_from": []},
    }
    validate_seed(seed)
    return seed


def _protected_atoms(anchor: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(atom) for atom in anchor["semantic_atoms"] if atom.get("protected")]


def compress_seed(
    client: ModelClient,
    model: str,
    raw_text: str,
    anchor: Mapping[str, Any],
    *,
    target_chars: int,
) -> dict[str, Any]:
    protected = _protected_atoms(anchor)
    prompt = f"""Comprima a ideia para uso cotidiano por uma IA local.

OBJETIVO: minimizar o payload de contexto sem criar significado novo.
ALVO: payload final <= {target_chars} caracteres quando possível.

REGRAS:
1. Os átomos PROTEGIDOS abaixo serão reinseridos pelo sistema e NÃO devem ser reescritos.
2. Comprima apenas core e informação não protegida.
3. Não invente intenção, emoção, causalidade, certeza ou decisão.
4. Não mude proveniência. O afeto será preservado pelo sistema, portanto não o reinterprete.
5. Se algo não protegido puder ser removido sem alterar o núcleo, remova.
6. Não tente caber no limite truncando palavras ou átomos.

ÁTOMOS PROTEGIDOS IMUTÁVEIS:
{json.dumps(protected, ensure_ascii=False, separators=(",", ":"))}

Retorne APENAS:
{{
  "core": {{"meaning":"...","intent":"...","tensions":[]}},
  "unprotected_atoms": [
    {{"statement":"...","kind":"fact|intent|constraint|uncertainty|negation|causality|authorship|affect|relation","provenance":"reported|inferred|hypothesis|memory|observed|measured","confidence":0.0}}
  ]
}}

FONTE PARA COMPRESSÃO:
<<<RAW_SOURCE>>>
{raw_text}
<<<END_RAW_SOURCE>>>"""
    payload = client.generate_json(model, prompt, system="IDEA_LEDGER_SEED_COMPRESSOR_V1", temperature=0.0)
    core = payload.get("core")
    if not isinstance(core, dict):
        raise SemanticLabError("compressed core ausente")
    unprotected_rows = payload.get("unprotected_atoms", [])
    if not isinstance(unprotected_rows, list):
        raise SemanticLabError("unprotected_atoms deve ser lista")
    unprotected: list[dict[str, Any]] = []
    for idx, row in enumerate(unprotected_rows, start=1):
        if not isinstance(row, dict):
            raise SemanticLabError("unprotected atom deve ser objeto")
        kind = row.get("kind")
        provenance = row.get("provenance")
        if kind not in ALLOWED_KINDS or provenance not in ALLOWED_PROVENANCE:
            raise SemanticLabError("unprotected atom contém kind/provenance inválido")
        unprotected.append({
            "id": f"u{idx}",
            "statement": _require_text(row.get("statement"), f"unprotected_atoms[{idx}].statement"),
            "kind": kind,
            "protected": False,
            "provenance": provenance,
            "confidence": _require_float01(row.get("confidence"), f"unprotected_atoms[{idx}].confidence"),
            "verbatim_tokens": [],
        })

    seed = {
        **dict(anchor),
        "core": {
            "meaning": _require_text(core.get("meaning"), "compressed.core.meaning"),
            "intent": _require_text(core.get("intent"), "compressed.core.intent"),
            "tensions": _text_list(core.get("tensions", []), "compressed.core.tensions"),
        },
        "semantic_atoms": protected + unprotected,
        "affect": dict(anchor["affect"]),
    }
    validate_seed(seed)
    return seed


def runtime_payload(seed: Mapping[str, Any]) -> dict[str, Any]:
    atoms = []
    for atom in seed["semantic_atoms"]:
        atoms.append({
            "id": atom["id"],
            "s": atom["statement"],
            "k": atom["kind"],
            "p": atom["provenance"],
            "c": atom["confidence"],
            "x": 1 if atom.get("protected") else 0,
        })
    affect = seed["affect"]
    return {
        "m": seed["core"]["meaning"],
        "i": seed["core"]["intent"],
        "t": seed["core"].get("tensions", []),
        "a": atoms,
        "f": {
            "m": affect["mode"],
            "q": affect.get("qualities", []),
            "v": affect.get("valence"),
            "r": affect.get("arousal"),
            "c": affect.get("confidence"),
        },
    }


def payload_json(seed: Mapping[str, Any]) -> str:
    return json.dumps(runtime_payload(seed), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def compression_metrics(raw_text: str, seed: Mapping[str, Any], target_chars: int) -> CompressionMetrics:
    payload = payload_json(seed)
    raw_bytes = len(raw_text.encode("utf-8"))
    payload_bytes = len(payload.encode("utf-8"))
    return CompressionMetrics(
        raw_chars=len(raw_text),
        raw_bytes=raw_bytes,
        payload_chars=len(payload),
        payload_bytes=payload_bytes,
        char_ratio=round(len(payload) / max(1, len(raw_text)), 4),
        byte_ratio=round(payload_bytes / max(1, raw_bytes), 4),
        target_chars=target_chars,
        within_target=len(payload) <= target_chars,
    )


def decode_seed(client: ModelClient, model: str, seed: Mapping[str, Any]) -> str:
    payload = payload_json(seed)
    prompt = f"""Reconstrua a ideia em português natural usando SOMENTE o payload abaixo.

REGRAS:
- Preserve autoria, negações, causalidade, incerteza, intenção e proveniência.
- Não transforme inferred em reported, hipótese em fato, possibilidade em decisão.
- Não invente contexto ausente.
- Pode mudar estilo e palavras, não o sentido.

<<<IDEA_SEED>>>
{payload}
<<<END_IDEA_SEED>>>"""
    return client.generate_text(model, prompt, system="IDEA_LEDGER_DECODER_V1", temperature=0.1).strip()


def judge_reconstruction(
    client: ModelClient,
    model: str,
    *,
    original_text: str,
    anchor: Mapping[str, Any],
    reconstruction: str,
) -> dict[str, Any]:
    atoms = anchor["semantic_atoms"]
    prompt = f"""Julgue a reconstrução contra a FONTE ORIGINAL. Seja conservador.

Para CADA atom_id classifique relation como:
- entailed: a reconstrução preserva o compromisso;
- contradicted: altera/inverte o compromisso;
- unknown: omite ou deixa insuficiente para sustentar o compromisso.

Liste em novel_material_claims qualquer afirmação material da reconstrução que NÃO seja sustentada pela fonte original. Mudança puramente estilística não é novidade material.

Retorne APENAS JSON:
{{
  "idea_id": {json.dumps(anchor['id'], ensure_ascii=False)},
  "atom_results": [{{"atom_id":"a1","relation":"entailed|contradicted|unknown"}}],
  "novel_material_claims": []
}}

ÁTOMOS CANÔNICOS:
{json.dumps(atoms, ensure_ascii=False, separators=(",", ":"))}

<<<ORIGINAL>>>
{original_text}
<<<END_ORIGINAL>>>

<<<RECONSTRUCTION>>>
{reconstruction}
<<<END_RECONSTRUCTION>>>"""
    result = client.generate_json(model, prompt, system="IDEA_LEDGER_FIDELITY_JUDGE_V1", temperature=0.0)
    result["idea_id"] = anchor["id"]
    return result


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return value or "idea"


def _round_record(
    *,
    round_index: int,
    budget: int,
    source_text: str,
    compressed: Mapping[str, Any],
    reconstruction: str,
    judgement: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = compression_metrics(source_text, compressed, budget)
    fidelity = evaluate_fidelity(anchor, judgement, reconstruction=reconstruction)
    return {
        "round": round_index,
        "budget_chars": budget,
        "metrics": metrics.to_dict(),
        "fidelity": fidelity.to_dict(),
        "reconstruction": reconstruction,
        "judgement": dict(judgement),
        "runtime_payload": runtime_payload(compressed),
    }


def run_experiment(
    client: ModelClient,
    *,
    original_text: str,
    idea_id: str,
    raw_anchor: str,
    source_ref: str,
    visibility: str,
    compressor_model: str,
    decoder_model: str,
    judge_model: str,
    budgets: Sequence[int],
    roundtrips: int = 1,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not original_text.strip():
        raise SemanticLabError("fonte vazia")
    if roundtrips < 1:
        raise SemanticLabError("roundtrips deve ser >= 1")
    clean_budgets = sorted({int(b) for b in budgets if int(b) > 0}, reverse=True)
    if not clean_budgets:
        raise SemanticLabError("informe ao menos um budget positivo")
    created_at = created_at or datetime.now(timezone.utc).isoformat()

    anchor = extract_anchor(
        client,
        compressor_model,
        original_text,
        idea_id=idea_id,
        raw_anchor=raw_anchor,
        source_ref=source_ref,
        visibility=visibility,
        created_at=created_at,
    )

    sweeps: list[dict[str, Any]] = []
    for budget in clean_budgets:
        current_text = original_text
        rounds: list[dict[str, Any]] = []
        for round_index in range(1, roundtrips + 1):
            compressed = compress_seed(client, compressor_model, current_text, anchor, target_chars=budget)
            reconstruction = decode_seed(client, decoder_model, compressed)
            judgement = judge_reconstruction(
                client,
                judge_model,
                original_text=original_text,
                anchor=anchor,
                reconstruction=reconstruction,
            )
            record = _round_record(
                round_index=round_index,
                budget=budget,
                source_text=original_text,
                compressed=compressed,
                reconstruction=reconstruction,
                judgement=judgement,
                anchor=anchor,
            )
            rounds.append(record)
            current_text = reconstruction

        all_pass = all(item["fidelity"]["passed"] for item in rounds)
        all_within = all(item["metrics"]["within_target"] for item in rounds)
        sweeps.append({
            "budget_chars": budget,
            "passed": all_pass and all_within,
            "semantic_fidelity_passed": all_pass,
            "within_budget_all_rounds": all_within,
            "rounds": rounds,
        })

    passing = [item for item in sweeps if item["passed"]]
    safe_floor = min((item["budget_chars"] for item in passing), default=None)
    return {
        "lab_version": "0.2.0",
        "idea_id": idea_id,
        "created_at": created_at,
        "source": {
            "ref": source_ref,
            "raw_anchor": raw_anchor,
            "visibility": visibility,
            "sha256": hashlib.sha256(original_text.encode("utf-8")).hexdigest(),
            "raw_chars": len(original_text),
            "raw_bytes": len(original_text.encode("utf-8")),
            "content_persisted_in_report": False,
        },
        "models": {
            "compressor": compressor_model,
            "decoder": decoder_model,
            "judge": judge_model,
            "judge_independent_from_generation": judge_model not in {compressor_model, decoder_model},
        },
        "anchor": anchor,
        "budgets_chars": clean_budgets,
        "roundtrips": roundtrips,
        "comparison_anchor": "original_source_and_original_anchor",
        "safe_floor_target_chars": safe_floor,
        "sweeps": sweeps,
    }


def render_markdown_report(result: Mapping[str, Any]) -> str:
    lines = [
        f"# Idea Ledger Semantic Lab · {result['idea_id']}",
        "",
        f"- Original: `{result['source']['raw_chars']}` chars / `{result['source']['raw_bytes']}` bytes",
        f"- Compressor: `{result['models']['compressor']}`",
        f"- Decoder: `{result['models']['decoder']}`",
        f"- Judge: `{result['models']['judge']}`",
        f"- Judge independente: `{result['models']['judge_independent_from_generation']}`",
        f"- Round-trips por budget: `{result['roundtrips']}`",
        f"- Menor budget aprovado: `{result['safe_floor_target_chars']}` chars",
        "",
        "| budget | pass | semantic | dentro do alvo | pior payload chars |",
        "|---:|:---:|:---:|:---:|---:|",
    ]
    for sweep in result["sweeps"]:
        worst = max(round_["metrics"]["payload_chars"] for round_ in sweep["rounds"])
        lines.append(
            f"| {sweep['budget_chars']} | {sweep['passed']} | {sweep['semantic_fidelity_passed']} | "
            f"{sweep['within_budget_all_rounds']} | {worst} |"
        )
    lines += [
        "",
        "> Cada round-trip é julgado contra a fonte original e a âncora original, nunca contra a rodada anterior.",
        "> O texto original não é copiado para o relatório; reconstruções podem conter conteúdo sensível e devem permanecer locais.",
        "",
    ]
    return "\n".join(lines)


def save_result(result: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "result.json"
    md_path = output_dir / "report.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(result), encoding="utf-8")
    return json_path, md_path


def _parse_budgets(value: str) -> list[int]:
    try:
        budgets = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("budgets deve ser lista de inteiros separados por vírgula") from exc
    if not budgets or any(value <= 0 for value in budgets):
        raise argparse.ArgumentTypeError("budgets deve conter inteiros positivos")
    return budgets


def _ensure_models(client: ModelClient, names: Sequence[str]) -> None:
    available = client.list_models()
    missing = [name for name in names if name not in available]
    if missing:
        raise SemanticLabError(
            "modelos não encontrados no Ollama: " + ", ".join(missing)
            + ". Disponíveis: " + (", ".join(available) if available else "nenhum")
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Idea Ledger local semantic compression lab")
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="lista modelos locais visíveis")

    p_run = sub.add_parser("run", help="executa sweep de compressão + round-trip")
    p_run.add_argument("--source", required=True)
    p_run.add_argument("--idea-id")
    p_run.add_argument("--raw-anchor")
    p_run.add_argument("--visibility", choices=["private", "public"], default="private")
    p_run.add_argument("--compressor-model", default=os.getenv("IDEA_LEDGER_COMPRESSOR_MODEL", "qwen3:4b"))
    p_run.add_argument("--decoder-model", default=os.getenv("IDEA_LEDGER_DECODER_MODEL", "qwen3:4b"))
    p_run.add_argument("--judge-model", default=os.getenv("IDEA_LEDGER_JUDGE_MODEL", "qwen3:8b"))
    p_run.add_argument("--budgets", type=_parse_budgets, default=_parse_budgets("1800,1400,1100,900,700"))
    p_run.add_argument("--roundtrips", type=int, default=3)
    p_run.add_argument("--output")

    args = parser.parse_args()
    client = OllamaClient(args.ollama_url)
    if args.command == "models":
        print(json.dumps({"models": client.list_models()}, ensure_ascii=False, indent=2))
        return 0

    source_path = Path(args.source).expanduser().resolve()
    raw_text = source_path.read_text(encoding="utf-8")
    idea_id = args.idea_id or f"idea-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    raw_anchor = args.raw_anchor or str(source_path)
    _ensure_models(client, [args.compressor_model, args.decoder_model, args.judge_model])
    result = run_experiment(
        client,
        original_text=raw_text,
        idea_id=idea_id,
        raw_anchor=raw_anchor,
        source_ref=source_path.name,
        visibility=args.visibility,
        compressor_model=args.compressor_model,
        decoder_model=args.decoder_model,
        judge_model=args.judge_model,
        budgets=args.budgets,
        roundtrips=args.roundtrips,
    )
    output = Path(args.output) if args.output else Path("idea-ledger/experiments/runs") / _safe_slug(idea_id)
    json_path, md_path = save_result(result, output)
    print(json.dumps({
        "status": "completed",
        "result": str(json_path),
        "report": str(md_path),
        "safe_floor_target_chars": result["safe_floor_target_chars"],
        "judge_independent": result["models"]["judge_independent_from_generation"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SemanticLabError, IdeaLedgerError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
        raise SystemExit(2)
