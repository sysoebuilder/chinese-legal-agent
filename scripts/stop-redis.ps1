$ErrorActionPreference = 'Stop'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($null -ne $docker) {
    $dockerPath = $docker.Source
} else {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) { throw '未找到 Docker。' }
}
$composeFile = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\compose.yaml'))
& $dockerPath compose -f $composeFile stop redis
if ($LASTEXITCODE -ne 0) { throw 'Redis 停止失败。' }
Write-Output 'Redis 已停止，持久卷中的队列数据保留。'
