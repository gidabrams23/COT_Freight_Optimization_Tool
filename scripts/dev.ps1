param(
    [Alias("Host")]
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 5000,
    [switch]$NoReload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\\Scripts\\python.exe"
$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

$null = Get-Command $pythonExe -ErrorAction Stop

$cmd = @(
    "-m", "flask",
    "--app", "app",
    "run",
    "--host", $BindHost,
    "--port", "$Port",
    "--debug"
)

if ($NoReload) {
    $cmd += "--no-reload"
}

Write-Host "Starting dev server from $repoRoot" -ForegroundColor Cyan
Write-Host "Python: $pythonExe" -ForegroundColor DarkCyan
Write-Host "URL: http://$BindHost`:$Port" -ForegroundColor DarkCyan

& $pythonExe @cmd
