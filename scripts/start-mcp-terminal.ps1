param([int]$Port = 8765)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先创建项目 .venv 并安装依赖。' }
Push-Location $projectRoot
try {
    & $python -m ai_agent_learning.mcp_terminal serve --configure --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Windows 终端 MCP 启动或运行失败。' }
} finally { Pop-Location }
