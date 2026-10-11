[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action,
    [string]$SceneDatabase,
    [switch]$StatusJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvironmentLoader = Join-Path $PSScriptRoot 'load_environment.ps1'
$ServePattern = '(?i)(?:^|\s)-m\s+backend\s+serve(?:\s|$)'
$WorkerPattern = '(?i)(?:^|\s)-m\s+backend\s+worker(?:\s|$)'

. $EnvironmentLoader

# Explicit scene selection is applied AFTER local/default environment loading.
# The ordinary start/check/init/worker entrypoints keep their canonical DB policy.
if (-not [string]::IsNullOrWhiteSpace($SceneDatabase)) {
    if (-not [System.IO.Path]::IsPathRooted($SceneDatabase) -or
        -not (Test-Path -LiteralPath $SceneDatabase -PathType Leaf)) {
        Write-Error 'Scene database must be an existing absolute file path.'
        exit 2
    }
    $selectedDatabase = [System.IO.Path]::GetFullPath($SceneDatabase)
    if ($selectedDatabase -eq $env:AIRPORT_GROUP_DB_PATH) {
        Write-Error 'The canonical database cannot be selected as a scene database.'
        exit 2
    }
    $env:AIRPORT_GROUP_DB_PATH = $selectedDatabase
}
$LifecycleState = Join-Path $ProjectRoot 'runtime\logs\web_lifecycle_state.json'

function Read-LifecycleState {
    if (Test-Path -LiteralPath $LifecycleState -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $LifecycleState -Raw | ConvertFrom-Json
            $owner = Get-ProcessRecord -ProcessId ([int]$state.owner_pid)
            if ($null -ne $owner -and $owner.CreationDate.ToUniversalTime().ToString('o') -eq $state.owner_created_at) {
                return $state
            }
        }
        catch { return $null }
    }
    return $null
}

function Save-LifecycleState {
    New-Item -ItemType Directory -Path (Split-Path -Parent $LifecycleState) -Force | Out-Null
    $owner = Get-ProcessRecord -ProcessId $PID
    if ($null -eq $owner) { throw 'Unable to identify lifecycle process.' }
    $state = @{ db_path = $env:AIRPORT_GROUP_DB_PATH; owner_pid = $PID
                owner_created_at = $owner.CreationDate.ToUniversalTime().ToString('o') }
    [System.IO.File]::WriteAllText($LifecycleState, ($state | ConvertTo-Json -Compress),
        (New-Object System.Text.UTF8Encoding($false)))
}

function Test-DatabaseSelection {
    if (@(Get-ProjectWebProcesses).Count -eq 0 -and @(Get-ProjectWorkerProcesses).Count -eq 0) {
        return $true
    }
    $state = Read-LifecycleState
    if ($null -eq $state) { return [string]::IsNullOrWhiteSpace($SceneDatabase) }
    return ([string]$state.db_path -eq $env:AIRPORT_GROUP_DB_PATH)
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

function Stop-ProjectProcess {
    param([Parameter(Mandatory = $true)]$ProcessRecord)
    $current = Get-ProcessRecord -ProcessId ([int]$ProcessRecord.ProcessId)
    if ($null -eq $current -or $current.CreationDate -ne $ProcessRecord.CreationDate) { return }
    if (-not (Test-ProjectWebProcess $current) -and -not (Test-ProjectWorkerProcess $current)) { return }
    try { Stop-Process -Id ([int]$current.ProcessId) -ErrorAction Stop }
    catch {
        # The foreground lifecycle may have stopped this Worker concurrently.
        # Keep real permission/termination failures visible, tolerate only exit.
        $remaining = Get-ProcessRecord -ProcessId ([int]$current.ProcessId)
        if ($null -ne $remaining -and $remaining.CreationDate -eq $current.CreationDate) { throw }
    }
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
    $workers = @(Get-ProjectWorkerLeafProcesses)
    if ($listeners.Count -eq 0) {
        Write-Host "Port $WebPort is free. The current project Web service is not listening."
        Write-Host "The current project Worker processes: $($workers.Count)"
        foreach ($worker in $workers) { Write-Host "  Worker PID=$($worker.ProcessId) CommandLine=$($worker.CommandLine)" }
        return 1
    }
    if (Test-OnlyProjectListeners -Listeners $listeners) {
        $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ', '
        Write-Host "The current project Web service is running. PID=$processIds"
        Write-ListenerDetails -Listeners $listeners
        Write-Host "  Worker processes: $($workers.Count)"
        foreach ($worker in $workers) { Write-Host "  Worker PID=$($worker.ProcessId) CommandLine=$($worker.CommandLine)" }
        return 0
    }
    Write-Host "Port $WebPort is occupied by another process."
    Write-ListenerDetails -Listeners $listeners
    Write-Host "The current project Worker processes: $($workers.Count)"
    foreach ($worker in $workers) { Write-Host "  Worker PID=$($worker.ProcessId) CommandLine=$($worker.CommandLine)" }
    return 2
}

function Show-JsonStatus {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $listeners = @(Get-WebListeners)
    $workers = @(Get-ProjectWorkerLeafProcesses)
    $owned = $listeners.Count -gt 0 -and (Test-OnlyProjectListeners -Listeners $listeners)
    $state = Read-LifecycleState
    $database = if ($null -ne $state) { [string]$state.db_path }
                elseif ($owned -or $workers.Count -gt 0) { '<unknown>' }
                else { $env:AIRPORT_GROUP_DB_PATH }
    $webIds = if ($owned) { @($listeners | ForEach-Object { [int]$_.OwningProcess }) } else { @() }
    @{ running = [bool]$owned; port_occupied = [bool]($listeners.Count -gt 0 -and -not $owned)
       web_pids = @($webIds); worker_pids = @($workers | ForEach-Object { [int]$_.ProcessId })
       db_path = $database; port = $WebPort } | ConvertTo-Json -Compress
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
        if (Test-Path -LiteralPath $LifecycleState) { Remove-Item -LiteralPath $LifecycleState }
        Write-Host "The current project Web service is not running."
        return 0
    }

    $listenerIds = @($listeners | ForEach-Object { [int]$_.OwningProcess })
    $ordered = @($processes | Sort-Object @{ Expression = { if ($listenerIds -contains [int]$_.ProcessId) { 0 } else { 1 } } })
    Write-Host "Stopping the current project Web/Worker service: Web PID=$($listenerIds -join ', '); Worker PID=$(@($workers | ForEach-Object { $_.ProcessId }) -join ', ')"
    foreach ($process in @($ordered + $workers)) {
        Stop-ProjectProcess -ProcessRecord $process
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
    if (Test-Path -LiteralPath $LifecycleState) { Remove-Item -LiteralPath $LifecycleState }
    return 0
}

switch ($Action) {
    'status' {
        if ($StatusJson) { Show-JsonStatus; exit 0 }
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
        if (-not (Test-DatabaseSelection)) {
            Write-Host 'The current project is using a different or unknown database. Stop it before switching scenes.'
            exit 6
        }
        New-Item -ItemType Directory -Path (Join-Path $ProjectRoot 'runtime\logs') -Force | Out-Null
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
        $existingWorkers = @(Get-ProjectWorkerLeafProcesses)
        $worker = $null
        $workerStartedHere = $false
        if ($existingWorkers.Count -eq 0) {
            $worker = Start-Process -FilePath $PythonPath -ArgumentList @('-m', 'backend', 'worker') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerStdout -RedirectStandardError $workerStderr -PassThru
            $workerStartedHere = $true
            Write-Host "Worker started in background. PID=$($worker.Id)"
        } else {
            Write-Host "Using the existing project Worker. PID=$($existingWorkers[0].ProcessId)"
        }
        $webExitCode = 0
        try {
            Save-LifecycleState
            & $PythonPath -m backend serve
            $webExitCode = $LASTEXITCODE
        } finally {
            if ($workerStartedHere -and $null -ne $worker) {
                # Windows venv Python may spawn a real interpreter child. Stop
                # only children owned by this Worker invocation, then its wrapper.
                $children = @(Get-ProjectWorkerProcesses | Where-Object { [int]$_.ParentProcessId -eq $worker.Id })
                foreach ($child in $children) {
                    Stop-ProjectProcess -ProcessRecord $child
                }
                $workerProcess = Get-ProcessRecord -ProcessId $worker.Id
                if (Test-ProjectWorkerProcess $workerProcess) {
                    Stop-ProjectProcess -ProcessRecord $workerProcess
                    Write-Host "Worker started by this lifecycle invocation was stopped. PID=$($worker.Id)"
                }
            }
            $state = Read-LifecycleState
            if ($null -ne $state -and [int]$state.owner_pid -eq $PID) {
                Remove-Item -LiteralPath $LifecycleState -ErrorAction SilentlyContinue
            }
        }
        exit $webExitCode
    }
}
