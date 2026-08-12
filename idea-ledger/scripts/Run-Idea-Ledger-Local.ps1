[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$Source,

    [string]$CompressorModel = $(if($env:IDEA_LEDGER_COMPRESSOR_MODEL){$env:IDEA_LEDGER_COMPRESSOR_MODEL}else{'qwen3:4b'}),
    [string]$DecoderModel = $(if($env:IDEA_LEDGER_DECODER_MODEL){$env:IDEA_LEDGER_DECODER_MODEL}else{'qwen3:4b'}),
    [string]$JudgeModel = $(if($env:IDEA_LEDGER_JUDGE_MODEL){$env:IDEA_LEDGER_JUDGE_MODEL}else{'qwen3:8b'}),
    [string]$Budgets = '1800,1400,1100,900,700',
    [int]$RoundTrips = 3,
    [string]$OllamaUrl = $(if($env:OLLAMA_URL){$env:OLLAMA_URL}else{'http://127.0.0.1:11434'}),
    [ValidateSet('private','public')]
    [string]$Visibility = 'private'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tool = Join-Path $repoRoot 'idea-ledger\tools\semantic_lab.py'
$sourcePath = (Resolve-Path $Source).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ideaId = "idea-$stamp"
$output = Join-Path $repoRoot "idea-ledger\experiments\runs\$ideaId"

Write-Host "`n=== IDEA LEDGER LOCAL SEMANTIC LAB ===" -ForegroundColor Cyan
Write-Host "Source:     $sourcePath"
Write-Host "Ollama:     $OllamaUrl"
Write-Host "Compressor: $CompressorModel"
Write-Host "Decoder:    $DecoderModel"
Write-Host "Judge:      $JudgeModel"
Write-Host "Budgets:    $Budgets chars"
Write-Host "Rounds:     $RoundTrips"
Write-Host "Output:     $output`n"

$python = Get-Command python -ErrorAction SilentlyContinue
if(-not $python){ throw 'Python não encontrado no PATH.' }

try {
    $tags = Invoke-RestMethod -Method Get -Uri ($OllamaUrl.TrimEnd('/') + '/api/tags') -TimeoutSec 10
} catch {
    throw "Ollama não respondeu em $OllamaUrl. Confirme se está aberto/rodando. $($_.Exception.Message)"
}

$available = @($tags.models | ForEach-Object { $_.name })
Write-Host ('Modelos locais: ' + ($available -join ', ')) -ForegroundColor DarkCyan

foreach($required in @($CompressorModel,$DecoderModel)){
    if($available -notcontains $required){
        throw "Modelo obrigatório '$required' não encontrado. Disponíveis: $($available -join ', ')"
    }
}

if($available -notcontains $JudgeModel){
    Write-Warning "Judge '$JudgeModel' não encontrado. Vou usar '$DecoderModel'. O relatório marcará judge_independent=false."
    $JudgeModel = $DecoderModel
}

& $python.Source $tool --ollama-url $OllamaUrl run `
    --source $sourcePath `
    --idea-id $ideaId `
    --visibility $Visibility `
    --compressor-model $CompressorModel `
    --decoder-model $DecoderModel `
    --judge-model $JudgeModel `
    --budgets $Budgets `
    --roundtrips $RoundTrips `
    --output $output

if($LASTEXITCODE -ne 0){ throw "Semantic Lab falhou com exit code $LASTEXITCODE" }

Write-Host "`nPASS: experimento concluído." -ForegroundColor Green
Write-Host "Abra: $(Join-Path $output 'report.md')" -ForegroundColor Green
