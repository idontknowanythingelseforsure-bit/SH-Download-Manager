param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Building SH Download Manager..." -ForegroundColor Cyan

if ($Clean) {
    foreach ($path in @("build", "dist")) {
        if (Test-Path $path) {
            Remove-Item $path -Recurse -Force
        }
    }
}

$pyArgs = @("-m", "PyInstaller", "--noconfirm")
if ($Clean) {
    $pyArgs += "--clean"
}
$pyArgs += "SH Download Manager.spec"

& python @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$source = Join-Path $root "dist\SH Download Manager.exe"
if (-not (Test-Path $source)) {
    throw "Build finished but dist\SH Download Manager.exe was not created."
}

$target = Join-Path $root "SH Download Manager.exe"
Copy-Item $source $target -Force

Write-Host ""
Write-Host "Built: $target" -ForegroundColor Green
Get-Item $target | Select-Object FullName, Length, LastWriteTime | Format-List
