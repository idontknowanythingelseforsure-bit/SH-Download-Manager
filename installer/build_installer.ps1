# Builds the SH Download Manager installer (single .exe) with Inno Setup 6.
#
#   powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
#   powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -RebuildApp
#
# Options:
#   -RebuildApp     run ..\build.ps1 first (PyInstaller)
#   -FfmpegRoot     folder that contains bin\ffmpeg.exe (default: C:\ffmpeg)
#   -NoOpen         do not open Explorer on the produced installer

[CmdletBinding()]
param(
    [switch]$RebuildApp,
    [string]$FfmpegRoot = 'C:\ffmpeg',
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$iss  = Join-Path $here 'SH Download Manager.iss'

function Find-Iscc {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($key in @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1')) {
        $item = Get-ItemProperty $key -ErrorAction SilentlyContinue
        if ($item -and $item.InstallLocation) {
            $candidate = Join-Path $item.InstallLocation 'ISCC.exe'
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it with: winget install --id JRSoftware.InnoSetup"
}
Write-Host "Using $iscc"

if ($RebuildApp) {
    Write-Host "Building the application with PyInstaller..."
    & powershell -ExecutionPolicy Bypass -File (Join-Path $repo 'build.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Application build failed." }
}

$appExe = Join-Path $repo 'SH Download Manager.exe'
if (-not (Test-Path $appExe)) {
    throw "SH Download Manager.exe is missing. Run build.ps1 or pass -RebuildApp."
}

$ffmpeg = Join-Path $FfmpegRoot 'bin\ffmpeg.exe'
if (Test-Path $ffmpeg) {
    Write-Host ("Bundling ffmpeg ({0:N1} MB) from {1}" -f `
        ((Get-Item $ffmpeg).Length / 1MB), $FfmpegRoot)
} else {
    Write-Warning "ffmpeg not found in $FfmpegRoot - MP3 320/128 options will be hidden on the target PC."
}

Write-Host "Compiling installer..."
$arguments = @("/Qp", "/DFFmpegRoot=$FfmpegRoot", $iss)
& $iscc @arguments
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed ($LASTEXITCODE)." }

$setup = Join-Path $repo 'SH Download Manager Setup.exe'
if (-not (Test-Path $setup)) { throw "Expected installer was not produced: $setup" }
$size = (Get-Item $setup).Length
Write-Host ""
Write-Host ("Installer ready: {0}" -f $setup)
Write-Host ("Size: {0:N1} MB" -f ($size / 1MB))
if (-not $NoOpen) { Start-Process explorer.exe -ArgumentList "/select,`"$setup`"" }
