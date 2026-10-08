param(
    [switch]$PrepareOnly,
    [switch]$SkipBuild,
    [switch]$Reinstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$provider = Join-Path $projectRoot '.venv\Scripts\agent100-mcp-provider.exe'
$manifest = Join-Path $projectRoot 'pyproject.toml'
$installedHash = Join-Path $projectRoot '.venv\agent100-project.sha256'

# 优先使用 PATH 中的 Docker，兼容 Docker Desktop 的用户级安装。
if (-not $PrepareOnly) {
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
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $projectPython)) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $basePython = $launcher.Source
            $baseArguments = @('-3')
        } elseif ($pythonCommand) {
            $basePython = $pythonCommand.Source
            $baseArguments = @()
        } else {
            throw '请先安装 Python 3.11 或更新版本。'
        }
        & $basePython @baseArguments -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
        if ($LASTEXITCODE -ne 0) { throw '需要可用的 Python 3.11 或更新版本。' }
        Write-Host '正在创建项目 Python 环境……'
        & $basePython @baseArguments -m venv (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw '创建 .venv 失败。' }
    }

    & $projectPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
    if ($LASTEXITCODE -ne 0) { throw '项目 .venv 的 Python 版本需要至少为 3.11。' }
    $manifestHash = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash
    $previousHash = if (Test-Path -LiteralPath $installedHash) {
        (Get-Content -LiteralPath $installedHash -Raw).Trim()
    } else { '' }
    if ($Reinstall -or $manifestHash -ne $previousHash -or -not (Test-Path -LiteralPath $provider)) {
        Write-Host '正在根据 pyproject.toml 安装项目依赖与 MCP provider……'
        & $projectPython -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw '安装项目依赖失败；再次运行此脚本可重试。' }
        if (-not (Test-Path -LiteralPath $provider)) { throw '安装后未找到 MCP provider 程序。' }
        Set-Content -LiteralPath $installedHash -Value $manifestHash -Encoding ascii
    } else {
        Write-Host '项目 Python 环境已就绪。'
    }

    if (-not $PrepareOnly) {
        $composeArguments = @('compose', 'up', '-d', '--wait')
        if (-not $SkipBuild) { $composeArguments += '--build' }
        & $dockerCli @composeArguments
        if ($LASTEXITCODE -ne 0) { throw 'Compose 启动失败。' }
    }
} finally { Pop-Location }
