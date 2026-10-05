param([string]$DataDirectory = (Join-Path $PSScriptRoot '..\data'))

$ErrorActionPreference = 'Stop'
$dataRoot = [System.IO.Path]::GetFullPath($DataDirectory)
$pgCtl = Join-Path $dataRoot 'postgresql\pgsql\bin\pg_ctl.exe'
$cluster = Join-Path $dataRoot 'postgres-db'
if (-not (Test-Path -LiteralPath $pgCtl) -or -not (Test-Path -LiteralPath (Join-Path $cluster 'PG_VERSION'))) {
    throw '未找到项目本地 PostgreSQL 安装或数据目录。'
}
& $pgCtl -D $cluster status
if ($LASTEXITCODE -ne 0) { exit 0 }
& $pgCtl -D $cluster -m fast -w stop
exit $LASTEXITCODE
