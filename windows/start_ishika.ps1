param(
  [int]$Port = 8300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path ".venv")) {
  python -m venv .venv
  & .\.venv\Scripts\pip install -q -r requirements.txt | Out-Null
}

. .\.venv\Scripts\Activate.ps1

function Test-PortListening {
  param([int]$Port)
  $match = netstat -ano | Select-String (":" + $Port) | Select-String "LISTENING"
  return $null -ne $match
}

if (-not (Test-PortListening -Port $Port)) {
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "python run.py" -WorkingDirectory $repoRoot | Out-Null
}

while (-not (Test-PortListening -Port $Port)) {
  Start-Sleep -Seconds 1
}

python wrapper.py ishika
