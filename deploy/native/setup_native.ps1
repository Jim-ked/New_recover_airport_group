[CmdletBinding()]
param(
    [string]$TileRoot,
    [ValidateRange(1, 65535)][int]$Port = 8080
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$PythonRoot = Join-Path $ProjectRoot 'runtime\python'
$BasePython = Join-Path $PythonRoot 'python.exe'
$VenvRoot = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'
$LocalEnvironment = Join-Path $ProjectRoot '.env.local.ps1'
$WheelRoot = Join-Path $ProjectRoot 'offline\wheels'
$MsiRoot = Join-Path $ProjectRoot 'offline\python\msi'

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments, [string]$Description)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Description failed: $LASTEXITCODE" }
}

try {
    Set-Location -LiteralPath $ProjectRoot
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Remove-Item Env:PYTHONHOME, Env:PYTHONPATH -ErrorAction SilentlyContinue
    $manifest = Get-Content -LiteralPath (Join-Path $ProjectRoot 'file_manifest.json') -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        # Database copies are intentionally mutable after first installation.
        if ((Test-Path -LiteralPath $VenvPython) -and $entry.path.StartsWith('runtime/')) { continue }
        $path = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $entry.path))
        if (-not $path.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Invalid package manifest path.'
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne $entry.size) {
            throw "Missing or truncated package file: $($entry.path)"
        }
    }
    if ($TileRoot) {
        $TileRoot = [IO.Path]::GetFullPath($TileRoot)
        if (-not (Test-Path -LiteralPath $TileRoot -PathType Container)) { throw 'TileRoot directory does not exist.' }
    } else { $TileRoot = Join-Path $ProjectRoot 'resources\tiles' }
    foreach ($directory in @('runtime\logs', 'runtime\runs', 'runtime\content', 'runtime\temp', 'runtime\db\scenes', 'resources\tiles')) {
        New-Item -ItemType Directory -Path (Join-Path $ProjectRoot $directory) -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) {
        New-Item -ItemType Directory -Path $PythonRoot -Force | Out-Null
        # /a extracts official files only; it never selects a registered Python,
        # changes PATH, or copies an installed development runtime.
        foreach ($component in @('core', 'exe', 'lib', 'tcltk')) {
            $msi = Join-Path $MsiRoot ($component + '.msi')
            $signature = Get-AuthenticodeSignature -LiteralPath $msi
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw "Invalid official Python signature: $component"
            }
            $log = Join-Path $ProjectRoot ('runtime\logs\python-extract-' + $component + '.log')
            $process = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList @(
                '/a', ('"' + $msi + '"'), '/qn', ('TARGETDIR="' + $PythonRoot + '"'), '/l*v', ('"' + $log + '"')
            ) -WindowStyle Hidden -Wait -PassThru
            if ($process.ExitCode -ne 0) { throw "Python extraction failed: $component $($process.ExitCode)" }
        }
    }
    $baseCheck = @'
import sys,struct,tkinter,venv,ensurepip
assert sys.version_info[:3] == (3,14,0)
assert struct.calcsize('P') == 8
print('Python 3.14.0 amd64; Tcl/Tk available')
'@
    Invoke-Checked $BasePython @('-B', '-c', $baseCheck) 'Base Python check'
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $VenvRoot) { throw 'Incomplete .venv exists. Use a fresh extracted package.' }
        Invoke-Checked $BasePython @('-B', '-m', 'venv', $VenvRoot) 'Fresh venv creation'
    }
    Invoke-Checked $VenvPython @('-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-index', '--no-compile', '--find-links', $WheelRoot, '--upgrade', 'pip') 'Offline pip bootstrap'
    Invoke-Checked $VenvPython @('-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-index', '--no-compile', '--find-links', $WheelRoot, '-r', (Join-Path $ProjectRoot 'offline\requirements-native.lock.txt')) 'Offline dependencies'
    Invoke-Checked $VenvPython @('-B', '-m', 'pip', 'check', '--disable-pip-version-check') 'Dependency consistency'
    Invoke-Checked $VenvPython @('-B', '-c', 'import openpyxl') 'openpyxl import'
    Invoke-Checked $VenvPython @('-B', '-c', 'import tkinter') 'tkinter import'
    $runtimeCheck = @'
import sys,flask,waitress,reportlab,numpy,pyscipopt,tkinter,openpyxl,et_xmlfile
import importlib.metadata as m
assert sys.prefix != sys.base_prefix
print('Python='+sys.version.split()[0])
print('openpyxl='+openpyxl.__version__)
print('et_xmlfile='+m.version('et_xmlfile'))
print('Tcl/Tk='+str(tkinter.TkVersion))
print('PySCIPOpt='+pyscipopt.__version__)
'@
    Invoke-Checked $VenvPython @('-B', '-c', $runtimeCheck) 'Native runtime imports'
    if (-not (Test-Path -LiteralPath $LocalEnvironment -PathType Leaf)) {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })
        $lines = @(
            '# Generated locally by Native V1.2 setup; never included in the delivery ZIP.',
            ('$env:AIRPORT_GROUP_SECRET_KEY = ''' + $secret + ''''),
            '$env:AIRPORT_GROUP_HOST = ''127.0.0.1''',
            ('$env:AIRPORT_GROUP_PORT = ''' + $Port + ''''),
            ('$env:AIRPORT_GROUP_TILE_ROOT = ''' + $TileRoot.Replace("'", "''") + ''''),
            '$env:AIRPORT_GROUP_GIS_TILE_TEMPLATE = ''/tiles/{source}/{z}/{x}/{y}.jpg'''
        )
        [IO.File]::WriteAllText($LocalEnvironment, (($lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($true)))
    } else { Write-Host 'Existing local configuration preserved; TileRoot/Port arguments are not applied.' }
    Invoke-Checked (Join-Path $ProjectRoot 'deploy\windows\check.cmd') @() 'backend check'
    Write-Host 'NATIVE_SETUP_PASS. Start scenario_gui.cmd or deploy\windows\start.cmd.'
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
