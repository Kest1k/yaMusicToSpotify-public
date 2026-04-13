param(
  [switch]$Login,
  [switch]$Chrome,
  [switch]$Main,
  [int]$Start = -1
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
$script = if ($Main) { 'browser_import\main.py' } else { 'browser_import\main_optimized.py' }
$arguments = @($script)

if ($Login) { $arguments += '--login' }
if ($Chrome) { $arguments += '--chrome' }
if ($Start -ge 0) {
  $arguments += '--start'
  $arguments += "$Start"
}

& $python @arguments
