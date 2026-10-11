param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

# 优先使用 PATH 中的 Docker，兼容 Docker Desktop 的用户级安装。
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$dockerCli = if ($dockerCommand) { $dockerCommand.Source } else { $null }
if (-not $dockerCli) {
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) { $dockerCli = $candidate; break }
    }
}
if (-not $dockerCli) { throw '请先安装 Docker Desktop。' }
$engine = & $dockerCli info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw '请先启动 Docker Desktop。' }
if ($engine -ne 'linux') { throw '请把 Docker Desktop 切换到 Linux containers。' }

Push-Location $projectRoot
try {
    $composeArguments = @('compose', 'up', '-d', '--wait')
    if (-not $SkipBuild) { $composeArguments += '--build' }
    & $dockerCli @composeArguments
    if ($LASTEXITCODE -ne 0) { throw 'Compose 启动失败。' }
} finally { Pop-Location }
