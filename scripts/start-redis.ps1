$ErrorActionPreference = 'Stop'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($null -ne $docker) {
    $dockerPath = $docker.Source
} else {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) { throw '未找到 Docker，请安装并启动 Docker Desktop。' }
}
$composeFile = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\compose.yaml'))
& $dockerPath compose -f $composeFile up -d --wait redis
if ($LASTEXITCODE -ne 0) { throw 'Redis 启动失败，请确认 Docker Desktop 已运行。' }
