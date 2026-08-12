# Local Semantic Lab

Este laboratório mede **quanto uma ideia pode ser compactada antes de começar a mudar de sentido**.

## Protocolo

```text
fonte original
    ↓
âncora canônica (átomos + proveniência)
    ↓
compressor local
    ↓
runtime seed compacto
    ↓
decoder sem acesso à fonte original
    ↓
juiz semântico
    ↓
fidelity gate determinístico
```

### Regra contra telefone-sem-fio

Em `roundtrips > 1`, a saída de uma rodada vira a entrada de compressão da próxima. Porém **todas as reconstruções continuam sendo julgadas contra a fonte original e a âncora original**.

### O que medimos

- tamanho da fonte em caracteres e bytes;
- tamanho do runtime payload em caracteres e bytes;
- razão payload/original;
- recall de átomos protegidos;
- contradições;
- átomos protegidos omitidos/indeterminados;
- afirmações materiais novas;
- respeito a literais protegidos;
- menor budget que sobrevive a todos os round-trips.

Não chamamos `chars` de tokens. Tokenização depende do modelo. Quando adicionarmos tokenizadores específicos, essa métrica entra separadamente.

## Privacidade

`experiments/runs/` deve permanecer local. O relatório não grava a fonte original, mas pode gravar reconstruções que revelem seu conteúdo.

## Windows

No PowerShell, a partir do repositório:

```powershell
.\idea-ledger\scripts\Run-Idea-Ledger-Local.ps1 -Source 'C:\caminho\ideia.md'
```

Defaults:

- compressor: `qwen3:4b`
- decoder: `qwen3:4b`
- judge: `qwen3:8b`
- budgets: `1800,1400,1100,900,700`
- round-trips: `3`

Se o judge não existir localmente, o runner usa o decoder como judge e o resultado fica explicitamente marcado como **não independente**.

## Interpretação

Um budget só é aprovado quando:

1. o payload realmente cabe no alvo;
2. todos os átomos protegidos continuam sustentados;
3. não há contradição;
4. não surgem afirmações materiais novas;
5. todos os round-trips passam contra o original.

O menor budget aprovado é o primeiro candidato a **semantic floor** daquela ideia.
