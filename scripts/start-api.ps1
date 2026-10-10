param([string]$HostAddress = '127.0.0.1', [ValidateRange(1, 65535)][int]$Port = 8001)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先创建项目 .venv 并安装依赖。' }
Push-Location $projectRoot
try {
    & $python -m chinese_legal_agent.api --host $HostAddress --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'API 启动或运行失败。' }
} finally { Pop-Location }
