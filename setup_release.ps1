Set-Location $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
  $systemPython = (Get-Command python -ErrorAction Stop).Source
  & $systemPython -m venv .venv
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvPython -m pip install -r 'requirements.txt'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvPython -m playwright install chromium
exit $LASTEXITCODE
