# Daily SQLite backup for factory-hub (fanbeilin instance). ASCII-only (GBK server).
# Mirrors the ERP-DBBackup-kx convention: 14 days of copies under C:\erp\backups\fanbeilin,
# one line per run in backup.log. Unlike the kx script this needs no DB password.
# The actual copy goes through sqlite_backup.py (online backup API + integrity check) --
# do NOT downgrade this to Copy-Item, the app writes to the DB while this runs.
$ErrorActionPreference = 'Stop'

$dir    = 'C:\erp\backups\fanbeilin'
$log    = Join-Path $dir 'backup.log'
$src    = 'C:\factory\fanbeilin\backend\factory_hub.db'
$helper = 'C:\factory\fanbeilin\deploy\sqlite_backup.py'
$py     = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe'
$keepDays = 14

if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }

function Log($m) {
  Add-Content -Path $log -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + '  ' + $m)
}

try {
  $stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
  $out   = Join-Path $dir ('factory_hub_' + $stamp + '.db')

  $detail = & $py $helper $src $out
  if ($LASTEXITCODE -ne 0) { throw ('sqlite_backup.py exit code ' + $LASTEXITCODE) }

  $size = [math]::Round((Get-Item $out).Length / 1MB, 2)
  Log('OK ' + $out + ' (' + $size + ' MB) ' + $detail)

  # retention: drop copies older than $keepDays
  Get-ChildItem $dir -Filter 'factory_hub_*.db' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$keepDays) } |
    ForEach-Object {
      Remove-Item $_.FullName -Force
      Log('PRUNED ' + $_.Name)
    }
} catch {
  Log('FAIL ' + $_.Exception.Message)
  exit 1
}
