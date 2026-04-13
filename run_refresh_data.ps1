param(
  [switch]$SyncSiteSnapshot
)

function Resolve-Python {
  $candidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    'python'
  )

  foreach ($candidate in $candidates) {
    if ($candidate -eq 'python') { return $candidate }
    if (Test-Path $candidate) { return $candidate }
  }
}

Set-Location $PSScriptRoot
$python = Resolve-Python
$arguments = @('refresh_dashboard_data.py')
if ($SyncSiteSnapshot) {
  $arguments += '--sync-site-snapshot'
}

& $python @arguments
