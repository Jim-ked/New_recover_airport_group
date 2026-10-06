Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$LocalEnvironment = Join-Path $ProjectRoot '.env.local.ps1'

if (Test-Path -LiteralPath $LocalEnvironment -PathType Leaf) {
    . $LocalEnvironment
}

# The delivery entrypoints always use the canonical database. Alternate databases
# remain available to explicit CLI/test invocations, but cannot leak into the standard
# start/check/init/worker commands from a previous shell. Network settings remain under
# the existing environment/local configuration contract.
$env:AIRPORT_GROUP_DB_PATH = [System.IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot 'runtime\db\airport_group.sqlite3')
)
