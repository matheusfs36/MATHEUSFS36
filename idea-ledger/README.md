# Idea Ledger

## Diretórios

- `raw/`: fontes originais, preferencialmente imutáveis;
- `seeds/`: sementes semânticas compactas;
- `promoted/`: ideias aprovadas para uso recorrente;
- `connections/`: genealogia entre ideias;
- `fidelity/`: resultados de testes de reconstrução;
- `schema/`: contrato de dados;
- `tools/`: validação e fidelity gate;
- `tests/`: provas herméticas.

## Política de privacidade

Este pacote pode viver em um repositório público, mas **raw anchors pessoais não devem ser publicados automaticamente**.

Um seed contém `source.visibility`. Ferramentas futuras devem recusar promoção pública de `private` sem decisão explícita.

## Protocolo de reconstrução

Um decoder deve receber o seed e produzir linguagem natural. Depois, um juiz semântico independente produz um arquivo de julgamento:

```json
{
  "idea_id": "idea-example-001",
  "atom_results": [
    {"atom_id": "a1", "relation": "entailed"},
    {"atom_id": "a2", "relation": "entailed"}
  ],
  "novel_material_claims": []
}
```

Relações permitidas:

- `entailed`
- `contradicted`
- `unknown`

O gate passa somente quando **todos os átomos protegidos** estão `entailed`, não há contradições e não surgiram afirmações materiais novas.

### Round-trip drift

Em ciclos sucessivos:

```text
original → seed → texto1 → seed2 → texto2 → ...
```

cada rodada é comparada com o **original**, não com a rodada anterior. Isso reduz o efeito de telefone-sem-fio.
