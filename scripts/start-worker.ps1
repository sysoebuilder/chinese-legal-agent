$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $projectRoot
try {
    & docker compose up -d --wait worker
    if ($LASTEXITCODE -ne 0) { throw '容器 Celery worker 启动失败。' }
} finally { Pop-Location }
