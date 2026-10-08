$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$dockerCli = if ($dockerCommand) { $dockerCommand.Source } else {
    Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
}
Push-Location $projectRoot
try {
    & $dockerCli compose up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose 操作失败。' }
} finally { Pop-Location }
