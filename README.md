# MATHEUSFS36 · Diário Vivo de Ideias

Fundação de um **Idea Ledger** pessoal: memória econômica para máquinas sem sacrificar a origem e o sentido das ideias.

Princípio central:

> **lossy context, lossless provenance**

A forma linguística pode ser compactada. A proveniência não.

## Fluxo

```text
IDEIA / EXPERIÊNCIA
      ↓
RAW ANCHOR                fonte preservada
      ↓
SEMANTIC ATOMS            compromissos de significado
      ↓
IDEA SEED                 memória compacta
      ↓
busca / genealogia
      ↓
EXPANSÃO LINGUÍSTICA
      ↓
FIDELITY GATE             concordância com a origem
```

## Por que existem duas memórias?

Guardar texto em disco é barato. Colocar todo esse texto no contexto do modelo repetidamente é caro.

Por isso:

- `raw/` preserva a fonte original;
- `seeds/` contém a representação compacta usada no cotidiano;
- `promoted/` guarda ideias já revisadas;
- `connections/` registra genealogia e relações;
- `fidelity/` guarda julgamentos de reconstrução;
- `tools/` implementa o contrato de fidelidade.

## Regra de ouro

Uma reconstrução pode mudar **as palavras**, mas não pode mudar silenciosamente:

- autoria;
- nomes, datas ou números protegidos;
- negações;
- causalidade;
- grau de certeza;
- intenção;
- restrições;
- proveniência afetiva;
- relações genealógicas relevantes.

Se o sistema só **inferiu** uma emoção, ela continua `inferred`. Não pode reaparecer depois como algo explicitamente dito pela pessoa.

## Estado desta fundação

Esta versão implementa o **contrato determinístico** e a interface para um juiz semântico futuro.

Ela já testa:

1. perda de átomos protegidos;
2. contradições;
3. introdução de afirmações materiais novas;
4. preservação de proveniência;
5. deriva em múltiplos round-trips sempre comparada ao original;
6. âncoras lexicais para dados de alto risco.

O juiz semântico pode ser conectado depois a um modelo local/NLI. O gate não depende de um fornecedor específico.
