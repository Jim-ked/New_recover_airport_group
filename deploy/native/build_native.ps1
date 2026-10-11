[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OfflineAssets,
    [string]$OutputDirectory = 'D:\Data',
    [string]$BuildPython
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $BuildPython) { $BuildPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
$assets = [IO.Path]::GetFullPath($OfflineAssets)
foreach ($name in @('python-3.14.0-amd64.exe', 'msi\core.msi', 'msi\exe.msi', 'msi\lib.msi', 'msi\tcltk.msi')) {
    $path = Join-Path (Join-Path $assets 'python') $name
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw "Invalid official Python signature: $name"
    }
}
& $BuildPython -B (Join-Path $PSScriptRoot 'package_native.py') --root $ProjectRoot --assets $assets --output $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "Native build failed: $LASTEXITCODE" }
