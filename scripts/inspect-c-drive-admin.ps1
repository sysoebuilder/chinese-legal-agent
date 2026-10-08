# 只读取文件元数据和磁盘占用；报告写入项目 E 盘，不删除文件或修改权限。
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '此诊断需要管理员 PowerShell；不会删除文件、清理快照或修改 ACL。'
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputDirectory = Join-Path $projectRoot 'data\backups'
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$outputPath = Join-Path $outputDirectory ('c-drive-admin-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$roots = @(
    'C:\Users\CodexSandboxOffline',
    'C:\Windows\LiveKernelReports',
    'C:\Windows\Minidump',
    'C:\Windows\Temp',
    'C:\Windows\SystemTemp',
    'C:\ProgramData\Microsoft\Windows\WER',
    'C:\Program Files\WindowsApps',
    'C:\$Recycle.Bin'
)
$previousScanPath = Join-Path $outputDirectory 'c-drive-investigation-20261007.json'
if (Test-Path -LiteralPath $previousScanPath) {
    $previousScan = Get-Content -LiteralPath $previousScanPath -Raw | ConvertFrom-Json
    $candidateRoots = @($roots) + @($previousScan.inaccessible_paths | ForEach-Object { $_.path })
    $roots = @()
    foreach ($candidate in ($candidateRoots | Sort-Object Length, { $_ } -Unique)) {
        $covered = $false
        foreach ($existing in $roots) {
            if ($candidate -eq $existing -or $candidate.StartsWith($existing + '\', [StringComparison]::OrdinalIgnoreCase)) {
                $covered = $true
                break
            }
        }
        if (-not $covered) { $roots += $candidate }
    }
}
$cutoff = [datetime]'2026-10-05'
$summaries = [Collections.Generic.List[object]]::new()
$largeFiles = [Collections.Generic.List[object]]::new()
$failures = [Collections.Generic.List[string]]::new()
foreach ($root in $roots) {
    $stack = [Collections.Generic.Stack[string]]::new()
    $stack.Push($root)
    [long]$bytes = 0
    [long]$createdBytes = 0
    [long]$modifiedBytes = 0
    [long]$files = 0
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        try {
            $entries = @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)
        } catch {
            $failures.Add($directory + ': ' + $_.Exception.Message)
            continue
        }
        foreach ($entry in $entries) {
            if ($entry.PSIsContainer) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                $stack.Push($entry.FullName)
                continue
            }
            $files++
            $bytes += $entry.Length
            if ($entry.CreationTime -ge $cutoff) { $createdBytes += $entry.Length }
            if ($entry.LastWriteTime -ge $cutoff) { $modifiedBytes += $entry.Length }
            if ($entry.Length -ge 100MB) {
                $largeFiles.Add([ordered]@{
                    path = $entry.FullName
                    bytes = $entry.Length
                    created_at = $entry.CreationTime.ToString('o')
                    modified_at = $entry.LastWriteTime.ToString('o')
                })
            }
        }
    }
    $summaries.Add([ordered]@{
        path = $root
        bytes = $bytes
        files = $files
        files_created_since_oct5_bytes = $createdBytes
        files_modified_since_oct5_bytes = $modifiedBytes
    })
}
$shadowStorage = @(& "$env:SystemRoot\System32\vssadmin.exe" list shadowstorage 2>&1 | ForEach-Object { "$_" })
$diskFree = @(& "$env:SystemRoot\System32\fsutil.exe" volume diskfree C: 2>&1 | ForEach-Object { "$_" })
$allocation = @(& "$env:SystemRoot\System32\fsutil.exe" volume allocationreport C: 2>&1 | ForEach-Object { "$_" })
$report = [ordered]@{
    collected_at = (Get-Date).ToString('o')
    note = 'Read-only metadata. File sizes are logical; hard links and sparse files can differ from allocated disk usage.'
    directories = @($summaries.ToArray())
    large_files = @($largeFiles.ToArray() | Sort-Object { $_.bytes } -Descending)
    inaccessible_paths = @($failures.ToArray())
    shadow_storage = $shadowStorage
    disk_free = $diskFree
    allocation_report = $allocation
}
[IO.File]::WriteAllText($outputPath, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Host "只读诊断完成：$outputPath"
