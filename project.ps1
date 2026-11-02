[CmdletBinding()]
param(
    [ValidateSet('Start', 'Status', 'Stop')]
    [string]$Action = 'Start',
    [switch]$Seed
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$appPath = Join-Path $PSScriptRoot 'app.py'
$runtimePath = Join-Path $PSScriptRoot '.runtime'
$statePath = Join-Path $runtimePath 'app-state.json'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python environment missing. Follow Quick start in README.md first.'
}

function Test-Dashboard {
    & $pythonPath (Join-Path $PSScriptRoot 'doctor.py') --dashboard-only | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if ($Action -eq 'Status') {
    & $pythonPath (Join-Path $PSScriptRoot 'doctor.py')
    exit $LASTEXITCODE
}

if ($Action -eq 'Stop') {
    if (-not (Test-Path -LiteralPath $statePath)) {
        Write-Output 'No launcher-owned dashboard is recorded. Other running servers were not changed.'
        exit 0
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $ownedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.processId)"
    if ($null -ne $ownedProcess) {
        $commandLine = [string]$ownedProcess.CommandLine
        $sameStart = [Math]::Abs(($ownedProcess.CreationDate.ToUniversalTime() - [datetime]::Parse($state.startedUtc).ToUniversalTime()).TotalSeconds) -lt 3
        if (-not $sameStart -or $commandLine.IndexOf($appPath, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or $commandLine -notmatch 'streamlit\s+run') {
            throw 'Process identity does not match the recorded project server. No process was stopped.'
        }
        & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $state.processId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the recorded project server.' }
    }
    Remove-Item -LiteralPath $statePath
    Write-Output 'Project dashboard stopped. Database containers and stored data were retained.'
    exit 0
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerPath = if ($dockerCommand) { $dockerCommand.Source } else { $null }
if (-not $dockerPath) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe')
    )
    $dockerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $dockerPath) { throw 'Docker Desktop is missing. Install it and complete its WSL setup first.' }

function Test-Docker {
    try {
        & $dockerPath info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (-not (Test-Docker)) {
    $desktopCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/Docker Desktop.exe'),
        (Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe')
    )
    $desktopPath = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $desktopPath) { throw 'Open Docker Desktop and wait for its engine, then retry.' }
    Write-Output 'Starting Docker Desktop. Waiting for the engine...'
    Start-Process -FilePath $desktopPath -WindowStyle Hidden
    $deadline = [datetime]::UtcNow.AddSeconds(90)
    while (-not (Test-Docker)) {
        if ([datetime]::UtcNow -gt $deadline) { throw 'Docker engine is not ready. Open Docker Desktop to inspect the WSL or engine message.' }
        Start-Sleep -Seconds 2
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
}
Write-Output 'Checking database containers...'
& $dockerPath compose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed. Inspect Docker Desktop and the message above; seeding and app startup were skipped.' }
if ($Seed) {
    & $pythonPath (Join-Path $PSScriptRoot 'storage.py')
    if ($LASTEXITCODE -ne 0) { throw 'Sample loading failed. Dashboard startup was skipped.' }
}
& $pythonPath (Join-Path $PSScriptRoot 'doctor.py') --database-only
if ($LASTEXITCODE -ne 0) { throw 'Databases are not ready. Check .env; for a new dedicated CP database, rerun with -Seed.' }

if (Test-Dashboard) {
    Write-Output 'An existing healthy dashboard is available at http://127.0.0.1:8501. No duplicate server was started.'
    exit 0
}
$listener = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw 'Port 8501 is occupied by an unresponsive server. Stop its original PowerShell command with Ctrl+C, or use -Action Stop if this launcher started it.' }

New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
$arguments = '-m streamlit run "' + $appPath + '" --server.headless true --server.address 127.0.0.1 --server.port 8501'
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'dashboard.out.log') -RedirectStandardError (Join-Path $runtimePath 'dashboard.err.log')
@{ processId = $serverProcess.Id; startedUtc = $serverProcess.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Dashboard)) {
    $serverProcess.Refresh()
    if ($serverProcess.HasExited -or [datetime]::UtcNow -gt $deadline) {
        throw "Dashboard startup failed. Read $runtimePath/dashboard.err.log and run project.ps1 -Action Status."
    }
    Start-Sleep -Milliseconds 500
}
Write-Output 'Project ready at http://127.0.0.1:8501. Select MySQL + MongoDB in the sidebar.'
Write-Output 'This dashboard runs in the background. Its logs and process record are in .runtime.'
