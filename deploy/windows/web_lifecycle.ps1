[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$LocalEnvironment = Join-Path $ProjectRoot '.env.local.ps1'
$ServePattern = '(?i)(?:^|\s)-m\s+backend\s+serve(?:\s|$)'
$WorkerPattern = '(?i)(?:^|\s)-m\s+backend\s+worker(?:\s|$)'

if (Test-Path -LiteralPath $LocalEnvironment -PathType Leaf) {
    . $LocalEnvironment
}

$WebHost = if ([string]::IsNullOrWhiteSpace($env:AIRPORT_GROUP_HOST)) {
    '127.0.0.1'
} else {
    $env:AIRPORT_GROUP_HOST
}

$WebPort = 8080
if (-not [string]::IsNullOrWhiteSpace($env:AIRPORT_GROUP_PORT)) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:AIRPORT_GROUP_PORT, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        Write-Error "AIRPORT_GROUP_PORT must be an integer between 1 and 65535."
        exit 2
    }
    $WebPort = $parsedPort
}

function Get-ProcessRecord {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Test-ProjectWebProcess {
    param($ProcessRecord)
    if ($null -eq $ProcessRecord) {
        return $false
    }
    if ($ProcessRecord.Name -notmatch '(?i)^python(?:\.exe)?$') {
        return $false
    }
    $commandLine = [string]$ProcessRecord.CommandLine
    return (
        $commandLine -match $ServePattern -and
        $commandLine.IndexOf($PythonPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )
}

function Test-ProjectWorkerProcess {
    param($ProcessRecord)
    if ($null -eq $ProcessRecord) { return $false }
    if ($ProcessRecord.Name -notmatch '(?i)^python(?:\.exe)?$') { return $false }
    $commandLine = [string]$ProcessRecord.CommandLine
    return ($commandLine -match $WorkerPattern -and $commandLine.IndexOf($PythonPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Get-ProjectWebProcesses {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { Test-ProjectWebProcess $_ }
    )
}

function Get-ProjectWorkerProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { Test-ProjectWorkerProcess $_ })
}

function Get-ProjectWorkerLeafProcesses {
    $workers = @(Get-ProjectWorkerProcesses)
    $parentIds = @($workers | ForEach-Object { [int]$_.ParentProcessId })
    @($workers | Where-Object { $parentIds -notcontains [int]$_.ProcessId })
}

function Get-WebListeners {
    @(
        Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue |
            Sort-Object OwningProcess -Unique
    )
}

function Write-ListenerDetails {
    param([Parameter(Mandatory = $true)]$Listeners)
    foreach ($listener in $Listeners) {
        $record = Get-ProcessRecord -ProcessId ([int]$listener.OwningProcess)
        $name = if ($null -eq $record) { '<unknown>' } else { $record.Name }
        $commandLine = if ($null -eq $record) { '<unavailable>' } else { $record.CommandLine }
        Write-Host "  PID=$($listener.OwningProcess) Process=$name Address=$($listener.LocalAddress):$WebPort"
        Write-Host "  CommandLine=$commandLine"
    }
}

function Test-OnlyProjectListeners {
    param([Parameter(Mandatory = $true)]$Listeners)
    if ($Listeners.Count -eq 0) {
        return $false
    }
    foreach ($listener in $Listeners) {
        if (-not (Test-ProjectWebProcess (Get-ProcessRecord -ProcessId ([int]$listener.OwningProcess)))) {
            return $false
        }
    }
    return $true
}

function Show-Status {
    $listeners = @(Get-WebListeners)
    if ($listeners.Count -eq 0) {
        Write-Host "Port $WebPort is free. The current project Web service is not listening."
        return 1
    }
    if (Test-OnlyProjectListeners -Listeners $listeners) {
        $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ', '
        Write-Host "The current project Web service is running. PID=$processIds"
        Write-ListenerDetails -Listeners $listeners
        $workers = @(Get-ProjectWorkerLeafProcesses)
        Write-Host "  Worker processes: $($workers.Count)"
        foreach ($worker in $workers) { Write-Host "  Worker PID=$($worker.ProcessId) CommandLine=$($worker.CommandLine)" }
        return 0
    }
    Write-Host "Port $WebPort is occupied by another process."
    Write-ListenerDetails -Listeners $listeners
    return 2
}

function Stop-ProjectWeb {
    $listeners = @(Get-WebListeners)
    if ($listeners.Count -gt 0 -and -not (Test-OnlyProjectListeners -Listeners $listeners)) {
        Write-Host "Port $WebPort is occupied by another process. It will not be stopped."
        Write-ListenerDetails -Listeners $listeners
        return 3
    }

    $processes = @(Get-ProjectWebProcesses)
    $workers = @(Get-ProjectWorkerProcesses)
    if ($processes.Count -eq 0 -and $workers.Count -eq 0) {
        Write-Host "The current project Web service is not running."
        return 0
    }

    $listenerIds = @($listeners | ForEach-Object { [int]$_.OwningProcess })
    $ordered = @($processes | Sort-Object @{ Expression = { if ($listenerIds -contains [int]$_.ProcessId) { 0 } else { 1 } } })
    Write-Host "Stopping the current project Web/Worker service: Web PID=$($listenerIds -join ', '); Worker PID=$(@($workers | ForEach-Object { $_.ProcessId }) -join ', ')"
    foreach ($process in @($ordered + $workers)) {
        if ($null -ne (Get-ProcessRecord -ProcessId ([int]$process.ProcessId))) {
            Stop-Process -Id ([int]$process.ProcessId) -ErrorAction Stop
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $remainingProcesses = @(Get-ProjectWebProcesses) + @(Get-ProjectWorkerProcesses)
        $remainingListeners = @(Get-WebListeners)
    } while (($remainingProcesses.Count -gt 0 -or $remainingListeners.Count -gt 0) -and [DateTime]::UtcNow -lt $deadline)

    if ($remainingProcesses.Count -gt 0) {
        Write-Error "The project Web process did not exit within 10 seconds."
        return 4
    }
    if ($remainingListeners.Count -gt 0) {
        Write-Error "Port $WebPort was not released after the project Web process stopped."
        Write-ListenerDetails -Listeners $remainingListeners
        return 5
    }
    Write-Host "The current project Web service stopped. Port $WebPort is free."
    return 0
}

switch ($Action) {
    'status' {
        exit (Show-Status)
    }
    'stop' {
        exit (Stop-ProjectWeb)
    }
    'start' {
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
            Write-Error "Project Python was not found: $PythonPath"
            exit 2
        }
        $listeners = @(Get-WebListeners)
        if ($listeners.Count -gt 0) {
            if (Test-OnlyProjectListeners -Listeners $listeners) {
                $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ', '
                Write-Host "The current project Web service is already running. PID=$processIds"
                if (@(Get-ProjectWorkerProcesses).Count -eq 0) {
                    $workerStdout = Join-Path $ProjectRoot 'runtime\logs\worker_stdout.log'
                    $workerStderr = Join-Path $ProjectRoot 'runtime\logs\worker_stderr.log'
                    $worker = Start-Process -FilePath $PythonPath -ArgumentList @('-m', 'backend', 'worker') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerStdout -RedirectStandardError $workerStderr -PassThru
                    Write-Host "Worker was missing; started it in background. PID=$($worker.Id)"
                }
                exit 0
            }
            Write-Host "Port $WebPort is occupied by another process. The Web service was not started."
            Write-ListenerDetails -Listeners $listeners
            exit 3
        }

        Write-Host "Project: $ProjectRoot"
        Write-Host "Python:  $PythonPath"
        Write-Host "Listen:  http://${WebHost}:$WebPort"
        Write-Host "Mode:    foreground (close with Ctrl+C or deploy\windows\stop.cmd)"
        Set-Location -LiteralPath $ProjectRoot
        $workerStdout = Join-Path $ProjectRoot 'runtime\logs\worker_stdout.log'
        $workerStderr = Join-Path $ProjectRoot 'runtime\logs\worker_stderr.log'
        $worker = Start-Process -FilePath $PythonPath -ArgumentList @('-m', 'backend', 'worker') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerStdout -RedirectStandardError $workerStderr -PassThru
        Write-Host "Worker started in background. PID=$($worker.Id)"
        & $PythonPath -m backend serve
        exit $LASTEXITCODE
    }
}
