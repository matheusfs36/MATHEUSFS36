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
$tool = Join-Path $repoRoot 'idea-ledger\tools\semantic_lab_ollama.py'
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
if(-not $python){ throw 'Python nao encontrado no PATH.' }

function Get-OllamaTags {
    param([int]$TimeoutSec = 3)
    try {
        return Invoke-RestMethod -Method Get -Uri ($OllamaUrl.TrimEnd('/') + '/api/tags') -TimeoutSec $TimeoutSec
    } catch {
        return $null
    }
}

$tags = Get-OllamaTags
if(-not $tags){
    $uri = [Uri]$OllamaUrl
    $isLocal = @('127.0.0.1','localhost','::1') -contains $uri.Host
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue

    if($isLocal -and $ollama){
        Write-Host 'Ollama local nao respondeu. Tentando iniciar ollama serve...' -ForegroundColor Yellow
        Start-Process -FilePath $ollama.Source -ArgumentList 'serve' -WindowStyle Hidden | Out-Null

        foreach($attempt in 1..20){
            Start-Sleep -Milliseconds 500
            $tags = Get-OllamaTags
            if($tags){ break }
        }
    }
}

if(-not $tags){
    $ollamaHint = Get-Command ollama -ErrorAction SilentlyContinue
    if($ollamaHint){
        throw "Ollama nao respondeu em $OllamaUrl mesmo apos tentativa de inicio. Rode 'ollama serve' e teste novamente."
    }
    throw "Ollama nao respondeu em $OllamaUrl e o comando 'ollama' nao foi encontrado no PATH."
}

Write-Host 'Ollama online.' -ForegroundColor Green

$available = @($tags.models | ForEach-Object { $_.name })
Write-Host ('Modelos locais: ' + ($available -join ', ')) -ForegroundColor DarkCyan

foreach($required in @($CompressorModel,$DecoderModel)){
    if($available -notcontains $required){
        throw "Modelo obrigatorio '$required' nao encontrado. Disponiveis: $($available -join ', ')"
    }
}

if($available -notcontains $JudgeModel){
    Write-Warning "Judge '$JudgeModel' nao encontrado. Vou usar '$DecoderModel'. O relatorio marcara judge_independent=false."
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

Write-Host "`nPASS: experimento concluido." -ForegroundColor Green
Write-Host "Abra: $(Join-Path $output 'report.md')" -ForegroundColor Green
