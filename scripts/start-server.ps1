param(
    [string]$Model = (Join-Path $env:USERPROFILE 'models\gemma-4-E2B-it-Q4_K_M.gguf'),
    [int]$Port = 8080,
    [int]$ContextSize = 4096,
    [int]$GpuLayers = 0,
    [int]$Threads = 4
)
$ErrorActionPreference = 'Stop'
$server = Join-Path $PSScriptRoot '..\.runtime\b11342\llama-server.exe'
if (-not (Test-Path -LiteralPath $server)) { throw 'Run python scripts/install_llama.py first.' }
if (-not (Test-Path -LiteralPath $Model)) { throw "Model does not exist: $Model" }
& $server -m $Model --host 127.0.0.1 --port $Port -c $ContextSize -ngl $GpuLayers -t $Threads --parallel 1 --jinja --reasoning off
exit $LASTEXITCODE
