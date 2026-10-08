param([switch]$ListOnly)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$candidates = [Collections.Generic.List[object]]::new()

function Add-CleanupTarget($item, [string]$reason) {
    $fullPath = [IO.Path]::GetFullPath($item.FullName)
    if (-not $fullPath.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "清理目标不在项目内：$fullPath"
    }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "不清理目录联接或符号链接：$fullPath"
    }
    $candidates.Add([pscustomobject]@{ path = $fullPath; reason = $reason })
}

# 仅匹配已确认的生成目录，不遍历虚拟环境、模型、数据库备份或依赖包。
Get-ChildItem -LiteralPath $projectRoot -Directory -Force |
    Where-Object { $_.Name -like '.test-tmp-*' -or $_.Name -in '.pytest_cache', '.ruff_cache', '.mypy_cache' } |
    ForEach-Object { Add-CleanupTarget $_ '测试临时目录或工具缓存' }

Get-ChildItem -LiteralPath (Join-Path $projectRoot 'data') -Directory -Force |
    Where-Object {
        $_.Name -like 'pytest-*' -or $_.Name -like 'mypy-*' -or
        $_.Name -in 'pycache-checkpoint', 'npm-cache'
    } | ForEach-Object { Add-CleanupTarget $_ '测试、类型检查或 npm 缓存' }

foreach ($directory in @('src', 'tests', 'scripts', 'evals', 'laws')) {
    Get-ChildItem -LiteralPath (Join-Path $projectRoot $directory) -Directory -Recurse -Force |
        Where-Object { $_.Name -eq '__pycache__' } |
        ForEach-Object { Add-CleanupTarget $_ 'Python 字节码缓存' }
}

foreach ($relative in @(
    'frontend\dist',
    'frontend\node_modules\.vite',
    'frontend\node_modules\.vite-temp',
    'data\chat-preview',
    'data\api-refactor-before-20261005-213512',
    'data\chat-production-check.cjs',
    'data\cpu-fallback-check.py',
    'data\gpu-smoke-check.py',
    'scripts\inspect-c-drive-admin.ps1'
)) {
    $path = Join-Path $projectRoot $relative
    if (Test-Path -LiteralPath $path) {
        Add-CleanupTarget (Get-Item -LiteralPath $path -Force) '旧构建产物、临时副本或一次性诊断文件'
    }
}

# 旧主机运行目录只有为空时才删除。
foreach ($relative in @('data\contexts', 'data\logs', 'data\memory', 'data\sessions')) {
    $path = Join-Path $projectRoot $relative
    if ((Test-Path -LiteralPath $path) -and -not (Get-ChildItem -LiteralPath $path -Force | Select-Object -First 1)) {
        Add-CleanupTarget (Get-Item -LiteralPath $path -Force) '空的旧运行目录'
    }
}

$targets = @($candidates | Sort-Object path -Unique)
if ($ListOnly) {
    $targets | ForEach-Object { "$($_.path) [$($_.reason)]" }
    Write-Host "共 $($targets.Count) 个清理项；未删除文件。"
    return
}

$removed = [Collections.Generic.List[string]]::new()
$failures = [Collections.Generic.List[object]]::new()
foreach ($target in $targets) {
    try {
        # 删除前再次核对顶层路径，防止目录已被替换为联接。
        $item = Get-Item -LiteralPath $target.path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw '目标已变成目录联接或符号链接'
        }
        if ($item.PSIsContainer) {
            $links = Get-ChildItem -LiteralPath $target.path -Recurse -Force |
                Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
                Select-Object -First 1
            if ($links) { throw '目录内包含链接，跳过清理' }
        }
        Remove-Item -LiteralPath $target.path -Recurse -Force
        $removed.Add($target.path)
        Write-Host "已删除：$($target.path)"
    } catch {
        $failures.Add([pscustomobject]@{ path = $target.path; error = $_.Exception.Message })
        Write-Warning "未删除 $($target.path)：$($_.Exception.Message)"
    }
}

$reportPath = Join-Path $projectRoot 'data\project-cleanup-result.json'
[pscustomobject]@{
    completed_at = (Get-Date).ToString('o')
    deleted = @($removed.ToArray())
    failed = @($failures.ToArray())
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Host "完成：删除 $($removed.Count) 项，失败 $($failures.Count) 项。清单位于 $reportPath"
if ($failures.Count -gt 0) { exit 1 }
