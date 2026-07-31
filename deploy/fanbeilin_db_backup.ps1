# Daily SQLite backup for factory-hub (fanbeilin instance). ASCII-only (GBK server).
# Mirrors the ERP-DBBackup-kx convention: 14 days of copies under C:\erp\backups\fanbeilin,
# one line per run in backup.log. Unlike the kx script this needs no DB password.
#
# The copy goes through sqlite_backup.py (online backup API + integrity check + empty-db
# sentinel) -- do NOT downgrade this to Copy-Item, the app writes to the DB while this runs.
#
# Deliberate details (each one is a bug that was found in review):
#  * $py path is probed before use -- it points into another account's per-user Python.
#  * stderr is folded into stdout (2>&1) so the real reason lands in backup.log,
#    not just "exit code 1".
#  * prune failures must not fail the whole run: the new backup already succeeded,
#    and a locked old file would otherwise report FAIL and skip the rest of the cleanup.
#  * logging itself is wrapped -- "disk full" is exactly the failure that both breaks
#    the backup and breaks the attempt to log it.
$ErrorActionPreference = 'Stop'

$dir    = 'C:\erp\backups\fanbeilin'
$log    = Join-Path $dir 'backup.log'
$src    = 'C:\factory\fanbeilin\backend\factory_hub.db'
$helper = 'C:\factory\fanbeilin\deploy\sqlite_backup.py'
$py     = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe'
$keepDays = 14

function Log($m) {
  # 记日志本身也可能失败(盘满/目录不可写)，不能让它把真正的错误盖掉
  try {
    Add-Content -Path $log -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + '  ' + $m) -ErrorAction Stop
  } catch {
    Write-Output ('LOGFAIL ' + $m)
  }
}

try {
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
  if (-not (Test-Path $py))     { throw ('python not found: ' + $py) }
  if (-not (Test-Path $helper)) { throw ('helper not found: ' + $helper) }
  if (-not (Test-Path $src))    { throw ('source db not found: ' + $src) }

  $stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
  $out   = Join-Path $dir ('factory_hub_' + $stamp + '.db')

  $detail = (& $py $helper $src $out 2>&1 | Out-String).Trim()
  $code = $LASTEXITCODE
  if ($code -ne 0) { throw ('sqlite_backup.py exit ' + $code + ' :: ' + $detail) }
  if (-not (Test-Path $out)) { throw ('backup reported success but file missing: ' + $out) }

  $size = [math]::Round((Get-Item $out).Length / 1MB, 2)
  Log('OK ' + $out + ' (' + $size + ' MB) ' + $detail)

  # retention：只在本次成功后才执行；单个文件删不掉不算整次失败
  try {
    Get-ChildItem $dir -Filter 'factory_hub_*.db' -ErrorAction Stop |
      Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$keepDays) } |
      ForEach-Object {
        try {
          Remove-Item $_.FullName -Force -ErrorAction Stop
          Log('PRUNED ' + $_.Name)
        } catch {
          Log('PRUNE-SKIP ' + $_.Name + ' :: ' + $_.Exception.Message)
        }
      }
  } catch {
    Log('PRUNE-ENUM-FAIL ' + $_.Exception.Message)
  }
} catch {
  Log('FAIL ' + $_.Exception.Message)
  exit 1
}
