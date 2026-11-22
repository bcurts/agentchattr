# agentchattr server control: server_ctl.ps1 stop|start|restart
# Finds the server via the configured web port (config.toml + config.local.toml + AGENTCHATTR_* env),
# verifies the listener is this checkout's run.py, and never touches agent wrappers.
param([Parameter(Mandatory)][ValidateSet('stop', 'start', 'restart')][string]$Action)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. "$PSScriptRoot\server_ctl_lib.ps1"
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { 'python' }

$port = & $py -c "from pathlib import Path; from config_loader import load_config; print(load_config(Path('.'))['server']['port'])"
if ($LASTEXITCODE -ne 0 -or $port -notmatch '^\d+$') { Write-Error "Could not read server.port. Check $root\config.toml and $root\config.local.toml and the Python error above."; exit 1 }
$port = [int]$port

function Get-ServerPid {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $c.OwningProcess } else { $null }
}

function Get-Chain([int]$ProcId) {
    # the process and up to 3 ancestors (venv launcher -> interpreter), as @{ExecutablePath; CommandLine}
    $chain = @(); $cur = $ProcId
    for ($i = 0; $i -lt 4 -and $cur; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $p) { break }
        $chain += @{ ExecutablePath = $p.ExecutablePath; CommandLine = $p.CommandLine }
        $cur = $p.ParentProcessId
    }
    return $chain
}

function Stop-Server {
    $srvPid = Get-ServerPid
    if (-not $srvPid) { Write-Host "No server listening on port $port."; return }
    $chain = Get-Chain $srvPid
    if (-not (Test-ChainOwnsServer $chain $root)) {
        Write-Error "Port $port is held by PID $srvPid ($($chain[0].CommandLine)), which cannot be verified as this checkout's run.py ($root). Not stopping it. (A server started with a relative `python run.py` can't be identified: stop it manually once and relaunch with windows\start.bat.)"
        exit 2
    }
    # the process tree too: the venv python launcher can sit in front of the real interpreter
    $tree = @($srvPid) + @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$srvPid" | ForEach-Object { $_.ProcessId })
    & taskkill /PID $srvPid /T /F | Out-Null
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $alive = $tree | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
        if (-not $alive -and -not (Get-ServerPid)) { Write-Host "Server (PID $srvPid) stopped."; return }
        Start-Sleep -Milliseconds 300
    }
    Write-Error "Server PID $srvPid did not exit within 30s."
    exit 3
}

function Start-Server {
    if (Get-ServerPid) { Write-Error "Port $port is already in use; not starting."; exit 4 }
    if (-not (Test-Path $venvPy)) {
        & python -m venv .venv; if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed."; exit 5 }
    }
    # Windows PowerShell 5.1 turns native stderr into a terminating error under 'Stop'; probe under 'Continue'.
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $venvPy -c "import fastapi, uvicorn, mcp" *> $null
    $depsOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $depsOk) {
        & $venvPy -m pip install -r requirements.txt; if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 5 }
    }
    $logDir = Join-Path $root 'data'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdoutPath = Join-Path $logDir 'server-startup.stdout.log'
    $stderrPath = Join-Path $logDir 'server-startup.stderr.log'
    $p = Start-Process -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -FilePath $venvPy -ArgumentList ('"' + (Join-Path $root 'run.py') + '"') -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ($p.HasExited) {
            $details = if (Test-Path $stderrPath) { (Get-Content -LiteralPath $stderrPath -Raw) } else { '' }
            Write-Error "Server exited during startup (code $($p.ExitCode)). Details: $details`nError log: $stderrPath`nOutput log: $stdoutPath"
            exit 6
        }
        $lp = Get-ServerPid
        if ($lp) {
            $parentOf = @{}
            Get-CimInstance Win32_Process | ForEach-Object { $parentOf[[int]$_.ProcessId] = [int]$_.ParentProcessId }
            if (Test-InTree ([int]$lp) $p.Id $parentOf) { Write-Host "Server running on port $port (PID $lp)."; return }
            Write-Error "Port $port is now held by PID $lp, which is not the server I launched (PID $($p.Id))."
            exit 8
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Error "Server did not start listening on port $port within 30s. Error log: $stderrPath. Output log: $stdoutPath."
    exit 7
}

switch ($Action) {
    'stop'    { Stop-Server }
    'start'   { Start-Server }
    'restart' { Stop-Server; Start-Server }
}
